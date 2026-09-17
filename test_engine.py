#!/usr/bin/env python3
"""
Checks the rules that decide which cards the engine keeps, and that a scan
asks eBay for the fewest calls it can.

    py test_engine.py            # all of them
    py test_engine.py -v         # naming each one

Needs nothing beyond what the engine already uses, no eBay keys and no
network: the batching tests stand a fake eBay in front of requests.get. The
cases in samples/cases.json are real cards the owner supplied, so the serial
tests below run against those too.
"""

import os
import json
import tempfile
import unittest
import urllib.parse
from unittest.mock import patch

import requests

import tennis_card_engine as engine

HERE = os.path.dirname(os.path.abspath(__file__))


class BookendRule(unittest.TestCase):
    """#1 or the last of the run, in a run under 500."""

    def test_first_and_last_of_a_run_are_bookends(self):
        self.assertTrue(engine.is_bookend_serial(1, 100))
        self.assertTrue(engine.is_bookend_serial(100, 100))

    def test_middle_of_a_run_is_not(self):
        self.assertFalse(engine.is_bookend_serial(71, 150))
        self.assertFalse(engine.is_bookend_serial(7, 15))

    def test_a_true_one_of_one_is_a_bookend(self):
        self.assertTrue(engine.is_bookend_serial(1, 1))

    def test_one_of_zero_is_kept(self):
        """Cards listed as "-1/0" are real; the minus sign cannot be shown, so
        they stay 1/0 and count as bookends. See CLAUDE.md -- do not undo."""
        self.assertTrue(engine.is_bookend_serial(1, 0))

    def test_print_run_ceiling_is_exclusive_by_default(self):
        self.assertFalse(engine.is_bookend_serial(1, 500))
        self.assertFalse(engine.is_bookend_serial(1, 2000))
        self.assertTrue(engine.is_bookend_serial(1, 499))

    def test_ceiling_can_be_made_inclusive(self):
        self.assertTrue(engine.is_bookend_serial(1, 500, inclusive=True))
        self.assertTrue(engine.is_bookend_serial(500, 500, inclusive=True))

    def test_a_smaller_ceiling_can_be_asked_for(self):
        self.assertTrue(engine.is_bookend_serial(1, 24, max_print_run=25))
        self.assertFalse(engine.is_bookend_serial(1, 99, max_print_run=25))

    def test_missing_numbers_are_not_bookends(self):
        self.assertFalse(engine.is_bookend_serial(None, 100))
        self.assertFalse(engine.is_bookend_serial(1, None))
        self.assertFalse(engine.is_bookend_serial(None, None))


class SerialReading(unittest.TestCase):
    """What counts as a serial in the listing text."""

    def test_serial_is_read_from_the_title(self):
        self.assertEqual(
            engine.extract_serial(
                "Matteo Arnaldi Green Refractor 001/125 Rookie 2025 Topps Chrome Tennis #55", {}),
            (1, 125))

    def test_checklist_number_is_never_a_serial(self):
        """The "162" on a Bublik back is the checklist number. Pairing it with
        a print run would invent a serial the card does not carry."""
        self.assertEqual(
            engine.extract_serial("Alexander Bublik 2026 Topps Tennis",
                                  {"Card Number": ["162"], "Print Run": ["15"]}),
            (None, None))

    def test_a_specific_carrying_its_own_n_of_m_is_read(self):
        self.assertEqual(
            engine.extract_serial("Bublik Topps Tennis", {"Card Number": ["7/15"]}),
            (7, 15))

    def test_printed_one_of_n_run_statement_is_not_a_serial(self):
        """'1 of 2000' is printed the same on every card in the run."""
        self.assertEqual(
            engine.extract_serial("2003 NetPro Elite Rafael Nadal 1 of 2000 PSA 9", {}),
            (None, None))

    def test_a_slashed_run_statement_is_dropped_by_the_ceiling(self):
        number, run = engine.extract_serial("NetPro Elite Nadal 1/2000", {})
        self.assertEqual((number, run), (1, 2000))
        self.assertFalse(engine.is_bookend_serial(number, run))

    def test_card_number_above_the_run_is_read_not_discarded(self):
        """No validation rejects these -- see CLAUDE.md. It is simply not a
        bookend, which is a different thing from being thrown away."""
        self.assertEqual(engine.extract_serial("Some Card 5/2 Tennis", {}), (5, 2))
        self.assertFalse(engine.is_bookend_serial(5, 2))


