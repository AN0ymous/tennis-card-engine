"""
Tennis Card Engine -- automated eBay monitor for bookend-serial tennis cards.

Scans active eBay listings for every player (or an optional named roster),
keeps only cards from a licensed-manufacturer allow-list, and flags the ones
whose serial number is either the first (#1) or the last card of a sub-500
print run. Qualifying
listings are appended to a spreadsheet and, optionally, emailed as a digest.

SETUP
-----
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
import collections
import hashlib
import logging
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


# ============================================================================
# CONFIG -- edit this section to retune players, brands, or the sub-500/
# bookend rules. Nothing below this section needs to change for that.
# ============================================================================

# Scan every player the brand searches return. Set False to restrict the scan
# to the PLAYERS roster below (the web app can also narrow a single run).
SCAN_ALL_PLAYERS = True
MAX_RESULTS_PER_BRAND = 1200   # when scanning all players: listings fetched per set, newest first

# Item details are the scan's bulk cost: one per listing not judged before.
# eBay's getItems takes up to 20 ids in a single call, and the calls run
# concurrently, so a window of listings costs a few round trips instead of
# one each. Lower DETAIL_WORKERS if eBay starts refusing calls.
DETAIL_BATCH_SIZE = 20         # eBay's getItems ceiling; do not raise
DETAIL_WORKERS = 8             # batches in flight at once
DETAIL_WINDOW = DETAIL_BATCH_SIZE * DETAIL_WORKERS   # listings judged per round

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
    "topps": ["topps chrome", "graphite", "royalty", "topps now"],
    "ace authentic": None,
    "ace authentic, inc": None,
}

# "Graphite" and "Royalty" name the sets on their own in eBay's Set field, but
# as bare words in a title they mean nothing -- every sport has a graphite
# parallel, and sellers call players "tennis royalty" in their prose. In a
# title these two only count with the maker's name attached.
QUALIFIED_IN_TITLE = {"graphite": "topps graphite", "royalty": "topps royalty"}

# Sets to skip even when the manufacturer is allowed, matched as whole words
# against the item's "Set" specific and the title. NetPro's base and Glossy
# sets are the unnumbered mass runs; anything marked "elite" (Elite Glossy is
# serial-numbered) is kept.
# Every NetPro set is in, except base, glossy and Elite glossy. "Elite" on its
# own is fine; "glossy" is out whatever else the title says. The one way back
# in is a buyback: a base card NetPro bought back and stamped with gold
# metallic numbering (the 20th-anniversary Tour Star cards). The listing text
# lets those through; the photo step can confirm the gold foil stamp.
BLOCKED_SETS = {
    "netpro": {"skip": ["base", "glossy"],
               "unless": ["buyback", "buybacks", "buy back", "buy-back", "20th anniversary", "20th netpro"]},
}
BUYBACK_PROMPT = (
    "These are a seller's photos of a NetPro tennis trading card. Is this a NetPro buyback: an "
    "older base card that NetPro bought back and stamped with a serial number in gold metallic "
    "foil (for example 1/1 or 3/10 in shiny gold on the front), often sealed in a case with a "
    "'20th NetPro' seal? A plain printed number, or no number, is not a buyback. Answer unclear "
    "when you cannot tell."
)

# Ace Authentic could not produce on-card autographs, so an Ace Authentic
# card with a signature on the card itself is a fake -- even hand-numbered,
# even slabbed and certified. From the listing text alone a sticker autograph
# (which can be genuine) can't be told from an on-card one, so until the
# photo check exists every Ace Authentic autograph listing is rejected. The
# photo step will refine this to on-card only. Matched as whole words against
# the title, the set, and eBay's own Autographed / Signed By specifics.
# Ace Authentic could not produce on-card autographs, and never numbered a
# card on a round hologram sticker: either one marks a fake. Sticker
# autographs and ordinary numbered cards from them are fine. The listing text
# settles it when it says "sticker" or "on-card"; otherwise the photo step
# decides when ANTHROPIC_API_KEY is set, and without it the card passes with a
# caution the page shows.
AUTOGRAPH_WORDS = ["auto", "autos", "autograph", "autographed", "autographs",
                   "signed", "signature", "signatures"]
CHECKED_AUTOGRAPH_MAKERS = ("ace authentic",)
ON_CARD_RE = re.compile(r"\bon[- ]card\b", re.I)
STICKER_AUTO_RE = re.compile(r"\b(sticker|label)\b", re.I)

# Manufacturer strings / sellers confirmed during manual review to be
# unlicensed novelty/custom cards -- always rejected regardless of anything
# else matching.
BLOCKED_MANUFACTURER_STRINGS = {
    "pro net",          # impersonates "NetPro" but is an unlicensed custom-card manufacturer
}
BLOCKED_SELLERS = {
    "athletes4christ",  # private seller producing unlicensed custom/novelty cards
}

# Cards somebody made themselves. These turn up with a licensed maker in the
# Manufacturer field -- a custom drawn over a Topps Now design still says
# "Topps" -- and sellers give them a 1/1 because only one exists, so neither
# the allow-list nor the serial rule stops them. The words below do, matched
# as whole words against the title and the Set field, before any of that.
#
# "sketch" is in the list by the owner's decision. Licensed artist sketch
# cards do exist and are often genuine 1/1s, so this does turn away some real
# cards -- that is the trade accepted to keep hand-drawn customs out, since
# the two read identically in a listing title. Drop "sketch" and "sketches"
# from the tuple to take it back.
#
# Matched against the title and Set only, never the other item specifics:
# eBay puts a "Custom Bundle: No" specific on a great many ordinary listings,
# and reading that as a custom card would throw away nearly everything.
CUSTOM_CARD_WORDS = (
    "custom", "customs", "custom made", "customized", "customised",
    "aceo",                     # the custom-card format: Art Card Editions and Originals
    "art card", "art cards",
    "fan art", "fanart",
    "hand drawn", "hand-drawn", "handdrawn", "hand painted", "hand-painted",
    "novelty",
    "sketch", "sketches",       # see the note above: this turns away licensed
                                # artist sketch cards along with the customs
    "unlicensed", "unofficial",
)

MAX_PRINT_RUN = 500          # "sub-500": print run must be strictly less than this
PRINT_RUN_INCLUSIVE = False  # set True to allow print run == 500

# A card qualifies if its serial number is 1 ("001") OR equals the print run
# (the "last"/bookend card in the run). True 1/1 cards satisfy both.

EBAY_CATEGORY_ID = "212"     # Sports Trading Cards
MARKETPLACE_ID = "EBAY_US"
PRICE_CURRENCY = "USD"       # currency the price filter is expressed in
PRICE_CEILING = 50000        # top of the price slider on the page; at or above it = no upper limit
CARD_TYPES = {               # page value -> label; what a listing is, read off its text
    "base": "Base",
    "patch": "Patch",
    "auto": "Auto",
    "patch_auto": "Patch auto",
}
AUTO_WORDS = ["auto", "autos", "autograph", "autographed", "autographs", "signed",
              "signature", "signatures", "on-card auto", "sticker auto"]
PATCH_WORDS = ["patch", "patches", "relic", "relics", "jersey", "jerseys", "memorabilia",
               "swatch", "swatches", "game used", "game-used", "match used", "match-used",
               "match worn", "match-worn", "player worn", "player-worn", "event worn",
               "event-worn", "tournament worn", "tournament-worn", "worn material",
               "materials", "material", "shirt", "ball relic", "racket piece"]
CONDITIONS = {"graded": "Graded", "raw": "Raw"}

# Colour match: the colour parallel named in the listing (Blue Refractor,
# Gold Wave...) against the predominant colour of the player's outfit in the
# seller's photo. The outfit colour needs a look at the photo, which is done
# by Claude when ANTHROPIC_API_KEY is set (a GitHub secret on the hosted
# setup); one short call per recorded card that has a colour parallel,
# answers cached in VISION_CACHE_FILE so a photo is never asked about twice.
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-opus-5")
VISION_CACHE_FILE = "vision_cache.json"
PARALLEL_COLOURS = ["blue", "red", "green", "gold", "orange", "purple", "pink", "black",
                    "aqua", "sepia", "yellow", "magenta", "teal", "bronze", "silver", "white",
                    "rose", "lavender", "mint", "navy", "cyan", "turquoise"]
OUTFIT_COLOURS = ["white", "black", "grey", "blue", "navy", "red", "green", "yellow", "orange",
                  "purple", "pink", "brown", "teal", "gold", "silver", "mixed", "unclear"]
COLOUR_FAMILIES = {                # parallel colour -> outfit colours that count as a match
    "blue": {"blue", "navy"}, "navy": {"blue", "navy"}, "cyan": {"teal", "blue"},
    "aqua": {"teal", "blue"}, "teal": {"teal", "blue"}, "turquoise": {"teal", "blue"},
    "red": {"red"}, "rose": {"pink", "red"}, "pink": {"pink"}, "magenta": {"pink", "purple"},
    "green": {"green"}, "mint": {"green", "teal"}, "gold": {"gold", "yellow"}, "yellow": {"yellow", "gold"},
    "orange": {"orange"}, "bronze": {"brown", "orange"}, "sepia": {"brown"},
    "purple": {"purple"}, "lavender": {"purple", "pink"}, "black": {"black"},
    "white": {"white"}, "silver": {"silver", "grey", "white"},
}
PARALLEL_WORDS = r"(?:refractor|refractors|wave|shimmer|speckle|prism|lava|raywave|ray wave|x-?fractor|foil|parallel|sapphire|mojo|atomic|helix|ice|cracked ice|\d+\s*/\s*\d+|/\s*\d|#)"
GRADERS = ["psa", "bgs", "sgc", "cgc", "csg", "hga", "isa", "gma", "ksa", "beckett", "tag"]
GRADE_RE = re.compile(
    r"\b(psa|bgs|sgc|cgc|csg|hga|isa|gma|ksa|beckett|tag)\b[\s:-]*"
    r"(?:gem\s*(?:mint|mt)|pristine|black\s*label|mint|nm-mt|nm)?[\s:-]*(10|[1-9](?:\.5)?)\b(?!\s*/)",
    re.I)
GRADER_RE = re.compile(r"\b(psa|bgs|sgc|cgc|csg|hga|beckett)\b", re.I)   # a grader named at all
GRADED_WORDS_RE = re.compile(r"\b(graded|slab|slabbed|gem\s*(?:mint|mt)\s*10)\b", re.I)
LISTING_TYPES = {            # page value -> eBay buying option
    "buy_now": "FIXED_PRICE",
    "auction": "AUCTION",
}
RESULTS_PER_QUERY = 50

# EBAY_ENV=sandbox points every call at eBay's sandbox, which has no real
# listings but lets you check that your keys and the API calls work. Sandbox
# keysets carry "SBX" in their App ID and only work there. Leave unset for
# the real marketplace, which needs a Production keyset.
EBAY_ENV = os.environ.get("EBAY_ENV", "production").strip().lower()
EBAY_API_BASE = ("https://api.sandbox.ebay.com" if EBAY_ENV == "sandbox"
                 else "https://api.ebay.com")

OUTPUT_XLSX = "tennis_cards_verified.xlsx"
SHEET_NAME = "Verified Active + Licensed"
STATE_FILE = "seen_items.json"
LOG_FILE = "engine_run.log"
NEW_MATCHES_FILE = "new_matches.json"
STATUS_FILE = "status.json"          # active / sold / ended, per listing, for saved cards
SCAN_CURSOR_FILE = "scan_cursors.json"  # newest listing timestamp reached by each exact search
API_USAGE_FILE = "ebay_api_usage.json"  # persistent daily safety counter (Pacific date)
API_DAILY_BUDGET = int(os.environ.get("EBAY_DAILY_CALL_BUDGET") or "4500")
_API_USAGE_LOCK = threading.Lock()

HEADERS = ["Player", "Manufacturer / Set", "Card Description", "Serial #",
           "Bookend Type", "Price", "eBay Item Link", "Date Found (UTC)", "Image",
           "Listed (UTC)", "Listing Type", "Card Type", "Grading", "Extra Images",
           "Parallel", "Outfit Colour", "Colour Match", "Caution", "Sport"]


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


def say(message):
    """A warning worth seeing. log.warning alone goes to engine_run.log, which
    on the hosted run lives only on the runner and is thrown away with it."""
    log.warning(message)
    print(f"WARNING: {message}", file=sys.stderr)


# ============================================================================
# eBay OAuth + search
# ============================================================================

class EngineError(RuntimeError):
    """Recoverable, user-facing problem (bad config, eBay refused us)."""


def have_credentials():
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def get_ebay_token():
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise EngineError(
            "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (env vars or .env file). "
            "Get free credentials at https://developer.ebay.com/my/keys"
        )
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        f"{EBAY_API_BASE}/identity/v1/oauth2/token",
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
    if resp.status_code != 200:
        hint = ""
        if "SBX" in client_id.upper() and EBAY_ENV != "sandbox":
            hint = (" Your App ID contains 'SBX', so this is a sandbox keyset; either use the "
                    "Production keyset from developer.ebay.com/my/keys, or set EBAY_ENV=sandbox "
                    "to test against the sandbox (which has no real listings).")
        raise EngineError(f"eBay refused the credentials ({resp.status_code}): "
                          f"{resp.text[:200]}.{hint}")
    return resp.json()["access_token"]


# eBay's own figures for how much of the daily call allowance is left. The
# keys never leave the machine holding them: the scheduled run writes the
# numbers into results/, and the local server answers from its .env, so the
# browser is only ever handed the counts.
RATE_LIMIT_URL = f"{EBAY_API_BASE}/developer/analytics/v1_beta/rate_limit/"
BROWSE_RATE_FILTER = {"api_context": "buy", "api_name": "browse", "api_version": "v1"}


def browse_allowance(token):
    """What is left of the Browse API's daily allowance, or {} if eBay says
    nothing. eBay answers 204 with no body when it holds no figures at all --
    a keyset that has made no calls yet, or one not enabled for the Analytics
    API -- which is not an error, just nothing to show."""
    try:
        resp = requests.get(RATE_LIMIT_URL, headers={"Authorization": f"Bearer {token}"},
                            params=BROWSE_RATE_FILTER, timeout=20)
        if resp.status_code != 200:
            log.warning("Rate limit read returned %s: %s", resp.status_code, resp.text[:200])
            return {}
        limits = resp.json().get("rateLimits", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("Rate limit read failed: %s", exc)
        return {}

    for entry in limits:
        for resource in entry.get("resources", []):
            for rate in resource.get("rates", []):
                cap, left = rate.get("limit"), rate.get("remaining")
                if cap is None or left is None:
                    continue
                return {
                    "name": resource.get("name") or entry.get("apiName") or "buy.browse",
                    "limit": cap,
                    "remaining": left,
                    "used": cap - left,
                    "resets": rate.get("reset") or "",
                    "window": rate.get("timeWindow") or 0,
                }
    return {}


def price_filter(min_price=None, max_price=None):
    """The Browse API's price clause for a range; '' when the range is open."""
    lo = f"{float(min_price):g}" if min_price else ""
    hi = f"{float(max_price):g}" if max_price and float(max_price) < PRICE_CEILING else ""
    if not lo and not hi:
        return ""
    return f"price:[{lo}..{hi}],priceCurrency:{PRICE_CURRENCY}"


