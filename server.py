#!/usr/bin/env python3
"""
Local web server for the Tennis Card Engine.

Serves the browser UI in web/ and exposes a small JSON API that drives
tennis_card_engine.run_scan(). Your eBay credentials stay on this machine --
they are read from the environment or the .env file next to this script and
never sent to the browser.

    python server.py                 # http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-browser

The server binds to 127.0.0.1 and has no authentication, so it is reachable
only from this machine. Don't expose it to a network you don't control.
"""

import os
import re
import sys
import json
import time
import argparse
import threading
import webbrowser
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")

# The engine pulls in requests/openpyxl. If they're missing we still want the
# UI to come up so it can tell the user exactly what to install.
engine = None
IMPORT_ERROR = None
try:
    import tennis_card_engine as engine
except Exception as e:                                  # pragma: no cover
    IMPORT_ERROR = f"{type(e).__name__}: {e}"


# ============================================================================
# Player portraits (optional)
#
# Photos of current players are essentially never uncopyrighted -- the
# photographer owns the image, and the player holds publicity rights over
# their likeness. What *is* usable is Wikimedia's freely-licensed pool, on
# condition that the photographer and licence are shown. So this only accepts
# public-domain / CC0 / CC BY(-SA) files and hands the credit back with the
# URL for the page to display. Anything else is treated as unavailable.
# ============================================================================

PORTRAIT_CACHE_FILE = "portrait_cache.json"
FREE_LICENCES = ("public domain", "cc0", "cc by", "cc-by", "attribution")
NON_FREE = ("fair use", "non-free", "all rights reserved", "copyrighted")
_portrait_lock = threading.Lock()
_portrait_cache = None
UA = {"User-Agent": "TennisCardEngine/1.0 (local tool; +https://github.com/)"}


def _cache():
    global _portrait_cache
    if _portrait_cache is None:
        path = os.path.join(BASE_DIR, PORTRAIT_CACHE_FILE)
        try:
            with open(path) as f:
                _portrait_cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            _portrait_cache = {}
    return _portrait_cache


def _cache_save():
    try:
        with open(os.path.join(BASE_DIR, PORTRAIT_CACHE_FILE), "w") as f:
            json.dump(_portrait_cache, f, indent=2)
    except OSError:
        pass


def _get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _strip_html(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def fetch_portrait(player):
    """Return a freely-licensed portrait for a player, or {'available': False}."""
    cached = _cache().get(player)
    if cached is not None:
        return cached

    result = {"available": False, "player": player}
    try:
        summary = _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/"
                            + urllib.parse.quote(player.replace(" ", "_")))
        source = (summary.get("originalimage") or summary.get("thumbnail") or {}).get("source", "")
        thumb = (summary.get("thumbnail") or {}).get("source", "") or source
        filename = urllib.parse.unquote(source.rsplit("/", 1)[-1]) if source else ""

        if filename:
            meta = _get_json(
                "https://commons.wikimedia.org/w/api.php?action=query&format=json"
                "&prop=imageinfo&iiprop=extmetadata|url&titles="
                + urllib.parse.quote("File:" + filename))
            page = next(iter(meta.get("query", {}).get("pages", {}).values()), {})
            info = (page.get("imageinfo") or [{}])[0]
            extra = info.get("extmetadata", {})
            licence = _strip_html(extra.get("LicenseShortName", {}).get("value", ""))
            artist = _strip_html(extra.get("Artist", {}).get("value", ""))
            low = licence.lower()

            if low and not any(bad in low for bad in NON_FREE) \
                    and any(ok in low for ok in FREE_LICENCES):
                result = {
                    "available": True,
                    "player": player,
                    "url": thumb,
                    "credit": artist or "Unknown photographer",
                    "licence": licence,
                    "licenceUrl": extra.get("LicenseUrl", {}).get("value", ""),
                    "source": info.get("descriptionurl", ""),
                }
    except Exception:
        pass        # offline, rate-limited, no article -- the card just uses its crest

    with _portrait_lock:
        _cache()[player] = result
        _cache_save()
    return result


# ============================================================================
# Scan job -- one at a time, with an event log the browser polls
# ============================================================================

class ScanJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.running = False
        self.stop_requested = False
        self.started_at = None
        self.finished_at = None
        self.error = None
        self.summary = None
        self.thread = None

    # -- event log ---------------------------------------------------------
    def add(self, kind, payload=None):
        with self.lock:
            self.events.append({
                "seq": len(self.events) + 1,
                "at": time.time(),
                "kind": kind,
                **(payload or {}),
            })

    def snapshot(self, since=0):
        with self.lock:
            return {
                "running": self.running,
                "stopRequested": self.stop_requested,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "error": self.error,
                "summary": self.summary,
                "total": len(self.events),
                "events": [e for e in self.events if e["seq"] > since],
            }

    # -- lifecycle ---------------------------------------------------------
    def start(self, options):
        with self.lock:
            if self.running:
                return False, "A scan is already running."
            self.events = []
            self.running = True
            self.stop_requested = False
            self.started_at = time.time()
            self.finished_at = None
            self.error = None
            self.summary = None
        self.thread = threading.Thread(target=self._run, args=(options,), daemon=True)
        self.thread.start()
        return True, None

    def stop(self):
        with self.lock:
            if not self.running:
                return False
            self.stop_requested = True
        self.add("info", {"message": "Stopping after the current query finishes."})
        return True

    def _run(self, options):
        try:
            matches, checked = engine.run_scan(
                players=options.get("players") or None,
                brand_keywords=options.get("brands") or None,
                max_print_run=options.get("maxPrintRun"),
                print_run_inclusive=options.get("printRunInclusive"),
                write_outputs=options.get("writeOutputs", True),
                min_price=options.get("minPrice"),
                max_price=options.get("maxPrice"),
                listing_types=options.get("listingTypes"),
                card_types=options.get("cardTypes"),
                conditions=options.get("conditions"),
                on_event=lambda kind, payload: self.add(kind, payload),
                should_stop=lambda: self.stop_requested,
            )
            with self.lock:
                self.summary = {"matches": len(matches), "checked": checked,
                                "cancelled": self.stop_requested}
        except Exception as e:
            message = str(e) if engine and isinstance(e, engine.EngineError) \
                else f"{type(e).__name__}: {e}"
            with self.lock:
                self.error = message
            self.add("error", {"message": message})
        finally:
            with self.lock:
                self.running = False
                self.finished_at = time.time()


JOB = ScanJob()

STATUS_CACHE = {"at": 0.0, "token": None, "statuses": {}}
STATUS_TTL = 600            # re-ask eBay about a listing after ten minutes

USAGE_CACHE = {"at": 0.0, "value": {}}
USAGE_TTL = 120             # the allowance barely moves; don't spend a call per page load


def listing_statuses(item_ids):
    """Live active / sold / ended readings for the listings named, cached briefly."""
    if engine is None:
        return {"error": "engine not loaded"}, 503
    now = time.time()
    cache = STATUS_CACHE
    stale = [i for i in item_ids if i and (now - (cache["statuses"].get(i) or {}).get("_at", 0)) > STATUS_TTL
             and (cache["statuses"].get(i) or {}).get("status") not in ("sold", "ended")]
    if stale:
        try:
            if not cache["token"] or now - cache["at"] > 3600:
                cache["token"], cache["at"] = engine.get_ebay_token(), now
            fresh = engine.refresh_statuses(cache["token"], stale, {})
        except Exception as exc:                          # no keys, no network, eBay down
            return {"error": str(exc), "statuses": {i: cache["statuses"][i] for i in item_ids if i in cache["statuses"]}}, 503
        for i, v in fresh.items():
            cache["statuses"][i] = dict(v, _at=now)
    out = {i: {k: v for k, v in cache["statuses"][i].items() if k != "_at"}
           for i in item_ids if i in cache["statuses"]}
    return {"statuses": out}, 200


