import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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

CINEWORLD_HOME = "https://www.cineworld.co.uk/"

SEATPLAN_PATH = "/api/SeatPlan"

OUTPUT_DIR = Path("cineworld_output")

PAGE_TIMEOUT = 45_000

SEATPLAN_TIMEOUT = 45_000

WARMUP_TIMEOUT = 30_000


# ============================================================
# STATUS MAP
# ============================================================

STATUS_MAP = {
    0: "Available",
    1: "Sold / Reserved",
    2: "Blocked / Unavailable",
    3: "Wheelchair",
    5: "Blocked / Unavailable",
    7: "Companion",
}


# ============================================================
# USER AGENT
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# HEADERS
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

    "user-agent":
        USER_AGENT,
}


# ============================================================
# LOGGING
# ============================================================

def log(message):

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"{message}",
        flush=True
    )


def warn(message):

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"WARNING: {message}",
        flush=True
    )


# ============================================================
# OUTPUT
# ============================================================

def ensure_output():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def save_json(
    filename,
    data
):

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

    log(
        f"Saved: {path}"
    )

    return path


# ============================================================
# SAVE DIAGNOSTICS
# ============================================================

async def save_diagnostics(
    page,
    reason
):

    ensure_output()

    log(
        f"Saving diagnostics: {reason}"
    )

    try:

        html =
            await page.content()

        html_path =
            OUTPUT_DIR / "diagnostic.html"

        html_path.write_text(
            html,
            encoding="utf-8"
        )

        log(
            f"Saved: {html_path}"
        )

    except Exception as exc:

        warn(
            f"Could not save HTML: {exc}"
        )


    try:

        await page.screenshot(
            path=str(
                OUTPUT_DIR /
                "diagnostic.png"
            ),
            full_page=True
        )

        log(
            "Saved: cineworld_output/diagnostic.png"
        )

    except Exception as exc:

        warn(
            f"Could not save screenshot: {exc}"
        )


    try:

        info = {

            "url":
                page.url,

            "title":
                await page.title(),

            "reason":
                reason,

        }

        save_json(
            "diagnostic.json",
            info
        )

    except Exception:
        pass


# ============================================================
# WARM UP CINEWORLD
# ============================================================

async def warmup_cineworld(
    page
):

    log(
        "Warming up Cineworld main website..."
    )

    try:

        response =
            await page.goto(
                CINEWORLD_HOME,
                wait_until="domcontentloaded",
                timeout=WARMUP_TIMEOUT
            )

        if response:

            log(
                f"Cineworld home status: "
                f"{response.status}"
            )

            log(
                f"Cineworld home URL: "
                f"{page.url}"
            )

    except Exception as exc:

        warn(
            f"Cineworld warm-up failed: {exc}"
        )


    await page.wait_for_timeout(
        2_000
    )


    try:

        cookies =
            await page.context.cookies()

        log(
            f"Cineworld cookies after warm-up: "
            f"{len(cookies)}"
        )

        for cookie in cookies:

            log(
                f"  cookie: {cookie['name']}"
            )

    except Exception as exc:

        warn(
            f"Could not inspect cookies: {exc}"
        )


# ============================================================
# OPEN BOOKING PAGE
# ============================================================

async def open_booking_page(
    page
):

    log(
        "Opening Cineworld booking:"
    )

    log(
        BOOKING_URL
    )

    response =
        await page.goto(
            BOOKING_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT
        )

    if response:

        log(
            f"Booking HTTP status: "
            f"{response.status}"
        )


    log(
        f"Final URL: {page.url}"
    )


    title =
        await page.title()

    log(
        f"Page title: {title}"
    )


    await page.wait_for_timeout(
        3_000
    )


# ============================================================
# DETECT POSSIBLE BLOCK
# ============================================================