class ToppsSetGate(unittest.TestCase):
    """Topps prints every sport, so a Topps card has to name one of its sets.
    Graphite and Royalty are searched separately, which means the gate has to
    accept either name on its own without letting a seller's prose in."""

    def gate(self, title, set_name, manufacturer="Topps"):
        ok, _ = engine.is_licensed_and_allowed_brand(title, manufacturer, set_name)
        return ok

    def test_each_set_is_accepted_on_its_own(self):
        for set_name in ("Topps Chrome", "Topps Graphite", "Topps Royalty", "Topps Now"):
            with self.subTest(set=set_name):
                self.assertTrue(self.gate(f"2025 {set_name} Tennis Alcaraz 1/25", set_name))

    def test_the_old_combined_name_still_passes(self):
        self.assertTrue(self.gate("2025 Topps Graphite Royalty Tennis 1/25",
                                  "Topps Graphite Royalty"))

    def test_a_bare_set_field_is_enough(self):
        """eBay's own Set field is specific, so the maker's name is not needed."""
        self.assertTrue(self.gate("2025 Sinner Refractor Tennis 1/25", "Graphite"))
        self.assertTrue(self.gate("2025 Sinner Refractor Tennis 1/25", "Royalty"))

    def test_a_title_needs_the_maker_name_attached(self):
        self.assertTrue(self.gate("2025 Topps Graphite Sinner Tennis 1/25", ""))
        self.assertTrue(self.gate("2025 Topps Royalty Sinner Tennis 1/25", ""))

    def test_seller_prose_does_not_confirm_a_set(self):
        """Sellers call players "tennis royalty"; that is not the Royalty set."""
        self.assertFalse(self.gate("2024 Topps Serena Williams TENNIS ROYALTY Insert 1/50",
                                   "Topps Series One"))
        self.assertFalse(self.gate("Topps 2023 Tennis Royalty Federer Base Card 1/99", ""))
        self.assertFalse(self.gate("2024 Topps Update Graphite Pencil Sketch Tennis 1/1",
                                   "Topps Update"))

    def test_an_unlisted_topps_set_is_still_turned_down(self):
        self.assertFalse(self.gate("2024 Topps Series One Tennis 1/50", "Topps Series One"))

    def test_makers_that_need_no_set_keyword(self):
        self.assertTrue(self.gate("2003 NetPro Elite Nadal 1/100", "NetPro Elite", "NetPro"))
        self.assertFalse(self.gate("2024 Upper Deck Tennis 1/25", "Some Set", "Upper Deck"))


class ItemIdFromLink(unittest.TestCase):
    """web/assets/app.js builds the same 'v1|<digits>|0' key, so saved cards
    line up with the statuses the scan writes."""

    def test_plain_listing_link(self):
        self.assertEqual(engine.item_id_from_link("https://www.ebay.com/itm/198635332126"),
                         "v1|198635332126|0")

    def test_link_with_a_slug_and_query(self):
        self.assertEqual(
            engine.item_id_from_link("https://www.ebay.com/itm/nice-card/198635332126?hash=abc"),
            "v1|198635332126|0")

    def test_nothing_usable(self):
        self.assertEqual(engine.item_id_from_link(""), "")
        self.assertEqual(engine.item_id_from_link("https://example.com/not-a-listing"), "")
        self.assertEqual(engine.item_id_from_link(None), "")


class PlayerSearches(unittest.TestCase):
    def test_a_full_name_is_searched_three_ways(self):
        self.assertEqual(engine.name_variants("Roger Federer"),
                         ["Roger Federer", "Federer", "Roger"])

    def test_a_single_word_name_gives_one_search(self):
        self.assertEqual(engine.name_variants("Federer"), ["Federer"])

    def test_a_surname_alone_needs_the_listing_to_say_tennis(self):
        self.assertTrue(engine.matches_player("Serena Williams 2003 NetPro", "Serena Williams"))
        self.assertFalse(engine.matches_player("Williams 1999 Topps Baseball", "Serena Williams"))

    def test_tennis_is_recognised_from_maker_or_word(self):
        self.assertTrue(engine.is_tennis_listing("2003 NetPro Elite Nadal", {}))
        self.assertTrue(engine.is_tennis_listing("Topps Chrome Tennis 2025", {}))
        self.assertFalse(engine.is_tennis_listing("2025 Topps Chrome Baseball", {}))


