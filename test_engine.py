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
import shutil
import smtplib
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
    def test_a_full_name_is_searched_two_ways(self):
        """Full name and surname. Not the first name: "Roger Topps Chrome" is
        every Roger in every sport, and a card titled with a first name only
        is vanishingly rare."""
        self.assertEqual(engine.name_variants("Roger Federer"), ["Roger Federer", "Federer"])

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
    """Stands in for the Browse API so the calls can be counted with no keys
    and no network. The batch endpoint (getItems) answers as eBay does for
    this keyset: it does not, and the engine must never ask."""

    BASE_ID = 100000000000

    def __init__(self, listings=500):
        self.listings = listings
        self.calls = {"search": 0, "getItem": 0}

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
            body = {"itemSummaries": page, "total": self.listings}
            if offset + len(page) < self.listings:
                body["next"] = f"{url}?offset={offset + len(page)}"     # as eBay does
            return self.Response(body)
        if url.rstrip("/").endswith("/buy/browse/v1/item"):
            raise AssertionError("the batch call is not offered to this keyset (run 43: "
                                 "HTTP 403) and must never be made")
        if "/buy/browse/v1/item/" in url:
            self.calls["getItem"] += 1
            return self.Response(self.detail(urllib.parse.unquote(url.rsplit("/", 1)[-1])))
        raise AssertionError(f"unexpected URL {url}")

    @property
    def detail_calls(self):
        return self.calls["getItem"]


