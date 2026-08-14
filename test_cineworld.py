import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# CONFIGURATION
# ============================================================

BOOKING_URL = (
    "https://experience.cineworld.co.uk/"
    "select-tickets?sitecode=105&site=104&id=193321"
)

OUTPUT_DIR = Path("cineworld_output")

PAGE_TIMEOUT = 45_000
SEATPLAN_TIMEOUT = 45_000


# Cineworld status codes
STATUS_MAP = {
    0: "Available",
    1: "Sold / Reserved",
    2: "Blocked / Unavailable",
    3: "Wheelchair",
    5: "Blocked / Unavailable",
    7: "Companion",
}


# ============================================================
# CHROME-LIKE HEADERS
# ============================================================

HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),

    "accept-language":
        "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",

    "cache-control":
        "no-cache",

    "pragma":
        "no-cache",

    "sec-ch-ua":
        '"Not=A?Brand";v="99", '
        '"Google Chrome";v="151", '
        '"Chromium";v="151"',

    "sec-ch-ua-arch":
        '"x86"',

    "sec-ch-ua-bitness":
        '"64"',

    "sec-ch-ua-full-version":
        '"151.0.7922.109"',

    "sec-ch-ua-full-version-list":
        '"Not=A?Brand";v="99.0.0.0", '
        '"Google Chrome";v="151.0.7922.109", '
        '"Chromium";v="151.0.7922.109"',

    "sec-ch-ua-mobile":
        "?0",

    "sec-ch-ua-model":
        '""',

    "sec-ch-ua-platform":
        '"Windows"',

    "sec-ch-ua-platform-version":
        '"19.0.0"',

    "sec-fetch-dest":
        "document",

    "sec-fetch-mode":
        "navigate",

    "sec-fetch-site":
        "none",

    "sec-fetch-user":
        "?1",

    "upgrade-insecure-requests":
        "1",

    "user-agent":
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
}


# ============================================================
# LOGGING
# ============================================================

def log(message):
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"{message}"
    )


def warn(message):
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"WARNING: {message}"
    )


# ============================================================
# UTILITY
# ============================================================

def ensure_output():
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def save_json(filename, data):
    ensure_output()

    path = OUTPUT_DIR / filename

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    log(f"Saved: {path}")

    return path


# ============================================================
# FIND TICKET
# ============================================================

async def find_ticket(page):
    """
    Find any available Cineworld ticket.

    Priority:
        1. Adult
        2. Any other available ticket

    Therefore all of these work:

        Adult
        Adult Superscreen
        Adult IMAX
        Adult 4DX
        Adult VIP

    If Adult doesn't exist, the first available
    ticket is selected.
    """

    log("Searching for ticket types...")

    await page.wait_for_selector(
        ".select-tickets_row",
        timeout=PAGE_TIMEOUT
    )

    rows = page.locator(
        ".select-tickets_row"
    )

    count = await rows.count()

    candidates = []

    for i in range(count):

        row = rows.nth(i)

        try:
            description = (
                await row.locator(
                    ".select-tickets_row-description"
                ).inner_text()
            ).strip()

        except Exception:
            description = ""

        description = re.sub(
            r"\s+",
            " ",
            description
        )

        add_buttons = row.locator(
            'button[aria-label^="Add "]'
        )

        if await add_buttons.count() == 0:
            continue

        add_button = add_buttons.first

        try:

            if not await add_button.is_visible():
                continue

            if not await add_button.is_enabled():
                continue

        except Exception:
            continue

        try:

            quantity = (
                await row.locator(
                    ".select-tickets_quantity"
                ).inner_text()
            ).strip()

        except Exception:

            quantity = "0"

        candidates.append({
            "row": row,
            "description": description,
            "button": add_button,
            "quantity": quantity,
        })

    if not candidates:

        raise RuntimeError(
            "No addable Cineworld ticket found."
        )

    # Prefer Adult
    adult = next(
        (
            item
            for item in candidates
            if re.match(
                r"^adult\b",
                item["description"],
                re.IGNORECASE
            )
        ),
        None
    )

    selected = adult or candidates[0]

    log(
        f"Selected ticket: "
        f"{selected['description']}"
    )

    log(
        f"Current quantity: "
        f"{selected['quantity']}"
    )

    return selected


