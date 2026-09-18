# Tennis Card Engine

Scans eBay for **bookend** tennis cards from licensed makers, records every
one in a spreadsheet, and shows them on a website.

A card is a bookend when it is serial-numbered **#1 of its run, or the last of
it** — `1/25`, `25/25`, `1/1` — and the run is **under 500**. Those are the two
numbers collectors chase, and they are hard to search for on eBay because the
serial lives in the title in a hundred different spellings, in eBay's item
specifics, or only in the photograph.

The engine reads all three, judges every listing once, and never pays eBay to
judge the same listing twice.

---

## What it looks for

**Seven sets, from four makers.** NetPro, Ace Authentic, Topps Chrome, Topps
Graphite, Topps Royalty, Topps Now and Panini Instant. The maker is checked
against an allow-list (`ALLOWED_MANUFACTURERS`), read from eBay's Manufacturer
field and falling back to the title when that field is blank or just names the
line.

**Every player, by default.** Fifteen names are built in for narrowing a scan
by hand, but a scheduled run names nobody: it searches each set and reads the
player off whatever it finds. That is what keeps the spreadsheet comprehensive.

**Not customs, not other sports, not graders' numbers.** A hand-made "1/1", a
Topps Royalty UFC card, a `PSA 9/9` slab grade and a `#CK-1 /500` card number
all read like bookends and are all turned away. Each of those rules was written
against a card that got through, and each is documented in `CLAUDE.md`.

---

## Where it runs

**On GitHub, which is the main setup.** `.github/workflows/scan.yml` runs the
engine once a day at 23:17 UTC (7:17am Singapore), exports the results and
commits them to `results/`. `.github/workflows/pages.yml` then publishes `web/`
plus `results/` to GitHub Pages, so the site is a static page anyone can open
from a phone with nothing switched on at home.

**On your own machine,** where `server.py` serves the same site with a live
scan behind it. Same page, same filters; locally it streams the engine's own
progress events, hosted it follows the GitHub run.

---

## Quick start on a PC

```
py check.py                       # says exactly what, if anything, to fix
pip install -r requirements.txt
```

Copy `.env.example` to `.env` beside `server.py` and fill in your own free eBay
keys from <https://developer.ebay.com/my/keys> (the **Production** keyset, not
the sandbox one):

```
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

Then:

```
py server.py
```

and open <http://127.0.0.1:8765>.

`.env` is gitignored and the keys are read by the server process only — they
never reach the browser. On macOS or Linux use `python3` in place of `py`.

### Other commands

| | |
|---|---|
| `py tennis_card_engine.py` | one scan, no website; settings come from `SCAN_*` environment variables |
| `py tennis_card_engine.py --colour-match` | fill in outfit colours from listing photos (needs `ANTHROPIC_API_KEY`) |
| `py export_static.py results` | rebuild `results/` for the published site |
| `py ebay_usage.py` | eBay's own view of how many calls today has spent |
| `py test_engine.py` | the test suite — **run it before pushing engine changes** |

---

## The website

One main page and two pages of its own, reached from the top bar.

- **The board** — every card found, newest first, with its serial, price and a
  link to the listing. Star one to keep it.
- **Scan setup** — players, sets, print-run ceiling, card type, graded or raw,
  price range, listing type. It decides what a scan records **and** narrows
  what the page shows, the moment you change it, with no scan needed.
- **Matches** — two sections, because there are two questions: *New this scan*
  (usually empty, and that is correct) and *Everything found so far* (the whole
  record, filtered, paged).
- **Saved cards** (`#saved`) — the cards you starred, each showing whether the
  listing is still live or has sold.
- **How it works** (`#method`) — the method and process reference: what
  qualifies, what is turned away, and why.

A scan started from the page runs on the server or on GitHub, never in the
browser, so it survives you closing the tab.

---

## Running a scan

**The daily scheduled run sends no settings at all**, so it covers all players,
all seven sets, every card type, graded and raw, both listing types, any price.
That is the run that keeps the record complete.

**"Run a scan" on the page sends whatever the scan setup shows.** Every
`workflow_dispatch` input is optional; on the hosted site the button needs a
GitHub fine-grained token (Actions: read and write, this repo only), pasted
once per device and kept in that browser, never in the repo. Without a token
the button opens the Actions page instead.

**A scan that adds nothing is normally working.** Once a listing has been
judged it is never a new find again, so a scan an hour after the last one
correctly adds nothing. Judge a run by its "Checked N listings" line, not by
whether anything was new.

---

## The eBay allowance, and the files that protect it

eBay serves about **5,000 Browse calls a day**, shared by the scheduled run,
phone-started scans and anything you run at home. A never-seen listing that the
title cannot settle costs one call, so the allowance is the real constraint on
this project and most of the engine's design exists to spend less of it.

Three files carry that protection between runs, all restored from `results/` by
the workflow before the engine starts:

| file | what it remembers |
|---|---|
| `seen_items.json` | what every listing was judged, and why — a judged listing is never fetched again |
| `scan_cursors.json` | the newest listing each exact search has reached, with a fingerprint of the rules that reached it |
| `ebay_api_usage.json` | a local daily counter enforcing a 4,500-call ceiling, below eBay's own |

**Never delete them.** Deleting any one makes the next scan pay eBay again for
work already done.

Beyond that: a title that says nothing about numbering is turned away for no
call at all (audited every run against live listings, so the rule cannot lose
cards quietly), statuses are refreshed once a day rather than once an hour, and
a run keeps everything it earned even when it fails partway.

---

## What's in the repo

| | |
|---|---|
| `tennis_card_engine.py` | the engine: search, judge, record |
| `server.py` | the local web server and scan runner (standard library only) |
| `export_static.py` | turns the spreadsheet and state into `results/` for the published site |
| `check.py` | pre-flight check of the machine |
| `ebay_usage.py` | eBay's own usage figures |
| `test_engine.py` | the test suite |
| `web/` | the site: `index.html`, `assets/app.js`, `assets/styles.css` |
| `results/` | the published board, statuses and state files |
| `samples/` | photographs of real and fake cards used as test cases |
| `tennis_cards_verified.xlsx` | the spreadsheet (gitignored locally; published through `results/`) |

### Further reading

- **`CLAUDE.md`** — the project's real memory: every decision, why it was made,
  what it was measured against, and what must not be undone. Read this before
  changing engine behaviour.
- **`WEBAPP.md`** — the local web app in detail.
- **`IPAD_SETUP.md`** — running the whole thing from an iPad, with GitHub doing
  the work.
- **`samples/README.md`** — the sample cards and what each one proves.
- **`web/README-hero.md`** — the hero clip, its credit, and how to swap it.

---

## Limitations

Two things no rule can fix from a listing alone, kept here so they are not
chased as bugs:

- **A title that states the wrong serial with nothing to contradict it.** A
  seller who writes `#01/77` on a card stamped `21/77` agrees with every
  reading the engine has. Only the photograph disagrees, and reading every
  photograph costs an Anthropic call per match.
- **A card is only as findable as its title.** eBay matches whole words and
  serves the newest 1,200 listings per query, so a card whose title names
  neither its set nor its player in the words a search uses is out of reach of
  a wide scan, and older listings surface only through a player scan.

---

## Two rules

- **Never commit `.env`, and never put a key or token in the code.** The repo
  is public.
- **Run `py test_engine.py` before pushing engine changes.** The scan commits
  to `main` on its own schedule, so `git pull --rebase` before you push.
