# ============================================================
#  LANDMARK CINEMAS – HINDI MOVIES SCRAPER (Direct + Logging)
#  • No proxies
#  • Retries on showtimes + seatmap requests (exponential backoff)
#  • Random rotating headers (UA, sec-ch-ua, accept-language)
#  • Fixed cookies + XSRF token (same for every request)
#  • Concurrency = 1, 1–2s delay between seatmap requests
#  • Logs every seatmap request/response to logs.txt
#  • Dumps raw seatmap JSON to seatmapdump/<sessionId>.json
#  • EXACT payload: SessionId as string, CinemaId as int
#  • Proper seat map parser (areas, wheelchair, blocked seats)
# ============================================================
#  pip install requests
#  Run: python3 landmark_direct.py
# ============================================================

import os
import re
import csv
import json
import time
import random
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== CONFIGURATION ==========
KEYWORD = 'hindi'
CHECK_DATE = '2026-09-11'
PRICE_MAP = {'2D': 14, 'IMAX': 18}
CONCURRENCY = 1                 # seatmap workers
MOVIES_CONCURRENCY = 1          # showtime workers
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3                 # per request (movies + seatmap)
RETRY_BACKOFF_BASE = 2.0        # seconds -> 2, 4, 8 ...
SEATMAP_MIN_DELAY = 1.0         # seconds between seatmap requests
SEATMAP_MAX_DELAY = 2.0
OUTPUT_CSV = 'landmark_hindi_results.csv'
LOG_FILE = 'logs.txt'
DUMP_DIR = 'seatmapdump'
# ===================================

