#!/usr/bin/env python3
"""
Production-level Hoyts Australia scraper - summary only.
Fetches Indian-language movies (Hindi, Tamil, Telugu, Kannada, Malayalam),
retrieves seat occupancy for each show, and saves aggregated data to AUSdata.json.
No venue breakdown - only totals + day-wise summaries.
"""

import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Tuple

import requests

# ------------------------------------------------------------------
# 1. Configuration
# ------------------------------------------------------------------
KEYWORDS = {"hindi", "tamil", "telugu", "kannada", "malayalam"}
MAX_WORKERS = 10          # lowered: CI runners get rate-limited fast
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0       # seconds, doubled each attempt

# Optional proxy - read from env so it can come from GitHub Secrets
PROXY = os.environ.get("HOYTS_PROXY", "").strip()
PROXY_LIST = [PROXY] if PROXY else []

# ------------------------------------------------------------------
# 2. Auto-generative headers & proxy helpers
# ------------------------------------------------------------------
# NOTE: fake_useragent can emit UAs that Cloudflare flags. Use a small,
# realistic pool instead so the API reliably returns JSON.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

ACCEPT_LANGUAGES = [
    "en-AU,en;q=0.9",
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
]


def get_random_headers() -> Dict[str, str]:
    ua = random.choice(USER_AGENTS)
    platform = "Windows"
    mobile = "?0"
    if "iPhone" in ua or "iPad" in ua:
        platform, mobile = "iOS", "?1"
    elif "Android" in ua:
        platform, mobile = "Android", "?1"
    elif "Macintosh" in ua:
        platform = "macOS"
    elif "Linux" in ua:
        platform = "Linux"

    sec_ch_ua = '"Chromium";v="133", "Google Chrome";v="133", "Not-A.Brand";v="24"'
    m = re.search(r"Firefox/(\d+)", ua)
    if m:
        sec_ch_ua = f'"Firefox";v="{m.group(1)}", "Gecko";v="{m.group(1)}"'

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Cache-Control": "no-cache",
        "Origin": "https://www.hoyts.com.au",
        "Pragma": "no-cache",
        "Priority": "u=1, i",
        "Referer": "https://www.hoyts.com.au/",
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": mobile,
        "Sec-CH-UA-Platform": f'"{platform}"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
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
    """Parse JSON, or print a readable diagnostic and raise."""
    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError as e:
        ctype = resp.headers.get("Content-Type", "unknown")
        preview = (resp.text or "")[:800].replace("\n", " ")
        print(f"❌ {label}: non-JSON response")
        print(f"   status={resp.status_code} content-type={ctype}")
        print(f"   body[:800]={preview!r}")
        raise e


# ------------------------------------------------------------------
# 3. API endpoints
# ------------------------------------------------------------------
CINEMA_BASE = "https://apim-aea.hoyts.com.au/cinemaapi-au-live/api"
TICKET_BASE = "https://apim-aea.hoyts.com.au/ticketing-au-live/api/v1"


def fetch_movies() -> List[Dict]:
    url = f"{CINEMA_BASE}/movies"
    resp = request_with_retry("GET", url)
    return safe_json(resp, "fetch_movies")


