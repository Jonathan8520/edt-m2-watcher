"""Export iCalendar (.ics) du planning : un calendrier par programme (IBI, SBI)."""
import datetime as dt
import hashlib
import re
from zoneinfo import ZoneInfo

from . import diff

PARIS = ZoneInfo("Europe/Paris")
MONTHS = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
          "aout": 8, "août": 8, "aoüt": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12}
DATE_RE = re.compile(r"(\d{1,2})(?:er)?\s+([a-zéûü]+)\s+(\d{4})", re.I)
RANGE_RE = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?\s*h?\s*-\s*(\d{1,2})\s*h\s*(\d{2})?", re.I)
TIME_RE = re.compile(r"(?<![\w-])(\d{1,2})\s*h\s*(\d{2})?(?![\w-])", re.I)
ROOM_RE = re.compile(r"\b(?:[A-Z]\d{4,5}|[A-Z]\.\d{1,2}\.\d{1,2})\b")
HOLIDAY_RE = re.compile(r"^(armistice|p[âa]ques|ascension|pentec[ôo]te|f[ée]ri[ée]|vacances|no[ëe]l|toussaint)\b", re.I)


def parse_date(text):
    m = DATE_RE.search(text)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    return dt.date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))


def _hm(h, m):
    return int(h), int(m or 0)


def slot_times(label):
    m = RANGE_RE.search(label or "")
    return (_hm(m.group(1), m.group(2)), _hm(m.group(3), m.group(4))) if m else None


def explicit_range(text):
    """Plage horaire écrite dans la cellule, si elle est plausible."""
    ranges = [(_hm(a, b), _hm(c, d)) for a, b, c, d in RANGE_RE.findall(text or "")]
    ranges = [r for r in ranges if 7 <= r[0][0] <= 21 and r[0] < r[1] <= (22, 0)]
    return (min(r[0] for r in ranges), max(r[1] for r in ranges)) if ranges else None


def event_times(text, slot):
    """Horaires précisés dans la cellule (« partiel … 14h-16h », « Rentrée 10h ») sinon ceux du créneau."""
    ranges = [(_hm(a, b), _hm(c, d)) for a, b, c, d in RANGE_RE.findall(text)]
    ranges = [r for r in ranges if 7 <= r[0][0] <= 21 and r[0] < r[1] <= (22, 0)]
    if ranges:
        return min(r[0] for r in ranges), max(r[1] for r in ranges)
    rest = RANGE_RE.sub(" ", text)
    singles = [_hm(h, m) for h, m in TIME_RE.findall(rest)]
    singles = [t for t in singles if slot[0] <= t < slot[1]]
    if len(singles) == 1:
        return singles[0], slot[1]
    return slot


def legend_rooms(grid, tags):
    rooms = {}
    for row in grid:
        for c, v in enumerate(row[:-1]):
            if v.upper() in tags and row[c + 1].lower().startswith("salle"):
                rooms[v.upper()] = row[c + 1]
    return rooms


def extract_events(grid, tags, tab_name):
    layout = diff.Layout(grid, tags)
    rooms = legend_rooms(grid, tags)
    events = []
    for idx in range(len(layout.blocks)):
        block_tags = [t for t in tags if diff.has_tag(t, layout.title(idx))]
        if len(block_tags) != 1:
            continue
        tag = block_tags[0]
        start, end = layout.span(idx)
        for r, row in enumerate(grid):
            date_cell = diff.row_date(row, (start, end))
            day = parse_date(date_cell) if date_cell else None
            if not day:
                continue
            slots, notes, row_room = [], [], None
            for c in range(start, min(end, len(row))):
                v = row[c]
                if not v or v == date_cell:
                    continue
                label = diff.slot_label(grid, r, c)
                times = slot_times(label)
                if times:
                    slots.append((label, times, v))
                elif ROOM_RE.fullmatch(v):
                    row_room = v
                elif explicit_range(v):
                    # ex. « partiel M2-INF2 (NB) - 17H30h-19h » écrit hors des colonnes de créneau
                    slots.append((f"col. {diff.col_letter(c)}", explicit_range(v), v))
                else:
                    notes.append(v)
            holiday_done = False
            for label, times, text in slots:
                base = {"tag": tag, "summary": text, "tab": tab_name, "calendar": f"M2 {layout.title(idx)}"}
                if HOLIDAY_RE.match(text):
                    if not holiday_done:
                        events.append({**base, "day": day, "all_day": True, "uid_key": f"{tag}|{day}|jour"})
                        holiday_done = True
                    continue
                room = ROOM_RE.search(text)
                if room:
                    location = room.group(0)
                elif "zoom" in text.lower():
                    location = "Zoom"
                else:
                    location = row_room or rooms.get(tag, "")
                s, e = event_times(text, times)
                events.append({**base, "day": day, "all_day": False, "start": s, "end": e, "location": location,
                               "slot": label, "notes": notes, "uid_key": f"{tag}|{day}|{label}"})
    return events


def _escape(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    out, current = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((current + ch).encode("utf-8")) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return "\r\n ".join(out)


def _utc(day, hm):
    local = dt.datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=PARIS)
    return local.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_calendar(events, name, sheet_url, stamp=None):
    stamp = (stamp or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jonathan8520//edt-m2-watcher//FR", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_escape(name)}", "X-WR-TIMEZONE:Europe/Paris",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for ev in sorted(events, key=lambda e: (e["day"], e.get("start", (0, 0)))):
        uid = hashlib.sha1(ev["uid_key"].encode()).hexdigest()[:20] + "@edt-m2-watcher"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
        if ev["all_day"]:
            lines += [f"DTSTART;VALUE=DATE:{ev['day']:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{ev['day'] + dt.timedelta(days=1):%Y%m%d}", "TRANSP:TRANSPARENT"]
            desc = f"{ev['tab']}\n{sheet_url}"
        else:
            lines += [f"DTSTART:{_utc(ev['day'], ev['start'])}", f"DTEND:{_utc(ev['day'], ev['end'])}"]
            if ev["location"]:
                lines.append(f"LOCATION:{_escape(ev['location'])}")
            desc = f"{ev['tab']} · créneau {ev['slot']}"
            if ev["notes"]:
                desc += "\nNote : " + ", ".join(ev["notes"])
            desc += f"\n{sheet_url}"
        lines += [f"SUMMARY:{_escape(ev['summary'])}", f"DESCRIPTION:{_escape(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
