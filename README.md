1. eBay API credentials (free): https://developer.ebay.com/my/keys
2. (Optional) Gmail App Password for email alerts:
   https://myaccount.google.com/apppasswords
3. pip install requests openpyxl python-dotenv --break-system-packages
4. Set env vars (or put in a `.env` file next to this script):
       EBAY_CLIENT_ID=...
       EBAY_CLIENT_SECRET=...
       DIGEST_FROM_EMAIL=...            (optional, enables email alerts)
       DIGEST_FROM_APP_PASSWORD=...     (optional)
       DIGEST_TO_EMAIL=...              (optional, defaults to FROM address)
5. Run:  python tennis_card_engine.py

SCHEDULING
----------
  macOS/Linux (crontab -e), every 6 hours:
      0 */6 * * * cd /path/to/folder && /usr/bin/python3 tennis_card_engine.py >> cron.log 2>&1

  Windows (Task Scheduler): Action -> python.exe, Arguments -> tennis_card_engine.py

  iPad: can't run cron locally -- use GitHub Actions instead (cloud-hosted,
  free, runs on a schedule, no device needed). See IPAD_SETUP.md.

KNOWN LIMITATION
-----------------
Fanatics Collect and Alt.xyz have no public search API and are JS-rendered,
so scripted monitoring there would mean fragile scraping against their ToS.
This engine deliberately covers eBay only. Check those two manually.
"""

import os
import re
import sys
import json
import base64
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


# ============================================================================
# CONFIG -- edit this section to retune players, brands, or the sub-500/
# bookend rules. Nothing below this section needs to change for that.
# ============================================================================

PLAYERS = [
    "Roger Federer",
    "Coco Gauff",
    "Carlos Alcaraz",
    "Pete Sampras",
    "Andre Agassi",
    "Rafael Nadal",
    "Jannik Sinner",
    "Martina Hingis",
    "Iga Swiatek",
    "Alexander Zverev",
    "Mirra Andreeva",
    "Qinwen Zheng",
    "Steffi Graf",
    "Anna Kournikova",
    "Maria Sharapova",
]

# Allowed manufacturers (must exactly match, case-insensitive, the eBay item
# specific "Manufacturer" / "Card Manufacturer" field). "Panini" and "Topps"
# alone are too broad (cover football, basketball, etc.), so those two also
# require one of their SET_KEYWORDS to appear in the item's "Set" specific
# or title.
ALLOWED_MANUFACTURERS = {
    "netpro": None,
    "the netpro trading card company": None,
    "panini": ["panini instant"],
    "topps": ["topps chrome"],
    "ace authentic": None,
    "ace authentic, inc": None,
}

# Manufacturer strings / sellers confirmed during manual review to be
# unlicensed novelty/custom cards -- always rejected regardless of anything
# else matching.
BLOCKED_MANUFACTURER_STRINGS = {
    "pro net",          # impersonates "NetPro" but is an unlicensed custom-card manufacturer
}
BLOCKED_SELLERS = {
    "athletes4christ",  # private seller producing unlicensed custom/novelty cards
}

MAX_PRINT_RUN = 500          # "sub-500": print run must be strictly less than this
PRINT_RUN_INCLUSIVE = False  # set True to allow print run == 500

# A card qualifies if its serial number is 1 ("001") OR equals the print run
# (the "last"/bookend card in the run). True 1/1 cards satisfy both.

EBAY_CATEGORY_ID = "212"     # Sports Trading Cards
MARKETPLACE_ID = "EBAY_US"
RESULTS_PER_QUERY = 50

OUTPUT_XLSX = "tennis_cards_verified.xlsx"
SHEET_NAME = "Verified Active + Licensed"
STATE_FILE = "seen_items.json"
LOG_FILE = "engine_run.log"
NEW_MATCHES_FILE = "new_matches.json"

HEADERS = ["Player", "Manufacturer / Set", "Card Description", "Serial #",
           "Bookend Type", "Price", "eBay Item Link", "Date Found (UTC)"]


# ============================================================================
# Setup: .env loading + logging
# ============================================================================

def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    filename=os.path.join(_BASE_DIR, LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tennis_engine")


# ============================================================================
# eBay OAuth + search
# ============================================================================

def get_ebay_token():
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "ERROR: Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (env vars or .env file).\n"
            "Get free credentials at https://developer.ebay.com/my/keys"
        )
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def search_ebay(token, player, brand_keyword):
    query = f'{player} {brand_keyword} tennis card'
    resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        },
        params={
            "q": query,
            "category_ids": EBAY_CATEGORY_ID,
            "limit": RESULTS_PER_QUERY,
            "filter": "buyingOptions:{FIXED_PRICE|AUCTION}",
        },
        timeout=20,
    )
    if resp.status_code != 200:
        log.warning("Search failed for %r: %s %s", query, resp.status_code, resp.text[:300])
        return []
    return resp.json().get("itemSummaries", [])


def get_item_detail(token, item_id):
    resp = requests.get(
        f"https://api.ebay.com/buy/browse/v1/item/{item_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        return None
    return resp.json()


# ============================================================================
# Filtering logic
# ============================================================================

SERIAL_RE = re.compile(r"\b(\d{1,5})\s*/\s*(\d{1,5})\b")


def extract_serial(title, aspects):
    print_run = None
    card_number = None

    for key in ("Print Run", "print run"):
        if key in aspects:
            try:
                print_run = int(re.sub(r"\D", "", aspects[key][0]))
            except (ValueError, IndexError):
                pass

    for key in ("Card Number", "card number"):
        if key in aspects:
            try:
                card_number = int(re.sub(r"\D", "", aspects[key][0]))
            except (ValueError, IndexError):
                pass

    if print_run and card_number:
        return card_number, print_run

    m = SERIAL_RE.search(title)
    if m:
        return int(m.group(1)), int(m.group(2))

    return None, None


def get_manufacturer_and_set(aspects):
    manu = None
    for key in ("Manufacturer", "Card Manufacturer"):
        if key in aspects:
            manu = aspects[key][0]
            break
    set_name = aspects.get("Set", [""])[0]
    return (manu or "").strip(), (set_name or "").strip()


def is_licensed_and_allowed_brand(title, manufacturer, set_name):
    manu_lower = manufacturer.lower()
    title_lower = title.lower()
    set_lower = set_name.lower()

    if manu_lower in BLOCKED_MANUFACTURER_STRINGS:
        return False, f"blocked manufacturer string: {manufacturer!r}"

    if manu_lower not in ALLOWED_MANUFACTURERS:
        return False, f"manufacturer not in allow-list: {manufacturer!r}"

    required_set_keywords = ALLOWED_MANUFACTURERS[manu_lower]
    if required_set_keywords:
        if not any(kw in set_lower or kw in title_lower for kw in required_set_keywords):
            return False, f"set/title doesn't confirm {required_set_keywords}"

    return True, "ok"


def is_bookend_serial(card_number, print_run):
    if card_number is None or print_run is None:
        return False
    if print_run >= MAX_PRINT_RUN and not (PRINT_RUN_INCLUSIVE and print_run == MAX_PRINT_RUN):
        return False
    return card_number == 1 or card_number == print_run


def matches_player(title, player):
    tokens = player.lower().split()
    title_lower = title.lower()
    return all(tok in title_lower for tok in tokens)


# ============================================================================
# Spreadsheet output
# ============================================================================

def load_or_create_sheet(path):
    if os.path.exists(path):
        wb = load_workbook(path)
        if SHEET_NAME in wb.sheetnames:
            ws = wb[SHEET_NAME]
        else:
            ws = wb.create_sheet(SHEET_NAME)
            _write_header(ws)
        return wb, ws
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    _write_header(ws)
    return wb, ws


def _write_header(ws):
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="2E5B8A", end_color="2E5B8A", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [18, 22, 46, 14, 24, 16, 55, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def append_row(ws, player, manufacturer, set_name, title, card_number, print_run, price, link):
    bookend = "True 1/1 (both bookends)" if card_number == print_run == 1 else (
        f"001 of {print_run}" if card_number == 1 else f"last of {print_run} ({card_number}/{print_run})"
    )
    row = [
        player,
        f"{manufacturer} / {set_name}" if set_name else manufacturer,
        title,
        f"{card_number}/{print_run}",
        bookend,
        price,
        link,
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    ]
    ws.append(row)
    r = ws.max_row
    for cell in ws[r]:
        cell.font = Font(name="Arial", size=10)
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    link_cell = ws.cell(row=r, column=7)
    link_cell.hyperlink = link
    link_cell.font = Font(name="Arial", size=10, color="0563C1", underline="single")
    return bookend


# ============================================================================
# State (dedup across runs)
# ============================================================================

def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()


def save_state(path, seen_ids):
    with open(path, "w") as f:
        json.dump(sorted(seen_ids), f, indent=2)


# ============================================================================
# Email digest (formatted like the restaurant-alert emails, built on our own
# legitimate data -- no scraped code involved, just HTML + smtplib)
# ============================================================================

CARD_TEMPLATE = """
<div style="background:#1a1d24;border-radius:10px;padding:14px 16px;margin:10px 0;
            border:1px solid #1e2028;border-left:3px solid #ffcc00">
  <div style="font-size:15px;font-weight:700;color:#fff">{player}
    <span style="color:#34c759;font-weight:700;margin-left:6px">{bookend}</span>
  </div>
  <div style="font-size:12px;margin-top:2px">
    <span style="color:#bbb">{manufacturer}</span>
    <span style="color:#444"> &middot; </span>
    <span style="color:#888">{serial}</span>
    <span style="color:#444"> &middot; </span>
    <span style="color:#666;font-size:10px;letter-spacing:1px">{price}</span>
  </div>
  <div style="margin-top:8px;font-size:13px;color:#aaa">{title}</div>
  <div style="margin-top:8px">
    <a href="{link}" style="color:#34c759;text-decoration:none;border-bottom:1px dotted #34c759"
       target="_blank">View listing &rarr;</a>
  </div>
