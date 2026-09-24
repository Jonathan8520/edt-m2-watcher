"""Mise en forme des changements et envoi au webhook Discord."""
import difflib
import json
import re
from collections import OrderedDict
import time
import urllib.error
import urllib.request

DESC_MAX = 3800      # limite Discord : 4096 par description
MESSAGE_MAX = 5800   # limite Discord : 6000 caractères d'embeds par message
EMBEDS_MAX = 10
COLOR = 0xF1C40F
MONTHS_SHORT = {"janvier": "janv.", "février": "févr.", "fevrier": "févr.", "mars": "mars", "avril": "avr.", "mai": "mai",
                "juin": "juin", "juillet": "juil.", "août": "août", "aout": "août", "septembre": "sept.",
                "octobre": "oct.", "novembre": "nov.", "décembre": "déc.", "decembre": "déc."}


def esc(text, limit=180):
    text = " / ".join(part for part in (text or "").split("\n") if part)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return re.sub(r"([\\*_~`|\[\]<>#])", r"\\\1", text)


def tag_prefix(tags):
    return "**" + "/".join(tags) + "** · " if tags else ""


def format_item(it):
    empty = "*(vide)*"
    if it["kind"] == "cell":
        old = f"~~{esc(it['old'])}~~" if it["old"] else empty
        new = f"**{esc(it['new'])}**" if it["new"] else empty
        return f"{tag_prefix(it['tags'])}{esc(it['context'], 80)} : {old} → {new}"
    if it["kind"] == "row":
        parts = []
        for c in it["cells"]:
            if "value" in c:
                parts.append(esc(c["value"], 40))
            else:
                old = f"~~{esc(c['old'])}~~" if c["old"] else empty
                new = f"**{esc(c['new'])}**" if c["new"] else empty
                parts.append(f"{old} → {new}")
        return tag_prefix(it["tags"]) + " | ".join(parts)
    sign = "➕" if it["kind"] == "add" else "➖"
    return f"{sign} {tag_prefix(it['tags'])}" + " | ".join(esc(v, 60) for v in it["cells"])


def _short(text, limit=40):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _edit_key(old, new):
    """Réduit un avant/après à la partie qui change vraiment (mots), pour regrouper les modifs identiques."""
    if not new:
        return ("del", old, "")
    if not old:
        return ("add", "", new)
    a, b = old.split(), new.split()
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(ops) == 1:
        _, i1, i2, j1, j2 = ops[0]
        return ("mod", " ".join(a[i1:i2]), " ".join(b[j1:j2]))
    return ("mod", old, new)


def summarize(report, max_groups=3):
    """Compte rendu d'une ligne : modifs identiques regroupées, les plus fréquentes d'abord."""
    groups, extras = OrderedDict(), []
    for ev in report["events"]:
        if ev["type"] == "tab_added":
            extras.append(f"nouvel onglet {ev['tab']}")
        elif ev["type"] == "tab_removed":
            extras.append(f"onglet {ev['tab']} supprimé")
        elif ev["type"] == "tab_renamed":
            extras.append(f"onglet renommé en {ev['tab']}")
        for it in ev.get("items", []):
            if it["kind"] == "cell":
                changes = [(it["old"], it["new"], it["context"].split(" · ")[0])]
            elif it["kind"] == "row":
                changes = [(c["old"], c["new"], None) for c in it["cells"] if "old" in c]
            else:
                key = ("rows_" + it["kind"], "", "")
                groups.setdefault(key, []).append(None)
                continue
            for old, new, day in changes:
                groups.setdefault(_edit_key(old, new), []).append(day)
    parts = []
    for (kind, old, new), days in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:max_groups]:
        n = len(days)
        if kind == "rows_add":
            parts.append(f"{n} ligne(s) ajoutée(s)")
            continue
        if kind == "rows_del":
            parts.append(f"{n} ligne(s) supprimée(s)")
            continue
        if kind == "del":
            text = f"{_short(old)} retiré"
        elif kind == "add":
            text = f"{_short(new)} ajouté"
        elif not old:
            text = f"« {_short(new)} » ajouté"
        elif not new:
            text = f"« {_short(old)} » retiré"
        else:
            text = f"{_short(old)} → {_short(new)}"
        if n > 1:
            text += f" ({n} créneaux)" if all(days) else f" ({n}×)"
        elif days[0]:
            m = re.search(r"(\d{1,2})(?:er)?\s+(\S+)", days[0])
            if m:
                text += f" ({m.group(1)} {MONTHS_SHORT.get(m.group(2).lower(), m.group(2))})"
        parts.append(text)
    hidden = len(groups) - len(parts)
    if hidden > 0:
        parts.append(f"+{hidden} autre(s)")
    return " ; ".join(parts + extras)