def fetch_all_sessions() -> List[Dict]:
    url = f"{CINEMA_BASE}/sessions"
    resp = request_with_retry("GET", url)
    data = safe_json(resp, "fetch_all_sessions")
    # The API sometimes wraps sessions in {"sessions": [...]} or returns a bare list.
    if isinstance(data, dict):
        for key in ("sessions", "items", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def fetch_seat_map(cinema_id: str, session_id: int) -> Dict:
    url = f"{TICKET_BASE}/ticket/seats/{cinema_id}/{session_id}/"
    resp = request_with_retry("GET", url)
    return safe_json(resp, f"fetch_seat_map({cinema_id}/{session_id})")


# ------------------------------------------------------------------
# 4. Seat-map parsing
# ------------------------------------------------------------------
def parse_seat_map(seat_map: Dict) -> Tuple[int, int, int]:
    total = 0
    sold = 0
    unavailable = 0
    for row in seat_map.get("rows", []):
        for seat in row.get("seats", []):
            if seat.get("typeId") == "gap":
                continue
            total += 1
            if seat.get("sold", False):
                sold += 1
            if seat.get("unavailable", False):
                unavailable += 1
    return total, sold, unavailable


# ------------------------------------------------------------------
# 5. Main processing - summary only (no venue breakdown)
# ------------------------------------------------------------------
def filter_movies_by_keyword(movies: List[Dict], keywords: Set[str]) -> List[Dict]:
    return [m for m in movies if any(kw in m.get("name", "").lower() for kw in keywords)]


def process_movies(movies: List[Dict], sessions: List[Dict]) -> Dict:
    sessions_by_movie = {}
    for s in sessions:
        movie_id = s.get("movieId")
        if movie_id:
            sessions_by_movie.setdefault(movie_id, []).append(s)

    tasks = []
    for movie in movies:
        movie_id = movie.get("vistaId")
        if not movie_id:
            continue
        for session in sessions_by_movie.get(movie_id, []):
            tasks.append((movie, session))

    print(f"📡 Fetching seat maps for {len(tasks)} sessions...")

    lock = threading.Lock()
    session_results = []

    def process_task(movie, session):
        cid = session["cinemaId"]
        sid = session["id"]
        try:
            seat_map = fetch_seat_map(cid, sid)
            total, sold, unavailable = parse_seat_map(seat_map)
            return {
                "movie": movie,
                "session": session,
                "totalSeats": total,
                "soldSeats": sold,
            }
        except Exception as e:
            print(f"⚠️  Failed seat map for {cid}/{sid}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_task = {executor.submit(process_task, m, s): (m, s) for m, s in tasks}
        for future in as_completed(future_to_task):
            res = future.result()
            if res is not None:
                with lock:
                    session_results.append(res)

    print(f"✅ Successfully processed {len(session_results)} sessions.")

    agg = {}
    for r in session_results:
        movie = r["movie"]
        movie_id = movie["vistaId"]
        session = r["session"]
        date_str = (session.get("date") or "").split("T")[0] or "Unknown"

        if movie_id not in agg:
            agg[movie_id] = {
                "movieName": movie.get("name", ""),
                "slug": movie.get("slug", ""),
                "totalShows": 0,
                "totalSeats": 0,
                "totalSold": 0,
                "days": {},
            }
        movie_data = agg[movie_id]
        movie_data["totalShows"] += 1
        movie_data["totalSeats"] += r["totalSeats"]
        movie_data["totalSold"] += r["soldSeats"]

        if date_str not in movie_data["days"]:
            movie_data["days"][date_str] = {"shows": 0, "seats": 0, "sold": 0}
        day = movie_data["days"][date_str]
        day["shows"] += 1
        day["seats"] += r["totalSeats"]
        day["sold"] += r["soldSeats"]

    for movie_data in agg.values():
        movie_data["occupancy"] = (
            movie_data["totalSold"] / movie_data["totalSeats"] * 100
            if movie_data["totalSeats"] > 0 else 0.0
        )
        for day_data in movie_data["days"].values():
            day_data["occupancy"] = (
                day_data["sold"] / day_data["seats"] * 100
                if day_data["seats"] > 0 else 0.0
            )

    return agg


# ------------------------------------------------------------------
# 6. Save and print summary
# ------------------------------------------------------------------
def save_results(agg: Dict, filename: str = "AUSdata.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(agg, f, indent=2, ensure_ascii=False)
    print(f"💾 Data saved to {filename}")


def print_summary(agg: Dict):
    if not agg:
        print("No data to summarise.")
        return

    print("\n" + "=" * 80)
    print(f"🎬 Summary for {len(agg)} Indian-language movies (totals + day-wise)")
    print("=" * 80)

    for movie_id, data in sorted(agg.items(), key=lambda x: x[1]["totalShows"], reverse=True):
        print(f"\n📽️  {data['movieName']} (slug: {data['slug']})")
        print(f"   Shows: {data['totalShows']}")
        print(f"   Seats: {data['totalSeats']} total, {data['totalSold']} sold, "
              f"occupancy: {data['occupancy']:.1f}%")
        print("   Day-wise:")
        for date_str, day in sorted(data["days"].items()):
            print(f"      {date_str}: {day['shows']} shows, "
                  f"{day['sold']}/{day['seats']} sold, "
                  f"{day['occupancy']:.1f}% occupancy")


# ------------------------------------------------------------------
# 7. Main
# ------------------------------------------------------------------
def main():
    print("🚀 Hoyts Australia scraper starting...")

    print("📥 Fetching all movies...")
    movies = fetch_movies()
    print(f"✅ Total movies: {len(movies)}")

    filtered = filter_movies_by_keyword(movies, KEYWORDS)
    print(f"🎯 Found {len(filtered)} movies with keywords: {', '.join(KEYWORDS)}")

    if not filtered:
        print("No matching movies found. Exiting.")
        return

    print("📥 Fetching all sessions...")
    sessions = fetch_all_sessions()
    print(f"✅ Total sessions: {len(sessions)}")

    agg = process_movies(filtered, sessions)

    if agg:
        save_results(agg)
        print_summary(agg)
    else:
        print("No data processed.")


if __name__ == "__main__":
    main()
