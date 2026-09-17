# Tennis Card Engine: project notes

Scans eBay for "bookend" tennis cards (serial #1 or the last of a print run
under 500) from licensed makers, records them in a spreadsheet, and shows them
on a website.

## About the owner

- Justin, on Windows 11. Uses Command Prompt (not PowerShell) and `py` (not `python3`).
- Project folder: `%USERPROFILE%\Downloads\tennis-main`
- Prefers copy-paste commands, one per step, with plain explanations.

## How it runs

- **GitHub (main setup):** public repo `tennis-card-engine`, branch `main`.
  - `.github/workflows/scan.yml` runs the engine once a day at `17 23 * * *`
    (7:17am Singapore time), timeout 60 minutes, then `export_static.py results`
    and commits `results/`.
  - `.github/workflows/pages.yml` publishes `web/` plus `results/` to GitHub Pages.
  - Secrets set: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (Production keyset),
    and possibly `ANTHROPIC_API_KEY` (optional photo checks).
  - Actions versions already updated to Node 24: checkout@v5, setup-python@v6,
    upload-artifact@v6.
- **PC:** `.env` holds the same eBay keys. `py server.py` serves the app at
  http://127.0.0.1:8765. `py check.py` checks setup. `py ebay_usage.py` shows
  eBay API usage (falls back to all APIs when eBay replies 204).

## Decisions (do not undo)

- **1/0 serials are deliberate.** Cards listed as "-1/0" are real; the minus
  sign can't be shown, so they are kept as 1/0 and treated as bookends. Do not
  add validation that rejects print run 0 or card number > print run.
- **The scheduled scan covers everything; a scan you start yourself obeys the
  settings.** The daily GitHub run sends no settings at all, so it still covers
  all players, all 7 sets, all card types, graded and raw, both listing types,
  any price -- that is what keeps the spreadsheet comprehensive. Pressing "Run
  a scan" sends whatever the scan setup is set to, on the hosted site exactly
  as on the PC. Until 17 Sep the hosted button sent nothing but `{"ref":
  "main"}` and `scan.yml` declared no inputs, so every phone-started scan
  quietly ran the defaults however the controls were set.
- eBay's account deletion notifications: opted out ("not persisting eBay data").
- **A card of another sport is turned away; a card that says nothing is kept
  and marked.** The scan searches category 212, which is every sport, and with
  `SCAN_ALL_PLAYERS` True nothing else checks what sport a card is. Measured
  against the 56 matches recorded by 17 Sep: 21 said "tennis" nowhere in their
  title, maker or set, and every one was a real tennis card, so a blanket
  `is_tennis_listing` requirement would have been expensive and wrong. Instead:
  `TENNIS_ONLY_SETS` (Topps Graphite, Topps Royalty) joins `TENNIS_ONLY_MAKERS`
  (NetPro, Ace Authentic), since those lines are only ever tennis -- that alone
  took the unconfirmed 21 down to 5. For what is left (Topps Chrome, Topps Now,
  Panini Instant, which every sport is printed in), a listing whose `Sport`
  specific names another sport is rejected outright, and a listing that states
  nothing is kept with a "check by eye" caution. A wrong card is one glance to
  dismiss; a missed one is gone for good.
- **Custom cards are rejected before the serial is read.** A card somebody
  made themselves carries a real maker in the Manufacturer field and is nearly
  always called a 1/1, because only one exists, so neither the allow-list nor
  the bookend rule stops it. `CUSTOM_CARD_WORDS` does, matched as whole words
  against the title and the Set field only -- never the other item specifics,
  since eBay puts "Custom Bundle: No" on a great many ordinary listings.
  "sketch" is in the list by the owner's decision, knowing it also turns away
  licensed artist sketch cards: a hand-drawn custom and a real sketch card
  read the same in a listing title. `build_board` applies the same rule, so a
  custom recorded before the rule existed drops off the page while its row
  stays in the spreadsheet.

## Changes made to web/assets/app.js

