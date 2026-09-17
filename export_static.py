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

out = sys.argv[1] if len(sys.argv) > 1 else "results"
os.makedirs(out, exist_ok=True)
here = os.path.dirname(os.path.abspath(__file__))
xlsx = os.path.join(here, engine.OUTPUT_XLSX)
matches = os.path.join(here, engine.NEW_MATCHES_FILE)

board = engine.build_board(xlsx, matches)
with open(os.path.join(out, "board.json"), "w") as f:
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
            with open(os.path.join(out, "board.json"), "w") as f:
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
try:
    ids = engine.item_ids_in_spreadsheet(xlsx)
    if ids:
        statuses = engine.refresh_statuses(engine.get_ebay_token(), ids, previous)
except Exception as exc:                                  # noqa: BLE001 -- keep the last file
    print(f"status refresh skipped: {exc}")
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

print(f"exported {len(board)} board card(s), {len(statuses)} listing status(es) and config to {out}/")
