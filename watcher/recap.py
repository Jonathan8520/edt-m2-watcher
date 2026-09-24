"""Phrase courte qui résume un changement, écrite par un modèle de GitHub Models (gratuit dans Actions)."""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://models.github.ai/inference/chat/completions"
MODELS = [m for m in os.environ.get("RECAP_MODELS", "openai/gpt-4.1-mini,openai/gpt-4o-mini").split(",") if m]
MAX_LINES = 40

SYSTEM = (
    "Tu rédiges la première ligne d'une notification Discord qui annonce une modification de l'emploi du temps "
    "d'un master en informatique (deux programmes : IBI et SBI). On te donne la liste des cellules modifiées. "
    "Les codes comme ISI8, GO1 ou INF4 sont des cours ; les prénoms ou noms entre parenthèses sont des enseignants ; "
    "une cellule vidée veut dire que le cours est retiré de ce créneau. "
    "Écris UNE seule phrase en français, simple et naturelle, de 20 mots maximum, qui dit l'essentiel. "
    "Regroupe les changements similaires. Ne cite des dates que s'il y en a une ou deux. N'invente rien. "
    "Pas d'emoji, pas de guillemets, pas de préfixe, pas de mention IBI ou SBI."
)


def describe(report):
    """Liste compacte des changements, une ligne par cellule."""
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
    return "\n".join(lines)


def _call(model, token, content):
    body = {"model": model, "temperature": 0.2, "max_tokens": 80,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "edt-m2-watcher"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def sentence(report, token):
    """Renvoie la phrase, ou None si le service ne répond pas (la notification part alors sans)."""
    if not token or report.get("status") not in ("changes", "access_restored") or not report["events"]:
        return None
    content = describe(report)
    for model in MODELS:
        try:
            text = _call(model, token, content)
        except urllib.error.HTTPError as e:
            print(f"::warning::GitHub Models ({model}) : HTTP {e.code} {e.read()[:200]!r}")
            continue
        except Exception as e:  # réseau, format inattendu : on n'empêche jamais la notification
            print(f"::warning::GitHub Models ({model}) : {e}")
            continue
        text = " ".join((text or "").split()).strip("\"'«» ")
        if text:
            return text[:220]
    return None