class OneCallPerListing(unittest.TestCase):
    """The judge fetches one listing per call, and only the listings the
    title cannot settle. There is no cheaper way: eBay's batch call is not
    offered to this keyset (run 43, 17 Sep: HTTP 403 for every batch ever
    sent), so the engine no longer has one."""

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

    def scan(self):
        fake = FakeEbay(self.LISTINGS)
        requests.get = fake.get
        events = []
        matches, checked = engine.run_scan(
            players=None, brand_keywords=["Topps Chrome"], write_outputs=False,
            on_event=lambda kind, payload: events.append(kind))
        return fake, matches, checked, events

    def test_only_the_titles_the_judge_cannot_settle_are_fetched(self):
        """Nine in ten fixture titles carry 7/50, which the title alone settles
        for no call; the bookend-titled tenth costs one call each."""
        fake, matches, checked, _ = self.scan()
        self.assertEqual(checked, self.LISTINGS)
        self.assertEqual(len(matches), self.LISTINGS // 10)
        self.assertEqual(fake.calls["getItem"], sum(1 for n in range(self.LISTINGS) if n % 10 == 0))


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

    def refresh(self, previous):
        fake = FakeEbay()
        requests.get = fake.get
        return fake, engine.refresh_statuses("fake-token", self.IDS, previous)

    def test_a_status_costs_one_call_each(self):
        fake, statuses = self.refresh({})
        self.assertEqual(len(statuses), len(self.IDS))
        self.assertTrue(all(s["status"] == "active" for s in statuses.values()))
        self.assertEqual(fake.detail_calls, len(self.IDS))

    def test_a_settled_listing_costs_no_call(self):
        fake, statuses = self.refresh({i: {"status": "sold"} for i in self.IDS})
        self.assertEqual(fake.detail_calls, 0)
        self.assertTrue(all(s["status"] == "sold" for s in statuses.values()))

    class Refusing(FakeEbay):
        """eBay with the day's allowance used up: every item call is a 429."""
        def get(self, url, headers=None, params=None, timeout=None, **kw):
            if "/buy/browse/v1/item/" in url:
                self.calls["getItem"] += 1
                return self.Response({"errors": [{"message": "call limit exceeded"}]}, status=429)
            return super().get(url, headers, params, timeout, **kw)

    def test_a_refused_call_keeps_the_last_reading_rather_than_calling_it_ended(self):
        """17 Sep, 06:21: the allowance was gone, the status calls were
        refused, and 55 live listings were recorded as ended -- which counts
        as settled, so they would never have been asked about again."""
        fake = self.Refusing()
        requests.get = fake.get
        previous = {i: {"status": "active", "checkedAt": "2026-09-17T00:00:00Z"} for i in self.IDS[:3]}
        statuses = engine.refresh_statuses("fake-token", self.IDS[:3], previous)
        self.assertEqual([s["status"] for s in statuses.values()], ["active"] * 3)
        unknown = engine.refresh_statuses("fake-token", self.IDS[3:4], {})
        self.assertEqual(unknown[self.IDS[3]]["status"], "unknown")

    def test_a_refusal_is_counted_for_the_export_to_say(self):
        fake = self.Refusing()
        requests.get = fake.get
        report = {}
        engine.refresh_statuses("fake-token", self.IDS[:55], {}, report=report)
        self.assertEqual(report, {"asked": 55, "refused": 55, "gone": 0})

    def test_the_55_wrongly_ended_are_repaired_by_single_calls(self):
        """Run 43, 17 Sep: 55 statuses recorded "ended" with no evidence,
        55 single calls, 55 live listings."""
        legacy = {"status": "ended", "checkedAt": "2026-09-17T06:21:44Z"}
        fake = FakeEbay()
        requests.get = fake.get
        report = {}
        statuses = engine.refresh_statuses(
            "fake-token", self.IDS[:55], {i: dict(legacy) for i in self.IDS[:55]}, report=report)
        self.assertEqual([statuses[i]["status"] for i in self.IDS[:55]], ["active"] * 55)
        self.assertEqual(fake.calls["getItem"], 55)
        self.assertEqual(report, {"asked": 55, "refused": 0, "gone": 0})

    def test_a_listing_ebay_no_longer_serves_is_ended_on_its_word(self):
        fake = FakeEbay(listings=1)                  # the fake serves only listing 0
        requests.get = fake.get
        gone = f"v1|{FakeEbay.BASE_ID + 999}|0"
        real_detail = fake.detail
        fake.detail = lambda item_id: real_detail(item_id) if item_id != gone else None
        fake_get = fake.get
        def get(url, headers=None, params=None, timeout=None, **kw):
            if gone in url:
                return fake.Response({"errors": [{"message": "not found"}]}, status=404)
            return fake_get(url, headers, params, timeout, **kw)
        requests.get = get
        report = {}
        statuses = engine.refresh_statuses("fake-token", [self.IDS[0], gone], {}, report=report)
        self.assertEqual(statuses[self.IDS[0]]["status"], "active")
        self.assertEqual(statuses[gone]["status"], "ended")
        self.assertTrue(statuses[gone]["gone"])
        self.assertEqual(report, {"asked": 2, "refused": 0, "gone": 1})
        self.assertTrue(engine.status_settled(statuses[gone]))

    def test_an_ended_reading_with_no_evidence_is_asked_about_again(self):
        """What the 55 look like: ended, no end date, no word from eBay."""
        legacy = {"status": "ended", "checkedAt": "2026-09-17T06:21:44Z"}
        self.assertFalse(engine.status_settled(legacy))
        self.assertTrue(engine.status_settled({"status": "ended", "endDate": "2026-09-01T00:00:00.000Z"}))
        fake = FakeEbay()
        requests.get = fake.get
        statuses = engine.refresh_statuses(
            "fake-token", self.IDS[:20], {i: dict(legacy) for i in self.IDS[:20]})
        self.assertEqual([statuses[i]["status"] for i in self.IDS[:20]], ["active"] * 20)
        self.assertEqual(fake.calls["getItem"], 20)

    def test_a_fresh_active_reading_is_not_asked_about_again(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fake, statuses = self.refresh({i: {"status": "active", "checkedAt": now} for i in self.IDS})
        self.assertEqual(fake.detail_calls, 0)
        fake = FakeEbay(); requests.get = fake.get
        engine.refresh_statuses("fake-token", self.IDS[:20], {i: {"status": "active", "checkedAt": now} for i in self.IDS[:20]}, fresh_within=0)
        self.assertEqual(fake.calls["getItem"], 20, "fresh_within=0 must re-check")



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


class OneWayToFetch(unittest.TestCase):
    """Judge and statuses alike: one call per listing, and the single call
    carries the item specifics the judge needs."""

    IDS = [f"v1|{FakeEbay.BASE_ID + n}|0" for n in range(200)]

    def setUp(self):
        self._get = requests.get
        self._consume = engine.consume_api_call
        engine.consume_api_call = lambda _bucket="browse": None

    def tearDown(self):
        requests.get = self._get
        engine.consume_api_call = self._consume

    def test_judging_asks_for_each_listing_and_gets_it_in_full(self):
        fake = FakeEbay()
        requests.get = fake.get
        details = engine.get_item_details("fake-token", self.IDS)
        self.assertEqual(fake.calls["getItem"], len(self.IDS))
        self.assertEqual(len(details), len(self.IDS))
        for item_id, detail in details.items():
            with self.subTest(item=item_id):
                self.assertTrue(detail.get("localizedAspects"),
                                "a listing came back with no item specifics to judge on")

    def test_statuses_cost_the_same(self):
        fake = FakeEbay()
        requests.get = fake.get
        statuses = engine.refresh_statuses("fake-token", self.IDS, {})
        self.assertEqual(len(statuses), len(self.IDS))
        self.assertEqual(fake.calls["getItem"], len(self.IDS))


class QuotaControls(unittest.TestCase):
    def test_incremental_search_stops_at_high_water(self):
        page = [
            {"itemId": "new", "itemCreationDate": "2026-09-16T02:00:00.000Z"},
            {"itemId": "old", "itemCreationDate": "2026-09-15T01:59:59.000Z"},
        ]
        completed = []
        with patch.object(engine, "search_ebay", return_value=(page, 100, True)) as search:
            found = list(engine.iter_listings(
                "token", None, "NetPro", high_water="2026-09-15T02:00:00.000Z",
                on_complete=lambda query, newest: completed.append((query, newest))))
        self.assertEqual([item["itemId"] for item in found], ["new"])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(completed[0][1], "2026-09-16T02:00:00.000Z")

    def test_a_default_sent_explicitly_matches_one_left_out(self):
        """The website always fills the print-run box, so pressing "Run a scan"
        without touching anything sends 500 where a scheduled run sends
        nothing. They are the same scan and must fingerprint the same, or no
        page-started scan ever matches the cursor the scheduled one left."""
        base = {"min_price": 0.0, "max_price": None, "card_types": set(),
                "conditions": set(), "player": None,
                "wanted_options": set(engine.buying_options([]))}
        scheduled = engine.rules_fingerprint(
            dict(base, max_print_run=None, print_run_inclusive=None))
        from_page = engine.rules_fingerprint(
            dict(base, max_print_run=engine.MAX_PRINT_RUN, print_run_inclusive=False))
        self.assertEqual(scheduled, from_page)
        # and a real change must still register
        self.assertNotEqual(scheduled, engine.rules_fingerprint(
            dict(base, max_print_run=1000, print_run_inclusive=False)))
        self.assertNotEqual(scheduled, engine.rules_fingerprint(
            dict(base, max_print_run=None, print_run_inclusive=True)))

    def test_a_price_floor_of_zero_is_no_floor(self):
        base = {"max_print_run": None, "print_run_inclusive": None, "max_price": None,
                "card_types": set(), "conditions": set(), "player": None,
                "wanted_options": set(engine.buying_options([]))}
        self.assertEqual(engine.rules_fingerprint(dict(base, min_price=0.0)),
                         engine.rules_fingerprint(dict(base, min_price=None)))
        self.assertNotEqual(engine.rules_fingerprint(dict(base, min_price=0.0)),
                            engine.rules_fingerprint(dict(base, min_price=25.0)))

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

    def test_the_bypass_toggle_lifts_the_ceiling_and_keeps_counting(self):
        """The "Bypass ceiling" toggle beside the eBay calls meter: hosted it
        arrives as SCAN_BYPASS_BUDGET, locally server.py sets the flag for one
        scan. Either way the count goes on, so the meter stays true."""
        saved = os.environ.pop("SCAN_BYPASS_BUDGET", None)
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(engine, "_BASE_DIR", folder), \
                    patch.object(engine, "API_DAILY_BUDGET", 1):
                os.environ["SCAN_BYPASS_BUDGET"] = "true"
                self.assertTrue(engine.budget_bypassed())
                for _ in range(3):
                    engine.consume_api_call("browse")
                os.environ.pop("SCAN_BYPASS_BUDGET")
                self.assertFalse(engine.budget_bypassed())
                with self.assertRaises(engine.EngineError):
                    engine.consume_api_call("browse")
                with patch.object(engine, "API_BUDGET_BYPASSED", True):
                    engine.consume_api_call("browse")
                with open(os.path.join(folder, engine.API_USAGE_FILE)) as f:
                    self.assertEqual(json.load(f)["browse"], 4)
                os.environ["SCAN_BYPASS_BUDGET"] = "false"
                self.assertFalse(engine.budget_bypassed(), "only a true-ish value bypasses")
        finally:
            os.environ.pop("SCAN_BYPASS_BUDGET", None)
            if saved is not None:
                os.environ["SCAN_BYPASS_BUDGET"] = saved

    def test_the_bypass_toggle_sits_beside_the_meter_and_reaches_both_paths(self):
        with open(os.path.join(HERE, "web", "index.html")) as f:
            html = f.read()
        box = html[html.index('id="allowance"'):html.index('</aside>')]
        self.assertIn('id="bypass-budget"', box, "the toggle is not beside the eBay calls meter")
        self.assertIn('id="bypass-note"', box)
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            js = f.read()
        self.assertIn("tce.bypassBudget", js, "the toggle is not remembered per device")
        self.assertIn("bypassBudget: bypassBudget()", js, "the local scan request does not carry it")
        self.assertIn("inputs.bypass_budget = !!s.bypassBudget", js, "the hosted dispatch does not carry it")
        with open(os.path.join(HERE, "server.py")) as f:
            py = f.read()
        self.assertIn('options.get("bypassBudget")', py, "server.py ignores the toggle")
        self.assertIn("engine.API_BUDGET_BYPASSED = False", py, "the bypass would outlive the scan")

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


class SportGate(unittest.TestCase):
    """What sport a card is, when nothing named a player to check against."""

    def judge(self, title, aspects):
        item = {"title": title, "itemId": "v1|1|0", "buyingOptions": ["FIXED_PRICE"]}
        detail = {
            "localizedAspects": [{"name": k, "value": v[0]} for k, v in aspects.items()],
            "price": {"value": "50.00", "currency": "USD"},
            "seller": {"username": "someseller"},
            "buyingOptions": ["FIXED_PRICE"],
        }
        return engine.judge_listing(item, detail)

    TOPPS = {"Manufacturer": ["Topps"]}

    def test_a_tennis_only_set_needs_no_tennis_word(self):
        """Topps Graphite is a tennis set, so the set name is the evidence.
        Topps Royalty was here too until 2025 Topps Royalty UFC put one of its
        cards in the spreadsheet as a tennis match: a Royalty card that says
        nothing is now kept with the "check by eye" caution, not believed."""
        for title, set_name in (
                ("2024 Topps Graphite Mirra Andreeva Patch Auto 1/1 Rookie Card", "2024 Topps"),
                ("MIOMIR KECMANOVIC 2024 GS-MKC Topps Graphite AUTOGRAPH 1/10", "2024 Topps Graphite")):
            with self.subTest(title=title):
                self.assertTrue(engine.is_tennis_listing(
                    title, {**self.TOPPS, "Set": [set_name]}))
        royalty = "2024 Topps Royalty Liv Hovde Auto Jumbo Relic 1/5"
        self.assertFalse(engine.is_tennis_listing(royalty, {**self.TOPPS, "Set": ["2024 Topps Royalty"]}))
        verdict, _, fields = self.judge(royalty, {**self.TOPPS, "Set": ["2024 Topps Royalty"]})
        self.assertEqual(verdict, "match")
        self.assertIn("check by eye", fields["caution"])

    def test_a_multi_sport_set_saying_nothing_is_kept_but_marked(self):
        """Topps Chrome is printed for every sport. Sellers leave the Sport
        field blank often enough that rejecting would cost real cards, so this
        is a caution, not a rejection."""
        verdict, _, fields = self.judge(
            "2021 Topps Chrome Autograph Card Tracy Austin 50/50 Bookend",
            {**self.TOPPS, "Set": ["2021 Topps Chrome"]})
        self.assertEqual(verdict, "match")
        self.assertIn("what sport", fields["caution"])
        self.assertEqual(fields["sport"], "")

    def test_a_listing_that_names_another_sport_is_rejected(self):
        """Asked and answered: believing it costs no tennis card."""
        verdict, reason, _ = self.judge(
            "2024 Topps Chrome Aaron Judge Refractor 1/25",
            {**self.TOPPS, "Set": ["2024 Topps Chrome"], "Sport": ["Baseball"]})
        self.assertEqual(verdict, "reject")
        self.assertIn("Baseball", reason)

    def test_a_listing_that_says_tennis_is_clean(self):
        verdict, _, fields = self.judge(
            "2025 Topps Chrome Tennis Clement Chidekh Auto 1/1",
            {**self.TOPPS, "Set": ["2025 Topps Chrome"], "Sport": ["Tennis"]})
        self.assertEqual(verdict, "match")
        self.assertEqual(fields["sport"], "Tennis")
        self.assertEqual(fields["caution"], "")

    def test_table_tennis_is_not_turned_away(self):
        """It says tennis, so it is kept. Deliberate: a card wrongly kept is
        one glance to dismiss."""
        self.assertTrue(engine.is_tennis_listing(
            "2024 Topps Chrome Ma Long 1/25", {**self.TOPPS, "Sport": ["Table Tennis"]}))

    def test_the_sport_is_read_off_the_listing(self):
        self.assertEqual(engine.sport_named({"Sport": ["Baseball"]}), "Baseball")
        self.assertEqual(engine.sport_named({"Sports": ["Tennis"]}), "Tennis")
        self.assertEqual(engine.sport_named({}), "")
        self.assertEqual(engine.sport_named({"Sport": ["  "]}), "")

    def test_the_spreadsheet_keeps_a_sport_column(self):
        """So the next run answers how often eBay states it at all, and whether
        the caution above can ever become a rejection."""
        self.assertIn("Sport", engine.HEADERS)


class CursorFollowsTheRules(unittest.TestCase):
    """A scan must not skip what a changed setting would now let through.

    The cursor says "everything older than this is already judged", which is
    only true of the rules that judged it. These fix the bug where changing the
    ceiling, a card type or a condition left the cursor in place, so the search
    stopped one page in and every scan reported nothing new.
    """

    SETS = ["Topps Chrome"]

    class Dated(FakeEbay):
        """Every listing a bookend 1/N, N cycling 100..900, each with its own
        creation time so a high-water mark actually cuts the walk short."""

        def summary(self, n):
            item = dict(super().summary(n))
            item["title"] = (f"2024 Topps Chrome Player{n} Refractor "
                             f"1/{100 + (n % 9) * 100} tennis card")
            item["itemCreationDate"] = f"2026-09-10T{23 - n // 60:02d}:{59 - n % 60:02d}:00.000Z"
            return item

    def setUp(self):
        self._get, self._token = requests.get, engine.get_ebay_token
        self._consume, self._per_brand = engine.consume_api_call, engine.MAX_RESULTS_PER_BRAND
        self._base = engine._BASE_DIR
        engine.get_ebay_token = lambda: "fake-token"
        engine.consume_api_call = lambda _bucket="browse": None
        engine.MAX_RESULTS_PER_BRAND = 54
        self.folder = tempfile.mkdtemp()
        engine._BASE_DIR = self.folder

    def tearDown(self):
        requests.get, engine.get_ebay_token = self._get, self._token
        engine.consume_api_call, engine.MAX_RESULTS_PER_BRAND = self._consume, self._per_brand
        engine._BASE_DIR = self._base

    def scan(self, **kwargs):
        fake = self.Dated(listings=54)
        requests.get = fake.get
        matches, checked = engine.run_scan(None, self.SETS, **kwargs)
        return matches, checked, fake.calls["search"]

    def cursors(self):
        with open(os.path.join(self.folder, engine.SCAN_CURSOR_FILE)) as f:
            return json.load(f)

    def test_an_unchanged_repeat_still_stops_at_the_mark(self):
        """The saving that matters is kept: the daily run sends the same
        settings every day, so it walks only what was listed since."""
        first, _, _ = self.scan()
        self.assertTrue(first)
        _, checked, searches = self.scan()
        self.assertEqual(searches, 1)
        self.assertLess(checked, 54)

    def test_widening_the_ceiling_walks_the_lot_again(self):
        """The bug: these cards were filtered out under the 500 ceiling, so
        raising it has to reach them, and the cursor must not stop the search."""
        self.scan()
        matches, checked, _ = self.scan(max_print_run=1000)
        self.assertEqual(checked, 54)
        self.assertTrue(matches, "raising the ceiling found nothing")
        self.assertTrue(all(int(m["serial"].split("/")[1]) >= 500 for m in matches))

    def test_a_narrower_card_type_also_rewalks(self):
        """Card type and condition are not in the cursor key at all, so before
        this they silently reused the last run's mark."""
        self.scan()
        _, checked, _ = self.scan(card_types=["base"])
        self.assertEqual(checked, 54)

    def test_the_mark_is_stored_with_the_rules_that_made_it(self):
        self.scan()
        entry = next(iter(self.cursors().values()))
        self.assertIn("newest", entry)
        self.assertIn("rules", entry)

    def test_a_cursor_from_the_old_format_is_ignored(self):
        """A bare timestamp says nothing about the rules behind it, so it is
        not trusted -- one full walk, then the new format takes over."""
        self.assertEqual(engine.cursor_high_water("2026-09-10T00:00:00.000Z", "abc"), "")
        self.assertEqual(engine.cursor_high_water({"newest": "t", "rules": "abc"}, "abc"), "t")
        self.assertEqual(engine.cursor_high_water({"newest": "t", "rules": "abc"}, "xyz"), "")
        self.assertEqual(engine.cursor_high_water(None, "abc"), "")

    def test_a_re_walk_only_pays_for_what_the_change_reopened(self):
        """Why walking it all again is affordable. seen_items.json still
        answers for every listing settled as match or reject, so a re-walk
        re-fetches only the few a changed filter genuinely reopens -- the
        search pages are the whole extra cost."""
        self.scan()
        with open(os.path.join(self.folder, engine.STATE_FILE)) as f:
            seen = json.load(f)
        settled = {k for k, v in seen.items() if engine.state_verdict(v) in ("match", "reject")}
        reopened = {k for k, v in seen.items()
                    if isinstance(v, dict) and v.get("verdict") == "filtered"}
        self.assertTrue(settled and reopened, "the fixture proves nothing")

        asked = []
        real = engine.get_item_details
        fake = self.Dated(listings=54)
        requests.get = fake.get

        def spy(token, item_ids, **kw):
            asked.extend(item_ids)
            return real(token, item_ids, **kw)

        with patch.object(engine, "get_item_details", side_effect=spy):
            engine.run_scan(None, self.SETS, conditions=["raw"])

        self.assertFalse(set(asked) & settled, "a settled listing was paid for twice")
        self.assertLessEqual(set(asked), reopened)


class PlayerTurnDownsAreNotPermanent(unittest.TestCase):
    """"Not a Federer card" describes the search, not the card."""

    ITEM = {"itemId": "v1|1|0", "title": "2024 Topps Chrome Alcaraz 1/25 tennis card",
            "buyingOptions": ["FIXED_PRICE"]}
    DETAIL = {"localizedAspects": [{"name": "Manufacturer", "value": "Topps"},
                                   {"name": "Set", "value": "2024 Topps Chrome"},
                                   {"name": "Sport", "value": "Tennis"}],
              "price": {"value": "50.00", "currency": "USD"},
              "seller": {"username": "someseller"},
              "buyingOptions": ["FIXED_PRICE"]}

    def test_the_wrong_player_is_filtered_not_rejected(self):
        verdict, reason, _ = engine.judge_listing(self.ITEM, self.DETAIL, "Roger Federer")
        self.assertEqual(verdict, "filtered")
        self.assertIn("Federer", reason)

    def test_the_same_card_matches_when_nobody_was_named(self):
        verdict, _, fields = engine.judge_listing(self.ITEM, self.DETAIL)
        self.assertEqual(verdict, "match")
        self.assertEqual((fields["card_number"], fields["print_run"]), (1, 25))

    def test_the_player_is_part_of_the_rules_fingerprint(self):
        """Otherwise a scan for one player would poison the next one for
        everybody, since a reject is never looked at again."""
        rules = {"max_print_run": None}
        self.assertNotEqual(engine.rules_fingerprint(dict(rules, player="Roger Federer")),
                            engine.rules_fingerprint(dict(rules, player=None)))


class ScanSettingsReachTheEngine(unittest.TestCase):
    """The website's settings have to survive the trip to GitHub.

    Before this, the hosted page posted only {"ref": "main"} and scan.yml
    declared no inputs, so every hosted scan ran engine defaults however the
    controls were set.
    """

    ENV = ("SCAN_PLAYERS", "SCAN_BRANDS", "SCAN_MAX_PRINT_RUN", "SCAN_PRINT_RUN_INCLUSIVE",
           "SCAN_MIN_PRICE", "SCAN_MAX_PRICE", "SCAN_LISTING_TYPES", "SCAN_CARD_TYPES",
           "SCAN_CONDITIONS", "SCAN_BYPASS_BUDGET")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.ENV}

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_nothing_set_leaves_every_default_alone(self):
        """A scheduled run sends no inputs and must scan exactly as before."""
        options = engine.scan_options_from_env()
        self.assertIsNone(options["players"])
        self.assertEqual(options["brand_keywords"], list(engine.DEFAULT_BRAND_KEYWORDS))
        self.assertIsNone(options["max_print_run"])
        self.assertIsNone(options["print_run_inclusive"])
        self.assertIsNone(options["min_price"])
        self.assertEqual(options["card_types"], [])

    def test_the_settings_are_read_back(self):
        os.environ.update(SCAN_MAX_PRINT_RUN="1000", SCAN_PRINT_RUN_INCLUSIVE="true",
                          SCAN_MIN_PRICE="25", SCAN_CARD_TYPES="auto",
                          SCAN_CONDITIONS="raw", SCAN_LISTING_TYPES="auction",
                          SCAN_BRANDS="Topps Chrome")
        options = engine.scan_options_from_env()
        self.assertEqual(options["max_print_run"], 1000)
        self.assertIs(options["print_run_inclusive"], True)
        self.assertEqual(options["min_price"], 25.0)
        self.assertEqual(options["card_types"], ["auto"])
        self.assertEqual(options["conditions"], ["raw"])
        self.assertEqual(options["listing_types"], ["auction"])
        self.assertEqual(options["brand_keywords"], ["Topps Chrome"])

    def test_rubbish_is_dropped_rather_than_scanned_on(self):
        os.environ.update(SCAN_BRANDS="Topps Chrome,Pokemon", SCAN_CARD_TYPES="auto,hologram",
                          SCAN_MAX_PRINT_RUN="not a number")
        options = engine.scan_options_from_env()
        self.assertEqual(options["brand_keywords"], ["Topps Chrome"])
        self.assertEqual(options["card_types"], ["auto"])
        self.assertIsNone(options["max_print_run"])

    def test_every_option_is_one_run_scan_accepts(self):
        import inspect
        accepted = set(inspect.signature(engine.run_scan).parameters)
        self.assertLessEqual(set(engine.scan_options_from_env()), accepted)

    def test_the_workflow_declares_and_passes_each_one(self):
        """scan.yml is where the settings would silently go missing."""
        with open(os.path.join(HERE, ".github", "workflows", "scan.yml")) as f:
            yml = f.read()
        for name in self.ENV:
            self.assertIn(f"{name}:", yml, f"{name} is never passed to the engine")
            declared = name[len("SCAN_"):].lower()
            self.assertIn(f"inputs.{declared}", yml, f"{declared} is not read from the inputs")
            self.assertIn(f"      {declared}:", yml, f"{declared} is not declared as an input")
        # GitHub's own ceiling for workflow_dispatch
        self.assertLessEqual(len(self.ENV), 10)

    def test_the_page_sends_every_input_the_workflow_declares(self):
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            js = f.read()
        self.assertIn("inputs: workflowInputs(settings)", js,
                      "the hosted scan still dispatches without its settings")
        for name in self.ENV:
            declared = name[len("SCAN_"):].lower()
            self.assertTrue(f'"{declared}"' in js or f"inputs.{declared}" in js,
                            f"app.js never sends {declared}")


class ThePanelAnswersBothQuestions(unittest.TestCase):
    """The page shows "what's new" and "what matches my filters" separately.

    Showing only new finds made every scan look like a failure and made the
    filters look broken: once a listing is judged it is never a new find
    again, so no filter setting can put an already-recorded card there.
    """

    def js(self):
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            return f.read()

    def test_the_board_carries_the_whole_record(self):
        """The lower section is the whole spreadsheet, filtered, so the board
        must not stop at the newest handful."""
        self.assertGreaterEqual(engine.BOARD_LIMIT, 500)
        xlsx = os.path.join(HERE, "results", engine.OUTPUT_XLSX)
        if os.path.exists(xlsx):
            board = engine.build_board(xlsx)
            # Rows are left off on purpose by exactly three rules -- customs, a
            # title naming another sport, and a serial that is really a grade
            # pair -- so the allowance is counted from those same rules rather
            # than a number that goes stale as the record grows. Anything the
            # board drops beyond what the rules account for is a lost card.
            from openpyxl import load_workbook
            ws = load_workbook(xlsx, read_only=True, data_only=True)[engine.SHEET_NAME]
            rows = ws.iter_rows(values_only=True)
            header = [str(h or "").strip() for h in next(rows, ())]
            col = {name: k for k, name in enumerate(header)}

            def cell(row, name):
                k = col.get(name)
                return "" if k is None or k >= len(row) or row[k] is None else str(row[k]).strip()

            recorded = [r for r in rows if any(r)]
            left_off_by_rule = 0
            for r in recorded:
                title = cell(r, "Card Description")
                set_name = cell(r, "Manufacturer / Set").partition(" / ")[2]
                if (engine.looks_custom(title, set_name)
                        or cell(r, "Serial #") in engine.grade_pairs_in(title)
                        or engine.other_sport_in_title(title)):
                    left_off_by_rule += 1
            dropped = min(len(recorded), engine.BOARD_LIMIT) - len(board)
            self.assertEqual(dropped, left_off_by_rule,
                             "the board drops cards no rule accounts for")

    def test_both_sections_are_rendered(self):
        js = self.js()
        self.assertIn('sectionHead("New this scan"', js)
        self.assertIn('sectionHead("Everything found so far"', js)

    def test_the_lower_section_reads_the_whole_board(self):
        js = self.js()
        self.assertIn("state.board.filter(passesFilters)", js)
        self.assertIn("state.matches.filter(passesFilters)", js)

    def test_no_new_cards_is_no_longer_reported_as_a_dead_end(self):
        """The old wording read as a failed scan. It has to say why nothing is
        new, and that the filters are not the reason."""
        js = self.js()
        self.assertNotIn("The latest scan found no new cards", js)
        self.assertIn("already", js)


class PublishingSurvivesAMovingMain(unittest.TestCase):
    """Run 31 scanned for 100 seconds, then threw the lot away.

    A pull request was merged while it ran, so its bare `git push` was
    refused: the cards it found and the state files that stop the next scan
    re-paying for the same listings all went in the bin. The step has to catch
    up and try again rather than fail.
    """

    def step(self):
        with open(os.path.join(HERE, ".github", "workflows", "scan.yml")) as f:
            yml = f.read()
        start = yml.index("Keep the results in the repo")
        end = yml.index("Offer the spreadsheet", start)
        return yml[start:end]

    def test_the_push_is_retried_rather_than_given_up_on(self):
        step = self.step()
        self.assertIn("for attempt in", step)
        self.assertIn("git fetch", step, "it never looks at what main became")
        self.assertIn("git reset --hard -q origin/main", step)

    def test_this_run_s_results_are_kept_aside_before_catching_up(self):
        """Resetting onto the new main would wipe the working tree, so the
        results have to survive it."""
        step = self.step()
        self.assertIn("mktemp -d", step)
        self.assertIn('cp -a "$keep/results" results', step)

    def test_it_stands_down_rather_than_overwrite_another_run(self):
        """Another scan's results are newer than this run's, which were worked
        out from an older base. Re-running costs calls; overwriting loses
        recorded cards."""
        self.assertIn('git diff --quiet "$base" origin/main -- results', self.step())


class TheScanSetupIsTheFilter(unittest.TestCase):
    """Players, sets and the print-run ceiling narrow what is shown, not only
    what is recorded, and the setup survives the reload a finished hosted
    scan triggers.

    Reproduced on 17 Sep with real clicks: Topps Chrome only, one player,
    ceiling 100 -- "Everything found so far" still said 54; and the reload put
    every control back to the engine default, so the setup was gone by the
    time the results appeared. Card type and condition had narrowed and
    survived all along, which is why the filters looked half-broken.
    """

    def test_a_brand_is_read_from_the_set_field(self):
        self.assertEqual(engine.brand_of("Topps", "2025 Topps Chrome", ""), "Topps Chrome")
        self.assertEqual(engine.brand_of("Topps", "2024 Graphite Signature Relics", ""), "Topps Graphite")
        self.assertEqual(engine.brand_of("Topps", "Topps Royalty / Museum / Premium Set", ""), "Topps Royalty")
        self.assertEqual(engine.brand_of("Topps", "2026 Topps Now", ""), "Topps Now")
        self.assertEqual(engine.brand_of("Panini", "2026 Panini Instant Tennis", ""), "Panini Instant")
        self.assertEqual(engine.brand_of("NetPro", "", ""), "NetPro")
        self.assertEqual(engine.brand_of("Ace Authentic, Inc", "", ""), "Ace Authentic")

    def test_a_bare_topps_set_is_placed_by_the_title(self):
        """Nine recorded cards say just "2024 Topps" in the Set field; the
        title is what the gate accepted them on."""
        self.assertEqual(engine.brand_of("Topps", "2024 Topps", "2024 Topps Chrome Coco Gauff 1/25"),
                         "Topps Chrome")
        # the same qualification the gate needs: "royalty" alone in a title is prose
        self.assertEqual(engine.brand_of("Topps", "2024 Topps", "tennis royalty Iga Swiatek 1/10"), "")
        self.assertEqual(engine.brand_of("Topps", "2024 Topps", "2024 Topps Royalty Iga Swiatek 1/10"),
                         "Topps Royalty")

    def test_an_unplaceable_card_is_blank_not_wrong(self):
        self.assertEqual(engine.brand_of("Upper Deck", "2003 SP Authentic", "Federer 1/1"), "")
        self.assertEqual(engine.brand_of("Topps", "2024 Topps", "Coco Gauff 1/25"), "")

    def test_every_board_card_carries_a_brand_the_setup_knows(self):
        xlsx = os.path.join(HERE, "results", engine.OUTPUT_XLSX)
        if not os.path.exists(xlsx):
            self.skipTest("no results/tennis_cards_verified.xlsx yet")
        board = engine.build_board(xlsx)
        self.assertTrue(board)
        for card in board:
            with self.subTest(card=card["title"][:60]):
                self.assertIn("brand", card)
                if card["brand"]:
                    self.assertIn(card["brand"], engine.DEFAULT_BRAND_KEYWORDS)
        placed = sum(1 for c in board if c["brand"])
        self.assertGreaterEqual(placed, len(board) * 0.9, "most of the record should be placeable")

    def js(self):
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            return f.read()

    def test_the_page_applies_all_three_to_what_is_shown(self):
        js = self.js()
        for fn in ("inPlayers", "inBrands", "underCeiling"):
            self.assertIn(f"{fn}(card)", js.split("function passesFilters")[1].split("}")[0],
                          f"passesFilters does not apply {fn}")

    def test_the_setup_is_remembered_and_restored(self):
        js = self.js()
        self.assertIn("SCAN_SETUP_KEY", js)
        self.assertIn("restoreScanSetup();", js.split("async function loadConfig")[1],
                      "loadConfig never restores the saved setup")
        for control in ('$("all-players").addEventListener("change", scanSetupChanged)',
                        '$("inclusive").addEventListener("change", scanSetupChanged)',
                        '$("max-print-run").addEventListener("input", scanSetupChanged)'):
            self.assertIn(control, js)

    def test_the_published_page_cannot_serve_a_stale_app_js(self):
        """Run 29 on 17 Sep: a phone still on the old app.js against the new
        results looked exactly like a failed fix."""
        with open(os.path.join(HERE, ".github", "workflows", "pages.yml")) as f:
            yml = f.read()
        self.assertIn("assets/app.js?v=", yml)
        self.assertIn("assets/styles.css?v=", yml)



class SurvivesABadDay(unittest.TestCase):
    """What a run keeps when something goes wrong partway through."""

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_a_half_written_memory_file_does_not_stop_the_engine(self):
        """A kill mid-write used to leave invalid JSON, and load_state raised
        on it -- which main() does not catch, so every later run died too."""
        path = os.path.join(self.work, "seen_items.json")
        whole = json.dumps({f"v1|{i}|0": "reject" for i in range(200)}, indent=2)
        with open(path, "w") as f:
            f.write(whole[:len(whole) // 2])
        self.assertEqual(engine.load_state(path), {})
        self.assertTrue(os.path.exists(path + ".unreadable"),
                        "the unreadable copy should be kept, not silently replaced")

    def test_a_write_that_dies_partway_leaves_the_old_file(self):
        path = os.path.join(self.work, "seen_items.json")
        engine.save_state(path, {f"v1|{i}|0": "reject" for i in range(50)})
        with open(path) as f:
            before = f.read()

        real_dump = json.dump

        def dies_partway(data, fp, **kw):
            fp.write(json.dumps(data, **kw)[:200])     # half a document, on disk
            raise RuntimeError("killed mid-write")

        json.dump = dies_partway
        try:
            with self.assertRaises(RuntimeError):
                engine.save_state(path, {f"v1|{i}|0": "match" for i in range(50)})
        finally:
            json.dump = real_dump
        with open(path) as f:
            self.assertEqual(f.read(), before, "the real file was damaged")
        self.assertEqual(len(engine.load_state(path)), 50)

    def test_a_scan_that_dies_partway_still_saves_what_it_found(self):
        """Everything used to be held in memory until the last line, so a
        stumble threw away the matches AND the record of listings judged --
        which the next scan then paid eBay to judge all over again."""
        fake = FakeEbay(200)
        # every fixture title a bookend, so every listing is fetched and the
        # crash below lands on a fetch, as it did before titles settled anything
        fake.summary = lambda n, _s=fake.summary: dict(
            _s(n), title=f"2024 Topps Chrome Player{n} Refractor 1/50 tennis card")
        real_get, real_token = requests.get, engine.get_ebay_token
        real_base, real_cap = engine._BASE_DIR, engine.MAX_RESULTS_PER_BRAND
        calls = {"n": 0}

        # 200 listings arrive in one search call, then go through the judge a
        # windowful at a time, one detail call each: 160 for the first window,
        # 40 for the rest. Blowing up on call 175 lands after the first window
        # has been judged and recorded, which is the work this test is about
        # keeping.
        def blows_up_partway(*a, **kw):
            calls["n"] += 1
            if calls["n"] > 175:
                raise RuntimeError("eBay said something unexpected")
            return fake.get(*a, **kw)

        requests.get = blows_up_partway
        engine.get_ebay_token = lambda: "tok"
        engine._BASE_DIR = self.work
        engine.MAX_RESULTS_PER_BRAND = 200
        try:
            with self.assertRaises(RuntimeError):
                engine.run_scan(players=None, brand_keywords=["Topps Chrome"],
                                write_outputs=True)
        finally:
            requests.get, engine.get_ebay_token = real_get, real_token
            engine._BASE_DIR, engine.MAX_RESULTS_PER_BRAND = real_base, real_cap

        for name in (engine.OUTPUT_XLSX, engine.STATE_FILE, engine.NEW_MATCHES_FILE):
            with self.subTest(file=name):
                self.assertTrue(os.path.exists(os.path.join(self.work, name)),
                                f"{name} was thrown away")
        self.assertGreater(len(engine.load_state(os.path.join(self.work, engine.STATE_FILE))), 0,
                           "no listing verdicts were kept, so the next scan pays for them again")


class DigestEmail(unittest.TestCase):
    """The email is sent after the scan has already succeeded and saved."""

    MATCH = [{"player": "T", "manufacturer": "Topps", "set_name": "Topps Graphite",
              "title": "a card", "serial": "1/10", "bookend": "001 of 10",
              "price": "10.00 USD", "link": "https://www.ebay.com/itm/1",
              "image": "", "found": "now"}]

    def test_a_send_that_fails_does_not_take_the_scan_down(self):
        """Raising here exits non-zero, and on GitHub that stops the steps that
        publish and commit the results -- losing a good scan over an email."""
        real_smtp = smtplib.SMTP
        os.environ.update(DIGEST_FROM_EMAIL="a@b.c", DIGEST_FROM_APP_PASSWORD="pw",
                          DIGEST_TO_EMAIL="a@b.c")

        class Dead:
            def __init__(self, *a, **kw):
                raise smtplib.SMTPAuthenticationError(535, b"auth failed")

        smtplib.SMTP = Dead
        try:
            engine.send_digest_email(self.MATCH)      # must simply return
        finally:
            smtplib.SMTP = real_smtp
            for k in ("DIGEST_FROM_EMAIL", "DIGEST_FROM_APP_PASSWORD", "DIGEST_TO_EMAIL"):
                os.environ.pop(k, None)


class ARefusedSearchIsNotSilent(unittest.TestCase):
    """Runs 32 to 35 on 17 Sep each checked 0 listings with the day's eBay
    allowance used up, and every one went green. The refusal went to an
    on_event nobody had passed and to a log file the runner throws away."""

    REFUSAL = ("eBay refused the search 'NetPro tennis card' at listing 1: HTTP 429 "
               "-- eBay's daily call allowance is used up; it resets at midnight Pacific time")

    def run_main(self, refuse_every=True):
        calls = {"n": 0}

        def listings(_token, _player, _brand, on_error=None, **kwargs):
            calls["n"] += 1
            if refuse_every or calls["n"] == 1:
                on_error(self.REFUSAL)
                return iter(())
            return iter([{"itemId": f"v1|{calls['n']}|0", "title": "card"}])

        import io, contextlib
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=listings), \
                patch.object(engine, "get_item_details", return_value={}), \
                patch.object(engine, "send_digest_email"), \
                patch.dict(os.environ, {"SCAN_BRANDS": "NetPro,Topps Chrome"}), \
                contextlib.redirect_stderr(err):
            try:
                engine.main()
            except SystemExit as exc:
                return exc.code, err.getvalue()
        return 0, err.getvalue()

    def test_a_run_that_could_check_nothing_fails_and_says_why(self):
        code, stderr = self.run_main(refuse_every=True)
        self.assertNotEqual(code, 0, "an all-refused run still exited 0")
        self.assertIn("refused every search", str(code))
        self.assertIn("allowance is used up", str(code))
        self.assertIn("WARNING: eBay refused", stderr, "the refusal never reached stderr")

    def test_a_partly_refused_run_warns_but_still_counts(self):
        code, stderr = self.run_main(refuse_every=False)
        self.assertEqual(code, 0)
        self.assertIn("WARNING: eBay refused", stderr)
        self.assertIn("covered less than usual", stderr)


class AnAddedChipCanBeTakenOut(unittest.TestCase):
    """A name typed in used to be a chip for ever: remembered in localStorage,
    rebuilt on every load, with nothing on the page to remove it."""

    def js(self):
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            return f.read()

    def test_only_typed_in_chips_get_a_cross(self):
        js = self.js()
        self.assertIn("if (added) label.append(removeButton(", js)
        # the built-in players and sets are built without the flag
        self.assertIn("values.forEach((value) => buildChip(box, value, name, true));", js)

    def test_the_cross_forgets_the_name_and_narrows_the_view(self):
        js = self.js()
        body = js.split("function removeButton")[1].split("\nfunction ")[0]
        self.assertIn("forget(group, value)", body)
        self.assertIn("label.remove()", body)
        self.assertIn("scanSetupChanged()", body)
        self.assertIn("e.preventDefault()", body, "the click would also toggle the chip")

    def test_forget_edits_the_remembered_list(self):
        js = self.js()
        body = js.split("function forget(")[1].split("\nfunction ")[0]
        self.assertIn("remembered(group).filter(", body)
        self.assertIn("SEARCHES[group].key", body)


class AFailedRunIsNotDressedAsAQuietDay(unittest.TestCase):
    """#18 exports and commits even when the engine failed, so the results
    it saved survive; #20 makes a run that could check nothing fail. Between
    them, a burnt allowance published a fresh lastRun and the page reloaded
    into "Nothing new ... which is normal". The export now records the
    outcome and the page says so."""

    def export(self, outcome):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as folder:
            for name in ("tennis_card_engine.py", "export_static.py"):
                with open(os.path.join(HERE, name)) as src, open(os.path.join(folder, name), "w") as dst:
                    dst.write(src.read())
            env = {k: v for k, v in os.environ.items()
                   if k not in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "ANTHROPIC_API_KEY")}
            if outcome is not None:
                env["SCAN_OUTCOME"] = outcome
            subprocess.run([sys.executable, "export_static.py", "out"], cwd=folder, env=env,
                           capture_output=True, text=True, timeout=60)
            with open(os.path.join(folder, "out", "config.json")) as f:
                return json.load(f)

    def test_the_export_records_the_engine_outcome(self):
        self.assertIs(self.export("failure")["lastRunOk"], False)
        self.assertIs(self.export("cancelled")["lastRunOk"], False)
        self.assertIs(self.export("success")["lastRunOk"], True)
        self.assertIs(self.export(None)["lastRunOk"], True, "run by hand: no outcome means fine")

    def test_the_workflow_hands_the_outcome_over(self):
        with open(os.path.join(HERE, ".github", "workflows", "scan.yml")) as f:
            yml = f.read()
        self.assertIn("        id: engine\n", yml)
        self.assertIn("SCAN_OUTCOME: ${{ steps.engine.outcome }}", yml)

    def test_the_page_says_failed_rather_than_quiet(self):
        with open(os.path.join(HERE, "web", "assets", "app.js")) as f:
            js = f.read()
        self.assertIn('c.lastRunOk === false', js.split("function enterHostedMode")[1].split("\nfunction ")[0])
        self.assertIn("state.config.lastRunOk === false", js.split("function renderMatches")[1].split("\nfunction ")[0])
        self.assertIn("Last scan failed", js)


class WhatTwoPlayerScansTaught(unittest.TestCase):
    """17 Sep: a Shapovalov scan paid for 2,077 listings (1,083 of them other
    Denises) and recorded a UFC card as his; a Gauff scan found 12 bookends the
    wide scan had never seen, 10 of them lacking the word "card"; and a
    77/77 Shapovalov Topps Chrome, in plain view, was passed over."""

    UFC = "2025 Topps Royalty UFC Benoit Saint Denis Blue Patch Logo /25 #SR-BS 1/25"
    SHAPO = "2024 Topps Chrome Tennis Denis Shapovalov 1st Pineapple Refractor 77/77 \u22481/1"

    def detail(self, **aspects):
        return {"localizedAspects": [{"name": k, "value": v} for k, v in aspects.items()],
                "price": {"value": "1.00", "currency": "USD"}, "seller": {"username": "s"},
                "buyingOptions": ["AUCTION"], "itemWebUrl": "https://www.ebay.com/itm/1"}

    def item(self, title):
        return {"itemId": "v1|1|0", "title": title, "buyingOptions": ["AUCTION"]}

    def test_the_wide_search_never_says_card(self):
        for brand in engine.DEFAULT_BRAND_KEYWORDS:
            self.assertNotIn("card", engine.wide_query(brand).lower(), brand)
        self.assertEqual(engine.wide_query("Topps Chrome"), "Topps Chrome tennis")
        self.assertEqual(engine.wide_query("Topps Royalty"), "Topps Royalty")
        self.assertEqual(engine.wide_query("NetPro"), "NetPro")
        self.assertEqual(engine.wide_query("Topps Graphite"), "Topps Graphite")

    def test_a_changed_wide_query_stales_the_cursor(self):
        with patch.object(engine, "wide_query", side_effect=lambda b: f"{b} tennis card"):
            before = engine.scan_cursor_key(None, "Topps Chrome", 0.0, None, [])
        after = engine.scan_cursor_key(None, "Topps Chrome", 0.0, None, [])
        self.assertNotEqual(before, after)

    def test_a_player_is_searched_by_full_name_and_surname_only(self):
        self.assertEqual(engine.name_variants("Denis Shapovalov"), ["Denis Shapovalov", "Shapovalov"])
        self.assertEqual(engine.name_variants("Coco Gauff"), ["Coco Gauff", "Gauff"])
        self.assertEqual(engine.name_variants("Serena"), ["Serena"])

    def test_a_first_name_alone_is_not_the_player(self):
        self.assertFalse(engine.matches_player(self.UFC, "Denis Shapovalov", {}))
        self.assertTrue(engine.matches_player("Federer 2003 NetPro Elite 1/100", "Roger Federer", {}))
        self.assertTrue(engine.matches_player(self.SHAPO, "Denis Shapovalov", {}))

    def test_topps_royalty_is_not_only_tennis(self):
        self.assertNotIn("topps royalty", engine.TENNIS_ONLY_SETS)
        verdict, reason, _ = engine.judge_listing(self.item(self.UFC), self.detail(Manufacturer="Topps"))
        self.assertEqual(verdict, "reject")
        self.assertIn("UFC", reason)

    def test_a_blank_or_line_named_maker_is_read_off_the_title(self):
        for aspects in ({}, {"Manufacturer": "Topps Chrome"}, {"Condition": "Ungraded - Excellent"}):
            verdict, reason, f = engine.judge_listing(self.item(self.SHAPO), self.detail(**aspects))
            self.assertEqual(verdict, "match", (aspects, reason))
            self.assertEqual((f["card_number"], f["print_run"]), (77, 77))
            self.assertEqual(f["manufacturer"], "Topps")
        self.assertEqual(engine.resolve_manufacturer("Upper Deck", "", "Upper Deck Federer 1/1"), "Upper Deck")
        verdict, reason, _ = engine.judge_listing(self.item("2003 SP Authentic Roger Federer 1/1 tennis"),
                                                  self.detail(Manufacturer="Upper Deck"))
        self.assertEqual(verdict, "reject")

    def test_what_the_title_settles_costs_no_call(self):
        self.assertIn("neither the first nor the last", engine.settled_by_title("2024 Topps Chrome Coco Gauff 7/50 tennis"))
        self.assertIn("UFC", engine.settled_by_title(self.UFC))
        self.assertIn("custom", engine.settled_by_title("Custom Federer 1/1 Topps Chrome"))
        for kept in (self.SHAPO, "2024 Topps Chrome Coco Gauff 1/50", "2024 Topps Chrome Coco Gauff 50/50",
                     "2024 Topps Chrome Coco Gauff Refractor", "NetPro Federer 1/0", "Topps Chrome tennis golf lot 1/50",
                     "2024 Topps Royalty Tennis Jamie Murray On Card Auto Relic /10 Superior Signature"):
            self.assertEqual(engine.settled_by_title(kept), "", kept)

    def test_a_settled_listing_is_not_fetched_and_keeps_its_reason(self):
        items = [{"itemId": "v1|7|0", "title": "2024 Topps Chrome Coco Gauff 7/50 tennis"},
                 {"itemId": "v1|1|0", "title": "2024 Topps Chrome Coco Gauff 1/50 tennis"}]
        asked = []

        def details(_token, ids, **kw):
            asked.extend(ids)
            return {i: self.detail(Manufacturer="Topps", Set="2024 Topps Chrome Tennis", Sport="Tennis") for i in ids}

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=lambda *a, **k: iter(items)), \
                patch.object(engine, "get_item_details", side_effect=details):
            matches, checked = engine.run_scan(None, ["Topps Chrome"])
            with open(os.path.join(folder, engine.STATE_FILE)) as f:
                seen = json.load(f)
        self.assertEqual(checked, 2)
        self.assertEqual(asked, ["v1|1|0"], "the 7/50 should never have been fetched")
        self.assertEqual(seen["v1|7|0"]["verdict"], "reject")
        self.assertIn("neither the first nor the last", seen["v1|7|0"]["reason"])
        self.assertEqual(len(matches), 1)

    def test_a_player_scan_keeps_a_mark_per_search(self):
        """It used to write marks nobody read. Now each of a player's searches
        keeps its own, keyed to the search text, so a repeat costs a page."""
        items = [{"itemId": "v1|1|0", "title": "2024 Topps Chrome Coco Gauff 1/50 tennis", "itemCreationDate": "2026-09-17T00:00:00.000Z"}]

        def listings(_t, _p, _b, on_complete=None, **kw):
            if on_complete: on_complete("Gauff Topps Chrome", "2026-09-17T00:00:00.000Z")
            return iter(items)

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=listings), \
                patch.object(engine, "get_item_details", return_value={"v1|1|0": self.detail(Manufacturer="Topps", Set="2024 Topps Chrome", Sport="Tennis", **{"Player/Athlete": "Coco Gauff"})}):
            engine.run_scan(["Coco Gauff"], ["Topps Chrome"])
            with open(os.path.join(folder, engine.SCAN_CURSOR_FILE)) as f:
                cursors = json.load(f)
        key = engine.scan_cursor_key("Coco Gauff", "Topps Chrome", 0.0, None, [], "Gauff Topps Chrome")
        self.assertEqual(list(cursors), [key])
        self.assertEqual(cursors[key]["newest"], "2026-09-17T00:00:00.000Z")

    def test_the_board_drops_a_card_of_another_sport(self):
        self.assertTrue(engine.other_sport_in_title(self.UFC))
        self.assertEqual(engine.other_sport_in_title("2024 Topps Chrome Tennis Golf Legends Federer 1/1"), "")
        self.assertEqual(engine.other_sport_in_title("2024 Topps Chrome Golfo Federer 1/1"), "")


