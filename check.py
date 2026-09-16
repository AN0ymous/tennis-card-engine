#!/usr/bin/env python3
"""
Checks the machine before you run the engine, and says exactly what to fix.

    python3 check.py

Standard library only, so it runs even before anything is installed.
"""

import os
import sys
import socket
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"
ok_all = True


def line(good, what, fix=None):
    global ok_all
    print(("  ✓ " if good else "  ✗ ") + what)
    if not good:
        ok_all = False
        if fix:
            print("      → " + fix)


def read_env():
    values = {}
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for raw in f:
                raw = raw.strip()
                if raw and not raw.startswith("#") and "=" in raw:
                    k, v = raw.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ENV", "ANTHROPIC_API_KEY"):
        if os.environ.get(k):
            values[k] = os.environ[k]
    return values, os.path.exists(path)


if len(sys.argv) > 1 and sys.argv[1] == "--probe":
    # python3 check.py --probe "Carlos Alcaraz" ["topps chrome"] [limit]
    # Every listing eBay returns for that player and set, with the verdict
    # the engine reaches and why. Nothing is written or remembered.
    sys.path.insert(0, HERE)
    import tennis_card_engine as engine
    player = sys.argv[2] if len(sys.argv) > 2 else ""
    if not player:
        sys.exit('Usage: python3 check.py --probe "Player Name" ["set keyword"] [limit]')
    sets = [sys.argv[3]] if len(sys.argv) > 3 and not sys.argv[3].isdigit() else list(engine.DEFAULT_BRAND_KEYWORDS)
    limit = int(sys.argv[-1]) if sys.argv[-1].isdigit() else 200
    try:
        for kw in sets:
            print(f"\n== {player} · {kw} (up to {limit} newest listings) ==")
            rows = engine.probe(player, kw, limit=limit)
            if not rows:
                print("  eBay returned no listings for this search.")
            for verdict, why, title, link in rows:
                mark = {"match": "MATCH ", "recorded": "KNOWN ", "error": "ERROR "}.get(verdict, "skip  ")
                print(f"  {mark} {title}\n         {why}\n         {link}")
            counts = {}
            for v, *_ in rows:
                counts[v] = counts.get(v, 0) + 1
            print("  -- " + ", ".join(f"{n} {v}" for v, n in sorted(counts.items())))
    except engine.EngineError as e:
        sys.exit(f"ERROR: {e}")
    sys.exit(0)

print("\nTennis Card Engine — system check\n")

# 1. Python
v = sys.version_info
line(v >= (3, 8), f"Python {v.major}.{v.minor}.{v.micro} at {PY}",
     "Install Python 3.8 or newer from python.org, then run this again with python3.")

# 2. Right folder
line(os.path.exists(os.path.join(HERE, "server.py")) and os.path.exists(os.path.join(HERE, "tennis_card_engine.py")),
     f"Project files found in {HERE}",
     "server.py and tennis_card_engine.py should be next to this file. Unzip the project fully and run from inside its folder.")

# 3. Dependencies
missing = []
for mod in ("requests", "openpyxl"):
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
line(not missing, "requests and openpyxl are installed" if not missing else f"Missing: {', '.join(missing)}",
     f'Run:  "{PY}" -m pip install -r "{os.path.join(HERE, "requirements.txt")}"')

# 4. Keys
env, has_file = read_env()
cid, secret = env.get("EBAY_CLIENT_ID", ""), env.get("EBAY_CLIENT_SECRET", "")
line(has_file or (cid and secret),
     ".env file present" if has_file else "Keys provided through the environment" if (cid and secret) else "No .env file yet",
     f"Copy .env.example to .env in {HERE} and paste your eBay keys into it.")
line(bool(cid and secret), "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are filled in",
     "Both lines in .env need a value after the = sign. Keys come from https://developer.ebay.com/my/keys")

if cid:
    kind = "sandbox" if "SBX" in cid.upper() else "production" if "PRD" in cid.upper() else "unknown"
    mode = (env.get("EBAY_ENV") or "production").lower()
    line(kind == "production" and mode == "production" or kind == "sandbox" and mode == "sandbox",
         f"Keyset is {kind}; engine set to {mode}",
         "A sandbox keyset (SBX) only works with EBAY_ENV=sandbox and finds no real listings. "
         "For real scans use the Production keyset (PRD) and leave EBAY_ENV blank.")
    if kind == "sandbox" and mode == "sandbox":
        print("      (sandbox: fine for testing the connection; expect zero real listings)")

# 4b. Photo step (optional)
akey = env.get("ANTHROPIC_API_KEY", "")
try:
    import anthropic  # noqa: F401
    have_sdk = True
except ImportError:
    have_sdk = False
if akey and have_sdk:
    print("  ✓ Colour match: ANTHROPIC_API_KEY set and the anthropic package installed")
elif akey:
    print("  · Colour match: key set but the anthropic package is missing -- pip install anthropic (optional)")
else:
    print("  · Colour match off: add ANTHROPIC_API_KEY to .env to read outfit colours from photos (optional)")

# 5. Can this machine reach eBay?
host = "api.sandbox.ebay.com" if (env.get("EBAY_ENV") or "").lower() == "sandbox" else "api.ebay.com"
try:
    socket.create_connection((host, 443), timeout=6).close()
    line(True, f"Can reach {host}")
except OSError as e:
    line(False, f"Cannot reach {host} ({e})",
         "Check the internet connection, or a firewall/VPN blocking outbound HTTPS.")

# 6. Port free?
port = 8765
with socket.socket() as s:
    busy = s.connect_ex(("127.0.0.1", port)) == 0
line(not busy, f"Port {port} is free" if not busy else f"Port {port} is already in use",
     f'Something is already listening there (maybe an earlier copy of the server). Either use it at http://127.0.0.1:{port}, or start on another port:  "{PY}" server.py --port 8766')

# 7. The engine imports?
if not missing:
    r = subprocess.run([PY, "-c", "import tennis_card_engine"], cwd=HERE, capture_output=True, text=True)
    line(r.returncode == 0, "The engine imports cleanly", (r.stderr.strip().splitlines() or ["(no detail)"])[-1])

print()
if ok_all:
    print("Everything looks right. Start it with:\n")
    print(f'    "{PY}" "{os.path.join(HERE, "server.py")}"\n')
    print("then open http://127.0.0.1:8765 and press Run scan.")
else:
    print("Fix the ✗ lines above (the → says how), then run this check again.")
print()
