"""Mapping des noms fournisseurs QBs -> SAP avec un LLM local (Ollama).

1. On récupère les Supplier Name uniques des deux fichiers (sans doublons).
2. Les noms déjà validés (data/name_mapping.json) sont repris tels quels :
   le mapping se fait UNE seule fois par nom, jamais redemandé au LLM.
3. Pour chaque NOUVEAU nom QBs, le LLM choisit le nom SAP correspondant
   dans la liste (réponse contrainte : un nom SAP exact ou NONE).
4. L'utilisateur valide / corrige dans l'app, puis on enregistre.
"""
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "name_mapping.json"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b")

VARIANTS = ("upper/lower case, LTD/LIMITED/PLC, currency suffix (USD), abbreviations, typos, "
            "word order, French/English words, name repeated twice")

QBS_PROMPT = """You match supplier names between two accounting systems (SAP and QuickBooks) of the same company.
The same supplier can be written differently: """ + VARIANTS + """.
A supplier renamed "deleted" in SAP can match nothing by name.

SAP suppliers:
{sap_list}

Given one QuickBooks supplier name, answer with the SAP supplier that is the SAME company, copied exactly, or NONE if no SAP supplier is the same company."""

SAP_PROMPT = """You look for duplicate suppliers inside one SAP system: the same company can have been created twice with a different name.
The same supplier can be written differently: """ + VARIANTS + """.
Different companies with only a common generic word (PHARMACY, CLINIC, RWANDA, HOLDING...) are NOT the same.

SAP suppliers:
{sap_list}

Given one SAP supplier name, answer with ANOTHER SAP supplier of the list that is the SAME company, copied exactly, or NONE."""


def unique_names(series) -> list[str]:
    """Noms uniques, sans vides ni doublons (espaces en trop retirés)."""
    names = {str(n).strip() for n in series if pd.notna(n) and str(n).strip()}
    return sorted(names)


def load_store() -> dict:
    """{"qbs": {nom QBs: nom SAP|None}, "sap": {nom SAP: autre nom SAP|None}}"""
    store = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    return {"qbs": store.get("qbs", {}), "sap": store.get("sap", {})}


def save_store(qbs_map: dict, sap_map: dict | None = None) -> None:
    """Ajoute / met à jour les correspondances validées."""
    store = load_store()
    store["qbs"].update(qbs_map)
    store["sap"].update(sap_map or {})
    STORE.parent.mkdir(parents=True, exist_ok=True)
    out = {k: dict(sorted(v.items())) for k, v in store.items()}
    STORE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def ask_llm(name: str, candidates: list[str], system: str, label: str) -> str | None:
    schema = {
        "type": "object",
        "properties": {"sap": {"type": "string", "enum": candidates + ["NONE"]}},
        "required": ["sap"],
    }
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 8192},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{label} supplier: {name}"},
        ],
    }
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            answer = json.loads(json.load(r)["message"]["content"]).get("sap")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama injoignable sur {OLLAMA_URL} ({e.reason}). "
                           "Vérifie qu'Ollama tourne.") from None
    return answer if answer in candidates else None


JUDGE_PROMPT = """You check supplier name matches for an accounting reconciliation.
You get a base supplier name and the name a user chose as its match.
Rate from 0 to 100 how likely both names refer to the same company:
100 = certainly the same company (same name, abbreviation, legal suffix such as
LTD / Limited / SARL, typo, word order, accents or case only differ);
50 = possible but doubtful; 0 = clearly different companies.
Give a short reason in French (one sentence)."""


