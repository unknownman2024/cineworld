#!/usr/bin/env python3
"""
Event Cinemas Australia scraper - summary only.
Fetches Indian-language movies (Hindi, Tamil, Telugu, Malayalam, Kannada),
retrieves seat occupancy for each session, and saves aggregated data to
EVENTdata.json. No venue breakdown - only totals + day-wise summaries.
"""

import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import requests

# ------------------------------------------------------------------
# 1. Configuration
# ------------------------------------------------------------------
KEYWORDS = {"hindi", "tamil", "telugu", "malayalam", "kannada"}

# Also accept the "Cine India" attribute as a signal for Indian cinema.
CINE_INDIA_ATTRIBUTE = "cine india"

MAX_WORKERS = 8
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

# Optional proxy from GitHub Secrets
PROXY = os.environ.get("EVENT_PROXY", "").strip()
PROXY_LIST = [PROXY] if PROXY else []

# ------------------------------------------------------------------
# 2. Headers & proxy helpers
# ------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
]

ACCEPT_LANGUAGES = ["en-AU,en;q=0.9", "en-US,en;q=0.9", "en-GB,en;q=0.9"]


def get_random_headers() -> Dict[str, str]:
    ua = random.choice(USER_AGENTS)
    return {
        "Accept": "application/json, text/plain, */*",
        # Event Cinemas' CDN serves brotli if we ask; requests cannot decode
        # it without the `brotli` package, so we only advertise gzip/deflate.
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Cache-Control": "no-cache",
        "Origin": "https://www.eventcinemas.com.au",
        "Pragma": "no-cache",
        "Referer": "https://www.eventcinemas.com.au/",
        "User-Agent": ua,
    }


def get_random_proxy() -> Optional[Dict[str, str]]:
    if not PROXY_LIST:
        return None
    proxy = random.choice(PROXY_LIST)
    return {"http": proxy, "https": proxy}


def request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.update(get_random_headers())
    proxies = get_random_proxy()
    last_exc: Optional[Exception] = None

    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.request(
                method, url,
                headers=headers,
                proxies=proxies,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"⚠️  Attempt {attempt + 1}/{RETRY_ATTEMPTS} failed for {url} "
                  f"(status={status}): {e}")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue
    raise RuntimeError(f"Max retries exceeded for {url}: {last_exc}")


def safe_json(resp: requests.Response, label: str):
    """Parse JSON or print a diagnostic before raising."""
    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError as e:
        ctype = resp.headers.get("Content-Type", "unknown")
        cenc = resp.headers.get("Content-Encoding", "none")
        preview = (resp.text or "")[:800].replace("\n", " ")
        print(f"❌ {label}: non-JSON response")
        print(f"   status={resp.status_code} content-type={ctype} "
              f"content-encoding={cenc}")
        print(f"   body[:800]={preview!r}")
        raise e


# ------------------------------------------------------------------
# 3. API endpoints
# ------------------------------------------------------------------
# Public Event Cinemas AU endpoints (no API key required).
EVENT_BASE = "https://www.eventcinemas.com.au"

# The seat-map / session-detail endpoint used by the Event Cinemas frontend.
# This is the same URL your track.html uses.
TICKET_API = "https://eventcinemas.text2024mail.workers.dev"


def fetch_now_showing() -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetNowShowing"
    resp = request_with_retry("GET", url)
    data = safe_json(resp, "fetch_now_showing")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_coming_soon() -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetComingSoon"
    resp = request_with_retry("GET", url)
    data = safe_json(resp, "fetch_coming_soon")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_all_movies() -> List[Dict]:
    """Merge now-showing and coming-soon into one deduplicated list."""
    now = fetch_now_showing()
    soon = fetch_coming_soon()
    merged: Dict[int, Dict] = {}
    for m in now + soon:
        mid = m.get("Id")
        if mid is not None:
            merged[mid] = m
    return list(merged.values())


def fetch_sessions_for_cinemas(cinema_ids: List[int], date_str: str) -> List[Dict]:
    """
    Fetch sessions for a list of cinema IDs on a given YYYY-MM-DD date.
    Returns the raw Movies list from the response (each movie contains
    CinemaModels with Sessions).
    """
    if not cinema_ids:
        return []
    params = "&".join(f"cinemaIds={cid}" for cid in cinema_ids)
    url = f"{TICKET_API}/Cinemas/GetSessions?{params}&date={date_str}"
    resp = request_with_retry("GET", url)
    data = safe_json(resp, f"fetch_sessions({date_str})")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_seat_map(session_id: int) -> Dict:
    """Fetch the seat map / ticket data for a single session."""
    url = f"{TICKET_API}/api/ticketing/session?sessionId={session_id}"
    resp = request_with_retry("GET", url)
    return safe_json(resp, f"fetch_seat_map({session_id})")


