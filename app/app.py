"""App Flask : réconciliation SAP / QBs avec mapping des noms par LLM (Ollama).

Parcours :
  1. Upload des deux fichiers Excel.
  2. Noms fournisseurs uniques des deux fichiers -> le LLM propose le nom SAP
     correspondant à chaque nom QBs (en arrière-plan, avec progression),
     puis, pour les fiches SAP non utilisées, s'il s'agit d'un doublon SAP.
  3. Écran de validation : on corrige si besoin, puis "Suivant".
  4. Score IA : précision de l'agent IA (propositions gardées / modifiées).
  5. "Enregistrer et calculer" : le mapping validé est enregistré
     (data/name_mapping.json, réutilisé la prochaine fois) et le script
     calcule l'Excel de sortie.

Pages HTML : app/templates/ · CSS et JS : app/static/

Lancer (production, Gunicorn) :
    docker compose up -d   (ou : gunicorn -c script/gunicorn.conf.py)
Lancer (développement, serveur Flask) :
    python app/app.py
puis ouvrir http://127.0.0.1:8080   (autre port : variable PORT)
Modèle : OLLAMA_MODEL (défaut gemma4:e2b), serveur : OLLAMA_URL (défaut http://127.0.0.1:11434)
"""
import os
import shutil
import sys
import tempfile
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file,
                   url_for)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "script"))
from merge_files import describe_dates, filter_period, load_qbs, load_sap, write_output  # noqa: E402
from name_mapping import (OLLAMA_MODEL, judge_choice, save_store,  # noqa: E402
                          suggest_mapping, suggest_sap_aliases, unique_names)

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


def asset(filename: str) -> str:
    """URL d'un fichier statique avec sa date de modification (?v=…) : le navigateur
    recharge le CSS / JS dès qu'il change, au lieu de garder l'ancien en cache."""
    try:
        version = int((Path(app.static_folder) / filename).stat().st_mtime)
    except OSError:
        version = 0
    return url_for("static", filename=filename, v=version)


@app.after_request
def no_cache_html(resp):
    """Pages HTML jamais servies depuis le cache du navigateur (toujours la dernière version)."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def common():
    return {"model": OLLAMA_MODEL, "asset": asset}


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


MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def fr_date(d) -> str:
    return pd.Timestamp(d).strftime("%d/%m/%Y")


def read_period(form) -> tuple[date | None, date | None]:
    """Période choisie sur la page d'upload (champs date_from / date_to, optionnels)."""
    try:
        start = date.fromisoformat(form["date_from"]) if form.get("date_from") else None
        end = date.fromisoformat(form["date_to"]) if form.get("date_to") else None
    except ValueError:
        raise ValueError("date de période invalide.") from None
    if start and end and start > end:
        raise ValueError(f"la période commence après sa fin ({fr_date(start)} > {fr_date(end)}).")
    return start, end


def period_label(start, end) -> str | None:
    if start and end:
        return f"du {fr_date(start)} au {fr_date(end)}"
    if start:
        return f"à partir du {fr_date(start)}"
    if end:
        return f"jusqu'au {fr_date(end)}"
    return None


def load_job(job: str):
    """Fichiers SAP et QBs du traitement, limités à la période choisie."""
    d, start, end = JOBS_DIR / job, *JOBS[job].get("period", (None, None))
    return (filter_period(load_sap(d / "sap.xlsx"), start, end),
            filter_period(load_qbs(d / "QBs.xlsx"), start, end))


UPLOADS = (("sap", "sap.xlsx", load_sap), ("qbs", "QBs.xlsx", load_qbs))


