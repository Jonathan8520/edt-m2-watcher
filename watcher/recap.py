"""Phrase courte qui résume un changement, écrite par un modèle d'IA via une API compatible OpenAI.

Par défaut : Gemini (clé gratuite Google AI Studio). Sans clé (secret RECAP_API_KEY), rien n'est appelé."""
import json
import os
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("RECAP_ENDPOINT") or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODELS = [m.strip() for m in (os.environ.get("RECAP_MODELS") or "gemini-2.5-flash-lite,gemini-2.5-flash").split(",") if m.strip()]
MAX_LINES = 40

SYSTEM = (
    "Tu rédiges la première ligne d'une notification Discord qui annonce une modification de l'emploi du temps "
    "d'un master en informatique. On te donne la liste des cellules modifiées. "
    "Les codes comme ISI8, GO1 ou INF4 sont des cours ; les noms entre parenthèses sont des enseignants ; "
    "une cellule qui devient vide veut dire que le cours est retiré de ce créneau.\n"
    "Écris UNE seule phrase en français, simple et naturelle, de 15 mots maximum, qui dit l'essentiel "
    "comme un étudiant l'annoncerait à sa promo. Regroupe les changements similaires en une idée. "
    "Ne liste pas les dates (sauf s'il n'y en a qu'une). N'invente rien. "
    "Pas d'emoji, pas de guillemets, pas de préfixe.\n"
    "Exemples de style (situations différentes) :\n"
    "- MM1 vidé sur 5 créneaux → Le cours MM1 est retiré du planning.\n"
    "- « INF4 (Claire) » → « INF4 (Claire) Zoom » sur 3 créneaux → Les cours d'INF4 passent sur Zoom.\n"
    "- case vide → « partiel GO3 » le jeudi 12 mars → Un partiel de GO3 est ajouté le 12 mars."
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
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
    puter = "/drivers/call" in ENDPOINT  # API native de Puter (celle de puter.js), accessible au compte gratuit
    if puter:
        body = {"interface": "puter-chat-completion", "method": "complete",
                "args": {"messages": messages, "model": model, "temperature": 0.2, "max_tokens": 1500}}
    else:
        body = {"model": model, "temperature": 0.2, "max_tokens": 1500, "messages": messages}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": "edt-m2-watcher"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    if puter:
        if not data.get("success"):
            raise RuntimeError(f"Puter : {str(data.get('error'))[:200]}")
        return data["result"]["message"]["content"]
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
            print(f"::warning::Résumé IA ({model}) : HTTP {e.code} {e.read()[:200]!r}")
            continue
        except Exception as e:  # réseau, format inattendu : on n'empêche jamais la notification
            print(f"::warning::Résumé IA ({model}) : {e}")
            continue
        text = " ".join((text or "").split()).strip("\"'«» ")
        if text:
            return text[:220]
    return None
