"""Emails to the people with accounts: a mail when a scan finds a card that
fits a saved search, and the daily board every morning.

Accounts live in Supabase. This runs on the GitHub runner after a scan, with
the service key (SUPABASE_SERVICE_KEY, a repository secret that never reaches
the page) to read every person's saved searches and email switches. Mail
goes out through Resend when EMAIL_API_KEY and EMAIL_FROM are set, else
through the Gmail app password the digest already uses, else nowhere -- with
a line saying so. Nothing here can fail a run: every entry point catches its
own errors and prints them.

The matcher mirrors passesFilters in web/assets/app.js field for field. A
saved search is the page's filter setup as currentFilters() captured it, so
a card the page would show under those filters is a card this emails about.
"""

import html
import json
import os
import re
import smtplib
import unicodedata
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

LISTING_WORDS = {"buy_now": "Buy It Now", "auction": "Auction"}
AUTO_RE = re.compile(r"\b(auto|autos|autograph|autographed|autographs|signed|signature|signatures)\b", re.I)
PATCH_RE = re.compile(r"\b(patch|patches|relic|relics|jersey|jerseys|memorabilia|swatch|swatches|game[ -]used|"
                      r"match[ -]used|match[ -]worn|player[ -]worn|event[ -]worn|tournament[ -]worn|materials?|shirt)\b", re.I)
GRADE_RE = re.compile(r"\b(psa|bgs|sgc|cgc|csg|hga|isa|gma|ksa|beckett|tag)\b", re.I)
GRADED_WORDS_RE = re.compile(r"\b(graded|slab|slabbed|gem\s*(?:mint|mt)\s*10)\b", re.I)


# ---------------------------------------------------------------- the matcher

def words(s):
    s = unicodedata.normalize("NFD", str(s or "")).lower()
    s = "".join(c for c in s if not unicodedata.combining(c))
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def same_player(wanted, recorded):
    a, b = words(wanted), words(recorded)
    if not a or not b:
        return False
    return all(w in b for w in a) or all(w in a for w in b)


def price_value(text):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text or ""))
    return float(m.group().replace(",", "")) if m else 0.0


def card_type(card):
    if card.get("cardType") in ("base", "patch", "auto", "patch_auto"):
        return card["cardType"]
    t = str(card.get("title", ""))
    auto, patch = bool(AUTO_RE.search(t)), bool(PATCH_RE.search(t))
    return "patch_auto" if auto and patch else "auto" if auto else "patch" if patch else "base"


def is_raw(card):
    if card.get("grading"):
        return card["grading"] == "Raw"
    t = str(card.get("title", ""))
    return not (GRADE_RE.search(t) or GRADED_WORDS_RE.search(t))


def bookend_kind(card):
    label = str(card.get("bookend", "")).lower()
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", str(card.get("serial", "")))
    n, run = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    if "true 1/1" in label or (n == 1 and run == 1):
        return "one"
    if label.startswith("001 of") or n == 1:
        return "first"
    if label.startswith("last of") or (m and n == run):
        return "last"
    return ""


def print_run_of(card):
    m = re.match(r"^\s*\d+\s*/\s*(\d+)\s*$", str(card.get("serial", "")))
    return int(m.group(1)) if m else None