# ------------------------------------------------------------------
# 4. Language detection
# ------------------------------------------------------------------
def movie_languages(movie: Dict) -> Set[str]:
    """
    Return the set of lower-cased language names for a movie.

    Event Cinemas exposes languages via the `Attributes` list (e.g.
    "Hindi", "Tamil", "Cine India") and sometimes via `AllFilters`.
    """
    langs: Set[str] = set()

    for attr in movie.get("Attributes") or []:
        langs.add(str(attr).strip().lower())

    for f in movie.get("AllFilters") or []:
        code = (f.get("code") or "").strip().lower()
        name = (f.get("name") or "").strip().lower()
        if code:
            langs.add(code)
        if name:
            langs.add(name)

    return langs


def is_indian_language(movie: Dict) -> bool:
    langs = movie_languages(movie)
    if CINE_INDIA_ATTRIBUTE in langs:
        return True
    return any(kw in langs for kw in KEYWORDS)


def matched_languages(movie: Dict) -> List[str]:
    """Return the Indian-language keywords that matched this movie."""
    langs = movie_languages(movie)
    return sorted(kw for kw in KEYWORDS if kw in langs)


# ------------------------------------------------------------------
# 5. Seat-map parsing
# ------------------------------------------------------------------
def parse_seat_map(seat_data: Dict) -> Tuple[int, int, int, float]:
    """
    Parse Event Cinemas seat data.

    Returns (total, sold, available, adult_price).

    The response shape (from track.html) is:
      { "Data": { "Seats": { "Rows": [ { "Seats": [ { "Status": ... } ] } ] },
                  "Tickets": [ { "Name": "Adult", "Price": 25.0 }, ... ] } }
    """
    total = 0
    sold = 0
    available = 0

    rows = ((seat_data.get("Data") or {}).get("Seats") or {}).get("Rows") or []
    for row in rows:
        for seat in row.get("Seats") or []:
            status = seat.get("Status")
            if not status or status == "Spacer":
                continue
            total += 1
            if status == "Booked":
                sold += 1
            elif status == "Available":
                available += 1

    adult_price = 0.0
    tickets = (seat_data.get("Data") or {}).get("Tickets") or []
    for t in tickets:
        if (t.get("Name") or "").strip().lower() == "adult":
            try:
                adult_price = float(t.get("Price") or 0)
            except (TypeError, ValueError):
                adult_price = 0.0
            break

    return total, sold, available, adult_price


# ------------------------------------------------------------------
# 6. Main processing
# ------------------------------------------------------------------
def date_range(days: int = 7) -> List[str]:
    """Return today + next `days-1` days as YYYY-MM-DD strings."""
    today = datetime.utcnow().date()
    return [(today + timedelta(days=i)).isoformat() for i in range(days)]


def collect_sessions_for_movie(
    movie: Dict,
    cinema_ids: List[int],
    dates: List[str],
) -> List[Dict]:
    """
    For one movie, query sessions across all its cinemas and dates,
    returning a flat list of session dicts.
    """
    movie_name = movie.get("Name", "")
    all_sessions: List[Dict] = []

    for date_str in dates:
        try:
            movies_on_date = fetch_sessions_for_cinemas(cinema_ids, date_str)
        except Exception as e:
            print(f"⚠️  Sessions fetch failed for {date_str}: {e}")
            continue

        for m in movies_on_date:
            if (m.get("Name") or "").strip() != movie_name.strip():
                continue
            for cinema in m.get("CinemaModels") or []:
                for session in cinema.get("Sessions") or []:
                    all_sessions.append({
                        "cinemaId": session.get("CinemaId"),
                        "cinemaName": cinema.get("Name"),
                        "sessionId": session.get("Id"),
                        "startTime": session.get("StartTime"),
                        "movieId": session.get("MovieId"),
                    })

    return all_sessions


