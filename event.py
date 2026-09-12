#!/usr/bin/env python3
"""
Event Cinemas Australia scraper - anti-detect edition.

Features:
  * Cloudscraper (falls back gracefully between enhanced and base)
  * Automatic proxy rotation with self-IP fallback
  * Randomized browser headers per request
  * Australia/Sydney "today" for correct date windows
  * 3-day window starting at max(today_au, movie_release_date)
  * Verbose progress logging - works on Replit, GitHub Actions, or locally
"""

import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    ZoneInfo = None  # type: ignore

import cloudscraper


# ------------------------------------------------------------------
# 0. Logging
# ------------------------------------------------------------------
def log(msg: str, level: str = "INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    tag = {
        "INFO": "INFO ",
        "OK":   "OK   ",
        "WARN": "WARN ",
        "ERR":  "ERROR",
        "STEP": "STEP ",
        "NET":  "NET  ",
        "DATA": "DATA ",
    }.get(level, "     ")
    print(f"[{ts}] {tag} {msg}", flush=True)


# ------------------------------------------------------------------
# 1. Configuration
# ------------------------------------------------------------------
KEYWORDS = {"hindi", "tamil", "telugu", "malayalam", "kannada"}
CINE_INDIA_ATTRIBUTE = "cine india"

MAX_WORKERS = 6
REQUEST_TIMEOUT = 40
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 3.0
DAY_WINDOW = 3

AU_TZ_NAME = "Australia/Sydney"
if ZoneInfo is not None:
    try:
        AU_TZ = ZoneInfo(AU_TZ_NAME)
    except Exception:
        AU_TZ = timezone(timedelta(hours=10))
else:
    AU_TZ = timezone(timedelta(hours=10))


def today_au() -> datetime:
    return datetime.now(AU_TZ)


# ------------------------------------------------------------------
# 2. Proxy rotation
# ------------------------------------------------------------------
_raw_proxies = os.environ.get("EVENT_PROXIES", "").strip()
PROXY_LIST: List[str] = [p.strip() for p in _raw_proxies.split(",") if p.strip()]

_bad_proxies: Dict[str, float] = {}
_BAN_TIME = 300
_rotation_lock = threading.Lock()
_rotation_index = 0


def _available_proxies() -> List[str]:
    now = time.time()
    with _rotation_lock:
        return [p for p in PROXY_LIST if _bad_proxies.get(p, 0) < now]


def get_next_proxy() -> Optional[Dict[str, str]]:
    global _rotation_index
    alive = _available_proxies()
    if not alive:
        return None
    with _rotation_lock:
        proxy = alive[_rotation_index % len(alive)]
        _rotation_index += 1
    return {"http": proxy, "https": proxy}