async def inspect_page(
    page
):

    title =
        (
            await page.title()
        ).strip()


    text =
        await page.locator(
            "body"
        ).inner_text(
            timeout=10_000
        )


    lower =
        text.lower()


    indicators = [

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

    ]


    found = [

        item
        for item in indicators
        if item in lower
    ]


    if found:

        warn(
            "Possible blocking/challenge detected:"
        )

        for item in found:

            warn(
                f"  - {item}"
            )


        return False


    log(
        "No obvious block/challenge text detected."
    )

    log(
        f"Page text length: {len(text):,}"
    )


    return True


# ============================================================
# HANDLE GUEST
# ============================================================

async def handle_guest(
    page
):

    log(
        "Checking for 'Continue as a guest'..."
    )

    try:

        guest =
            page.get_by_role(
                "button",
                name=re.compile(
                    r"continue as a guest",
                    re.IGNORECASE
                )
            )


        await guest.wait_for(
            state="visible",
            timeout=7_000
        )


        log(
            "Continue as a guest found."
        )


        await guest.click()


        await page.wait_for_timeout(
            1_000
        )


        log(
            "Continued as guest."
        )


        return True


    except PlaywrightTimeoutError:

        log(
            "Continue as a guest not present."
        )

        return False


    except Exception as exc:

        warn(
            f"Guest handling error: {exc}"
        )

        return False


# ============================================================
# FIND TICKET
# ============================================================