@app.route("/inspect", methods=["POST"])
def inspect():
    """Contrôle de chaque fichier déposé (format .xlsx + colonnes obligatoires), dès son
    dépôt, puis dates trouvées dans les deux fichiers (pour choisir la période).
    Réponse : {sap: {ok, error | infos}, qbs: {...}, ready, min, max, months…}."""
    out, loaded = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for key, name, loader in UPLOADS:
            f = request.files.get(key)
            if not f or not f.filename:
                continue
            try:
                if not f.filename.lower().endswith(".xlsx"):
                    raise ValueError(f"format refusé : « {f.filename} » n’est pas un fichier .xlsx.")
                f.save(Path(tmp) / name)
                loaded[key] = loader(Path(tmp) / name)
            except ValueError as e:
                out[key] = {"ok": False, "error": str(e)}
            except Exception as e:
                out[key] = {"ok": False, "error": f"lecture impossible ({type(e).__name__} : {e})."}

    all_dates = []
    for key, df in loaded.items():
        d = pd.to_datetime(df["Journal Entry Date"]).dropna().dt.normalize()
        all_dates.append(d)
        info = df.attrs.get("dates", {})
        out[key] = {"ok": True, "n": len(df), "no_date": int(len(df) - len(d)),
                    "dates": d.dt.strftime("%Y-%m-%d").tolist(),
                    "desc": describe_dates(info), "problems": info.get("invalid", 0)}
    out["ready"] = len(loaded) == 2
    if not out["ready"]:
        return jsonify(out)
    every = pd.concat(all_dates)
    if every.empty:
        out.update(ready=False, error="aucune date trouvée dans la colonne Journal Entry Date / Date.")
        return jsonify(out)
    months = sorted(every.dt.to_period("M").unique())
    out.update(
        min=every.min().strftime("%Y-%m-%d"), max=every.max().strftime("%Y-%m-%d"),
        min_fr=fr_date(every.min()), max_fr=fr_date(every.max()),
        months=[{"label": f"{MONTHS[m.month - 1]} {m.year}",
                 "from": m.start_time.strftime("%Y-%m-%d"), "to": m.end_time.strftime("%Y-%m-%d")}
                for m in months])
    return jsonify(out)


def run_mapping(job: str) -> None:
    """Thread : lit les noms uniques et demande au LLM les correspondances."""
    state = JOBS[job]
    try:
        sap, qbs = load_job(job)
        sap_names, qbs_names = unique_names(sap["Supplier Name"]), unique_names(qbs["Supplier Name"])
        state.update(total=len(qbs_names), sap_names=sap_names)

        def progress(done, total, name):
            state.update(done=done, total=total, current=name)

        state["phase"] = "Noms QBs"
        state["suggestions"] = suggest_mapping(sap_names, qbs_names, progress)
        used = {v["sap"] for v in state["suggestions"].values() if v["sap"]}
        state.update(phase="Doublons SAP", done=0, total=0)
        state["aliases"] = suggest_sap_aliases(sap_names, used, progress)
        # propositions d'origine de l'agent IA : base du score de précision,
        # gardées même si l'utilisateur revient modifier le mapping
        state["ai_map"] = {q: v["sap"] for q, v in state["suggestions"].items() if v["source"] == "LLM"}
        state["ai_alias"] = {n: v["sap"] for n, v in state["aliases"].items() if v["source"] == "LLM"}
        # choix déjà enregistrés au chargement : pour détecter leurs modifications
        state["saved_map"] = {q: v["sap"] for q, v in state["suggestions"].items() if v["source"] != "LLM"}
        state["saved_alias"] = {n: v["sap"] for n, v in state["aliases"].items() if v["source"] != "LLM"}
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
        start, end = read_period(request.form)
    except ValueError as e:
        return render_template("upload.html", step=1, error=str(e)), 400

    job = uuid.uuid4().hex[:12]
    d = JOBS_DIR / job
    d.mkdir(parents=True)
    # noms fixes -> colonne System = "SAP" / "QBs"
    sap_file.save(d / "sap.xlsx")
    qbs_file.save(d / "QBs.xlsx")
    JOBS[job] = {"state": "running", "done": 0, "total": 0, "current": "",
                 "period": (start, end), "period_label": period_label(start, end)}
    try:
        sap, qbs = load_job(job)   # contrôle du format et des colonnes des deux fichiers
    except Exception as e:
        del JOBS[job]
        shutil.rmtree(d, ignore_errors=True)
        msg = str(e) if isinstance(e, ValueError) else f"lecture impossible ({type(e).__name__} : {e})."
        return render_template("upload.html", step=1, error=f"fichier refusé : {msg}"), 400
    if sap.empty and qbs.empty:
        del JOBS[job]
        shutil.rmtree(d, ignore_errors=True)
        return render_template("upload.html", step=1,
                               error=f"aucune ligne SAP ni QBs {period_label(start, end)}."), 400
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
    s.pop("pending", None)   # choix modifiables : le score devra être recalculé
    s.pop("judge", None)
    sugg, ai_map, ai_alias = s["suggestions"], s["ai_map"], s["ai_alias"]
    saved_map, saved_alias = s["saved_map"], s["saved_alias"]
    rows = [{"qbs": q, "sap": v["sap"], "source": v["source"], "is_ai": q in ai_map, "ai": ai_map.get(q),
             "orig": saved_map.get(q)} for q, v in sugg.items()]
    rows.sort(key=lambda r: (r["source"] != "LLM", r["qbs"]))   # nouveaux noms en premier
    aliases = [{"name": n, "sap": v["sap"], "source": v["source"], "is_ai": n in ai_alias,
                "ai": ai_alias.get(n), "orig": saved_alias.get(n)} for n, v in s["aliases"].items()]
    return render_template(
        "review.html", step=3, job=job, period=s.get("period_label"), rows=rows, aliases=aliases, sap_names=choices(s),
        n_llm=sum(r["source"] == "LLM" for r in rows),
        n_saved=sum(r["source"] == "enregistré" for r in rows),
        n_none_new=sum(not r["sap"] and r["source"] == "LLM" for r in rows))