class SuppliedCards(unittest.TestCase):
    """samples/cases.json -- real cards, with the verdict each must reach."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "samples", "cases.json")) as f:
            cls.cases = json.load(f)

    def test_the_file_still_describes_every_sample_image(self):
        listed = {c["file"] for c in self.cases}
        on_disk = {n for n in os.listdir(os.path.join(HERE, "samples"))
                   if n.lower().endswith(".jpg")}
        self.assertEqual(listed, on_disk)

    def test_serial_rule_agrees_with_each_recorded_verdict(self):
        checked = 0
        for case in self.cases:
            number, run = case.get("card_number"), case.get("print_run")
            if not case.get("legitimate_serial") or number is None or run is None:
                continue        # dropped for the maker or the printing, not the serial
            checked += 1
            with self.subTest(card=case["file"]):
                self.assertEqual(engine.is_bookend_serial(number, run),
                                 case["expected_engine_verdict"] == "keep",
                                 case.get("why", ""))
        self.assertGreater(checked, 0, "no case carried a usable serial")


class BoardForTheWebsite(unittest.TestCase):
    """What the page reads. Skipped until a scan has produced a spreadsheet."""

    def test_board_entries_carry_what_the_page_shows(self):
        xlsx = os.path.join(HERE, "results", engine.OUTPUT_XLSX)
        if not os.path.exists(xlsx):
            self.skipTest("no results/tennis_cards_verified.xlsx yet -- run a scan first")
        board = engine.build_board(xlsx, os.path.join(HERE, "results", engine.NEW_MATCHES_FILE))
        self.assertIsInstance(board, list)
        for card in board:
            with self.subTest(card=card.get("title", "?")):
                for field in ("player", "title", "serial", "link"):
                    self.assertTrue(card.get(field), f"{field} is empty")
                self.assertTrue(engine.item_id_from_link(card["link"]),
                                "link gives no item id, so its status can never be looked up")



# ===========================================================================
# Batched detail fetching
# ===========================================================================

class FakeEbay:
    """Stands in for the Browse API so the batching can be tested with no
    keys and no network. Counts round trips, and can pretend getItems does
    not exist so the single-call fallback is exercised too."""

    BASE_ID = 100000000000

    def __init__(self, listings=500, bulk=True, aspects_in_bulk=True):
        self.listings, self.bulk = listings, bulk
        # eBay can answer getItems perfectly well and still leave the item
        # specifics out of what it sends back
        self.aspects_in_bulk = aspects_in_bulk
        self.calls = {"search": 0, "getItem": 0, "getItems": 0}

    # -- what eBay would return ------------------------------------------
    def summary(self, n):
        return {
            "itemId": f"v1|{self.BASE_ID + n}|0",
            # every tenth listing is a bookend, the rest are mid-run
            "title": f"2024 Topps Chrome Player{n} Refractor "
                     f"{1 if n % 10 == 0 else 7}/50 tennis card",
            "itemWebUrl": f"https://www.ebay.com/itm/{self.BASE_ID + n}",
            "image": {"imageUrl": f"https://img.test/{n}.jpg"},
            "itemCreationDate": "2026-09-10T00:00:00.000Z",
            "buyingOptions": ["FIXED_PRICE"],
        }

    def detail(self, item_id):
        n = int(item_id.split("|")[1]) - self.BASE_ID
        return dict(self.summary(n), **{
            "additionalImages": [],
            "price": {"value": "25.00", "currency": "USD"},
            "seller": {"username": "someseller"},
            "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
            "localizedAspects": [
                {"name": "Manufacturer", "value": "Topps"},
                {"name": "Set", "value": "2024 Topps Chrome"},
                {"name": "Sport", "value": "Tennis"},
                {"name": "Player/Athlete", "value": f"Player{n}"},
            ],
        })

    # -- the stand-in for requests.get ------------------------------------
    class Response:
        def __init__(self, payload, status=200):
            self.status_code, self._payload = status, payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    def get(self, url, headers=None, params=None, timeout=None, **kw):
        if "item_summary/search" in url:
            self.calls["search"] += 1
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
            page = [self.summary(i) for i in range(offset, min(offset + limit, self.listings))]
            return self.Response({"itemSummaries": page, "total": self.listings})
        if url.rstrip("/").endswith("/buy/browse/v1/item"):
            if not self.bulk:
                return self.Response({"errors": [{"message": "not found"}]}, status=404)
            self.calls["getItems"] += 1
            ids = params["item_ids"].split(",")
            assert len(ids) <= 20, f"batch of {len(ids)} is over eBay's ceiling"
            items = [self.detail(i) for i in ids]
            if not self.aspects_in_bulk:
                items = [{k: v for k, v in d.items() if k != "localizedAspects"} for d in items]
            return self.Response({"items": items})
        if "/buy/browse/v1/item/" in url:
            self.calls["getItem"] += 1
            return self.Response(self.detail(urllib.parse.unquote(url.rsplit("/", 1)[-1])))
        raise AssertionError(f"unexpected URL {url}")

    @property
    def detail_calls(self):
        return self.calls["getItem"] + self.calls["getItems"]


class BatchedDetails(unittest.TestCase):
    """Details go to eBay 20 at a time, and the fallback finds the same cards."""

    LISTINGS = 500

    def setUp(self):
        self._get, self._token = requests.get, engine.get_ebay_token
        self._consume = engine.consume_api_call
        self._per_brand = engine.MAX_RESULTS_PER_BRAND
        engine.get_ebay_token = lambda: "fake-token"
        engine.consume_api_call = lambda _bucket="browse": None
        engine.MAX_RESULTS_PER_BRAND = self.LISTINGS

    def tearDown(self):
        requests.get, engine.get_ebay_token = self._get, self._token
        engine.consume_api_call = self._consume
        engine.MAX_RESULTS_PER_BRAND = self._per_brand
        engine._bulk_details_supported = True

    def scan(self, bulk):
        fake = FakeEbay(self.LISTINGS, bulk=bulk)
        requests.get = fake.get
        engine._bulk_details_supported = True
        events = []
        matches, checked = engine.run_scan(
            players=None, brand_keywords=["Topps Chrome"], write_outputs=False,
            on_event=lambda kind, payload: events.append(kind))
        return fake, matches, checked, events

    def test_batching_and_the_fallback_agree(self):
        fast, fast_matches, fast_checked, fast_events = self.scan(bulk=True)
        slow, slow_matches, slow_checked, slow_events = self.scan(bulk=False)

        self.assertEqual(fast_checked, slow_checked, "different listings checked")
        self.assertEqual([m["link"] for m in fast_matches],
                         [m["link"] for m in slow_matches], "different cards found")
        self.assertEqual(fast_events, slow_events, "different events, or a different order")
        self.assertEqual(len(fast_matches), self.LISTINGS // 10)

    def test_batching_costs_one_call_per_twenty_listings(self):
        fast, _, _, _ = self.scan(bulk=True)
        self.assertEqual(fast.detail_calls, self.LISTINGS / engine.DETAIL_BATCH_SIZE)
        self.assertEqual(fast.calls["getItem"], 0, "fell back when it did not need to")

    def test_without_getitems_every_listing_still_gets_judged(self):
        slow, _, _, _ = self.scan(bulk=False)
        self.assertEqual(slow.calls["getItem"], self.LISTINGS)


class StatusRefresh(unittest.TestCase):
    """Statuses batch the same way, and settled listings are never re-checked."""

    IDS = [f"v1|{FakeEbay.BASE_ID + n}|0" for n in range(200)]

    def setUp(self):
        self._get = requests.get
        self._consume = engine.consume_api_call
        engine.consume_api_call = lambda _bucket="browse": None

    def tearDown(self):
        requests.get = self._get
        engine.consume_api_call = self._consume
        engine._bulk_details_supported = True

    def refresh(self, previous, bulk=True):
        fake = FakeEbay(bulk=bulk)
        requests.get = fake.get
        engine._bulk_details_supported = True
        return fake, engine.refresh_statuses("fake-token", self.IDS, previous)

    def test_statuses_go_out_in_batches(self):
        fake, statuses = self.refresh({})
        self.assertEqual(len(statuses), len(self.IDS))
        self.assertTrue(all(s["status"] == "active" for s in statuses.values()))
        self.assertEqual(fake.detail_calls, len(self.IDS) / engine.DETAIL_BATCH_SIZE)

    def test_a_settled_listing_costs_no_call(self):
        fake, statuses = self.refresh({i: {"status": "sold"} for i in self.IDS})
        self.assertEqual(fake.detail_calls, 0)
        self.assertTrue(all(s["status"] == "sold" for s in statuses.values()))



class CustomCards(unittest.TestCase):
    """Cards somebody made themselves, however they are numbered."""

    REAL_TOPPS = ("topps", "2025 Topps Now")

    def assertRejected(self, title, manufacturer="Topps", set_name="2025 Topps Now"):
        ok, reason = engine.is_licensed_and_allowed_brand(title, manufacturer, set_name)
        self.assertFalse(ok, f"{title!r} was let through")
        self.assertIn("custom", reason)

    def assertKept(self, title, manufacturer="Topps", set_name="2025 Topps Now"):
        ok, reason = engine.is_licensed_and_allowed_brand(title, manufacturer, set_name)
        self.assertTrue(ok, f"{title!r} was turned down: {reason}")

    def test_a_custom_art_card_is_rejected_despite_the_1_of_1(self):
        """The card from the board: a real maker's name, a real set, a 1/1,
        and someone's own artwork. The 1/1 is what makes it tempting."""
        self.assertRejected("1/1 CUSTOM ART CARD Coco Gauff Topps Now 2nd Career Major French Open")

    def test_every_custom_word_is_caught(self):
        for word in engine.CUSTOM_CARD_WORDS:
            with self.subTest(word=word):
                self.assertRejected(f"Coco Gauff Topps Now {word} 1/1")

    def test_custom_in_the_set_field_counts_too(self):
        ok, _ = engine.is_licensed_and_allowed_brand(
            "Coco Gauff Topps Now 1/1", "Topps", "Topps Now Custom")
        self.assertFalse(ok)

    def test_the_rule_runs_before_the_serial_is_read(self):
        """A custom is turned down as a reject, not a filtered card, so it is
        never fetched from eBay again."""
        verdict, reason, _ = engine.judge_listing(
            {"title": "1/1 CUSTOM ART CARD Coco Gauff Topps Now", "itemId": "v1|1|0"},
            {"localizedAspects": [{"name": "Manufacturer", "value": "Topps"},
                                  {"name": "Set", "value": "2025 Topps Now"}]})
        self.assertEqual(verdict, "reject")
        self.assertIn("custom", reason)

    def test_ordinary_cards_are_left_alone(self):
        self.assertKept("Coco Gauff 2021 Topps Chrome Refractor Auto 50/50 PSA 8",
                        "Topps", "2021 Topps Chrome")
        self.assertKept("2013 ACE Tennis National Autograph Card BA-CW1 Caroline Wozniacki 15/15",
                        "Ace Authentic", "2013 Ace Authentic Grand Slam")
        self.assertKept("2013 Ace Personal Best Career Ranking Arvane Rezai 1/15 #1 Card signed auto",
                        "Ace Authentic", "")

    def test_a_longer_word_is_not_a_match(self):
        """Whole words only, so "customer" and "artwork" are not custom cards."""
        self.assertEqual(engine.looks_custom("Sold by a happy customer, Topps Now 1/1"), "")
        self.assertEqual(engine.looks_custom("Great artwork on this Topps Now 1/1"), "")

    def test_sketch_cards_are_rejected_too(self):
        """The owner's call: a hand-drawn custom and a licensed artist sketch
        card read the same in a listing title, so both go. This does turn away
        some real cards. See CUSTOM_CARD_WORDS."""
        self.assertRejected("2025 Topps Now Coco Gauff Artist Sketch Card 1/1")
        self.assertRejected("2024 Topps Chrome Federer Sketches 1/1")


