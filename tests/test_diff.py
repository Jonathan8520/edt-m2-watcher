import unittest

from watcher import diff, discord

TAGS = ["IBI", "SBI"]
HEAD = [["IBI 2026-2027", "", "", "", "SBI 2026-2027", "", "", "", "IBI", "Salle A1401"],
        ["", "", "", "", "", "", "", "", "SBI", "Salle A1410"],
        ["Session 1", "9h30-12h30", "14h-17h", "", "Session 1", "9h30-12h30", "14h-17h"],
        ["Date", "", "", "", "Date"]]


def row(date, a, b, c, d):
    return [date, a, b, "", date, c, d]


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

    def test_messages_respect_discord_limits(self):
        items = [{"kind": "cell", "tags": ["SBI"], "context": f"lundi {i} octobre 2026 · 9h30-12h30",
                  "old": "A" * 150, "new": "B" * 150} for i in range(400)]
        report = {"status": "changes", "title": "T",
                  "events": [{"type": "cells", "tab": f"tab{k}", "url": "https://x", "items": items} for k in range(12)]}
        msgs = discord.build_messages(report, {"SBI": "123"}, None)
        self.assertTrue(msgs[0]["content"].startswith("<@&123>"))
        for m in msgs:
            self.assertLessEqual(len(m["embeds"]), 10)
            self.assertLessEqual(sum(len(e["title"]) + len(e.get("description", "")) for e in m["embeds"]), 6000)
            self.assertTrue(all(len(e["description"]) <= 4096 for e in m["embeds"]))
        self.assertEqual(sum(len(m["embeds"]) for m in msgs), 12)

    def test_markdown_and_mentions_are_escaped(self):
        self.assertEqual(discord.esc("**x** <@&1>"), "\\*\\*x\\*\\* \\<@&1\\>")


if __name__ == "__main__":
    unittest.main()