# ============================================================================
# HTTP handler
# ============================================================================

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        if "/api/events" not in self.path:          # polling would drown the log
            sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers -----------------------------------------------------------
    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path)
        if route.path == "/api/config":
            return self._json(self._config())
        if route.path == "/api/events":
            since = int((parse_qs(route.query).get("since") or ["0"])[0])
            return self._json(JOB.snapshot(since))
        if route.path == "/api/matches":
            return self._json({"matches": self._saved_matches()})
        if route.path == "/api/board":
            return self._json({"cards": self._board()})
        if route.path == "/api/spreadsheet":
            return self._spreadsheet()
        if route.path == "/api/status":
            ids = [i for i in (parse_qs(route.query).get("ids") or [""])[0].split(",") if i][:100]
            payload, code = listing_statuses(ids)
            return self._json(payload, code)
        if route.path == "/api/usage":
            return self._json(self._usage())
        if route.path == "/api/portrait":
            player = (parse_qs(route.query).get("player") or [""])[0].strip()
            if not player:
                return self._json({"available": False, "error": "No player named."}, 400)
            return self._json(fetch_portrait(player))
        return super().do_GET()

    def do_POST(self):
        route = urlparse(self.path)
        if route.path == "/api/scan":
            if engine is None:
                return self._json({"error": f"The engine failed to import ({IMPORT_ERROR}). "
                                            "Run: pip install requests openpyxl"}, 503)
            options = self._body()
            ok, error = JOB.start(options)
            return self._json({"started": ok, "error": error}, 200 if ok else 409)
        if route.path == "/api/stop":
            return self._json({"stopping": JOB.stop()})
        return self._json({"error": "Unknown endpoint."}, 404)

    # -- payload builders --------------------------------------------------
    def _config(self):
        if engine is None:
            return {"engineAvailable": False, "importError": IMPORT_ERROR,
                    "players": [], "brands": [], "credentials": False}
        cfg = engine.public_config()
        cfg.update({
            "engineAvailable": True,
            "hosted": False,
            "credentials": engine.have_credentials(),
            "emailConfigured": bool(os.environ.get("DIGEST_FROM_EMAIL")
                                    and os.environ.get("DIGEST_FROM_APP_PASSWORD")),
            "spreadsheetExists": os.path.exists(self._data_path(engine.OUTPUT_XLSX)),
        })
        return cfg

    @staticmethod
    def _data_path(name):
        """A working file beside the server, else the copy the scheduled
        GitHub run commits into results/ (see IPAD_SETUP.md)."""
        local = os.path.join(BASE_DIR, name)
        if os.path.exists(local):
            return local
        committed = os.path.join(BASE_DIR, "results", name)
        return committed if os.path.exists(committed) else local

    def _usage(self):
        """eBay's own count of the day's calls. The keys stay here; the browser
        is handed the numbers only. Cached briefly, since the page asks on every
        load and this costs a call of its own."""
        if engine is None:
            return {}
        now = time.time()
        if USAGE_CACHE["at"] and now - USAGE_CACHE["at"] < USAGE_TTL:
            return USAGE_CACHE["value"]
        try:
            value = engine.browse_allowance(engine.get_ebay_token())
        except Exception as exc:                          # noqa: BLE001
            return {"error": str(exc)}
        value = dict(value, checkedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
        USAGE_CACHE.update(at=now, value=value)
        return value

    def _saved_matches(self):
        path = self._data_path(engine.NEW_MATCHES_FILE if engine else "new_matches.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _board(self, limit=24):
        if engine is None:
            return []
        return engine.build_board(self._data_path(engine.OUTPUT_XLSX),
                                  self._data_path(engine.NEW_MATCHES_FILE), limit)

    def _spreadsheet(self):
        name = engine.OUTPUT_XLSX if engine else "tennis_cards_verified.xlsx"
        path = self._data_path(name)
        if not os.path.exists(path):
            return self._json({"error": "No spreadsheet yet -- run a scan first."}, 404)
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="Run the Tennis Card Engine web app.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Default 127.0.0.1 (this machine only). There is no "
                             "authentication, so only change this on a trusted network.")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab.")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"Tennis Card Engine  ->  {url}")
    if engine is None:
        print(f"  ! engine import failed: {IMPORT_ERROR}")
        print("    pip install requests openpyxl")
    elif not engine.have_credentials():
        print("  ! EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set -- the UI will show setup steps.")
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"  ! Listening on {args.host} with no authentication.")
    print("  Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