class BoardDropsCustoms(unittest.TestCase):
    """A rule added later still applies to rows already recorded."""

    def test_a_recorded_custom_never_reaches_the_page(self):
        xlsx = os.path.join(HERE, "results", engine.OUTPUT_XLSX)
        if not os.path.exists(xlsx):
            self.skipTest("no results/tennis_cards_verified.xlsx yet -- run a scan first")
        board = engine.build_board(xlsx, os.path.join(HERE, "results", engine.NEW_MATCHES_FILE))
        for card in board:
            with self.subTest(card=card.get("title", "?")):
                self.assertEqual(engine.looks_custom(card["title"], card.get("set_name", "")), "",
                                 "a custom card is on the board")


class BulkWithoutItemSpecifics(unittest.TestCase):
    """getItems can answer fine and still leave out localizedAspects. Every
    listing then needs its own call regardless, so carrying on batching would
    cost more than never batching -- and nothing in the HTTP status says so."""

    IDS = [f"v1|{FakeEbay.BASE_ID + n}|0" for n in range(200)]

    def setUp(self):
        self._get = requests.get
        self._consume = engine.consume_api_call
        engine.consume_api_call = lambda _bucket="browse": None

    def tearDown(self):
        requests.get = self._get
        engine.consume_api_call = self._consume
        engine._bulk_details_supported = True
        engine._bulk_details_carry_aspects = True

    def fetch(self, ids, **kwargs):
        fake = FakeEbay(aspects_in_bulk=False)
        requests.get = fake.get
        engine._bulk_details_supported = True
        engine._bulk_details_carry_aspects = True
        return fake, engine.get_item_details("fake-token", ids, **kwargs)

    def test_every_listing_is_still_returned_in_full(self):
        fake, details = self.fetch(self.IDS)
        self.assertEqual(len(details), len(self.IDS))
        for item_id, detail in details.items():
            with self.subTest(item=item_id):
                self.assertTrue(detail.get("localizedAspects"),
                                "a listing came back with no item specifics to judge on")

    def test_batching_stops_instead_of_costing_more_than_it_saves(self):
        fake, _ = self.fetch(self.IDS)
        self.assertFalse(engine._bulk_details_carry_aspects,
                         "batching stayed on despite never carrying item specifics")
        # one round of batches to find out, then one call each -- never a
        # batch call per listing on top of the single call it still needs
        self.assertLessEqual(fake.calls["getItems"], len(self.IDS) / engine.DETAIL_BATCH_SIZE)
        self.assertLess(fake.detail_calls, len(self.IDS) * 1.2)

    def test_later_fetches_skip_the_batch_entirely(self):
        self.fetch(self.IDS)
        fake = FakeEbay(aspects_in_bulk=False)
        requests.get = fake.get
        engine.get_item_details("fake-token", self.IDS)
        self.assertEqual(fake.calls["getItems"], 0)
        self.assertEqual(fake.calls["getItem"], len(self.IDS))

    def test_statuses_keep_batching_because_they_need_no_specifics(self):
        engine._bulk_details_carry_aspects = False
        fake = FakeEbay(aspects_in_bulk=False)
        requests.get = fake.get
        statuses = engine.refresh_statuses("fake-token", self.IDS, {})
        self.assertEqual(len(statuses), len(self.IDS))
        self.assertEqual(fake.calls["getItem"], 0)
        self.assertEqual(fake.calls["getItems"], len(self.IDS) / engine.DETAIL_BATCH_SIZE)