def judge_choice(base: str, choice: str, label: str = "QuickBooks") -> dict:
    """L'agent IA juge un choix fait à la main : {"score": 0..100, "raison": str}."""
    schema = {
        "type": "object",
        "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100},
                       "raison": {"type": "string"}},
        "required": ["score", "raison"],
    }
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 2048},
        "messages": [
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"Base {label} supplier: {base}\nChosen SAP supplier: {choice}"},
        ],
    }
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            answer = json.loads(json.load(r)["message"]["content"])
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama injoignable sur {OLLAMA_URL} ({e.reason}). "
                           "Vérifie qu'Ollama tourne.") from None
    score = max(0, min(100, int(answer.get("score", 0))))
    raison = str(answer.get("raison", "")).strip()
    # garde-fou : le petit modèle donne parfois un score élevé à deux noms sans
    # aucun rapport. Sans mot distinctif commun, le score est plafonné.
    if score > JUDGE_CAP and not shares_word(base, choice):
        score = JUDGE_CAP
        raison += f" (score plafonné à {JUDGE_CAP} : aucun mot distinctif en commun)"
    return {"score": score, "raison": raison}


JUDGE_CAP = 25
GENERIC_WORDS = {"ltd", "limited", "co", "company", "cie", "sarl", "sa", "plc", "inc", "llc", "group",
                 "groupe", "rwanda", "rw", "the", "and", "et", "de", "du", "des", "la", "le"}


def distinctive_words(name: str) -> set:
    words = re.sub(r"[^0-9a-z]+", " ", unicodedata.normalize("NFKD", name.lower())
                   .encode("ascii", "ignore").decode()).split()
    return {w for w in words if w not in GENERIC_WORDS}


def shares_word(a: str, b: str) -> bool:
    """Au moins un mot distinctif commun (ou l'un contient l'autre : RWANDACELL / MTN…),
    ou des noms très proches une fois les mots génériques retirés."""
    wa, wb = distinctive_words(a), distinctive_words(b)
    if not wa or not wb:
        return True   # rien de distinctif à comparer : on laisse le score de l'agent
    if wa & wb or any(x in y or y in x for x in wa for y in wb if min(len(x), len(y)) >= 3):
        return True
    return SequenceMatcher(None, " ".join(sorted(wa)), " ".join(sorted(wb))).ratio() >= 0.6


def _bullets(names):
    return "\n".join(f"- {n}" for n in names)


def suggest_mapping(sap_names: list[str], qbs_names: list[str], progress=None) -> dict:
    """{nom QBs: {"sap": nom SAP ou None, "source": "enregistré" | "LLM"}}.

    progress(fait, total, nom) est appelé après chaque nom (pour l'app web).
    """
    saved_map = load_store()["qbs"]
    system = QBS_PROMPT.format(sap_list=_bullets(sap_names))
    result = {}
    for i, name in enumerate(qbs_names, start=1):
        if name in saved_map:   # déjà validé une fois -> jamais redemandé au LLM
            result[name] = {"sap": saved_map[name], "source": "enregistré"}
        else:
            result[name] = {"sap": ask_llm(name, sap_names, system, "QuickBooks"), "source": "LLM"}
        if progress:
            progress(i, len(qbs_names), name)
    return result


def suggest_sap_aliases(sap_names: list[str], used: set, progress=None) -> dict:
    """Pour chaque nom SAP qu'aucun nom QBs n'utilise : est-ce une autre fiche
    d'un fournisseur SAP déjà utilisé ? (ex. "GVA Rwanda" = "GVA RWANDA LTD")

    {nom SAP: {"sap": autre nom SAP ou None, "source": ...}}
    """
    saved_map = load_store()["sap"]
    unused = [n for n in sap_names if n not in used]
    targets = [n for n in sap_names if n in used]
    system = SAP_PROMPT.format(sap_list=_bullets(targets))
    result = {}
    for i, name in enumerate(unused, start=1):
        if name in saved_map:
            result[name] = {"sap": saved_map[name], "source": "enregistré"}
        elif targets:
            result[name] = {"sap": ask_llm(name, targets, system, "SAP"), "source": "LLM"}
        else:
            result[name] = {"sap": None, "source": "LLM"}
        if progress:
            progress(i, len(unused), name)
    return result
