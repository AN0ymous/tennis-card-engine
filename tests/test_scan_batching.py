"""Drive run_scan and refresh_statuses against a fake eBay, counting round
trips. Needs no credentials and no network.

Proves the three things the batched detail fetch has to get right:
  * the batched path and the one-call-per-listing fallback find the same
    matches and emit the same events in the same order,
  * batching really is 20 listings to a call, eBay's ceiling,
  * a settled listing is never re-checked.

    python tests/test_scan_batching.py
"""
import json, os, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import tennis_card_engine as e

N_LISTINGS = 500          # listings the fake search returns for every query
calls = {"search": 0, "item": 0, "items": 0}
BULK_ENABLED = True


def make_item(n):
    return {
        "itemId": f"v1|{100000000000 + n}|0",
        "title": f"2024 Topps Chrome Player{n} Refractor {1 if n % 10 == 0 else 7}/{'50'} tennis card",
        "itemWebUrl": f"https://www.ebay.com/itm/{100000000000 + n}",
        "image": {"imageUrl": f"https://img/{n}.jpg"},
        "itemCreationDate": "2026-09-10T00:00:00.000Z",
        "buyingOptions": ["FIXED_PRICE"],
    }


def make_detail(item_id, n):
    return {
        "itemId": item_id,
        "title": make_item(n)["title"],
        "itemWebUrl": f"https://www.ebay.com/itm/{100000000000 + n}",
        "image": {"imageUrl": f"https://img/{n}.jpg"},
        "additionalImages": [],
        "itemCreationDate": "2026-09-10T00:00:00.000Z",
        "buyingOptions": ["FIXED_PRICE"],
        "price": {"value": "25.00", "currency": "USD"},
        "seller": {"username": "someseller"},
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
        "localizedAspects": [
            {"name": "Manufacturer", "value": "Topps"},
            {"name": "Set", "value": "2024 Topps Chrome"},
            {"name": "Sport", "value": "Tennis"},
            {"name": "Player/Athlete", "value": f"Player{n}"},
        ],
    }


def n_of(item_id):
    return int(item_id.split("|")[1]) - 100000000000


class Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._p = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


def fake_get(url, headers=None, params=None, timeout=None, **kw):
    if "item_summary/search" in url:
        calls["search"] += 1
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
        page = [make_item(i) for i in range(offset, min(offset + limit, N_LISTINGS))]
        return Resp({"itemSummaries": page, "total": N_LISTINGS})
    if url.rstrip("/").endswith("/buy/browse/v1/item"):
        if not BULK_ENABLED:
            return Resp({"errors": [{"message": "not found"}]}, status=404)
        calls["items"] += 1
        ids = params["item_ids"].split(",")
        assert len(ids) <= 20, f"batch of {len(ids)} exceeds eBay's ceiling"
        return Resp({"items": [make_detail(i, n_of(i)) for i in ids]})
    if "/buy/browse/v1/item/" in url:
        calls["item"] += 1
        item_id = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        return Resp(make_detail(item_id, n_of(item_id)))
    raise AssertionError(f"unexpected URL {url}")


requests.get = fake_get
e.get_ebay_token = lambda: "fake-token"
e.MAX_RESULTS_PER_BRAND = N_LISTINGS


def run(label, bulk):
    global BULK_ENABLED
    BULK_ENABLED = bulk
    e._bulk_details_supported = True
    for k in calls:
        calls[k] = 0
    events = []
    matches, checked = e.run_scan(
        players=None, brand_keywords=["Topps Chrome"], write_outputs=False,
        on_event=lambda kind, payload: events.append(kind))
    detail_calls = calls["item"] + calls["items"]
    print(f"{label}:")
    print(f"  checked={checked} matches={len(matches)} "
          f"search={calls['search']} getItems={calls['items']} getItem={calls['item']} "
          f"-> {detail_calls} detail round trips")
    return matches, checked, detail_calls, events


fast_m, fast_c, fast_calls, fast_ev = run("getItems available ", True)
slow_m, slow_c, slow_calls, slow_ev = run("getItems missing   ", False)

assert fast_c == slow_c == N_LISTINGS, (fast_c, slow_c)
assert [m["link"] for m in fast_m] == [m["link"] for m in slow_m], "different matches!"
assert len(fast_m) == N_LISTINGS // 10, len(fast_m)
assert fast_ev == slow_ev, "event stream differs"
assert fast_calls == N_LISTINGS / 20, fast_calls
assert slow_calls == N_LISTINGS, slow_calls
print(f"\nsame {len(fast_m)} matches, same event order, "
      f"{slow_calls} -> {fast_calls} detail calls ({slow_calls / fast_calls:.0f}x fewer)")

# ---- statuses ----
ids = [f"v1|{100000000000 + n}|0" for n in range(200)]
for bulk in (True, False):
    BULK_ENABLED = bulk
    e._bulk_details_supported = True
    for k in calls:
        calls[k] = 0
    st = e.refresh_statuses("fake-token", ids, {})
    assert len(st) == 200 and all(v["status"] == "active" for v in st.values())
    print(f"refresh_statuses bulk={bulk}: {calls['items'] + calls['item']} detail round trips")

# settled listings are never re-checked
for k in calls:
    calls[k] = 0
prev = {i: {"status": "sold"} for i in ids}
st = e.refresh_statuses("fake-token", ids, prev)
assert calls["items"] + calls["item"] == 0, "re-checked a settled listing"
print("settled listings cost no calls: ok")
print("\nALL CHECKS PASSED")