async def find_ticket(
    page
):

    log(
        "Searching for ticket types..."
    )


    try:

        await page.wait_for_selector(
            ".select-tickets_row",
            timeout=PAGE_TIMEOUT
        )

    except PlaywrightTimeoutError:

        await save_diagnostics(
            page,
            "Ticket rows not found"
        )

        raise RuntimeError(
            "Cineworld ticket rows were not found."
        )


    rows =
        page.locator(
            ".select-tickets_row"
        )


    count =
        await rows.count()


    log(
        f"Ticket rows found: {count}"
    )


    candidates = []


    for i in range(count):

        row =
            rows.nth(i)


        try:

            description =
                await row.locator(
                    ".select-tickets_row-description"
                ).inner_text()

            description =
                re.sub(
                    r"\s+",
                    " ",
                    description
                ).strip()

        except Exception:

            description = ""


        buttons =
            row.locator(
                'button[aria-label^="Add "]'
            )


        if await buttons.count() == 0:

            continue


        button =
            buttons.first


        try:

            if not await button.is_visible():

                continue


            if not await button.is_enabled():

                continue


        except Exception:

            continue


        try:

            quantity =
                await row.locator(
                    ".select-tickets_quantity"
                ).inner_text()

            quantity =
                quantity.strip()

        except Exception:

            quantity = "0"


        log(
            f"  Ticket: "
            f"{description} | "
            f"quantity={quantity}"
        )


        candidates.append({

            "row":
                row,

            "description":
                description,

            "button":
                button,

            "quantity":
                quantity,

        })


    if not candidates:

        await save_diagnostics(
            page,
            "No addable ticket buttons found"
        )

        raise RuntimeError(
            "No addable Cineworld ticket found."
        )


    # --------------------------------------------------------
    # Prefer Adult
    # --------------------------------------------------------

    adult =
        next(
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


    selected =
        adult or candidates[0]


    log(
        f"Selected ticket: "
        f"{selected['description']}"
    )


    return selected


# ============================================================
# ADD TICKET
# ============================================================

async def add_ticket(
    page,
    ticket
):

    log(
        f"Adding ticket: "
        f"{ticket['description']}"
    )


    button =
        ticket["button"]


    await button.scroll_into_view_if_needed()


    await button.click()


    await page.wait_for_timeout(
        750
    )


    try:

        quantity =
            await ticket["row"].locator(
                ".select-tickets_quantity"
            ).inner_text()


        log(
            f"Quantity after click: "
            f"{quantity.strip()}"
        )


    except Exception as exc:

        warn(
            f"Could not read quantity: {exc}"
        )


# ============================================================
# CONFIRM TICKETS
# ============================================================

async def confirm_tickets(
    page
):

    log(
        "Waiting for Confirm Tickets..."
    )


    try:

        confirm =
            page.get_by_role(
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


    except Exception as exc:

        await save_diagnostics(
            page,
            "Confirm Tickets button not found"
        )

        raise RuntimeError(
            f"Could not confirm tickets: {exc}"
        )


# ============================================================
# PARSE SEATPLAN
# ============================================================

def parse_seatplan(
    data
):

    seat_layout =
        data.get(
            "SeatLayoutData",
            {}
        )


    if not isinstance(
        seat_layout,
        dict
    ):

        raise ValueError(
            "SeatLayoutData is not an object."
        )


    areas =
        seat_layout.get(
            "Areas",
            []
        )


    if not isinstance(
        areas,
        list
    ):

        raise ValueError(
            "SeatLayoutData.Areas is not a list."
        )


    seats = []


    for area in areas:

        if not isinstance(
            area,
            dict
        ):

            continue


        area_number =
            area.get(
                "Number"
            )


        area_name =
            (
                area.get(
                    "Description"
                )
                or
                area.get(
                    "DescriptionAlt"
                )
                or
                f"Area {area_number}"
            )


        rows =
            area.get(
                "Rows",
                []
            )


        if not isinstance(
            rows,
            list
        ):

            continue


        for row in rows:

            if not isinstance(
                row,
                dict
            ):

                continue


            row_index =
                row.get(
                    "RowIndexZeroBased"
                )


            row_name =
                (
                    row.get(
                        "PhysicalName"
                    )
                    or
                    str(
                        row_index
                    )
                )


            row_seats =
                row.get(
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


                position =
                    seat.get(
                        "Position"
                    ) or {}


                if not isinstance(
                    position,
                    dict
                ):

                    position = {}


                status =
                    seat.get(
                        "Status"
                    )


                try:

                    status =
                        int(status)

                except (
                    TypeError,
                    ValueError
                ):

                    status = -1


                original_status =
                    seat.get(
                        "OriginalStatus",
                        status
                    )


                try:

                    original_status =
                        int(
                            original_status
                        )

                except (
                    TypeError,
                    ValueError
                ):

                    original_status = status


                actual_row_index =
                    position.get(
                        "RowIndex"
                    )


                if (
                    actual_row_index
                    is None
                ):

                    actual_row_index =
                        row_index


                column =
                    position.get(
                        "ColumnIndex"
                    )


                key = (
                    f"{area_number}:"
                    f"{actual_row_index}:"
                    f"{column}"
                )


                seats.append({

                    "key":
                        key,

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
                        ),

                })


    return seats


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    seats
):

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
            0,

    }


    for seat in seats:

        status =
            seat["status"]


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

        stats["sold"] /
        stats["total"] *
        100

        if stats["total"]

        else 0

    )


    return stats


# ============================================================
# AREA STATISTICS
# ============================================================

