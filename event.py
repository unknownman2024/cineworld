#!/usr/bin/env python3
"""
Event Cinemas Australia scraper - anti-detect edition.

Features:
  * Cloudscraper-enhanced engine (Cloudflare v2/v3, brotli, stealth mode)
  * Automatic proxy rotation with self-IP fallback
  * Randomized browser headers per request
  * Random human-like delays
  * Seat-map aggregation -> EVENTdata.json
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

import cloudscraper  # from cloudscraper-enhanced

# ------------------------------------------------------------------
# 1. Configuration
# ------------------------------------------------------------------
KEYWORDS = {"hindi", "tamil", "telugu", "malayalam", "kannada"}
CINE_INDIA_ATTRIBUTE = "cine india"

MAX_WORKERS = 6               # lower because proxies add latency
REQUEST_TIMEOUT = 40
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 3.0

# ------------------------------------------------------------------
# 2. Proxy rotation (self IP fallback)
# ------------------------------------------------------------------
# Read a comma-separated list from the environment.
# Example: "http://user:pass@1.2.3.4:8080,http://5.6.7.8:3128"
# Leave empty to use your own IP (self-IP mode).
_raw_proxies = os.environ.get("EVENT_PROXIES", "").strip()
PROXY_LIST: List[str] = [p.strip() for p in _raw_proxies.split(",") if p.strip()]

# Track which proxies have failed recently so we stop hammering them.
_bad_proxies: Dict[str, float] = {}
_BAN_TIME = 300  # seconds before retrying a failed proxy

_rotation_lock = threading.Lock()
_rotation_index = 0


def _available_proxies() -> List[str]:
    """Return proxies that are not currently banned. Empty list = self IP."""
    now = time.time()
    with _rotation_lock:
        alive = [p for p in PROXY_LIST if _bad_proxies.get(p, 0) < now]
    return alive


def get_next_proxy() -> Optional[Dict[str, str]]:
    """
    Round-robin through healthy proxies. Returns None when the list is empty
    or every proxy is banned -> caller falls back to the self IP.
    """
    global _rotation_index
    alive = _available_proxies()
    if not alive:
        return None
    with _rotation_lock:
        proxy = alive[_rotation_index % len(alive)]
        _rotation_index += 1
    return {"http": proxy, "https": proxy}


def mark_proxy_bad(proxy_url: str):
    """Ban a proxy for _BAN_TIME seconds after a failure."""
    with _rotation_lock:
        _bad_proxies[proxy_url] = time.time() + _BAN_TIME
        print(f"🚫 Proxy banned for {_BAN_TIME}s: {proxy_url}")


# ------------------------------------------------------------------
# 3. Cloudscraper session builder
# ------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
    "Gecko/20100101 Firefox/133.0",
]

ACCEPT_LANGUAGES = ["en-AU,en;q=0.9", "en-US,en;q=0.9", "en-GB,en;q=0.9"]


def build_headers() -> Dict[str, str]:
    """Fresh randomized browser headers for every request."""
    ua = random.choice(USER_AGENTS)
    platform = "Windows"
    mobile = "?0"
    if "Macintosh" in ua:
        platform = "macOS"
    elif "Linux" in ua:
        platform = "Linux"

    sec_ch_ua = '"Chromium";v="133", "Google Chrome";v="133", "Not-A.Brand";v="24"'
    m = re.search(r"Firefox/(\d+)", ua)
    if m:
        sec_ch_ua = f'"Firefox";v="{m.group(1)}", "Gecko";v="{m.group(1)}"'

    return {
        "Accept": "application/json, text/plain, */*",
        # Brotli is handled by cloudscraper-enhanced, but we still avoid
        # advertising it explicitly so the CDN chooses a format we control.
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Cache-Control": "no-cache",
        "Origin": "https://www.eventcinemas.com.au",
        "Pragma": "no-cache",
        "Referer": "https://www.eventcinemas.com.au/",
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": mobile,
        "Sec-CH-UA-Platform": f'"{platform}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": ua,
    }


def create_session() -> cloudscraper.CloudScraper:
    """
    Create a Cloudscraper session with stealth defaults.

    The `browser` dict is deliberately explicit so cloudscraper-enhanced
    picks desktop Chrome UAs only, matching the rest of our headers.
    """
    return cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        },
        enable_stealth=True,
        stealth_options={
            "min_delay": 1.0,
            "max_delay": 4.0,
            "human_like_delays": True,
            "randomize_headers": True,
            "browser_quirks": True,
        },
        # Brotli on in case the CDN forces Content-Encoding: br
        allow_brotli=True,
    )


# ------------------------------------------------------------------
# 4. Resilient request wrapper
# ------------------------------------------------------------------
def request_with_retry(
    method: str,
    url: str,
    session: cloudscraper.CloudScraper,
    **kwargs,
) -> "requests.Response":
    """
    Send a request through the current proxy (if any), retrying on failure.
    On proxy errors the proxy is banned and the next attempt uses a fresh one
    — eventually falling back to the self IP.
    """
    headers = kwargs.pop("headers", {})
    headers.update(build_headers())

    last_exc: Optional[Exception] = None

    for attempt in range(RETRY_ATTEMPTS):
        proxy = get_next_proxy()
        proxy_url = proxy["http"] if proxy else None

        try:
            resp = session.request(
                method, url,
                headers=headers,
                proxies=proxy,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            via = proxy_url or "self-IP"
            print(f"⚠️  Attempt {attempt + 1}/{RETRY_ATTEMPTS} via {via} "
                  f"failed for {url} (status={status}): {e}")
            if proxy_url:
                mark_proxy_bad(proxy_url)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue

    raise RuntimeError(f"Max retries exceeded for {url}: {last_exc}")


def safe_json(resp, label: str):
    try:
        return resp.json()
    except Exception as e:
        ctype = resp.headers.get("Content-Type", "unknown")
        cenc = resp.headers.get("Content-Encoding", "none")
        preview = (resp.text or "")[:800].replace("\n", " ")
        print(f"❌ {label}: non-JSON response")
        print(f"   status={resp.status_code} content-type={ctype} "
              f"content-encoding={cenc}")
        print(f"   body[:800]={preview!r}")
        raise e


# ------------------------------------------------------------------
# 5. API endpoints
# ------------------------------------------------------------------
EVENT_BASE = "https://www.eventcinemas.com.au"
TICKET_API = "https://eventcinemas.text2024mail.workers.dev"


def fetch_now_showing(session) -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetNowShowing"
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, "fetch_now_showing")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_coming_soon(session) -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetComingSoon"
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, "fetch_coming_soon")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_all_movies(session) -> List[Dict]:
    now = fetch_now_showing(session)
    soon = fetch_coming_soon(session)
    merged: Dict[int, Dict] = {}
    for m in now + soon:
        mid = m.get("Id")
        if mid is not None:
            merged[mid] = m
    return list(merged.values())


def fetch_sessions_for_cinemas(session, cinema_ids: List[int], date_str: str) -> List[Dict]:
    if not cinema_ids:
        return []
    params = "&".join(f"cinemaIds={cid}" for cid in cinema_ids)
    url = f"{TICKET_API}/Cinemas/GetSessions?{params}&date={date_str}"
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, f"fetch_sessions({date_str})")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_seat_map(session, session_id: int) -> Dict:
    url = f"{TICKET_API}/api/ticketing/session?sessionId={session_id}"
    resp = request_with_retry("GET", url, session)
    return safe_json(resp, f"fetch_seat_map({session_id})")


# ------------------------------------------------------------------
# 6. Language detection
# ------------------------------------------------------------------
def movie_languages(movie: Dict) -> Set[str]:
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
    langs = movie_languages(movie)
    return sorted(kw for kw in KEYWORDS if kw in langs)


# ------------------------------------------------------------------
# 7. Seat-map parsing
# ------------------------------------------------------------------
def parse_seat_map(seat_data: Dict) -> Tuple[int, int, int, float]:
    total = sold = available = 0
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
    for t in (seat_data.get("Data") or {}).get("Tickets") or []:
        if (t.get("Name") or "").strip().lower() == "adult":
            try:
                adult_price = float(t.get("Price") or 0)
            except (TypeError, ValueError):
                adult_price = 0.0
            break
    return total, sold, available, adult_price


# ------------------------------------------------------------------
# 8. Aggregation
# ------------------------------------------------------------------
def date_range(days: int = 7) -> List[str]:
    today = datetime.utcnow().date()
    return [(today + timedelta(days=i)).isoformat() for i in range(days)]


def collect_sessions_for_movie(session, movie: Dict, cinema_ids: List[int], dates: List[str]) -> List[Dict]:
    movie_name = movie.get("Name", "")
    all_sessions: List[Dict] = []
    for date_str in dates:
        try:
            movies_on_date = fetch_sessions_for_cinemas(session, cinema_ids, date_str)
        except Exception as e:
            print(f"⚠️  Sessions fetch failed for {date_str}: {e}")
            continue
        for m in movies_on_date:
            if (m.get("Name") or "").strip() != movie_name.strip():
                continue
            for cinema in m.get("CinemaModels") or []:
                for s in cinema.get("Sessions") or []:
                    all_sessions.append({
                        "cinemaId": s.get("CinemaId"),
                        "cinemaName": cinema.get("Name"),
                        "sessionId": s.get("Id"),
                        "startTime": s.get("StartTime"),
                        "movieId": s.get("MovieId"),
                    })
    return all_sessions


def process_movies(movies: List[Dict]) -> Dict:
    # One session per thread to avoid cloudscraper state clashes.
    # Cloudscraper sessions are not documented as thread-safe, so we create
    # a fresh one inside each worker via thread-local storage.
    thread_local = threading.local()

    def get_session() -> cloudscraper.CloudScraper:
        if not hasattr(thread_local, "session"):
            thread_local.session = create_session()
        return thread_local.session

    dates = date_range(7)
    tasks: List[Tuple[Dict, Dict]] = []

    # Session discovery is done sequentially with the main session so we
    # don't hammer the worker proxy pool with discovery traffic.
    main_session = create_session()
    for movie in movies:
        cinema_ids = movie.get("CinemaIds") or []
        if not cinema_ids:
            continue
        sessions = collect_sessions_for_movie(main_session, movie, cinema_ids, dates)
        for s in sessions:
            tasks.append((movie, s))

    print(f"📡 Fetching seat maps for {len(tasks)} sessions...")

    lock = threading.Lock()
    session_results: List[Dict] = []

    def process_task(movie: Dict, session_info: Dict):
        sid = session_info.get("sessionId")
        if sid is None:
            return None
        sess = get_session()
        try:
            seat_data = fetch_seat_map(sess, sid)
            total, sold, available, price = parse_seat_map(seat_data)
            return {
                "movie": movie,
                "session": session_info,
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

    agg: Dict[int, Dict] = {}
    for r in session_results:
        movie = r["movie"]
        movie_id = movie.get("Id")
        if movie_id is None:
            continue
        s = r["session"]
        start = s.get("startTime") or ""
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
            md["days"][date_str] = {"shows": 0, "seats": 0, "sold": 0,
                                    "available": 0, "gross": 0.0}
        day = md["days"][date_str]
        day["shows"] += 1
        day["seats"] += r["totalSeats"]
        day["sold"] += r["soldSeats"]
        day["available"] += r["availableSeats"]
        day["gross"] += r["soldSeats"] * r["adultPrice"]

    for md in agg.values():
        md["occupancy"] = (
            md["totalSold"] / md["totalSeats"] * 100 if md["totalSeats"] > 0 else 0.0
        )
        for day in md["days"].values():
            day["occupancy"] = (
                day["sold"] / day["seats"] * 100 if day["seats"] > 0 else 0.0
            )
    return agg


# ------------------------------------------------------------------
# 9. Save & print
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
    for movie_id, data in sorted(agg.items(), key=lambda x: x[1]["totalShows"], reverse=True):
        langs = ", ".join(data["languages"]) or "-"
        print(f"\n📽️  {data['movieName']}  [{langs}]  ({data['rating']})")
        print(f"   Shows: {data['totalShows']}")
        print(f"   Seats: {data['totalSeats']} total, {data['totalSold']} sold, "
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
# 10. Main
# ------------------------------------------------------------------
def main():
    print("🚀 Event Cinemas AU scraper starting (anti-detect mode)...")
    print(f"🔁 Proxy mode: {len(PROXY_LIST)} proxies configured"
          if PROXY_LIST else "🔁 Proxy mode: self-IP (direct)")

    session = create_session()

    print("📥 Fetching all movies (now showing + coming soon)...")
    all_movies = fetch_all_movies(session)
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
