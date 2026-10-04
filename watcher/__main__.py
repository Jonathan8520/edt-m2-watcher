"""python -m watcher scan|notify"""
import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import diff, discord, ics, recap, sheets


def load_grid(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sheets.normalize_grid(csv.reader(f))


def save_grid(path, grid):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(grid)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def gh_output(**kv):
    lines = [f"{k}={v}" for k, v in kv.items()]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print(*lines, sep="\n")


def summary(reports):
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    out = []
    for rep in reports:
        out.append(f"### {rep['title']} : {rep['status']}")
        for ev in rep["events"]:
            out.append(f"**{ev['type']} · {ev['tab']}**")
            out += [f"- {discord.format_item(it)}" for it in ev.get("items", [])]
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def migrate_legacy(root, sheet_ids):
    """Ancien emplacement state/meta.json -> state/<sheet_id>/meta.json."""
    legacy = root / "meta.json"
    if not legacy.exists():
        return False
    sid = json.loads(legacy.read_text(encoding="utf-8")).get("sheet_id")
    if sid not in sheet_ids or (root / sid).exists():
        return False
    (root / sid).mkdir(parents=True)
    shutil.move(str(legacy), str(root / sid / "meta.json"))
    if (root / "tabs").exists():
        shutil.move(str(root / "tabs"), str(root / sid / "tabs"))
    return True


def scan_sheet(sheet_id, state, tags):
    """Retourne (fichiers_modifiés, rapport|None, résumé_commit|None) pour un sheet."""
    meta_path = state / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    try:
        title, tabs = sheets.list_tabs(sheet_id)
        grids = {}
        for gid, _ in tabs:
            grids[gid] = sheets.fetch_tab(sheet_id, gid)
            time.sleep(0.5)
    except sheets.TransientError as e:
        print(f"::warning::{sheet_id} : lecture impossible pour ce passage : {e}")
        return False, None, None
    except sheets.AccessLost as e:
        if meta and not meta.get("access_lost"):
            meta["access_lost"] = True
            write_json(meta_path, meta)
            return True, {"status": "access_lost", "title": meta["title"], "reason": str(e), "events": []}, \
                f"{meta['title']} inaccessible"
        print(f"::warning::{sheet_id} : toujours inaccessible : {e}")
        return False, None, None

    new_meta = {"sheet_id": sheet_id, "title": title, "access_lost": False,
                "tabs": [{"gid": gid, "name": name} for gid, name in tabs]}

    if meta is None:
        for gid, _ in tabs:
            save_grid(state / "tabs" / f"{gid}.csv", grids[gid])
        write_json(meta_path, new_meta)
        return True, {"status": "init", "title": title, "tabs": [n for _, n in tabs], "events": []}, \
            f"{title} : premier relevé ({len(tabs)} onglets)"

    old_names = {t["gid"]: t["name"] for t in meta["tabs"]}
    events = []
    for gid, name in tabs:
        url = sheets.tab_url(sheet_id, gid)
        path = state / "tabs" / f"{gid}.csv"
        if gid not in old_names or not path.exists():
            events.append({"type": "tab_added", "tab": name, "url": url, "tags": diff.all_tags_in(grids[gid], tags)})
        else:
            if old_names[gid] != name:
                events.append({"type": "tab_renamed", "tab": name, "old": old_names[gid], "url": url})
            old_grid = load_grid(path)
            if old_grid != grids[gid]:
                items = diff.diff_tab(old_grid, grids[gid], tags)
                if items:
                    events.append({"type": "cells", "tab": name, "url": url, "items": items})
        save_grid(path, grids[gid])
    for gid, name in old_names.items():
        if gid not in grids:
            path = state / "tabs" / f"{gid}.csv"
            old_tags = diff.all_tags_in(load_grid(path), tags) if path.exists() else tags
            events.append({"type": "tab_removed", "tab": name, "tags": old_tags})
            path.unlink(missing_ok=True)

    restored = bool(meta.get("access_lost"))
    if new_meta == meta and not events:
        return False, None, None
    write_json(meta_path, new_meta)
    if not events and not restored:
        return True, None, f"{title} : métadonnées"
    labels = {"tab_added": "nouvel onglet", "tab_removed": "onglet supprimé", "tab_renamed": "renommé"}
    parts = [f"{ev['tab']} : " + (f"{len(ev['items'])} changement(s)" if ev["type"] == "cells" else labels[ev["type"]])
             for ev in events]
    report = {"status": "access_restored" if restored else "changes", "title": title, "events": events}
    return True, report, ("Accès rétabli. " if restored else "") + ", ".join(parts)


def sheet_ids_from_env():
    raw = os.environ.get("SHEET_IDS") or os.environ.get("SHEET_ID", "")
    ids = [x for x in re.split(r"[\s,;]+", raw) if x]
    if not ids:
        sys.exit("SHEET_IDS vide")
    return ids


def cmd_scan(args):
    ids = sheet_ids_from_env()
    tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
    root, out = Path(args.state), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    changed = migrate_legacy(root, ids)
    reports, messages = [], ["Migration de l'état par sheet"] if changed else []
    for sid in ids:
        ch, rep, msg = scan_sheet(sid, root / sid, tags)
        changed |= ch
        if rep:
            reports.append(rep)
        if msg:
            messages.append(msg)
    (out / "report.json").write_text(json.dumps({"sheets": reports}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "commit_msg.txt").write_text(" | ".join(messages) + "\n", encoding="utf-8")
    summary(reports)
    if args.ics and (changed or not any(Path(args.ics).glob("*.ics"))):
        if write_calendars(root, args.ics, ids, tags) and not changed:
            changed = True
            (out / "commit_msg.txt").write_text("Calendriers ICS\n", encoding="utf-8")
    if not changed:
        print("Aucun changement.")
    gh_output(changed=str(changed).lower(), notify=str(bool(reports)).lower())


def parse_pings(raw):
    """IBI=<@&123>,SBI=<@456>  (un nombre seul = rôle)."""
    pings = {}
    for part in re.split(r"[,;\n]+", (raw or "").strip()):
        if "=" not in part:
            continue
        tag, value = (x.strip() for x in part.split("=", 1))
        m = re.search(r"<@(&|!)?(\d+)>", value)
        if m:
            pings[tag.upper()] = ("role" if m.group(1) == "&" else "user", m.group(2))
        elif value.isdigit():
            pings[tag.upper()] = ("role", value)
    return pings


def calendar_links(ics_dir, repo):
    """{"IBI": url, "SBI": url} des calendriers publiés dans le gist d'abonnement."""
    links, gist_id = {}, os.environ.get("ICS_GIST_ID", "").strip()
    if gist_id and ics_dir and repo:
        tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
        for path in sorted(Path(ics_dir).glob("*.ics")):
            for t in tags:
                if re.search(rf"(?<![A-Za-z]){t}(?![A-Za-z])", path.name, re.I):
                    links[t] = f"https://gist.githubusercontent.com/{repo.split('/')[0]}/{gist_id}/raw/{path.name}"
    return links


def cmd_notify(args):
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    pings = parse_pings(os.environ.get("DISCORD_PINGS") or os.environ.get("DISCORD_ROLES"))
    repo = os.environ.get("GITHUB_REPOSITORY")
    commit_url = None  # repo privé : lien « diff » illisible pour le salon, retiré
    links = calendar_links(args.ics_dir, repo)
    models_token = os.environ.get("RECAP_API_KEY", "").strip()
    payloads = []
    for rep in report["sheets"]:
        phrase = recap.bullets(rep, models_token)
        print(f"Résumé : {phrase}")
        payloads += discord.build_messages(rep, pings, commit_url, args.note, links, phrase)
    if not webhook:
        print("::warning::Secret DISCORD_WEBHOOK_URL absent : notification ignorée")
        print(json.dumps(payloads, ensure_ascii=False, indent=1))
        return
    ids = []
    for i, payload in enumerate(payloads):
        if i:
            time.sleep(1)
        ids.append((discord.send(webhook, payload) or {}).get("id"))
    print(f"{len(payloads)} message(s) envoyé(s) : {ids}")
    if args.log:
        # journal des messages (branche datas) : permet de les corriger plus tard sans notifier
        entry = {"date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "old": os.environ.get("PREV_SHA", ""),
                 "messages": [i for i in ids if i]}
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_calendars(state_root, out_dir, ids, tags):
    """Un .ics par programme à partir de l'état enregistré (state/<id>/)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for sid in ids:
        state = Path(state_root) / sid
        if not (state / "meta.json").exists():
            continue
        meta = json.loads((state / "meta.json").read_text(encoding="utf-8"))
        url = f"https://docs.google.com/spreadsheets/d/{sid}/htmlview"
        events = []
        for tab in meta["tabs"]:
            path = state / "tabs" / f"{tab['gid']}.csv"
            if path.exists():
                events += ics.extract_events(load_grid(path), tags, tab["name"])
        for tag in tags:
            evs = [e for e in events if e["tag"] == tag]
            if evs:
                name = evs[0]["calendar"]
                path = out / (re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") + ".ics")
                path.write_bytes(ics.build_calendar(evs, name, url).encode("utf-8"))
                written.append(path)
                print(f"{path} : {len(evs)} événements")
    return written


def cmd_ics(args):
    tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
    write_calendars(args.state, args.out, sheet_ids_from_env(), tags)

def cmd_gist(args):
    """Synchronise les .ics avec le gist d'abonnement (ICS_GIST_ID, GIST_TOKEN) ; ne réécrit que ce qui a changé."""
    import urllib.request
    gist_id, token = os.environ.get("ICS_GIST_ID", "").strip(), os.environ.get("GIST_TOKEN", "").strip()
    if not gist_id or not token:
        print("ICS_GIST_ID ou GIST_TOKEN absent : publication ignorée")
        return

    def api(method, body=None):
        req = urllib.request.Request(f"https://api.github.com/gists/{gist_id}", method=method,
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                              "User-Agent": "edt-m2-watcher"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    remote = api("GET")["files"]
    changes = {}
    for path in sorted(Path(args.dir).glob("*.ics")):
        content = path.read_text(encoding="utf-8")
        current = remote.get(path.name) or {}
        # les gists stockent les fins de ligne en LF : on compare sans les CR
        if current.get("truncated") or (current.get("content") or "").replace("\r\n", "\n") != content.replace("\r\n", "\n"):
            changes[path.name] = {"content": content}
    if changes:
        api("PATCH", {"files": changes})
    print(f"Gist : {len(changes)} fichier(s) mis à jour")

def report_between(datas_dir, sheet_id, old_rev, new_rev, tags):
    """Rapport de changements entre deux commits de la branche datas (pour tester sans rien envoyer)."""
    import subprocess

    def show(rev, rel):
        r = subprocess.run(["git", "-C", datas_dir, "show", f"{rev}:state/{sheet_id}/{rel}"],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    meta = json.loads(show(new_rev, "meta.json"))
    events = []
    for tab in meta["tabs"]:
        old, new = (sheets.normalize_grid(csv.reader(io.StringIO(show(rev, f"tabs/{tab['gid']}.csv") or "")))
                    for rev in (old_rev, new_rev))
        if old != new:
            items = diff.diff_tab(old, new, tags)
            if items:
                events.append({"type": "cells", "tab": tab["name"], "url": "", "items": items})
    return {"status": "changes", "title": meta["title"], "events": events}


def cmd_recap(args):
    tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
    for sid in sheet_ids_from_env():
        rep = report_between(args.datas, sid, args.old, args.new, tags)
        print(f"=== {args.old} -> {args.new} : {sum(len(e['items']) for e in rep['events'])} changement(s)")
        print(recap.describe(rep))
        for tag, points in recap.bullets(rep, os.environ.get("RECAP_API_KEY", "").strip()).items():
            print(f"RÉSUMÉ {tag} :", " | ".join(points))

def cmd_fix(args):
    """Réécrit la première ligne d'un message déjà envoyé (pings + phrase), sans notifier personne."""
    import subprocess
    log = Path(args.datas) / "state" / "messages.jsonl"
    entries = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []
    entry = next((e for e in entries if args.message_id in e["messages"]), None)
    if not entry:
        sys.exit(f"Message {args.message_id} absent du journal {log}")
    if entry["messages"][0] != args.message_id:
        sys.exit("Seul le premier message d'une notification porte la phrase : donne l'identifiant de celui-là")
    new = entry.get("new") or subprocess.run(
        ["git", "-C", args.datas, "log", "--reverse", "--format=%H", f"-S{args.message_id}", "--", "state/messages.jsonl"],
        capture_output=True, text=True, check=True).stdout.split()[0]
    tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
    pings = parse_pings(os.environ.get("DISCORD_PINGS"))
    rep = report_between(args.datas, sheet_ids_from_env()[0], entry["old"], new, tags)
    summary_ = recap.bullets(rep, os.environ.get("RECAP_API_KEY", "").strip())
    links = calendar_links(Path(args.datas) / "ics", os.environ.get("GITHUB_REPOSITORY", ""))
    msgs = discord.build_messages(rep, pings, None, None, links, summary_)
    payload = {"content": args.phrase or msgs[0]["content"], "allowed_mentions": {"parse": []}}
    if len(msgs) == 1 and len(entry["messages"]) == 1:
        payload["embeds"] = msgs[0].get("embeds", [])
    print("Nouveau contenu :\n" + payload["content"])
    print(f"Encadrés : {len(payload.get('embeds', [])) or 'inchangés'}")
    if args.dry_run:
        return
    discord.send(os.environ["DISCORD_WEBHOOK_URL"], payload, message_id=args.message_id)
    print("Message modifié (aucune notification).")

def main():
    p = argparse.ArgumentParser(prog="watcher")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--state", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--ics", help="dossier où régénérer les .ics quand le planning change")
    n = sub.add_parser("notify")
    n.add_argument("--report", required=True)
    n.add_argument("--note")
    n.add_argument("--ics-dir", help="dossier des .ics pour ajouter les liens d'abonnement au message")
    n.add_argument("--log", help="fichier JSONL où noter les identifiants des messages envoyés")
    i = sub.add_parser("ics")
    i.add_argument("--state", required=True)
    i.add_argument("--out", required=True)
    g = sub.add_parser("gist")
    g.add_argument("--dir", required=True)
    r = sub.add_parser("recap", help="teste la phrase de résumé entre deux commits de datas (n'envoie rien)")
    r.add_argument("--datas", required=True)
    r.add_argument("--old", required=True)
    r.add_argument("--new", required=True)
    f = sub.add_parser("fix", help="réécrit la phrase d'un message déjà envoyé, sans notifier")
    f.add_argument("--datas", required=True)
    f.add_argument("--message-id", required=True)
    f.add_argument("--phrase", help="texte complet imposé pour le haut du message (sinon régénéré par l'IA)")
    f.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    {"scan": cmd_scan, "notify": cmd_notify, "ics": cmd_ics, "gist": cmd_gist, "recap": cmd_recap,
     "fix": cmd_fix}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
