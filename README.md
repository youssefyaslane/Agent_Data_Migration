# Agent Data Migration — Réconciliation SAP / QuickBooks

Outil de rapprochement des écritures fournisseurs entre un export **SAP** et un export **QuickBooks (QBs)**.
Il fusionne les deux fichiers Excel, rapproche les noms de fournisseurs avec un LLM local (Ollama)
et calcule un statut pour chaque ligne : `OK`, `Reversed`, `NOK` ou `0`.

Deux façons de l'utiliser :

- **App web Flask** : upload des deux fichiers, validation du mapping des noms, téléchargement du résultat.
- **Script en ligne de commande** : `script/merge_files.py`.

## Fonctionnement

```
Fichier SAP ─┐
             ├─> Noms fournisseurs uniques ─> Mapping des noms ─> deuxieme_name ─> Status ─> output.xlsx
Fichier QBs ─┘                                 (mémorisé ou LLM)
```

### 1. Mapping des noms (`deuxieme_name`)

Un même fournisseur n'a pas toujours le même nom dans les deux systèmes
(ex. `Netis Holding Limited` dans SAP, `NETIS HOLDING USD` dans QBs).
La colonne `deuxieme_name` donne un nom commun : le nom SAP de référence.

- Les noms fournisseurs uniques des deux fichiers sont extraits (sans doublons).
- Pour chaque **nouveau** nom QBs, le LLM (`gemma4:e2b` via Ollama) choisit le nom SAP correspondant,
  ou « aucun » si le fournisseur n'existe pas dans SAP.
- Le LLM signale aussi les fiches SAP en double (ex. `GVA Rwanda` / `GVA RWANDA LTD`).
- Tu valides ou corriges dans l'app. Les choix sont enregistrés dans `data/name_mapping.json`.
- **Le mapping se fait une seule fois par nom** : au traitement suivant, les noms déjà validés sont
  repris directement, seuls les nouveaux noms passent par le LLM.

### 2. Calcul du Status

Une ligne est comparée sur la clé **`deuxieme_name` + `Journal Entry Date` + `Amount (CoCode Crcy)`**
(montant signé, au centime près). Les règles s'appliquent dans l'ordre, chacune ne remplit que les
statuts encore vides :

| # | Lignes | Règle | Status |
|---|--------|-------|--------|
| 1 | SAP + QBs | Appariement 1 pour 1 : une ligne SAP et une ligne QBs avec la même clé | `OK` |
| 2 | SAP | Lignes vides groupées par `Supplier` : la somme des montants = 0 | `Reversed` |
| 3 | QBs | Aucune ligne SAP avec la même clé | `NOK` |
| 4 | SAP | Par `Supplier`, triées par date : deux lignes de montants opposés (x / -x) sont annulées, on recommence jusqu'à ce qu'il n'y ait plus de paire | `Reversed`, le reste `NOK` |
| 5 | SAP + QBs | `Amount (CoCode Crcy)` = 0 (remplace le statut précédent) | `0` |

### 3. Fichier de sortie

`output.xlsx` contient les colonnes :

`Supplier` · `Supplier Name` · `deuxieme_name` · `Journal Entry Date` · `Journal Entry` ·
`Amount (CoCode Crcy)` · `System` · `Status` · `ABS`

`System` vaut `SAP` ou `QBs` selon le fichier d'origine. `ABS` est une formule Excel (`=ABS(montant)`).

## Fichiers d'entrée

| Fichier | Colonnes utilisées |
|---------|--------------------|
| SAP (`.xlsx`) | `Supplier`, `Supplier Name`, `Journal Entry Date`, `Journal Entry`, `Amount (CoCode Crcy)`, `Clearing Status` |
| QBs (`.xlsx`) | `Name`, `Date`, `Memo`, `Debit`, `Credit` (montant = Debit − Credit) |

**Dates QBs.** QuickBooks exporte les dates au format américain `MM/JJ/AAAA`. Si le fichier passe
par un Excel en français, les dates dont le jour est ≤ 12 sont converties avec jour et mois inversés
(`10/01/2025` = 1er octobre devient le 10 janvier) et les autres restent en texte. Le script détecte
ce mélange, lit le texte en `MM/JJ/AAAA` et remet jour et mois des dates converties à l'endroit.