def listed_at(card):
    raw = str(card.get("listed") or card.get("found") or "").replace(" UTC", "Z").replace(" ", "T")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def fold(text):
    """Lower-case with accents removed, punctuation kept -- the page's fold()."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def in_query(f, card):
    """Every word typed must appear in the card; a typed serial is a pair."""
    q = str(f.get("query") or "").strip()
    if not q:
        return True
    hay = fold(" ".join(str(card.get(k, "")) for k in ("title", "player", "set_name", "brand", "parallel", "serial")))
    for t in fold(q).split():
        pair = re.match(r"^0*(\d+)/0*(\d+)$", t)
        if pair:
            if not re.search(rf"(^|[^\d])0*{pair.group(1)}\s*/\s*0*{pair.group(2)}(?!\d)", hay):
                return False
        elif t not in hay:
            return False
    return True


def card_fits(filters, card, now=None):
    """Whether the page would show this card under these filters."""
    f = filters or {}
    now = now or datetime.now(timezone.utc)
    # a new find is for sale; a search saved while looking at sold or ended
    # cards is not asking to hear about new ones
    if f.get("avail", "sale") in ("sold", "ended"):
        return False
    price = f.get("price") or {}
    v = price_value(card.get("price"))
    if v:
        if v < (price.get("min") or 0):
            return False
        if price.get("max") is not None and v > price["max"]:
            return False
    listing = f.get("listing", "all")
    if listing != "all" and card.get("listing") and LISTING_WORDS.get(listing, "") not in card["listing"]:
        return False
    bookend = f.get("bookend", "all")
    if bookend != "all":
        kind = bookend_kind(card)
        if kind and kind != "one" and kind != bookend:
            return False
    window = f.get("window", "any")
    if window != "any":
        d = listed_at(card)
        if d and (now - d) > timedelta(days=float(window)):
            return False
    if f.get("cardtype", "all") != "all" and card_type(card) != f["cardtype"]:
        return False
    cond = f.get("condition", "all")
    if cond != "all" and (is_raw(card) != (cond == "raw")):
        return False
    if f.get("colourmatch", "all") != "all" and card.get("colourMatch") != "yes":
        return False
    if not f.get("allPlayers", True) and f.get("players"):
        if not any(same_player(n, card.get("player", "")) for n in f["players"]):
            return False
    brands = f.get("brands") or []
    if brands and card.get("brand") and not any(b.lower() == card["brand"].lower() for b in brands):
        return False
    run = print_run_of(card)
    ceiling = int(float(f.get("ceiling") or 0)) or 500
    if run is not None and not (run < ceiling or (f.get("inclusive") and run == ceiling)):
        return False
    if f.get("caution", "all") == "hide" and card.get("caution"):
        return False
    if f.get("caution", "all") == "only" and not card.get("caution"):
        return False
    return in_query(f, card)


# ---------------------------------------------------------------- Supabase, with the service key

def supabase():
    url, key = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SERVICE_KEY", "")
    return (url, key) if url and key else (None, None)


def rows(path, method="GET", body=None):
    url, key = supabase()
    r = requests.request(method, f"{url}/rest/v1/{path}", json=body, timeout=30,
                         headers={"apikey": key, "Authorization": f"Bearer {key}",
                                  "Content-Type": "application/json", "Prefer": "return=representation"})
    r.raise_for_status()
    return r.json() if r.text else []


def subscribers():
    """{user_id: {email, daily_email, alerts, searches: [...]}} for every account."""
    people = {p["user_id"]: dict(p, searches=[]) for p in rows("profiles?select=user_id,email,daily_email,alerts")}
    for s in rows("saved_searches?select=id,user_id,name,filters,alerts&alerts=is.true"):
        if s["user_id"] in people:
            people[s["user_id"]]["searches"].append(s)
    return people


# ---------------------------------------------------------------- sending

def send_email(to, subject, html_body):
    """Resend when its key is set, else the Gmail app password the digest uses.
    Returns how it went, as a sentence, and never raises."""
    api_key, sender = os.environ.get("EMAIL_API_KEY", ""), os.environ.get("EMAIL_FROM", "")
    if api_key and sender:
        try:
            r = requests.post("https://api.resend.com/emails", timeout=30,
                              headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                              json={"from": sender, "to": [to], "subject": subject, "html": html_body})
            r.raise_for_status()
            return f"sent to {to} via Resend"
        except requests.RequestException as exc:
            return f"Resend refused the mail to {to}: {exc}"
    gmail, pw = os.environ.get("DIGEST_FROM_EMAIL", ""), os.environ.get("DIGEST_FROM_APP_PASSWORD", "")
    if gmail and pw:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, gmail, to
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(gmail, pw)
                server.sendmail(gmail, to, msg.as_string())
            return f"sent to {to} via Gmail"
        except (smtplib.SMTPException, OSError) as exc:
            return f"Gmail refused the mail to {to}: {exc}"
    return f"no email sender configured (EMAIL_API_KEY+EMAIL_FROM or DIGEST_FROM_EMAIL); nothing sent to {to}"


# ---------------------------------------------------------------- the emails themselves

def esc(s):
    return html.escape(str(s or ""), quote=True)


def card_block(card, site):
    """One card: photo on the left, the facts on the right, a link to the
    listing -- the shape of a Card Ladder daily."""
    move = ""
    if card.get("foundPrice") and card.get("foundPrice") != card.get("price"):
        move = f'<div style="font-size:12px;color:#64749A">Was {esc(card["foundPrice"])} when found</div>'
    end = ""
    if card.get("endDate"):
        end = f'<div style="font-size:12px;color:#64749A">Ends {esc(str(card["endDate"])[:16].replace("T", " "))} UTC</div>'
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px">
  <tr>
    <td width="112" valign="top" style="padding-right:16px">
      <a href="{esc(card.get("link"))}"><img src="{esc(card.get("image"))}" width="112" alt=""
        style="display:block;width:112px;border-radius:6px;border:1px solid #D3DCEB"></a>
    </td>
    <td valign="top" style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0A1B3D;font-size:14px;line-height:1.5">
      <div style="font-weight:700">{esc(card.get("player") or "Player not named")}
        <span style="color:#1F6F43;margin-left:6px">{esc(card.get("serial"))}</span></div>
      <div style="color:#64749A;font-size:12px">{esc(card.get("brand") or card.get("manufacturer"))} &middot; {esc(card.get("bookend"))}</div>
      <div style="margin:4px 0">{esc(card.get("title"))}</div>
      <div><strong>{esc(card.get("price"))}</strong> &middot; {esc(card.get("listing"))}</div>
      {move}{end}
      <div style="margin-top:6px"><a href="{esc(card.get("link"))}" style="color:#1F3A5F">View on eBay &rarr;</a>
        &nbsp; <a href="{esc(site)}" style="color:#64749A">On the board</a></div>
    </td>
  </tr>
</table>"""