class ARejectIsOnlyAsPermanentAsItsRule(unittest.TestCase):
    """The 77/77 Shapovalov card was stamped a reject by the old maker rule.
    Fixing the rule alone would never have reached it: a scan skipped every
    reject without looking. Now a reject carries the judge version it was
    made under, and one a newer judge might reverse is judged again."""

    BOOKEND = "2024 Topps Chrome Tennis Denis Shapovalov 1st Pineapple Refractor 77/77"
    MIDRUN = "2024 Topps Chrome Tennis Denis Shapovalov Refractor 7/77"
    UNNUMBERED = "2024 Topps Chrome Tennis Denis Shapovalov Refractor"

    def test_an_old_bare_reject_is_reconsidered_only_for_a_bookend_title(self):
        self.assertFalse(engine.reject_stands("reject", self.BOOKEND))
        self.assertTrue(engine.reject_stands("reject", self.MIDRUN))
        self.assertTrue(engine.reject_stands("reject", self.UNNUMBERED))

    def test_a_versioned_reject_is_reconsidered_by_reason_and_version(self):
        old_maker = {"verdict": "reject", "reason": "manufacturer not in allow-list: ''", "judge": 1}
        old_serial = {"verdict": "reject", "reason": "no serial number (N/M) in the title or specifics", "judge": 1}
        current = {"verdict": "reject", "reason": "manufacturer not in allow-list: ''", "judge": engine.JUDGE_VERSION}
        self.assertFalse(engine.reject_stands(old_maker, self.BOOKEND))
        self.assertTrue(engine.reject_stands(old_serial, self.BOOKEND))
        self.assertTrue(engine.reject_stands(current, self.BOOKEND))
        # a reject with a reason but no version is a version-1 reject
        self.assertFalse(engine.reject_stands({"verdict": "reject", "reason": "manufacturer not in allow-list: 'Topps Chrome'"}, self.BOOKEND))

    def test_a_scan_judges_the_reconsidered_ones_again_and_no_others(self):
        items = [{"itemId": "v1|1|0", "title": self.BOOKEND},
                 {"itemId": "v1|2|0", "title": self.MIDRUN},
                 {"itemId": "v1|3|0", "title": self.UNNUMBERED},
                 {"itemId": "v1|4|0", "title": "2024 Topps Chrome Tennis Coco Gauff 1/25"}]
        seen = {"v1|1|0": "reject", "v1|2|0": "reject", "v1|3|0": "reject",
                "v1|4|0": {"verdict": "reject", "reason": "no serial number (N/M) in the title or specifics", "judge": 1}}
        asked = []

        def details(_token, ids, **kw):
            asked.extend(ids)
            return {i: {"localizedAspects": [{"name": "Sport", "value": "Tennis"}],
                        "price": {"value": "1.00", "currency": "USD"}, "seller": {"username": "s"},
                        "buyingOptions": ["AUCTION"], "itemWebUrl": "https://www.ebay.com/itm/1"} for i in ids}

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=lambda *a, **k: iter(items)), \
                patch.object(engine, "get_item_details", side_effect=details):
            engine.save_state(os.path.join(folder, engine.STATE_FILE), seen)
            matches, checked = engine.run_scan(None, ["Topps Chrome"])
            with open(os.path.join(folder, engine.STATE_FILE)) as f:
                after = json.load(f)
        self.assertEqual(asked, ["v1|1|0"], "only the bookend-titled old reject is worth a call")
        self.assertEqual([m["itemId"] for m in matches], ["v1|1|0"], "the blank-maker card comes through")
        self.assertEqual(after["v1|2|0"], "reject")
        self.assertEqual(after["v1|3|0"], "reject")
        self.assertEqual(after["v1|4|0"]["judge"], 1)

    def test_a_reject_made_now_carries_the_version(self):
        entry = engine.rejected("no serial number (N/M) in the title or specifics")
        self.assertEqual(entry["judge"], engine.JUDGE_VERSION)
        self.assertTrue(engine.reject_stands(entry, self.BOOKEND))

    def test_every_reconsidered_reason_belongs_to_a_version_the_judge_reached(self):
        for version in engine.RECONSIDER_REASONS:
            self.assertLessEqual(version, engine.JUDGE_VERSION)