## Installation

Prérequis : Docker avec Docker Compose, et [Ollama](https://ollama.com) installé sur la machine
avec le modèle `gemma4:e2b`.

```bash
git clone https://github.com/youssefyaslane/Agent_Data_Migration.git
cd Agent_Data_Migration
ollama pull gemma4:e2b
cp .env.example .env    # puis mettre APP_UID / APP_GID = résultat de `id -u` / `id -g`
mkdir -p data output
docker compose up -d --build
```

## Utilisation

### App web (Docker)

```bash
docker compose up -d --build   # construire et démarrer (aussi après un git pull)
docker compose logs -f         # suivre les logs
docker compose down            # arrêter
```

Le conteneur lance l'app avec Gunicorn (`script/gunicorn.conf.py`) et redémarre tout seul
avec la machine. Il utilise le réseau de la machine (`network_mode: host`) : il joint Ollama
sur `127.0.0.1:11434` et répond directement sur le port 8080. Les dossiers `data/`
(noms validés) et `output/` (fichiers générés) sont montés depuis la machine : ils sont
conservés quand le conteneur est reconstruit.

Puis ouvrir http://127.0.0.1:8080 :

1. Charger le fichier SAP et le fichier QBs.
2. Attendre la fin du mapping (seuls les nouveaux noms sont envoyés au LLM).
3. Vérifier ou corriger les correspondances, puis **Enregistrer et calculer**.
   La case « Afficher aussi les noms déjà validés » permet de corriger un ancien choix.
4. Télécharger l'Excel. La page affiche le nombre de lignes par Status.

### Ligne de commande (dans le conteneur)

```bash
# fichiers par défaut : data/sap.xlsx + data/QBs.xlsx -> output/output.xlsx
docker compose exec app python script/merge_files.py

# fichiers et mapping au choix
docker compose exec app python script/merge_files.py --sap data/sap.xlsx --qbs data/QBs.xlsx \
    --out output/output.xlsx --mapping data/name_mapping.json
```

Sans `--mapping`, le script reprend les noms enregistrés et demande les nouveaux au LLM,
sans étape de validation.

### Configuration

Les variables se règlent dans le fichier `.env` (modèle : `.env.example`).

| Variable | Défaut | Rôle |
|----------|--------|------|
| `APP_UID` / `APP_GID` | `1000` | Utilisateur du conteneur (propriétaire des fichiers de `data/` et `output/`) |
| `PORT` | `8080` | Port de l'app web |
| `HOST` | `0.0.0.0` | Adresse d'écoute de Gunicorn |
| `THREADS` | `8` | Nombre de threads Gunicorn |
| `LOG_LEVEL` | `info` | Niveau de logs Gunicorn |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Serveur Ollama |
| `OLLAMA_MODEL` | `gemma4:e2b` | Modèle utilisé pour le mapping |

## Structure

```
Dockerfile               Image de l'app (Python 3.12, Gunicorn)
docker-compose.yml       Service app : port 8080, volumes data/ et output/
.env.example             Modèle de configuration (.env, non versionné)
script/gunicorn.conf.py  Configuration Gunicorn (port 8080, 1 worker, threads)
app/app.py               App Flask (routes : upload, progression, validation, résultat)
app/templates/           Pages HTML (Jinja) : base, upload, progress, review, result
app/static/              style.css (design, mode sombre) et app.js (glisser-déposer, filtres, progression)
script/merge_files.py    Fusion, deuxieme_name, calcul du Status, écriture de l'Excel
script/name_mapping.py   Appels au LLM et mémoire des noms validés
data/                    Fichiers d'entrée et name_mapping.json (non versionnés)
output/                  Fichiers générés (non versionnés)
```

Les dossiers `data/` et `output/` contiennent des données comptables : ils sont exclus du dépôt
par le `.gitignore`.

### Note sur Gunicorn

`script/gunicorn.conf.py` lance **un seul worker** avec plusieurs threads. L'état des traitements
en cours est gardé en mémoire dans le processus : avec plusieurs workers, la page de
progression pourrait arriver sur un autre processus et répondre 404. Pour plusieurs workers,
il faudrait stocker cet état hors du processus (Redis, base de données…).