def wrap(title, intro, body, site):
    return f"""<!doctype html><html><body style="margin:0;background:#F3F6FB;padding:24px 12px">
<div style="max-width:620px;margin:0 auto;background:#FFFFFF;border:1px solid #D3DCEB;border-radius:10px;overflow:hidden">
  <div style="background:#0A1B3D;color:#FFFFFF;padding:18px 24px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
    <div style="font-size:11px;letter-spacing:.2em;text-transform:uppercase;opacity:.75">Tennis Card Engine</div>
    <div style="font-size:22px;font-weight:700;margin-top:4px">{esc(title)}</div>
  </div>
  <div style="padding:22px 24px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#0A1B3D">
    <p style="margin:0 0 18px;font-size:14px;line-height:1.6;color:#334">{intro}</p>
    {body}
    <p style="margin:22px 0 0"><a href="{esc(site)}" style="display:inline-block;background:#0A1B3D;color:#FFFFFF;text-decoration:none;
      padding:12px 22px;border-radius:6px;font-weight:700;font-size:13px;letter-spacing:.08em">VIEW THE BOARD</a></p>
  </div>
  <div style="padding:14px 24px 20px;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:12px;color:#64749A;border-top:1px solid #D3DCEB">
    You get this because you turned it on in your account. Change or stop it in the account panel on
    <a href="{esc(site)}" style="color:#64749A">the site</a>. Prices come from eBay and move; always check the listing before buying.
  </div>
</div></body></html>"""


def alert_email(person, hits, site):
    """hits: [(search, [cards])]."""
    sections = []
    for search, cards in hits:
        sections.append(f'<h3 style="font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#64749A;margin:18px 0 10px">'
                        f'Fits &ldquo;{esc(search["name"])}&rdquo;</h3>' + "".join(card_block(c, site) for c in cards))
    n = sum(len(c) for _, c in hits)
    title = f"{n} new card{'' if n == 1 else 's'} fit your saved search{'' if len(hits) == 1 else 'es'}"
    intro = "A scan just recorded these. The first of a print run or the last, under 500, from a licensed maker."
    return title, wrap(title, intro, "".join(sections), site)


def recap_html(stats):
    cell = lambda label, value: (f'<td style="padding:10px 12px;border:1px solid #D3DCEB;border-radius:6px">'
                                 f'<div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#64749A">{esc(label)}</div>'
                                 f'<div style="font-size:22px;font-weight:700;color:#1F6F43">{esc(value)}</div></td>')
    return ('<h3 style="font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#64749A;margin:22px 0 10px">Daily board recap</h3>'
            '<table role="presentation" cellpadding="0" cellspacing="6" width="100%"><tr>'
            + cell("Found yesterday", stats["found"]) + cell("For sale now", stats["for_sale"])
            + cell("Sold yesterday", stats["sold"]) + cell("Sold for", f"${stats['sold_value']:,.0f}") + "</tr></table>")