class EveryCallEarnsItsKeep(unittest.TestCase):
    """The savings made on 17 Sep, each with the completeness it keeps."""

    def page(self, n, start=0, newest="2026-09-17T12:00:00.000Z"):
        return [{"itemId": f"v1|{start + i}|0", "title": f"2024 Topps Chrome Coco Gauff 1/{50 + i} tennis",
                 "itemCreationDate": newest if i == 0 else "2026-09-16T00:00:00.000Z"} for i in range(n)]

    def test_the_walk_stops_when_ebay_says_there_is_no_further_page(self):
        """eBay's total is an estimate that runs high; the last page used to
        be followed by an empty one, a call each. eBay's own "next" is the
        word that is trusted -- not a short page, which eBay does not promise
        to be the last."""
        with patch.object(engine, "search_ebay", return_value=(self.page(150), 1000, False)) as search:
            found = list(engine.iter_listings("token", None, "Topps Chrome"))
        self.assertEqual(len(found), 150)
        self.assertEqual(search.call_count, 1)

        def short_but_more(token, query, limit, offset=0, **kw):
            return (self.page(150, start=offset), 1000, offset < 300)

        with patch.object(engine, "search_ebay", side_effect=short_but_more) as search:
            found = list(engine.iter_listings("token", None, "Topps Chrome"))
        self.assertEqual(len(found), 450)            # three short pages, all walked
        self.assertEqual(search.call_count, 3)

    def test_a_player_is_searched_by_surname_and_by_full_name_only_past_the_cap(self):
        """"Shapovalov Topps Chrome" returns everything "Denis Shapovalov Topps
        Chrome" would; the narrower search is only worth a call when the wider
        one hit the cap and more may lie beyond it."""
        asked = []

        def search(token, query, limit, offset=0, **kw):
            asked.append(query)
            return (self.page(50), 50, False)

        with patch.object(engine, "search_ebay", side_effect=search):
            list(engine.iter_listings("token", "Denis Shapovalov", "Topps Chrome", limit=200))
        self.assertEqual(asked, ["Shapovalov Topps Chrome"])

        asked.clear()

        def search_capped(token, query, limit, offset=0, **kw):
            asked.append(query)
            return (self.page(limit, start=offset), 5000, True)

        with patch.object(engine, "search_ebay", side_effect=search_capped):
            list(engine.iter_listings("token", "Denis Shapovalov", "Topps Chrome", limit=200))
        self.assertEqual(asked, ["Shapovalov Topps Chrome", "Denis Shapovalov Topps Chrome"])

    def scan_twice(self, folder, players, bump_judge=False):
        calls = {"search": 0}

        def search(token, query, limit, offset=0, **kw):
            calls["search"] += 1
            return (self.page(3), 3, False) if offset == 0 else ([], 3, False)

        detail = {"localizedAspects": [{"name": "Manufacturer", "value": "Topps"}, {"name": "Set", "value": "2024 Topps Chrome"},
                                       {"name": "Sport", "value": "Tennis"}, {"name": "Player/Athlete", "value": "Coco Gauff"}],
                  "price": {"value": "1.00", "currency": "USD"}, "seller": {"username": "s"},
                  "buyingOptions": ["AUCTION"], "itemWebUrl": "https://www.ebay.com/itm/1"}
        with patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "search_ebay", side_effect=search), \
                patch.object(engine, "get_item_details", side_effect=lambda t, ids, **k: {i: dict(detail) for i in ids}), \
                patch.object(engine, "consume_api_call", lambda _b="browse": None):
            _, first = engine.run_scan(players, ["Topps Chrome"])
            before = calls["search"]
            if bump_judge:
                with patch.object(engine, "JUDGE_VERSION", engine.JUDGE_VERSION + 1):
                    _, second = engine.run_scan(players, ["Topps Chrome"])
            else:
                _, second = engine.run_scan(players, ["Topps Chrome"])
        return first, second, before, calls["search"] - before

    def test_a_repeat_player_scan_stops_at_its_mark(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second, searches1, searches2 = self.scan_twice(folder, ["Coco Gauff"])
        self.assertEqual(first, 3)
        self.assertEqual(second, 1, "the repeat should see only the newest listing, at the mark")
        self.assertEqual(searches2, 1)

    def test_a_judge_bump_stales_every_mark(self):
        """A mark says everything older is judged -- by that judge. A newer one
        walks past the mark once, so listings its rules could reverse are
        reached without anyone editing a file."""
        with tempfile.TemporaryDirectory() as folder:
            first, second, _, _ = self.scan_twice(folder, None, bump_judge=True)
        self.assertEqual(first, 3)
        self.assertEqual(second, 3)

    def test_a_listing_gone_between_search_and_fetch_is_recorded_gone_at_once(self):
        """eBay answered and said the listing is no longer there: nothing to
        retry, and nothing to hold the set's mark back for."""
        item = {"itemId": "v1|9|0", "title": "2024 Topps Chrome Coco Gauff 1/50 tennis",
                "itemCreationDate": "2026-09-17T00:00:00.000Z"}

        def listings(_t, _p, _b, on_complete=None, **kw):
            if on_complete: on_complete("Topps Chrome tennis", "2026-09-17T00:00:00.000Z")
            return iter([item])

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=listings), \
                patch.object(engine, "get_item_details", return_value={}):
            engine.run_scan(None, ["Topps Chrome"])
            seen = engine.load_state(os.path.join(folder, engine.STATE_FILE))
            cursors = engine.load_state(os.path.join(folder, engine.SCAN_CURSOR_FILE))
        self.assertEqual(seen["v1|9|0"]["verdict"], "reject")
        self.assertIn("no longer serves", seen["v1|9|0"]["reason"])
        self.assertEqual(len(cursors), 1)

    def test_a_refused_fetch_is_retried_then_recorded_as_gone(self):
        item = {"itemId": "v1|9|0", "title": "2024 Topps Chrome Coco Gauff 1/50 tennis",
                "itemCreationDate": "2026-09-17T00:00:00.000Z"}

        def listings(_t, _p, _b, on_complete=None, **kw):
            if on_complete: on_complete("Topps Chrome tennis", "2026-09-17T00:00:00.000Z")
            return iter([item])

        def refused(token, ids, failures=None, **kw):
            if failures is not None:
                failures.update(ids)
            return {}

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=listings), \
                patch.object(engine, "get_item_details", side_effect=refused):
            for attempt in range(1, engine.UNAVAILABLE_TRIES + 1):
                engine.run_scan(None, ["Topps Chrome"])
                seen = engine.load_state(os.path.join(folder, engine.STATE_FILE))
                cursors = engine.load_state(os.path.join(folder, engine.SCAN_CURSOR_FILE))
                with self.subTest(attempt=attempt):
                    if attempt < engine.UNAVAILABLE_TRIES:
                        self.assertEqual(seen["v1|9|0"], {"verdict": "unavailable", "tries": attempt})
                        self.assertEqual(cursors, {}, "the mark moved on past a listing never judged")
                    else:
                        self.assertEqual(seen["v1|9|0"]["verdict"], "reject")
                        self.assertIn("would not return", seen["v1|9|0"]["reason"])
                        self.assertEqual(len(cursors), 1, "gone for good, so the mark can move")

    def test_a_blocked_seller_costs_no_call(self):
        seller = next(iter(engine.BLOCKED_SELLERS))
        item = {"itemId": "v1|5|0", "title": "2024 Topps Chrome Coco Gauff 1/50 tennis",
                "seller": {"username": seller.upper()}}
        self.assertIn("blocked seller", engine.settled_by_summary(item))
        self.assertEqual(engine.settled_by_summary(dict(item, seller={"username": "someone"})), "")
        asked = []
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=lambda *a, **k: iter([item])), \
                patch.object(engine, "get_item_details", side_effect=lambda t, ids, **k: (asked.extend(ids), {})[1]):
            engine.run_scan(None, ["Topps Chrome"])
        self.assertEqual(asked, [])

    def test_the_run_says_where_its_calls_went(self):
        """So the next saving is decided on numbers, not guesses."""
        import io, contextlib
        items = [{"itemId": "v1|1|0", "title": "2024 Topps Chrome Coco Gauff 7/50 tennis"},
                 {"itemId": "v1|2|0", "title": "2024 Topps Chrome Coco Gauff Refractor tennis"}]
        detail = {"localizedAspects": [{"name": "Manufacturer", "value": "Topps"}, {"name": "Set", "value": "2024 Topps Chrome"}],
                  "price": {"value": "1.00", "currency": "USD"}, "seller": {"username": "s"}, "buyingOptions": ["AUCTION"]}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(engine, "_BASE_DIR", folder), \
                patch.object(engine, "get_ebay_token", return_value="token"), \
                patch.object(engine, "iter_listings", side_effect=lambda *a, **k: iter(items)), \
                patch.object(engine, "get_item_details", side_effect=lambda t, ids, **k: {i: dict(detail) for i in ids}), \
                patch.object(engine, "send_digest_email"), \
                patch.dict(os.environ, {"SCAN_BRANDS": "Topps Chrome"}), \
                contextlib.redirect_stdout(out):
            engine.main()
        text = out.getvalue()
        self.assertIn("Detail calls: 1; settled from the search result with no call: 1", text)
        self.assertIn("turned away:", text)
        self.assertIn("neither the first nor the last", text)
        self.assertIn("no serial number", text)



