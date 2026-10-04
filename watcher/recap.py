"""Phrase courte qui résume un changement, écrite par un modèle d'IA via une API compatible OpenAI.

Par défaut : Gemini (clé gratuite Google AI Studio). Sans clé (secret RECAP_API_KEY), rien n'est appelé."""
import json
import os
import re
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("RECAP_ENDPOINT") or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODELS = [m.strip() for m in (os.environ.get("RECAP_MODELS") or "gpt-4.1-mini,gemini-2.5-flash-lite").split(",") if m.strip()]
MAX_LINES = 40

SYSTEM = (
    "Tu rédiges le résumé en tête d'une notification Discord qui annonce à une promo de master une modification "
    "de son emploi du temps. On te donne une synthèse par cours (la plus fiable) puis le détail des cellules. "
    "Les codes comme ISI8, GO1 ou INF2 sont des cours ; « partiel X » est l'examen du cours X ; les noms entre "
    "parenthèses sont des enseignants.\n"
    "Règles :\n"
    "- Format : 1 à 4 lignes commençant chacune par « - », une idée par ligne, 15 mots maximum par ligne, "
    "en français simple, partiels en premier. Aucune introduction ni conclusion.\n"
    "- Dis concrètement ce qui change avec les dates courtes (ex. « 5 oct. », « 19 janv. »), sans jour de la semaine "
    "ni année. Ajoute l'horaire seulement pour un partiel ou un horaire inhabituel.\n"
    "- Un cours retiré à une date et ajouté à une autre est DÉPLACÉ : écris « déplacé du 5 oct. au 15 déc. ».\n"
    "- Interdit d'être vague : jamais « modifié », « des changements », « annulé puis rajouté » sans dire quoi et quand.\n"
    "- Ne parle jamais de ce qui reste inchangé. Une seule ligne par cours, même s'il apparaît plusieurs fois.\n"
    "- Seulement si plus de 4 points seraient nécessaires : garde les 3 plus importants et termine par "
    "« - Autres ajustements mineurs. »\n"
    "- N'invente rien. Pas d'emoji, pas de guillemets, pas de préfixe, pas de mention IBI ou SBI.\n"
    "Exemples (situations inventées, une situation par ligne) :\n"
    "* MM1 retiré le 3 nov. et ajouté le 8 déc. → - MM1 est déplacé du 3 nov. au 8 déc.\n"
    "* partiel GO3 ajouté le 12 mars 14h-16h → - Partiel de GO3 ajouté le 12 mars, 14h-16h.\n"
    "* REC1 retiré les 2, 9 et 16 févr. → - REC1 est retiré les 2, 9 et 16 févr.\n"
    "* INF4 : texte complété par « Claire DUPONT » → - Nom complet de l'enseignante d'INF4 précisé : Claire Dupont."
)

MONTHS_SHORT = {"janvier": "janv.", "février": "févr.", "fevrier": "févr.", "mars": "mars", "avril": "avr.",
                "mai": "mai", "juin": "juin", "juillet": "juil.", "août": "août", "aout": "août",
                "septembre": "sept.", "octobre": "oct.", "novembre": "nov.", "décembre": "déc.", "decembre": "déc."}
CODE_RE = re.compile(r"\b(?:M2-)?([A-Z]{2,4}\d{1,2})\b")
RANGE_RE = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?\s*h?\s*-\s*(\d{1,2})\s*h\s*(\d{2})?", re.I)


def _short_date(text):
    m = re.search(r"(\d{1,2})(?:er)?\s+([a-zéû]+)", text or "", re.I)
    return f"{m.group(1)} {MONTHS_SHORT.get(m.group(2).lower(), m.group(2))}" if m else (text or "")


def _moment(slot, text):
    m = RANGE_RE.search(text or "")
    if m:
        return f"{int(m.group(1))}h{m.group(2) or ''}-{int(m.group(3))}h{m.group(4) or ''}"
    return ""


def _codes(text):
    """Cours d'une cellule : {("INF2", False): segment, ("ISI5", True): segment} (True = partiel).

    Une cellule « partiel MM1 … + ISI5 part 1 … » ne contient que des partiels."""
    partiel = "partiel" in (text or "").lower()
    found = {}
    for part in re.split(r"\+", text or ""):
        for code in CODE_RE.findall(part):
            found.setdefault((code, partiel), part)
    return found


def synthesis(report):
    """Regroupe par cours : où il est retiré, où il est ajouté (permet de reconnaître un déplacement)."""
    by_course = {}
    for ev in report["events"]:
        for it in ev.get("items", []):
            if it["kind"] != "cell":
                continue
            date, _, slot = it["context"].partition(" · ")
            old, new = _codes(it["old"]), _codes(it["new"])
            changes = [(k, "retiré", old[k]) for k in old.keys() - new.keys()] + \
                      [(k, "ajouté", new[k]) for k in new.keys() - old.keys()]
            for key, verb, text in changes:
                moment = _moment(slot, text)
                where = f"{_short_date(date)} ({moment})" if moment else _short_date(date)
                entry = by_course.setdefault(key, {"retiré": [], "ajouté": []})
                if where not in entry[verb]:
                    entry[verb].append(where)
    lines = []
    # textes simplement complétés (mêmes cours, mots ajoutés) : ex. « GO1 (Paul) » → « GO1 (Paul MARTIN) »
    completed = {}
    for ev in report["events"]:
        for it in ev.get("items", []):
            if it["kind"] != "cell" or not it["old"] or not it["new"] or _codes(it["old"]).keys() != _codes(it["new"]).keys():
                continue
            a, b = it["old"].replace("(", " ").replace(")", " ").split(), it["new"].replace("(", " ").replace(")", " ").split()
            added = [w for w in b if w not in a]
            if added and all(w in b for w in a):
                segs_old, segs_new = it["old"].split("+"), it["new"].split("+")
                changed = [n for o, n in zip(segs_old, segs_new) if o.strip() != n.strip()] \
                    if len(segs_old) == len(segs_new) else segs_new
                codes = ", ".join(sorted({c for seg in changed for c, _ in _codes(seg)})) or "texte"
                key = (codes, " ".join(added))
                completed.setdefault(key, []).append(_short_date(it["context"].partition(" · ")[0]))
    for (codes, added), days in completed.items():
        uniq = list(dict.fromkeys(days))
        lines.append(f"- {codes} : texte complété par « {added} » (même cours, mêmes créneaux) "
                     f"sur {len(days)} cellule(s), du {uniq[0]} au {uniq[-1]}")
    for (code, partiel), entry in sorted(by_course.items(), key=lambda kv: (not kv[0][1], kv[0][0])):
        parts = [f"{verb} le {', '.join(where)}" for verb, where in entry.items() if where]
        lines.append(f"- {'partiel ' if partiel else ''}{code} : " + " ; ".join(parts))
    return lines


