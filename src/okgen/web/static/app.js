"use strict";

// ---- state ----
const state = {
  rootDir: null,
  file: null,          // current file path
  view: null,          // parsed view
  edits: {},           // key -> value
  clipboard: [],       // array of paths copied for paste
  treeToken: 0,        // increments per Open; guards against stale renders
  treeAbort: null,     // AbortController for the in-flight root load
  selection: new Set(),// multi-selected file paths (for bulk copy / future bulk edit)
  selAnchor: null,     // last plainly-clicked file, for Shift-range select
  busy: false,         // guards slow file ops (make-unique) from double-runs
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

// ---- API ----
async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const getTree = (dir, signal) =>
  api(`/api/tree?dir=${encodeURIComponent(dir)}`, signal ? { signal } : undefined);
const getParse = (p) => api(`/api/parse?path=${encodeURIComponent(p)}`);
const postJSON = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---- status ----
function setStatus(msg, kind) {
  const s = $("#status");
  s.textContent = msg || "";
  s.className = "status" + (kind ? " " + kind : "");
}

// ---- dirty state ----
function isDirty() { return Object.keys(state.edits).length > 0; }
function confirmDiscardIfDirty() {
  return !isDirty() || confirm("You have unsaved changes. Discard them?");
}
function updateDirtyIndicator() {
  const dirty = isDirty();
  if (state.view) {
    const v = state.view;
    const tag = v.chain_info ? v.chain_info.name : v.chain;
    $("#fileTitle").textContent = `${dirty ? "* " : ""}${v.name}  ·  ${v.layout}  ·  ${tag}`;
    const selName = document.querySelector(".node.selected .file-name");
    if (selName) selName.textContent = (dirty ? "* " : "") + v.name;
  }
}

// ---- folder picker (native OS dialog) ----
async function browseFolder() {
  try {
    const res = await postJSON("/api/browse-folder", {});
    if (res.path) { const fp = $("#folderPath"); fp.value = res.path; fp.title = res.path; openFolder(res.path); }
    else setStatus("No folder selected");
  } catch (e) {
    setStatus("Native dialog unavailable — paste a path instead", "err");
  }
}

// ---- tree (lazy, one level at a time) ----
function loadingLi(text) {
  const li = el("li", "tree-loading");
  li.appendChild(el("span", "spinner"));
  li.appendChild(document.createTextNode(" " + (text || "Loading…")));
  return li;
}
function emptyLi() { return el("li", "tree-empty", "(no .OK files)"); }

async function openFolder(dir) {
  if (!dir) return;
  if (!confirmDiscardIfDirty()) return;

  const token = ++state.treeToken;
  if (state.treeAbort) state.treeAbort.abort();   // cancel any in-flight load
  state.treeAbort = new AbortController();
  setSelection([]);                                // reset multi-select for a new folder

  const host = $("#tree");
  host.innerHTML = "";
  host.appendChild(loadingLi("Loading folder…"));
  setStatus("Loading folder…", "dirty");
  $("#openBtn").disabled = true;

  try {
    const tree = await getTree(dir, state.treeAbort.signal);
    if (token !== state.treeToken) return;        // a newer Open superseded this
    state.rootDir = dir;
    localStorage.setItem("okgen.dir", dir);
    renderTree(tree);
    setStatus("Folder loaded", "ok");
  } catch (e) {
    if (e.name === "AbortError" || token !== state.treeToken) return;  // ignore stale
    host.innerHTML = "";
    setStatus("Open failed: " + e.message, "err");
  } finally {
    if (token === state.treeToken) $("#openBtn").disabled = false;
  }
}

function renderTree(root) {
  const host = $("#tree");
  host.innerHTML = "";
  const ul = el("ul");
  ul.appendChild(renderFolderNode(root, true));   // root: open, children preloaded
  host.appendChild(ul);
  updateSelectionUI();
}

function renderNode(node) {
  return node.type === "folder" ? renderFolderNode(node, false) : renderFileNode(node);
}

function renderFolderNode(node, openPreloaded) {
  const li = el("li", "folder");
  const row = el("div", "node");
  row.dataset.path = node.path;
  const nameEl = el("span", "file-name", node.name || node.path);
  nameEl.title = node.name || node.path;   // full name on hover
  row.appendChild(nameEl);
  const childUl = el("ul");
  li.appendChild(row);
  li.appendChild(childUl);
  row.addEventListener("click", (e) => { e.stopPropagation(); toggleFolder(li, node, childUl); });
  row.addEventListener("contextmenu", (e) => showFolderCtxMenu(e, node));
  if (openPreloaded) {
    li.classList.add("open");
    li.dataset.loaded = "1";
    const kids = node.children || [];
    if (!kids.length) childUl.appendChild(emptyLi());
    else kids.forEach((c) => childUl.appendChild(renderNode(c)));
  }
  return li;
}

function renderFileNode(node) {
  const li = el("li", "file");
  const row = el("div", "node");
  row.dataset.path = node.path;
  const info = node.chain_info || {};
  const badge = el("span", "chain-badge", info.short || node.chain || "?");
  badge.style.background = info.color || "#666";
  badge.title = info.name || ("chain " + (node.chain || "?"));
  const nameEl = el("span", "file-name", node.name);
  nameEl.title = node.name;            // full name on hover (names truncate)
  row.appendChild(badge);
  row.appendChild(nameEl);
  if (node.duplicate) {
    const warn = el("span", "dup-warn", "⚠");
    warn.title = `duplicate ${node.key_field || "key"}: ${node.key_value}`;
    row.appendChild(warn);
  }
  row.addEventListener("click", (e) => onFileClick(e, node, row));
  row.addEventListener("contextmenu", (e) => showCtxMenu(e, node, row));
  li.appendChild(row);
  return li;
}

// ---- multi-select ----
function onFileClick(e, node, row) {
  if (e.metaKey || e.ctrlKey) {            // toggle this file in the selection
    e.preventDefault();
    if (state.selection.has(node.path)) state.selection.delete(node.path);
    else state.selection.add(node.path);
    state.selAnchor = node.path;
    updateSelectionUI();
  } else if (e.shiftKey) {                  // range from anchor to here
    e.preventDefault();
    rangeSelect(node.path);
  } else {                                  // plain click: open + single select
    selectFile(node.path, row);            // (has the unsaved-changes guard)
    setSelection([node.path]);
    state.selAnchor = node.path;
  }
}

function setSelection(paths) {
  state.selection = new Set(paths);
  updateSelectionUI();
}

function rangeSelect(path) {
  const rows = [...document.querySelectorAll(".file > .node")];
  const paths = rows.map((r) => r.dataset.path);
  const a = paths.indexOf(state.selAnchor);
  const b = paths.indexOf(path);
  if (a === -1 || b === -1) { state.selection.add(path); updateSelectionUI(); return; }
  const [lo, hi] = a < b ? [a, b] : [b, a];
  for (let i = lo; i <= hi; i++) state.selection.add(paths[i]);
  updateSelectionUI();
}

function updateSelectionUI() {
  const sel = state.selection;
  document.querySelectorAll(".file > .node").forEach((r) => {
    r.classList.toggle("multi-selected", sel.has(r.dataset.path));
  });
  const n = sel.size;
  const c = $("#selCount");
  if (c) c.textContent = n > 1 ? ` · ${n} selected` : "";
  const btn = $("#bulkBtn");
  if (btn) { btn.classList.toggle("hidden", n < 2); btn.textContent = `Bulk Edit (${n})`; }
  // Bulk Edit only makes sense for a multi-selection — close it otherwise.
  if (isBulkOpen() && n < 2) exitBulkMode();
}

function isBulkOpen() {
  return !$("#bulkPanel").classList.contains("hidden");
}

// ---- bulk edit (B1: Header field, one layout, set value) ----
async function enterBulkMode() {
  if (state.selection.size < 2) return;
  if (!confirmDiscardIfDirty()) return;
  state.file = null; state.view = null; state.edits = {};
  $("#editorTabs").classList.add("hidden");
  $("#editor").classList.add("hidden");
  $("#rawView").classList.add("hidden");
  $("#editorEmpty").style.display = "none";
  $("#fileTitle").textContent = "";
  updateSaveButtons();

  const panel = $("#bulkPanel");
  panel.classList.remove("hidden");
  panel.innerHTML = "<div class='bulk-loading'><span class='spinner'></span> Loading selection…</div>";
  try {
    const scope = await postJSON("/api/bulk/scope", { paths: [...state.selection] });
    renderBulkPanel(scope);
  } catch (e) {
    panel.innerHTML = "";
    setStatus("Bulk scope failed: " + e.message, "err");
  }
}

function exitBulkMode() {
  const panel = $("#bulkPanel");
  if (panel.classList.contains("hidden")) return;
  panel.classList.add("hidden");
  panel.innerHTML = "";
  $("#editor").classList.remove("hidden");
  if (state.view) {
    $("#editorTabs").classList.remove("hidden");
    $("#editorEmpty").style.display = "none";
  } else {
    $("#editorTabs").classList.add("hidden");
    $("#rawView").classList.add("hidden");
    $("#editorEmpty").style.display = "";
  }
}

function renderBulkPanel(scope) {
  const panel = $("#bulkPanel");
  panel.innerHTML = "";
  const layoutNames = Object.keys(scope.layouts);

  const head = el("div", "bulk-head");
  head.appendChild(el("h3", null, `Bulk Edit — ${scope.files.length} file(s) selected`));
  const close = el("button", "btn", "✕ Close");
  close.addEventListener("click", exitBulkMode);
  head.appendChild(close);
  panel.appendChild(head);

  if (!layoutNames.length) {
    panel.appendChild(el("div", "bulk-note", "No recognizable OK layouts in the selection."));
    return;
  }

  let selectedLayout = layoutNames[0];

  // ---- Layout chooser ----
  const scopeBox = el("div", "bulk-scope");
  scopeBox.appendChild(el("span", "bulk-label", "Layout:"));
  layoutNames.forEach((name) => {
    const lbl = el("label", "bulk-radio");
    const rb = el("input"); rb.type = "radio"; rb.name = "bulkLayout";
    if (name === selectedLayout) rb.checked = true;
    rb.addEventListener("change", () => { selectedLayout = name; rebuildSections(); });
    lbl.appendChild(rb);
    lbl.appendChild(document.createTextNode(` ${name} (${scope.layouts[name]})`));
    scopeBox.appendChild(lbl);
  });
  panel.appendChild(scopeBox);

  // ---- Section + operation row ----
  const row1 = el("div", "bulk-edit-row");
  const sectionSel = el("select", "bulk-field");
  const opSel = el("select", "bulk-field");
  row1.appendChild(el("span", "bulk-label", "Section:"));
  row1.appendChild(sectionSel);
  row1.appendChild(el("span", "bulk-label", "Operation:"));
  row1.appendChild(opSel);
  panel.appendChild(row1);

  // ---- Dynamic inputs (field/value or count) ----
  const row2 = el("div", "bulk-edit-row");
  panel.appendChild(row2);

  const actions = el("div", "bulk-actions");
  const previewBtn = el("button", "btn", "Preview");
  const applyBtn = el("button", "btn btn-primary", "Apply"); applyBtn.disabled = true;
  actions.appendChild(previewBtn); actions.appendChild(applyBtn);
  panel.appendChild(actions);

  const previewBox = el("div", "bulk-preview");
  const resultsBox = el("div", "bulk-results");
  panel.appendChild(previewBox); panel.appendChild(resultsBox);

  // Sections for the current layout: Header + detail sections.
  function sectionsFor() {
    const det = (scope.detail_sections[selectedLayout] || []).map((d) => ({ ...d, isHeader: false }));
    return [{ name: "Header", isHeader: true, fields: scope.header_fields[selectedLayout] || [] }, ...det];
  }
  const curSection = () => sectionsFor().find((s) => s.name === sectionSel.value);
  const reset = () => { applyBtn.disabled = true; previewBox.innerHTML = ""; resultsBox.innerHTML = ""; };

  function opsForSection(sec) {
    if (sec.isHeader) return [{ v: "set", t: "Set value" }];
    return [
      { v: "set", t: "Set value (all rows)" },
      { v: "random", t: "Set random value (each row)" },
      { v: "unique", t: "Set unique value (each row)" },
      { v: "add", t: "Add rows" },
      { v: "keep", t: "Keep first N rows" },
    ];
  }

  function rebuildInputs() {
    row2.innerHTML = "";
    const sec = curSection(); if (!sec) return;
    const op = opSel.value;
    if (op === "set" || op === "random" || op === "unique") {
      const fieldSel = el("select", "bulk-field");
      sec.fields.forEach((f) => fieldSel.appendChild(new Option(`${f.name} (${f.size != null ? f.size : "?"})`, f.name)));
      row2.appendChild(el("span", "bulk-label", "Field:"));
      row2.appendChild(fieldSel);

      if (op === "set") {
        const valueHolder = el("span", "bulk-value-holder");
        const buildValue = () => {
          valueHolder.innerHTML = "";
          const f = sec.fields.find((x) => x.name === fieldSel.value); if (!f) return;
          let ctrl;
          if (f.options) {
            ctrl = el("select", "bulk-value");
            Object.keys(f.options).forEach((code) => ctrl.appendChild(new Option(`${f.options[code]} (${code})`, code)));
          } else {
            ctrl = el("input", "bulk-value"); ctrl.type = "text";
            if (f.size != null) ctrl.maxLength = f.size;
          }
          valueHolder.appendChild(ctrl);
        };
        fieldSel.addEventListener("change", () => { buildValue(); reset(); });
        row2.appendChild(el("span", "bulk-label", "Set value:"));
        row2.appendChild(valueHolder);
        buildValue();
      } else if (op === "unique") {
        const startInp = el("input", "bulk-value"); startInp.type = "number"; startInp.min = "0"; startInp.value = "1"; startInp.style.width = "90px";
        row2.appendChild(el("span", "bulk-label", "Start at:"));
        row2.appendChild(startInp);
        row2.appendChild(el("span", "bulk-section", "· each row gets the next number (per file)"));
        fieldSel.addEventListener("change", reset);
      } else {  // random
        const rmin = el("input", "bulk-value bulk-rmin"); rmin.type = "number"; rmin.min = "0"; rmin.placeholder = "min"; rmin.style.width = "90px";
        const rmax = el("input", "bulk-value bulk-rmax"); rmax.type = "number"; rmax.min = "0"; rmax.placeholder = "max"; rmax.style.width = "90px";
        row2.appendChild(el("span", "bulk-label", "Range:"));
        row2.appendChild(rmin);
        row2.appendChild(el("span", "bulk-section", "to"));
        row2.appendChild(rmax);
        row2.appendChild(el("span", "bulk-section", "· optional — blank = full field width"));
        fieldSel.addEventListener("change", reset);
      }
    } else {
      const cnt = el("input", "bulk-value"); cnt.type = "number"; cnt.min = "0"; cnt.value = op === "add" ? "1" : "5";
      cnt.style.width = "80px";
      row2.appendChild(el("span", "bulk-label", op === "add" ? "Add how many rows:" : "Keep first N rows:"));
      row2.appendChild(cnt);
      if (op === "add" && sec.max_records != null) {
        row2.appendChild(el("span", "bulk-section", `(section max ${sec.max_records})`));
      }
      if (sec.count_field) row2.appendChild(el("span", "bulk-section", `· header ${sec.count_field} kept in sync`));
    }
    reset();
  }

  function rebuildOps() {
    const sec = curSection();
    opSel.innerHTML = "";
    opsForSection(sec).forEach((o) => opSel.appendChild(new Option(o.t, o.v)));
    rebuildInputs();
  }
  function rebuildSections() {
    sectionSel.innerHTML = "";
    sectionsFor().forEach((s) => sectionSel.appendChild(new Option(s.name, s.name)));
    rebuildOps();
  }
  sectionSel.addEventListener("change", rebuildOps);
  opSel.addEventListener("change", rebuildInputs);

  // Build the op spec from the current inputs.
  function buildOp() {
    const op = opSel.value;
    const fieldSel = row2.querySelector("select.bulk-field");
    if (op === "set") {
      return { type: "set", field: fieldSel.value, value: row2.querySelector(".bulk-value").value };
    }
    if (op === "random") {
      const o = { type: "random", field: fieldSel.value };
      const mn = row2.querySelector(".bulk-rmin").value, mx = row2.querySelector(".bulk-rmax").value;
      if (mn !== "") o.min = Number(mn);
      if (mx !== "") o.max = Number(mx);
      return o;
    }
    if (op === "unique") {
      return { type: "unique", field: fieldSel.value, start: Number(row2.querySelector(".bulk-value").value || 0) };
    }
    return { type: op, count: Number(row2.querySelector(".bulk-value").value || 0) };
  }
  function describe() {
    const sec = curSection().name, op = buildOp();
    if (op.type === "set") return `${sec}: set ${op.field} = "${op.value}"`;
    if (op.type === "random") {
      const rng = (op.min != null || op.max != null) ? ` in [${op.min != null ? op.min : 0}..${op.max != null ? op.max : "max"}]` : "";
      return `${sec}: set ${op.field} to a random value${rng} on every row`;
    }
    if (op.type === "unique") return `${sec}: set ${op.field} to unique values from ${op.start}`;
    if (op.type === "add") return `${sec}: add ${op.count} row(s)`;
    return `${sec}: keep first ${op.count} row(s)`;
  }

  async function run(url, box, applied) {
    if (!beginBusy(applied ? "Applying…" : "Previewing…")) { setStatus("Please wait — an operation is already running…", "dirty"); return null; }
    previewBtn.disabled = true; applyBtn.disabled = true;
    box.innerHTML = `<div class='bulk-loading'><span class='spinner'></span> ${applied ? "Applying" : "Previewing"}…</div>`;
    try {
      return await postJSON(url, {
        paths: scope.files.map((x) => x.path), layout: selectedLayout, section: curSection().name, op: buildOp(),
      });
    } catch (e) {
      box.innerHTML = ""; setStatus((applied ? "Apply" : "Preview") + " failed: " + e.message, "err");
      return null;
    } finally {
      state.busy = false; previewBtn.disabled = false;
    }
  }

  previewBtn.addEventListener("click", async () => {
    resultsBox.innerHTML = "";
    const res = await run("/api/bulk/op/preview", previewBox, false);
    if (!res) return;
    renderBulkTable(previewBox, res.results, false);
    applyBtn.disabled = !res.results.some((r) => r.status === "change");
  });

  applyBtn.addEventListener("click", async () => {
    if (!confirm(`Apply — ${describe()} — to the ${selectedLayout} files?\nA .bak backup is made for each changed file.`)) return;
    const res = await run("/api/bulk/op/apply", resultsBox, true);
    if (!res) return;
    renderBulkTable(resultsBox, res.results, true);
    new Set(res.results.filter((r) => r.status === "changed").map((r) => folderOf(r.path))).forEach((fp) => refreshFolder(fp));
    setStatus(`Bulk applied: ${res.results.filter((r) => r.status === "changed").length} changed`, "ok");
    applyBtn.disabled = true;
  });

  rebuildSections();
}

function renderBulkTable(host, results, applied) {
  host.innerHTML = "";
  const counts = {};
  results.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  const summary = Object.entries(counts).map(([k, v]) => `${v} ${k}`).join("  ·  ");
  host.appendChild(el("div", "bulk-summary", (applied ? "Results:  " : "Preview:  ") + summary));
  const table = el("table", "bulk-table");
  const thead = el("thead"); const htr = el("tr");
  ["File", "Change", "Status"].forEach((h) => htr.appendChild(el("th", null, h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = el("tbody");
  results.forEach((r) => {
    const tr = el("tr", "st-" + r.status);
    tr.appendChild(el("td", null, r.name));
    tr.appendChild(el("td", "mono", r.detail || ""));
    tr.appendChild(el("td", null, r.status + (r.error ? `: ${r.error}` : "")));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); host.appendChild(table);
}

// ---- Bulk Rename ----
async function enterRenameMode() {
  if (!state.selection.size) return;
  if (!confirmDiscardIfDirty()) return;
  state.file = null; state.view = null; state.edits = {};
  $("#editorTabs").classList.add("hidden");
  $("#editor").classList.add("hidden");
  $("#rawView").classList.add("hidden");
  $("#bulkPanel").classList.add("hidden"); $("#bulkPanel").innerHTML = "";
  $("#editorEmpty").style.display = "none";
  $("#fileTitle").textContent = "";
  updateSaveButtons();
  const panel = $("#renamePanel");
  panel.classList.remove("hidden");
  panel.innerHTML = "<div class='bulk-loading'><span class='spinner'></span> Loading…</div>";
  try {
    const scope = await postJSON("/api/rename/scope", { paths: [...state.selection] });
    renderRenamePanel(scope);
  } catch (e) {
    panel.innerHTML = ""; setStatus("Rename scope failed: " + e.message, "err");
  }
}

function exitRenameMode() {
  const panel = $("#renamePanel");
  if (panel.classList.contains("hidden")) return;
  panel.classList.add("hidden"); panel.innerHTML = "";
  $("#editor").classList.remove("hidden");
  if (state.view) { $("#editorTabs").classList.remove("hidden"); $("#editorEmpty").style.display = "none"; }
  else { $("#editorTabs").classList.add("hidden"); $("#rawView").classList.add("hidden"); $("#editorEmpty").style.display = ""; }
}

function jsBuildName(parts, sample, sep) {
  const inv = /[\\/:*?"<>|]/g;
  let out = ""; let glue = false;
  (parts || []).forEach((p) => {
    if (p.type === "glue") { glue = true; return; }
    let v = "";
    if (p.type === "text") v = String(p.value || "").replace(inv, "");
    else if (p.name === "seq") v = "0001";
    else if (p.name === "brand" || p.name === "format_label") v = String(sample[p.name] || "").replace(/ /g, "_").replace(inv, "");
    else v = String(sample[p.name] || "").replace(inv, "");
    if (v === "") return;
    out = out === "" ? v : out + (glue ? "" : sep) + v;
    glue = false;
  });
  return out + ".OK";
}

function renderRenamePanel(scope) {
  const panel = $("#renamePanel");
  panel.innerHTML = "";
  const head = el("div", "bulk-head");
  head.appendChild(el("h3", null, `Bulk Rename — ${scope.files.length} file(s)`));
  const close = el("button", "btn", "✕ Close");
  close.addEventListener("click", exitRenameMode);
  head.appendChild(close);
  panel.appendChild(head);
  if (!scope.files.length) { panel.appendChild(el("div", "bulk-note", "No files.")); return; }

  // Preset chooser — fills all the parts at once.
  if ((scope.presets || []).length) {
    const presetRow = el("div", "bulk-edit-row");
    presetRow.appendChild(el("span", "bulk-label", "Preset:"));
    const presetSel = el("select", "bulk-field");
    presetSel.appendChild(new Option("— choose a preset —", ""));
    scope.presets.forEach((p, i) => presetSel.appendChild(new Option(p.name, String(i))));
    presetSel.addEventListener("change", () => {
      if (presetSel.value === "") return;
      applyPreset(scope.presets[Number(presetSel.value)]);
    });
    presetRow.appendChild(presetSel);
    panel.appendChild(presetRow);
  }

  const partsBox = el("div", "rn-parts");
  panel.appendChild(partsBox);
  const addRow = el("div", "bulk-actions");
  const addBtn = el("button", "btn", "＋ Add part");
  addRow.appendChild(addBtn);
  panel.appendChild(addRow);

  const sepRow = el("div", "bulk-edit-row");
  sepRow.appendChild(el("span", "bulk-label", "Separator:"));
  const sepSel = el("select", "bulk-field");
  [["_", "_ underscore"], ["-", "- dash"], [".", ". dot"], ["", "(none)"], ["__custom__", "custom…"]]
    .forEach(([v, t]) => sepSel.appendChild(new Option(t, v)));
  const sepCustom = el("input", "bulk-value"); sepCustom.style.width = "60px"; sepCustom.placeholder = "sep"; sepCustom.classList.add("hidden");
  sepSel.addEventListener("change", () => { sepCustom.classList.toggle("hidden", sepSel.value !== "__custom__"); updateLive(); });
  sepCustom.addEventListener("input", updateLive);
  sepRow.appendChild(sepSel); sepRow.appendChild(sepCustom);
  panel.appendChild(sepRow);

  const live = el("div", "rn-live");
  panel.appendChild(live);

  const actions = el("div", "bulk-actions");
  const previewBtn = el("button", "btn", "Preview");
  const applyBtn = el("button", "btn btn-primary", "Apply"); applyBtn.disabled = true;
  actions.appendChild(previewBtn); actions.appendChild(applyBtn);
  panel.appendChild(actions);
  const previewBox = el("div", "bulk-preview");
  const resultsBox = el("div", "bulk-results");
  panel.appendChild(previewBox); panel.appendChild(resultsBox);

  const sep = () => (sepSel.value === "__custom__" ? sepCustom.value : sepSel.value);

  function tokenSelect() {
    const sel = el("select", "rn-token");
    const grp = (label, names) => {
      if (!names || !names.length) return;
      const g = document.createElement("optgroup"); g.label = label;
      names.forEach((n) => g.appendChild(new Option(n, n)));
      sel.appendChild(g);
    };
    grp("Custom", Object.keys(scope.palette.custom || {}));
    grp("Derived", scope.palette.derived || []);
    grp("Header fields", scope.palette.header_fields || []);
    const og = document.createElement("optgroup"); og.label = "Other";
    og.appendChild(new Option("— custom text —", "__text__"));
    og.appendChild(new Option("— no separator —", "__glue__"));
    sel.appendChild(og);
    return sel;
  }
  function addPartRow(part) {
    const row = el("div", "rn-part");
    const sel = tokenSelect();
    const txt = el("input", "rn-text"); txt.type = "text"; txt.placeholder = "text"; txt.classList.add("hidden");
    const up = el("button", "btn rn-mini", "↑"), down = el("button", "btn rn-mini", "↓"), del = el("button", "btn rn-mini", "✕");
    if (part) {
      if (part.type === "glue") {
        sel.value = "__glue__";
      } else if (part.type === "text") {
        sel.value = "__text__"; txt.value = part.value || ""; txt.classList.remove("hidden");
      } else {
        const name = part.name;
        if (![...sel.options].some((o) => o.value === name)) {   // token not in palette -> inject
          const og = document.createElement("optgroup"); og.label = "Preset";
          og.appendChild(new Option(name, name)); sel.insertBefore(og, sel.firstChild);
        }
        sel.value = name;
      }
    }
    sel.addEventListener("change", () => { txt.classList.toggle("hidden", sel.value !== "__text__"); updateLive(); });
    txt.addEventListener("input", updateLive);
    up.addEventListener("click", () => { const p = row.previousElementSibling; if (p) partsBox.insertBefore(row, p); updateLive(); });
    down.addEventListener("click", () => { const n = row.nextElementSibling; if (n) partsBox.insertBefore(n, row); updateLive(); });
    del.addEventListener("click", () => { row.remove(); updateLive(); });
    row.append(sel, txt, up, down, del);
    partsBox.appendChild(row);
    updateLive();
  }
  function applyPreset(preset) {
    partsBox.innerHTML = "";
    const sepVal = preset.separator != null ? preset.separator : "_";
    if ([...sepSel.options].some((o) => o.value === sepVal)) {
      sepSel.value = sepVal; sepCustom.classList.add("hidden");
    } else {
      sepSel.value = "__custom__"; sepCustom.classList.remove("hidden"); sepCustom.value = sepVal;
    }
    (preset.parts || []).forEach((p) => addPartRow(p));
    if (!partsBox.children.length) addPartRow();
    updateLive();
  }
  function buildParts() {
    return [...partsBox.querySelectorAll(".rn-part")].map((row) => {
      const sel = row.querySelector(".rn-token");
      if (sel.value === "__glue__") return { type: "glue" };
      if (sel.value === "__text__") return { type: "text", value: row.querySelector(".rn-text").value };
      return { type: "token", name: sel.value };
    });
  }
  function updateLive() {
    const parts = buildParts();
    live.textContent = parts.length ? "Example (file 1):  " + jsBuildName(parts, scope.sample, sep()) : "Add parts to build a name…";
    previewBox.innerHTML = ""; resultsBox.innerHTML = ""; applyBtn.disabled = true;
  }
  addBtn.addEventListener("click", addPartRow);

  async function run(url, box, applied) {
    if (!beginBusy(applied ? "Renaming…" : "Previewing…")) { setStatus("Please wait — an operation is already running…", "dirty"); return null; }
    previewBtn.disabled = true; applyBtn.disabled = true;
    box.innerHTML = `<div class='bulk-loading'><span class='spinner'></span> ${applied ? "Renaming" : "Previewing"}…</div>`;
    try {
      return await postJSON(url, { paths: scope.files.map((x) => x.path), parts: buildParts(), separator: sep() });
    } catch (e) {
      box.innerHTML = ""; setStatus((applied ? "Rename" : "Preview") + " failed: " + e.message, "err"); return null;
    } finally {
      state.busy = false; previewBtn.disabled = false;
    }
  }
  previewBtn.addEventListener("click", async () => {
    resultsBox.innerHTML = "";
    const res = await run("/api/rename/preview", previewBox, false);
    if (!res) return;
    renderRenameTable(previewBox, res.results, false);
    applyBtn.disabled = !res.results.some((r) => r.status === "rename");
  });
  applyBtn.addEventListener("click", async () => {
    if (!confirm(`Rename ${scope.files.length} file(s)?`)) return;
    const res = await run("/api/rename/apply", resultsBox, true);
    if (!res) return;
    renderRenameTable(resultsBox, res.results, true);
    new Set(scope.files.map((x) => folderOf(x.path))).forEach((fp) => refreshFolder(fp));
    setSelection([]);
    setStatus(`Renamed ${res.results.filter((r) => r.status === "renamed").length} file(s)`, "ok");
    applyBtn.disabled = true;
  });

  addPartRow();
}

function renderRenameTable(host, results, applied) {
  host.innerHTML = "";
  const counts = {};
  results.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  host.appendChild(el("div", "bulk-summary", (applied ? "Results:  " : "Preview:  ") +
    Object.entries(counts).map(([k, v]) => `${v} ${k}`).join("  ·  ")));
  const table = el("table", "bulk-table");
  const thead = el("thead"); const htr = el("tr");
  ["Old name", "New name", "Status"].forEach((h) => htr.appendChild(el("th", null, h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = el("tbody");
  results.forEach((r) => {
    const tr = el("tr", "st-" + r.status);
    tr.appendChild(el("td", "mono", r.old || ""));
    tr.appendChild(el("td", "mono", r.new || ""));
    tr.appendChild(el("td", null, r.status + (r.error ? `: ${r.error}` : "")));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); host.appendChild(table);
}

async function toggleFolder(li, node, childUl) {
  setSelection([]);   // clicking a folder clears any file multi-selection (and closes bulk)
  const willOpen = !li.classList.contains("open");
  li.classList.toggle("open");
  if (!willOpen || li.dataset.loaded) return;   // collapsing, or already loaded
  li.dataset.loaded = "1";                        // mark now to avoid double-load
  childUl.innerHTML = "";
  childUl.appendChild(loadingLi());
  try {
    const data = await getTree(node.path);
    childUl.innerHTML = "";
    const kids = data.children || [];
    if (!kids.length) childUl.appendChild(emptyLi());
    else kids.forEach((c) => childUl.appendChild(renderNode(c)));
    updateSelectionUI();
  } catch (e) {
    childUl.innerHTML = "";
    childUl.appendChild(el("li", "tree-error", "failed to load"));
    li.dataset.loaded = "";                        // allow a retry on next expand
  }
}

function selectFile(path, rowEl) {
  if (!confirmDiscardIfDirty()) return;
  document.querySelectorAll(".node.selected").forEach((n) => n.classList.remove("selected"));
  if (rowEl) rowEl.classList.add("selected");
  loadFile(path);
}

// Update a file's chain badge in the tree in place (e.g. after the chain is
// edited and saved) without rebuilding the whole tree.
function updateTreeBadge(path, chainInfo, chain) {
  document.querySelectorAll(".node").forEach((n) => {
    if (n.dataset.path !== path) return;
    const badge = n.querySelector(".chain-badge");
    if (!badge) return;
    badge.textContent = (chainInfo && chainInfo.short) || chain || "?";
    badge.style.background = (chainInfo && chainInfo.color) || "#666";
    badge.title = (chainInfo && chainInfo.name) || ("chain " + (chain || "?"));
  });
}

// ---- editor ----
async function loadFile(path) {
  try {
    const view = await getParse(path);
    state.file = path;
    state.view = view;
    state.edits = {};
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    updateTreeBadge(path, view.chain_info, view.chain);  // reflect chain edits in the tree
    setStatus(view.roundtrip_ok ? "Loaded (round-trip OK)" : "Loaded (round-trip DIFFERS!)",
              view.roundtrip_ok ? "ok" : "err");
  } catch (e) {
    setStatus("Parse failed: " + e.message, "err");
  }
}

function renderEditor(view) {
  $("#editorEmpty").style.display = "none";
  $("#editorTabs").classList.remove("hidden");
  const host = $("#editor");
  host.innerHTML = "";
  view.sections.forEach((sec) => host.appendChild(renderSection(sec)));
  renderRaw(view);
  switchTab("rendered");   // always land on the edit view when (re)loading
}

// ---- Raw verify tab ----
function positionRuler(width) {
  width = Math.min(Math.max(width, 10), 400);
  let tens = "";
  for (let m = 10; m <= width; m += 10) {
    const s = String(m);
    tens = tens.padEnd(m - s.length, " ") + s;
  }
  tens = tens.padEnd(width, " ");
  let ones = "";
  for (let i = 1; i <= width; i++) ones += String(i % 10);
  return tens + "\n" + ones;
}

function renderRaw(view) {
  const host = $("#rawView");
  host.innerHTML = "";

  // Toolbar: status banner + a gridlines toggle (on by default, remembered).
  const toolbar = el("div", "raw-toolbar");
  toolbar.appendChild(el("div", "raw-banner"));
  const gridOn = localStorage.getItem("okgen.rawGrid") !== "0";
  const toggle = el("label", "raw-toggle");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = gridOn;
  toggle.appendChild(cb);
  toggle.appendChild(document.createTextNode(" gridlines"));
  toolbar.appendChild(toggle);
  host.appendChild(toolbar);
  updateRawBanner();

  let text = (view.raw_text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();  // drop phantom last line
  const body = lines.join("\n");
  const maxLen = lines.reduce((m, l) => Math.max(m, l.length), 0);

  // Scroll container: a sticky line-number gutter + the code column.
  const wrap = el("div", "raw-pre" + (gridOn ? " grid" : ""));
  const inner = el("div", "raw-inner");

  const gutter = el("pre", "raw-gutter");
  // The ruler spans TWO rows (tens + ones); reserve two blank gutter rows so
  // line 1 aligns with the first data line, then 1..N.
  gutter.textContent = " \n \n" + lines.map((_, i) => i + 1).join("\n");

  const code = el("pre", "raw-code");
  const ruler = el("span", "raw-ruler", positionRuler(maxLen) + "\n");
  code.appendChild(ruler);
  code.appendChild(document.createTextNode(body));

  inner.appendChild(gutter);
  inner.appendChild(code);
  wrap.appendChild(inner);
  host.appendChild(wrap);

  cb.addEventListener("change", () => {
    wrap.classList.toggle("grid", cb.checked);
    localStorage.setItem("okgen.rawGrid", cb.checked ? "1" : "0");
  });
}

function updateRawBanner() {
  const banner = $("#rawView .raw-banner");
  if (!banner) return;
  if (isDirty()) {
    banner.textContent = "⚠ Showing the last saved file — you have unsaved edits. Save to refresh this view.";
    banner.className = "raw-banner warn";
  } else {
    banner.textContent = "Read-only view of the file on disk (use the ruler to verify character positions).";
    banner.className = "raw-banner";
  }
}

function switchTab(which) {
  const rendered = which !== "raw";
  $("#editor").classList.toggle("hidden", !rendered);
  $("#rawView").classList.toggle("hidden", rendered);
  $("#tabRendered").classList.toggle("active", rendered);
  $("#tabRaw").classList.toggle("active", !rendered);
  if (!rendered) updateRawBanner();   // refresh banner when entering Raw
}

function renderSection(sec) {
  const wrap = el("div", "section");
  const head = el("div", "section-head");
  head.appendChild(el("span", "title", sec.name));
  const count = sec.max_records != null
    ? `${sec.records.length} / ${sec.max_records} record(s)`
    : `${sec.records.length} record(s)`;
  const meta = count +
    (sec.ignored_fields && sec.ignored_fields.length ? `  ·  ignored: ${sec.ignored_fields.join(", ")}` : "");
  head.appendChild(el("span", "meta", meta));

  // Add-row button for repeating sections (not the single Header record).
  if (!sec.is_header) {
    const atLimit = sec.max_records != null && sec.records.length >= sec.max_records;
    const addBtn = el("button", "btn add-btn", "＋ Add row");
    addBtn.disabled = atLimit;
    if (atLimit) addBtn.title = `Limit of ${sec.max_records} reached`;
    addBtn.addEventListener("click", () => addRow(sec.index));
    head.appendChild(addBtn);
  }
  wrap.appendChild(head);

  const body = el("div", "section-body");
  if (sec.is_header || sec.records.length === 1) {
    body.appendChild(renderForm(sec));
  } else {
    body.appendChild(renderTable(sec));
  }
  wrap.appendChild(body);
  return wrap;
}

async function addRow(sectionIndex) {
  if (!state.file) return;
  try {
    const view = await postJSON("/api/record/add", {
      path: state.file,
      section_index: sectionIndex,
      edits: collectEdits(),
    });
    state.view = view;
    state.edits = {};
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("Row added — copied from last row (saved)", "ok");
  } catch (e) {
    setStatus("Add failed: " + e.message, "err");
  }
}

function editKey(s, r, f) { return `${s}|${r}|${f}`; }

function makeControl(sec, rec, field) {
  const value = (rec.values[field.name] != null) ? rec.values[field.name] : "";
  let ctrl;
  if (field.options) {
    ctrl = el("select", "cell fval");
    const codes = Object.keys(field.options);
    if (!codes.includes(value)) {
      ctrl.appendChild(new Option(value + " (current)", value));
    }
    codes.forEach((code) => ctrl.appendChild(new Option(`${field.options[code]} (${code})`, code)));
    ctrl.value = value;
  } else {
    ctrl = el("input", "cell fval");
    ctrl.type = "text";
    ctrl.value = value;
    if (field.size != null) ctrl.maxLength = field.size;
  }
  ctrl.dataset.section = sec.index;
  ctrl.dataset.record = rec.index;
  ctrl.dataset.field = field.name;
  ctrl.dataset.orig = value;
  ctrl.addEventListener("input", onEdit);
  ctrl.addEventListener("change", onEdit);
  return ctrl;
}

function renderForm(sec) {
  const grid = el("div", "form-grid");
  const rec = sec.records[0];
  const keyField = state.view && state.view.key_field;
  const colors = window.OKGEN_FIELD_COLORS || {};
  sec.fields.forEach((field) => {
    const isKey = field.name === keyField;
    const f = el("div", "field" + (isKey ? " field-key" : ""));
    const color = colors[field.name];
    const label = el("label", "field-label" + (!color && field.options ? " field-coded" : ""));
    label.textContent = `${field.name}  ·  ${field.size != null ? field.size : "?"}ch`;
    if (color) { label.style.color = color; label.style.fontWeight = "700"; }  // configured field color
    if (isKey) label.appendChild(el("span", "key-tag", "🔑 unique"));
    f.appendChild(label);
    f.appendChild(makeControl(sec, rec, field));
    grid.appendChild(f);
  });
  return grid;
}

function renderTable(sec) {
  const box = el("div", "rec-table");
  const table = el("table");
  const thead = el("thead");
  const htr = el("tr");
  htr.appendChild(el("th", null, "#"));
  sec.fields.forEach((f) => htr.appendChild(el("th", null, `${f.name} (${f.size != null ? f.size : "?"})`)));
  htr.appendChild(el("th", null, ""));   // delete column
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = el("tbody");
  sec.records.forEach((rec) => {
    const tr = el("tr");
    const num = el("td"); num.appendChild(el("span", "rownum", String(rec.index))); tr.appendChild(num);
    sec.fields.forEach((field) => {
      const td = el("td");
      td.appendChild(makeControl(sec, rec, field));
      tr.appendChild(td);
    });
    const delTd = el("td", "del-cell");
    const delBtn = el("button", "row-del", "✕");
    delBtn.title = "Delete this row";
    delBtn.addEventListener("click", () => deleteRow(rec.index));
    delTd.appendChild(delBtn);
    tr.appendChild(delTd);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  box.appendChild(table);
  return box;
}

async function deleteRow(recordIndex) {
  if (!state.file) return;
  if (!confirm("Delete this row?")) return;
  try {
    const view = await postJSON("/api/record/delete", {
      path: state.file,
      record_index: recordIndex,
      edits: collectEdits(),
    });
    state.view = view;
    state.edits = {};
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("Row deleted (saved)", "ok");
  } catch (e) {
    setStatus("Delete failed: " + e.message, "err");
  }
}

function onEdit(e) {
  const c = e.target;
  const key = editKey(c.dataset.section, c.dataset.record, c.dataset.field);
  if (c.value !== c.dataset.orig) {
    state.edits[key] = c.value;
    c.classList.add("dirty");
  } else {
    delete state.edits[key];
    c.classList.remove("dirty");
  }
  updateSaveButtons();
}

function updateSaveButtons() {
  const dirty = Object.keys(state.edits).length;
  $("#saveBtn").disabled = !state.file || dirty === 0;
  $("#saveAsBtn").disabled = !state.file;
  updateDirtyIndicator();
  updateRawBanner();
  if (dirty) setStatus(`${dirty} unsaved edit(s)`, "dirty");
}

function collectEdits() {
  return Object.keys(state.edits).map((k) => {
    const [s, r, f] = k.split("|");
    return { section_index: Number(s), record_index: Number(r), field: f, value: state.edits[k] };
  });
}

async function save(targetPath) {
  if (!state.file) return;
  const edits = collectEdits();
  try {
    const res = await postJSON("/api/save", {
      path: state.file,
      edits,
      target_path: targetPath || null,
    });
    state.edits = {};  // persisted — clear so refresh isn't treated as dirty
    setStatus(`Saved ${res.edits_applied} edit(s)` + (res.roundtrip_ok ? "" : " (round-trip DIFFERS!)"),
              res.roundtrip_ok ? "ok" : "err");
    const openPath = targetPath || state.file;
    if (targetPath) await refreshFolder(folderOf(targetPath));
    await loadFile(openPath);
  } catch (e) {
    setStatus("Save failed: " + e.message, "err");
  }
}

// ---- context menu (file actions) ----
function showCtxMenu(e, node, row) {
  e.preventDefault();
  // Right-clicking a file that isn't in the selection makes it the selection.
  if (!state.selection.has(node.path)) { setSelection([node.path]); state.selAnchor = node.path; }
  const count = state.selection.size;

  const menu = $("#ctxMenu");
  menu.innerHTML = "";
  const add = (label, fn, disabled) => {
    const item = el("div", "ctx-item", label);
    if (disabled) item.classList.add("disabled");
    else item.addEventListener("click", () => { hideCtxMenu(); fn(); });
    menu.appendChild(item);
  };
  if (count <= 1) add("Open", () => loadFile(node.path));
  add(count > 1 ? `Copy ${count} files` : "Copy", () => copySelection());
  add("Paste here", () => pasteInto(folderOf(node.path)), !state.clipboard.length);
  add(count > 1 ? `Make keys unique (${count})` : "Make keys unique", () => makeUniqueSelection());
  menu.appendChild(el("div", "ctx-sep"));
  add(count > 1 ? `Send ${count} files to NiceLabel` : "Send to NiceLabel", () => sendToNiceLabel());
  menu.appendChild(el("div", "ctx-sep"));
  add(count > 1 ? `Bulk Rename ${count} files…` : "Bulk Rename…", () => enterRenameMode());
  add("Rename…", () => renameFile(node), count > 1);
  add(count > 1 ? `Delete ${count} files` : "Delete",
      () => (count > 1 ? deleteSelection() : deleteFile(node)));
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
}

function copySelection() {
  state.clipboard = [...state.selection];
  setStatus(`Copied ${state.clipboard.length} file(s)`, "ok");
}
function hideCtxMenu() { $("#ctxMenu").classList.add("hidden"); }
function folderOf(p) { const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return p.slice(0, i); }

function showFolderCtxMenu(e, node) {
  e.preventDefault();
  const isRoot = node.path === state.rootDir;
  const menu = $("#ctxMenu");
  menu.innerHTML = "";
  const add = (label, fn, disabled) => {
    const item = el("div", "ctx-item", label);
    if (disabled) { item.classList.add("disabled"); }
    else item.addEventListener("click", () => { hideCtxMenu(); fn(); });
    menu.appendChild(item);
  };
  add("New folder…", () => createFolder(node.path));
  const n = state.clipboard.length;
  add(n ? `Paste ${n} item(s) here` : "Paste here (nothing copied)",
      () => pasteInto(node.path), !n);
  if (!isRoot) {
    menu.appendChild(el("div", "ctx-sep"));
    add("Copy folder", () => copyFolder(node));
    add("Rename folder…", () => renameFolder(node));
    add("Delete folder", () => deleteFolder(node));
  }
  menu.appendChild(el("div", "ctx-sep"));
  add("Make keys unique", () => makeUniqueFolder(node.path));
  add("Refresh", () => refreshFolder(node.path));
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
}

function beginBusy(message) {
  if (state.busy) return false;
  state.busy = true;
  setStatus(message, "dirty");   // immediate feedback before the (possibly slow) call
  return true;
}

async function makeUniqueFolder(path) {
  if (!beginBusy("Making keys unique…")) {
    setStatus("Please wait — an operation is already running…", "dirty");
    return;
  }
  try {
    const res = await postJSON("/api/unique/folder", { path });
    await refreshFolder(path);
    const n = (res.rekeyed || []).filter((r) => r.to).length;
    setStatus(n ? `Made keys unique: ${n} file(s) re-keyed` : "Keys already unique", "ok");
  } catch (e) {
    setStatus("Make unique failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

async function makeUniqueSelection() {
  const paths = [...state.selection];
  if (!paths.length) return;
  if (!beginBusy("Making keys unique…")) {
    setStatus("Please wait — an operation is already running…", "dirty");
    return;
  }
  try {
    const res = await postJSON("/api/unique/bulk", { paths });
    new Set(paths.map(folderOf)).forEach((f) => refreshFolder(f));
    const n = (res.folders || []).reduce((a, f) => a + (f.rekeyed || []).filter((r) => r.to).length, 0);
    setStatus(n ? `Made keys unique: ${n} file(s) re-keyed` : "Keys already unique", "ok");
  } catch (e) {
    setStatus("Make unique failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

// ---- Send to NiceLabel ----
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

async function sendToNiceLabel() {
  const paths = [...state.selection];
  if (!paths.length) return;
  const dest = window.OKGEN_NICELABEL || "the NiceLabel folder";
  if (!confirm(`Send ${paths.length} file(s) to NiceLabel?\n\n${dest}`)) return;
  if (!beginBusy("Sending to NiceLabel…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }

  showCopyAnimation(paths.length, dest);
  const minOnScreen = delay(2000);   // keep the animation up long enough to register
  try {
    const res = await postJSON("/api/send", { paths });
    await minOnScreen;
    finishCopyAnimation(res);
    const s = res.sent.length, er = res.errors.length;
    setStatus(`Sent ${s} file(s) to NiceLabel` + (er ? `, ${er} failed` : ""), er ? "err" : "ok");
  } catch (e) {
    await minOnScreen;
    hideCopyAnimation();
    setStatus("Send failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

function showCopyAnimation(n, dest) {
  hideCopyAnimation();
  const overlay = el("div", "send-overlay");
  overlay.id = "sendOverlay";
  overlay.innerHTML = `
    <div class="send-card">
      <div class="send-scene">
        <span class="send-folder">📂</span>
        <span class="send-papers">
          <span class="send-paper"></span><span class="send-paper"></span><span class="send-paper"></span>
        </span>
        <span class="send-folder">🏷️</span>
      </div>
      <div class="send-title">Sending ${n} file(s) to NiceLabel…</div>
      <div class="send-sub">${(dest || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")}</div>
    </div>`;
  document.body.appendChild(overlay);
}

function finishCopyAnimation(res) {
  const overlay = $("#sendOverlay");
  if (!overlay) return;
  const s = res.sent.length, er = res.errors.length;
  const card = overlay.querySelector(".send-card");
  // Keep the scene (papers keep flying) for enjoyment; just show the result + OK.
  const title = card.querySelector(".send-title");
  if (title) title.innerHTML = `<span class="send-ok-check">✓</span> Sent ${s} file(s) to NiceLabel${er ? ` · ${er} failed` : ""}`;
  const sub = card.querySelector(".send-sub");
  if (sub) sub.remove();
  if (!card.querySelector(".send-ok-btn")) {
    const btn = el("button", "btn btn-primary send-ok-btn", "OK");
    btn.addEventListener("click", hideCopyAnimation);
    card.appendChild(btn);
    btn.focus();   // Enter/Space closes it
  }
}

function hideCopyAnimation() {
  const overlay = $("#sendOverlay");
  if (overlay) overlay.remove();
}

async function createFolder(parentPath) {
  const name = prompt("New folder name:");
  if (!name) return;
  try {
    await postJSON("/api/folder/create", { parent: parentPath, name });
    await refreshFolder(parentPath);
    setStatus("Created folder " + name, "ok");
  } catch (e) { setStatus("Create failed: " + e.message, "err"); }
}

function copyFolder(node) {
  state.clipboard = [node.path];
  setSelection([]);                       // folder copy isn't a file multi-select
  setStatus("Copied folder: " + node.name, "ok");
}

async function renameFolder(node) {
  const name = prompt("Rename folder to:", node.name);
  if (!name || name === node.name) return;
  const sep = node.path.includes("\\") ? "\\" : "/";
  try {
    await postJSON("/api/folder/rename", { src: node.path, dst: folderOf(node.path) + sep + name });
    await refreshFolder(folderOf(node.path));
    setStatus("Renamed folder to " + name, "ok");
  } catch (e) { setStatus("Rename failed: " + e.message, "err"); }
}

async function deleteFolder(node) {
  if (!confirm("Delete folder \"" + node.name + "\" and ALL its contents?\nThis cannot be undone.")) return;
  try {
    await postJSON("/api/folder/delete", { path: node.path });
    // If the open file lived inside this folder, clear the editor.
    if (state.file && state.file.startsWith(node.path)) clearEditor();
    await refreshFolder(folderOf(node.path));
    setStatus("Deleted folder " + node.name, "ok");
  } catch (e) { setStatus("Delete failed: " + e.message, "err"); }
}

// Reload one folder's children in place (after a paste/delete/rename), without
// rebuilding or collapsing the rest of the tree. No-op if the folder isn't
// currently rendered.
async function refreshFolder(path) {
  let row = null;
  document.querySelectorAll(".folder > .node").forEach((r) => {
    if (r.dataset.path === path) row = r;
  });
  if (!row) return;
  const li = row.parentElement;
  const childUl = li.querySelector(":scope > ul");
  li.classList.add("open");
  li.dataset.loaded = "1";
  childUl.innerHTML = "";
  childUl.appendChild(loadingLi());
  try {
    const data = await getTree(path);
    childUl.innerHTML = "";
    const kids = data.children || [];
    if (!kids.length) childUl.appendChild(emptyLi());
    else kids.forEach((c) => childUl.appendChild(renderNode(c)));
    updateSelectionUI();
  } catch (e) {
    childUl.innerHTML = "";
    childUl.appendChild(el("li", "tree-error", "failed to load"));
  }
}

async function pasteInto(folder) {
  if (!state.clipboard.length) { setStatus("Clipboard empty", "err"); return; }
  if (!beginBusy("Pasting…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const res = await postJSON("/api/file/copy-batch", { srcs: state.clipboard, dst_dir: folder });
    await refreshFolder(folder);
    const c = res.copied.length, r = (res.renamed || []).length;
    const rk = (res.rekeyed || []).filter((x) => x.to).length, er = res.errors.length;
    const msg = `Pasted ${c} item(s)` +
      (r ? `, ${r} renamed to avoid overwrite` : "") +
      (rk ? `, ${rk} re-keyed for uniqueness` : "") +
      (er ? `, ${er} failed` : "");
    setStatus(msg, er ? "err" : "ok");
  } catch (e) {
    setStatus("Paste failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

function clearEditor() {
  state.file = null; state.view = null;
  $("#editor").innerHTML = ""; $("#rawView").innerHTML = "";
  $("#editorTabs").classList.add("hidden");
  $("#editorEmpty").style.display = "";
  updateSaveButtons();
}

async function deleteFile(node) {
  if (!confirm("Delete " + node.name + "? This cannot be undone.")) return;
  if (!beginBusy("Deleting…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    await postJSON("/api/file/delete", { path: node.path });
    if (state.file === node.path) clearEditor();
    await refreshFolder(folderOf(node.path));
    setStatus("Deleted " + node.name, "ok");
  } catch (e) {
    setStatus("Delete failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

async function deleteSelection() {
  const paths = [...state.selection];
  if (!paths.length) return;
  if (!confirm(`Delete ${paths.length} selected file(s)? This cannot be undone.`)) return;
  if (!beginBusy("Deleting…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const res = await postJSON("/api/file/delete-batch", { paths });
    if (state.file && paths.includes(state.file)) clearEditor();
    new Set(paths.map(folderOf)).forEach((f) => refreshFolder(f));
    setSelection([]);
    const d = res.deleted.length, er = res.errors.length;
    setStatus(`Deleted ${d} file(s)` + (er ? `, ${er} failed` : ""), er ? "err" : "ok");
  } catch (e) {
    setStatus("Delete failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

async function renameFile(node) {
  const name = prompt("Rename to:", node.name);
  if (!name || name === node.name) return;
  const sep = node.path.includes("\\") ? "\\" : "/";
  try {
    await postJSON("/api/file/rename", { src: node.path, dst: folderOf(node.path) + sep + name });
    await refreshFolder(folderOf(node.path));
    setStatus("Renamed to " + name, "ok");
  } catch (e) { setStatus("Rename failed: " + e.message, "err"); }
}

// ---- wire up ----
document.addEventListener("click", hideCtxMenu);
// Tab switching is a pure view toggle — it must NOT trigger the unsaved guard.
$("#tabRendered").addEventListener("click", () => switchTab("rendered"));
$("#tabRaw").addEventListener("click", () => switchTab("raw"));
$("#openBtn").addEventListener("click", browseFolder);
$("#bulkBtn").addEventListener("click", enterBulkMode);
$("#folderPath").addEventListener("keydown", (e) => { if (e.key === "Enter") openFolder(e.target.value.trim()); });
// Show the full folder path on hover (the box is usually too narrow to see it).
$("#folderPath").addEventListener("input", (e) => { e.target.title = e.target.value; });
$("#saveBtn").addEventListener("click", () => save(null));
$("#saveAsBtn").addEventListener("click", () => {
  const dflt = state.file ? state.file.replace(/\.OK$/i, "_copy.OK") : "";
  const target = prompt("Save As (full path):", dflt);
  if (target) save(target);
});

window.addEventListener("beforeunload", (e) => {
  if (isDirty()) { e.preventDefault(); e.returnValue = ""; }
});

// ---- resizable file panel ----
(function setupResizer() {
  const pane = $("#treePane");
  const bar = $("#dragbar");
  if (!pane || !bar) return;
  const saved = parseInt(localStorage.getItem("okgen.treeWidth"), 10);
  if (saved) pane.style.width = saved + "px";

  let dragging = false;
  bar.addEventListener("mousedown", (e) => {
    dragging = true;
    bar.classList.add("dragging");
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const layout = pane.parentElement.getBoundingClientRect();
    let w = e.clientX - layout.left;
    w = Math.max(160, Math.min(w, layout.width - 220));  // keep room for the editor
    pane.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    bar.classList.remove("dragging");
    document.body.style.userSelect = "";
    localStorage.setItem("okgen.treeWidth", String(parseInt(pane.style.width, 10) || 300));
  });
  // Double-click the divider to reset to the default width.
  bar.addEventListener("dblclick", () => {
    pane.style.width = "300px";
    localStorage.setItem("okgen.treeWidth", "300");
  });
})();

const last = localStorage.getItem("okgen.dir");
if (last) { $("#folderPath").value = last; $("#folderPath").title = last; openFolder(last); }
