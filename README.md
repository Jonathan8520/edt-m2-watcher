# edt-m2-watcher

Surveille le [planning M2 IBI/SBI](https://docs.google.com/spreadsheets/d/ID_DU_SHEET/htmlview) et poste l'avant/après de chaque cellule modifiée sur Discord, avec ping `@ibi` / `@sbi` selon le bloc concerné.

## Fonctionnement

- **main** : le code. **datas** : le dernier état connu (`state/<id du sheet>/`, un CSV par onglet + `meta.json`), un commit par changement détecté, et les calendriers `ics/M2-IBI-….ics` / `ics/M2-SBI-….ics` régénérés à chaque changement.
- Le workflow `Surveillance EDT` tourne toutes les 20 min en journée (2 h la nuit), exporte chaque onglet en CSV, compare avec `datas`, notifie puis pousse le nouvel état.
- Les lignes insérées ou supprimées sont alignées (pas de faux changements en cascade). Contexte affiché : date + créneau pour le planning, ligne complète ailleurs.
- Attribution : bloc IBI ou SBI du sheet, ou mention explicite dans la ligne ; si rien ne permet de trancher, les deux rôles sont pingés.
- Sheet devenu privé ou supprimé : une seule alerte, puis une autre quand l'accès revient. Erreurs réseau passagères : ignorées jusqu'au passage suivant.
- Les changements de couleur seuls ne sont pas détectés (export CSV = valeurs).
- Le cron GitHub est « best effort » : un passage peut être retardé ou sauté, mais rien n'est perdu, le suivant compare avec le dernier état enregistré. Seule exception : une cellule modifiée puis remise à l'identique entre deux passages.
- Fiabilité du diff : tests de fuzzing (`python3 -m unittest`), mutations aléatoires contrôlées cellule par cellule.

## Calendriers ICS

Un calendrier par programme : chaque cellule de créneau devient un événement (9h30-12h30 ou 14h-17h, ou l'horaire écrit dans la cellule, ex. « partiel … 14h-16h »). Salle : celle de la cellule, sinon celle de la ligne, sinon la salle par défaut du programme ; « Zoom » si la cellule le mentionne. Jours fériés en journée entière. Les UID sont stables par créneau : un calendrier abonné met à jour les cours au lieu de les dupliquer.

```sh
SHEET_IDS=... python3 -m watcher ics --state datas/state --out /tmp/ics
```

## Configuration (Settings > Secrets and variables > Actions)

| Nom | Type | Valeur |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | secret | URL du webhook |
| `DISCORD_PINGS` | variable | `IBI=<@&id rôle>,SBI=<@id utilisateur>` : rôle (`<@&…>`) ou personne (`<@…>`). Sans ça, `@ibi`/`@sbi` s'affichent sans ping |
| `SHEET_IDS` | variable (optionnelle) | IDs de sheets séparés par des virgules. Par défaut : le planning M2 |

Pour obtenir la mention à coller : taper `\@sbi` ou `\@pseudo` dans Discord, le message affiche `<@&…>` ou `<@…>`.

## En local

```sh
SHEET_IDS=... python3 -m watcher scan --state /tmp/state --out /tmp/out
DISCORD_WEBHOOK_URL=... python3 -m watcher notify --report /tmp/out/report.json
python3 -m unittest
```

Python 3.10+, aucune dépendance.