def daily_email(found, stats, site, limit=12):
    title = f"{len(found)} new card{'' if len(found) == 1 else 's'} on the board"
    intro = "What the engine found in the last day, newest first, and how the board stands this morning."
    body = "".join(card_block(c, site) for c in found[:limit])
    if len(found) > limit:
        body += f'<p style="font-size:13px;color:#64749A">And {len(found) - limit} more on the board.</p>'
    if not found:
        body = '<p style="font-size:13px;color:#64749A">Nothing new in the last day. The board below is as it stands.</p>'
    return title, wrap(title, intro, body + recap_html(stats), site)


# ---------------------------------------------------------------- entry points

def send_alerts(new_matches, site, now=None):
    """After a scan: one email per person whose saved searches a new find fits.
    Returns the lines it printed."""
    out = []
    try:
        if not new_matches:
            return ["alerts: no new finds this run"]
        if not supabase()[0]:
            return ["alerts: SUPABASE_URL / SUPABASE_SERVICE_KEY not set; no saved searches read"]
        people = subscribers()
        sent = 0
        for person in people.values():
            if not person.get("alerts", True) or not person["searches"]:
                continue
            hits = []
            for search in person["searches"]:
                cards = [c for c in new_matches if card_fits(search.get("filters") or {}, c, now)]
                if cards:
                    hits.append((search, cards))
            if not hits:
                continue
            subject, body = alert_email(person, hits, site)
            out.append("alerts: " + send_email(person["email"], subject, body))
            sent += 1
            for search, _ in hits:
                try:
                    rows(f"saved_searches?id=eq.{search['id']}", "PATCH",
                         {"last_sent": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                except requests.RequestException:
                    pass
        out.append(f"alerts: {len(new_matches)} new find(s) checked against {sum(len(p['searches']) for p in people.values())} "
                   f"saved search(es) across {len(people)} account(s); {sent} email(s)")
    except Exception as exc:                                  # noqa: BLE001 -- never fail a run over an email
        out.append(f"alerts: skipped ({exc})")
    for line in out:
        print(line)
    return out


def found_since(board, archive, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    recent = []
    for c in list(board) + list(archive):
        d = None
        try:
            d = datetime.strptime(str(c.get("found", "")), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        if d and now - d <= timedelta(hours=hours):
            recent.append((d, c))
    return [c for _, c in sorted(recent, key=lambda t: t[0], reverse=True)]


def daily_stats(board, archive, statuses, hours=24, now=None):
    now = now or datetime.now(timezone.utc)
    sold, value = 0, 0.0
    for c in archive:
        st = (statuses or {}).get(c.get("itemId") or "", {}) if isinstance(statuses, dict) else {}
        if not st:
            m = re.search(r"/itm/(?:[^/?#]+/)?(\d{9,})", str(c.get("link", "")))
            st = (statuses or {}).get(f"v1|{m.group(1)}|0", {}) if m else {}
        if st.get("status") != "sold":
            continue
        try:
            end = datetime.fromisoformat(str(st.get("endDate", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - end <= timedelta(hours=hours):
            sold += 1
            value += price_value(st.get("price") or c.get("price"))
    return {"found": len(found_since(board, archive, hours, now)), "for_sale": len(board), "sold": sold, "sold_value": value}


def send_daily(board, archive, statuses, site, now=None):
    """Every morning, after the scheduled run: the last day's finds and the
    recap, to everyone who turned the daily board on."""
    out = []
    try:
        if not supabase()[0]:
            return ["daily: SUPABASE_URL / SUPABASE_SERVICE_KEY not set; nobody to send to"]
        people = [p for p in subscribers().values() if p.get("daily_email")]
        if not people:
            return ["daily: nobody has the daily board turned on"]
        found = found_since(board, archive, 24, now)
        stats = daily_stats(board, archive, statuses, 24, now)
        subject, body = daily_email(found, stats, site)
        for person in people:
            out.append("daily: " + send_email(person["email"], subject, body))
        out.append(f"daily: {len(found)} card(s) found in the last day, {stats['sold']} sold; {len(people)} recipient(s)")
    except Exception as exc:                                  # noqa: BLE001
        out.append(f"daily: skipped ({exc})")
    for line in out:
        print(line)
    return out
