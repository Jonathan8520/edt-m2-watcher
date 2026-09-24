import random
import unittest

from watcher import diff, discord
from watcher.__main__ import parse_pings

TAGS = ["IBI", "SBI"]
HEAD = [["IBI 2026-2027", "", "", "", "SBI 2026-2027", "", "", "", "IBI", "Salle A1401"],
        ["", "", "", "", "", "", "", "", "SBI", "Salle A1410"],
        ["Session 1", "9h30-12h30", "14h-17h", "", "Session 1", "9h30-12h30", "14h-17h"],
        ["Date", "", "", "", "Date"]]
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]
COURSES = ["ISI1 (Claire)", "INF4", "GO3 (Sofia)", "REC1 (Sofia)", "MM1", "partiel ?", "", ""]


def row(date, a, b, c, d):
    return [date, a, b, "", date, c, d]


def schedule(rng, n=30):
    grid = [r[:] for r in HEAD]
    for k in range(n):
        date = f"{DAYS[k % 5]} {k + 1} octobre 2026"
        grid.append(row(date, *(rng.choice(COURSES) for _ in range(4))))
    grid += [[], ["Soutenances:", "???"]]
    return grid


def trim(grid):
    out = []
    for r in grid:
        r = r[:]
        while r and not r[-1]:
            r.pop()
        out.append(r)
    while out and not out[-1]:
        out.pop()
    return out


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.old = HEAD + [row("lundi 5 octobre 2026", "MM1", "REC1", "REC1", "ISI8"),
                           row("mardi 6 octobre 2026", "INF2", "INF2", "INF4", "INF4")]

    def test_cell_change_is_tagged_and_contextualized(self):
        new = [r[:] for r in self.old]
        new[5][5] = "ISI1"
        items = diff.diff_tab(self.old, new, TAGS)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["kind"], it["tags"], it["old"], it["new"]), ("cell", ["SBI"], "INF4", "ISI1"))
        self.assertEqual(it["context"], "mardi 6 octobre 2026 · 9h30-12h30")

    def test_inserted_row_does_not_shift_everything(self):
        new = self.old[:5] + [row("mercredi 7 octobre 2026", "X", "", "Y", "")] + self.old[5:]
        items = diff.diff_tab(self.old, new, TAGS)
        self.assertEqual([i["kind"] for i in items], ["add", "add"])
        self.assertEqual([i["tags"] for i in items], [["IBI"], ["SBI"]])

    def test_legend_row_uses_row_mention(self):
        new = [r[:] for r in self.old]
        new[1][9] = "Salle C1420"
        items = diff.diff_tab(self.old, new, TAGS)
        self.assertEqual((items[0]["kind"], items[0]["tags"]), ("row", ["SBI"]))

    def test_unattributed_row_pings_everyone(self):
        old = self.old + [[], ["Soutenances:", "???"]]
        new = self.old + [[], ["Soutenances:", "15/06"]]
        self.assertEqual(diff.diff_tab(old, new, TAGS)[0]["tags"], ["IBI", "SBI"])


class FuzzTests(unittest.TestCase):
    """Mutations aléatoires : chaque cellule modifiée doit être signalée, une seule fois, avec le bon bloc."""

    def test_random_cell_edits_are_all_reported(self):
        rng = random.Random(42)
        slots = {1: "9h30-12h30", 2: "14h-17h", 5: "9h30-12h30", 6: "14h-17h"}
        for _ in range(1000):
            old = trim(schedule(rng))
            new = [r[:] + [""] * (7 - len(r)) if 4 <= i < len(old) - 2 else r[:] for i, r in enumerate(old)]
            for _ in range(rng.randint(1, 8)):
                r, c = rng.randrange(4, len(old) - 2), rng.choice(list(slots))
                new[r][c] = rng.choice(["NOUVEAU", "Annulé", "", "ISI7 (Raphael)"])
            new = trim(new)
            truth = {(old[r][0], slots[c], "IBI" if c < 4 else "SBI"): (diff.cell(old, r, c), diff.cell(new, r, c))
                     for r in range(len(old)) for c in slots if diff.cell(old, r, c) != diff.cell(new, r, c)}
            got = {}
            for it in diff.diff_tab(old, new, TAGS):
                self.assertEqual((it["kind"], len(it["tags"])), ("cell", 1))
                key = (*it["context"].split(" · "), it["tags"][0])
                self.assertNotIn(key, got, "changement signalé deux fois")
                got[key] = (it["old"], it["new"])
            self.assertEqual(got, truth)

    def test_random_row_insert_or_delete(self):
        rng = random.Random(7)
        for _ in range(300):
            old = trim(schedule(rng))
            new = [r[:] for r in old]
            pos = rng.randrange(5, len(old) - 3)
            if rng.random() < 0.5:
                new.insert(pos, row("samedi 31 octobre 2026", "AJOUT", "", "AJOUT", "AJOUT"))
                kinds = {"add"}
            else:
                del new[pos]
                kinds = {"del"}
            items = diff.diff_tab(old, new, TAGS)
            self.assertTrue(items)
            self.assertEqual({i["kind"] for i in items}, kinds)
            self.assertLessEqual(len(items), 2)

    def test_identical_grids_report_nothing(self):
        rng = random.Random(1)
        for _ in range(100):
            g = trim(schedule(rng))
            self.assertEqual(diff.diff_tab(g, [r[:] for r in g], TAGS), [])


