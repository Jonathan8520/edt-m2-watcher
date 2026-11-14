# edt-m2-watcher

Surveille un planning Google Sheets (M2 IBI/SBI) et poste l'avant/après de chaque cellule modifiée sur Discord, avec ping `@ibi` / `@sbi` selon le bloc concerné.

## Fonctionnement

- **Ce repo (public)** : le code seulement. **`edt-m2-watcher-data` (privé)** : le dernier état connu (`state/<id du sheet>/`, un CSV par onglet + `meta.json`), un commit par changement détecté, et les calendriers `ics/M2-IBI-….ics` / `ics/M2-SBI-….ics` régénérés à chaque changement. Le planning n'est donc jamais visible ici.
- Rien de confidentiel dans les logs : l'ID du sheet, le gist et les pings sont des secrets (masqués), et le résumé d'exécution ne donne que le nombre de changements par onglet.
- Le workflow `Surveillance EDT` tourne toutes les 20 min en journée (2 h la nuit), exporte chaque onglet en CSV, compare avec l'état du repo privé, notifie puis pousse le nouvel état. Il se réactive lui-même chaque nuit, sinon GitHub coupe le planifié d'un repo public après 60 jours sans commit.
- Les lignes insérées ou supprimées sont alignées (pas de faux changements en cascade). Contexte affiché : date + créneau pour le planning, ligne complète ailleurs.
- Attribution : bloc IBI ou SBI du sheet, ou mention explicite dans la ligne ; si rien ne permet de trancher, les deux rôles sont pingés.
- Sheet devenu privé ou supprimé : une seule alerte, puis une autre quand l'accès revient. Erreurs réseau passagères : ignorées jusqu'au passage suivant.
- Les changements de couleur seuls ne sont pas détectés (export CSV = valeurs).
- Le cron GitHub est « best effort » : un passage peut être retardé ou sauté, mais rien n'est perdu, le suivant compare avec le dernier état enregistré. Seule exception : une cellule modifiée puis remise à l'identique entre deux passages.
- Fiabilité du diff : tests de fuzzing (`python3 -m unittest`), mutations aléatoires contrôlées cellule par cellule.

## Calendriers ICS

Un calendrier par programme : chaque cellule de créneau devient un événement (9h30-12h30 ou 14h-17h, ou l'horaire écrit dans la cellule, ex. « partiel … 14h-16h »). Salle : celle de la cellule, sinon celle de la ligne, sinon la salle par défaut du programme ; « Zoom » si la cellule le mentionne. Jours fériés en journée entière. Les UID sont stables par créneau : un calendrier abonné met à jour les cours au lieu de les dupliquer.

Abonnement : le workflow recopie les `.ics` dans un gist secret (secret `ICS_GIST_ID`, secret `GIST_TOKEN` avec la permission Gists en écriture). URL d'abonnement : `https://gist.githubusercontent.com/Jonathan8520/<ICS_GIST_ID>/raw/<fichier>.ics`.

```sh
SHEET_IDS=... python3 -m watcher ics --state datas/state --out /tmp/ics
```

## Phrase de résumé

Chaque notification commence par une phrase courte écrite par un modèle d'IA (ex. « Le cours ISI8 est annulé pour cette année. »). Fournisseur par défaut : l'API native de Puter (`RECAP_ENDPOINT=https://api.puter.com/drivers/call`, token Puter dans le secret `RECAP_API_KEY`, modèles dans `RECAP_MODELS`). Toute API compatible OpenAI marche aussi. Si l'appel échoue, la notification part sans la phrase.

Tester sans rien envoyer : lancer le workflow « Test de la phrase de résumé » avec deux commits de `edt-m2-watcher-data`. Les logs étant publics, supprimer le run après lecture (pareil pour « Corriger un message » en mode « Afficher seulement »).

## Configuration (Settings > Secrets and variables > Actions)

| Nom | Type | Valeur |
| --- | --- | --- |
| `SHEET_IDS` | secret | ID du sheet (plusieurs : séparés par des virgules) |
| `DATA_TOKEN` | secret | Token fine-grained limité à `edt-m2-watcher-data`, permission Contents en lecture et écriture |
| `DISCORD_WEBHOOK_URL` | secret | URL du webhook |
| `DISCORD_PINGS` | secret | `IBI=<@&id rôle>,SBI=<@id utilisateur>` : rôle (`<@&…>`) ou personne (`<@…>`). Sans ça, `@ibi`/`@sbi` s'affichent sans ping |
| `ICS_GIST_ID` | secret | ID du gist secret des calendriers |
| `GIST_TOKEN` | secret | Token avec la permission Gists en écriture |
| `RECAP_API_KEY` | secret | Clé du fournisseur d'IA |
| `RECAP_MODELS`, `RECAP_ENDPOINT` | variables | Modèles et adresse du fournisseur d'IA |

Tout ce qui identifie le planning ou le salon doit rester en **secret** : sur un repo public, les variables s'affichent en clair dans les logs.

Pour obtenir la mention à coller : taper `\@sbi` ou `\@pseudo` dans Discord, le message affiche `<@&…>` ou `<@…>`.

## En local

```sh
SHEET_IDS=... python3 -m watcher scan --state /tmp/state --out /tmp/out
DISCORD_WEBHOOK_URL=... python3 -m watcher notify --report /tmp/out/report.json
python3 -m unittest
```

Python 3.10+, aucune dépendance.