# ============================================================
#  PASTE FULL COOKIE STRING FROM BROWSER HERE
#  DevTools → Network → /GetSessionSeatMap → Request Headers → cookie
# ============================================================
COOKIE_STRING = (
    '_ga=GA1.1.1510103123.1787225372; '
    '__qca=P1-dc8dc41f-3a1f-46f1-9e5e-9eaa756d3bf9; '
    '_aimtellSubscriberID=3ea26818-1336-02e8-e817-b22389992090; '
    'sbt_i=WY3NGI3NDg1MjsyMDM1ODgwNTk5NjQwNDg7MzA1YmQ4OTEtNTFiOS00ZDg0LThkZjgtZTBjZjE1Yjc3ODQ4OzZGJjMzUzOGUtYzI2NC00Y2EwLTk1MDItZTg2NTczMmVjMzQ4O2Q5M2QzMzdkLTg4NjUtNGExMi04OWVhLTU4NA=; '
    '.AspNet.Cookies=NYStMx7iCTuOouFVDxXoGRAckelkIx0Jt5UgonRtqtM_0lJIkMGuRsMr0WdHnPJiw-7bEtOsWZc7drtFhOvWw0Ph5xdZLgPPwXq6PvWzLF8QB_gJJD0SQbsxZVoEqqPfpxt42jSovV8I-QYlwi4FQRh_pNzvchELyhWAvzg3dKBzZ7jqM1r3MQOugUppFDkxW4hZ5-77AAJSek8hbjHKPuy4FvtDd1A5-YhAObe53BcJTAY6OVlWoNrspnfxe3IBOClFOKzzq0BwJVPWsQdimDgUtKn2_DSYWH8fQ4thhzn4L-5cehDjCwcakGUDwiv7f0sJPWah8BYfeGavjqydTpFfI5lwz_mFFqsCDkSHnfzdNocqJ2Uripw6g6XEcKOLl19WFp93SayPs_va-sVr8ipMr2VBTUXK99heCUzB15RThwxf0wJrH3ClJ5djrkIEaQHbSAzT0zgUu3bCb4kQLz0ALmfUYZFsBYQbgtXvF7sXgGcbd8qTbiWvb3N4pewgu4IBhpHRrwsM_ReQ8bq-C300XngP2h7UJ3LC1qFGCFqcrqCV8y7NHBpVI8AKpnJaKrw0poczKpWryr1a6wJ6--2vqY-NeB48x72gkS_BBFvv8Gfzj-ChAzyqaaRY9ogtjH451BwbbRcFIu5RLvslwviA3RK_CVoWJ7l93AOWFSWq2gPxOVm_DuNO7-btDaeEB0LPx9t_m5IlJOYQiJDHufxLioERnv5AocE6HpEwIypOgUIqcmmG2tpN4kLG_CVAvioEyz3lsj1s5b_G0KrY9MGFzH0gGZ-MlSb1rD1ZmxaNtjX1_0ousSYuaHqAUnPEF4IGzG2rIZlykbRphpu5RDOxASshCALA5zWh2gTWOonzzfJDKTi3plE4KtJCCFgYsFVgPQP8K57Q-G4H0n4F6TdSvhN3JGlfgg22sjOMDmW2JAgpYXIKbg6o8u9WJ5PXaqu5LQQV2ny-FHMSvhxfuAhytcEiFPhcpcinLT1DDon5SeS1euw1h9AJxtXRDBwg1Kh2MkBLrvOMLOKSTzeBDGgxqfkJYivDE4HFyY1ErciHDNGa2fvOrJiyPd-zp0xVJICuP4zdjJ56G5UXWig06hnziIAUnP5AwBKihfX290QrVa4ZG_W92kZ_6zCRXt6_4LOKGuqQ0aj6Vhn3BbiVG9Qxy3O6lcDOMRSts9NoBQ87Izk3Y4Z_6vZfsT5sou1OQdqHbRvBc-eGbITqYgoeb4PaKem6nICqDxlJNWCdenJ5DKOOEhrLu9Tbv3BYT6KQn36mH3QwRe7ndRj26iVehjkDxkbTSsZrL4P3DlNNcUofnTE_MVzVH8D81M10XnCKOkyyaRqetqqY0Rn1YgdrPiBIfQYJjT4XCtvIDZEO3h5W8_FqX6VUpRAgZXunwJTWFSm3vMwo5FdnV29XJORrxHwLcByaU0R2va4b2rgExAl-agu1OGrPoUGgyQb64AKmzpnLVaUI2_tT87HoWaQy-eur-9iL_pz9UNTpqYOnj79aCi4nEs9i1XBrAF7CvfJ8JBKkWY4xTRqUUjeh6txT71zWn0FGeTKkxALMh9voNyW8iVqaC7OpoKIvB-aB_gFSM9v9hkffKTWqAK7VxNsV_FUXP7gIa2WVi9BiAH83btDivzFNLebFgSrgjF5UEZWjq1iMrv28-JyQQsJhdsVyYMBzxkoOaNIVhcJfjdnUkug4XbBe2-DvMzYTBiPVK7R-hM99-vBP2tIBSSmN8oqqeg7guGacJ65gniBQ8lVmN2g12A3Zy8kDoARAfrhV62wpYnBOdmVQHN_uRN7JxqhspgTRX8_N6wE_qvM6-l5pL29dU3UkBnaky2c1n-UyQvrg_-hcbBYcipxmgVrCH9chN6AY6Quv2gNVNkxB4jVMSnFKZTAQyUnNA9fMOfzzES8V7y1pSfyGCM-gj54n_iT-Nd3K7beY1VWv3AQ9bWotsQuEd7N7iUTwWhrk3fWhOdXqV9nM1wruG7OnQighv7T-Dz16ER7p3P482uLyGBB15l1gVTJ6k13AVkyD9i0eOS3AbZpjw_xsPz3KB6Pbk2DWF-pgU48y9JRL46d45uuyMQcFdYHMCTMkH1JRaV1VL8x9TcrXLbPPlWyyFFj4fkGAWMoIk6EfzzIBFNwrkqG9w7jZt62L7jrYDqx_248xaJc2NbNEsgRtsL6YcbB7um-8a2b8FuwCF0OaFxJc-iIkGiHNsZxYcOC4quWe_OjY_mcuBa_CLpwq3tBKMTbQKB8GaPmUD-WszBINzq6tLx9FWVvF901DwfmfjtsZUcv-5Kulo10CKxdK9LmtP5zK4SfAgByjO1nbip6822ubVmz5MH55E0OybP7P9ANaidwUY5nsfcRwUv87YQsqmo9vZDHqVk-vtzRRYQ5fEQUrQDn9D9y9N7vJd6mull9LK7POOH9zldqyytDGY9nmeE9BC_prCnu9fpy8QSqQMl7w_B-bk2_2T2tN9ExrxoGKeevBvwqbRlABk8xSiY5nrVeR0jij1hOewEnsmZKSz-S0ykdc8urz_Bi3i0bwOM3xF_dHOPTBhVxLyx9m0j_nM4WHfuGEO3DDb7TaeWCg8Sd56zoqAOHrSHK1P1HkASK7adFXENouT0-twCUsXC_lvWNNFlzNC7Y4RA9l2XwZKc2dYpf9K37nXpZhX9kEvXAtEMaxuMcSvvNqD4ayEYE6kEVBHoPyu_GYQ9sSE1hcDYNYu5KDMZUJ1rhnXmK7_4BGgjS-cAbpYjyzmINIMTNUTuby31QJiZP9PRd4wPBj5nEaLEL_Hno; '
    '_aimtellPromptApproved=true; '
    '_aimtellSubscriptionURL=https://www.landmarkcinemas.com/; '
    'LMC_TheatreId=7782; '
    'LMC_TheatreURL=%2Fshowtimes%2Fedson; '
    'LMC_TheatreName=%2Fnow-playing%2Fedson; '
    'm_ses=20260910133101; '
    '__cmpcccx97154=aCQqWB57AB_ovWB92taaY1k1evBZDTWDS0sLNNaIxGTANVMaAyM0jDqnhaEwJhTKxqTUmMK0VpMWomBNVqsEeV5RMiySMGhJgCyWRF6XkDABRIARgA; '
    'm_cnt=3; '
    '_ga_7M6ER5F56D=GS2.1.s1789027250$o3$g1$t1789028825$j38$l0$h0; '
    '_ga_F0SPX7GNS6=GS2.1.s1789027255$o3$g1$t1789028825$j38$l0$h0'
)