class GradesAreNotSerials(unittest.TestCase):
    """A card grade over an autograph grade looks exactly like N/M."""

    def serial(self, title):
        return engine.extract_serial(title, {})

    def test_the_three_recorded_psa_nines_carry_no_serial(self):
        """Real titles from the record, each kept as the last of a run of 9."""
        for title in (
                "Coco Gauff Signed 2021 Topps Chrome Purple Wave Rookie Auto /199 Psa MINT 9/9 RC",
                "Coco Gauff Signed 2021 Topps Chrome #100 Refractor Rookie Card Auto Psa MINT 9/9"):
            with self.subTest(title=title):
                self.assertEqual(self.serial(title), (None, None))

    def test_a_grader_right_before_the_pair_makes_it_a_grade(self):
        self.assertEqual(self.serial("Sinner 2024 Topps Chrome PSA 10/10 Auto"), (None, None))
        self.assertEqual(self.serial("Gauff Refractor BGS 9.5/10 Gem"), (None, None))

    def test_a_serial_after_a_stated_grade_is_still_a_serial(self):
        """The Seles card from the record: PSA 10, and then the real 1/10.
        The word before 1/10 is "10", not a grade word."""
        self.assertEqual(self.serial(
            "Monica Seles On Card Auto PSA 10 1/10 SSP 2024 Topps Graphite #GS-MSS"), (1, 10))
        self.assertEqual(self.serial("2025 Topps Chrome Sinner Gold 10/10 PSA 10"), (10, 10))
        self.assertEqual(self.serial("Alcaraz Refractor PSA 10 10/10"), (10, 10))

    def test_a_grade_is_stepped_over_to_reach_the_serial_behind_it(self):
        self.assertEqual(self.serial("Gauff BGS 9.5/10 Purple Refractor 1/25"), (1, 25))

    def test_a_decimal_grade_never_yields_its_tail(self):
        """9.5/10 used to read as 5/10."""
        self.assertEqual(self.serial("BGS 9.5/10 Sinner"), (None, None))

    def test_auto_is_not_grade_context(self):
        """"Rookie Auto 5/5" is a real serial after the word auto."""
        self.assertEqual(self.serial("2025 Topps Chrome Rookie Auto 5/5"), (5, 5))

    def test_big_numbers_are_never_a_grade(self):
        self.assertEqual(self.serial("PSA 25/50"), (25, 50))

    def test_the_board_drops_a_recorded_grade_pair(self):
        self.assertEqual(engine.grade_pairs_in(
            "Coco Gauff Signed 2021 Topps Chrome Rookie Auto Psa MINT 9/9"), {"9/9"})
        self.assertEqual(engine.grade_pairs_in(
            "Monica Seles On Card Auto PSA 10 1/10 SSP"), set())



