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
import urllib.error
import urllib.request
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