1. **One-tap scans:** on the hosted site, a GitHub fine-grained token
   (Actions: read and write, this repo only) can be pasted per device and is kept
   in localStorage (`tce.githubToken`), never in the repo. With a token, "Run a
   scan" calls the workflow_dispatch API and the page reloads when
   `results/config.json` shows a new `lastRun`. Without one, the button opens
   the GitHub Actions page. Hosted note text says "on a schedule", not a fixed
   interval.
2. **Scans survive leaving the tab.** No scan runs in the page: the local one
   runs in `server.py`'s thread, the hosted one on GitHub. What used to stop
   was the page *noticing*, since a hidden tab has its timers throttled and a
   phone may drop the tab entirely. The hosted watch is kept in localStorage
   (`tce.watching`, with a deadline and the run id) and picked up on load, and
   `visibilitychange` / `focus` / `pageshow` re-check straight away instead of
   waiting for the next tick. On a local page, `resumeLocalScan()` replays the
   server's whole event log from seq 0, so a scan started before a reload comes
   back with its log and progress intact. A watch whose results already landed
   is dropped rather than acted on, so it can never cause a reload loop.
3. **Progress while a scan runs.** A local scan drives the bar from the
   engine's own events. A hosted scan has no event stream, so the bar follows
   the GitHub run instead: `readRunProgress()` finds the run this dispatch
   created (by time, not "the latest run", which could be the daily one) and
   reads `/actions/runs/{id}/jobs` for the step, shown in plain words
   ("Searching eBay"), with the step count and the clock. When that cannot be
   read -- no key, rate-limited, run not yet created -- it falls back to
   `HOSTED_PHASES`, which is honest about being an estimate. The bar is
   visible in hosted mode whether or not a scan is running; when idle it says
   when the last scan finished. The activity log stays hidden there, since
   GitHub sends no per-listing events.
4. **Matches section fallback:** when the latest scan's `new_matches.json` is
   empty, show the board's recent cards with "The latest scan found no new
   cards"; show example cards only when nothing has ever been found.
5. **eBay calls today.** A small meter under the progress bar, from eBay's own
   Developer Analytics figures. The eBay keys never reach the browser: the
   scheduled run writes `results/usage.json` after each scan, and `py
   server.py` answers `/api/usage` from its `.env` (cached 2 minutes, since
   reading it costs a call). Hosted, it appears only once a one-tap key is set
   for the device, and disappears when the key is removed. **That gate is what
   is drawn, not a secret kept** -- the repo is public, so `results/usage.json`
   is published with the page and anyone who looks can read it. It holds
   counts only, never a key. Locally it is always shown.

## Open items

- How often eBay states the sport at all is still unknown: the new `Sport`
  column answers it after the next scan. If nearly every listing states it, the
  "check by eye" caution below can become a rejection; if many leave it blank,
  it has to stay a caution. Sort the spreadsheet by `Sport` after the next run.
- `results/status.json` was empty because `export_static.py` ran without the
  eBay keys; the keys were added to that step, but no scan has run since, so
  the fix is unverified. Check the next run's "Export what the hosted site
  reads" step for "status refresh skipped: ...".
- Whether batching actually saves calls is unconfirmed -- see the note on the
  420 figure under "Things to keep in mind".
- The seven cursors in `results/scan_cursors.json` are in the old bare-timestamp
  format, which says nothing about the rules behind it, so the next run ignores
  them and walks each set in full once before the new format takes over. Expect
  roughly 200 calls on that run rather than the usual handful; it settles by
  itself on the run after.
- `README.md` is not a real readme (it contains pasted engine code).

Done since these notes were written: `app.js` confirmed on `main` (the Matches
fallback works), `scan.yml`'s six-hourly comment corrected, and `test_engine.py`
added -- run `py test_engine.py` before pushing engine changes.

## Things to keep in mind

