"""App Flask : réconciliation SAP / QBs avec mapping des noms par LLM (Ollama).

Parcours :
  1. Upload des deux fichiers Excel.
  2. Noms fournisseurs uniques des deux fichiers -> le LLM propose le nom SAP
     correspondant à chaque nom QBs (en arrière-plan, avec progression),
     puis, pour les fiches SAP non utilisées, s'il s'agit d'un doublon SAP.
  3. Écran de validation : on corrige si besoin, puis "Enregistrer et calculer".
  4. Le mapping validé est enregistré (data/name_mapping.json, réutilisé la
     prochaine fois) et le script calcule l'Excel de sortie.

Pages HTML : app/templates/ · CSS et JS : app/static/

Lancer :
    .venv/bin/python app/app.py
puis ouvrir http://127.0.0.1:5050   (autre port : PORT=8080 .venv/bin/python app/app.py)
Modèle : OLLAMA_MODEL (défaut gemma4:e2b), serveur : OLLAMA_URL (défaut http://127.0.0.1:11434)
"""
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   url_for)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "script"))
from merge_files import load_qbs, load_sap, write_output  # noqa: E402
from name_mapping import (OLLAMA_MODEL, save_store, suggest_mapping,  # noqa: E402
                          suggest_sap_aliases, unique_names)

JOBS_DIR = ROOT / "output" / "app"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 Mo

# état des traitements en cours (une seule personne à la fois suffit ici)
JOBS: dict[str, dict] = {}

# ordre d'affichage, classe CSS et explication de chaque Status
STATUS_ORDER = ("OK", "Reversed", "NOK", "0", "(vide)")
STATUS_CLASS = {"OK": "ok", "Reversed": "rev", "NOK": "nok", "0": "zero", "(vide)": "empty"}
STATUS_HINT = {
    "OK": "SAP = QBs, même clé",
    "Reversed": "écritures SAP annulées",
    "NOK": "sans correspondance",
    "0": "montant égal à 0",
    "(vide)": "non classées",
}


@app.context_processor
def common():
    return {"model": OLLAMA_MODEL}


def job_dir(job: str) -> Path:
    if not job.isalnum():
        abort(404)
    return JOBS_DIR / job


def check_upload(field: str):
    f = request.files.get(field)
    if not f or not f.filename:
        raise ValueError(f"fichier {field.upper()} manquant.")
    if not f.filename.lower().endswith(".xlsx"):
        raise ValueError(f"le fichier {field.upper()} doit être un .xlsx ({f.filename}).")
    return f


def run_mapping(job: str) -> None:
    """Thread : lit les noms uniques et demande au LLM les correspondances."""
    state = JOBS[job]
    try:
        d = JOBS_DIR / job
        sap, qbs = load_sap(d / "sap.xlsx"), load_qbs(d / "QBs.xlsx")
        sap_names, qbs_names = unique_names(sap["Supplier Name"]), unique_names(qbs["Supplier Name"])
        state.update(total=len(qbs_names), sap_names=sap_names)

        def progress(done, total, name):
            state.update(done=done, total=total, current=name)

        state["phase"] = "Noms QBs"
        state["suggestions"] = suggest_mapping(sap_names, qbs_names, progress)
        used = {v["sap"] for v in state["suggestions"].values() if v["sap"]}
        state.update(phase="Doublons SAP", done=0, total=0)
        state["aliases"] = suggest_sap_aliases(sap_names, used, progress)
        state["state"] = "ready"
    except KeyError as e:
        state.update(state="error", error=f"colonne introuvable : {e}. Vérifie que SAP et QBs "
                                          "sont dans le bon champ.")
    except Exception as e:
        state.update(state="error", error=f"{type(e).__name__} : {e}")


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("upload.html", step=1)
    try:
        sap_file, qbs_file = check_upload("sap"), check_upload("qbs")
    except ValueError as e:
        return render_template("upload.html", step=1, error=str(e)), 400

    job = uuid.uuid4().hex[:12]
    d = JOBS_DIR / job
    d.mkdir(parents=True)
    # noms fixes -> colonne System = "SAP" / "QBs"
    sap_file.save(d / "sap.xlsx")
    qbs_file.save(d / "QBs.xlsx")
    JOBS[job] = {"state": "running", "done": 0, "total": 0, "current": ""}
    threading.Thread(target=run_mapping, args=(job,), daemon=True).start()
    return redirect(url_for("review", job=job))


