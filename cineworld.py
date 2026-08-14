import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BOOKING_URL = "https://experience.cineworld.co.uk/select-tickets?sitecode=105&site=104&id=193321"
CINEWORLD_HOME = "https://www.cineworld.co.uk/"
SEATPLAN_PATH = "/api/SeatPlan"
OUTPUT_DIR = Path("cineworld_output")
PAGE_TIMEOUT = 45000
SEATPLAN_TIMEOUT = 45000

STATUS_MAP = {
    0: "Available",
    1: "Sold / Reserved",
    2: "Blocked / Unavailable",
    3: "Wheelchair",
    5: "Blocked / Unavailable",
    7: "Companion",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def warn(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] WARNING: {message}", flush=True)


def save_json(name, data):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / name
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"Saved: {path}")
    return path


async def diagnostics(page, reason):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        (OUTPUT_DIR / "diagnostic.html").write_text(
            await page.content(), encoding="utf-8"
        )
    except Exception as exc:
        warn(f"Could not save HTML: {exc}")

    try:
        await page.screenshot(
            path=str(OUTPUT_DIR / "diagnostic.png"),
            full_page=True,
        )
    except Exception as exc:
        warn(f"Could not save screenshot: {exc}")

    try:
        save_json(
            "diagnostic.json",
            {
                "reason": reason,
                "url": page.url,
                "title": await page.title(),
            },
        )
    except Exception:
        pass