def describe(report):
    """Synthèse par cours puis liste compacte des changements, une ligne par cellule."""
    lines = []
    for ev in report["events"]:
        tab = ev["tab"]
        if ev["type"] == "tab_added":
            lines.append(f"- nouvel onglet « {tab} »")
        elif ev["type"] == "tab_removed":
            lines.append(f"- onglet « {tab} » supprimé")
        elif ev["type"] == "tab_renamed":
            lines.append(f"- onglet « {ev['old']} » renommé en « {tab} »")
        for it in ev.get("items", []):
            if it["kind"] == "cell":
                lines.append(f"- {it['context']} : « {it['old']} » → « {it['new']} »")
            elif it["kind"] == "row":
                ctx = " | ".join(c["value"] for c in it["cells"] if "value" in c)[:120]
                for c in it["cells"]:
                    if "old" in c:
                        lines.append(f"- onglet {tab}, ligne « {ctx} » : « {c['old']} » → « {c['new']} »")
            else:
                verb = "ajoutée" if it["kind"] == "add" else "supprimée"
                lines.append(f"- onglet {tab}, ligne {verb} : {' | '.join(it['cells'])[:160]}")
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"- … et {len(lines) - MAX_LINES} autres changements du même genre"]
    synth = synthesis(report)
    head = ["Synthèse par cours :", *synth, "", "Détail des cellules :"] if synth else ["Détail des cellules :"]
    return "\n".join(head + lines)


def _call(model, token, content):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    puter = "/drivers/call" in ENDPOINT  # API native de Puter (celle de puter.js), accessible au compte gratuit
    if puter:
        body = {"interface": "puter-chat-completion", "method": "complete",
                "args": {"messages": messages, "model": model, "temperature": 0.1, "max_tokens": 1500}}
    else:
        body = {"model": model, "temperature": 0.1, "max_tokens": 1500, "messages": messages}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "edt-m2-watcher"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if puter:
        if not data.get("success"):
            raise RuntimeError(f"Puter : {str(data.get('error'))[:200]}")
        content = data["result"]["message"]["content"]
    else:
        content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):  # certains modèles renvoient des blocs [{"type": "text", "text": …}]
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content


def sentence(report, token):
    """Renvoie la phrase, ou None si le service ne répond pas (la notification part alors sans)."""
    if not token or report.get("status") not in ("changes", "access_restored") or not report["events"]:
        return None
    content = describe(report)
    for model in MODELS:
        try:
            text = _call(model, token, content)
        except urllib.error.HTTPError as e:
            print(f"::warning::Résumé IA ({model}) : HTTP {e.code} {e.read()[:200]!r}")
            continue
        except Exception as e:  # réseau, format inattendu : on n'empêche jamais la notification
            print(f"::warning::Résumé IA ({model}) : {e}")
            continue
        text = " ".join((text or "").split()).strip("\"'«» ")
        if text:
            return text[:300]
    return None


def _bullet_lines(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    items = [re.sub(r"^[-•*]\s*", "", l).strip() for l in lines if re.match(r"^[-•*]\s", l)]
    if not items:
        items = [" ".join(lines)] if lines else []
    return [i.strip("\"'«» ") for i in items if i][:5]


def _for_tag(report, tag):
    events = []
    for ev in report["events"]:
        if ev["type"] == "cells":
            items = [it for it in ev["items"] if tag in it["tags"]]
            if items:
                events.append({**ev, "items": items})
    return {**report, "events": events}


def bullets(report, token):
    """Points de résumé par programme : {"IBI": [...], "SBI": [...]} ({} si l'IA ne répond pas)."""
    if not token or report.get("status") not in ("changes", "access_restored") or not report["events"]:
        return {}
    tags = sorted({t for ev in report["events"] for it in ev.get("items", []) for t in it["tags"]})
    out = {}
    for tag in tags:
        sub = _for_tag(report, tag)
        if not sub["events"]:
            continue
        content = describe(sub)
        for model in MODELS:
            try:
                points = _bullet_lines(_call(model, token, content))
            except urllib.error.HTTPError as e:
                print(f"::warning::Résumé IA ({model}) : HTTP {e.code} {e.read()[:200]!r}")
                continue
            except Exception as e:
                print(f"::warning::Résumé IA ({model}) : {e}")
                continue
            if points:
                out[tag] = [p[:200] for p in points]
                break
    return out