def mark_proxy_bad(proxy_url: str):
    with _rotation_lock:
        _bad_proxies[proxy_url] = time.time() + _BAN_TIME
        log(f"Proxy banned for {_BAN_TIME}s: {proxy_url}", "WARN")


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
    Build a Cloudscraper session.

    Tries cloudscraper-enhanced's stealth kwargs first. If the installed
    package is the original cloudscraper (no stealth support), falls back
    to a plain create_scraper().
    """
    try:
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            enable_stealth=True,
            stealth_options={
                "min_delay": 1.0,
                "max_delay": 4.0,
                "human_like_delays": True,
                "randomize_headers": True,
                "browser_quirks": True,
            },
            allow_brotli=True,
        )
    except TypeError as e:
        log(f"cloudscraper-enhanced kwargs rejected ({e}); "
            f"falling back to base cloudscraper", "WARN")
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows"},
        )
        try:
            import brotli  # noqa: F401
            log("brotli decoder available for fallback session", "INFO")
        except ImportError:
            log("brotli package missing - br responses will fail. "
                "Install with: pip install brotli", "WARN")
        return scraper


# ------------------------------------------------------------------
# 4. Resilient request wrapper
# ------------------------------------------------------------------
def request_with_retry(method, url, session, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.update(build_headers())

    last_exc: Optional[Exception] = None
    for attempt in range(RETRY_ATTEMPTS):
        proxy = get_next_proxy()
        proxy_url = proxy["http"] if proxy else None
        via = proxy_url or "self-IP"

        try:
            resp = session.request(
                method, url,
                headers=headers, proxies=proxy,
                timeout=REQUEST_TIMEOUT, **kwargs,
            )
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            log(f"Attempt {attempt + 1}/{RETRY_ATTEMPTS} via {via} failed "
                f"(status={status}) for {url}: {e}", "WARN")
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
        log(f"{label}: non-JSON response "
            f"(status={resp.status_code}, ct={ctype}, ce={cenc})", "ERR")
        log(f"body[:800]={preview!r}", "ERR")
        raise e


# ------------------------------------------------------------------
# 5. API endpoints
# ------------------------------------------------------------------
EVENT_BASE = "https://www.eventcinemas.com.au"

def fetch_now_showing(session) -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetNowShowing"
    log(f"GET {url}", "NET")
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, "fetch_now_showing")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_coming_soon(session) -> List[Dict]:
    url = f"{EVENT_BASE}/Movies/GetComingSoon"
    log(f"GET {url}", "NET")
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, "fetch_coming_soon")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_all_movies(session) -> List[Dict]:
    now = fetch_now_showing(session)
    log(f"now-showing: {len(now)} movies", "DATA")
    soon = fetch_coming_soon(session)
    log(f"coming-soon: {len(soon)} movies", "DATA")
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
    url = f"{EVENT_BASE}/Cinemas/GetSessions?{params}&date={date_str}"
    log(f"GET sessions date={date_str} cinemas={len(cinema_ids)}", "NET")
    resp = request_with_retry("GET", url, session)
    data = safe_json(resp, f"fetch_sessions({date_str})")
    return (data.get("Data") or {}).get("Movies") or []


def fetch_seat_map(session, session_id: int) -> Dict:
    url = f"{EVENT_BASE}/api/ticketing/session?sessionId={session_id}"
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
# 8. Date helpers
# ------------------------------------------------------------------
def parse_release_date(movie: Dict) -> Optional[datetime]:
    raw = movie.get("ReleasedAt")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=AU_TZ)
        except ValueError:
            continue
    return None


def build_date_window(movie: Dict, days: int = DAY_WINDOW) -> List[str]:
    today = today_au().replace(hour=0, minute=0, second=0, microsecond=0)
    release = parse_release_date(movie)

    start = today
    if release is not None and release > today:
        start = release

    return [(start + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days)]


# ------------------------------------------------------------------
# 9. Session collection
# ------------------------------------------------------------------
def collect_sessions_for_movie(session, movie, cinema_ids, dates):
    movie_name = movie.get("Name", "")
    all_sessions: List[Dict] = []
    for date_str in dates:
        try:
            movies_on_date = fetch_sessions_for_cinemas(session, cinema_ids, date_str)
        except Exception as e:
            log(f"Sessions fetch failed for {movie_name} @ {date_str}: {e}", "WARN")
            continue

        matched_here = 0
        for m in movies_on_date:
            if (m.get("Name") or "").strip() != movie_name.strip():
                continue
            for cinema in m.get("CinemaModels") or []:
                for s in cinema.get("Sessions") or []:
                    matched_here += 1
                    all_sessions.append({
                        "cinemaId": s.get("CinemaId"),
                        "cinemaName": cinema.get("Name"),
                        "sessionId": s.get("Id"),
                        "startTime": s.get("StartTime"),
                        "movieId": s.get("MovieId"),
                    })
        log(f"  {movie_name} @ {date_str}: {matched_here} sessions", "DATA")
    return all_sessions


# ------------------------------------------------------------------
# 10. Aggregation
# ------------------------------------------------------------------
def process_movies(movies: List[Dict]) -> Dict:
    thread_local = threading.local()

    def get_session():
        if not hasattr(thread_local, "session"):
            thread_local.session = create_session()
        return thread_local.session

    log("Phase 1 - session discovery", "STEP")
    main_session = create_session()
    tasks: List[Tuple[Dict, Dict]] = []
    per_movie_dates: Dict[int, List[str]] = {}

    for movie in movies:
        movie_id = movie.get("Id")
        cinema_ids = movie.get("CinemaIds") or []
        if not cinema_ids:
            log(f"  skip {movie.get('Name')}: no CinemaIds", "WARN")
            continue

        dates = build_date_window(movie)
        per_movie_dates[movie_id] = dates
        release_str = movie.get("ReleasedAt") or "unknown"
        log(f"  {movie.get('Name')} | release={release_str} | window={dates}")

        sessions = collect_sessions_for_movie(main_session, movie, cinema_ids, dates)
        for s in sessions:
            tasks.append((movie, s))

    log(f"Discovered {len(tasks)} total sessions across "
        f"{len(movies)} movie(s)", "OK")

    if not tasks:
        return {}

    log(f"Phase 2 - fetching {len(tasks)} seat maps "
        f"({MAX_WORKERS} workers)", "STEP")

    lock = threading.Lock()
    session_results: List[Dict] = []
    done = [0]

    def process_task(movie, session_info):
        sid = session_info.get("sessionId")
        if sid is None:
            return None
        sess = get_session()
        try:
            seat_data = fetch_seat_map(sess, sid)
            total, sold, available, price = parse_seat_map(seat_data)
            with lock:
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(tasks):
                    log(f"  progress: {done[0]}/{len(tasks)} seat maps", "DATA")
            return {
                "movie": movie,
                "session": session_info,
                "totalSeats": total,
                "soldSeats": sold,
                "availableSeats": available,
                "adultPrice": price,
            }
        except Exception as e:
            with lock:
                done[0] += 1
            log(f"  seat map failed for session {sid}: {e}", "WARN")
            return None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_task, m, s): (m, s) for m, s in tasks}
        for future in as_completed(futures):
            res = future.result()
            if res is not None:
                with lock:
                    session_results.append(res)

    log(f"Seat maps OK: {len(session_results)}/{len(tasks)}", "OK")

    log("Phase 3 - aggregating totals + day-wise", "STEP")
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
                "releaseDate": (movie.get("ReleasedAt") or "").split("T")[0],
                "window": per_movie_dates.get(movie_id, []),
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

    log(f"Aggregated {len(agg)} movie(s)", "OK")
    return agg


# ------------------------------------------------------------------
# 11. Save & print
# ------------------------------------------------------------------
def save_results(agg: Dict, filename: str = "EVENTdata.json"):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(agg, f, indent=2, ensure_ascii=False)
    log(f"Data saved to {filename}", "OK")


def print_summary(agg: Dict):
    if not agg:
        log("No data to summarise.", "WARN")
        return
    print("\n" + "=" * 80)
    print(f"Event Cinemas AU - {len(agg)} Indian-language movies")
    print("=" * 80)
    for _, data in sorted(agg.items(), key=lambda x: x[1]["totalShows"], reverse=True):
        langs = ", ".join(data["languages"]) or "-"
        print(f"\n  {data['movieName']}  [{langs}]  ({data['rating']})")
        print(f"   Release: {data['releaseDate']}  Window: {data['window']}")
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
# 12. Main
# ------------------------------------------------------------------
def main():
    started = time.time()
    log("Event Cinemas AU scraper starting (anti-detect mode)", "STEP")
    log(f"Host OS: {sys.platform} | Python {sys.version.split()[0]}")
    log(f"Proxy mode: {len(PROXY_LIST)} proxies configured"
        if PROXY_LIST else "Proxy mode: self-IP (direct)")
    log(f"Reference 'today' (Australia/Sydney): "
        f"{today_au().strftime('%Y-%m-%d %H:%M %Z')}")
    log(f"Scrape window per movie: {DAY_WINDOW} days "
        f"starting at max(today_au, release)")

    session = create_session()

    log("Fetching all movies (now showing + coming soon)", "STEP")
    all_movies = fetch_all_movies(session)
    log(f"Total movies: {len(all_movies)}", "OK")

    indian = [m for m in all_movies if is_indian_language(m)]
    log(f"Indian-language matches: {len(indian)}", "OK")
    for m in indian:
        log(f"   - {m.get('Name')} -> {matched_languages(m)}")

    if not indian:
        log("No matching movies found. Exiting.", "WARN")
        return

    agg = process_movies(indian)
    if agg:
        save_results(agg)
        print_summary(agg)
    else:
        log("No data processed.", "WARN")

    elapsed = time.time() - started
    log(f"Done in {elapsed:.1f}s", "OK")


if __name__ == "__main__":
    main()
