# SPDX-FileCopyrightText: 2023-present David C Wang <dcwangmit01@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
Amazon Invoice Downloader

Usage:
  amazon-invoice-downloader.py \
    [--email=<email> --password=<password>] \
    [--year=<YYYY> | --date-range=<YYYYMMDD-YYYYMMDD>] \
    [--url=<url>] \
    [--type=<type>]
  amazon-invoice-downloader.py (-h | --help)
  amazon-invoice-downloader.py (-v | --version)

Login Options:
  --email=<email>          Amazon login email  [default: $AMAZON_EMAIL].
  --password=<password>    Amazon login password  [default: $AMAZON_PASSWORD].
  --url=<url>              Amazon URL to use (e.g., https://www.amazon.com/)  [default: $AMAZON_URL].
  --type=<type>            Amazon invoice type (invoice or summary)  [default: $AMAZON_INVOICE].
                           Alternate method will be fallback if provided type is not found.

Date Range Options:
  --date-range=<YYYYMMDD-YYYYMMDD>  Start and end date range
  --year=<YYYY>                     Year, formatted as YYYY  [default: <CUR_YEAR>].

Options:
  -h --help                Show this screen.
  -v --version             Show version.

Examples:
  amazon-invoice-downloader.py --year=2022  # Uses .env file or env vars $AMAZON_EMAIL and $AMAZON_PASSWORD
  amazon-invoice-downloader.py --date-range=20220101-20221231 --type=summary
  amazon-invoice-downloader.py --email=user@example.com --password=secret  # Defaults to current year
  amazon-invoice-downloader.py --email=user@example.com --password=secret --year=2022 --url=https://www.amazon.ca/
  amazon-invoice-downloader.py --email=user@example.com --password=secret --date-range=20220101-20221231

Features:
  - Remote debugging enabled on port 9222 for AI MCP Servers
  - Virtual authenticator configured to prevent passkey dialogs
  - Stealth mode enabled to avoid detection

Credential Precedence:
  1. Command line arguments (--email, --password, --url, --type)
  2. Environment variables ($AMAZON_EMAIL, $AMAZON_PASSWORD, $AMAZON_URL, $AMAZON_INVOICE)
  3. .env file (automatically loaded if env vars not set)
"""

import os
import random
import re
import sys
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urljoin

from docopt import docopt
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError, sync_playwright
from playwright_stealth import Stealth

from ..__about__ import __version__

cards = ['all']


class ret(Enum):
    SUCCESS = 0
    FAILURE = 1
    SKIPPED = 2


def load_env_if_needed():
    """Load environment variables from .env file if it exists and variables aren't set."""
    # Check if Amazon credentials are already set in environment
    amazon_email = os.environ.get('AMAZON_EMAIL')
    amazon_password = os.environ.get('AMAZON_PASSWORD')

    # If both are already set, no need to load .env
    if amazon_email and amazon_password:
        return

    # Look for .env file in current directory and parent directories
    current_dir = Path.cwd()
    env_file = None

    # Check current directory and up to 3 parent directories
    for i in range(4):
        check_path = current_dir / '.env'
        if check_path.exists():
            env_file = check_path
            break
        current_dir = current_dir.parent

    if env_file:
        print(f"Loading environment variables from {env_file}")
        load_dotenv(env_file)
    else:
        print("No .env file found in current directory or parent directories")


def sleep():
    # Add human latency
    # Generate a random sleep time between 3 and 5 seconds
    sleep_time = random.uniform(2, 5)
    # Sleep for the generated time
    time.sleep(sleep_time)


def get_order_id(spans):
    # Attempt to find the order ID from the spans
    # Order ID is usually in a span following "Order #" span
    for i in range(len(spans)):
        text = spans[i].inner_text().strip()
        if text.lower().startswith("order #"):
            return spans[i + 1].inner_text().strip()
    return "unknown_order_id"


def get_last_four(page):
    # Get last four digits of payment card
    elem = page.get_by_test_id("method-details-number")
    try:
        return elem.nth(0).inner_text(timeout=500).strip()
    except Exception:
        pass
    # Test the alternative selector
    elem = page.locator('[data-pmts-component-id^="pp-"]')
    try:
        last_four = elem.nth(0).text_content(timeout=500).strip()
        match = re.search(r'\d{4}', last_four)
        if match:
            return match.group(0)
    except Exception:
        pass
    # Last shot is the match by text
    elem = page.locator("span", has_text=re.compile(r"ending in \d{4}"))
    try:
        for i in range(elem.count()):
            match = re.search(r'\d{4}', elem.nth(i).inner_text())
            if match:
                return match.group(0)
    except Exception:
        pass
    # Check if payment details are out of date
    elem = page.get_by_text("Unable to display payment details")
    try:
        if elem.count() > 0:
            return "unknown"
    except Exception:
        pass
    print("No last four digits found.")
    return "none"


def download_invoice(context, page, url, order_card, file_name):
    # Placeholder for future invoice type handling
    order_card.query_selector('xpath=.//a[contains(normalize-space(), "Invoice")]').click()

    # Wait for the popover dialog and get its Invoice href
    popover = page.locator('div.a-popover[role="dialog"][aria-modal="true"][aria-hidden="false"]')
    popover.wait_for(state="visible")

    invoice_link = popover.get_by_role("link", name="Invoice", exact=True)
    href = invoice_link.get_attribute("href")
    link = urljoin(url, href)
    # Save
    invoice_page = context.new_page()
    resp = invoice_page.request.get(link)
    ct = (resp.headers.get("content-type") or "").lower()
    if "pdf" not in ct or not resp.ok:
        print(f"Download failed: {resp.status} {resp.status_text}, content-type: {ct}")
        invoice_page.close()
        return ret.FAILURE
    Path(file_name).write_bytes(resp.body())
    invoice_page.close()

    return ret.SUCCESS


def download_summary(context, page, url, order_card, file_name):
    # Placeholder for future invoice type handling
    order_card.query_selector('xpath=.//a[contains(normalize-space(), "Invoice")]').click()

    # Wait for the popover dialog and get its Invoice href
    popover = page.locator('div.a-popover[role="dialog"][aria-modal="true"][aria-hidden="false"]')
    popover.wait_for(state="visible")

    invoice_link = popover.get_by_role("link", name="Printable Order Summary", exact=True)
    href = invoice_link.get_attribute("href")
    link = urljoin(url, href)
    # Navigate to link
    summary_page = context.new_page()
    summary_page.goto(link)
    summary_page.wait_for_load_state("domcontentloaded")
    # Get last four digits of payment card
    last_four = 'all'
    if last_four not in cards:
        last_four = get_last_four(summary_page)
    # Find Print button
    if last_four in cards:
        summary_page.pdf(
            path=file_name,
            format="Letter",
            margin={"top": ".5in", "right": ".5in", "bottom": ".5in", "left": ".5in"},
        )
    else:
        print(f"Skipping download for card ending in {last_four}")
        summary_page.close()
        return ret.SKIPPED

    summary_page.close()
    return ret.SUCCESS


def run(playwright, args):
    email = args.get("--email")
    if email == "$AMAZON_EMAIL":
        email = os.environ.get("AMAZON_EMAIL")

    password = args.get("--password")
    if password == "$AMAZON_PASSWORD":
        password = os.environ.get("AMAZON_PASSWORD")

    url = args.get("--url")
    if url == "$AMAZON_URL":
        url = os.environ.get("AMAZON_URL") or "https://www.amazon.com/"

    invoice_type = args.get("--type")
    if invoice_type == "$AMAZON_INVOICE":
        invoice_type = os.environ.get("AMAZON_INVOICE") or "invoice"
    # Initialize invoice download method and fallback
    download_method = []
    if invoice_type.lower() == "invoice":
        download_method.append(download_invoice)
        download_method.append(download_summary)
    elif invoice_type.lower() == "summary":
        download_method.append(download_summary)
        download_method.append(download_invoice)

    # Parse date ranges int start_date and end_date
    if args["--date-range"]:
        start_date, end_date = args["--date-range"].split("-")
    elif args["--year"] != "<CUR_YEAR>":
        start_date, end_date = args["--year"] + "0101", args["--year"] + "1231"
    else:
        year = str(datetime.now().year)
        start_date, end_date = year + "0101", year + "1231"
    start_date = datetime.strptime(start_date, "%Y%m%d")
    end_date = datetime.strptime(end_date, "%Y%m%d")

    # Ensure the location exists for where we will save our downloads
    target_dir = os.getcwd() + "/" + "downloads"
    os.makedirs(target_dir, exist_ok=True)

    # Create Playwright context with Chromium
    # Always use CDP for virtual authenticator and remote debugging
    print("🚀 Launching Chromium with CDP debugging on port 9222")
    print("📱 You can connect to this browser at: http://localhost:9222")
    print("🔗 AI assistant can control this browser instance via CDP")

    # Launch browser with CDP endpoint
    browser = playwright.chromium.launch(
        headless=False,
        args=[
            '--remote-debugging-port=9222',
            '--remote-debugging-address=0.0.0.0',
            '--disable-web-security',
            '--disable-features=VizDisplayCompositor',
        ],
    )

    # Connect to the browser using CDP
    browser = playwright.chromium.connect_over_cdp("http://localhost:9222")

    # Create context and page
    context = browser.new_context()
    page = context.new_page()

    # Set up virtual authenticator to prevent passkey dialogs
    print("🔐 Setting up virtual authenticator to disable passkeys")
    try:
        client = page.context.new_cdp_session(page)
        client.send("WebAuthn.enable")
        client.send(
            "WebAuthn.addVirtualAuthenticator",
            {
                "options": {
                    "protocol": "ctap2",
                    "transport": "internal",
                    "hasResidentKey": True,
                    "hasUserVerification": True,
                    "isUserVerified": True,
                    "automaticPresenceSimulation": True,
                }
            },
        )
        print("✅ Virtual authenticator configured successfully")
    except Exception as e:
        print(f"⚠️ Warning: Could not configure virtual authenticator: {e}")

    Stealth().apply_stealth_sync(page)

    # Wait for page to fully load
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")

    # Check if we're on the less fully featured page
    test_less_featured_page = page.query_selector('a:has-text("Returns & Orders")')
    if not test_less_featured_page:
        print("Less featured page detected, navigating to sign-in...")
        page.query_selector('a:has-text("Your Account")').click()
        page.wait_for_load_state("domcontentloaded")
        sleep()

    page.query_selector('a:has-text("Hello, sign in")').click()
    page.wait_for_load_state("domcontentloaded")
    sleep()

    if email:
        page.get_by_label("Email").fill(email)
        page.get_by_role("button", name="Continue").click()
        page.wait_for_load_state("domcontentloaded")
        sleep()

    if password:
        page.get_by_label("Password").fill(password)
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        sleep()

    # Check for 2FA page
    if page.query_selector('title:has-text("Two-Step Verification")'):
        print("🔐 2FA detected - please complete authentication in browser")
        while page.query_selector('title:has-text("Two-Step Verification")'):
            time.sleep(1)
        print("✅ 2FA completed")
    page.wait_for_load_state("domcontentloaded")

    page.wait_for_selector("a >> text=Returns & Orders", timeout=0).click()
    sleep()

    # Get a list of years from the select options
    select = page.query_selector("select#time-filter")
    years = select.inner_text().split("\n")  # skip the first two text options

    # Filter years to include only numerical years (YYYY)
    years = [year for year in years if year.isnumeric()]

    # Filter years to the include only the years between start_date and end_date inclusively
    years = [year for year in years if start_date.year <= int(year) <= end_date.year]
    years.sort(reverse=True)

    # Year Loop (Run backwards through the time range from years to pages to orders)
    for year in years:
        # Select the year in the order filter
        page.select_option('form[action="/your-orders/orders"] select#time-filter', value=f"year-{year}")
        sleep()

        # Page Loop
        first_page = True
        done = False
        while not done:
            # Go to the next page pagination, and continue downloading
            #   if there is not a next page then break
            try:
                if first_page:
                    first_page = False
                else:
                    print("Navigating to next page...")
                    page.locator("ul.a-pagination li.a-last a", has_text="Next").click(timeout=1000)
                sleep()  # sleep after every page load
            except TimeoutError:
                # There are no more pages
                break

            # Order Loop
            order_cards = page.query_selector_all(".order-card.js-order-card")
            for order_card in order_cards:
                # Parse the order card to create the date and file_name
                spans = order_card.query_selector_all("span")
                # Debug:
                # for i,s in enumerate(spans): print(i, s.inner_text())

                # Skip cancelled orders
                if spans[4].inner_text().strip().lower() == "cancelled":
                    continue

                date = datetime.strptime(spans[1].inner_text(), "%B %d, %Y")
                total = spans[3].inner_text().replace("$", "").replace(",", "")  # remove dollar sign and commas
                orderid = get_order_id(spans)
                date_str = date.strftime("%Y-%m-%d")
                file_name = f"{target_dir}/{date_str}_{total}_amazon_{orderid}.pdf"

                if date > end_date:
                    continue
                elif date < start_date:
                    done = True
                    break

                if os.path.isfile(file_name):
                    print(f"File [{file_name}] already exists")
                else:
                    # Click the "Invoice" link inside the card
                    for method in download_method:
                        try:
                            ret = method(context, page, url, order_card, file_name)
                            if ret == ret.SUCCESS:
                                print(f"✅ Successfully downloaded [{file_name}]")
                                break
                            elif ret == ret.SKIPPED:
                                break
                        except Exception as e:
                            print(f"Error downloading [{file_name}]: {e}")

    # Close the browser
    context.close()
    browser.close()


def amazon_invoice_downloader():
    # Load environment variables from .env file if needed
    load_env_if_needed()

    args = docopt(__doc__)
    # print(args)
    if args['--version']:
        print(__version__)
        sys.exit(0)

    with sync_playwright() as playwright:
        run(playwright, args)
