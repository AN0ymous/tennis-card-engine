#!/usr/bin/env python3
"""
Writes what the hosted site reads: results/board.json, results/config.json,
and a copy of the latest matches. The scheduled GitHub run calls this after
the engine, then commits results/ and publishes the site from it.

    python export_static.py results
"""

import os
import sys
import json
import shutil
from datetime import datetime, timezone

import tennis_card_engine as engine

# Running this publishes: it overwrites results/. Importing it must never do
# that by accident -- there is no main() to guard, so guard the module.
if __name__ != "__main__":
    raise ImportError("export_static.py is a script -- run it, do not import it.")

out = sys.argv[1] if len(sys.argv) > 1 else "results"
os.makedirs(out, exist_ok=True)
here = os.path.dirname(os.path.abspath(__file__))
xlsx = os.path.join(here, engine.OUTPUT_XLSX)
matches = os.path.join(here, engine.NEW_MATCHES_FILE)

board = engine.build_board(xlsx, matches)
board_path = os.path.join(out, "board.json")
# An empty board over a good one blanks the published site, and the commit
# step runs with if: always(), so it would be published. build_board returns
# nothing at all when the spreadsheet is missing or unreadable -- which is a
# lost file, not a day with no cards -- so in that case keep what is already
# there and say so loudly. A first run, with no board yet, still writes one.
board_kept = not board and os.path.exists(board_path)
if board_kept:
    print("::warning::The spreadsheet gave no cards, so the board already "
          "published is kept rather than blanked. Check that "
          f"{engine.OUTPUT_XLSX} was restored before the engine ran.")
else:
    with open(board_path, "w") as f:
        json.dump({"cards": board}, f)

config = engine.public_config()
config.update({
    "hosted": True,
    "engineAvailable": True,
    "credentials": True,                     # the run on GitHub has them
    "spreadsheetExists": os.path.exists(xlsx),
    "lastRun": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    # scan.yml passes the engine step's outcome; unset when run by hand, which
    # means the scan just finished in front of you and you already know
    "lastRunOk": os.environ.get("SCAN_OUTCOME", "success") == "success",
    "repo": os.environ.get("GITHUB_REPOSITORY", ""),
    "workflow": "scan.yml",
})
with open(os.path.join(out, "config.json"), "w") as f:
    json.dump(config, f)

# colour readings for rows recorded before the photo step existed
try:
    if engine.vision_available():
        n = engine.fill_colour_matches(xlsx, limit=60)
        if n:
            print(f"colour readings added to {n} row(s)")
            board = engine.build_board(xlsx, matches)
            if board:
                with open(board_path, "w") as f:
                    json.dump({"cards": board}, f)
except Exception as exc:                                  # noqa: BLE001
    print(f"colour readings skipped: {exc}")

for name in (engine.NEW_MATCHES_FILE, engine.OUTPUT_XLSX, engine.STATE_FILE,
             engine.SCAN_CURSOR_FILE, engine.VISION_CACHE_FILE):
    src = os.path.join(here, name)
    if os.path.exists(src):
        shutil.copy(src, os.path.join(out, name))
if not os.path.exists(os.path.join(out, engine.NEW_MATCHES_FILE)):
    with open(os.path.join(out, engine.NEW_MATCHES_FILE), "w") as f:
        json.dump([], f)

# active / sold / ended for every recorded listing, so a saved card on the
# hosted page can be filtered without a server
status_path = os.path.join(out, engine.STATUS_FILE)
previous = {}
if os.path.exists(status_path):
    try:
        with open(status_path) as f:
            previous = json.load(f).get("statuses", {})
    except (json.JSONDecodeError, OSError):
        previous = {}
statuses = previous
report = {}
if engine.budget_bypassed():
    print("The safety ceiling is bypassed for this run's status checks too.")
try:
    ids = engine.item_ids_in_spreadsheet(xlsx)
    if ids:
        statuses = engine.refresh_statuses(engine.get_ebay_token(), ids, previous, report=report)
except Exception as exc:                                  # noqa: BLE001 -- keep the last file
    print(f"status refresh skipped: {exc}")
if report:
    # said every time, so a status that never changes can be told from one
    # that was never asked about (run 42 on 17 Sep: 55 asked, 55 refused, 0 said)
    print(f"statuses: asked eBay about {report['asked']} of {len(ids)} listing(s); "
          f"{report['refused']} refused (last reading kept); {report['gone']} no longer served")
with open(status_path, "w") as f:
    json.dump({"checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "statuses": statuses}, f)

# How much of eBay's daily allowance this keyset has left, as eBay reports it.
# Written after the scan, so it is the figure the run itself finished on.
allowance = {}
try:
    allowance = engine.browse_allowance(engine.get_ebay_token())
except Exception as exc:                                  # noqa: BLE001 -- never fail the run
    print(f"allowance read skipped: {exc}")
with open(os.path.join(out, "usage.json"), "w") as f:
    json.dump(dict(allowance, checkedAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")), f)
if allowance:
    print(f"eBay allowance: {allowance['used']:,} of {allowance['limit']:,} calls used")

# Persist the local safety counter after scanning and status refreshes have
# completed, so the next scheduled process sees the full daily usage.
usage = os.path.join(here, engine.API_USAGE_FILE)
if os.path.exists(usage):
    shutil.copy(usage, os.path.join(out, engine.API_USAGE_FILE))

wrote = "kept the board already published" if board_kept else f"exported {len(board)} board card(s)"
print(f"{wrote}, {len(statuses)} listing status(es) and config to {out}/")