def calculate_area_stats(
    seats
):

    result = {}


    for seat in seats:

        name =
            seat["areaName"]


        if name not in result:

            result[name] = {

                "total":
                    0,

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

            }


        area =
            result[name]


        area["total"] += 1


        status =
            seat["status"]


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

            area["sold"] /
            area["total"] *
            100

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


        browser =
            await p.chromium.launch(

                headless=True,

                args=[

                    "--no-sandbox",

                    "--disable-dev-shm-usage",

                    "--disable-blink-features="
                    "AutomationControlled",

                ]

            )


        context =
            await browser.new_context(

                user_agent=
                    USER_AGENT,

                locale=
                    "en-GB",

                extra_http_headers={

                    "accept":
                        HEADERS["accept"],

                    "accept-language":
                        HEADERS["accept-language"],

                    "cache-control":
                        "no-cache",

                    "pragma":
                        "no-cache",

                },

                viewport={

                    "width":
                        1440,

                    "height":
                        900,

                },

            )


        page =
            await context.new_page()


        # ====================================================
        # NETWORK DEBUGGING
        # ====================================================

        seatplan_response =
            None


        async def response_handler(
            response
        ):

            nonlocal seatplan_response


            try:

                if (
                    SEATPLAN_PATH
                    not in response.url
                ):

                    return


                log(
                    "================================"
                )

                log(
                    "SEATPLAN RESPONSE DETECTED"
                )

                log(
                    f"URL: {response.url}"
                )

                log(
                    f"HTTP: {response.status}"
                )


                try:

                    data =
                        await response.json()

                except Exception:

                    text =
                        await response.text()

                    data =
                        json.loads(
                            text
                        )


                seatplan_response = {

                    "url":
                        response.url,

                    "status":
                        response.status,

                    "data":
                        data,

                }


                log(
                    "SeatPlan JSON captured."
                )


            except Exception as exc:

                warn(
                    f"SeatPlan response handling error: {exc}"
                )


        page.on(
            "response",
            response_handler
        )


        # ====================================================
        # WARM-UP
        # ====================================================

        await warmup_cineworld(
            page
        )


        # ====================================================
        # BOOKING
        # ====================================================

        await open_booking_page(
            page
        )


        # ====================================================
        # INSPECT
        # ====================================================

        page_ok =
            await inspect_page(
                page
            )


        if not page_ok:

            await save_diagnostics(
                page,
                "Possible Cineworld block"
            )

            raise RuntimeError(
                "Cineworld appears to have returned "
                "a challenge/block page."
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


        start =
            asyncio.get_running_loop().time()


        while (
            seatplan_response is None
            and
            (
                asyncio.get_running_loop().time()
                - start
            )
            <
            (
                SEATPLAN_TIMEOUT /
                1000
            )
        ):

            await page.wait_for_timeout(
                250
            )


        # ====================================================
        # FALLBACK RESOURCE CHECK
        # ====================================================

        if seatplan_response is None:

            log(
                "Checking Performance Resource Timing..."
            )


            resources =
                await page.evaluate(
                    """
                    () => performance
                        .getEntriesByType("resource")
                        .map(x => x.name)
                        .filter(
                            x =>
                                x.includes(
                                    "/api/SeatPlan"
                                )
                        )
                    """
                )


            if resources:

                url =
                    resources[-1]


                log(
                    f"SeatPlan resource found: {url}"
                )


                try:

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
                                data,

                        }

                except Exception as exc:

                    warn(
                        f"Resource fallback failed: {exc}"
                    )


        # ====================================================
        # FINAL CHECK
        # ====================================================

        if seatplan_response is None:

            await save_diagnostics(
                page,
                "SeatPlan response not captured"
            )

            raise RuntimeError(
                "SeatPlan API response was not obtained."
            )


        # ====================================================
        # RAW
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
        # SESSION
        # ====================================================

        seatplan_url =
            seatplan_response[
                "url"
            ]


        parsed =
            urlparse(
                seatplan_url
            )


        query =
            parse_qs(
                parsed.query
            )


        theatre_code =
            query.get(
                "theatreCode",
                [None]
            )[0]


        vista_session =
            query.get(
                "vistaSession",
                [None]
            )[0]


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
                seats,

        }


        save_json(
            "seatplan_parsed.json",
            result
        )


        # ====================================================
        # SUMMARY
        # ====================================================

        print()
        print(
            "=" * 65
        )

        print(
            "CINEWORLD SEAT INTELLIGENCE"
        )

        print(
            "=" * 65
        )

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
            f"Occupancy    : "
            f"{stats['occupancy']:.2f}%"
        )

        print(
            "=" * 65
        )


        # ====================================================
        # KEEP ALIVE
        # ====================================================

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
            f"ERROR: {exc}"
        )

        raise
