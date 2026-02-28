from playwright.sync_api import sync_playwright
import datetime
import json

MAGIC_ID = "10108"

def run():
    until_date = (
        datetime.date.today() + datetime.timedelta(days=30)
    ).isoformat()

    api_url = (
        f"https://www.cineworld.co.uk/uk/data-api-service/v1/quickbook/"
        f"{MAGIC_ID}/cinemas/with-event/until/{until_date}?attr=&lang=en_GB"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled"
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Europe/London",
        )

        page = context.new_page()

        print("Opening homepage (Cloudflare challenge)…")
        page.goto("https://www.cineworld.co.uk/", wait_until="networkidle")

        print("Calling API via browser fetch…")
        response = page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    credentials: "include"
                });
                return {
                    status: res.status,
                    text: await res.text()
                };
            }""",
            api_url,
        )

        print("STATUS:", response["status"])

        if response["status"] != 200:
            print("BLOCKED RESPONSE:")
            print(response["text"][:500])
            return

        data = json.loads(response["text"])
        print("SUCCESS 🎉")
        print("Cinema count:", len(data["body"]["cinemas"]))

        browser.close()

if __name__ == "__main__":
    run()
