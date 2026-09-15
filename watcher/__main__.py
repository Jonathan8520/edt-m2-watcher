"""python -m watcher scan|notify"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from . import diff, discord, sheets


def load_grid(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sheets.normalize_grid(csv.reader(f))


def save_grid(path, grid):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(grid)


def gh_output(**kv):
    target = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={v}" for k, v in kv.items()]
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print(*lines, sep="\n")


def summary(report):
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    out = [f"### {report['title']} : {report['status']}"]
    for ev in report["events"]:
        out.append(f"**{ev['type']} · {ev['tab']}**")
        out += [f"- {discord.format_item(it)}" for it in ev.get("items", [])]
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def cmd_scan(args):
    sheet_id = os.environ["SHEET_ID"]
    tags = [t.strip() for t in os.environ.get("WATCH_TAGS", "IBI,SBI").split(",") if t.strip()]
    state = Path(args.state)
    meta_path = state / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def finish(report, message):
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "commit_msg.txt").write_text(message + "\n", encoding="utf-8")
        summary(report)
        gh_output(changed="true", notify="true")

    try:
        title, tabs = sheets.list_tabs(sheet_id)
        grids = {}
        for gid, _ in tabs:
            grids[gid] = sheets.fetch_tab(sheet_id, gid)
            time.sleep(0.5)
    except sheets.TransientError as e:
        print(f"::warning::Lecture du sheet impossible pour ce passage : {e}")
        gh_output(changed="false", notify="false")
        return
    except sheets.AccessLost as e:
        if meta and not meta.get("access_lost"):
            meta["access_lost"] = True
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            finish({"status": "access_lost", "title": meta["title"], "reason": str(e), "events": []},
                   f"Sheet inaccessible : {e}")
        else:
            print(f"::warning::Sheet toujours inaccessible : {e}")
            gh_output(changed="false", notify="false")
        return

    new_meta = {"sheet_id": sheet_id, "title": title, "access_lost": False,
                "tabs": [{"gid": gid, "name": name} for gid, name in tabs]}

    if meta is None:
        for gid, _ in tabs:
            save_grid(state / "tabs" / f"{gid}.csv", grids[gid])
        meta_path.write_text(json.dumps(new_meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        finish({"status": "init", "title": title, "tabs": [n for _, n in tabs], "events": []},
               f"Premier relevé : {len(tabs)} onglets")
        return

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
    if not events and not restored and new_meta == meta:
        print("Aucun changement.")
        gh_output(changed="false", notify="false")
        return
    meta_path.write_text(json.dumps(new_meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report = {"status": "access_restored" if restored else "changes", "title": title, "events": events}
    if not events and not restored:
        # seul le titre ou l'ordre des onglets a bougé : on enregistre sans notifier
        (out / "commit_msg.txt").write_text("Métadonnées mises à jour\n", encoding="utf-8")
        gh_output(changed="true", notify="false")
        return
    labels = {"tab_added": "nouvel onglet", "tab_removed": "onglet supprimé", "tab_renamed": "renommé"}
    parts = [f"{ev['tab']} : " + (f"{len(ev['items'])} changement(s)" if ev["type"] == "cells" else labels[ev["type"]])
             for ev in events]
    finish(report, ("Accès rétabli. " if restored else "") + (", ".join(parts) or "rien d'autre"))


def parse_roles(raw):
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return {k.upper(): str(v) for k, v in json.loads(raw).items()}
    roles = {}
    for part in raw.replace(";", ",").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            roles[k.strip().upper()] = "".join(ch for ch in v if ch.isdigit())
    return roles


def cmd_notify(args):
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    roles = parse_roles(os.environ.get("DISCORD_ROLES"))
    sha = os.environ.get("COMMIT_SHA", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY")
    commit_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/commit/{sha}" if sha and repo else None
    messages = discord.build_messages(report, roles, commit_url, args.note)
    if not webhook:
        print("::warning::Secret DISCORD_WEBHOOK_URL absent : notification ignorée")
        print(json.dumps(messages, ensure_ascii=False, indent=1))
        return
    for i, payload in enumerate(messages):
        if i:
            time.sleep(1)
        discord.send(webhook, payload)
    print(f"{len(messages)} message(s) envoyé(s)")


def main():
    p = argparse.ArgumentParser(prog="watcher")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--state", required=True)
    s.add_argument("--out", required=True)
    n = sub.add_parser("notify")
    n.add_argument("--report", required=True)
    n.add_argument("--note")
    args = p.parse_args()
    {"scan": cmd_scan, "notify": cmd_notify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