class QuotaControls(unittest.TestCase):
    def test_incremental_search_stops_at_high_water(self):
        page = [
            {"itemId": "new", "itemCreationDate": "2026-09-16T02:00:00.000Z"},
            {"itemId": "old", "itemCreationDate": "2026-09-15T01:59:59.000Z"},
        ]
        completed = []
        with patch.object(engine, "search_ebay", return_value=(page, 100)) as search:
            found = list(engine.iter_listings(
                "token", None, "NetPro", high_water="2026-09-15T02:00:00.000Z",
                on_complete=lambda query, newest: completed.append((query, newest))))
        self.assertEqual([item["itemId"] for item in found], ["new"])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(completed[0][1], "2026-09-16T02:00:00.000Z")

    def test_filtered_cache_is_scoped_to_rules(self):
        first = engine.rules_fingerprint({"max": 100, "types": {"base"}})
        second = engine.rules_fingerprint({"max": 200, "types": {"base"}})
        entry = {"verdict": "filtered", "rules": first}
        self.assertTrue(engine.filtered_for_rules(entry, first))
        self.assertFalse(engine.filtered_for_rules(entry, second))

    def test_daily_safety_budget_persists(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "API_DAILY_BUDGET", 1):
            engine.consume_api_call("browse")
            with self.assertRaises(engine.EngineError):
                engine.consume_api_call("browse")
            with open(os.path.join(folder, engine.API_USAGE_FILE)) as f:
                self.assertEqual(json.load(f)["browse"], 1)

    def test_scan_persists_filtered_result_and_cursor(self):
        item = {"itemId": "v1|1|0", "title": "card"}

        def listings(_token, _player, _brand, **kwargs):
            kwargs["on_complete"]("NetPro tennis card", "2026-09-16T02:00:00.000Z")
            yield item

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=listings), \
                patch.object(engine, "get_item_details", return_value={"v1|1|0": item}), \
                patch.object(engine, "judge_listing", return_value=("filtered", "price", {})):
            matches, checked = engine.run_scan(brand_keywords=["NetPro"])
            with open(os.path.join(folder, engine.STATE_FILE)) as f:
                saved = json.load(f)
            with open(os.path.join(folder, engine.SCAN_CURSOR_FILE)) as f:
                cursors = json.load(f)

        self.assertEqual((matches, checked), ([], 1))
        self.assertEqual(saved["v1|1|0"]["verdict"], "filtered")
        self.assertEqual(len(cursors), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
