"""Mise en forme des changements et envoi au webhook Discord."""
import json
import re
import time
import urllib.error
import urllib.request

DESC_MAX = 3800      # limite Discord : 4096 par description
MESSAGE_MAX = 5800   # limite Discord : 6000 caractères d'embeds par message
EMBEDS_MAX = 10
COLOR = 0xF1C40F


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
    return len(e.get("title", "")) + len(e.get("description", ""))


def report_tags(report):
    tags = set()
    for ev in report["events"]:
        if ev["type"] == "cells":
            for it in ev["items"]:
                tags.update(it["tags"])
        elif ev["type"] in ("tab_added", "tab_removed"):
            tags.update(ev.get("tags", []))
    return sorted(tags)


def build_messages(report, roles, commit_url=None, note=None):
    """Transforme un rapport en payloads webhook (pings dans le 1er message uniquement)."""
    status = report["status"]
    if status == "init":
        text = f"👀 Surveillance active sur **{esc(report['title'])}** : {len(report['tabs'])} onglets"
        return [{"content": text, "allowed_mentions": {"parse": []}}]
    if status == "access_lost":
        return [{"content": f"⚠️ **{esc(report['title'])}** n'est plus accessible ({esc(report['reason'], 120)}). "
                            "Je réessaie à chaque passage.", "allowed_mentions": {"parse": []}}]

    tags = report_tags(report)
    mentions = [f"<@&{roles[t]}>" if t in roles else f"@{t.lower()}" for t in tags]
    head = " ".join(mentions)
    if status == "access_restored":
        head = ("✅ Sheet de nouveau accessible. " + head).strip()
    if note:
        head = f"{note} {head}".strip()
    if commit_url:
        head += f" · [diff](<{commit_url}>)"

    messages, current, size = [], [], 0
    for e in build_embeds(report):
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
    for m in messages:
        m["allowed_mentions"] = {"parse": [], "roles": [roles[t] for t in tags if t in roles]}
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
