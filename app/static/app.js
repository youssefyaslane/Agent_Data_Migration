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
    zone.classList.remove("valid", "bad");   // nouveau fichier : contrôle à refaire
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
  const summary = $("submitSummary"), submitBtn = $("submitBtn");
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
    if (!data) {                 // fichiers pas encore validés (format + colonnes)
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
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

  // ---- contrôle de chaque fichier dès son dépôt : format .xlsx + colonnes obligatoires.
  // Le bouton d'envoi reste désactivé tant que les deux fichiers ne sont pas valides.
  const LABELS = { sap: "SAP", qbs: "QuickBooks" };
  const setZone = (key, state, msg) => {
    const zone = $(key).closest(".dropzone");
    ["valid", "bad", "checking"].forEach((c) => zone.classList.remove(c));
    if (state) zone.classList.add(state);
    const e = $("err-" + key);
    e.textContent = msg ? `Fichier refusé : ${msg}` : "";
    e.hidden = state !== "bad";
  };
  const waiting = (msg, warn) => {
    data = null;
    box.hidden = true;
    submitBtn.disabled = true;
    summary.classList.toggle("warn", !!warn);
    summary.textContent = msg;
  };

  const inspect = async () => {
    const files = { sap: sapIn.files[0], qbs: qbsIn.files[0] };
    const mine = ++seq;
    Object.keys(files).forEach((k) => { showMeta(k); setZone(k, files[k] ? "checking" : null); });
    if (!files.sap && !files.qbs) return waiting("Dépose les deux fichiers pour choisir la période.");
    waiting("Vérification du format et des colonnes…");
    const fd = new FormData();
    Object.entries(files).forEach(([k, f]) => f && fd.append(k, f));
    try {
      const r = await fetch(uploadForm.dataset.inspectUrl, { method: "POST", body: fd });
      const d = await r.json();
      if (mine !== seq) return;            // un autre fichier a été choisi entre-temps
      if (!r.ok) throw new Error(d.error || r.statusText);
      const bad = [];
      Object.keys(files).forEach((k) => {
        const f = d[k];
        if (!f) return;
        if (f.ok) { setZone(k, "valid"); showMeta(k, f); } else { setZone(k, "bad", f.error); bad.push(LABELS[k]); }
      });
      if (bad.length) return waiting(`Fichier ${bad.join(" et ")} refusé : corrige-le puis dépose-le à nouveau.`, true);
      if (!d.ready) {
        return d.error ? waiting(`Impossible de choisir la période : ${d.error}`, true)
          : waiting(`Fichier ${LABELS[files.sap ? "sap" : "qbs"]} valide. Dépose le fichier ${LABELS[files.sap ? "qbs" : "sap"]}.`);
      }
      data = d;
      const noDate = d.sap.no_date + d.qbs.no_date;
      info.textContent = `Dates trouvées du ${d.min_fr} au ${d.max_fr}` +
        (noDate ? ` · ${plural(noDate, "ligne sans date exclue", "lignes sans date exclues")} si tu limites la période` : "") + ".";
      box.hidden = false;
      body.hidden = false;
      submitBtn.disabled = false;
      drawRange();
      setRange(d.min, d.max);
    } catch (e) {
      if (mine !== seq) return;
      Object.keys(files).forEach((k) => files[k] && setZone(k, null));
      waiting(`Impossible de vérifier les fichiers : ${e.message}`, true);
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

// Listes avec recherche : le <select> reste (caché, envoyé avec le formulaire) ;
// un bouton affiche le choix et ouvre un panneau avec un champ de recherche.
// Un seul panneau partagé par toutes les listes de la page.
function setupCombos(selects) {
  if (!selects.length) return;
  const norm = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const pop = document.createElement("div");
  pop.className = "combo-pop";
  pop.hidden = true;
  pop.innerHTML =
    '<input type="search" class="combo-search" placeholder="Rechercher un fournisseur SAP…" ' +
    'aria-label="Rechercher un fournisseur SAP" autocomplete="off">' +
    '<ul class="combo-list" role="listbox"></ul>' +
    '<p class="combo-none" hidden>Aucun fournisseur trouvé</p>';
  document.body.appendChild(pop);
  const input = pop.querySelector("input");
  const list = pop.querySelector("ul");
  const none = pop.querySelector("p");
  let cur = null;      // { sel, btn } de la liste ouverte
  let items = [];      // <li> affichés
  let active = -1;     // <li> surligné au clavier

  const label = (sel) => (sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].text : "");

  const place = () => {
    const r = cur.btn.getBoundingClientRect();
    const below = innerHeight - r.bottom;
    const up = below < 220 && r.top > below;
    const width = Math.max(r.width, 280);
    pop.style.width = width + "px";
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - width - 8)) + "px";
    pop.style.maxHeight = Math.min(340, (up ? r.top : below) - 12) + "px";
    pop.style.top = up ? "" : r.bottom + 4 + "px";
    pop.style.bottom = up ? innerHeight - r.top + 4 + "px" : "";
  };

  const setActive = (i) => {
    if (items[active]) items[active].classList.remove("active");
    active = Math.max(0, Math.min(i, items.length - 1));
    if (items[active]) {
      items[active].classList.add("active");
      items[active].scrollIntoView({ block: "nearest" });
    }
  };

  const render = () => {
    const q = norm(input.value.trim());
    list.textContent = "";
    items = [];
    [...cur.sel.options].forEach((opt, i) => {
      const text = opt.text;
      const n = norm(text);
      const at = q ? n.indexOf(q) : -1;
      if (q && at < 0) return;
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.i = i;
      if (!opt.value) li.classList.add("placeholder");
      if (i === cur.sel.selectedIndex) { li.classList.add("sel"); li.setAttribute("aria-selected", "true"); }
      if (at >= 0 && n.length === text.length) {   // texte trouvé mis en gras
        const mark = document.createElement("mark");
        mark.textContent = text.slice(at, at + q.length);
        li.append(text.slice(0, at), mark, text.slice(at + q.length));
      } else {
        li.textContent = text;
      }
      list.appendChild(li);
      items.push(li);
    });
    none.hidden = items.length > 0;
    active = -1;
    const selIdx = items.findIndex((li) => li.classList.contains("sel"));
    setActive(q ? 0 : Math.max(selIdx, 0));
  };

  const close = (focusBtn) => {
    if (!cur) return;
    cur.btn.setAttribute("aria-expanded", "false");
    if (focusBtn) cur.btn.focus();
    pop.hidden = true;
    cur = null;
  };

  const open = (sel, btn, text = "") => {
    if (cur) close(false);
    cur = { sel, btn };
    btn.setAttribute("aria-expanded", "true");
    input.value = text;
    pop.hidden = false;
    render();
    place();
    input.focus();
  };

  const choose = (li) => {
    if (!li || !cur) return;
    const { sel, btn } = cur;
    if (sel.selectedIndex !== +li.dataset.i) {
      sel.selectedIndex = +li.dataset.i;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    btn.firstChild.textContent = label(sel);
    btn.title = label(sel);
    close(true);
  };

  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active - 1); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[active]); }   // pas d'envoi du formulaire
    else if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "Tab") close(false);
  });
  list.addEventListener("mousedown", (e) => e.preventDefault());   // garde le focus dans la recherche
  list.addEventListener("click", (e) => choose(e.target.closest("li")));
  document.addEventListener("pointerdown", (e) => {
    if (cur && !pop.contains(e.target) && !cur.btn.contains(e.target)) close(false);
  });
  window.addEventListener("resize", () => cur && place());
  window.addEventListener("scroll", (e) => cur && !pop.contains(e.target) && place(), true);

  selects.forEach((sel) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "combo-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-label", sel.getAttribute("aria-label") || "");
    btn.title = label(sel);
    const span = document.createElement("span");
    span.textContent = label(sel);
    btn.appendChild(span);
    sel.classList.add("combo-native");
    sel.after(btn);
    btn.addEventListener("click", () => (cur && cur.btn === btn ? close(true) : open(sel, btn)));
    btn.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(sel, btn); }
      else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") {
        e.preventDefault();
        open(sel, btn, e.key);   // taper une lettre ouvre la liste avec la recherche
      }
    });
  });
}

// Validation : filtres, recherche, surlignage des lignes sans correspondance
const showSaved = document.getElementById("showSaved");
if (showSaved) {
  const onlyNone = document.getElementById("onlyNone");
  const noResult = document.getElementById("noResult");

  const apply = () => {
    let visible = 0;
    document.querySelectorAll("tr[data-saved]").forEach((tr) => {
      const inMap = !!tr.closest("#map");
      const hide =
        (tr.dataset.saved === "1" && !showSaved.checked) ||
        (onlyNone.checked && inMap && !tr.classList.contains("none"));
      tr.hidden = hide;
      if (inMap && !hide) visible++;
    });
    noResult.hidden = visible > 0;
  };

  document.querySelectorAll("#map select").forEach((sel) => {
    sel.addEventListener("change", () => sel.closest("tr").classList.toggle("none", !sel.value));
  });
  setupCombos(document.querySelectorAll("#map select"));
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
  apply();
}
