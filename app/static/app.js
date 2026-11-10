// Réconciliation SAP / QBs : comportements des pages

// Formulaires : bouton désactivé pendant l'envoi
document.querySelectorAll("form[data-busy]").forEach((form) => {
  form.addEventListener("submit", () => {
    const btn = form.querySelector("button[type=submit]");
    if (btn) {
      btn.disabled = true;
      btn.textContent = form.dataset.busy;
    }
  });
});

// Upload : nom du fichier choisi + glisser-déposer
document.querySelectorAll(".dropzone").forEach((zone) => {
  const input = zone.querySelector("input[type=file]");
  const label = zone.querySelector(".dz-file");
  const show = () => {
    const f = input.files[0];
    label.textContent = f ? f.name : label.dataset.empty;
    zone.classList.toggle("has-file", !!f);
  };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach((e) => input.addEventListener(e, () => zone.classList.add("drag")));
  ["dragleave", "drop"].forEach((e) => input.addEventListener(e, () => zone.classList.remove("drag")));
  show();
});

// Upload : dès que les deux fichiers sont déposés, lecture des dates
// (Journal Entry Date) et choix de la période.
// Les dates sont toujours affichées et saisies en jj/mm/aaaa, quelle que soit la
// langue du navigateur (un <input type="date"> suit la langue de Chrome : mm/dd/yyyy
// en anglais). Les champs cachés date_from / date_to envoient l'ISO AAAA-MM-JJ.
const uploadForm = document.querySelector("form[data-inspect-url]");
if (uploadForm) {
  const $ = (id) => document.getElementById(id);
  const sapIn = uploadForm.querySelector("#sap");
  const qbsIn = uploadForm.querySelector("#qbs");
  const box = $("period"), body = $("periodBody"), info = $("periodInfo");
  const from = $("dateFrom"), to = $("dateTo");
  const fromTxt = $("dateFromTxt"), toTxt = $("dateToTxt");
  const count = $("periodCount"), err = $("dateError");
  const hFrom = $("hFrom"), hTo = $("hTo"), range = $("range");
  const summary = $("submitSummary");
  const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
  const DAY = 864e5;
  let data = null;
  let seq = 0;
  let days = [];           // tous les jours de la période trouvée, en ISO

  const fr = (iso) => (iso ? iso.split("-").reverse().join("/") : "");
  const time = (iso) => Date.parse(iso + "T00:00:00Z");
  const iso = (t) => new Date(t).toISOString().slice(0, 10);
  const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;
  // « 1/11/2025 », « 01-11-25 », « 01.11.2025 », « 01112025 » -> « 2025-11-01 » ; null si invalide
  const parseFr = (txt) => {
    const t = txt.trim();
    if (!t) return "";
    const m = t.match(/^(\d{1,2})[\/.\-\s](\d{1,2})[\/.\-\s](\d{2}|\d{4})$/) || t.match(/^(\d{2})(\d{2})(\d{4})$/);
    if (!m) return null;
    const day = +m[1], month = +m[2], year = m[3].length === 2 ? 2000 + +m[3] : +m[3];
    const d = new Date(Date.UTC(year, month - 1, day));
    if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null;
    return d.toISOString().slice(0, 10);
  };
  const inRange = (dates) => dates.filter((d) => (!from.value || d >= from.value) && (!to.value || d <= to.value)).length;
  const dayIndex = (v) => Math.min(days.length - 1, Math.max(0, Math.round((time(v) - time(days[0])) / DAY)));

  // ---- frise : histogramme des lignes par jour + libellés de mois
  const drawRange = () => {
    days = [];
    for (let t = time(data.min); t <= time(data.max); t += DAY) days.push(iso(t));
    const per = {};
    [...data.sap.dates, ...data.qbs.dates].forEach((d) => { per[d] = (per[d] || 0) + 1; });
    const max = Math.max(1, ...Object.values(per));
    const hist = $("rangeHist");
    hist.innerHTML = "";
    days.forEach((d) => {
      const bar = document.createElement("span");
      const n = per[d] || 0;
      bar.style.height = (n ? Math.max(8, (100 * n) / max) : 3) + "%";
      bar.title = `${fr(d)} : ${plural(n, "ligne", "lignes")}`;
      bar.dataset.day = d;
      hist.appendChild(bar);
    });
    const lbl = $("rangeMonths");
    lbl.innerHTML = "";
    data.months.forEach((m) => {
      const a = dayIndex(m.from < data.min ? data.min : m.from);
      const b = dayIndex(m.to > data.max ? data.max : m.to);
      const w = ((b - a + 1) / days.length) * 100;
      const el = document.createElement("span");
      const [y, mo] = m.from.split("-");
      el.style.left = (a / days.length) * 100 + "%";
      el.style.width = w + "%";
      // libellé toujours complet ; un mois trop étroit pour son libellé (ex. 5 jours de
      // septembre) le cale sur le bord de la frise au lieu de le couper
      el.textContent = `${MONTHS[+mo - 1]} ${y}`;
      if (w < 12) el.classList.add(a === 0 ? "narrow-start" : b === days.length - 1 ? "narrow-end" : "narrow");
      lbl.appendChild(el);
    });
  };

  const refresh = () => {
    if (!data) return;
    const ns = inRange(data.sap.dates), nq = inRange(data.qbs.dates);
    const a = from.value || data.min, b = to.value || data.max;
    const bad = from.value && to.value && from.value > to.value;
    const nDays = bad ? 0 : Math.round((time(b) - time(a)) / DAY) + 1;
    $("cntSap").textContent = ns; $("totSap").textContent = data.sap.n;
    $("cntQbs").textContent = nq; $("totQbs").textContent = data.qbs.n;
    $("cntDays").textContent = nDays;
    $("sumRange").textContent = bad ? "" : `${fr(a)} → ${fr(b)}`;

    // frise : barres dans la période, sélection et poignées
    const i = dayIndex(a), j = dayIndex(b), n = days.length;
    $("rangeHist").querySelectorAll("span").forEach((bar, k) => bar.classList.toggle("in", !bad && k >= i && k <= j));
    const sel = $("timelineSel");
    sel.style.left = (Math.min(i, j) / n) * 100 + "%";
    sel.style.width = ((Math.abs(j - i) + 1) / n) * 100 + "%";
    sel.classList.toggle("bad", !!bad);
    hFrom.style.left = (i / n) * 100 + "%";
    hTo.style.left = ((j + 1) / n) * 100 + "%";
    [[hFrom, a], [hTo, b]].forEach(([h, v]) => {
      h.setAttribute("aria-valuetext", fr(v));
      h.dataset.label = fr(v);
    });

    const empty = ns + nq === 0;
    count.hidden = !(bad || empty);
    count.textContent = bad ? "La date de début est après la date de fin."
      : "Aucune ligne dans cette période : choisis une autre période.";
    summary.classList.toggle("warn", !!(bad || empty));
    summary.innerHTML = bad || empty ? count.textContent
      : `Sera traité : <strong>${ns} lignes SAP</strong> + <strong>${nq} lignes QBs</strong>, du ${fr(a)} au ${fr(b)}.`;
    $("periodAll").hidden = from.value === data.min && to.value === data.max;
  };

  const setOne = (hidden, txt, v) => {
    hidden.value = v;
    txt.value = fr(v);
    txt.classList.remove("invalid");
  };
  const setRange = (a, b) => {
    setOne(from, fromTxt, a);
    setOne(to, toTxt, b);
    err.hidden = true;
    refresh();
  };

  // ---- poignées : glisser à la souris / au doigt, flèches au clavier
  const dragTo = (handle, clientX) => {
    const r = range.getBoundingClientRect();
    const k = Math.min(days.length - 1, Math.max(0, Math.floor(((clientX - r.left) / r.width) * days.length)));
    if (handle === hFrom) setRange(days[Math.min(k, dayIndex(to.value || data.max))], to.value);
    else setRange(from.value, days[Math.max(k, dayIndex(from.value || data.min))]);
  };
  [hFrom, hTo].forEach((h) => {
    h.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      h.setPointerCapture(e.pointerId);
      h.classList.add("dragging");
    });
    h.addEventListener("pointermove", (e) => { if (h.hasPointerCapture(e.pointerId)) dragTo(h, e.clientX); });
    h.addEventListener("pointerup", () => h.classList.remove("dragging"));
    h.addEventListener("keydown", (e) => {
      const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -7, PageUp: 7 }[e.key];
      const isFrom = h === hFrom;
      let k = dayIndex(isFrom ? from.value || data.min : to.value || data.max);
      if (step) k += step;
      else if (e.key === "Home") k = 0;
      else if (e.key === "End") k = days.length - 1;
      else return;
      e.preventDefault();
      k = Math.min(days.length - 1, Math.max(0, k));
      if (isFrom) setRange(days[Math.min(k, dayIndex(to.value || data.max))], to.value);
      else setRange(from.value, days[Math.max(k, dayIndex(from.value || data.min))]);
    });
  });
  // clic dans la frise : la poignée la plus proche vient à cet endroit
  range.addEventListener("pointerdown", (e) => {
    if (!data || e.target.classList.contains("range-handle")) return;
    const r = range.getBoundingClientRect();
    const x = e.clientX - r.left;
    const near = Math.abs(x - hFrom.offsetLeft) <= Math.abs(x - hTo.offsetLeft) ? hFrom : hTo;
    dragTo(near, e.clientX);
  });

  // ---- saisie au clavier : appliquée dès que la date est complète et valide,
  // signalée en rouge si elle est invalide quand on quitte le champ
  const checkTxt = (txt, hidden, final) => {
    const v = parseFr(txt.value);
    if (v === null) {
      if (final) {
        txt.classList.add("invalid");
        err.textContent = `Date invalide « ${txt.value} » : écris-la au format jj/mm/aaaa, par exemple 01/11/2025.`;
        err.hidden = false;
      }
      return false;
    }
    hidden.value = v;
    txt.classList.remove("invalid");
    if (final && v) txt.value = fr(v);
    if (!fromTxt.classList.contains("invalid") && !toTxt.classList.contains("invalid")) err.hidden = true;
    refresh();
    return true;
  };
  [[fromTxt, from], [toTxt, to]].forEach(([txt, hidden]) => {
    txt.addEventListener("input", () => {
      txt.value = txt.value.replace(/[^\d\/.\- ]/g, "");
      checkTxt(txt, hidden, false);
    });
    txt.addEventListener("blur", () => checkTxt(txt, hidden, true));
  });

  // ---- bouton calendrier : sélecteur natif caché, la date choisie revient en jj/mm/aaaa
  document.querySelectorAll(".cal-btn").forEach((btn) => {
    const picker = $(btn.dataset.picker);
    const isFrom = picker.id === "pickFrom";
    btn.addEventListener("click", () => {
      if (!data) return;
      picker.min = data.min;
      picker.max = data.max;
      picker.value = (isFrom ? from.value : to.value) || (isFrom ? data.min : data.max);
      try { picker.showPicker(); } catch (e) { picker.focus(); picker.click(); }
    });
    picker.addEventListener("change", () => {
      if (!picker.value) return;
      if (isFrom) setRange(picker.value, to.value); else setRange(from.value, picker.value);
    });
  });

  // ---- pas d'envoi avec une date invalide ou une période à l'envers
  uploadForm.addEventListener("submit", (e) => {
    if (box.hidden || !data) return;
    const ok = checkTxt(fromTxt, from, true) & checkTxt(toTxt, to, true);
    const reversed = from.value && to.value && from.value > to.value;
    if (!ok || reversed) {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (reversed && ok) { err.textContent = "La date de début est après la date de fin."; err.hidden = false; }
      (fromTxt.classList.contains("invalid") ? fromTxt : toTxt).focus();
    }
  }, true);

  // ---- infos de chaque fichier dans sa carte : lignes, dates, format détecté
  const showMeta = (key, f) => {
    const meta = $("meta-" + key);
    if (!f) { meta.hidden = true; return; }
    const sorted = [...f.dates].sort();
    meta.querySelector('[data-k="rows"]').textContent = f.n;
    meta.querySelector('[data-k="range"]').textContent = sorted.length
      ? `du ${fr(sorted[0])} au ${fr(sorted[sorted.length - 1])}` : "aucune date";
    const fmt = meta.querySelector('[data-k="format"]');
    fmt.textContent = "Dates : " + f.desc;
    fmt.classList.toggle("warn", f.problems > 0);
    meta.hidden = false;
  };

  const inspect = async () => {
    if (!sapIn.files[0] || !qbsIn.files[0]) {
      box.hidden = true; data = null;
      showMeta("sap"); showMeta("qbs");
      summary.classList.remove("warn");
      summary.textContent = "Dépose les deux fichiers pour choisir la période.";
      return;
    }
    const mine = ++seq;
    box.hidden = false;
    body.hidden = true;
    info.textContent = "Lecture des dates des deux fichiers…";
    summary.textContent = "Lecture des dates…";
    const fd = new FormData();
    fd.append("sap", sapIn.files[0]);
    fd.append("qbs", qbsIn.files[0]);
    try {
      const r = await fetch(uploadForm.dataset.inspectUrl, { method: "POST", body: fd });
      const d = await r.json();
      if (mine !== seq) return;            // un autre fichier a été choisi entre-temps
      if (!r.ok) throw new Error(d.error || r.statusText);
      data = d;
      const noDate = d.sap.no_date + d.qbs.no_date;
      info.textContent = `Dates trouvées du ${d.min_fr} au ${d.max_fr}` +
        (noDate ? ` · ${plural(noDate, "ligne sans date exclue", "lignes sans date exclues")} si tu limites la période` : "") + ".";
      showMeta("sap", d.sap);
      showMeta("qbs", d.qbs);
      body.hidden = false;
      drawRange();
      setRange(d.min, d.max);
    } catch (e) {
      if (mine !== seq) return;
      data = null;
      info.textContent = `Impossible de lire les dates : ${e.message}`;
      summary.textContent = "";
    }
  };

  [sapIn, qbsIn].forEach((el) => el.addEventListener("change", inspect));
  $("periodAll").addEventListener("click", () => data && setRange(data.min, data.max));
  inspect();   // fichiers déjà choisis (retour arrière du navigateur)
}