class PlayersReadOffTheTitle(unittest.TestCase):
    """When eBay's Player field is blank, the name is usually in the title."""

    # the twelve recorded cards that read "Unknown player", and what they should say
    RECORDED = {
        "2013 Ace Personal Best Career Ranking Arvane Rezai 1/15 #1 Card signed auto": "Arvane Rezai",
        'VINCE SPADEA "SILVER BASE CARD 100 /100" ACE SIGNATURE SERIES 2005': "Vince Spadea",
        'NICOLAS MASSU "SILVER BASE CARD #001/100" ACE SIGNATURE SERIES 2005': "Nicolas Massu",
        "2024 Topps Graphite Red Crystalline Refractor Stefanos Tsitsipas Relic Card 1/1!": "Stefanos Tsitsipas",
        "topps graphite tennis 3 Card Kayla Day Lot. Relic, Relic Auto and 15/15": "Kayla Day",
        "VICTORIA J KASINTSEVA 2024 TRA-VJK Topps Graphite Tour Relic autograph card 1/15": "Victoria J Kasintseva",
        "MIOMIR KECMANOVIC 2024 GS-MKC Topps Graphite AUTOGRAPH card in new holder 1/15": "Miomir Kecmanovic",
        "2024 Topps Graphite Tennis Daniel Rincon Rookie Blue On Card Auto 50/50 RC SP": "Daniel Rincon",
        "2024 Topps Graphite Tennis Abdullah Shelbayh Blue On Card Rookie Auto 01/50 RC": "Abdullah Shelbayh",
        "Mirra Andreeva 2024 Topps Royalty Tennis BASE CARD GOLD 01/10 #39 RC Russia /10": "Mirra Andreeva",
        "2024 TOPPS ROYALTY TENNIS FERNANDA CONTRERAS ROOKIE ON CARD AUTOGRAPH GREEN 5/5": "Fernanda Contreras",
        "2024 Topps Royalty Tennis Jamie Murray On Card Auto Relic /10 Superior Signature": "Jamie Murray",
    }

    def test_every_recorded_unknown_is_read_off_its_title(self):
        for title, want in self.RECORDED.items():
            with self.subTest(title=title[:50]):
                self.assertEqual(engine.player_from_title(title), want)

    def test_ebays_own_field_still_wins(self):
        self.assertEqual(engine.get_player({"Player/Athlete": ["Iga Swiatek"]},
                                           "2024 Topps Chrome Coco Gauff Auto 1/1"), "Iga Swiatek")

    def test_signed_by_is_read_when_player_is_blank(self):
        """The Ace Authentic autos: eBay had the name, under a different key."""
        self.assertEqual(engine.get_player({"Signed By": ["Vince Spadea"]}, "some title"), "Vince Spadea")

    def test_a_known_name_is_preferred_over_the_walk(self):
        title = "Career Ranking Card Coco Gauff Auto 1/1"
        self.assertEqual(engine.player_from_title(title, known=["Coco Gauff"]), "Coco Gauff")

    def test_a_title_with_no_name_yields_nothing_not_a_shrug(self):
        for title in ("2024 Topps Chrome Tennis Blue Refractor 1/25 RC",
                      "TOPPS ROYALTY GOLD RELIC AUTO 5/5 SSP", ""):
            with self.subTest(title=title):
                self.assertEqual(engine.player_from_title(title), "")
                self.assertEqual(engine.get_player({}, title), "")

    def test_set_codes_and_abbreviations_are_not_names(self):
        self.assertEqual(engine.player_from_title("GS-MKC TRA-VJK RC SSP USA 1/10"), "")

    def test_an_ordinal_does_not_shed_a_false_name(self):
        """'20th' used to split into '20' and 'th', and 'Th Anniv' came back."""
        self.assertEqual(engine.player_from_title(
            "NETPRO 20th Anniv 2003 Venus Williams Sealed Bag 1/1 Rookie Card With COA"),
            "Venus Williams")

    def test_a_pair_card_names_the_first_player(self):
        """'Royalty-Prodigious Pair Coco Gauff,Jessica Pegula' once ran four words
        together, tripped the too-long rule and fell through to Pegula."""
        title = "2024 Topps Royalty-Prodigious Pair Coco Gauff,Jessica Pegula Dual Auto 1/25"
        self.assertEqual(engine.player_from_title(title), "Coco Gauff")

    def test_a_slash_between_two_players_names_the_first(self):
        self.assertEqual(engine.player_from_title(
            "2024 TOPPS ROYALTY MADISON KEYS/BJORN FRATANGELO DUAL ON CARD AUTO 1/25"), "Madison Keys")

    def test_a_card_word_hyphenated_onto_a_name_keeps_the_name(self):
        self.assertEqual(engine.player_from_title(
            "2025 Topps Chrome Autograph Rookie Card-Elina Avanesyan #CA-EAN 1/75"), "Elina Avanesyan")

    def test_two_players_with_nothing_between_them_is_no_guess(self):
        """Four names in a row could be either player; the walk says nothing and
        leaves it to the known-names pass, which reads the recorded name."""
        title = "Coco Gauff Venus Williams 2025 Topps Chrome Tennis DUAL REFRACTOR 1/5"
        self.assertEqual(engine.player_from_title(title), "")
        self.assertEqual(engine.player_from_title(title, known=["Coco Gauff"]), "Coco Gauff")

    def test_dash_runs_are_separators(self):
        self.assertEqual(engine.player_from_title(
            "2026 TOPPS GRAPHITE TENNIS----DANIIL MEDVEDEV---RELIC CARD---15/15"), "Daniil Medvedev")
        self.assertEqual(engine.player_from_title(
            "2024 Topps Royalty --DENIS SHAPOVALOV--REGALIA RELICS-10/10"), "Denis Shapovalov")

    def test_insert_names_are_not_players(self):
        self.assertEqual(engine.player_from_title(
            "2025 Topps Chrome Yoshihito Nishioka Frozen Fractor Tennis Card 1/1"), "Yoshihito Nishioka")
        self.assertEqual(engine.player_from_title(
            "2024 Topps Royalty Collection MONICA SELES ROYAL DECREE ON CARD AUTO 1/10"), "Monica Seles")

    def test_mixed_case_is_kept_as_written(self):
        self.assertEqual(engine.player_from_title("2024 Topps Chrome John McEnroe Auto 1/5"), "John McEnroe")

    def test_the_board_fills_the_blank_players(self):
        xlsx = os.path.join(HERE, "results", engine.OUTPUT_XLSX)
        if not os.path.exists(xlsx):
            self.skipTest("no results/tennis_cards_verified.xlsx yet -- run a scan first")
        board = engine.build_board(xlsx)
        self.assertNotIn("Unknown player", {c["player"] for c in board},
                         "the board still shows the old shrug")


if __name__ == "__main__":
    unittest.main(verbosity=2)