# ============================================================
# CLICK GUEST
# ============================================================

async def handle_guest(page):

    log(
        "Checking for 'Continue as a guest'..."
    )

    try:

        guest = page.get_by_role(
            "button",
            name=re.compile(
                r"continue as a guest",
                re.IGNORECASE
            )
        )

        await guest.wait_for(
            state="visible",
            timeout=5_000
        )

        log(
            "Guest button found."
        )

        await guest.click()

        await page.wait_for_timeout(
            1_000
        )

        log(
            "Continued as guest."
        )

    except PlaywrightTimeoutError:

        log(
            "Guest button not present."
        )

    except Exception as exc:

        warn(
            f"Guest handling failed: {exc}"
        )


# ============================================================
# ADD TICKET
# ============================================================

async def add_ticket(
    page,
    ticket
):

    button = ticket["button"]

    log(
        f"Adding: "
        f"{ticket['description']}"
    )

    await button.scroll_into_view_if_needed()

    await button.click()

    await page.wait_for_timeout(
        700
    )

    # Verify quantity
    try:

        quantity_locator = ticket["row"].locator(
            ".select-tickets_quantity"
        )

        await page.wait_for_function(
            """
            (el) => {
                if (!el) return false;

                const value =
                    parseInt(
                        el.textContent.trim(),
                        10
                    );

                return !isNaN(value) && value > 0;
            }
            """,
            await quantity_locator.element_handle(),
            timeout=10_000
        )

    except Exception:

        # Fallback verification
        try:

            quantity = (
                await ticket["row"]
                .locator(
                    ".select-tickets_quantity"
                )
                .inner_text()
            ).strip()

            log(
                f"Ticket quantity now: {quantity}"
            )

        except Exception:

            warn(
                "Could not verify ticket quantity."
            )

    log(
        "Ticket added."
    )


# ============================================================
# CONFIRM TICKETS
# ============================================================

async def confirm_tickets(page):

    log(
        "Waiting for Confirm Tickets..."
    )

    confirm = page.get_by_role(
        "button",
        name=re.compile(
            r"confirm tickets",
            re.IGNORECASE
        )
    )

    await confirm.wait_for(
        state="visible",
        timeout=PAGE_TIMEOUT
    )

    await confirm.scroll_into_view_if_needed()

    log(
        "Clicking Confirm Tickets..."
    )

    await confirm.click()

    log(
        "Tickets confirmed."
    )


# ============================================================
# PARSE SEATPLAN
# ============================================================

def parse_seatplan(data):

    seat_layout =
        data.get(
            "SeatLayoutData",
            {}
        )

    areas =
        seat_layout.get(
            "Areas",
            []
        )

    if not isinstance(areas, list):

        raise ValueError(
            "SeatLayoutData.Areas is not a list."
        )

    seats = []

    for area in areas:

        if not isinstance(area, dict):
            continue

        area_number = area.get(
            "Number"
        )

        area_name = (
            area.get("Description")
            or area.get("DescriptionAlt")
            or f"Area {area_number}"
        )

        rows = area.get(
            "Rows",
            []
        )

        if not isinstance(rows, list):
            continue

        for row in rows:

            if not isinstance(row, dict):
                continue

            row_name = (
                row.get("PhysicalName")
                or str(
                    row.get(
                        "RowIndexZeroBased",
                        ""
                    )
                )
            )

            row_index = row.get(
                "RowIndexZeroBased"
            )

            row_seats = row.get(
                "Seats",
                []
            )

            if not isinstance(
                row_seats,
                list
            ):
                continue

            for seat in row_seats:

                if not isinstance(
                    seat,
                    dict
                ):
                    continue

                position = seat.get(
                    "Position"
                ) or {}

                status = seat.get(
                    "Status"
                )

                try:
                    status = int(status)
                except (
                    TypeError,
                    ValueError
                ):
                    status = -1

                original_status = seat.get(
                    "OriginalStatus",
                    status
                )

                try:
                    original_status = int(
                        original_status
                    )
                except (
                    TypeError,
                    ValueError
                ):
                    original_status = status

                actual_row_index = (
                    position.get(
                        "RowIndex"
                    )
                    if isinstance(
                        position,
                        dict
                    )
                    else None
                )

                if actual_row_index is None:
                    actual_row_index = row_index

                column = (
                    position.get(
                        "ColumnIndex"
                    )
                    if isinstance(
                        position,
                        dict
                    )
                    else None
                )

                unique_key = (
                    f"{area_number}:"
                    f"{actual_row_index}:"
                    f"{column}"
                )

                seats.append({

                    "key":
                        unique_key,

                    "areaNumber":
                        area_number,

                    "areaName":
                        area_name,

                    "rowIndex":
                        actual_row_index,

                    "rowName":
                        row_name,

                    "column":
                        column,

                    "id":
                        seat.get(
                            "Id"
                        ),

                    "status":
                        status,

                    "statusName":
                        STATUS_MAP.get(
                            status,
                            f"Unknown ({status})"
                        ),

                    "originalStatus":
                        original_status,

                    "priority":
                        seat.get(
                            "Priority"
                        ),

                    "seatStyle":
                        seat.get(
                            "SeatStyle"
                        ),

                    "seatsInGroup":
                        seat.get(
                            "SeatsInGroup"
                        )

                })

    return seats