async def warmup(page):
    log("Warming up Cineworld main website...")

    try:
        response = await page.goto(
            CINEWORLD_HOME,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        if response:
            log(f"Home HTTP status: {response.status}")
    except Exception as exc:
        warn(f"Warm-up failed: {exc}")

    await page.wait_for_timeout(2000)

    try:
        cookies = await page.context.cookies()
        log(f"Cookies after warm-up: {len(cookies)}")
        for cookie in cookies:
            log(f"  cookie: {cookie['name']}")
    except Exception as exc:
        warn(f"Cookie inspection failed: {exc}")


async def inspect_page(page):
    title = (await page.title()).strip()

    try:
        text = await page.locator("body").inner_text(timeout=10000)
    except Exception:
        text = ""

    lower = text.lower()

    indicators = (
        "access denied",
        "forbidden",
        "verify you are human",
        "checking your browser",
        "just a moment",
        "captcha",
        "cloudflare",
        "unusual traffic",
        "request blocked",
        "bot detection",
        "security check",
    )

    found = [x for x in indicators if x in lower]

    log(f"Page title: {title}")
    log(f"Page text length: {len(text):,}")

    if found:
        warn("Possible block/challenge detected: " + ", ".join(found))
        return False

    return True


async def handle_guest(page):
    log("Checking for 'Continue as a guest'...")

    try:
        button = page.get_by_role(
            "button",
            name=re.compile(r"continue as a guest", re.I),
        )
        await button.wait_for(state="visible", timeout=7000)
        await button.click()
        await page.wait_for_timeout(1000)
        log("Continued as guest.")
        return True
    except PlaywrightTimeoutError:
        log("Continue as a guest not present.")
        return False
    except Exception as exc:
        warn(f"Guest handling failed: {exc}")
        return False


async def find_ticket(page):
    log("Searching for ticket types...")

    try:
        await page.wait_for_selector(
            ".select-tickets_row",
            timeout=PAGE_TIMEOUT,
        )
    except PlaywrightTimeoutError:
        await diagnostics(page, "Ticket rows not found")
        raise RuntimeError(
            "Ticket rows were not found. Diagnostic files were saved."
        )

    rows = page.locator(".select-tickets_row")
    count = await rows.count()
    log(f"Ticket rows found: {count}")

    candidates = []

    for i in range(count):
        row = rows.nth(i)

        try:
            description = await row.locator(
                ".select-tickets_row-description"
            ).inner_text()
            description = re.sub(r"\s+", " ", description).strip()
        except Exception:
            description = ""

        buttons = row.locator('button[aria-label^="Add "]')
        if await buttons.count() == 0:
            continue

        button = buttons.first

        try:
            if not await button.is_visible():
                continue
            if not await button.is_enabled():
                continue
        except Exception:
            continue

        try:
            quantity = (
                await row.locator(".select-tickets_quantity").inner_text()
            ).strip()
        except Exception:
            quantity = "0"

        log(f"  {description} | quantity={quantity}")

        candidates.append(
            {
                "row": row,
                "description": description,
                "button": button,
                "quantity": quantity,
            }
        )

    if not candidates:
        await diagnostics(page, "No addable ticket buttons found")
        raise RuntimeError("No addable Cineworld ticket found.")

    adult = next(
        (
            item
            for item in candidates
            if re.match(r"^adult\b", item["description"], re.I)
        ),
        None,
    )

    selected = adult or candidates[0]
    log(f"Selected ticket: {selected['description']}")
    return selected


async def add_ticket(ticket):
    log(f"Adding ticket: {ticket['description']}")
    await ticket["button"].scroll_into_view_if_needed()
    await ticket["button"].click()
    await asyncio.sleep(0.75)

    try:
        quantity = await ticket["row"].locator(
            ".select-tickets_quantity"
        ).inner_text()
        log(f"Quantity after click: {quantity.strip()}")
    except Exception as exc:
        warn(f"Could not read ticket quantity: {exc}")


async def confirm_tickets(page):
    log("Waiting for Confirm Tickets...")

    try:
        button = page.get_by_role(
            "button",
            name=re.compile(r"confirm tickets", re.I),
        )
        await button.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        await button.scroll_into_view_if_needed()
        await button.click()
        log("Tickets confirmed.")
    except Exception as exc:
        await diagnostics(page, "Confirm Tickets button not found")
        raise RuntimeError(f"Could not confirm tickets: {exc}")


def parse_seatplan(data):
    seat_layout = data.get("SeatLayoutData", {})

    if not isinstance(seat_layout, dict):
        raise ValueError("SeatLayoutData is not an object.")

    areas = seat_layout.get("Areas", [])

    if not isinstance(areas, list):
        raise ValueError("SeatLayoutData.Areas is not a list.")

    seats = []

    for area in areas:
        if not isinstance(area, dict):
            continue

        area_number = area.get("Number")
        area_name = (
            area.get("Description")
            or area.get("DescriptionAlt")
            or f"Area {area_number}"
        )

        rows = area.get("Rows", [])
        if not isinstance(rows, list):
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue

            row_index = row.get("RowIndexZeroBased")
            row_name = row.get("PhysicalName") or str(row_index)

            row_seats = row.get("Seats", [])
            if not isinstance(row_seats, list):
                continue

            for seat in row_seats:
                if not isinstance(seat, dict):
                    continue

                position = seat.get("Position") or {}
                if not isinstance(position, dict):
                    position = {}

                try:
                    status = int(seat.get("Status"))
                except (TypeError, ValueError):
                    status = -1

                try:
                    original_status = int(
                        seat.get("OriginalStatus", status)
                    )
                except (TypeError, ValueError):
                    original_status = status

                actual_row = position.get("RowIndex", row_index)
                column = position.get("ColumnIndex")

                seats.append(
                    {
                        "key": f"{area_number}:{actual_row}:{column}",
                        "areaNumber": area_number,
                        "areaName": area_name,
                        "rowIndex": actual_row,
                        "rowName": row_name,
                        "column": column,
                        "id": seat.get("Id"),
                        "status": status,
                        "statusName": STATUS_MAP.get(
                            status, f"Unknown ({status})"
                        ),
                        "originalStatus": original_status,
                        "priority": seat.get("Priority"),
                        "seatStyle": seat.get("SeatStyle"),
                        "seatsInGroup": seat.get("SeatsInGroup"),
                    }
                )

    return seats


def calculate_stats(seats):
    stats = {
        "total": len(seats),
        "available": 0,
        "sold": 0,
        "blocked": 0,
        "wheelchair": 0,
        "companion": 0,
        "unknown": 0,
    }

    for seat in seats:
        status = seat["status"]

        if status == 0:
            stats["available"] += 1
        elif status == 1:
            stats["sold"] += 1
        elif status in (2, 5):
            stats["blocked"] += 1
        elif status == 3:
            stats["wheelchair"] += 1
        elif status == 7:
            stats["companion"] += 1
        else:
            stats["unknown"] += 1

    stats["occupancy"] = (
        stats["sold"] / stats["total"] * 100
        if stats["total"]
        else 0
    )

    return stats


def calculate_area_stats(seats):
    result = {}

    for seat in seats:
        name = seat["areaName"]

        if name not in result:
            result[name] = {
                "total": 0,
                "available": 0,
                "sold": 0,
                "blocked": 0,
                "wheelchair": 0,
                "companion": 0,
            }

        area = result[name]
        area["total"] += 1
        status = seat["status"]

        if status == 0:
            area["available"] += 1
        elif status == 1:
            area["sold"] += 1
        elif status in (2, 5):
            area["blocked"] += 1
        elif status == 3:
            area["wheelchair"] += 1
        elif status == 7:
            area["companion"] += 1

    for area in result.values():
        area["occupancy"] = (
            area["sold"] / area["total"] * 100
            if area["total"]
            else 0
        )

    return result


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        log("Launching Chromium...")

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-GB",
            extra_http_headers={
                "accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/avif,image/webp,"
                    "image/apng,*/*;q=0.8"
                ),
                "accept-language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "cache-control": "no-cache",
                "pragma": "no-cache",
            },
            viewport={"width": 1440, "height": 900},
        )

        page = await context.new_page()
        seatplan_response = None

        async def on_response(response):
            nonlocal seatplan_response

            try:
                if SEATPLAN_PATH not in response.url:
                    return

                log(f"SeatPlan response: {response.status} {response.url}")

                try:
                    data = await response.json()
                except Exception:
                    data = json.loads(await response.text())

                seatplan_response = {
                    "url": response.url,
                    "status": response.status,
                    "data": data,
                }

            except Exception as exc:
                warn(f"SeatPlan response handling error: {exc}")

        page.on("response", on_response)

        await warmup(page)

        log("Opening Cineworld booking:")
        log(BOOKING_URL)

        response = await page.goto(
            BOOKING_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        if response:
            log(f"Booking HTTP status: {response.status}")

        log(f"Final URL: {page.url}")
        log(f"Page title: {(await page.title()).strip()}")

        await page.wait_for_timeout(3000)

        if not await inspect_page(page):
            await diagnostics(page, "Possible Cineworld block")
            raise RuntimeError(
                "Cineworld appears to have returned a challenge/block page."
            )

        await handle_guest(page)

        ticket = await find_ticket(page)
        await add_ticket(ticket)
        await confirm_tickets(page)

        log("Waiting for SeatPlan API...")

        start = asyncio.get_running_loop().time()

        while (
            seatplan_response is None
            and asyncio.get_running_loop().time() - start
            < SEATPLAN_TIMEOUT / 1000
        ):
            await page.wait_for_timeout(250)

        if seatplan_response is None:
            resources = await page.evaluate(
                """
                () => performance
                    .getEntriesByType("resource")
                    .map(x => x.name)
                    .filter(x => x.includes("/api/SeatPlan"))
                """
            )

            if resources:
                url = resources[-1]
                log(f"SeatPlan resource found: {url}")

                try:
                    api_response = await context.request.get(url)

                    if api_response.ok:
                        seatplan_response = {
                            "url": url,
                            "status": api_response.status,
                            "data": await api_response.json(),
                        }
                except Exception as exc:
                    warn(f"SeatPlan fallback failed: {exc}")

        if seatplan_response is None:
            await diagnostics(page, "SeatPlan response not captured")
            raise RuntimeError("SeatPlan API response was not obtained.")

        raw_data = seatplan_response["data"]
        save_json("seatplan_raw.json", raw_data)

        seats = parse_seatplan(raw_data)

        if not seats:
            raise RuntimeError("SeatPlan contained zero parsed seats.")

        stats = calculate_stats(seats)
        areas = calculate_area_stats(seats)

        parsed_url = urlparse(seatplan_response["url"])
        query = parse_qs(parsed_url.query)

        theatre_code = query.get("theatreCode", [None])[0]
        vista_session = query.get("vistaSession", [None])[0]

        result = {
            "timestamp": datetime.now().isoformat(),
            "bookingURL": BOOKING_URL,
            "seatPlanURL": seatplan_response["url"],
            "theatreCode": theatre_code,
            "vistaSession": vista_session,
            "ticket": ticket["description"],
            "stats": stats,
            "areas": areas,
            "seats": seats,
        }

        save_json("seatplan_parsed.json", result)

        print()
        print("=" * 65)
        print("CINEWORLD SEAT INTELLIGENCE")
        print("=" * 65)
        print(f"Ticket       : {ticket['description']}")
        print(f"Theatre      : {theatre_code}")
        print(f"Session      : {vista_session}")
        print(f"Total seats  : {stats['total']:,}")
        print(f"Available    : {stats['available']:,}")
        print(f"Sold         : {stats['sold']:,}")
        print(f"Blocked      : {stats['blocked']:,}")
        print(f"Wheelchair   : {stats['wheelchair']:,}")
        print(f"Companion    : {stats['companion']:,}")
        print(f"Occupancy    : {stats['occupancy']:.2f}%")
        print("=" * 65)

        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as exc:
        print(f"\nERROR: {exc}")
        raise
