# edt-m2-watcher

Surveille le [planning M2 IBI/SBI](https://docs.google.com/spreadsheets/d/ID_DU_SHEET/htmlview) et poste l'avant/après de chaque cellule modifiée sur Discord, avec ping `@ibi` / `@sbi` selon le bloc concerné.

## Fonctionnement

- **main** : le code. **datas** : le dernier état connu (un CSV par onglet + `meta.json`), un commit par changement détecté.
- Le workflow `Surveillance EDT` tourne toutes les 20 min en journée (2 h la nuit), exporte chaque onglet en CSV, compare avec `datas`, notifie puis pousse le nouvel état.
- Les lignes insérées ou supprimées sont alignées (pas de faux changements en cascade). Contexte affiché : date + créneau pour le planning, ligne complète ailleurs.
- Attribution : bloc IBI ou SBI du sheet, ou mention explicite dans la ligne ; si rien ne permet de trancher, les deux rôles sont pingés.
- Sheet devenu privé ou supprimé : une seule alerte, puis une autre quand l'accès revient. Erreurs réseau passagères : ignorées jusqu'au passage suivant.
- Les changements de couleur seuls ne sont pas détectés (export CSV = valeurs).

## Configuration (Settings > Secrets and variables > Actions)

| Nom | Type | Valeur |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | secret | URL du webhook |
| `DISCORD_ROLES` | variable | `IBI=<id rôle>,SBI=<id rôle>` (sans ça, `@ibi`/`@sbi` s'affichent sans ping) |

ID d'un rôle : taper `\@sbi` dans Discord, le message affiche `<@&ID>`.

## En local

```sh
SHEET_ID=... python3 -m watcher scan --state /tmp/state --out /tmp/out
DISCORD_WEBHOOK_URL=... python3 -m watcher notify --report /tmp/out/report.json
python3 -m unittest
```

Python 3.10+, aucune dépendance.