</div>
"""

HTML_WRAPPER = """
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            max-width:600px;margin:0 auto;background:#111318;color:#e8e6e1;
            border-radius:12px;overflow:hidden;border:1px solid #1e2028">
  <div style="background:linear-gradient(135deg,#ffcc00,#ff9500);padding:16px 24px">
    <h1 style="margin:0;font-size:18px;color:#000;font-weight:700">
      &#127919; Tennis Card Engine
    </h1>
  </div>
  <div style="padding:24px">
    <h2 style="margin:0 0 12px;font-size:20px;color:#ffcc00">
      New matches &mdash; {count} qualifying listing{plural}
    </h2>
    <div style="color:#ccc;font-size:13px;line-height:1.6">
      {cards}
    </div>
    <hr style="border:none;border-top:1px solid #1e2028;margin:24px 0 16px">
    <p style="color:#555;font-size:11px;margin:0">
      Sub-500 print run, serial #1 or last-of-run, NetPro/Panini Instant/Topps
      Chrome/Ace Authentic only, licensing-verified, active listings only.
    </p>
  </div>
</div>
"""


def build_digest_html(matches):
    cards = "".join(
        CARD_TEMPLATE.format(
            player=m["player"],
            bookend=m["bookend"],
            manufacturer=m["manufacturer"],
            serial=m["serial"],
            price=m["price"],
            title=m["title"],
            link=m["link"],
        )
        for m in matches
    )
    return HTML_WRAPPER.format(count=len(matches), plural="" if len(matches) == 1 else "s", cards=cards)


def send_digest_email(matches):
    if not matches:
        print("No new matches this run -- no email sent.")
        return

    from_email = os.environ.get("DIGEST_FROM_EMAIL")
    app_password = os.environ.get("DIGEST_FROM_APP_PASSWORD")
    to_email = os.environ.get("DIGEST_TO_EMAIL", from_email)

    if not from_email or not app_password:
        print("DIGEST_FROM_EMAIL / DIGEST_FROM_APP_PASSWORD not set -- skipping email send "
              "(spreadsheet was still updated).")
        return

    html_body = build_digest_html(matches)
    subject = f"\U0001F3AF Tennis Card Engine: {len(matches)} new match(es)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(from_email, app_password)
        server.sendmail(from_email, to_email, msg.as_string())

    print(f"Digest sent to {to_email}.")


# ============================================================================
# Main
# ============================================================================

def main():
    xlsx_path = os.path.join(_BASE_DIR, OUTPUT_XLSX)
    state_path = os.path.join(_BASE_DIR, STATE_FILE)
    new_matches_path = os.path.join(_BASE_DIR, NEW_MATCHES_FILE)

    token = get_ebay_token()
    seen_ids = load_state(state_path)
    wb, ws = load_or_create_sheet(xlsx_path)

    brand_keywords = ["NetPro", "Panini Instant", "Topps Chrome", "Ace Authentic"]
    checked = 0
    new_match_records = []

    for player in PLAYERS:
        for brand_kw in brand_keywords:
            items = search_ebay(token, player, brand_kw)
            for item in items:
                checked += 1
                item_id = item.get("itemId")
                if not item_id or item_id in seen_ids:
                    continue

                title = item.get("title", "")
                if not matches_player(title, player):
                    continue

                detail = get_item_detail(token, item_id)
                if not detail:
                    continue
                aspects = {a["name"]: a["value"] if isinstance(a["value"], list) else [a["value"]]
                           for a in detail.get("localizedAspects", [])}

                manufacturer, set_name = get_manufacturer_and_set(aspects)
                licensed_ok, reason = is_licensed_and_allowed_brand(title, manufacturer, set_name)
                if not licensed_ok:
                    log.info("REJECT (%s): %s", reason, title)
                    continue

                seller = (detail.get("seller", {}) or {}).get("username", "").lower()
                if seller in BLOCKED_SELLERS:
                    log.info("REJECT (blocked seller %s): %s", seller, title)
                    continue

                card_number, print_run = extract_serial(title, aspects)
                if not is_bookend_serial(card_number, print_run):
                    continue

                price = detail.get("price", {})
                price_str = f"{price.get('value', '?')} {price.get('currency', '')}".strip()
                link = detail.get("itemWebUrl", item.get("itemWebUrl", ""))

                bookend_label = append_row(ws, player, manufacturer, set_name, title,
                                            card_number, print_run, price_str, link)
                seen_ids.add(item_id)
                new_match_records.append({
                    "player": player,
                    "manufacturer": manufacturer,
                    "set_name": set_name,
                    "title": title,
                    "serial": f"{card_number}/{print_run}",
                    "bookend": bookend_label,
                    "price": price_str,
                    "link": link,
                })
                log.info("MATCH: %s | %s | %s/%s | %s", player, title, card_number, print_run, link)

    wb.save(xlsx_path)
    save_state(state_path, seen_ids)

    with open(new_matches_path, "w") as f:
        json.dump(new_match_records, f, indent=2)

    print(f"Checked {checked} listings across {len(PLAYERS)} players x {len(brand_keywords)} brands.")
    print(f"Added {len(new_match_records)} new qualifying listing(s) to {OUTPUT_XLSX}.")
    if not new_match_records:
        print("No new matches this run -- that's normal, keep it scheduled and it'll catch new listings as they post.")

    send_digest_email(new_match_records)


if __name__ == "__main__":
    main()