def consume_api_call(bucket="browse"):
    """Reserve one call from a conservative per-key, per-Pacific-day budget."""
    with _API_USAGE_LOCK:
        path = os.path.join(_BASE_DIR, API_USAGE_FILE)
        today = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
        usage = {"date": today, "browse": 0, "getItems": 0}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    saved = json.load(f)
                if saved.get("date") == today:
                    usage.update(saved)
            except (OSError, ValueError, AttributeError):
                pass
        used = int(usage.get(bucket, 0) or 0)
        if used >= API_DAILY_BUDGET:
            raise EngineError(
                f"Stopped before eBay's daily limit: local {bucket} safety budget "
                f"of {API_DAILY_BUDGET} calls is exhausted for {today} Pacific time."
            )
        usage[bucket] = used + 1
        temporary = path + ".tmp"
        with open(temporary, "w") as f:
            json.dump(usage, f, indent=2)
        os.replace(temporary, path)


def _has_word(words, text):
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def classify_card(title, aspects=None):
    """'base', 'patch', 'auto' or 'patch_auto' from the title and item specifics."""
    aspects = aspects or {}
    text = f" {title} ".lower()
    features = " ".join(str(v) for vals in (aspects.get("Features") or [],) for v in vals).lower()
    signed = str((aspects.get("Autographed") or [""])[0]).strip().lower() in ("yes", "true")
    signed = signed or bool(aspects.get("Signed By")) or "autograph" in features
    auto = signed or _has_word(AUTO_WORDS, text)
    patch = _has_word(PATCH_WORDS, text) or any(
        w in features for w in ("memorabilia", "relic", "patch", "jersey", "game used", "worn"))
    if auto and patch:
        return "patch_auto"
    if auto:
        return "auto"
    if patch:
        return "patch"
    return "base"


def classify_grading(title, aspects=None):
    """('graded', 'PSA 10') / ('graded', 'BGS') / ('raw', 'Raw') from the title and specifics."""
    aspects = aspects or {}
    first = lambda key: str((aspects.get(key) or [""])[0]).strip()
    grader = first("Professional Grader")
    grade = first("Grade")
    graded_flag = first("Graded").lower()
    if graded_flag == "no" or grader.lower() in ("not graded", "ungraded", "none") or grade.lower() == "ungraded":
        return "raw", "Raw"
    if grader and grader.lower() not in ("not graded", "ungraded", "none"):
        return "graded", " ".join(x for x in (grader, grade) if x)
    if graded_flag == "yes" or (grade and grade.lower() != "ungraded"):
        return "graded", grade or "Graded"
    m = GRADE_RE.search(title)
    if m:
        return "graded", f"{m.group(1).upper()} {m.group(2)}"
    m = GRADER_RE.search(title)
    if m:
        return "graded", m.group(1).upper()
    if GRADED_WORDS_RE.search(title):
        return "graded", "Graded"
    return "raw", "Raw"


def parallel_colour(title, aspects=None):
    """The colour of the parallel named on the listing ('blue', 'gold'...), or ''."""
    aspects = aspects or {}
    for key in ("Parallel/Variety", "Parallel", "Variety"):
        for value in aspects.get(key) or []:
            m = re.search(rf"\b({'|'.join(PARALLEL_COLOURS)})\b", str(value).lower())
            if m:
                return m.group(1)
    text = title.lower()
    m = re.search(rf"\b({'|'.join(PARALLEL_COLOURS)})\b\s+(?:\w+\s+)?{PARALLEL_WORDS}", text)
    if m:
        return m.group(1)
    return ""


def colours_match(parallel, outfit):
    return bool(parallel) and outfit in COLOUR_FAMILIES.get(parallel, {parallel})


def vision_available():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


_vision_cache = None


def _load_vision_cache():
    global _vision_cache
    if _vision_cache is None:
        path = os.path.join(_BASE_DIR, VISION_CACHE_FILE)
        _vision_cache = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    _vision_cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                _vision_cache = {}
    return _vision_cache


def _save_vision_cache():
    with open(os.path.join(_BASE_DIR, VISION_CACHE_FILE), "w") as f:
        json.dump(_vision_cache, f, indent=1)


OUTFIT_PROMPT = (
    "This is a seller's photo of a tennis trading card. Name the predominant colour of the "
    "player's clothing (shirt, dress, or top; include shorts or skirt if they dominate). "
    "Judge the clothing only: ignore the card border, the background, any refractor or foil "
    "tint over the whole card, the court, skin, hair, the racket and any text. If the outfit "
    "is a near-even mix of colours answer 'mixed'; if you cannot see the clothing answer 'unclear'."
)