def ai_score(ai_map: dict, mapping: dict, ai_alias: dict, aliases: dict) -> tuple[int, int]:
    """Précision de l'agent IA : nombre de propositions gardées telles quelles par
    l'utilisateur, sur le nombre total de propositions (noms QBs + doublons SAP).
    « Aucune correspondance » gardé tel quel compte aussi comme juste."""
    pairs = [(v, mapping.get(k)) for k, v in ai_map.items() if k in mapping]
    pairs += [(v, aliases.get(k)) for k, v in ai_alias.items() if k in aliases]
    return sum((a or None) == (b or None) for a, b in pairs), len(pairs)


def choices(s: dict) -> list[str]:
    """Noms SAP proposés dans les listes : ceux du fichier + ceux déjà enregistrés
    (un fournisseur validé peut être absent du fichier SAP de ce mois)."""
    saved = {v["sap"] for v in list(s["suggestions"].values()) + list(s["aliases"].values()) if v["sap"]}
    return sorted(set(s["sap_names"]) | saved)


@app.route("/job/<job>/score", methods=["POST"])
def score(job):
    """Étape 4 : lit les choix de la validation et calcule la précision de
    l'agent IA (ce que l'utilisateur a gardé / modifié). Rien n'est encore enregistré."""
    job_dir(job)
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

    # les choix sont gardés dans le traitement : retour possible sur la validation
    mapping = {}
    for q, v in pairs("q", "m", s["suggestions"]):
        mapping[q] = v if v in sap_names else None
        s["suggestions"][q] = {"sap": mapping[q], "source": s["suggestions"][q]["source"]}
    aliases = {}
    for n, v in pairs("n", "a", s["aliases"]):
        aliases[n] = v if v in sap_names and v != n else None
        s["aliases"][n] = {"sap": aliases[n], "source": s["aliases"][n]["source"]}

    kept, total = ai_score(s["ai_map"], mapping, s["ai_alias"], aliases)

    # toutes les modifications : propositions de l'agent IA changées ET choix déjà
    # enregistrés changés. Chacune sera vérifiée par l'agent IA (nom de base + nouveau choix).
    changes = []
    for kind, label, chosen, refs in (
            ("Nom QBs", "QuickBooks", mapping, [("Agent IA", s["ai_map"]), ("Enregistré", s["saved_map"])]),
            ("Doublon SAP", "SAP", aliases, [("Agent IA", s["ai_alias"]), ("Enregistré", s["saved_alias"])])):
        for origin, ref in refs:
            for name, before in ref.items():
                if name in chosen and (before or None) != chosen[name]:
                    changes.append({"kind": kind, "label": label, "name": name, "origin": origin,
                                    "before": before, "after": chosen[name]})

    s["pending"] = {"mapping": mapping, "aliases": aliases, "ai_kept": kept, "ai_total": total}
    judge = {"state": "running", "phase": "Vérification", "done": 0, "total": len(changes), "current": "",
             "changes": changes}
    s["judge"] = judge
    threading.Thread(target=run_judge, args=(judge,), daemon=True).start()
    return redirect(url_for("score_view", job=job))