def build_embeds(report):
    embeds = []
    for ev in report["events"]:
        if ev["type"] == "cells":
            lines, size, shown = [], 0, 0
            for it in ev["items"]:
                line = format_item(it)
                if size + len(line) + 1 > DESC_MAX:
                    break
                lines.append(line)
                size += len(line) + 1
                shown += 1
            if shown < len(ev["items"]):
                lines.append(f"… et {len(ev['items']) - shown} autre(s) changement(s)")
            embeds.append({"title": f"📅 {ev['tab']}"[:250], "url": ev["url"],
                           "description": "\n".join(lines), "color": COLOR})
        elif ev["type"] == "tab_added":
            embeds.append({"title": f"🆕 Nouvel onglet : {ev['tab']}"[:250], "url": ev["url"], "color": COLOR})
        elif ev["type"] == "tab_removed":
            embeds.append({"title": f"🗑️ Onglet supprimé : {ev['tab']}"[:250], "color": COLOR})
        elif ev["type"] == "tab_renamed":
            embeds.append({"title": f"✏️ Onglet renommé : {ev['old']} → {ev['tab']}"[:250],
                           "url": ev["url"], "color": COLOR})
    return embeds


def _embed_len(e):
    return len(e.get("title", "")) + len(e.get("description", "")) + len(e.get("footer", {}).get("text", ""))


def report_tags(report):
    tags = set()
    for ev in report["events"]:
        if ev["type"] == "cells":
            for it in ev["items"]:
                tags.update(it["tags"])
        elif ev["type"] in ("tab_added", "tab_removed"):
            tags.update(ev.get("tags", []))
    return sorted(tags)


def build_messages(report, pings, commit_url=None, note=None, links=None):
    """Transforme le rapport d'un sheet en payloads webhook (pings dans le 1er message uniquement).

    pings : {"SBI": ("role", "123"), "IBI": ("user", "456")}
    """
    status = report["status"]
    if status == "init":
        text = f"👀 Surveillance active sur **{esc(report['title'])}** : {len(report['tabs'])} onglets"
        return [{"content": text, "allowed_mentions": {"parse": []}}]
    if status == "access_lost":
        return [{"content": f"⚠️ **{esc(report['title'])}** n'est plus accessible ({esc(report['reason'], 120)}). "
                            "Je réessaie à chaque passage.", "allowed_mentions": {"parse": []}}]

    tags = report_tags(report)
    mentions, roles, users = [], [], []
    for t in tags:
        if t not in pings:
            mention = f"@{t.lower()}"
        else:
            kind, id_ = pings[t]
            mention = f"<@&{id_}>" if kind == "role" else f"<@{id_}>"
            (roles if kind == "role" else users).append(id_)
        if mention not in mentions:
            mentions.append(mention)
    head = " ".join(mentions)
    if status == "access_restored":
        head = ("✅ Sheet de nouveau accessible. " + head).strip()
    if note:
        head = f"{note} {head}".strip()
    if commit_url:
        head += f" · [diff](<{commit_url}>)"
    recap = summarize(report)
    if recap:
        head += (" · " if head else "") + esc(recap, 300)
    cal = [f"[{t}](<{links[t]}>)" for t in (tags or links or []) if links and t in links]
    if cal and status in ("changes", "access_restored"):
        head += " · 📅 " + " ".join(cal)

    footer = {"text": report["title"][:200]}
    messages, current, size = [], [], 0
    for e in build_embeds(report):
        e["footer"] = footer
        if current and (len(current) >= EMBEDS_MAX or size + _embed_len(e) > MESSAGE_MAX):
            messages.append({"embeds": current})
            current, size = [], 0
        current.append(e)
        size += _embed_len(e)
    if current:
        messages.append({"embeds": current})
    if not messages:
        messages = [{}]
    messages[0]["content"] = head[:2000]
    allowed = {"parse": [], "roles": sorted(set(roles)), "users": sorted(set(users))}
    for m in messages:
        m["allowed_mentions"] = allowed
    return messages


def send(webhook_url, payload, tries=5):
    data = json.dumps(payload).encode()
    url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    for _ in range(tries):
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/Jonathan8520/edt-m2-watcher, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                remaining = r.headers.get("X-RateLimit-Remaining")
                if remaining == "0":
                    time.sleep(float(r.headers.get("X-RateLimit-Reset-After", "1")))
                return
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 429:
                try:
                    wait = float(json.loads(body).get("retry_after", 2))
                except ValueError:
                    wait = 2
                time.sleep(wait + 0.5)
                continue
            if e.code >= 500:
                time.sleep(3)
                continue
            raise RuntimeError(f"Discord HTTP {e.code} : {body[:500]}") from e
        except urllib.error.URLError:
            time.sleep(3)
    raise RuntimeError("Discord injoignable après plusieurs tentatives")
