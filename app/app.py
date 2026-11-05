"""App Flask : réconciliation SAP / QBs avec mapping des noms par LLM (Ollama).

Parcours :
  1. Upload des deux fichiers Excel.
  2. Noms fournisseurs uniques des deux fichiers -> le LLM propose le nom SAP
     correspondant à chaque nom QBs (en arrière-plan, avec progression),
     puis, pour les fiches SAP non utilisées, s'il s'agit d'un doublon SAP.
  3. Écran de validation : on corrige si besoin, puis "Enregistrer et calculer".
  4. Le mapping validé est enregistré (data/name_mapping.json, réutilisé la
     prochaine fois) et le script calcule l'Excel de sortie.

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
from flask import (Flask, abort, jsonify, redirect, render_template_string, request,
                   send_file, url_for)

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

BASE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Réconciliation SAP / QBs</title>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --text:#1f2933; --muted:#616e7c; --line:#d9dee4;
          --accent:#2563eb; --ok:#15803d; --nok:#b91c1c; --rev:#a16207; --warn:#fff7ed; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
  main { max-width:980px; margin:36px auto; padding:0 16px; }
  h1 { font-size:1.5rem; margin:0 0 4px; } h1 a { color:inherit; text-decoration:none; }
  h2 { font-size:1.1rem; margin:0 0 10px; }
  p.sub { color:var(--muted); margin:0 0 24px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:22px; margin-bottom:20px; }
  label { display:block; font-weight:600; margin:0 0 6px; }
  .field { margin-bottom:18px; }
  .hint, .muted { color:var(--muted); font-size:.85rem; }
  input[type=file] { width:100%; padding:10px; border:1px dashed var(--line); border-radius:8px; background:#fafbfc; }
  button, a.btn { display:inline-block; background:var(--accent); color:#fff; border:0; border-radius:8px;
                  padding:11px 20px; font-size:1rem; font-weight:600; cursor:pointer; text-decoration:none; }
  a.btn.secondary { background:#e5e7eb; color:var(--text); }
  button:disabled { opacity:.6; cursor:wait; }
  .error { background:#fef2f2; border-color:#fecaca; color:var(--nok); }
  table { width:100%; border-collapse:collapse; }
  th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }
  th { font-size:.85rem; color:var(--muted); font-weight:600; }
  td.num, th.num { text-align:right; }
  .OK { color:var(--ok); } .NOK { color:var(--nok); } .Reversed { color:var(--rev); }
  select { width:100%; padding:6px 8px; border:1px solid var(--line); border-radius:6px; font-size:.9rem; background:#fff; }
  tr.none td { background:var(--warn); }
  .tag { font-size:.75rem; padding:2px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; white-space:nowrap; }
  .tag.saved { background:#ecfdf5; color:#065f46; }
  .bar { height:10px; background:#e5e7eb; border-radius:999px; overflow:hidden; margin:12px 0 8px; }
  .bar > div { height:100%; background:var(--accent); width:0; transition:width .4s; }
  .actions { display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin-top:16px; }
  .sticky { position:sticky; bottom:0; background:var(--card); padding-top:12px; border-top:1px solid var(--line); }
  .filter { display:flex; gap:16px; margin-bottom:12px; font-size:.9rem; }
  @media (max-width:640px) { th.hide, td.hide { display:none; } }
</style>
</head>
<body>
<main>
  <h1><a href="{{ url_for('index') }}">Réconciliation SAP / QBs</a></h1>
  <p class="sub">{% block sub %}{% endblock %}</p>
  {% if error %}<div class="card error"><strong>Erreur :</strong> {{ error }}</div>{% endif %}
  {% block body %}{% endblock %}
</main>
</body>
</html>"""

UPLOAD = BASE.replace("{% block sub %}{% endblock %}",
                      "Étape 1/3 : charge les deux exports Excel.").replace("{% block body %}{% endblock %}", """
  <form class="card" method="post" enctype="multipart/form-data"
        onsubmit="this.querySelector('button').disabled=true;this.querySelector('button').textContent='Envoi…'">
    <div class="field">
      <label for="sap">Fichier SAP (.xlsx)</label>
      <input id="sap" name="sap" type="file" accept=".xlsx" required>
      <div class="hint">Colonnes : Supplier, Supplier Name, Journal Entry Date, Journal Entry, Amount (CoCode Crcy), Clearing Status</div>
    </div>
    <div class="field">
      <label for="qbs">Fichier QBs (.xlsx)</label>
      <input id="qbs" name="qbs" type="file" accept=".xlsx" required>
      <div class="hint">Colonnes : Name, Date, Memo, Debit, Credit</div>
    </div>
    <button type="submit">Lancer le mapping des noms</button>
    <p class="muted">Le LLM ({{ model }}) propose une correspondance pour chaque fournisseur QBs.
      Les noms déjà validés sont repris directement.</p>
  </form>""")