@app.route("/job/<job>/progress")
def progress(job):
    s = JOBS.get(job) or abort(404)
    return jsonify({k: s.get(k) for k in ("state", "phase", "done", "total", "current")})


@app.route("/job/<job>")
def review(job):
    job_dir(job)
    s = JOBS.get(job) or abort(404)
    if s["state"] == "running":
        return render_template("progress.html", step=2, job=job)
    if s["state"] == "error":
        return render_template("upload.html", step=1, error=s["error"])
    sugg = s["suggestions"]
    rows = [{"qbs": q, "sap": v["sap"], "source": v["source"]} for q, v in sugg.items()]
    rows.sort(key=lambda r: (r["source"] != "LLM", r["qbs"]))   # nouveaux noms en premier
    aliases = [{"name": n, "sap": v["sap"], "source": v["source"]} for n, v in s["aliases"].items()]
    return render_template(
        "review.html", step=3, job=job, rows=rows, aliases=aliases, sap_names=choices(s),
        n_llm=sum(r["source"] == "LLM" for r in rows),
        n_saved=sum(r["source"] == "enregistré" for r in rows),
        n_none_new=sum(not r["sap"] and r["source"] == "LLM" for r in rows))


def choices(s: dict) -> list[str]:
    """Noms SAP proposés dans les listes : ceux du fichier + ceux déjà enregistrés
    (un fournisseur validé peut être absent du fichier SAP de ce mois)."""
    saved = {v["sap"] for v in list(s["suggestions"].values()) + list(s["aliases"].values()) if v["sap"]}
    return sorted(set(s["sap_names"]) | saved)


@app.route("/job/<job>/save", methods=["POST"])
def save(job):
    d = job_dir(job)
    s = JOBS.get(job) or abort(404)
    if s.get("state") != "ready":
        abort(400)
    sap_names = set(choices(s))
    f = request.form

    def pairs(name_key, value_key, known):
        """(nom, choix) lus dans le formulaire, par nom et non par position."""
        i = 0
        while f"{name_key}{i}" in f:
            name = f[f"{name_key}{i}"]
            if name in known:
                yield name, f.get(f"{value_key}{i}", "")
            i += 1

    mapping = {}
    for q, v in pairs("q", "m", s["suggestions"]):
        mapping[q] = v if v in sap_names else None
        s["suggestions"][q] = {"sap": mapping[q], "source": "enregistré"}

    aliases = {}
    for n, v in pairs("n", "a", s["aliases"]):
        aliases[n] = v if v in sap_names and v != n else None
        s["aliases"][n] = {"sap": aliases[n], "source": "enregistré"}

    save_store(mapping, aliases)  # réutilisé au prochain traitement
    try:
        sap, qbs = load_sap(d / "sap.xlsx"), load_qbs(d / "QBs.xlsx")
        write_output(sap, qbs, d / "output.xlsx", mapping, aliases)
    except Exception as e:
        return render_template("upload.html", step=1, error=f"{type(e).__name__} : {e}"), 500

    df = pd.read_excel(d / "output.xlsx")
    counts = df.assign(Status=df["Status"].fillna("(vide)").astype(str)).groupby(["System", "Status"]).size()
    present = set(counts.index.get_level_values(1))
    statuses = [x for x in STATUS_ORDER if x in present]
    table = {sys_: counts[sys_].to_dict() for sys_ in counts.index.get_level_values(0).unique()}
    totals = {st: sum(c.get(st, 0) for c in table.values()) for st in statuses}
    return render_template("result.html", step=4, job=job, n_sap=len(sap), n_qbs=len(qbs),
                           statuses=statuses, table=table, totals=totals,
                           status_class=STATUS_CLASS, status_hint=STATUS_HINT)


@app.route("/download/<job>")
def download(job):
    path = job_dir(job) / "output.xlsx"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=f"output_{datetime.now():%Y%m%d_%H%M}.xlsx")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