def process_movies(movies: List[Dict]) -> Dict:
    """
    Fetch seat maps for every session of every matching movie
    and aggregate into totals + day-wise summaries.
    """
    # Build the full task list: (movie, session)
    tasks: List[Tuple[Dict, Dict]] = []
    dates = date_range(7)

    for movie in movies:
        cinema_ids = movie.get("CinemaIds") or []
        if not cinema_ids:
            continue
        sessions = collect_sessions_for_movie(movie, cinema_ids, dates)
        for s in sessions:
            tasks.append((movie, s))

    print(f"📡 Fetching seat maps for {len(tasks)} sessions...")

    lock = threading.Lock()
    session_results: List[Dict] = []

    def process_task(movie: Dict, session: Dict):
        sid = session.get("sessionId")
        if sid is None:
            return None
        try:
            seat_data = fetch_seat_map(sid)
            total, sold, available, price = parse_seat_map(seat_data)
            return {
                "movie": movie,
                "session": session,
                "totalSeats": total,
                "soldSeats": sold,
                "availableSeats": available,
                "adultPrice": price,
            }
        except Exception as e:
            print(f"⚠️  Failed seat map for session {sid}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_task, m, s): (m, s) for m, s in tasks}
        for future in as_completed(futures):
            res = future.result()
            if res is not None:
                with lock:
                    session_results.append(res)

    print(f"✅ Successfully processed {len(session_results)} sessions.")

    # Aggregate
    agg: Dict[int, Dict] = {}

    for r in session_results:
        movie = r["movie"]
        movie_id = movie.get("Id")
        if movie_id is None:
            continue

        session = r["session"]
        start = session.get("startTime") or ""
        date_str = start.split("T")[0] if "T" in start else "Unknown"

        if movie_id not in agg:
            agg[movie_id] = {
                "movieName": movie.get("Name", ""),
                "slug": (movie.get("MovieUrl") or "").rstrip("/").split("/")[-1],
                "languages": matched_languages(movie),
                "rating": movie.get("Rating", ""),
                "totalShows": 0,
                "totalSeats": 0,
                "totalSold": 0,
                "totalAvailable": 0,
                "totalGross": 0.0,
                "days": {},
            }

        md = agg[movie_id]
        md["totalShows"] += 1
        md["totalSeats"] += r["totalSeats"]
        md["totalSold"] += r["soldSeats"]
        md["totalAvailable"] += r["availableSeats"]
        md["totalGross"] += r["soldSeats"] * r["adultPrice"]

        if date_str not in md["days"]:
            md["days"][date_str] = {
                "shows": 0, "seats": 0, "sold": 0,
                "available": 0, "gross": 0.0,
            }
        day = md["days"][date_str]
        day["shows"] += 1
        day["seats"] += r["totalSeats"]
        day["sold"] += r["soldSeats"]
        day["available"] += r["availableSeats"]
        day["gross"] += r["soldSeats"] * r["adultPrice"]

    # Compute occupancy percentages
    for md in agg.values():
        md["occupancy"] = (
            md["totalSold"] / md["totalSeats"] * 100
            if md["totalSeats"] > 0 else 0.0
        )
        for day in md["days"].values():
            day["occupancy"] = (
                day["sold"] / day["seats"] * 100
                if day["seats"] > 0 else 0.0
            )

    return agg


# ------------------------------------------------------------------
# 7. Save & print
# ------------------------------------------------------------------
def save_results(agg: Dict, filename: str = "EVENTdata.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(agg, f, indent=2, ensure_ascii=False)
    print(f"💾 Data saved to {filename}")


def print_summary(agg: Dict):
    if not agg:
        print("No data to summarise.")
        return

    print("\n" + "=" * 80)
    print(f"🎬 Event Cinemas AU - {len(agg)} Indian-language movies")
    print("=" * 80)

    for movie_id, data in sorted(
        agg.items(), key=lambda x: x[1]["totalShows"], reverse=True
    ):
        langs = ", ".join(data["languages"]) or "-"
        print(f"\n📽️  {data['movieName']}  [{langs}]  ({data['rating']})")
        print(f"   Shows: {data['totalShows']}")
        print(f"   Seats: {data['totalSeats']} total, "
              f"{data['totalSold']} sold, "
              f"{data['totalAvailable']} available, "
              f"occupancy: {data['occupancy']:.1f}%")
        print(f"   Gross: ${data['totalGross']:.2f}")
        print("   Day-wise:")
        for date_str, day in sorted(data["days"].items()):
            print(f"      {date_str}: {day['shows']} shows, "
                  f"{day['sold']}/{day['seats']} sold, "
                  f"{day['occupancy']:.1f}% occupancy, "
                  f"${day['gross']:.2f}")


# ------------------------------------------------------------------
# 8. Main
# ------------------------------------------------------------------
def main():
    print("🚀 Event Cinemas AU scraper starting...")

    print("📥 Fetching all movies (now showing + coming soon)...")
    all_movies = fetch_all_movies()
    print(f"✅ Total movies: {len(all_movies)}")

    indian = [m for m in all_movies if is_indian_language(m)]
    print(f"🎯 Found {len(indian)} Indian-language movies")
    for m in indian:
        print(f"   • {m.get('Name')} -> {matched_languages(m)}")

    if not indian:
        print("No matching movies found. Exiting.")
        return

    agg = process_movies(indian)

    if agg:
        save_results(agg)
        print_summary(agg)
    else:
        print("No data processed.")


if __name__ == "__main__":
    main()