PROGRESS = BASE.replace("{% block sub %}{% endblock %}",
                        "Étape 2/3 : le LLM rapproche les noms fournisseurs.").replace("{% block body %}{% endblock %}", """
  <div class="card">
    <h2>Mapping en cours…</h2>
    <div class="bar"><div id="bar"></div></div>
    <div id="txt" class="muted">Démarrage (le premier chargement du modèle peut prendre ~1 min)…</div>
  </div>
  <script>
    async function poll() {
      try {
        const r = await fetch("{{ url_for('progress', job=job) }}");
        const d = await r.json();
        if (d.state === "ready" || d.state === "error") { location.reload(); return; }
        if (d.total) {
          document.getElementById("bar").style.width = (100 * d.done / d.total) + "%";
          document.getElementById("txt").textContent = (d.phase || "") + " : " + d.done + " / " + d.total + " — " + (d.current || "");
        }
      } catch (e) {}
      setTimeout(poll, 1500);
    }
    poll();
  </script>""")

REVIEW = BASE.replace("{% block sub %}{% endblock %}",
                      "Étape 3/3 : vérifie les correspondances, corrige si besoin, puis enregistre.").replace("{% block body %}{% endblock %}", """
  <form class="card" method="post" action="{{ url_for('save', job=job) }}"
        onsubmit="const b=this.querySelector('button');b.disabled=true;b.textContent='Calcul…'">
    {% if n_llm %}
    <h2>{{ n_llm }} nouveau{{ 'x' if n_llm > 1 }} fournisseur{{ 's' if n_llm > 1 }} QBs à vérifier</h2>
    <p class="muted">{{ n_saved }} fournisseurs déjà validés (masqués, repris sans LLM) ·
      <strong>{{ n_none_new }} nouveau{{ 'x' if n_none_new > 1 }} sans correspondance</strong> (surlignés).</p>
    {% else %}
    <h2>Aucun nouveau fournisseur</h2>
    <p class="muted">Les {{ n_saved }} fournisseurs QBs sont déjà validés : rien à demander au LLM.
      Tu peux calculer directement.</p>
    {% endif %}
    <div class="filter">
      <label><input type="checkbox" id="showSaved" {{ 'checked' if not n_llm }}> Afficher aussi les noms déjà validés</label>
      <label><input type="checkbox" id="onlyNone"> Seulement les sans correspondance</label>
    </div>
    <table id="map">
      <tr><th>Fournisseur QBs</th><th>Fournisseur SAP correspondant</th><th class="hide">Source</th></tr>
      {% for r in rows %}
      <tr class="{{ 'none' if not r.sap }}" data-saved="{{ 1 if r.source == 'enregistré' else 0 }}">
        <td>{{ r.qbs }}<input type="hidden" name="q{{ loop.index0 }}" value="{{ r.qbs }}"></td>
        <td>
          <select name="m{{ loop.index0 }}" onchange="this.closest('tr').classList.toggle('none', !this.value)">
            <option value="">— aucun (nouveau fournisseur) —</option>
            {% for s in sap_names %}<option value="{{ s }}" {{ 'selected' if s == r.sap }}>{{ s }}</option>{% endfor %}
          </select>
        </td>
        <td class="hide"><span class="tag {{ 'saved' if r.source == 'enregistré' }}">{{ r.source }}</span></td>
      </tr>
      {% endfor %}
    </table>

    {% if aliases %}
    <h2 style="margin-top:28px">Fournisseurs SAP sans nom QBs : doublon d'une autre fiche SAP ?</h2>
    <p class="muted">Ex. « GVA Rwanda » et « GVA RWANDA LTD » = 2 fiches SAP du même fournisseur.
      Choisis la fiche principale pour les regrouper, sinon laisse « non ».</p>
    <table id="alias">
      <tr><th>Fournisseur SAP</th><th>Même fournisseur que</th><th class="hide">Source</th></tr>
      {% for a in aliases %}
      <tr data-saved="{{ 1 if a.source == 'enregistré' else 0 }}">
        <td>{{ a.name }}<input type="hidden" name="n{{ loop.index0 }}" value="{{ a.name }}"></td>
        <td>
          <select name="a{{ loop.index0 }}">
            <option value="">— non, fournisseur distinct —</option>
            {% for s in sap_names if s != a.name %}<option value="{{ s }}" {{ 'selected' if s == a.sap }}>{{ s }}</option>{% endfor %}
          </select>
        </td>
        <td class="hide"><span class="tag {{ 'saved' if a.source == 'enregistré' }}">{{ a.source }}</span></td>
      </tr>
      {% endfor %}
    </table>
    {% endif %}

    <div class="actions sticky">
      <button type="submit">Enregistrer et calculer</button>
      <a class="btn secondary" href="{{ url_for('index') }}">Annuler</a>
    </div>
  </form>
  <script>
    const showSaved = document.getElementById("showSaved"), onlyNone = document.getElementById("onlyNone");
    function applyFilter() {
      document.querySelectorAll("#map tr[data-saved], #alias tr[data-saved]").forEach(tr => {
        const hide = (tr.dataset.saved === "1" && !showSaved.checked)
                  || (onlyNone.checked && tr.closest("#map") && !tr.classList.contains("none"));
        tr.style.display = hide ? "none" : "";
      });
    }
    showSaved.addEventListener("change", applyFilter);
    onlyNone.addEventListener("change", applyFilter);
    applyFilter();
  </script>""")

