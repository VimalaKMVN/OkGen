"use strict";

// ---- state ----
const state = {
  rootDir: null,
  file: null,          // current file path
  view: null,          // parsed view
  edits: {},           // key -> value
  clipboard: null,     // path copied for paste
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
const getTree = (dir) => api(`/api/tree?dir=${encodeURIComponent(dir)}`);
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
    if (res.path) { $("#folderPath").value = res.path; openFolder(res.path); }
    else setStatus("No folder selected");
  } catch (e) {
    setStatus("Native dialog unavailable — paste a path instead", "err");
  }
}

// ---- tree ----
async function openFolder(dir) {
  if (!dir) return;
  if (!confirmDiscardIfDirty()) return;
  try {
    const tree = await getTree(dir);
    state.rootDir = dir;
    localStorage.setItem("okgen.dir", dir);
    renderTree(tree);
    setStatus("Folder loaded", "ok");
  } catch (e) {
    setStatus("Open failed: " + e.message, "err");
  }
}

function renderTree(root) {
  const host = $("#tree");
  host.innerHTML = "";
  const ul = el("ul");
  ul.appendChild(renderNode(root));
  host.appendChild(ul);
}

function renderNode(node) {
  const li = el("li");
  if (node.type === "folder") {
    li.className = "folder open";
    const row = el("div", "node");
    row.appendChild(el("span", "file-name", node.name || node.path));
    row.addEventListener("click", (e) => { e.stopPropagation(); li.classList.toggle("open"); });
    li.appendChild(row);
    const childUl = el("ul");
    (node.children || []).forEach((c) => childUl.appendChild(renderNode(c)));
    li.appendChild(childUl);
  } else {
    li.className = "file";
    const row = el("div", "node");
    row.dataset.path = node.path;
    const info = node.chain_info || {};
    const badge = el("span", "chain-badge", info.short || node.chain || "?");
    badge.style.background = info.color || "#666";
    badge.title = info.name || ("chain " + (node.chain || "?"));
    row.appendChild(badge);
    row.appendChild(el("span", "file-name", node.name));
    row.addEventListener("click", () => selectFile(node.path, row));
    row.addEventListener("contextmenu", (e) => showCtxMenu(e, node));
    li.appendChild(row);
  }
  return li;
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
  const banner = el("div", "raw-banner");
  host.appendChild(banner);
  updateRawBanner();

  const text = (view.raw_text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = text.split("\n");
  const maxLen = lines.reduce((m, l) => Math.max(m, l.length), 0);

  const pre = el("pre", "raw-pre");
  // Position ruler so the user can verify character columns.
  const ruler = el("span", "raw-ruler", positionRuler(maxLen) + "\n");
  pre.appendChild(ruler);
  pre.appendChild(document.createTextNode(text));
  host.appendChild(pre);
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
  sec.fields.forEach((field) => {
    const f = el("div", "field");
    const label = el("label", field.options ? "field-coded" : null,
      `${field.name}  ·  ${field.size != null ? field.size : "?"}ch`);
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
    if (targetPath && state.rootDir) await openFolder(state.rootDir);
    await loadFile(openPath);
  } catch (e) {
    setStatus("Save failed: " + e.message, "err");
  }
}

// ---- context menu (file actions) ----
function showCtxMenu(e, node) {
  e.preventDefault();
  const menu = $("#ctxMenu");
  menu.innerHTML = "";
  const add = (label, fn) => {
    const item = el("div", "ctx-item", label);
    item.addEventListener("click", () => { hideCtxMenu(); fn(); });
    menu.appendChild(item);
  };
  add("Open", () => loadFile(node.path));
  add("Copy", () => { state.clipboard = node.path; setStatus("Copied: " + node.name, "ok"); });
  add("Paste here", () => pasteInto(folderOf(node.path)));
  menu.appendChild(el("div", "ctx-sep"));
  add("Rename…", () => renameFile(node));
  add("Delete", () => deleteFile(node));
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
}
function hideCtxMenu() { $("#ctxMenu").classList.add("hidden"); }
function folderOf(p) { const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return p.slice(0, i); }

async function pasteInto(folder) {
  if (!state.clipboard) { setStatus("Clipboard empty", "err"); return; }
  const base = state.clipboard.split(/[\\/]/).pop();
  const name = prompt("New file name:", base.replace(/\.OK$/i, "_copy.OK"));
  if (!name) return;
  const sep = folder.includes("\\") ? "\\" : "/";
  try {
    await postJSON("/api/file/copy", { src: state.clipboard, dst: folder + sep + name });
    await openFolder(state.rootDir);
    setStatus("Pasted " + name, "ok");
  } catch (e) { setStatus("Paste failed: " + e.message, "err"); }
}

async function deleteFile(node) {
  if (!confirm("Delete " + node.name + "? This cannot be undone.")) return;
  try {
    await postJSON("/api/file/delete", { path: node.path });
    if (state.file === node.path) {
      state.file = null; state.view = null;
      $("#editor").innerHTML = ""; $("#rawView").innerHTML = "";
      $("#editorTabs").classList.add("hidden");
      $("#editorEmpty").style.display = "";
      updateSaveButtons();
    }
    await openFolder(state.rootDir);
    setStatus("Deleted " + node.name, "ok");
  } catch (e) { setStatus("Delete failed: " + e.message, "err"); }
}

async function renameFile(node) {
  const name = prompt("Rename to:", node.name);
  if (!name || name === node.name) return;
  const sep = node.path.includes("\\") ? "\\" : "/";
  try {
    await postJSON("/api/file/rename", { src: node.path, dst: folderOf(node.path) + sep + name });
    await openFolder(state.rootDir);
    setStatus("Renamed to " + name, "ok");
  } catch (e) { setStatus("Rename failed: " + e.message, "err"); }
}

// ---- wire up ----
document.addEventListener("click", hideCtxMenu);
// Tab switching is a pure view toggle — it must NOT trigger the unsaved guard.
$("#tabRendered").addEventListener("click", () => switchTab("rendered"));
$("#tabRaw").addEventListener("click", () => switchTab("raw"));
$("#openBtn").addEventListener("click", browseFolder);
$("#folderPath").addEventListener("keydown", (e) => { if (e.key === "Enter") openFolder(e.target.value.trim()); });
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
if (last) { $("#folderPath").value = last; openFolder(last); }
