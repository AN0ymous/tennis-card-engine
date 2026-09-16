#!/usr/bin/env python3
"""
Shows how much of your daily eBay call allowance is left, straight from eBay.

    py ebay_usage.py            # the Browse API that the scan uses
    py ebay_usage.py --all      # every API your keyset can call

Reads the same .env keys the engine uses. The numbers come from eBay's own
Developer Analytics API (getRateLimits), documented at
https://developer.ebay.com/api-docs/developer/analytics/resources/rate_limit/methods/getRateLimits
The same figures are on the web at https://developer.ebay.com/my/analytics
"""

import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tennis_card_engine as engine

RATE_LIMIT_URL = f"{engine.EBAY_API_BASE}/developer/analytics/v1_beta/rate_limit/"
BROWSE = {"api_context": "buy", "api_name": "browse", "api_version": "v1"}


def fetch(token, params):
    resp = requests.get(
        RATE_LIMIT_URL,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=20,
    )
    if resp.status_code in (401, 403):
        raise engine.EngineError(
            f"eBay refused the request ({resp.status_code}). The Analytics API needs a "
            "Production keyset that has been granted access; check the keyset at "
            "https://developer.ebay.com/my/keys and try again."
        )
    # eBay answers 204 with no body when it holds no figures for that filter,
    # which is why asking for one API can come back empty while --all works.
    if resp.status_code not in (200, 204):
        raise engine.EngineError(
            f"eBay returned {resp.status_code}: {resp.text[:300]}"
        )
    try:
        limits = resp.json().get("rateLimits", []) if resp.status_code == 200 else []
    except ValueError:
        limits = []
    return resp, limits


def when(reset):
    for shape in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            at = datetime.strptime(reset, shape).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        left = at - datetime.now(timezone.utc)
        hours, minutes = divmod(max(int(left.total_seconds()), 0) // 60, 60)
        return f"{at:%Y-%m-%d %H:%M UTC} (in {hours}h {minutes}m)"
    return str(reset)


def show(limits):
    for entry in limits:
        for resource in entry.get("resources", []):
            for rate in resource.get("rates", []):
                cap, left = rate.get("limit"), rate.get("remaining")
                if cap is None or left is None:
                    continue
                window = rate.get("timeWindow", 0)
                cap_label = "daily limit" if window == 86400 else f"limit per {window}s"
                print(f"\n  {resource.get('name', entry.get('apiName', '?'))}")
                for text, value in ((cap_label, cap), ("used", cap - left), ("left", left)):
                    print(f"      {text:<16}{value:>7,}")
                print(f"      {'resets':<16}{when(rate.get('reset'))}")
                if cap and left / cap < 0.2:
                    print("      ** under a fifth left; scans may start coming back empty **")


def main():
    everything = "--all" in sys.argv
    try:
        token = engine.get_ebay_token()
        resp, limits = fetch(token, {} if everything else BROWSE)
        if not limits and not everything:
            print("\neBay reported nothing for the Browse API, so here is every API instead.")
            resp, limits = fetch(token, {})
    except engine.EngineError as exc:
        sys.exit(f"\nERROR: {exc}\n")
    except requests.RequestException as exc:
        sys.exit(f"\nERROR: could not reach eBay: {exc}\n")

    print(f"\neBay call allowance ({engine.EBAY_ENV} keyset)")
    if "--raw" in sys.argv:
        print(f"\n  asked:  {resp.url}")
        print(f"  got:    HTTP {resp.status_code}")
        print(f"  body:   {resp.text.strip() or '(empty)'}")
    if not limits:
        print(f"\n  eBay answered HTTP {resp.status_code} with no figures.")
        print("  Run again with --raw to see exactly what it sent back, and compare with")
        print("  https://developer.ebay.com/my/analytics which shows the same counts.\n")
        return
    show(limits)
    print()


if __name__ == "__main__":
    main()
