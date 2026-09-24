import datetime as dt
import unittest

from watcher import ics
from tests.test_diff import HEAD, TAGS, row


class IcsTests(unittest.TestCase):
    def test_times_from_cell_override_slot(self):
        slot = ((9, 30), (12, 30))
        self.assertEqual(ics.event_times("partiel MM1 - 9h-11h + ISI5 11h-12h", slot), ((9, 0), (12, 0)))
        self.assertEqual(ics.event_times("partiel GO3 (I): 14H-16H", ((14, 0), (17, 0))), ((14, 0), (16, 0)))
        self.assertEqual(ics.event_times("Rentrée 10h (C) A20004", slot), ((10, 0), (12, 30)))
        self.assertEqual(ics.event_times("M2-INF2 (léa)", slot), slot)

    def test_extract_events_per_program(self):
        grid = HEAD + [row("mercredi 11 novembre 2026", "Armistice", "", "Armistice", ""),
                       row("jeudi 12 novembre 2026", "MM1", "", "", "ISI1 - Zoom")]
        grid[5][3] = "D.01.01"
        evs = ics.extract_events(grid, TAGS, "t")
        sbi = [e for e in evs if e["tag"] == "SBI"]
        ibi = [e for e in evs if e["tag"] == "IBI"]
        self.assertEqual([(e["summary"], e["all_day"]) for e in sbi], [("Armistice", True), ("ISI1 - Zoom", False)])
        self.assertEqual(sbi[1]["location"], "Zoom")
        self.assertEqual((ibi[1]["summary"], ibi[1]["location"]), ("MM1", "D.01.01"))
        self.assertEqual(ibi[1]["day"], dt.date(2026, 11, 12))

    def test_calendar_is_valid_and_folded(self):
        grid = HEAD + [row("lundi 5 octobre 2026", "Cours au nom très long, avec ; virgules et accents éèà" * 2, "", "", "")]
        cal = ics.build_calendar(ics.extract_events(grid, TAGS, "t"), "M2 IBI", "https://x")
        self.assertTrue(all(len(line.encode()) <= 75 for line in cal.split("\r\n")))
        self.assertIn("DTSTART:20261005T073000Z", cal)  # 9h30 heure de Paris (été)
        self.assertIn("\\;", cal)


if __name__ == "__main__":
    unittest.main()
