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
  [showSaved, onlyNone].forEach((el) => el.addEventListener("change", apply));
  search.addEventListener("input", apply);
  apply();
}
