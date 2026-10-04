"""Mise en forme des changements et envoi au webhook Discord."""
import json
import re
import time
import urllib.error
import urllib.request

DESC_MAX = 3700      # limite Discord : 4096 par description (marge pour le lien agenda)
MESSAGE_MAX = 5500   # limite Discord : 6000 caractères d'embeds par message (marge pour le lien agenda)
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


DAYS_SHORT = {"lundi": "lun.", "mardi": "mar.", "mercredi": "mer.", "jeudi": "jeu.", "vendredi": "ven.",
              "samedi": "sam.", "dimanche": "dim."}


def short_context(context):
    """« lundi 5 octobre 2026 · 9h30-12h30 » → « lun. 5 oct. · 9h30-12h30 » (l'onglet donne déjà la période)."""
    def day(m):
        return f"{DAYS_SHORT.get(m.group(1).lower(), m.group(1))} {m.group(2)} {MONTHS_SHORT.get(m.group(3).lower(), m.group(3))}"
    return re.sub(r"\b([a-zé]+)\s+(\d{1,2}(?:er)?)\s+([a-zéû]+)\s+\d{4}\b", day, context, count=1, flags=re.I)


def change_line(old, new):
    """❌ retiré · ➕ ajouté · 🔁 remplacé."""
    if not new:
        return f"❌ ~~{esc(old)}~~"
    if not old:
        return f"➕ **{esc(new)}**"
    return f"🔁 ~~{esc(old)}~~ → **{esc(new)}**"


def format_item(it, show_tags=True):
    """Un changement sur deux lignes : le moment (ou le cours) en gras, puis ce qui change."""
    prefix = tag_prefix(it["tags"]) if show_tags else ""
    if it["kind"] == "cell":
        return f"{prefix}**{esc(short_context(it['context']), 80)}**\n{change_line(it['old'], it['new'])}"
    if it["kind"] == "row":
        changes = [c for c in it["cells"] if "old" in c]
        head = it.get("key") or " | ".join(esc(c["value"], 40) for c in it["cells"] if "value" in c)[:120]
        lines = [(f"{esc(c['header'], 30)} : " if c.get("header") else "") + change_line(c["old"], c["new"])
                 for c in changes]
        return f"{prefix}**{esc(head, 60)}**\n" + "\n".join(lines)
    sign = "➕ Ligne ajoutée" if it["kind"] == "add" else "❌ Ligne supprimée"
    return f"{prefix}**{sign}**\n" + " | ".join(esc(v, 60) for v in it["cells"])


PROGRAM_COLORS = {"IBI": 0x9B59B6, "SBI": 0x3498DB}


def _lines(items, show_tags):
    lines, size, shown = [], 0, 0
    for it in items:
        line = format_item(it, show_tags)
        if size + len(line) + 2 > DESC_MAX:
            break
        lines.append(line)
        size += len(line) + 2
        shown += 1
    if shown < len(items):
        lines.append(f"… et {len(items) - shown} autre(s) changement(s)")
    return "\n\n".join(lines)


def build_embeds(report, links=None):
    """Encadrés regroupés par programme (IBI puis SBI), un par onglet ; lien d'agenda à la fin de chaque section."""
    embeds = []
    cells = [ev for ev in report["events"] if ev["type"] == "cells"]
    for prog in report_tags(report):
        section = []
        for ev in cells:
            items = [it for it in ev["items"] if prog in it["tags"]]
            if items:
                section.append({"title": f"📅 {prog} · {ev['tab']}"[:250], "url": ev["url"],
                                "description": _lines(items, False), "color": PROGRAM_COLORS.get(prog, COLOR)})
        if section and links and prog in links:
            section[-1]["description"] += f"\n\n[Ajouter l'emploi du temps {prog} à mon agenda]({links[prog]})"
        embeds += section
    for ev in cells:  # changements sans programme identifié (pas de mots-clés configurés)
        items = [it for it in ev["items"] if not it["tags"]]
        if items:
            embeds.append({"title": f"📅 {ev['tab']}"[:250], "url": ev["url"],
                           "description": _lines(items, True), "color": COLOR})
    for ev in report["events"]:
        if ev["type"] == "tab_added":
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


def build_messages(report, pings, commit_url=None, note=None, links=None, recap=None):
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
    mention_of, roles, users = {}, [], []
    for t in tags:
        if t not in pings:
            mention_of[t] = f"@{t.lower()}"
        else:
            kind, id_ = pings[t]
            mention_of[t] = f"<@&{id_}>" if kind == "role" else f"<@{id_}>"
            (roles if kind == "role" else users).append(id_)
    if isinstance(recap, str):  # ancienne forme : une phrase pour tout le monde
        recap = {"*": [recap]}
    recap = {k: v for k, v in (recap or {}).items() if v}
    if not recap:
        lines = [" ".join(dict.fromkeys(mention_of.values()))]
    elif "*" in recap:
        lines = [" ".join(dict.fromkeys(mention_of.values()))] + [f"- {esc(b, 200)}" for b in recap["*"]]
    else:
        lines = []
        for t in tags:  # une section par programme : son ping puis ses points
            lines.append(mention_of[t])
            lines += [f"- {esc(b, 200)}" for b in recap.get(t, [])]
    head = "\n".join(l for l in lines if l)
    if status == "access_restored":
        head = ("✅ Sheet de nouveau accessible.\n" + head).strip()
    if note:
        head = f"{note}\n{head}".strip()
    if commit_url:
        head += f"\n[diff](<{commit_url}>)"

    messages, current, size = [], [], 0
    for e in build_embeds(report, links):
        if current and (len(current) >= EMBEDS_MAX or size + _embed_len(e) > MESSAGE_MAX):
            messages.append({"embeds": current})
            current, size = [], 0
        current.append(e)
        size += _embed_len(e)
    if current:
        messages.append({"embeds": current})
    if messages:  # le nom du planning une seule fois, sous le dernier encadré
        messages[-1]["embeds"][-1]["footer"] = {"text": report["title"][:200]}
    if not messages:
        messages = [{}]
    messages[0]["content"] = head[:2000]
    allowed = {"parse": [], "roles": sorted(set(roles)), "users": sorted(set(users))}
    for m in messages:
        m["allowed_mentions"] = allowed
    return messages


def send(webhook_url, payload, tries=5, message_id=None):
    """Envoie un message (ou modifie le message message_id) ; renvoie le message Discord."""
    data = json.dumps(payload).encode()
    if message_id:
        url, method = f"{webhook_url.split('?')[0]}/messages/{message_id}", "PATCH"
    else:
        url, method = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true", "POST"
    for _ in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/Jonathan8520/edt-m2-watcher, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                remaining = r.headers.get("X-RateLimit-Remaining")
                if remaining == "0":
                    time.sleep(float(r.headers.get("X-RateLimit-Reset-After", "1")))
                try:
                    return json.loads(body)
                except ValueError:
                    return {}
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
