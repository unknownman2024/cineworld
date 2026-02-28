import requests
import datetime

MAGIC_ID = "10108"

headers = {
    "accept": "application/json;charset=utf-8",
    "accept-language": "en-GB,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "referer": "https://www.cineworld.co.uk/",
}

def test_api():
    until_date = (
        datetime.datetime.now().date()
        + datetime.timedelta(days=30)
    ).isoformat()

    url = (
        f"https://www.cineworld.co.uk/uk/data-api-service/v1/quickbook/"
        f"{MAGIC_ID}/cinemas/with-event/until/{until_date}?attr=&lang=en_GB"
    )

    session = requests.Session()
    session.headers.update(headers)

    # Step 1: warm up (get cookies)
    home = session.get("https://www.cineworld.co.uk/", timeout=20)
    print("HOME STATUS:", home.status_code)
    print("HOME COOKIES:", session.cookies.get_dict())

    # Step 2: actual API call
    r = session.get(url, timeout=20)

    print("\nAPI STATUS:", r.status_code)
    print("CONTENT-TYPE:", r.headers.get("content-type"))
    print("FINAL URL:", r.url)
    print("TEXT PREVIEW:\n", r.text[:500])

    # Try JSON safely
    try:
        js = r.json()
        print("\nJSON KEYS:", js.keys())
    except Exception as e:
        print("\nJSON FAILED:", e)

if __name__ == "__main__":
    test_api()