def ask_photo(image_urls, prompt, schema):
    """One structured question to Claude about one or more photos. Returns the
    parsed answer, or {} on a refusal."""
    import anthropic
    client = anthropic.Anthropic()
    content = [{"type": "image", "source": {"type": "url", "url": u}} for u in image_urls]
    content.append({"type": "text", "text": prompt})
    response = client.messages.create(
        model=VISION_MODEL,
        max_tokens=256,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        return {}
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def ask_outfit_colour(image_url):
    """One question to Claude about one photo. Returns (colour, confidence)."""
    data = ask_photo([image_url], OUTFIT_PROMPT, {
        "type": "object",
        "properties": {
            "outfit": {"type": "string", "enum": OUTFIT_COLOURS},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["outfit", "confidence"],
        "additionalProperties": False,
    })
    return data.get("outfit", "unclear"), data.get("confidence", "low")


def outfit_colour(image_url):
    """Cached answer for a photo; '' when the photo step is not available."""
    if not image_url or not vision_available():
        return ""
    cache = _load_vision_cache()
    if image_url in cache:
        return cache[image_url].get("outfit", "")
    try:
        colour, confidence = ask_outfit_colour(image_url)
    except Exception as exc:                                  # noqa: BLE001 -- a photo step must never sink a scan
        log.warning("Outfit colour check failed for %s: %s", image_url, exc)
        return ""
    cache[image_url] = {"outfit": colour, "confidence": confidence,
                        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _save_vision_cache()
    return colour


def colour_reading(title, aspects, image_url):
    """(parallel, outfit, verdict) where verdict is 'yes', 'no' or 'unknown'."""
    parallel = parallel_colour(title, aspects)
    if not parallel:
        return "", "", "no"                     # nothing to match against
    outfit = outfit_colour(image_url)
    if not outfit or outfit in ("mixed", "unclear"):
        return parallel, outfit, "unknown"
    return parallel, outfit, "yes" if colours_match(parallel, outfit) else "no"


def buying_options(listing_types=None):
    """eBay buying options for the page's listing types; both when unset."""
    chosen = [LISTING_TYPES[t] for t in (listing_types or []) if t in LISTING_TYPES]
    return chosen or list(LISTING_TYPES.values())


def listing_label(item):
    """'Buy It Now', 'Auction' or 'Auction + Buy It Now' from an item's buying options."""
    opts = set(item.get("buyingOptions") or [])
    parts = []
    if "AUCTION" in opts:
        parts.append("Auction")
    if "FIXED_PRICE" in opts:
        parts.append("Buy It Now")
    return " + ".join(parts)


def search_ebay(token, query, limit=None, offset=0, newest_first=False,
                min_price=None, max_price=None, listing_types=None):
    """One Browse API search. Returns (item summaries, total available, and
    whether eBay says a further page exists)."""
    filters = ["buyingOptions:{%s}" % "|".join(buying_options(listing_types))]
    clause = price_filter(min_price, max_price)
    if clause:
        filters.append(clause)
    params = {
        "q": query,
        "category_ids": EBAY_CATEGORY_ID,
        "limit": limit or RESULTS_PER_QUERY,
        "offset": offset,
        "filter": ",".join(filters),
    }
    if newest_first:
        params["sort"] = "newlyListed"
    consume_api_call("browse")
    resp = requests.get(
        f"{EBAY_API_BASE}/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
        },
        params=params,
        timeout=20,
    )
    if resp.status_code != 200:
        log.warning("Search failed for %r: %s %s", query, resp.status_code, resp.text[:300])
        raise SearchError(resp.status_code, query, offset, resp.text)
    data = resp.json()
    # "next" is eBay's own word that a further page exists; "total" is an
    # estimate that runs high, so it cannot say when the last page has been
    # reached, and a page shorter than asked for is not promised to be last.
    return (data.get("itemSummaries", []), int(data.get("total", 0) or 0),
            bool(data.get("next")))


class SearchError(EngineError):
    """eBay refused a search page. Carries enough to say why in the log."""

    def __init__(self, status, query, offset, body):
        self.status, self.query, self.offset = status, query, offset
        try:
            errors = json.loads(body).get("errors") or []
            detail = "; ".join(str(e.get("longMessage") or e.get("message") or "") for e in errors) or body[:200]
        except (ValueError, AttributeError):
            detail = body[:200]
        hint = ""
        if status == 429 or "call limit" in detail.lower() or "quota" in detail.lower():
            hint = " -- eBay's daily call allowance is used up; it resets at midnight Pacific time"
        elif status in (401, 403):
            hint = " -- the token was refused; check the keys and that the keyset is enabled"
        super().__init__(f"eBay refused the search {query!r} at listing {offset + 1}: HTTP {status} {detail}{hint}")


def iter_listings(token, player, brand_kw, on_page=None, should_stop=None,
                  min_price=None, max_price=None, listing_types=None, limit=None,
                  on_error=None, high_water=None, on_complete=None):
    """Yield listings for one brand search: a single page when a player is
    named, or up to MAX_RESULTS_PER_BRAND newest-first when scanning everyone."""
    price = {"min_price": min_price, "max_price": max_price, "listing_types": listing_types}
    # A named player is searched by surname: eBay matches every word, so
    # "Shapovalov Topps Chrome" returns everything "Denis Shapovalov Topps
    # Chrome" would and more, and the judge decides afterwards whether a
    # listing really is that player. The full name is searched as well only
    # when the surname search hit the cap, since then listings past the cap
    # may still be reachable by the narrower search. Everyone else goes
    # through wide_query(). All page through everything, newest first.
    # high_water is a mark, or a function of the query giving one.
    if player:
        variants = name_variants(player)
        queries = [f"{variants[-1]} {brand_kw}"]
        narrower = f"{variants[0]} {brand_kw}" if len(variants) > 1 else None
    else:
        queries, narrower = [wide_query(brand_kw)], None
    cap = limit or MAX_RESULTS_PER_BRAND
    seen_here = set()
    while queries:
        query = queries.pop(0)
        mark = high_water(query) if callable(high_water) else high_water
        fetched = 0
        newest_seen = ""
        completed = True
        stopped_at_mark = False
        while fetched < cap:
            if should_stop and should_stop():
                return
            page = min(200, cap - fetched)                      # 200 is the API ceiling
            try:
                items, total, more = search_ebay(token, query, page, offset=fetched,
                                                 newest_first=True, **price)
            except SearchError as exc:
                # say so where the reader can see it, then move to the next query
                if on_error:
                    on_error(str(exc))
                completed = False
                break
            if on_page:
                on_page(len(items), fetched, total, query)
            if not items:
                break
            fetched += len(items)
            for item in items:
                created = item.get("itemCreationDate") or ""
                if created and not newest_seen:
                    newest_seen = created
                # Include equal timestamps so a listing created during the
                # same timestamp tick after the last run cannot be missed.
                if mark and created and created < mark:
                    stopped_at_mark = True
                    break
                item_id = item.get("itemId")
                if item_id in seen_here:
                    continue                                    # already fetched under another name
                seen_here.add(item_id)
                yield item
            # eBay's total is an estimate that runs high, so the last page
            # used to be followed by an empty one, a call each: stop when eBay
            # itself says there is no further page.
            if stopped_at_mark or fetched >= total or not more:
                break
        if completed and on_complete:
            on_complete(query, newest_seen)
        if narrower and fetched >= cap and not stopped_at_mark:
            queries.append(narrower)
            narrower = None


# What a fetch says when it says nothing: None means eBay no longer serves
# the listing (404 or 410 -- it ended, or was taken down), REFUSED means the
# call itself failed (the allowance is used up, eBay is down, the network
# dropped) and nothing is known. The two used to be the same None, and on 17
# Sep a refused batch of status calls recorded 55 live listings as ended.
REFUSED = False


def get_item_detail(token, item_id):
    """One listing's full details; None when eBay no longer serves it;
    REFUSED when the call failed and nothing is known."""
    consume_api_call("browse")
    try:
        resp = requests.get(
            f"{EBAY_API_BASE}/buy/browse/v1/item/{item_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        # raising here would take down every other listing sharing the pool
        log.warning("Item detail failed for %s: %s", item_id, exc)
        return REFUSED
    if resp.status_code in (404, 410):
        return None
    if resp.status_code != 200:
        log.warning("Item detail failed for %s: %s %s", item_id, resp.status_code, resp.text[:200])
        return REFUSED
    try:
        return resp.json()
    except ValueError:                       # a 200 that is not JSON
        log.warning("Item detail for %s was not JSON", item_id)
        return REFUSED


# Set False for the rest of the process the first time eBay says it has no
# bulk item endpoint, or does not offer it to this keyset, so a scan stops
# paying for a call that cannot work. Run 42 on 17 Sep: three batch status
# calls, all turned away, and not one of the 55 wrongly "ended" statuses
# repaired -- the old code fell back to single calls without a word, and
# every batch call ever made may have been the same wasted call.
_bulk_details_supported = True
_bulk_refusal_said = False


def _say_bulk_refused(what):
    """Once per process: a refused batch is worth one line in the run log,
    not one per batch."""
    global _bulk_refusal_said
    if not _bulk_refusal_said:
        _bulk_refusal_said = True
        say(f"eBay refused a bulk item call ({what}); the listings in it keep "
            "their last reading and are asked about again next run")

# Judging never batches. Measured 17 Sep: eBay's getItems hands back the item
# specifics the judge reads for about 4 listings in 100, so a batch of 20
# cost one call and then 19 single calls anyway -- 20.2 calls per 20
# listings against 20 without it. Statuses need no specifics, so they still
# go in batches of 20, where the batch really is the answer.


def _bulk_item_details(token, item_ids):
    """One getItems call: up to DETAIL_BATCH_SIZE listings, keyed by item id.

    Returns what the call gave back; {} when the endpoint is not there in this
    form, so the caller falls back to single calls; REFUSED when the call
    itself failed, so the caller does not spend a single call per listing
    finding out the same thing twenty times over."""
    global _bulk_details_supported
    if not _bulk_details_supported or not item_ids:
        return {}
    consume_api_call("getItems")
    try:
        resp = requests.get(
            f"{EBAY_API_BASE}/buy/browse/v1/item",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
            },
            params={"item_ids": ",".join(item_ids)},
            timeout=30,
        )
    except requests.RequestException as exc:
        _say_bulk_refused(f"the call failed: {exc}")
        return REFUSED
    if resp.status_code in (400, 403, 404, 405):
        # not there, not there in this form, or not offered to this keyset
        # (eBay lists getItems as a limited release): stop asking for this
        # process and let the single calls, which do work, take over. Said
        # out loud, with eBay's own words, so the run log settles which it is.
        _bulk_details_supported = False
        say(f"eBay does not answer the bulk item call for this keyset (HTTP "
            f"{resp.status_code}: {resp.text[:200]}); checking listings one at a "
            "time for the rest of this run")
        return {}
    if resp.status_code != 200:
        _say_bulk_refused(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return REFUSED
    try:
        items = resp.json().get("items") or []
    except ValueError:
        _say_bulk_refused("the answer was not JSON")
        return REFUSED
    return {entry["itemId"]: entry for entry in items if entry.get("itemId")}


def _in_parallel(fn, jobs, workers=None):
    """fn over jobs, results in the order of jobs. fn must not raise."""
    jobs = list(jobs)
    if len(jobs) <= 1:
        return [fn(job) for job in jobs]
    workers = max(1, min(workers or DETAIL_WORKERS, len(jobs)))
    if workers == 1:
        return [fn(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, jobs))


def get_item_details(token, item_ids, workers=None, require="localizedAspects", failures=None):
    """Details for many listings, keyed by item id.

    With `require` set (the judge needs localizedAspects) every listing is
    fetched singly, several at a time: the batch endpoint rarely carries the
    specifics, so batching cost more than it saved. With no requirement
    (statuses) the ids go in batches of DETAIL_BATCH_SIZE, and anything a
    batch leaves out is fetched singly afterwards.

    An id absent from the result is one eBay no longer serves. An id whose
    call failed instead -- nothing is known about it -- goes into `failures`
    when a set is given, so a caller can tell "gone" from "not answered"."""
    ids = [i for i in dict.fromkeys(item_ids) if i]
    if not ids:
        return {}
    refused = set()

    details = {}
    if not require:
        batches = [ids[i:i + DETAIL_BATCH_SIZE] for i in range(0, len(ids), DETAIL_BATCH_SIZE)]
        for batch, got in zip(batches, _in_parallel(
                lambda batch: _bulk_item_details(token, batch), batches, workers)):
            if got is REFUSED:
                refused.update(batch)             # not worth twenty single calls to learn again
            else:
                details.update(got)

    # localizedAspects carries the manufacturer, set, print run and card
    # number: without it a listing cannot be judged, only wrongly rejected.
    missing = [i for i in ids if i not in refused
               and not (details.get(i) if not require else (details.get(i) or {}).get(require))]
    if missing:
        for item_id, detail in zip(missing, _in_parallel(
                lambda i: get_item_detail(token, i), missing, workers)):
            if detail:
                details[item_id] = detail
            else:
                details.pop(item_id, None)
                if detail is REFUSED:
                    refused.add(item_id)
    if failures is not None:
        failures.update(refused)
    return details


def save_outputs(wb, xlsx_path, seen, state_path, cursors, cursor_path,
                 records, matches_path):
    """Put on disk everything a run earned, whether or not it finished.

    Each piece is written on its own so one failure cannot take the others
    with it, and a failure is said out loud rather than buried in a log file
    the hosted run throws away."""
    for what, write in (
            ("spreadsheet", lambda: wb.save(xlsx_path)),
            ("record of listings already judged", lambda: save_state(state_path, seen)),
            ("scan cursors", lambda: save_state(cursor_path, cursors)),
            ("list of new matches", lambda: write_json_atomically(matches_path, records)),
    ):
        try:
            write()
        except Exception as exc:                          # noqa: BLE001
            say(f"could not save the {what}: {exc}")


# A reject is permanent only while the rule that made it stands. Bump this
# when a permanent rule changes in a way that could reverse an old reject,
# and name the old reason's opening words in RECONSIDER_REASONS, so those
# rejects -- and only those -- are judged again when a walk next reaches
# them. A reject made under the current version is never looked at twice.
JUDGE_VERSION = 2
RECONSIDER_REASONS = {
    2: ("manufacturer not in allow-list",),      # a blank or line-named maker now reads off the title
}


def reject_stands(entry, title):
    """Whether a recorded reject is still trusted for this listing.

    A reject from before 17 Sep is a bare string with no reason and no
    version. The only thing known about it is the title in front of us, so
    it stands unless the title carries a bookend serial -- the one case worth
    a call, since that listing could be a match the old rules turned away. A
    reject with no serial in the title stays; the specifics might carry one
    (1 of 71 recorded matches did), and that slice of the backlog is the
    price of not re-fetching thousands. A versioned reject stands unless its
    version is older than the judge and its reason is one a newer version
    reconsiders."""
    if not isinstance(entry, dict):
        card_number, print_run = extract_serial(title, {})
        return not (card_number is not None and print_run is not None
                    and card_number in (1, print_run))
    made = int(entry.get("judge") or 1)
    if made >= JUDGE_VERSION:
        return True
    reason = str(entry.get("reason") or "")
    return not any(reason.startswith(opening)
                   for version, openings in RECONSIDER_REASONS.items()
                   if version > made for opening in openings)


# A detail eBay would not return blocks the set's mark, so the next run walks
# past it again and retries -- a real listing behind a hiccup is not lost.
# After this many tries it is recorded as gone and the mark moves on.
UNAVAILABLE_TRIES = 3


def rejected(reason):
    """The state entry for a reject made by this judge."""
    return {"verdict": "reject", "reason": reason, "judge": JUDGE_VERSION}


def settled_by_summary(item):
    """What the search result alone settles, before any detail call: a
    blocked seller (the summary names the seller), then everything the
    title settles."""
    seller = ((item.get("seller") or {}).get("username") or "").lower()
    if seller and seller in BLOCKED_SELLERS:
        return f"blocked seller: {seller}"
    return settled_by_title(item.get("title", ""))


def settled_by_title(title):
    """A reject the title proves on its own, so the listing costs no detail
    call: a custom, another sport, or a serial that is not a bookend. The
    serial is read exactly as the judge reads it (the title first), so this
    never turns away a card the judge would have kept. A title with no serial
    is not settled -- the specifics may carry one -- and is fetched as before.
    Measured on 17 Sep: a never-seen listing costs about one call whatever the
    batching does, so every listing settled here is a call kept."""
    custom = looks_custom(title, "")
    if custom:
        return f"custom or novelty card: says {custom!r}"
    sport = other_sport_in_title(title)
    if sport:
        return f"title says {sport}, not tennis"
    card_number, print_run = extract_serial(title, {})
    if card_number is not None and print_run is not None and card_number not in (1, print_run):
        return f"{card_number}/{print_run} is neither the first nor the last of its run"
    return ""


def _windows(items, size):
    """Consecutive slices of an iterable, the last one short."""
    window = []
    for item in items:
        window.append(item)
        if len(window) >= size:
            yield window
            window = []
    if window:
        yield window


# ============================================================================
# Listing status: is a saved card still up, sold, or ended?
# ============================================================================

ITEM_LINK_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,})")


def item_id_from_link(link):
    """Browse API item id ('v1|123456789012|0') from an eBay listing link."""
    m = ITEM_LINK_RE.search(link or "")
    return f"v1|{m.group(1)}|0" if m else ""


def status_from_detail(detail):
    """One listing's state, read off details already fetched."""
    now = datetime.now(timezone.utc)
    result = {"status": "unknown", "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if detail is None:
        result["status"] = "ended"           # eBay no longer serves it
        return result
    end_raw = detail.get("itemEndDate") or ""
    ended = False
    if end_raw:
        try:
            ended = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) <= now
            result["endDate"] = end_raw
        except ValueError:
            pass
    avail = (detail.get("estimatedAvailabilities") or [{}])[0]
    sold_qty = int(avail.get("estimatedSoldQuantity") or 0)
    out_of_stock = avail.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK"
    bids = int(detail.get("bidCount") or 0)
    price = detail.get("currentBidPrice") or detail.get("price") or {}
    if price:
        result["price"] = f"{price.get('value', '?')} {price.get('currency', '')}".strip()
    if bids:
        result["bids"] = bids
    if out_of_stock or (ended and (sold_qty > 0 or bids > 0)):
        result["status"] = "sold"
    elif ended:
        result["status"] = "ended"
    else:
        result["status"] = "active"
    return result


def check_listing_status(token, item_id):
    """One listing's state from the Browse API item call; a refused call
    says nothing, so the reading is "unknown", never "ended"."""
    detail = get_item_detail(token, item_id)
    if detail is REFUSED:
        return {"status": "unknown",
                "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if detail is None:
        return dict(status_from_detail(None), gone=True)
    return status_from_detail(detail)


# An active reading younger than this is not asked for again: several scans
# in an hour used to re-check every unsold row each time, four batch calls a
# run that cost more than the repeat scan itself. The daily run is always
# past it. A SOLD flag can therefore lag by up to an hour; set 0 to re-check
# every run.
STATUS_FRESH_SECONDS = 3600


def status_settled(entry):
    """A reading that will never change: sold, or ended on eBay's word --
    an end date it gave, or a listing it no longer serves. An "ended" with
    neither is what a refused call used to be recorded as, and is asked
    about again."""
    status = (entry or {}).get("status")
    if status == "sold":
        return True
    return status == "ended" and bool(entry.get("endDate") or entry.get("gone"))


def status_fresh(entry, now, within=None):
    within = STATUS_FRESH_SECONDS if within is None else within
    try:
        checked = datetime.strptime((entry or {}).get("checkedAt", ""), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return (now - checked.replace(tzinfo=timezone.utc)).total_seconds() < within


def refresh_statuses(token, item_ids, previous=None, fresh_within=None, report=None):
    """Check the listings named; settled ones keep their reading, fresh ones
    are not asked about again, and a listing eBay refused to answer for
    keeps its last reading rather than being called ended. `report`, when a
    dict is given, is filled with how many were asked about, refused and
    found gone, so the export can say what happened."""
    statuses = dict(previous or {})
    if report is not None:
        report.update(asked=0, refused=0, gone=0)
    now = datetime.now(timezone.utc)
    stale = [i for i in dict.fromkeys(item_ids)
             if i and not status_settled(statuses.get(i))
             and not ((statuses.get(i) or {}).get("status") == "active"
                      and status_fresh(statuses.get(i), now, fresh_within))]
    if not stale:
        return statuses
    # no field is required: a status reads off whatever eBay returns
    refused = set()
    details = get_item_details(token, stale, require=None, failures=refused)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    gone = 0
    for item_id in stale:
        if item_id in refused:
            statuses[item_id] = statuses.get(item_id) or {"status": "unknown", "checkedAt": stamp}
        elif item_id not in details:
            statuses[item_id] = {"status": "ended", "gone": True, "checkedAt": stamp}
            gone += 1
        else:
            statuses[item_id] = status_from_detail(details[item_id])
    if report is not None:
        report.update(asked=len(stale), refused=len(refused), gone=gone)
    return statuses


def item_ids_in_spreadsheet(xlsx_path, limit=300):
    """Item ids of the most recent rows, newest last-found first."""
    if not os.path.exists(xlsx_path):
        return []
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip() for h in next(rows, ())]
    col = header.index("eBay Item Link") if "eBay Item Link" in header else 6
    ids = [item_id_from_link(str(r[col] or "")) for r in rows if len(r) > col]
    wb.close()
    return [i for i in reversed(ids) if i][:limit]


# ============================================================================
# Filtering logic
# ============================================================================

# (?<!\.) so "BGS 9.5/10" does not yield 5/10 -- the numerator must not be the
# tail of a decimal grade.
SERIAL_RE = re.compile(r"(?<!\.)\b(\d{1,5})\s*/\s*(\d{1,5})\b")

# The word right before an N/M that marks it as a grade, not a serial. A
# grader's name, or the grade words sellers put between the grader and the
# numbers ("Psa MINT 9/9"). Deliberately not "auto": "Rookie Auto 5/5" is a
# real serial after the word auto, and 5/5 is exactly the bookend wanted.
GRADE_CONTEXT_RE = re.compile(
    r"\b(psa|bgs|sgc|cgc|csg|hga|isa|gma|ksa|beckett|tag|mint|gem|mt|nm|grade|graded)\s*$", re.I)


def is_grade_pair(title, match):
    """Whether this N/M is a card grade over an autograph grade -- "PSA 9/9",
    "Psa MINT 9/9", "BGS 9.5/10" -- rather than a serial position.

    Both numbers are grades (10 or under) and the word immediately before is
    grade context. Only the word immediately before: in "PSA 10 1/10" that
    word is "10", a number, so the 1/10 is read as the serial it is, with the
    grade stated separately in front of it. Three recorded matches were PSA 9
    autos written "Psa MINT 9/9", each kept as the last of a run of nine."""
    n, run = int(match.group(1)), int(match.group(2))
    if n > 10 or run > 10:
        return False
    return bool(GRADE_CONTEXT_RE.search(title[:match.start()]))


def grade_pairs_in(title):
    """The N/M strings in a title that read as grades, as "9/9" text."""
    return {f"{int(m.group(1))}/{int(m.group(2))}"
            for m in SERIAL_RE.finditer(title) if is_grade_pair(title, m)}


def extract_serial(title, aspects):
    """(card_number, print_run) from the title's N/M, else from item specifics.

    The title is read first: the "Card Number" specific is the checklist
    number (the 162 on a Bublik back), not the serial position, so it must
    never be paired with "Print Run" to fake a serial. Specifics are used
    only when they themselves carry an N/M."""
    for m in SERIAL_RE.finditer(title):
        if is_grade_pair(title, m):
            continue                        # a grade; the serial may still follow
        return int(m.group(1)), int(m.group(2))

    for key in ("Serial Number", "Serial Numbered", "Card Number", "card number"):
        value = str((aspects.get(key) or [""])[0])
        m = SERIAL_RE.search(value)
        if m:
            return int(m.group(1)), int(m.group(2))

    return None, None


PLAYER_ASPECTS = ("Player/Athlete", "Player", "Athlete", "Featured Person/Artist")


def get_player(aspects):
    for key in PLAYER_ASPECTS:
        values = aspects.get(key)
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    return "Unknown player"


def get_manufacturer_and_set(aspects):
    manu = None
    for key in ("Manufacturer", "Card Manufacturer"):
        if key in aspects:
            manu = aspects[key][0]
            break
    set_name = aspects.get("Set", [""])[0]
    return (manu or "").strip(), (set_name or "").strip()


def resolve_manufacturer(manufacturer, set_name="", title=""):
    """The allow-list name for a maker the specifics leave blank or spell as
    the product line. "Topps Chrome" in the Manufacturer field is Topps;
    a blank field on a card whose title says "2024 Topps Chrome Tennis" is
    Topps too -- the same evidence the set check reads a line off. A maker
    the allow-list does not know is returned as given, so it is still turned
    away by name."""
    manu = manufacturer.strip()
    lower = manu.lower()
    if lower in ALLOWED_MANUFACTURERS:
        return manu
    for key in ALLOWED_MANUFACTURERS:
        if lower.startswith(key + " "):
            return key.title() if key != "netpro" else "NetPro"
    if not lower:
        text = f"{set_name} {title}".lower()
        for key, name in (("netpro", "NetPro"), ("ace authentic", "Ace Authentic"),
                          ("panini", "Panini"), ("topps", "Topps")):
            if re.search(rf"\b{re.escape(key)}\b", text):
                return name
    return manu


def netpro_buyback_reading(image_urls):
    """Cached photo reading: does the card carry gold-foil buyback numbering?"""
    urls = [u for u in image_urls if u][:3]
    if not urls or not vision_available():
        return {}
    cache = _load_vision_cache()
    key = "buyback:" + urls[0]
    if key in cache:
        return cache[key]
    try:
        reading = ask_photo(urls, BUYBACK_PROMPT, {
            "type": "object",
            "properties": {"buyback": {"type": "string", "enum": ["yes", "no", "unclear"]}},
            "required": ["buyback"],
            "additionalProperties": False,
        })
    except Exception as exc:                                  # noqa: BLE001
        log.warning("NetPro buyback photo check failed for %s: %s", urls[0], exc)
        return {}
    reading["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache[key] = reading
    _save_vision_cache()
    return reading


def ace_authentic_check(title, manufacturer, aspects=None, images=None):
    """(ok, reason, caution) for a maker whose on-card autographs and
    circle-sticker numbering are fakes. ok=True with a caution means the
    listing gave no way to tell and no photo step was available."""
    aspects = aspects or {}
    manu_lower = manufacturer.lower()
    if not any(m in manu_lower for m in CHECKED_AUTOGRAPH_MAKERS):
        return True, "ok", ""
    text = f"{title} {' '.join(str(v) for vals in aspects.values() for v in vals)}"
    signed = str((aspects.get("Autographed") or [""])[0]).strip().lower() in ("yes", "true")
    signed = signed or bool(aspects.get("Signed By") or aspects.get("Autograph Authentication"))
    signed = signed or _has_word(AUTOGRAPH_WORDS, text.lower())
    auto_type = str((aspects.get("Autograph Format") or aspects.get("Autograph Type") or [""])[0]).lower()

    if signed and (ON_CARD_RE.search(text) or "on-card" in auto_type or "on card" in auto_type):
        return False, f"{manufacturer} on-card autograph: they could not produce one, so it is not genuine", ""
    if signed and (STICKER_AUTO_RE.search(text) or "sticker" in auto_type or "label" in auto_type):
        return True, "ok", ""                 # a sticker autograph is how they were made

    reading = ace_photo_reading(images or [])
    if reading:
        if reading.get("autograph") == "on_card":
            return False, f"{manufacturer} autograph is on the card itself (from the photo): not genuine", ""
        if reading.get("numbering_on_circle_sticker") == "yes":
            return False, f"{manufacturer} numbering sits on a round hologram sticker (from the photo): not genuine", ""
        if reading.get("autograph") in ("sticker", "none") and reading.get("numbering_on_circle_sticker") == "no":
            return True, "ok", ""
        return True, "ok", f"{manufacturer}: photo could not settle on-card vs sticker; check by eye"
    if signed:
        return True, "ok", f"{manufacturer} autograph: on-card or sticker not stated; check by eye"
    return True, "ok", f"{manufacturer}: check the numbering is stamped, not on a circle sticker"


ACE_PROMPT = (
    "These are a seller's photos of an Ace Authentic tennis trading card. Answer two things. "
    "1) The autograph: is the signature written directly on the card surface (on_card), on a "
    "sticker or clear label applied to the card (sticker), or is there no signature (none)? "
    "2) The numbering: is the serial number (such as 8/10) written or printed on a round "
    "holographic circle sticker rather than stamped into the card? Answer unclear when you "
    "cannot see enough to tell."
)


def ace_photo_reading(image_urls):
    """Cached photo reading for the Ace Authentic checks; {} without the photo step."""
    urls = [u for u in image_urls if u][:3]
    if not urls or not vision_available():
        return {}
    cache = _load_vision_cache()
    key = "ace:" + urls[0]
    if key in cache:
        return cache[key]
    try:
        reading = ask_photo(urls, ACE_PROMPT, {
            "type": "object",
            "properties": {
                "autograph": {"type": "string", "enum": ["on_card", "sticker", "none", "unclear"]},
                "numbering_on_circle_sticker": {"type": "string", "enum": ["yes", "no", "unclear"]},
            },
            "required": ["autograph", "numbering_on_circle_sticker"],
            "additionalProperties": False,
        })
    except Exception as exc:                                  # noqa: BLE001
        log.warning("Ace Authentic photo check failed for %s: %s", urls[0], exc)
        return {}
    reading["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache[key] = reading
    _save_vision_cache()
    return reading


def looks_custom(title, set_name=""):
    """The word that marks this as somebody's own card, or "" if there is none.

    Read off the title and the Set field only -- see CUSTOM_CARD_WORDS for why
    the other item specifics are left out of it."""
    text = f"{title} {set_name}".lower()
    return next((w for w in CUSTOM_CARD_WORDS
                 if re.search(rf"\b{re.escape(w)}\b", text)), "")


def is_licensed_and_allowed_brand(title, manufacturer, set_name, aspects=None):
    manu_lower = manufacturer.lower()
    title_lower = title.lower()
    set_lower = set_name.lower()
    aspects = aspects or {}

    if manu_lower in BLOCKED_MANUFACTURER_STRINGS:
        return False, f"blocked manufacturer string: {manufacturer!r}"

    # Before the allow-list and long before the serial: a custom card carries a
    # real maker's name and a 1/1, and neither of those tells you anything.
    custom = looks_custom(title, set_name)
    if custom:
        return False, f"custom or novelty card: says {custom!r}"

    if manu_lower not in ALLOWED_MANUFACTURERS:
        return False, f"manufacturer not in allow-list: {manufacturer!r}"

    required_set_keywords = ALLOWED_MANUFACTURERS[manu_lower]
    if required_set_keywords:
        def confirms(kw):
            return kw in set_lower or QUALIFIED_IN_TITLE.get(kw, kw) in title_lower
        if not any(confirms(kw) for kw in required_set_keywords):
            return False, f"set/title doesn't confirm {required_set_keywords}"

    haystack = f"{set_lower} {title_lower} " + " ".join(
        str(v).lower() for vals in aspects.values() for v in vals)

    for family, rule in BLOCKED_SETS.items():
        if family not in manu_lower:
            continue
        if any(re.search(rf"\b{re.escape(w)}\b", haystack) for w in rule.get("unless", [])):
            continue
        hit = next((w for w in rule["skip"] if re.search(rf"\b{re.escape(w)}\b", haystack)), None)
        if hit:
            return False, f"blocked set: {manufacturer} {hit}"

    return True, "ok"


def is_bookend_serial(card_number, print_run, max_print_run=None, inclusive=None):
    max_print_run = MAX_PRINT_RUN if max_print_run is None else max_print_run
    inclusive = PRINT_RUN_INCLUSIVE if inclusive is None else inclusive
    if card_number is None or print_run is None:
        return False
    if print_run >= max_print_run and not (inclusive and print_run == max_print_run):
        return False
    return card_number == 1 or card_number == print_run


TENNIS_ONLY_MAKERS = ("netpro", "ace authentic")

# Product lines that are only ever tennis. Topps Graphite and Topps Royalty are
# tennis sets, so their name is the sport evidence and a listing need not spell
# it out -- 16 of the 21 recorded matches that say "tennis" nowhere are in these
# two. Topps Chrome, Topps Now and Panini Instant are printed for every sport,
# so those do have to say so. Matched against the title and the Set specific.
# Topps Royalty is not here: 2025 Topps Royalty UFC exists, and one of its
# cards reached the spreadsheet on 17 Sep as a tennis match.
TENNIS_ONLY_SETS = ("topps graphite", "graphite royalty")


# Lines Topps and Panini print for every sport in volume, so the wide search
# has to say "tennis" to stay in bounds. NetPro and Ace Authentic only ever
# printed tennis and Topps Graphite is a tennis set, so their name alone is the
# search. Topps Royalty is searched bare too: it is mostly tennis, its tennis
# titles rarely say so (4 of the 5 Royalty bookends found on 17 Sep did not),
# and the UFC line it also has names its sport in the title, where
# settled_by_title() turns it away for no call. Never "card": all 17 bookends
# the two player scans found that day had been invisible to the wide scan,
# and 15 of them lacked that word.
MULTI_SPORT_LINES = ("Topps Chrome", "Topps Now", "Panini Instant")


def wide_query(brand_kw):
    return f"{brand_kw} tennis" if brand_kw in MULTI_SPORT_LINES else brand_kw


def name_variants(player):
    """The searches run for one player: the full name, then the surname alone,
    so a listing titled just "Federer" is still fetched. Not the first name
    alone: "Denis Topps Chrome" brought in every Denis in every sport -- 1,083
    of the 2,077 listings a Shapovalov scan paid for on 17 Sep were not his --
    and a card titled with a first name only is vanishingly rare. Single-word
    names give one search."""
    tokens = [t for t in player.split() if t]
    variants = [" ".join(tokens)]
    if len(tokens) > 1 and len(tokens[-1]) >= 3 and tokens[-1].lower() != variants[0].lower():
        variants.append(tokens[-1])
    return variants


def sport_named(aspects):
    """The sport the listing states, or "" when it states none. Recorded as
    found so the question can be settled from the spreadsheet later."""
    for key in ("Sport", "Sports"):
        values = [str(v).strip() for v in (aspects or {}).get(key) or [] if str(v).strip()]
        if values:
            return values[0]
    return ""


# Words that put a card in another sport when the title says them and not
# "tennis". Whole words, so "golf" cannot hit "Golfo".
OTHER_SPORT_WORDS = ("ufc", "mma", "baseball", "mlb", "basketball", "nba", "wnba",
                     "football", "nfl", "soccer", "hockey", "nhl", "golf", "pga",
                     "wrestling", "wwe", "nascar", "f1", "formula 1", "boxing",
                     "cricket", "rugby", "olympics")


def other_sport_in_title(title):
    """The other sport a title names, or "" -- and never when it says tennis
    too, since "tennis / golf lot" is not a verdict."""
    text = title.lower()
    if "tennis" in text:
        return ""
    for word in OTHER_SPORT_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text):
            return word.upper() if len(word) <= 4 else word
    return ""


def is_tennis_listing(title, aspects=None):
    """Something on the listing says tennis: the word, the Sport specific, a
    maker that only prints tennis cards, or a set that is only ever tennis."""
    aspects = aspects or {}
    text = title.lower()
    if "tennis" in text:
        return True
    if "tennis" in sport_named(aspects).lower():
        return True
    maker = " ".join(str(v) for k in ("Manufacturer", "Card Manufacturer", "Set") for v in aspects.get(k) or []).lower()
    return any(m in maker or m in text
               for m in TENNIS_ONLY_MAKERS + TENNIS_ONLY_SETS)


def matches_player(title, player, aspects=None):
    """True when the listing is about this player.

    The full name in the title or in the Player/Athlete specific is enough on
    its own. The surname alone counts only when the listing also says tennis
    somewhere -- a bare "Williams" could be any sport."""
    tokens = player.lower().split()
    if not tokens:
        return False
    title_lower = title.lower()
    if all(re.search(rf"\b{re.escape(tok)}\b", title_lower) for tok in tokens):
        return True
    for key in PLAYER_ASPECTS:
        for value in (aspects or {}).get(key) or []:
            v = str(value).lower()
            if all(tok in v for tok in tokens):
                return True
    # The surname alone counts when the listing also says tennis. The first
    # name alone never does: "2025 Topps Royalty UFC Benoit Saint Denis" was
    # recorded as Denis Shapovalov on 17 Sep that way.
    if len(tokens) > 1:
        part = tokens[-1]
        if len(part) >= 3 and re.search(rf"\b{re.escape(part)}\b", title_lower):
            return is_tennis_listing(title, aspects)
    return False


# ============================================================================
# Spreadsheet output
# ============================================================================

def load_or_create_sheet(path):
    if os.path.exists(path):
        wb = load_workbook(path)
        if SHEET_NAME in wb.sheetnames:
            ws = wb[SHEET_NAME]
            _extend_header(ws)
        else:
            ws = wb.create_sheet(SHEET_NAME)
            _write_header(ws)
        return wb, ws
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    _write_header(ws)
    return wb, ws


def _extend_header(ws):
    """Older spreadsheets predate some columns; label them so lookups by name work."""
    present = [str(c.value or "").strip() for c in ws[1]]
    for name in HEADERS:
        if name not in present:
            cell = ws.cell(row=1, column=len(present) + 1, value=name)
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="2E5B8A", end_color="2E5B8A", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            present.append(name)


def _write_header(ws):
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="2E5B8A", end_color="2E5B8A", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [18, 22, 46, 14, 24, 16, 55, 18, 40, 22, 20, 14, 14, 40, 12, 12, 12, 40, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def append_row(ws, player, manufacturer, set_name, title, card_number, print_run, price, link,
               image="", listed="", listing="", card_type="", grading="", images=None,
               parallel="", outfit="", colour_match="", caution="", sport=""):
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
        image,
        listed,
        listing,
        card_type,
        grading,
        " | ".join(images or []),
        parallel,
        outfit,
        colour_match,
        caution,
        sport,                    # what the listing said, "" when it said nothing
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

def write_json_atomically(path, data):
    """Write beside the file, then swap it in.

    Writing straight to the path truncates it first, so a kill in that moment
    -- the workflow's 60-minute timeout, a runner going away, Ctrl-C -- leaves
    half a document where the real one was. Writing a neighbour and replacing
    it means the old file survives intact instead. os.replace is atomic on
    Windows as well, which os.rename is not."""
    tmp = f"{path}.writing"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_state(path):
    """Listings already judged. Older string/list formats remain supported.

    A file that will not parse is treated as no memory at all rather than as a
    reason to stop. It used to raise, and since main() catches only EngineError
    that killed the run -- and every run after it, because nothing repaired the
    file. The unreadable copy is kept beside it, since a truncated file is
    mostly good data and the next save would otherwise overwrite it."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (ValueError, OSError) as exc:
        say(f"{os.path.basename(path)} could not be read ({exc}). Starting without it, "
            "so this scan will cost more eBay calls than usual.")
        try:
            os.replace(path, f"{path}.unreadable")
        except OSError:
            pass
        return {}
    if isinstance(data, list):
        return {i: "match" for i in data}
    return dict(data) if isinstance(data, dict) else {}


def save_state(path, seen):
    write_json_atomically(path, dict(sorted(seen.items())))


def state_verdict(entry):
    return entry.get("verdict") if isinstance(entry, dict) else entry


def rules_fingerprint(rules):
    """Stable identity for settings that can change a filtered verdict.

    The settings are resolved to what they actually mean before hashing, so a
    value sent explicitly fingerprints the same as the same value left out.
    The website always fills the print-run box, so pressing "Run a scan"
    without touching anything sends 500 where a scheduled run sends nothing --
    the same scan, and before this it hashed differently, which meant no
    page-started scan ever matched the cursor the scheduled one had left.
    """
    resolved = dict(rules)
    resolved["max_print_run"] = rules.get("max_print_run") or MAX_PRINT_RUN
    inclusive = rules.get("print_run_inclusive")
    resolved["print_run_inclusive"] = (PRINT_RUN_INCLUSIVE if inclusive is None
                                       else bool(inclusive))
    resolved["min_price"] = float(rules.get("min_price") or 0.0)
    ceiling = rules.get("max_price")
    resolved["max_price"] = None if ceiling is None else float(ceiling)
    serializable = {
        key: sorted(value) if isinstance(value, set) else value
        for key, value in resolved.items()
    }
    raw = json.dumps(serializable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def filtered_for_rules(entry, fingerprint):
    return (isinstance(entry, dict) and entry.get("verdict") == "filtered"
            and entry.get("rules") == fingerprint)


def cursor_high_water(entry, fingerprint):
    """The timestamp a search may safely resume from, or "" to walk the lot.

    A cursor means "everything older than this has already been judged" -- but
    that is only true of the rules in force when it was written. Widen the
    print-run ceiling, or ask for a card type that was turned down before, and
    every listing below the mark needs judging again. So the fingerprint is
    stored with the mark and the mark is trusted only while it matches.
    Re-walking costs search pages alone: seen_items.json still answers for
    every listing already judged, so no detail call is paid twice.
    """
    if isinstance(entry, dict) and entry.get("rules") == fingerprint:
        return entry.get("newest") or ""
    return ""                     # changed rules, or a cursor from an older format


def scan_cursor_key(player, brand_kw, min_price, max_price, listing_types, query=None):
    """One mark per exact search: the wide query of a set, or one of a
    player's searches. The search text is part of the key, so changing it
    stales the mark and the set walks in full once."""
    scope = {
        "player": player or "*", "brand": brand_kw,
        "query": query if query is not None else (wide_query(brand_kw) if player is None else ""),
        "minPrice": min_price, "maxPrice": max_price,
        "listingTypes": sorted(listing_types or []),
        "marketplace": MARKETPLACE_ID, "category": EBAY_CATEGORY_ID,
    }
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]


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

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(from_email, app_password)
            server.sendmail(from_email, to_email, msg.as_string())
        print(f"Digest sent to {to_email}.")
    except (smtplib.SMTPException, OSError) as exc:
        # This runs after the scan has already succeeded and saved. Letting it
        # out would exit non-zero, which on GitHub stops the steps that publish
        # and commit the results -- throwing away a good scan over an email.
        say(f"the digest email could not be sent ({exc}). The scan's results were "
            "saved and published as usual.")


# ============================================================================
# The board -- every card ever recorded, highest asking price first. Read from
# the spreadsheet so it outlives any one run. Shared by the web server and by
# the static export the scheduled run publishes.
# ============================================================================

# The page shows the whole record, filtered, not just the newest handful, so
# the board carries every recorded card. At roughly 780 bytes a card that is
# about 390KB at the ceiling; revisit if the spreadsheet ever approaches it.
BOARD_LIMIT = 500


# The set a recorded card belongs to, as the scan setup names it, decided from
# the same evidence the brand gate accepted it on. Keys are the gate's own
# keywords, so the two cannot disagree.
BRAND_FOR_KEYWORD = {"topps chrome": "Topps Chrome", "graphite": "Topps Graphite",
                     "royalty": "Topps Royalty", "topps now": "Topps Now"}


def brand_of(manufacturer, set_name, title=""):
    """Which of DEFAULT_BRAND_KEYWORDS a card is, or "" when none fits. The
    page filters the record by set with this; a card it cannot place is shown
    rather than hidden, so "" never loses anything."""
    manu, set_lower, title_lower = manufacturer.lower(), set_name.lower(), title.lower()
    if "netpro" in manu:
        return "NetPro"
    if "ace authentic" in manu:
        return "Ace Authentic"
    if "panini" in manu:
        return "Panini Instant"
    if "topps" in manu:
        for kw in ALLOWED_MANUFACTURERS["topps"]:
            if kw in set_lower or QUALIFIED_IN_TITLE.get(kw, kw) in title_lower:
                return BRAND_FOR_KEYWORD[kw]
    return ""


def build_board(xlsx_path, matches_path=None, limit=BOARD_LIMIT):
    if not os.path.exists(xlsx_path):
        return []
    images = {}
    if matches_path and os.path.exists(matches_path):
        try:
            with open(matches_path) as f:
                images = {m.get("link"): m.get("image", "") for m in json.load(f) if m.get("link")}
        except (json.JSONDecodeError, OSError):
            pass
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip() for h in next(rows, ())]
    col = {name: i for i, name in enumerate(header)}

    def cell(row, name):
        i = col.get(name)
        return "" if i is None or i >= len(row) or row[i] is None else str(row[i]).strip()

    cards = []
    for row in rows:
        link = cell(row, "eBay Item Link")
        if not link and not cell(row, "Card Description"):
            continue
        manufacturer, _, set_name = cell(row, "Manufacturer / Set").partition(" / ")
        # A rule added after a row was recorded still applies to it: the board
        # is what the page shows, and a custom card should not be on it. The
        # spreadsheet keeps the row, so nothing found is ever lost.
        if looks_custom(cell(row, "Card Description"), set_name):
            continue
        # A serial the reader now knows to be a grade pair -- recorded before
        # it could tell "Psa MINT 9/9" from a run of nine. Off the page, row kept.
        if cell(row, "Serial #") in grade_pairs_in(cell(row, "Card Description")):
            continue
        if other_sport_in_title(cell(row, "Card Description")):
            continue
        price = cell(row, "Price")
        number = re.search(r"\d[\d,]*(?:\.\d+)?", price)
        cards.append({
            "player": cell(row, "Player") or "Unknown player",
            "manufacturer": manufacturer,
            "set_name": set_name,
            "brand": brand_of(manufacturer, set_name, cell(row, "Card Description")),
            "title": cell(row, "Card Description"),
            "serial": cell(row, "Serial #"),
            "bookend": cell(row, "Bookend Type"),
            "price": price,
            "priceValue": float(number.group().replace(",", "")) if number else 0.0,
            "link": link,
            "image": cell(row, "Image") or images.get(link, ""),
            "found": cell(row, "Date Found (UTC)"),
            "listed": cell(row, "Listed (UTC)"),
            "listing": cell(row, "Listing Type"),
            "cardType": next((k for k, v in CARD_TYPES.items() if v == cell(row, "Card Type")), ""),
            "grading": cell(row, "Grading"),
            "images": [u.strip() for u in cell(row, "Extra Images").split("|") if u.strip()],
            "parallel": cell(row, "Parallel"),
            "outfit": cell(row, "Outfit Colour"),
            "colourMatch": cell(row, "Colour Match"),
            "caution": cell(row, "Caution"),
            "sport": cell(row, "Sport"),
        })
    wb.close()
    # newest listing first; rows from before the Listed column fall back to
    # when the scan found them (both are ISO-ish strings, so text order is time order)
    cards.sort(key=lambda c: (c["listed"] or "", c["found"] or ""), reverse=True)
    return cards[:limit]


def public_config():
    """What the web page needs to know about this configuration -- no secrets."""
    return {
        "scanAllPlayers": SCAN_ALL_PLAYERS,
        "maxResultsPerBrand": MAX_RESULTS_PER_BRAND,
        "players": PLAYERS,
        "brands": DEFAULT_BRAND_KEYWORDS,
        "maxPrintRun": MAX_PRINT_RUN,
        "printRunInclusive": PRINT_RUN_INCLUSIVE,
        "priceCurrency": PRICE_CURRENCY,
        "priceCeiling": PRICE_CEILING,
        "listingTypes": list(LISTING_TYPES),
        "cardTypes": CARD_TYPES,
        "conditions": CONDITIONS,
        "colourMatch": vision_available(),
        "visionModel": VISION_MODEL,
        "allowedManufacturers": [{"manufacturer": k, "requiresSetKeyword": v}
                                 for k, v in ALLOWED_MANUFACTURERS.items()],
        "blockedSets": [{"manufacturer": f, "skip": r["skip"], "unless": r.get("unless", [])}
                        for f, r in BLOCKED_SETS.items()],
        "checkedAutographs": list(CHECKED_AUTOGRAPH_MAKERS),
        "blockedManufacturers": sorted(BLOCKED_MANUFACTURER_STRINGS),
        "blockedSellers": sorted(BLOCKED_SELLERS),
        "customWords": list(CUSTOM_CARD_WORDS),
        "resultsPerQuery": RESULTS_PER_QUERY,
        "categoryId": EBAY_CATEGORY_ID,
        "marketplace": MARKETPLACE_ID,
        "spreadsheet": OUTPUT_XLSX,
    }


# ============================================================================
# Main
# ============================================================================

DEFAULT_BRAND_KEYWORDS = ["NetPro", "Panini Instant", "Topps Chrome",
                          "Topps Graphite", "Topps Royalty", "Topps Now",
                          "Ace Authentic"]


def aspects_of(detail):
    return {a["name"]: a["value"] if isinstance(a["value"], list) else [a["value"]]
            for a in detail.get("localizedAspects", [])}


def judge_listing(item, detail, player=None, rules=None):
    """Every rule in one place. Returns (verdict, reason, fields): verdict is
    'match', 'reject' (for a reason that cannot change, so the listing need
    never be fetched again) or 'filtered' (turned down by an adjustable
    filter: which player was asked for, ceiling, price, type, grading, listing
    type). fields carry what a match needs recorded."""
    rules = rules or {}
    title = item.get("title", "")
    aspects = aspects_of(detail)

    if player and not matches_player(title, player, aspects):
        # Not a permanent verdict: it says this card is not the player *this
        # search asked for*. Recorded against the player in the fingerprint, so
        # a later scan for somebody else, or for everyone, judges it afresh.
        return "filtered", f"not a {player} card", None

    manufacturer, set_name = get_manufacturer_and_set(aspects)
    manufacturer = resolve_manufacturer(manufacturer, set_name, title)
    photos = [(detail.get("image") or {}).get("imageUrl") or (item.get("image") or {}).get("imageUrl") or ""]
    photos += [img.get("imageUrl") for img in (detail.get("additionalImages") or []) if img.get("imageUrl")]
    ok, reason = is_licensed_and_allowed_brand(title, manufacturer, set_name, aspects)
    if not ok and reason.startswith("blocked set: NetPro"):
        # a base or glossy card can still be a buyback with gold-foil numbering
        if netpro_buyback_reading(photos).get("buyback") == "yes":
            ok, reason = True, "ok"
    if not ok:
        return "reject", reason, None
    ok, reason, caution = ace_authentic_check(title, manufacturer, aspects, photos)
    if not ok:
        return "reject", reason, None

    # The scan searches category 212, which is every sport, and with no player
    # named nothing else here checks what sport a card is. Turning these away
    # would cost far more than it saves: of the matches recorded so far, 21 say
    # "tennis" nowhere, and every one of them is a real tennis card. So this
    # marks rather than rejects -- a wrong card is one glance to dismiss, a
    # missed one is gone for good. Recording the sport is what will settle
    # whether this can ever become a rejection.
    sport = sport_named(aspects) or other_sport_in_title(title)
    if not is_tennis_listing(title, aspects):
        if sport:
            # The listing was asked and answered: this is somebody else's
            # sport. No tennis card is lost by believing it.
            return "reject", f"listing says {sport}, not tennis", None
        # Nothing on the listing says either way, and that is the common case:
        # of the matches recorded so far, 21 say "tennis" nowhere and every one
        # is a real tennis card. So this marks rather than rejects -- a wrong
        # card is one glance to dismiss, a missed one is gone for good.
        note = "nothing on the listing says what sport this is; check by eye"
        caution = f"{caution} \u00b7 {note}" if caution else note

    seller = (detail.get("seller", {}) or {}).get("username", "").lower()
    if seller in BLOCKED_SELLERS:
        return "reject", f"blocked seller: {seller}", None

    max_print_run = rules.get("max_print_run")
    card_number, print_run = extract_serial(title, aspects)
    if card_number is None or print_run is None:
        return "reject", "no serial number (N/M) in the title or specifics", None
    if card_number not in (1, print_run):
        return "reject", f"{card_number}/{print_run} is neither the first nor the last of its run", None
    if not is_bookend_serial(card_number, print_run, max_print_run, rules.get("print_run_inclusive")):
        return "filtered", (f"{card_number}/{print_run}: print run not under the ceiling of "
                            f"{max_print_run or MAX_PRINT_RUN}"), None

    card_type = classify_card(title, aspects)
    if rules.get("card_types") and card_type not in rules["card_types"]:
        return "filtered", f"card type {CARD_TYPES[card_type]} not wanted", None
    condition, grading = classify_grading(title, aspects)
    if rules.get("conditions") and condition not in rules["conditions"]:
        return "filtered", f"{condition} card not wanted", None

    item_options = set(detail.get("buyingOptions") or item.get("buyingOptions") or [])
    wanted = rules.get("wanted_options")
    if wanted and item_options and not (item_options & set(wanted)):
        return "filtered", f"listing type {listing_label(detail) or 'unknown'} not wanted", None

    price = detail.get("price", {})
    price_str = f"{price.get('value', '?')} {price.get('currency', '')}".strip()
    try:
        value = float(price.get("value"))
    except (TypeError, ValueError):
        value = None
    min_price = rules.get("min_price") or 0.0
    max_price = rules.get("max_price")
    if value is not None and (value < min_price or (max_price is not None and value > max_price)):
        return "filtered", f"price {price_str} outside {min_price:g}-{max_price if max_price is not None else 'open'}", None

    fields = {
        "player": player or get_player(aspects),
        "manufacturer": manufacturer,
        "set_name": set_name,
        "card_number": card_number,
        "print_run": print_run,
        "price": price_str,
        "link": detail.get("itemWebUrl", item.get("itemWebUrl", "")),
        "image": ((detail.get("image") or {}).get("imageUrl")
                  or (item.get("image") or {}).get("imageUrl") or ""),
        "images": [img.get("imageUrl") for img in (detail.get("additionalImages") or [])
                   if img.get("imageUrl")][:4],
        "listed": detail.get("itemCreationDate") or item.get("itemCreationDate") or "",
        "listing": listing_label(detail if detail.get("buyingOptions") else item),
        "bids": detail.get("bidCount"),
        "cardType": card_type,
        "grading": grading,
        "sport": sport,
        "caution": caution,
    }
    fields["parallel"], fields["outfit"], fields["colourMatch"] = colour_reading(
        title, aspects, fields["image"])
    return "match", "ok", fields


def fill_colour_matches(xlsx_path, limit=200):
    """Give older spreadsheet rows a colour reading. Returns rows updated."""
    if not os.path.exists(xlsx_path):
        return 0
    wb, ws = load_or_create_sheet(xlsx_path)
    header = [str(c.value or "").strip() for c in ws[1]]
    col = {name: i + 1 for i, name in enumerate(header)}
    need = ("Card Description", "Image", "Parallel", "Outfit Colour", "Colour Match")
    if any(n not in col for n in need):
        wb.close()
        return 0
    updated = 0
    for r in range(2, ws.max_row + 1):
        if updated >= limit:
            break
        if str(ws.cell(r, col["Colour Match"]).value or "").strip():
            continue
        title = str(ws.cell(r, col["Card Description"]).value or "")
        image = str(ws.cell(r, col["Image"]).value or "")
        parallel, outfit, verdict = colour_reading(title, {}, image)
        if verdict == "unknown" and not vision_available():
            continue                                    # nothing to write yet
        ws.cell(r, col["Parallel"], parallel)
        ws.cell(r, col["Outfit Colour"], outfit)
        ws.cell(r, col["Colour Match"], verdict)
        updated += 1
    if updated:
        wb.save(xlsx_path)
    wb.close()
    return updated


def probe(player, brand_kw, limit=200, max_print_run=None):
    """Dry run for one player and one set: every listing eBay returns, with
    the verdict the engine reaches and why. Writes nothing, remembers nothing."""
    token = get_ebay_token()
    seen = load_state(os.path.join(_BASE_DIR, STATE_FILE))
    rows = []
    errors = []
    for item in iter_listings(token, player, brand_kw, limit=limit, on_error=errors.append):
        item_id = item.get("itemId", "")
        title = item.get("title", "")
        if seen.get(item_id) == "match":
            rows.append(("recorded", "already in the spreadsheet from an earlier run", title,
                         item.get("itemWebUrl", "")))
            continue
        detail = get_item_detail(token, item_id)
        if not detail:
            rows.append(("error", "eBay would not return the item details", title,
                         item.get("itemWebUrl", "")))
            continue
        verdict, reason, f = judge_listing(item, detail, player, {"max_print_run": max_print_run})
        rows.append((verdict, reason if verdict != "match" else f"{f['card_number']}/{f['print_run']}",
                     title, item.get("itemWebUrl", "")))
    for message in errors:
        rows.append(("error", message, "(search page refused)", ""))
    return rows


def run_scan(players=None, brand_keywords=None, max_print_run=None,
             print_run_inclusive=None, on_event=None, should_stop=None,
             write_outputs=True, min_price=None, max_price=None, listing_types=None,
             card_types=None, conditions=None):
    """Run one scan pass; return (new_match_records, listings_checked).

    players / brand_keywords fall back to the config block at the top of this
    file. on_event(kind, payload) streams progress so a caller can show a live
    log, and should_stop() is polled between queries so a caller can cancel.
    Pass write_outputs=False to scan without touching the spreadsheet or the
    seen-items state file. min_price / max_price (in PRICE_CURRENCY) narrow
    the eBay query; a max at or above PRICE_CEILING means no upper limit.
    listing_types is a list of "buy_now" / "auction"; empty means both.
    card_types is a list of "base" / "patch" / "auto" / "patch_auto"; empty means all.
    conditions is a list of "graded" / "raw"; empty means both.
    """
    card_types = {t for t in (card_types or []) if t in CARD_TYPES}
    conditions = {c for c in (conditions or []) if c in CONDITIONS}
    listing_types = [t for t in (listing_types or []) if t in LISTING_TYPES]
    wanted_options = set(buying_options(listing_types))
    min_price = float(min_price) if min_price else 0.0
    max_price = float(max_price) if max_price and float(max_price) < PRICE_CEILING else None
    players = list(players) if players else []            # empty = every player
    brand_keywords = list(brand_keywords) if brand_keywords else list(DEFAULT_BRAND_KEYWORDS)
    targets = players or [None]
    rules = {
        "max_print_run": max_print_run, "print_run_inclusive": print_run_inclusive,
        "min_price": min_price, "max_price": max_price, "wanted_options": wanted_options,
        "card_types": card_types, "conditions": conditions,
    }

    def emit(kind, **payload):
        if on_event:
            on_event(kind, payload)

    xlsx_path = os.path.join(_BASE_DIR, OUTPUT_XLSX)
    state_path = os.path.join(_BASE_DIR, STATE_FILE)
    cursor_path = os.path.join(_BASE_DIR, SCAN_CURSOR_FILE)
    new_matches_path = os.path.join(_BASE_DIR, NEW_MATCHES_FILE)

    token = get_ebay_token()
    seen = load_state(state_path)
    cursors = load_state(cursor_path)
    wb, ws = load_or_create_sheet(xlsx_path)

    total_queries = len(targets) * len(brand_keywords)
    query_index = 0
    checked = 0
    known = 0
    judged = 0
    failed = 0
    fetched = 0                  # detail calls asked for
    settled = 0                  # turned away from the search result alone, no call
    reasons = collections.Counter()   # why listings were turned away, for the summary
    new_match_records = []
    cancelled = False

    emit("start", players=len(players) or "all", brands=len(brand_keywords),
         queries=total_queries, perBrand=MAX_RESULTS_PER_BRAND)

    # Whatever happens in here -- an eBay response the judge cannot read, the
    # workflow's 60-minute timeout, a runner going away -- the run keeps what it
    # has already earned. Before this it was all held in memory until the last
    # line, so a stumble threw away the matches found AND the record of every
    # listing judged, which the next scan then paid eBay to judge again.
    try:
        for player in targets:
            for brand_kw in brand_keywords:
                if should_stop and should_stop():
                    cancelled = True
                    break

                query_index += 1
                label = player or "All players"
                # The player belongs in the fingerprint: "not a Federer card" is a
                # verdict about the search, not about the card, so it must not
                # carry over to a scan that asked for somebody else or for everyone.
                rule_key = rules_fingerprint(dict(rules, player=player))
                emit("query", player=label, brand=brand_kw,
                     index=query_index, total=total_queries)

                # A mark says "everything older than this is already judged",
                # which is true only of the settings AND the judge that did the
                # judging: a judge version bump stales every mark, so listings
                # its rules could reverse are walked past again. Player scans
                # keep marks too, one per search, so a repeat costs a page per
                # set rather than a walk to the back.
                cursor_rules = rules_fingerprint(dict(rules, player=player, judge=JUDGE_VERSION))

                def key_for(query):
                    return scan_cursor_key(player, brand_kw, min_price, max_price,
                                           listing_types, query)

                def mark_for(query):
                    return (cursor_high_water(cursors.get(key_for(query)), cursor_rules)
                            if write_outputs else "")

                reached = {}                                  # query -> newest listing seen
                failed_before_query = failed

                def remember_cursor(query, newest):
                    if newest:
                        reached[query] = newest

                listings = iter_listings(
                    token, player, brand_kw, should_stop=should_stop,
                    min_price=min_price, max_price=max_price, listing_types=listing_types,
                    high_water=mark_for, on_complete=remember_cursor,
                    on_error=lambda message: emit("error", message=message),
                    on_page=lambda count, offset, total, query="": emit(
                        "results", player=label, brand=brand_kw, query=query,
                        count=count, offset=offset, total=total))

                # Listings are judged a windowful at a time so their details can be
                # fetched in batches, several batches at once, rather than one round
                # trip per listing. The window is still walked in the order eBay
                # returned it, so the live log reads exactly as it did before.
                for window in _windows(listings, DETAIL_WINDOW):
                    if should_stop and should_stop():
                        cancelled = True
                        break

                    triage = [(item, seen.get(item.get("itemId")))
                              for item in window if item.get("itemId")]
                    checked += len(triage)

                    def needs_judging(entry, title):
                        verdict = state_verdict(entry)
                        if verdict == "match":
                            return False
                        if verdict == "reject":
                            return not reject_stands(entry, title)
                        if verdict == "unavailable":
                            return True                       # tried, never settled
                        return not filtered_for_rules(entry, rule_key)

                    for item, earlier in triage:
                        if needs_judging(earlier, item.get("title", "")):
                            reason = settled_by_summary(item)
                            if reason:
                                seen[item["itemId"]] = rejected(reason)
                                settled += 1
                                reasons[reason.split(":")[0].split(",")[0]] += 1
                                emit("reject", title=item.get("title", ""), reason=reason)
                    triage = [(item, seen.get(item["itemId"])) for item, _ in triage]
                    to_fetch = [item["itemId"] for item, earlier in triage
                                if needs_judging(earlier, item.get("title", ""))]
                    fetched += len(to_fetch)
                    refused = set()
                    details = get_item_details(token, to_fetch, failures=refused)

                    for item, earlier in triage:
                        item_id = item["itemId"]
                        title = item.get("title", "")
                        earlier_verdict = state_verdict(earlier)
                        if earlier_verdict == "match":
                            # recorded on an earlier run: say so instead of vanishing
                            known += 1
                            emit("known", title=title, link=item.get("itemWebUrl", ""))
                            continue
                        if not needs_judging(earlier, title):
                            judged += 1             # turned down before for good; no call to eBay
                            continue

                        detail = details.get(item_id)
                        if not detail and item_id not in refused:
                            # ended, or taken down, between the search and now
                            reason = "eBay no longer serves this listing"
                            seen[item_id] = rejected(reason)
                            reasons[reason] += 1
                            emit("reject", title=title, reason=reason)
                            continue
                        if not detail:
                            tries = (int(earlier.get("tries") or 0) if isinstance(earlier, dict) else 0) + 1
                            if tries >= UNAVAILABLE_TRIES:
                                reason = f"eBay would not return the item details, {tries} runs running"
                                seen[item_id] = rejected(reason)
                                reasons[reason] += 1
                            else:
                                seen[item_id] = {"verdict": "unavailable", "tries": tries}
                                failed += 1                   # holds this set's mark back
                                reason = "eBay would not return the item details; will try again"
                            emit("reject", title=title, reason=reason)
                            continue

                        verdict, reason, f = judge_listing(item, detail, player, rules)
                        if verdict != "match":
                            log.info("REJECT (%s): %s", reason, title)
                            reasons[reason.split(":")[0]] += 1
                            emit("reject", title=title, reason=reason)
                            if verdict == "reject":
                                # the reason is kept, so "why was my card
                                # passed over?" can be answered from the file
                                seen[item_id] = rejected(reason)
                            else:
                                seen[item_id] = {"verdict": "filtered", "rules": rule_key,
                                                 "reason": reason}
                            continue

                        bookend_label = append_row(ws, f["player"], f["manufacturer"], f["set_name"], title,
                                                   f["card_number"], f["print_run"], f["price"], f["link"],
                                                   f["image"], f["listed"], f["listing"],
                                                   CARD_TYPES[f["cardType"]], f["grading"], f["images"],
                                                   f["parallel"], f["outfit"], f["colourMatch"], f["caution"],
                                                   f["sport"])
                        seen[item_id] = "match"
                        record = {
                            "player": f["player"],
                            "manufacturer": f["manufacturer"],
                            "set_name": f["set_name"],
                            "brand": brand_of(f["manufacturer"], f["set_name"], title),
                            "title": title,
                            "serial": f"{f['card_number']}/{f['print_run']}",
                            "bookend": bookend_label,
                            "price": f["price"],
                            "link": f["link"],
                            "image": f["image"],
                            "images": f["images"],
                            "listed": f["listed"],
                            "listing": f["listing"],
                            "bids": f["bids"],
                            "itemId": item_id,
                            "cardType": f["cardType"],
                            "grading": f["grading"],
                            "parallel": f["parallel"],
                            "outfit": f["outfit"],
                            "colourMatch": f["colourMatch"],
                            "sport": f["sport"],
                            "caution": f["caution"],
                            "found": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                        }
                        new_match_records.append(record)
                        log.info("MATCH: %s | %s | %s/%s | %s", f["player"], title, f["card_number"], f["print_run"], f["link"])
                        emit("match", **record)

                # Retry a query next run if even one listing detail was missing;
                # otherwise persist its newest successfully processed timestamp.
                if write_outputs and not cancelled and failed == failed_before_query:
                    for query, newest in reached.items():
                        cursors[key_for(query)] = {"newest": newest, "rules": cursor_rules}

            if cancelled:
                break
    finally:
        if write_outputs:
            save_outputs(wb, xlsx_path, seen, state_path, cursors, cursor_path,
                         new_match_records, new_matches_path)

    emit("done", checked=checked, matches=len(new_match_records), known=known,
         judged=judged, failed=failed, cancelled=cancelled, fetched=fetched,
         settled=settled, reasons=reasons.most_common(8))
    return new_match_records, checked


def _env_list(name):
    """A comma-separated workflow input as a list; empty means "no preference"."""
    return [part.strip() for part in (os.environ.get(name) or "").split(",") if part.strip()]


def _env_number(name):
    """A numeric workflow input, or None when it is unset or not a number."""
    try:
        return float((os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        return None


def scan_options_from_env():
    """Settings for one run, as the website sends them when it starts a scan on
    GitHub. Everything is optional: an unset input leaves the engine default,
    so the scheduled scan behaves exactly as it always has."""
    players = _env_list("SCAN_PLAYERS")
    brands = [b for b in _env_list("SCAN_BRANDS") if b in DEFAULT_BRAND_KEYWORDS]
    inclusive = (os.environ.get("SCAN_PRINT_RUN_INCLUSIVE") or "").strip().lower()
    return {
        "players": players or (None if SCAN_ALL_PLAYERS else PLAYERS),
        "brand_keywords": brands or list(DEFAULT_BRAND_KEYWORDS),
        "max_print_run": int(_env_number("SCAN_MAX_PRINT_RUN") or 0) or None,
        "print_run_inclusive": True if inclusive in ("true", "1", "yes") else None,
        "min_price": _env_number("SCAN_MIN_PRICE"),
        "max_price": _env_number("SCAN_MAX_PRICE"),
        "listing_types": [t for t in _env_list("SCAN_LISTING_TYPES") if t in LISTING_TYPES],
        "card_types": [t for t in _env_list("SCAN_CARD_TYPES") if t in CARD_TYPES],
        "conditions": [c for c in _env_list("SCAN_CONDITIONS") if c in CONDITIONS],
    }


def main():
    options = scan_options_from_env()
    brand_keywords = options["brand_keywords"]
    players = options["players"]

    # A refused search used to vanish here: run_scan hands it to on_event,
    # main() passed none, and the only other copy went to engine_run.log,
    # which the hosted runner throws away. Runs 32 to 35 on 17 Sep each
    # checked 0 listings with the day's allowance used up, and every one went
    # green -- indistinguishable from a quiet day. Now each refusal is said out
    # loud, and a run that could check nothing at all fails, so it shows red.
    refusals = []
    summary = {}

    def on_event(kind, payload):
        if kind == "error":
            refusals.append(payload.get("message", ""))
            say(payload.get("message", ""))
        elif kind == "done":
            summary.update(payload)

    try:
        new_match_records, checked = run_scan(**options, on_event=on_event)
    except EngineError as e:
        sys.exit(f"ERROR: {e}")

    if refusals and not checked:
        sys.exit(f"ERROR: eBay refused every search ({len(refusals)} of them) and not one "
                 f"listing was checked, so this run found nothing because it could not look. "
                 f"First refusal: {refusals[0]}")
    if refusals:
        say(f"{len(refusals)} search(es) were refused, so this run covered less than usual; "
            "see the warnings above.")

    scope = "all players" if not players else f"{len(players)} players"
    print(f"Checked {checked} listings across {scope} x {len(brand_keywords)} sets.")
    if summary:
        # where the calls went, so the next saving can be decided on numbers
        print(f"Detail calls: {summary.get('fetched', 0)}; settled from the search result "
              f"with no call: {summary.get('settled', 0)}; already judged: {summary.get('judged', 0)}; "
              f"already recorded: {summary.get('known', 0)}.")
        for reason, n in summary.get("reasons") or []:
            print(f"  turned away: {n:>5}  {reason}")
        if summary.get("failed"):
            say(f"eBay refused {summary['failed']} detail call(s); those listings are tried "
                "again next run, and the mark of each set they sit in was held back")
    print(f"Added {len(new_match_records)} new qualifying listing(s) to {OUTPUT_XLSX}.")
    if not new_match_records:
        print("No new matches this run -- that's normal, keep it scheduled and it'll catch new listings as they post.")

    send_digest_email(new_match_records)


if __name__ == "__main__":
    if "--colour-match" in sys.argv:
        n = fill_colour_matches(os.path.join(_BASE_DIR, OUTPUT_XLSX))
        print(f"colour readings added to {n} row(s)" + ("" if vision_available()
              else " (set ANTHROPIC_API_KEY to read outfit colours from photos)"))
    else:
        main()
