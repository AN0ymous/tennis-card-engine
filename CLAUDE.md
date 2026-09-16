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
- The scan covers all players, all 7 sets, all card types, graded and raw,
  both listing types, any price. Website filters only narrow what is shown.
- eBay's account deletion notifications: opted out ("not persisting eBay data").

## Changes made to web/assets/app.js

1. **One-tap scans:** on the hosted site, a GitHub fine-grained token
   (Actions: read and write, this repo only) can be pasted per device and is kept
   in localStorage (`tce.githubToken`), never in the repo. With a token, "Run a
   scan" calls the workflow_dispatch API and the page reloads when
   `results/config.json` shows a new `lastRun`. Without one, the button opens
   the GitHub Actions page. Hosted note text says "on a schedule", not a fixed
   interval.
2. **Matches section fallback:** when the latest scan's `new_matches.json` is
   empty, show the board's recent cards with "The latest scan found no new
   cards"; show example cards only when nothing has ever been found.

## Open items

- Confirm the updated `app.js` is pushed to GitHub (as of the last check, the
  repo still had the older version, so Matches showed example cards even though
  `results/board.json` had 12 real cards).
- `results/status.json` came back with empty statuses. Check the "Export what
  the hosted site reads" step log for "status refresh skipped: ..." and fix.
- Stale comment on the first line of `scan.yml` still says "every six hours".
- `README.md` is not a real readme (it contains pasted engine code).

## Things to keep in mind

- eBay Browse API default limit is about 5,000 calls a day, shared by GitHub
  scans, phone-triggered scans and PC scans. A full fresh scan covers about
  8,400 listings (7 sets x 1,200), but item details go out in batches of 20
  (eBay's `getItems` ceiling), so that should cost roughly 420 detail calls
  rather than 8,400. Still never delete `seen_items.json`: a listing judged
  on an earlier run costs no call at all.
- **The 420 figure is not yet confirmed against live eBay.** It holds only if
  `getItems` returns `localizedAspects` (the manufacturer, set and serial the
  judge reads). If it does not, every listing needs its own call anyway, and
  the engine notices on the first window and stops batching those, so a scan
  costs about 8,400 again rather than more. The first real run settles it:
  look in the "Run the engine" log for "Bulk item details carry no
  localizedAspects" or "Bulk item details unavailable".
- Detail fetching is batched and concurrent, tuned by `DETAIL_BATCH_SIZE`
  (20, eBay's ceiling, do not raise) and `DETAIL_WORKERS` (8) at the top of
  `tennis_card_engine.py`. Lower `DETAIL_WORKERS` if eBay starts refusing
  calls. If eBay ever drops `getItems`, the engine falls back to one call per
  listing on its own -- slower, same results. The batching tests in
  `test_engine.py` cover both paths and need no keys.
- Never commit `.env` or put any key or token in the code.
- The scan commits to `main`, so always `git pull --rebase` before `git push`,
  and avoid pushing while a scan is running.