- eBay Browse API default limit is about 5,000 calls a day, shared by GitHub
  scans, phone-triggered scans and PC scans. A full fresh scan covers about
  8,400 listings (7 sets x 1,200), but item details go out in batches of 20
  (eBay's `getItems` ceiling), so that should cost roughly 420 detail calls
  rather than 8,400. Still never delete `seen_items.json`: a listing judged
  on an earlier run costs no call at all.
- **Three files carry the quota guards between runs**, all restored from
  `results/` by `scan.yml` before the engine starts: `seen_items.json` (what
  each listing was judged, with filtered verdicts keyed to a fingerprint of
  the rules that produced them, so changing a filter re-judges only what that
  filter touches), `scan_cursors.json` (the newest listing each exact search has
  already reached **together with a fingerprint of the rules that reached it**,
  so a scheduled scan does not walk the same window again but a changed setting
  does -- see below), and `ebay_api_usage.json` (a local daily counter enforcing a 4,500
  call ceiling per Pacific day, below eBay's own ~5,000, set with
  `EBAY_DAILY_CALL_BUDGET`). Deleting any of them makes the next scan pay for
  work already done. A card turned away for its sport is a permanent `reject`,
  so it costs one detail call ever, not one per run.
- **A cursor is only trusted while the rules behind it are unchanged.** It
  means "everything older than this is already judged", which is true only of
  the settings that judged it. Before 17 Sep the key covered the search scope
  alone (player, set, price, listing type), so raising the print-run ceiling or
  picking a card type left the mark in place, the search stopped one page in,
  and every scan reported "no new cards" whatever the filter said. Measured
  against a 1,200-listing set: with the mark wrongly kept, 1 call and 0 cards;
  with it correctly dropped, 44 calls and 665 cards. Re-walking is cheap
  because `seen_items.json` still answers for every listing already settled as
  a match or a reject, so only the search pages are paid for again -- about 42
  calls across all 7 sets, against a 4,500 budget. Keeping the cursor for an
  unchanged repeat saves 35 of those; that is all it was ever worth.
- **A fingerprint resolves the settings before hashing.** The website always
  fills the print-run box, so pressing "Run a scan" without touching anything
  sends `max_print_run=500` where a scheduled run sends nothing. They are the
  same scan. Before 17 Sep they hashed differently, so no page-started scan
  ever matched the cursor the scheduled one had left, and every one walked all
  seven sets in full. Seen live in runs 29 and 30: the same default scan wrote
  `7a651a5f2ed5ae03` and `60a3c781e14f279c`.
- **"Not a Federer card" is a filtered verdict, not a reject.** It describes
  the search, not the card, so the player is part of the rules fingerprint. As
  a permanent reject it would have hidden that card from every later scan,
  including the all-players scan that would have matched it.
- **The 420 figure is not yet confirmed against live eBay.** It holds only if
  `getItems` returns `localizedAspects` (the manufacturer, set and serial the
  judge reads). If it does not, every listing needs its own call anyway, and
  the engine notices on the first window and stops batching those, so a scan
  costs about 8,400 again rather than more.
- **Those two warnings do not appear in the Actions log.** `logging.basicConfig`
  sends every `log.warning` to `engine_run.log`, a file on whichever machine
  ran the scan, and that file is gitignored -- so "Bulk item details carry no
  localizedAspects" and "Bulk item details unavailable" never leave the
  runner. An earlier note here said to look for them in the run's output; that
  was wrong. To settle whether batching saves calls, read the allowance before
  and after a scan (`py ebay_usage.py --raw`, or the "eBay calls today" panel
  on the page) and compare -- the difference is the real call count.
- Detail fetching is batched and concurrent, tuned by `DETAIL_BATCH_SIZE`
  (20, eBay's ceiling, do not raise) and `DETAIL_WORKERS` (8) at the top of
  `tennis_card_engine.py`. Lower `DETAIL_WORKERS` if eBay starts refusing
  calls. If eBay ever drops `getItems`, the engine falls back to one call per
  listing on its own -- slower, same results. The batching tests in
  `test_engine.py` cover both paths and need no keys.
- Never commit `.env` or put any key or token in the code.
- The scan commits to `main`, so always `git pull --rebase` before `git push`,
  and avoid pushing while a scan is running.
