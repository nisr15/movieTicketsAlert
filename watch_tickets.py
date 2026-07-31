"""
BookMyShow Ticket Watcher
-------------------------
Polls a BookMyShow (or District) showtimes page and alerts you the moment
showtimes for a specific theatre + date appear.

WHY PLAYWRIGHT (not just `requests`):
BookMyShow renders showtimes with JavaScript after the page loads, so a plain
HTTP GET usually returns an empty shell. Playwright runs a real (headless)
browser so we see what you'd actually see.

SETUP (run once):
    pip install playwright plyer
    playwright install chromium

CONFIGURE:
    Edit the CONFIG block below with your actual movie URL, theatre name,
    and target date.

RUN:
    python watch_tickets.py

Leave it running (in a terminal, tmux, or as a background service). It will
keep checking every CHECK_INTERVAL_MINUTES and alert you (desktop
notification + terminal bell + a line in ticket_watcher.log) as soon as it
finds showtimes for your theatre on your date. It stops automatically once
it finds a match (so it won't spam you), or you can Ctrl+C anytime.
"""

import time
import sys
import logging
from datetime import datetime

# ---------------------- CONFIG (edit these) ----------------------

# The movie's showtimes page in your city. Get this by:
#   1. Open BookMyShow (or district.in) in your browser
#   2. Search "Spider-Man: Brand New Day", select Hyderabad
#   3. Copy the URL of the movie's showtimes page
MOVIE_URL = "https://in.bookmyshow.com/movies/hyderabad/spider-man-brand-new-day/buytickets/ET00505581/20260806?etCodes=*&language=english&refEventCode=ET00505581"

# Exact-ish theatre name to look for on the page
THEATRE_NAME = "Prasads"

# Screen/format hint (optional, set to "" to ignore). BMS lists PCX under Prasads.
FORMAT_HINT = "PCX"

# Date you care about, just used for logging/clarity (also embed it in MOVIE_URL if possible)
TARGET_DATE = "2026-08-07"

# How often to check (only used in LOCAL/loop mode, see bottom of file)
CHECK_INTERVAL_MINUTES = 30

# ntfy.sh topic name — make up any unique string (it's your private channel).
# Install the ntfy app (iOS/Android) and subscribe to this same topic name.
NTFY_TOPIC = "bms-spiderman-nisr-8008"

# ---------------------- END CONFIG ----------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler("ticket_watcher.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def check_once() -> bool:
    """Load the page with a headless browser and look for the theatre's showtimes.
    Returns True if showtimes for THEATRE_NAME appear to be listed."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        try:
            page.goto(MOVIE_URL, timeout=45000, wait_until="domcontentloaded")
            # Give any lazy-loaded content a moment to render
            page.wait_for_timeout(6000)
            content = page.content()
            page.screenshot(path="debug_screenshot.png", full_page=True)
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            log.warning(f"Page load failed ({e}); will retry next cycle.")
            # Best-effort debug capture even on failure, so we can see what went wrong
            try:
                page.screenshot(path="debug_screenshot.png", full_page=True)
            except Exception:
                pass
            try:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            with open("debug_error.txt", "w", encoding="utf-8") as f:
                f.write(f"MOVIE_URL: {MOVIE_URL}\nError: {repr(e)}\n")
            browser.close()
            return False
        browser.close()

    lower = content.lower()
    theatre_present = THEATRE_NAME.lower() in lower

    # BMS shows a "not yet available" style message when a theatre isn't listed yet
    coming_soon_markers = ["coming soon", "not available", "showtimes will appear"]
    looks_unavailable = any(m in lower for m in coming_soon_markers) and not theatre_present

    if theatre_present and not looks_unavailable:
        log.info(f"'{THEATRE_NAME}' found on the page — showtimes likely listed!")
        return True

    log.info(f"No '{THEATRE_NAME}' showtimes yet for {TARGET_DATE}.")
    return False


def alert():
    message = f"Tickets may be open! '{THEATRE_NAME}' showtimes found for {TARGET_DATE}.\n{MOVIE_URL}"
    log.info("ALERT: " + message)

    # Terminal bell (works in most terminals)
    print("\a\a\a")

    # Push notification via ntfy.sh (works anywhere, including CI/cloud runners)
    try:
        import requests

        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": "BookMyShow: Tickets available!", "Priority": "high"},
            timeout=10,
        )
    except Exception as e:
        log.info(f"(Push notification failed: {e})")

    # Also try a local desktop notification if one happens to be available (harmless no-op in CI)
    try:
        from plyer import notification as desktop_notification

        desktop_notification.notify(title="BookMyShow: Tickets available!", message=message, timeout=0)
    except Exception:
        pass


FOUND_FLAG_FILE = "FOUND.flag"


def run_single_check():
    """One check-and-exit, for use under cron / GitHub Actions.
    Skips the check (and exits quietly) if we already alerted before,
    so a scheduled job doesn't spam you every run after tickets open."""
    import os

    if os.path.exists(FOUND_FLAG_FILE):
        log.info("Already alerted previously (FOUND.flag present) — skipping check.")
        return

    log.info(f"Checking '{THEATRE_NAME}' ({FORMAT_HINT}) showtimes on {TARGET_DATE} — {MOVIE_URL}")
    if check_once():
        alert()
        with open(FOUND_FLAG_FILE, "w") as f:
            f.write(f"Found at {datetime.now().isoformat()}\n")


def run_loop():
    """Continuous local loop mode (Ctrl+C to stop). Use this if you're running
    the script on a machine that stays on; use run_single_check() under cron."""
    log.info(f"Watching for '{THEATRE_NAME}' ({FORMAT_HINT}) showtimes on {TARGET_DATE}")
    log.info(f"URL: {MOVIE_URL}")
    log.info(f"Checking every {CHECK_INTERVAL_MINUTES} minutes. Ctrl+C to stop.\n")

    while True:
        try:
            if check_once():
                alert()
                log.info("Stopping — go grab your seats before they sell out!")
                break
        except Exception as e:
            log.warning(f"Unexpected error this cycle: {e}")

        time.sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    # Under GitHub Actions (or any cron), each invocation runs once and exits.
    # Locally, pass "loop" as an argument to keep it running continuously instead.
    if len(sys.argv) > 1 and sys.argv[1] == "loop":
        run_loop()
    else:
        run_single_check()