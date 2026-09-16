#!/usr/bin/env python3
"""
Checks the rules that decide which cards the engine keeps.

    py test_engine.py            # all of them
    py test_engine.py -v         # naming each one

Standard library only, so it needs nothing beyond what the engine already
uses. The cases in samples/cases.json are real cards the owner supplied, so
the serial tests below run against those too.
"""

import os
import json
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