XSRF_TOKEN = (
    'o_CBOYZHVNJf0Xvp76dfIZg84NGBRa8Dj3MjHhWmDLaqUTf0yQjBHaT-Ae-z3-LZJ61t8oudyrVg97'
    'BotC3_ll4hcI8xWGa5TUMf7h3O5iY1:_eT_jfhctaEiByvipV6AGIKSPNhttuLMpFPMCNwYnVnw4uE'
    'NxzDDkBPPm2A_p2EbvDT3Gw3AAc22kHny3EcdWQ8vKn-AuI3Co85eVxqhHl8ObehY4TLIsIpEyW-mkwF'
    'mPrs9DLjyk0eicn0-jTmg4Q2'
)
# ============================================================

# ============================================================
#  RANDOM ROTATING HEADER PROFILES
#  (cookies + x-xsrf-token stay FIXED — only cosmetic headers rotate)
# ============================================================
HEADER_PROFILES = [
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
    },
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Google Chrome";v="152", "Chromium";v="152", "Not?A_Brand";v="24"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-CA,en;q=0.9,en-US;q=0.8,fr-CA;q=0.6',
    },
    {
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"macOS"',
        'accept-language': 'en-US,en;q=0.9',
    },
    {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
        'sec-ch-ua-platform': '"Linux"',
        'accept-language': 'en-GB,en;q=0.9,en-US;q=0.8',
    },
    {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'sec-ch-ua': '"Firefox";v="127", "Not)A;Brand";v="99"',
        'sec-ch-ua-platform': '"Windows"',
        'accept-language': 'en-CA,en;q=0.5',
    },
    {
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'sec-ch-ua': '"Firefox";v="127", "Not)A;Brand";v="99"',
        'sec-ch-ua-platform': '"Linux"',
        'accept-language': 'en-US,en;q=0.5',
    },
    {
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
        'sec-ch-ua': '"Safari";v="17", "Not?A_Brand";v="24"',
        'sec-ch-ua-platform': '"macOS"',
        'accept-language': 'en-CA,en;q=0.9',
    },
]

THEATRES = {
    177: 'Campbell River', 180: 'Caledon, Bolton', 181: 'Brandon',
    182: 'Edmonton City Centre', 184: 'Calgary Country Hills',
    186: 'Winnipeg, Grant Park', 187: 'Surrey, Guildford',
    188: 'Hamilton, Jackson Square', 189: 'Kanata', 190: 'Kingston',
    191: 'Kitchener', 192: 'London', 193: 'Orleans',
    194: 'St. Catharines, Pen Centre', 195: 'Penticton',
    196: 'Calgary Shawnessy', 197: 'Spruce Grove', 200: 'Waterloo',
    201: 'Whitby', 202: 'Winkler', 203: 'Courtenay', 204: 'Cranbrook',
    206: 'Drayton Valley', 207: 'West Kelowna, Xtreme', 209: 'Fort St. John',
    211: 'Kelowna, Grand 10', 213: 'Nanaimo', 214: 'New Westminster',
    217: 'Sylvan Lake', 220: 'Port Alberni', 7779: 'Brooks',
    7782: 'Edson', 7784: 'West Kelowna, Encore', 7795: 'St. Albert',
    7796: 'Regina', 7798: 'Saskatoon', 7799: 'Fort McMurray Eagle Ridge',
    7800: 'Calgary Market Mall', 7801: 'Edmonton Tamarack', 7802: 'Windsor',
}

BASE_URL = 'https://www.landmarkcinemas.com'
MOVIES_BY_CINEMA_API = f'{BASE_URL}/Umbraco/Api/MovieApi/MoviesByCinema'
SEATMAP_API = f'{BASE_URL}/Umbraco/Api/SeatMapApi/GetSessionSeatMap'

_thread_local = threading.local()
_log_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_seatmap_ts = [0.0]     # monotonic ts of last seatmap request


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# ============================================================
#  COOKIE HANDLING
# ============================================================
def parse_cookies(cookie_str: str) -> dict:
    out = {}
    for part in cookie_str.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def build_cookie_header(cookie_dict: dict) -> str:
    return '; '.join(f'{k}={v}' for k, v in cookie_dict.items())