RESULT = BASE.replace("{% block sub %}{% endblock %}",
                      "Terminé : mapping enregistré et calcul effectué.").replace("{% block body %}{% endblock %}", """
  <div class="card">
    <h2>Résultat</h2>
    <p>{{ n_sap }} lignes SAP + {{ n_qbs }} lignes QBs traitées.</p>
    <table style="margin:12px 0 18px">
      <tr><th>System</th>{% for s in statuses %}<th class="num {{ s }}">{{ s }}</th>{% endfor %}<th class="num">Total</th></tr>
      {% for sys, counts in table.items() %}
      <tr><td>{{ sys }}</td>{% for s in statuses %}<td class="num">{{ counts.get(s, 0) }}</td>{% endfor %}<td class="num">{{ counts.values()|sum }}</td></tr>
      {% endfor %}
    </table>
    <div class="actions">
      <a class="btn" href="{{ url_for('download', job=job) }}">Télécharger l'Excel</a>
      <a class="btn secondary" href="{{ url_for('review', job=job) }}">Modifier le mapping</a>
      <a class="btn secondary" href="{{ url_for('index') }}">Nouveau traitement</a>
    </div>
  </div>""")


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
        return render_template_string(UPLOAD, model=OLLAMA_MODEL)
    try:
        sap_file, qbs_file = check_upload("sap"), check_upload("qbs")
    except ValueError as e:
        return render_template_string(UPLOAD, model=OLLAMA_MODEL, error=str(e)), 400

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
        return render_template_string(PROGRESS, job=job)
    if s["state"] == "error":
        return render_template_string(UPLOAD, model=OLLAMA_MODEL, error=s["error"])
    sugg = s["suggestions"]
    rows = [{"qbs": q, "sap": v["sap"], "source": v["source"]} for q, v in sugg.items()]
    rows.sort(key=lambda r: (r["source"] != "LLM", r["qbs"]))   # nouveaux noms en premier
    aliases = [{"name": n, "sap": v["sap"], "source": v["source"]} for n, v in s["aliases"].items()]
    return render_template_string(
        REVIEW, job=job, rows=rows, aliases=aliases, sap_names=choices(s),
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
        return render_template_string(UPLOAD, model=OLLAMA_MODEL, error=f"{type(e).__name__} : {e}"), 500

    df = pd.read_excel(d / "output.xlsx")
    counts = df.assign(Status=df["Status"].fillna("(vide)").astype(str)).groupby(["System", "Status"]).size()
    present = set(counts.index.get_level_values(1))
    statuses = [x for x in ("OK", "Reversed", "NOK", "0", "(vide)") if x in present]
    table = {sys_: counts[sys_].to_dict() for sys_ in counts.index.get_level_values(0).unique()}
    return render_template_string(RESULT, job=job, n_sap=len(sap), n_qbs=len(qbs),
                                  statuses=statuses, table=table)


@app.route("/download/<job>")
def download(job):
    path = job_dir(job) / "output.xlsx"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=f"output_{datetime.now():%Y%m%d_%H%M}.xlsx")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