class DiscordTests(unittest.TestCase):
    def test_messages_respect_discord_limits(self):
        items = [{"kind": "cell", "tags": ["SBI"], "context": f"lundi {i} octobre 2026 · 9h30-12h30",
                  "old": "A" * 150, "new": "B" * 150} for i in range(400)]
        report = {"status": "changes", "title": "T",
                  "events": [{"type": "cells", "tab": f"tab{k}", "url": "https://x", "items": items} for k in range(12)]}
        msgs = discord.build_messages(report, {"SBI": ("role", "123")}, None)
        self.assertTrue(msgs[0]["content"].startswith("<@&123>"))
        for m in msgs:
            self.assertLessEqual(len(m["embeds"]), 10)
            self.assertLessEqual(sum(discord._embed_len(e) for e in m["embeds"]), 6000)
            self.assertTrue(all(len(e["description"]) <= 4096 for e in m["embeds"]))
        self.assertEqual(sum(len(m["embeds"]) for m in msgs), 12)

    def test_user_ping_is_deduplicated(self):
        pings = parse_pings("IBI=<@123456789012345678>, SBI=<@123456789012345678>")
        self.assertEqual(pings["SBI"], ("user", "123456789012345678"))
        report = {"status": "changes", "title": "T", "events": [{"type": "cells", "tab": "t", "url": "https://x",
                  "items": [{"kind": "add", "tags": ["IBI", "SBI"], "cells": ["x"]}]}]}
        msg = discord.build_messages(report, pings)[0]
        self.assertEqual(msg["content"], "<@123456789012345678>")
        self.assertEqual(msg["allowed_mentions"]["users"], ["123456789012345678"])

    def test_parse_pings_formats(self):
        self.assertEqual(parse_pings("IBI=<@&1>,SBI=22"), {"IBI": ("role", "1"), "SBI": ("role", "22")})

    def test_markdown_and_mentions_are_escaped(self):
        self.assertEqual(discord.esc("**x** <@&1>"), "\\*\\*x\\*\\* \\<@&1\\>")


if __name__ == "__main__":
    unittest.main()


class CalendarLinkTests(unittest.TestCase):
    def test_link_sent_after_all_embeds_silently(self):
        items = [{"kind": "cell", "tags": ["IBI", "SBI"], "context": f"lundi {i} octobre 2026 · 9h30-12h30",
                  "old": "A" * 150, "new": "B" * 150} for i in range(400)]
        report = {"status": "changes", "title": "T",
                  "events": [{"type": "cells", "tab": f"tab{k}", "url": "https://x", "items": items} for k in range(3)]}
        links = {"SBI": "https://gist.example/raw/M2-SBI.ics", "IBI": "https://gist.example/raw/M2-IBI.ics"}
        msgs = discord.build_messages(report, {}, None, None, links, "Phrase.")
        last = msgs[-1]
        self.assertNotIn("embeds", last)
        self.assertEqual(last["content"], "[Ajouter l'emploi du temps IBI à mon agenda](<https://gist.example/raw/M2-IBI.ics>)\n"
                                          "[Ajouter l'emploi du temps SBI à mon agenda](<https://gist.example/raw/M2-SBI.ics>)")
        self.assertEqual((last["flags"], last["_optional"]), (4 | 4096, True))
        self.assertFalse(any("agenda" in e.get("description", "") for m in msgs[:-1] for e in m["embeds"]))
        for m in msgs[:-1]:
            self.assertLessEqual(sum(discord._embed_len(e) for e in m["embeds"]), 6000)
