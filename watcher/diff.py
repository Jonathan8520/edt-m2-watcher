"""Comparaison de deux versions d'un onglet et construction des changements lisibles."""
import difflib
import re

DATE_RE = re.compile(
    r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+\d{1,2}(er)?\s+"
    r"(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\b",
    re.I)
SLOT_RE = re.compile(r"^\d{1,2}\s*h\s*\d{0,2}\s*-\s*\d{1,2}\s*h\s*\d{0,2}$", re.I)


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def cell(grid, r, c):
    if 0 <= r < len(grid) and c < len(grid[r]):
        return grid[r][c]
    return ""


def has_tag(tag, text):
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(tag)}(?![A-Za-z0-9])", text or "", re.I) is not None


class Layout:
    """Découpe l'onglet en blocs côte à côte (ex. IBI | SBI) à partir d'une ligne d'en-tête."""

    def __init__(self, grid, tags):
        self.tags = tags
        self.blocks = [(0, "")]
        for r, row in enumerate(grid[:10]):
            starts = [(c, v) for c, v in enumerate(row) if v and (not tags or any(has_tag(t, v) for t in tags))]
            if len(starts) >= 2:
                self.blocks = starts
                break

    def block_index(self, c):
        idx = 0
        for i, (start, _) in enumerate(self.blocks):
            if start <= c:
                idx = i
        return idx

    def span(self, idx):
        start = 0 if idx == 0 else self.blocks[idx][0]
        end = self.blocks[idx + 1][0] if idx + 1 < len(self.blocks) else 10 ** 6
        return start, end

    def title(self, idx):
        return self.blocks[idx][1]

    def is_program(self, idx):
        """Vrai tableau de programme (« SBI 2026-2027 ») ; faux pour une légende titrée juste « IBI »."""
        title = self.title(idx).strip().upper()
        return bool(title) and title not in {t.upper() for t in self.tags}


def _block_cells(row, span):
    return row[span[0]:span[1]]


def resolve_tags(layout, idx, rows):
    """Programmes concernés par un changement dans le bloc idx (rows = versions avant/après de la ligne)."""
    span = layout.span(idx)
    cells = [c for row in rows for c in _block_cells(row, span)]
    found = {t for t in layout.tags if any(has_tag(t, c) for c in cells)}
    block_tags = {t for t in layout.tags if has_tag(t, layout.title(idx))}
    if block_tags and layout.is_program(idx):
        dated = any(DATE_RE.search(c) for c in cells)
        others = any(v for row in rows for c, v in enumerate(row) if v and not span[0] <= c < span[1])
        if dated or others:
            return sorted(block_tags | found)
    if found:
        return sorted(found)
    return sorted(layout.tags)


def row_date(row, span):
    for v in _block_cells(row, span):
        m = DATE_RE.search(v)
        if m:
            return v
    return None


def slot_label(grid, r, c):
    for rr in range(r - 1, -1, -1):
        v = cell(grid, rr, c)
        if SLOT_RE.match(v):
            return v
    return None


COURSE_RE = re.compile(r"\b(?:M2-)?[A-Z]{2,4}\d{1,2}\b")


def column_header(grid, layout, idx, r, c):
    """Titre de la colonne c (« Exam Date », « Enseignant »…), ou None s'il n'y en a pas."""
    start, end = layout.span(idx)
    for rr in range(r - 1, -1, -1):
        row = grid[rr] if rr < len(grid) else []
        if cell(grid, rr, start) or not any(row[start:end]):
            continue
        v = cell(grid, rr, c)
        if v and not DATE_RE.search(v) and len(v) <= 30:
            return v.rstrip(" :")
        return None
    return None


def row_key(row, span, skip):
    """Ce qui identifie la ligne : le code du cours s'il y en a un, sinon la première cellule remplie."""
    cells = [v for c, v in enumerate(row[span[0]:span[1]], span[0]) if v and c not in skip]
    for v in cells:
        m = COURSE_RE.search(v)
        if m:
            return m.group(0)
    return cells[0][:40] if cells else ""


def _row_key(row):
    for v in row:
        if DATE_RE.search(v):
            return v
    return next((v for v in row if v), "")


def align(old, new):
    """Aligne les lignes : renvoie des tuples (i_old|None, j_new|None)."""
    pairs = []
    sm = difflib.SequenceMatcher(None, [tuple(r) for r in old], [tuple(r) for r in new], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        if op == "delete":
            pairs += [(i, None) for i in range(i1, i2)]
        elif op == "insert":
            pairs += [(None, j) for j in range(j1, j2)]
        elif i2 - i1 == j2 - j1:
            pairs += list(zip(range(i1, i2), range(j1, j2)))
        else:
            sub = difflib.SequenceMatcher(None, [_row_key(old[i]) for i in range(i1, i2)],
                                          [_row_key(new[j]) for j in range(j1, j2)], autojunk=False)
            for sop, a1, a2, b1, b2 in sub.get_opcodes():
                if sop in ("equal", "replace") and a2 - a1 == b2 - b1:
                    pairs += list(zip(range(i1 + a1, i1 + a2), range(j1 + b1, j1 + b2)))
                else:
                    pairs += [(i1 + a, None) for a in range(a1, a2)]
                    pairs += [(None, j1 + b) for b in range(b1, b2)]
    return pairs


def diff_tab(old, new, tags):
    """Liste d'items de changement pour un onglet, dans l'ordre du sheet."""
    layout = Layout(new if new else old, tags)
    items = []
    for i, j in align(old, new):
        orow = old[i] if i is not None else []
        nrow = new[j] if j is not None else []
        r = j if j is not None else i
        width = max(len(orow), len(nrow))
        for idx in range(len(layout.blocks)):
            span = layout.span(idx)
            ocells, ncells = _block_cells(orow, span), _block_cells(nrow, span)
            if i is None or j is None:
                cells = ncells if j is not None else ocells
                if any(cells):
                    items.append({"kind": "add" if j is not None else "del",
                                  "tags": resolve_tags(layout, idx, [orow, nrow]),
                                  "cells": [v for v in cells if v], "order": r})
                continue
            changed = [c for c in range(span[0], min(span[1], width)) if cell(old, i, c) != cell(new, j, c)]
            if not changed:
                continue
            tags_ = resolve_tags(layout, idx, [orow, nrow])
            date = row_date(nrow, span) or row_date(orow, span)
            if date:
                for c in changed:
                    slot = slot_label(new, j, c) or column_header(new, layout, idx, j, c) or "note"
                    items.append({"kind": "cell", "tags": tags_, "context": f"{date} · {slot}",
                                  "old": cell(old, i, c), "new": cell(new, j, c), "order": j})
            else:
                end = min(span[1], width)
                row_cells = []
                for c in range(span[0], end):
                    o, n = cell(old, i, c), cell(new, j, c)
                    if o != n:
                        row_cells.append({"old": o, "new": n, "header": column_header(new, layout, idx, j, c)})
                    elif n:
                        row_cells.append({"value": n})
                changed_cols = {c for c in range(span[0], end) if cell(old, i, c) != cell(new, j, c)}
                items.append({"kind": "row", "tags": tags_, "cells": row_cells, "order": j,
                              "key": row_key(nrow or orow, span, changed_cols)})
    items.sort(key=lambda it: it["order"])
    for it in items:
        it.pop("order")
    return items


def all_tags_in(grid, tags):
    return sorted({t for row in grid for v in row for t in tags if has_tag(t, v)}) or sorted(tags)
