import requests
import json

BASE = "https://www.landmarkcinemas.com"

SEATMAP_URL = (
    BASE +
    "/Umbraco/Api/SeatMapApi/GetSessionSeatMap"
)

headers = {
    "authority": "www.landmarkcinemas.com",
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "content-type": "application/json; charset=UTF-8",
    "origin": BASE,
    "pragma": "no-cache",
    "referer": BASE + "/",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36"
    ),
    "x-requested-with": "XMLHttpRequest",
    "x-xsrf-token": "VPUlGSlruWIEfK_Z9iMIhaUqRh693zA7ZdhulWRwAq8Sn6EcFkg7I--BqyX5QW4eIol79gWhFsATMYUlHbzJMf0oX7h35BjPIJH0HHDhCQg1:fpp9Qv7vmvjAyb2JtZ9f4JvsWJalpeIlJnugfqoaox4n_KnS4WEAzjtr2EkUyaepRut5voMK8WZKUPqlaqlyMZ8tqRvyolx3zGejy_sfQd3rlmL2eqKcQcAmUkLvLOf29WKyhK51q9ZgFfURXxQmOg2",
}

payload = {
    "SessionId": 11555649,
    "CinemaId": 184
}

session = requests.Session()

# Establish server-side session first
r = session.get(
    BASE + "/",
    headers={
        "User-Agent": headers["user-agent"],
        "Accept": "text/html,application/xhtml+xml",
    },
    timeout=30
)

print("Initial GET:", r.status_code)
print("Cookies received:", list(session.cookies.keys()))

# Now apply the exact API headers
session.headers.update(headers)

# Make the API request using the SAME session
r = session.post(
    SEATMAP_URL,
    json=payload,
    timeout=30
)

print("SeatMap:", r.status_code)
print("Size:", len(r.content))
print("Content-Type:", r.headers.get("content-type"))

if r.text:
    try:
        print(json.dumps(r.json(), indent=2))
    except ValueError:
        print(r.text[:5000])
else:
    print("EMPTY RESPONSE")