// Progression du mapping
const progressBox = document.querySelector("[data-progress-url]");
if (progressBox) {
  const bar = document.getElementById("bar");
  const txt = document.getElementById("txt");
  const poll = async () => {
    try {
      const r = await fetch(progressBox.dataset.progressUrl);
      const d = await r.json();
      if (d.state === "ready" || d.state === "error") {
        location.reload();
        return;
      }
      if (d.total) {
        bar.style.width = (100 * d.done) / d.total + "%";
        txt.textContent = `${d.phase || ""} : ${d.done} / ${d.total} — ${d.current || ""}`;
      }
    } catch (e) {
      /* on réessaie au prochain tour */
    }
    setTimeout(poll, 1500);
  };
  poll();
}

// Validation : filtres, recherche, surlignage des lignes sans correspondance
const showSaved = document.getElementById("showSaved");
if (showSaved) {
  const onlyNone = document.getElementById("onlyNone");
  const search = document.getElementById("search");
  const noResult = document.getElementById("noResult");

  const apply = () => {
    const q = search.value.trim().toLowerCase();
    let visible = 0;
    document.querySelectorAll("tr[data-saved]").forEach((tr) => {
      const inMap = !!tr.closest("#map");
      const hide =
        (tr.dataset.saved === "1" && !showSaved.checked) ||
        (onlyNone.checked && inMap && !tr.classList.contains("none")) ||
        (q && !tr.cells[0].textContent.toLowerCase().includes(q));
      tr.hidden = hide;
      if (inMap && !hide) visible++;
    });
    noResult.hidden = visible > 0;
  };

  document.querySelectorAll("#map select").forEach((sel) => {
    sel.addEventListener("change", () => sel.closest("tr").classList.toggle("none", !sel.value));
  });
  // Badge « Modifié » dès qu'un choix diffère de la proposition de l'agent IA
  // ou du choix déjà enregistré (le score est calculé à l'étape « Score IA »)
  document.querySelectorAll("select[data-ai], select[data-orig]").forEach((sel) => {
    const isAi = "ai" in sel.dataset;
    const ref = isAi ? sel.dataset.ai : sel.dataset.orig;
    const badge = sel.closest("tr").querySelector(isAi ? ".ai-badge" : ".saved-badge");
    const label = badge ? (isAi ? "Agent IA" : badge.dataset.label || "enregistré") : "";
    sel.addEventListener("change", () => {
      if (!badge) return;
      const same = sel.value === ref;
      badge.textContent = same ? label : "Modifié";
      badge.classList.toggle("edited", !same);
      if (!isAi) badge.classList.toggle("saved", same);
    });
  });

  [showSaved, onlyNone].forEach((el) => el.addEventListener("change", apply));
  search.addEventListener("input", apply);
  apply();
}
