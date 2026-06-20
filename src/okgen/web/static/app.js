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

// ---- tree ----
async function openFolder(dir) {
  if (!dir) return;
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
  document.querySelectorAll(".node.selected").forEach((n) => n.classList.remove("selected"));
  if (rowEl) rowEl.classList.add("selected");
  loadFile(path);
}

// ---- editor ----
async function loadFile(path) {
  try {
    const view = await getParse(path);
    state.file = path;
    state.view = view;
    state.edits = {};
    renderEditor(view);
    $("#fileTitle").textContent = `${view.name}  ·  ${view.layout}  ·  ${view.chain_info ? view.chain_info.name : view.chain}`;
    updateSaveButtons();
    setStatus(view.roundtrip_ok ? "Loaded (round-trip OK)" : "Loaded (round-trip DIFFERS!)",
              view.roundtrip_ok ? "ok" : "err");
  } catch (e) {
    setStatus("Parse failed: " + e.message, "err");
  }
}

function renderEditor(view) {
  $("#editorEmpty").style.display = "none";
  const host = $("#editor");
  host.innerHTML = "";
  view.sections.forEach((sec) => host.appendChild(renderSection(sec)));
}

function renderSection(sec) {
  const wrap = el("div", "section");
  const head = el("div", "section-head");
  head.appendChild(el("span", "title", sec.name));
  const meta = `${sec.records.length} record(s)` +
    (sec.ignored_fields && sec.ignored_fields.length ? `  ·  ignored: ${sec.ignored_fields.join(", ")}` : "");
  head.appendChild(el("span", "meta", meta));
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
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  box.appendChild(table);
  return box;
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
    if (state.file === node.path) { state.file = null; $("#editor").innerHTML = ""; $("#editorEmpty").style.display = ""; updateSaveButtons(); }
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
$("#openBtn").addEventListener("click", () => openFolder($("#folderPath").value.trim()));
$("#folderPath").addEventListener("keydown", (e) => { if (e.key === "Enter") openFolder(e.target.value.trim()); });
$("#saveBtn").addEventListener("click", () => save(null));
$("#saveAsBtn").addEventListener("click", () => {
  const dflt = state.file ? state.file.replace(/\.OK$/i, "_copy.OK") : "";
  const target = prompt("Save As (full path):", dflt);
  if (target) save(target);
});

const last = localStorage.getItem("okgen.dir");
if (last) { $("#folderPath").value = last; openFolder(last); }