def make_cookie_header_for_theatre(cinema_id: int, theatre_name: str) -> str:
    jar = parse_cookies(COOKIE_STRING)
    slug = slugify(theatre_name)
    jar['LMC_TheatreId']   = str(cinema_id)
    jar['LMC_TheatreURL']  = f'%2Fshowtimes%2F{slug}'
    jar['LMC_TheatreName'] = f'%2Fnow-playing%2F{slug}'
    return build_cookie_header(jar)


# ============================================================
#  DUMP HELPERS
# ============================================================
def dump_seatmap_file(session_id, cinema_id, theatre_name, status,
                      request_payload, response_body, resp_headers,
                      attempt=1):
    os.makedirs(DUMP_DIR, exist_ok=True)
    suffix = '' if attempt == 1 else f'_attempt{attempt}'
    path = os.path.join(DUMP_DIR, f'{session_id}{suffix}.json')

    try:
        parsed_body = json.loads(response_body) if response_body else None
    except Exception:
        parsed_body = None

    record = {
        'sessionId': str(session_id),
        'cinemaId': int(cinema_id),
        'theatreName': theatre_name,
        'status': status,
        'attempt': attempt,
        'requestPayload': request_payload,
        'responseHeaders': dict(resp_headers),
        'responseBodyRaw': response_body,
        'responseBodyJson': parsed_body,
        'timestamp': time.time(),
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, indent=2, ensure_ascii=False)


# ============================================================
#  SEAT MAP PARSER
# ============================================================
def parse_seatmap(data: dict) -> dict:
    """
    Walk the seatmap response and count seats.

    Response shape:
      {
        "Data": {
          "Area": [
            {
              "AreaId": "...",
              "AreaDescription": "Standard",
              "Rows": {
                "<rowkey>": {
                  "PhysicalName": "E",
                  "Seats": {
                    "<seatkey>": {
                      "Status": 0|1|...,   # 0 = available, 1 = sold
                      "SeatId": "...",
                      "Type":   1|2,       # 1 = normal, 2 = wheelchair
                      "Column": ..., "Row": ...
                    }
                  }
                }
              }
            }
          ]
        },
        "ResultMessage": "OK",
        "ResultCode": 0
      }
    """
    out = {
        'total': 0,
        'sold': 0,
        'available': 0,
        'blocked': 0,
        'wheelchair': 0,
        'areas': [],
        'result_code': data.get('ResultCode'),
        'result_message': data.get('ResultMessage', ''),
    }

    body = data.get('Data') or {}
    areas = body.get('Area') or []

    for area in areas:
        area_stats = {
            'id': area.get('AreaId'),
            'description': area.get('AreaDescription', ''),
            'total': 0,
            'sold': 0,
            'available': 0,
            'blocked': 0,
            'wheelchair': 0,
        }
        rows = area.get('Rows') or {}
        for row_key, row in rows.items():
            seats = row.get('Seats') or {}
            for seat_key, seat in seats.items():
                status = seat.get('Status', -1)
                seat_type = seat.get('Type', 1)

                area_stats['total'] += 1
                out['total'] += 1

                if status == 1:
                    area_stats['sold'] += 1
                    out['sold'] += 1
                elif status == 0:
                    area_stats['available'] += 1
                    out['available'] += 1
                else:
                    area_stats['blocked'] += 1
                    out['blocked'] += 1

                if seat_type == 2:
                    area_stats['wheelchair'] += 1
                    out['wheelchair'] += 1

        out['areas'].append(area_stats)

    return out


# ============================================================
#  LOGGER (logs.txt)
# ============================================================
def log_dump(section_title: str, lines):
    with _log_lock:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('\n' + '=' * 78 + '\n')
            f.write(f'{section_title}\n')
            f.write('=' * 78 + '\n')
            for ln in lines:
                f.write(f'{ln}\n')


def log_seatmap_request(url, headers, body, cookie_header, extra=None):
    lines = [f'URL     : {url}', '', 'HEADERS :']
    for k, v in headers.items():
        if k.lower() == 'x-xsrf-token':
            lines.append(f'  {k}: {v[:50]}...  (len={len(v)})')
        elif k.lower() == 'cookie':
            lines.append(f'  {k}: {v[:120]}...  (len={len(v)})')
        else:
            lines.append(f'  {k}: {v}')
    lines.append('')
    lines.append(f'COOKIE HEADER (len={len(cookie_header)}):')
    lines.append(f'  {cookie_header[:300]}...')
    lines.append('')
    lines.append('PAYLOAD :')
    lines.append(f'  {body}')
    lines.append(f'  (length: {len(body)} bytes)')
    if extra:
        lines.append('')
        for ln in extra:
            lines.append(ln)
    log_dump('📤 SEATMAP REQUEST', lines)