def run_judge(judge: dict) -> None:
    """Thread : l'agent IA vérifie chaque choix modifié (nom de base + nouveau choix)."""
    try:
        for i, c in enumerate(judge["changes"], start=1):
            judge["current"] = c["name"]
            if c["after"]:
                r = judge_choice(c["name"], c["after"], c["label"])
                c["ia_score"], c["ia_raison"] = r["score"], r["raison"]
            else:
                c["ia_score"], c["ia_raison"] = None, "aucun fournisseur SAP choisi : rien à vérifier"
            judge["done"] = i
        judge["state"] = "ready"
    except Exception as e:
        judge.update(state="error", error=f"{type(e).__name__} : {e}")


def mod_score(changes: list) -> int | None:
    """Score des modifications : moyenne des scores donnés par l'agent IA."""
    scores = [c["ia_score"] for c in changes if c.get("ia_score") is not None]
    return round(sum(scores) / len(scores)) if scores else None


@app.route("/job/<job>/score")
def score_view(job):
    job_dir(job)
    s = JOBS.get(job) or abort(404)
    judge, p = s.get("judge"), s.get("pending")
    if not judge or not p:
        return redirect(url_for("review", job=job))
    if judge["state"] == "running":
        return render_template("progress.html", step=4, job=job,
                               progress_url=url_for("score_progress", job=job),
                               title="Vérification de tes modifications par l’agent IA…",
                               hint="Chaque choix modifié est envoyé à l’agent IA avec le nom de base.")
    changes = judge["changes"]
    p["mod_score"] = mod_score(changes)
    return render_template("score.html", step=4, job=job, period=s.get("period_label"), changes=changes, mod=p["mod_score"],
                           n_checked=sum(c.get("ia_score") is not None for c in changes),
                           ai_kept=p["ai_kept"], ai_total=p["ai_total"], judge_error=judge.get("error"))


@app.route("/job/<job>/score/progress")
def score_progress(job):
    s = JOBS.get(job) or abort(404)
    j = s.get("judge") or abort(404)
    return jsonify({k: j.get(k) for k in ("state", "phase", "done", "total", "current")})


@app.route("/job/<job>/save", methods=["POST"])
def save(job):
    """Étape 5 : enregistre le mapping validé à l'étape Score IA et calcule l'Excel."""
    d = job_dir(job)
    s = JOBS.get(job) or abort(404)
    p = s.get("pending")
    if s.get("state") != "ready" or not p:
        return redirect(url_for("review", job=job))   # passer d'abord par l'étape Score IA
    if s.get("judge", {}).get("state") == "running":
        return redirect(url_for("score_view", job=job))
    mapping, aliases = p["mapping"], p["aliases"]

    save_store(mapping, aliases)  # réutilisé au prochain traitement
    try:
        sap, qbs = load_job(job)
        write_output(sap, qbs, d / "output.xlsx", mapping, aliases)
    except Exception as e:
        return render_template("upload.html", step=1, error=f"{type(e).__name__} : {e}"), 500

    df = pd.read_excel(d / "output.xlsx")
    counts = df.assign(Status=df["Status"].fillna("(vide)").astype(str)).groupby(["System", "Status"]).size()
    present = set(counts.index.get_level_values(1))
    statuses = [x for x in STATUS_ORDER if x in present]
    table = {sys_: counts[sys_].to_dict() for sys_ in counts.index.get_level_values(0).unique()}
    totals = {st: sum(c.get(st, 0) for c in table.values()) for st in statuses}
    return render_template("result.html", step=5, job=job, period=s.get("period_label"),
                           n_sap=len(sap), n_qbs=len(qbs),
                           statuses=statuses, table=table, totals=totals,
                           status_class=STATUS_CLASS, status_hint=STATUS_HINT,
                           ai_kept=p["ai_kept"], ai_total=p["ai_total"], mod=p.get("mod_score"))


@app.route("/download/<job>")
def download(job):
    path = job_dir(job) / "output.xlsx"
    if not path.exists():
        abort(404)
    start, end = JOBS.get(job, {}).get("period", (None, None))
    span = f"_{start or 'debut'}_{end or 'fin'}" if (start or end) else ""
    return send_file(path, as_attachment=True,
                     download_name=f"output{span}_{datetime.now():%Y%m%d_%H%M}.xlsx")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8080)), debug=False, threaded=True)
