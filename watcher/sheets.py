"""Récupération d'un Google Sheet partagé par lien : liste des onglets + export CSV."""
import csv
import html as html_mod
import io
import json
import re
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BASE = "https://docs.google.com/spreadsheets/d/{id}"


class TransientError(Exception):
    """Erreur passagère (réseau, 429, 5xx, réponse inattendue) : on réessaiera au prochain passage."""


class AccessLost(Exception):
    """Le sheet n'est plus lisible (supprimé, rendu privé, redirection vers la connexion)."""


def _get(url, tries=3):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://docs.google.com/", "Accept-Language": "fr-FR,fr;q=0.9"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                final = r.geturl()
                if "accounts.google.com" in final or "ServiceLogin" in final:
                    raise AccessLost(f"redirection vers la connexion Google ({url})")
                return r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise AccessLost(f"HTTP {e.code} sur {url}") from e
            last = TransientError(f"HTTP {e.code} sur {url}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = TransientError(f"{type(e).__name__}: {e} sur {url}")
        time.sleep(3 * (attempt + 1))
    raise last


def _js_unescape(s):
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: "\\u00" + m.group(1), s)
    return json.loads(f'"{s}"')


def list_tabs(sheet_id):
    """Retourne (titre, [(gid, nom), ...]) à partir de la page htmlview."""
    _, body = _get(BASE.format(id=sheet_id) + "/htmlview")
    html = body.decode("utf-8", "replace")
    tabs = [(gid, _js_unescape(name)) for name, gid in
            re.findall(r'items\.push\(\{name: "((?:[^"\\]|\\.)*)", pageUrl: "(?:[^"\\]|\\.)*", gid: "(\d+)"', html)]
    if not tabs:
        raise TransientError("aucun onglet trouvé dans htmlview (format de page inattendu ?)")
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    title = re.sub(r"\s*-\s*Google\s+(Drive|Sheets|Docs)\s*$", "", html_mod.unescape(m.group(1)).strip()) if m else sheet_id
    return title, tabs


def normalize_cell(value):
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def normalize_grid(rows):
    grid = []
    for row in rows:
        cells = [normalize_cell(c) for c in row]
        while cells and not cells[-1]:
            cells.pop()
        grid.append(cells)
    while grid and not grid[-1]:
        grid.pop()
    return grid


def fetch_tab(sheet_id, gid):
    ctype, body = _get(BASE.format(id=sheet_id) + f"/export?format=csv&gid={gid}")
    if "text/csv" not in ctype:
        raise TransientError(f"onglet {gid} : réponse {ctype!r} au lieu d'un CSV")
    return normalize_grid(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def tab_url(sheet_id, gid):
    return BASE.format(id=sheet_id) + f"/edit?gid={gid}#gid={gid}"
