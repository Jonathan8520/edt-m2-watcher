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


class RecapSynthesisTests(unittest.TestCase):
    def test_move_and_grouped_partiels(self):
        from watcher import recap
        items = [
            {"kind": "cell", "tags": ["IBI"], "context": "lundi 5 octobre 2026 · 9h30-12h30", "old": "MM1 (Rébecca)", "new": ""},
            {"kind": "cell", "tags": ["IBI"], "context": "mardi 15 décembre 2026 · 14h-17h", "old": "ISI5 (Rébecca)", "new": "MM1 (Rébecca)"},
            {"kind": "cell", "tags": ["IBI"], "context": "lundi 18 janvier 2027 · 9h30-12h30",
             "old": "partiel MM1 (R) - 9h-11h + ISI5 part 1 (R) 11h-12h", "new": "partiel MM1 (R) - 9h-11h"},
        ]
        report = {"status": "changes", "events": [{"type": "cells", "tab": "t", "items": items}]}
        self.assertEqual(recap.synthesis(report), [
            "- partiel ISI5 : retiré le 18 janv. (11h-12h)",
            "- ISI5 : retiré le 15 déc. (après-midi)",
            "- MM1 : retiré le 5 oct. (matin) ; ajouté le 15 déc. (après-midi)"])

    def test_partiel_written_outside_slot_columns_becomes_event(self):
        grid = HEAD + [row("lundi 25 janvier 2027", "M2-INF2", "", "", "")]
        grid[4][3] = "partiel M2-INF2 (NB) - 17H30h-19h"
        evs = [e for e in ics.extract_events(grid, TAGS, "t") if e["tag"] == "IBI"]
        self.assertEqual([(e["summary"], e["start"], e["end"]) for e in evs],
                         [("M2-INF2", (9, 30), (12, 30)), ("partiel M2-INF2 (NB) - 17H30h-19h", (17, 30), (19, 0))])