def log_seatmap_response(status, reason, resp_headers, body, extra=None):
    lines = [f'STATUS  : {status} {reason}', '', 'RESPONSE HEADERS :']
    for k, v in resp_headers.items():
        lines.append(f'  {k}: {v}')
    lines.append('')
    lines.append('RESPONSE BODY (first 2000 chars):')
    snippet = body[:2000] + ('... [truncated]' if len(body) > 2000 else '')
    lines.append(snippet)
    if extra:
        lines.append('')
        for ln in extra:
            lines.append(ln)
    log_dump('📥 SEATMAP RESPONSE', lines)


def log_retry(kind, attempt, max_retries, wait, err, extra=None):
    lines = [
        f'KIND    : {kind}',
        f'ATTEMPT : {attempt}/{max_retries}',
        f'WAIT    : {wait:.2f}s',
        f'ERROR   : {err}',
    ]
    if extra:
        for ln in extra:
            lines.append(ln)
    log_dump('🔁 RETRY', lines)


# ============================================================
#  SESSIONS / HEADERS
# ============================================================
def get_headers(accept: str = 'application/json, text/javascript, */*; q=0.01') -> dict:
    profile = random.choice(HEADER_PROFILES)
    return {
        'authority': 'www.landmarkcinemas.com',
        'accept': accept,
        'accept-language': profile['accept-language'],
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'origin': BASE_URL,
        'referer': BASE_URL + '/',
        'priority': 'u=1, i',
        'sec-ch-ua': profile['sec-ch-ua'],
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': profile['sec-ch-ua-platform'],
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': profile['user-agent'],
        'x-requested-with': 'XMLHttpRequest',
        # FIXED — never rotates
        'x-xsrf-token': XSRF_TOKEN,
    }