# ============================================================
# CALCULATE STATS
# ============================================================

def calculate_stats(seats):

    stats = {

        "total":
            len(seats),

        "available":
            0,

        "sold":
            0,

        "blocked":
            0,

        "wheelchair":
            0,

        "companion":
            0,

        "unknown":
            0
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
        (
            stats["sold"] /
            stats["total"]
        ) * 100
        if stats["total"]
        else 0
    )

    return stats


# ============================================================
# AREA STATS
# ============================================================

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

                "companion": 0
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
            (
                area["sold"] /
                area["total"]
            ) * 100
            if area["total"]
            else 0
        )

    return result


# ============================================================
# MAIN
# ============================================================

async def main():

    ensure_output()

    async with async_playwright() as p:

        log(
            "Launching Chromium..."
        )

        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = await browser.new_context(

            user_agent=HEADERS[
                "user-agent"
            ],

            locale="en-IN",

            extra_http_headers={
                k: v
                for k, v in HEADERS.items()
                if not k.startswith(
                    "sec-"
                )
                and k not in {
                    "accept",
                    "cache-control",
                    "pragma",
                    "upgrade-insecure-requests",
                }
            },

            viewport={
                "width": 1440,
                "height": 900
            }

        )

        page = await context.new_page()


        # ====================================================
        # SEATPLAN RESPONSE CAPTURE
        # ====================================================

        seatplan_response = None


        async def response_handler(response):

            nonlocal seatplan_response

            try:

                if (
                    CONFIG["SEATPLAN_PATH"]
                    not in response.url
                ):
                    return

                log(
                    "SeatPlan response detected:"
                )

                log(
                    response.url
                )

                try:

                    data = await response.json()

                except Exception:

                    text = await response.text()

                    data = json.loads(text)

                seatplan_response = {

                    "url":
                        response.url,

                    "status":
                        response.status,

                    "data":
                        data
                }

            except Exception as exc:

                warn(
                    f"SeatPlan response handling error: {exc}"
                )


        page.on(
            "response",
            response_handler
        )


        # ====================================================
        # OPEN BOOKING PAGE
        # ====================================================

        log(
            "Opening Cineworld:"
        )

        log(
            BOOKING_URL
        )


        await page.goto(
            BOOKING_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )


        log(
            "Booking page loaded."
        )


        # Give React time to render
        await page.wait_for_timeout(
            2_000
        )


        # ====================================================
        # GUEST
        # ====================================================

        await handle_guest(
            page
        )


        # ====================================================
        # TICKET
        # ====================================================

        ticket =
            await find_ticket(
                page
            )


        await add_ticket(
            page,
            ticket
        )


        # ====================================================
        # CONFIRM
        # ====================================================

        await confirm_tickets(
            page
        )


        # ====================================================
        # WAIT FOR SEATPLAN
        # ====================================================

        log(
            "Waiting for SeatPlan API..."
        )


        started =
            asyncio.get_event_loop().time()


        while (
            seatplan_response is None
            and
            (
                asyncio.get_event_loop().time()
                - started
            )
            < SEATPLAN_TIMEOUT / 1000
        ):

            await page.wait_for_timeout(
                250
            )


        # ====================================================
        # FALLBACK: PERFORMANCE RESOURCE
        # ====================================================

        if seatplan_response is None:

            warn(
                "No SeatPlan response captured yet."
            )

            resources =
                await page.evaluate(
                    """
                    () => performance
                        .getEntriesByType("resource")
                        .map(x => x.name)
                        .filter(x =>
                            x.includes("/api/SeatPlan")
                        )
                    """
                )


            if resources:

                url =
                    resources[-1]

                log(
                    "SeatPlan resource found:"
                )

                log(
                    url
                )


                response =
                    await context.request.get(
                        url
                    )


                if response.ok:

                    data =
                        await response.json()


                    seatplan_response = {

                        "url":
                            url,

                        "status":
                            response.status,

                        "data":
                            data
                    }


        if seatplan_response is None:

            raise RuntimeError(
                "SeatPlan API response was not obtained."
            )


        # ====================================================
        # RAW DATA
        # ====================================================

        raw_data =
            seatplan_response["data"]


        save_json(
            "seatplan_raw.json",
            raw_data
        )


        # ====================================================
        # PARSE
        # ====================================================

        log(
            "Parsing SeatPlan..."
        )


        seats =
            parse_seatplan(
                raw_data
            )


        if not seats:

            raise RuntimeError(
                "SeatPlan contained zero parsed seats."
            )


        stats =
            calculate_stats(
                seats
            )


        area_stats =
            calculate_area_stats(
                seats
            )


        # ====================================================
        # SESSION INFO
        # ====================================================

        seatplan_url =
            seatplan_response[
                "url"
            ]


        from urllib.parse import (
            urlparse,
            parse_qs
        )


        parsed =
            urlparse(
                seatplan_url
            )


        query =
            parse_qs(
                parsed.query
            )


        theatre_code =
            (
                query
                .get(
                    "theatreCode",
                    [None]
                )[0]
            )


        vista_session =
            (
                query
                .get(
                    "vistaSession",
                    [None]
                )[0]
            )


        # ====================================================
        # FINAL RESULT
        # ====================================================

        result = {

            "timestamp":
                datetime.now().isoformat(),

            "bookingURL":
                BOOKING_URL,

            "seatPlanURL":
                seatplan_url,

            "theatreCode":
                theatre_code,

            "vistaSession":
                vista_session,

            "ticket":
                ticket["description"],

            "stats":
                stats,

            "areas":
                area_stats,

            "seats":
                seats
        }


        save_json(
            "seatplan_parsed.json",
            result
        )


        # ====================================================
        # PRINT SUMMARY
        # ====================================================

        print()
        print("=" * 65)
        print(
            "CINEWORLD SEAT INTELLIGENCE"
        )
        print("=" * 65)

        print(
            f"Ticket       : "
            f"{ticket['description']}"
        )

        print(
            f"Theatre      : "
            f"{theatre_code}"
        )

        print(
            f"Session      : "
            f"{vista_session}"
        )

        print(
            f"Total seats  : "
            f"{stats['total']:,}"
        )

        print(
            f"Available    : "
            f"{stats['available']:,}"
        )

        print(
            f"Sold         : "
            f"{stats['sold']:,}"
        )

        print(
            f"Blocked      : "
            f"{stats['blocked']:,}"
        )

        print(
            f"Wheelchair   : "
            f"{stats['wheelchair']:,}"
        )

        print(
            f"Companion    : "
            f"{stats['companion']:,}"
        )

        print(
            f"Unknown      : "
            f"{stats['unknown']:,}"
        )

        print(
            f"Occupancy    : "
            f"{stats['occupancy']:.2f}%"
        )

        print("=" * 65)


        print()
        print(
            "AREA SUMMARY"
        )

        print("-" * 65)


        for name, area in area_stats.items():

            print(
                f"{name}: "
                f"{area['sold']}/"
                f"{area['total']} sold "
                f"({area['occupancy']:.2f}%)"
            )


        print("-" * 65)


        # ====================================================
        # KEEP BROWSER OPEN
        # ====================================================

        log(
            "Browser will remain open."
        )

        log(
            "Press CTRL+C to exit."
        )


        try:

            while True:

                await asyncio.sleep(
                    3600
                )

        except KeyboardInterrupt:

            pass


        await browser.close()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\nStopped."
        )

    except Exception as exc:

        print()
        print(
            "ERROR:",
            exc
        )

        raise