# ============================================================
#  GENERIC RETRY WRAPPER
# ============================================================
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def request_with_retry(method, url, *, kind='request', max_retries=MAX_RETRIES,
                       backoff_base=RETRY_BACKOFF_BASE, log_extra=None, **kwargs):
    """
    Wrap requests.request with exponential backoff + jitter.
    Retries on network errors and retryable HTTP statuses.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.request(method, url, **kwargs)

            if r.status_code in RETRYABLE_STATUS:
                raise requests.HTTPError(
                    f'HTTP {r.status_code} {r.reason}', response=r
                )

            r.raise_for_status()
            return r

        except (requests.RequestException, requests.HTTPError) as e:
            last_exc = e
            if attempt >= max_retries:
                break

            wait = (backoff_base ** attempt) + random.uniform(0.0, 1.0)
            print(f'   🔁 retry {attempt}/{max_retries - 1} in {wait:.1f}s — {e}')
            log_retry(kind, attempt, max_retries, wait, e, extra=log_extra)
            time.sleep(wait)

    assert last_exc is not None
    raise last_exc


# ============================================================
#  RATE LIMIT HELPER (seatmap)
# ============================================================
def seatmap_delay():
    """Sleep so that at least SEATMAP_MIN_DELAY..MAX_DELAY s elapse between
    successive seatmap calls (thread-safe; with concurrency=1 this simply
    spaces each request)."""
    target = random.uniform(SEATMAP_MIN_DELAY, SEATMAP_MAX_DELAY)
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_seatmap_ts[0]
        sleep_for = max(0.0, target - elapsed)
        _last_seatmap_ts[0] = now + sleep_for
    if sleep_for > 0:
        time.sleep(sleep_for)


# ============================================================
#  HTTP HELPERS
# ============================================================
def fetch_movies_for_cinema(cinema_id: int):
    params = {
        'cinemaId': str(cinema_id),
        'splitByAttributes': 'true',
        'expandSessions': 'true',
    }
    headers = get_headers(accept='*/*')
    headers['cookie'] = make_cookie_header_for_theatre(cinema_id, THEATRES[cinema_id])

    r = request_with_retry(
        'GET',
        MOVIES_BY_CINEMA_API,
        kind='movies',
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
        log_extra=[f'CINEMA_ID: {cinema_id}', f'THEATRE: {THEATRES[cinema_id]}'],
    )
    return r.json()


def fetch_seat_map(session_id, cinema_id, theatre_name, log_context=''):
    """
    Fetch a seatmap for a single session.

    Retries on transient failures. Every attempt is logged + dumped.
    Cookies + XSRF stay fixed; only cosmetic headers rotate each attempt.
    """
    last_exc = None

    for attempt in range(1, MAX_RETRIES + 1):
        # Fresh random headers per attempt (cookies/xsrf unchanged)
        headers = get_headers()
        headers['content-type'] = 'application/json; charset=UTF-8'

        cookie_header = make_cookie_header_for_theatre(cinema_id, theatre_name)
        headers['cookie'] = cookie_header

        body = json.dumps(
            {'SessionId': str(session_id), 'CinemaId': int(cinema_id)},
            separators=(',', ':'),
        )

        log_seatmap_request(
            url=SEATMAP_API, headers=headers, body=body,
            cookie_header=cookie_header,
            extra=[
                f'CONTEXT  : {log_context}',
                f'CINEMA_ID: {cinema_id}',
                f'SESSION  : {session_id}',
                f'THEATRE  : {theatre_name}',
                f'ATTEMPT  : {attempt}/{MAX_RETRIES}',
            ],
        )

        try:
            r = requests.post(
                SEATMAP_API,
                headers=headers,
                data=body,
                timeout=REQUEST_TIMEOUT,
            )

            log_seatmap_response(
                status=r.status_code, reason=r.reason,
                resp_headers=dict(r.headers), body=r.text,
                extra=[
                    f'CONTEXT  : {log_context}',
                    f'CINEMA_ID: {cinema_id}',
                    f'SESSION  : {session_id}',
                    f'THEATRE  : {theatre_name}',
                    f'ATTEMPT  : {attempt}/{MAX_RETRIES}',
                ],
            )

            # Dump raw response (success OR failure)
            dump_seatmap_file(
                session_id=session_id,
                cinema_id=cinema_id,
                theatre_name=theatre_name,
                status=r.status_code,
                request_payload={'SessionId': str(session_id),
                                 'CinemaId': int(cinema_id)},
                response_body=r.text,
                resp_headers=dict(r.headers),
                attempt=attempt,
            )

            if r.status_code in RETRYABLE_STATUS:
                raise requests.HTTPError(
                    f'HTTP {r.status_code} {r.reason}', response=r
                )

            r.raise_for_status()
            return r.json()

        except (requests.RequestException, requests.HTTPError) as e:
            last_exc = e
            if attempt >= MAX_RETRIES:
                break

            wait = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0.0, 1.0)
            print(f'   🔁 seatmap retry {attempt}/{MAX_RETRIES - 1} '
                  f'in {wait:.1f}s — {e}')
            log_retry(
                'seatmap', attempt, MAX_RETRIES, wait, e,
                extra=[
                    f'CONTEXT  : {log_context}',
                    f'CINEMA_ID: {cinema_id}',
                    f'SESSION  : {session_id}',
                    f'THEATRE  : {theatre_name}',
                ],
            )
            time.sleep(wait)

    assert last_exc is not None
    raise last_exc


# ============================================================
#  CONCURRENCY POOL
# ============================================================
def async_pool(limit, items, worker):
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=limit) as ex:
        futs = {ex.submit(worker, it): i for i, it in enumerate(items)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                results[i] = {'__error__': str(e)}
    return results


# ============================================================
#  MAIN
# ============================================================
def main():
    os.makedirs(DUMP_DIR, exist_ok=True)
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        f.write(f'Landmark scraper log — {time.ctime()}\n')
        f.write(f'Keyword: {KEYWORD}  Date: {CHECK_DATE}\n')

    print(f'🍁 Landmark Scraper (Direct) — keyword="{KEYWORD}" date={CHECK_DATE}\n')
    print(f'📝 logs         → {LOG_FILE}')
    print(f'📦 raw seatmaps → {DUMP_DIR}/<sessionId>.json')
    print(f'⚙️  concurrency  → movies={MOVIES_CONCURRENCY}  seatmap={CONCURRENCY}')
    print(f'⚙️  retries      → {MAX_RETRIES} (backoff base {RETRY_BACKOFF_BASE}s)')
    print(f'⚙️  seatmap gap  → {SEATMAP_MIN_DELAY}–{SEATMAP_MAX_DELAY}s\n')

    theatre_ids = list(THEATRES.keys())

    def fetch_theatre(cid):
        name = THEATRES[cid]
        try:
            movies = fetch_movies_for_cinema(cid)
            print(f'✅ [{name}] {len(movies)} film(s)')
            return {'theatreId': cid, 'theatreName': name, 'movies': movies}
        except Exception as e:
            print(f'❌ Movies [{name}]: {e}')
            return {'theatreId': cid, 'theatreName': name, 'movies': []}

    print('📥 Fetching showtimes per theatre...')
    theatre_results = async_pool(MOVIES_CONCURRENCY, theatre_ids, fetch_theatre)

    shows_to_fetch = []
    for tr in theatre_results:
        if not tr or '__error__' in tr:
            continue
        for film in tr['movies']:
            title = film.get('Title', '') or ''
            if KEYWORD.lower() not in title.lower():
                continue
            print(f'🎯 [{tr["theatreName"]}] MATCH: "{title}" (FilmId={film.get("FilmId")})')

            sess_count = 0
            for sess in film.get('Sessions', []) or []:
                if sess.get('NewDate') != CHECK_DATE:
                    continue
                for exp in sess.get('ExperienceTypes', []) or []:
                    attrs = exp.get('ExperienceAttributes', []) or []
                    lang, version = 'English', '2D'
                    for a in attrs:
                        n = (a.get('Name') or '').lower()
                        if n == 'language':
                            lang = a.get('Value', lang)
                        elif n == 'version':
                            version = a.get('Value', version)
                    for show in exp.get('Times', []) or []:
                        shows_to_fetch.append({
                            'theatreId': tr['theatreId'],
                            'theatreName': tr['theatreName'],
                            'movieTitle': title,
                            'filmId': film.get('FilmId'),
                            'sessionId': show.get('Scheduleid'),
                            'cinemaId': show.get('CinemaId', tr['theatreId']),
                            'showTime': show.get('StartTime'),
                            'screen': show.get('Screen', ''),
                            'lang': lang,
                            'version': version,
                        })
                        sess_count += 1
            if sess_count:
                print(f'🎬 [{tr["theatreName"]}] "{title}" → {sess_count} show(s) on {CHECK_DATE}')

    if not shows_to_fetch:
        print('❌ No matching shows found.')
        return

    unique_theatres = len({s['theatreId'] for s in shows_to_fetch})
    print(f'\n🎬 {len(shows_to_fetch)} show(s) across {unique_theatres} theatre(s). '
          f'Fetching seats (1–2s delay between requests)...\n')

    def fetch_seats(show):
        tag = (f'[{show["theatreName"]}] {show["movieTitle"]} '
               f'@{show["showTime"]} (session {show["sessionId"]})')

        # Enforce spacing between successive seatmap requests
        seatmap_delay()

        try:
            data = fetch_seat_map(
                show['sessionId'], int(show['cinemaId']),
                show['theatreName'], log_context=tag)

            stats = parse_seatmap(data)

            # Handle "ResultCode != 0" (session expired, no seatmap available)
            if stats['result_code'] not in (0, None):
                print(f'⚠️  {tag} → ResultCode={stats["result_code"]}: '
                      f'{stats["result_message"]}')
                return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                        'avgPrice': 0, 'gross': 0,
                        'wheelchair': 0, 'available': 0, 'blocked': 0,
                        'areas': [], 'result_code': stats['result_code'],
                        'result_message': stats['result_message'],
                        'error': f'ResultCode={stats["result_code"]}: '
                                 f'{stats["result_message"]}'}

            # Handle empty seat map (no areas returned)
            if stats['total'] == 0:
                print(f'⚠️  {tag} → empty seat map '
                      f'(ResultCode={stats["result_code"]})')
                return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                        'avgPrice': 0, 'gross': 0,
                        'wheelchair': 0, 'available': 0, 'blocked': 0,
                        'areas': [], 'result_code': stats['result_code'],
                        'result_message': stats['result_message'],
                        'error': 'empty seat map'}

            sold  = stats['sold']
            total = stats['total']
            occ   = round(sold / total * 100, 2) if total else 0
            price = PRICE_MAP.get(show['version'], 15)
            gross = price * sold

            area_desc = ' | '.join(
                f"{a['description']}:{a['sold']}/{a['total']}"
                for a in stats['areas']
            )

            print(f'💺 {tag} → sold={sold}/{total} ({occ}%) '
                  f'[wc={stats["wheelchair"]}] {area_desc}')

            return {
                **show,
                'sold': sold,
                'total': total,
                'occupancy': occ,
                'avgPrice': price,
                'gross': gross,
                'wheelchair': stats['wheelchair'],
                'available': stats['available'],
                'blocked': stats['blocked'],
                'areas': stats['areas'],
                'result_code': stats['result_code'],
                'result_message': stats['result_message'],
                'error': None,
            }
        except Exception as e:
            print(f'❌ {tag} → {e}')
            return {**show, 'sold': 0, 'total': 0, 'occupancy': 0,
                    'avgPrice': 0, 'gross': 0,
                    'wheelchair': 0, 'available': 0, 'blocked': 0,
                    'areas': [], 'result_code': None,
                    'result_message': '', 'error': str(e)}

    shows_with_seats = async_pool(CONCURRENCY, shows_to_fetch, fetch_seats)
    valid_shows = [s for s in shows_with_seats if s and not s.get('error')]
    failed = [s for s in shows_with_seats if s and s.get('error')]
    print(f'\nℹ️  Seat summary: {len(valid_shows)} ok / {len(failed)} failed.')
    print(f'📦 All raw responses → {DUMP_DIR}/')

    if not valid_shows:
        print('❌ No seat data.')
        return

    # Aggregate
    movie_summary, theatre_summary = {}, {}
    overall_sold = overall_total = overall_gross = 0
    for s in valid_shows:
        m, t = s['movieTitle'], s['theatreName']
        movie_summary.setdefault(m, {'sold': 0, 'total': 0, 'gross': 0, 'shows': 0})
        movie_summary[m]['sold'] += s['sold']; movie_summary[m]['total'] += s['total']
        movie_summary[m]['gross'] += s['gross']; movie_summary[m]['shows'] += 1
        theatre_summary.setdefault(t, {'sold': 0, 'total': 0, 'gross': 0, 'shows': 0})
        theatre_summary[t]['sold'] += s['sold']; theatre_summary[t]['total'] += s['total']
        theatre_summary[t]['gross'] += s['gross']; theatre_summary[t]['shows'] += 1
        overall_sold += s['sold']; overall_total += s['total']; overall_gross += s['gross']

    def print_table(rows, cols, title):
        print('\n' + '=' * 70)
        print(title)
        print('=' * 70)
        if not rows:
            print('(none)'); return
        widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
        header = ' | '.join(c.ljust(widths[c]) for c in cols)
        print(header); print('-' * len(header))
        for r in rows:
            print(' | '.join(str(r[c]).ljust(widths[c]) for c in cols))

    detail = [{
        'Movie': s['movieTitle'],
        'Theatre': s['theatreName'],
        'Show Time': s['showTime'],
        'Lang': s['lang'],
        'Ver': s['version'],
        'Sold': s['sold'],
        'Available': s['available'],
        'Total': s['total'],
        'Wheelchair': s['wheelchair'],
        'Occ %': s['occupancy'],
        'Gross': f"{s['gross']:.2f}",
        'Areas': ' | '.join(f"{a['description']}:{a['sold']}/{a['total']}"
                           for a in s['areas']),
        'ResultCode': s['result_code'],
    } for s in valid_shows]

    print_table(detail,
                ['Movie', 'Theatre', 'Show Time', 'Lang', 'Ver',
                 'Sold', 'Available', 'Total', 'Wheelchair',
                 'Occ %', 'Gross'],
                '📊 DETAILED SHOW DATA')

    movie_rows = [{
        'Movie': k, 'Shows': v['shows'], 'Sold': v['sold'], 'Total': v['total'],
        'Occ %': round(v['sold'] / v['total'] * 100, 2) if v['total'] else 0,
        'Gross': f"{v['gross']:.2f}",
    } for k, v in sorted(movie_summary.items(), key=lambda x: -x[1]['gross'])]
    print_table(movie_rows, ['Movie', 'Shows', 'Sold', 'Total', 'Occ %', 'Gross'],
                '🎬 PER-MOVIE SUMMARY')

    theatre_rows = [{
        'Theatre': k, 'Shows': v['shows'], 'Sold': v['sold'], 'Total': v['total'],
        'Occ %': round(v['sold'] / v['total'] * 100, 2) if v['total'] else 0,
        'Gross': f"{v['gross']:.2f}",
    } for k, v in sorted(theatre_summary.items(), key=lambda x: -x[1]['gross'])]
    print_table(theatre_rows, ['Theatre', 'Shows', 'Sold', 'Total', 'Occ %', 'Gross'],
                '🏢 PER-THEATRE SUMMARY')

    print('\n' + '=' * 70)
    print('📈 OVERALL SUMMARY')
    print('=' * 70)
    print(f'Total Shows:       {len(valid_shows)}')
    print(f'Total Sold Seats:  {overall_sold}')
    print(f'Total Capacity:    {overall_total}')
    print(f'Overall Occupancy: {round(overall_sold / overall_total * 100, 2) if overall_total else 0}%')
    print(f'Total Est. Gross:  ${overall_gross:.2f} CAD')

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'Movie', 'Theatre', 'Show Time', 'Lang', 'Ver',
            'Sold', 'Available', 'Total', 'Wheelchair',
            'Occ %', 'Gross', 'Areas', 'ResultCode',
        ])
        writer.writeheader()
        for r in detail:
            writer.writerow(r)
    print(f'\n💾 CSV:  {OUTPUT_CSV}')
    print(f'📝 Log:  {LOG_FILE}')
    print(f'📦 Dump: {DUMP_DIR}/  ({len(os.listdir(DUMP_DIR))} files)')


if __name__ == '__main__':
    start = time.time()
    try:
        main()
    except KeyboardInterrupt:
        print('\n⏹️  Interrupted.')
    finally:
        print(f'\n⏱️  Run time: {round(time.time() - start, 2)} s')
