"use strict";

// Build marker — shown in the Generate panel header so a cached copy of this
// file is obvious at a glance ("build" in the UI vs the tag you deployed).
const OKGEN_BUILD = "v0.30.2";

// Nothing in this app should fail silently: surface JS errors and rejected
// promises in the status bar, otherwise a thrown error inside a click handler
// looks exactly like a dead button.
window.addEventListener("error", (e) => {
  try { setStatus("Script error: " + (e.message || e.error), "err"); } catch (_) {}
});
window.addEventListener("unhandledrejection", (e) => {
  const msg = (e.reason && (e.reason.message || e.reason)) || "unknown";
  try { setStatus("Unhandled error: " + msg, "err"); } catch (_) {}
});

// ---- state ----
const state = {
  rootDir: null,
  file: null,          // current file path
  view: null,          // parsed view
  edits: {},           // key -> value
  ops: [],             // staged row-op journal, replayed on Save/Save As
  normalized: 0,       // junk removed on open (blank lines + trimmed lines); Save persists it
  cleanupDesc: "",     // human description of that cleanup, for the status/banner
  clipboard: [],       // array of paths copied for paste
  treeToken: 0,        // increments per Open; guards against stale renders
  treeAbort: null,     // AbortController for the in-flight root load
  selection: new Set(),// multi-selected file paths (for bulk copy / future bulk edit)
  selAnchor: null,     // last plainly-clicked file, for Shift-range select
  busy: false,         // guards slow file ops (make-unique) from double-runs
  browsing: false,     // a native folder dialog is open — block re-launching it
  activityTimer: null, // auto-hide timer for the activity indicator
  activityResult: false, // a result is showing — don't let a follow-up status clobber it
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
// ---- JSON source (SCAN / WMS) ----
// Calgary JSON files come from two sources that send identical structure but a
// different unique-key field, and nothing in the file says which. It's normally
// read from a SCAN/WMS token in the folder/file name; when a folder carries no
// token we ask ONCE and remember the answer here, per folder. Remembering it
// against the FOLDER (rather than a mode/tab) is deliberate: there is no
// "current source" to be in, so nothing can go stale and be silently wrong.
const dirOf = (p) => String(p || "").replace(/[\\/][^\\/]*$/, "");

// No `source` is sent with any request any more. A Calgary JSON file states its
// own source (headerASNid present = WMS, absent = SCAN), so a remembered
// folder-level answer could only ever contradict the file in front of you —
// which is how a WMS keytrol used to get renumbered.
const srcParam = () => "";

const getTree = (dir, signal) =>
  api(`/api/tree?dir=${encodeURIComponent(dir)}${srcParam(dir)}`,
      signal ? { signal } : undefined);
const getParse = (p) =>
  api(`/api/parse?path=${encodeURIComponent(p)}${srcParam(dirOf(p))}`);
const postJSON = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---- status ----
function setStatus(msg, kind) {
  const s = $("#status");
  s.textContent = msg || "";
  s.className = "status" + (kind ? " " + kind : "");
  // Mirror into the prominent activity indicator while it's on screen.
  const a = $("#activity");
  if (a && !a.classList.contains("hidden")) {
    if (state.activityResult) return;   // keep the operation's result; don't clobber it
    if (kind === "ok" || kind === "err") activityResult(msg, kind);
    else activityWorking(msg);
  }
}

// ---- prominent activity indicator (center-top) ----
function showActivity(msg) {
  const a = $("#activity");
  clearTimeout(state.activityTimer);
  state.activityResult = false;
  a.className = "act-working";
  a.innerHTML = '<span class="spinner"></span><span class="act-msg"></span>';
  a.querySelector(".act-msg").textContent = msg || "Working…";
  a.classList.remove("hidden");
}
function activityWorking(msg) {
  const m = $("#activity .act-msg");
  if (m) m.textContent = msg;
}
function activityResult(msg, kind) {
  const a = $("#activity");
  state.activityResult = true;
  a.className = kind === "err" ? "act-err" : "act-ok";
  a.innerHTML = `<span class="act-icon">${kind === "err" ? "✗" : "✓"}</span><span class="act-msg"></span>`;
  a.querySelector(".act-msg").textContent = msg;
  a.classList.remove("hidden");
  clearTimeout(state.activityTimer);
  state.activityTimer = setTimeout(() => { $("#activity").classList.add("hidden"); state.activityResult = false; }, 2800);
}
function hideActivity() { $("#activity").classList.add("hidden"); }

// ---- dirty state ----
// Two kinds of pending change, both unsaved until Save/Save As:
//   state.edits — field edits keyed section|record|field
//   state.ops   — the ordered row-op journal (add/delete/move + the field edits
//                 that were pending when each row op was made). The server
//                 replays it against the file on disk; nothing is written until
//                 a Save button, so Save As leaves the original untouched.
function isDirty() { return Object.keys(state.edits).length > 0 || pendingOps().length > 0; }
function pendingOps() { return state.ops || (state.ops = []); }
function dirtyCount() { return Object.keys(state.edits).length + pendingOps().length; }
// The journal the server should replay if `op` is accepted: everything staged
// so far, then the field edits pending right now (so they replay in the order
// the user made them), then the op itself. Returned as a NEW array — it is only
// committed to state.ops once the server accepts it, so a rejected op (e.g. a
// section at its record limit) leaves the pending state exactly as it was.
function journalWith(op) {
  const edits = collectEdits();
  return pendingOps().concat(edits.length ? [{ type: "edit", edits }] : [], [op]);
}
function commitJournal(journal) {
  state.ops = journal;
  state.edits = {};   // now folded into the journal
}
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

// ---- auto-clean-on-open toggle (persisted, default ON) ----
function autoCleanEnabled() { return localStorage.getItem("okgen.autoClean") !== "0"; }
(function initAutoCleanToggle() {
  const chk = $("#autoCleanChk");
  if (!chk) return;
  chk.checked = autoCleanEnabled();
  chk.addEventListener("change", () => {
    localStorage.setItem("okgen.autoClean", chk.checked ? "1" : "0");
    setStatus(chk.checked
      ? "Auto-clean ON — opening a file with stray blank lines removes them and saves"
      : "Auto-clean OFF — you'll be asked to Save blank-line removals yourself", "ok");
  });
})();

// NOTE: there is deliberately NO auto-fix-Total-Qty-on-open toggle.
// One shipped in v0.77.0 (default OFF) and was withdrawn: a single global
// browser setting cannot mean "my files but not someone else's", and opening a
// file is the one action that must never rewrite a FIELD VALUE — auto-clean may
// default ON precisely because junk is not data, which is the distinction that
// does not survive here. The backlog goes through the Total Qty check sweep
// instead, which previews before it writes.

// ---- folder picker (native OS dialog) ----
async function browseFolder() {
  const btn = $("#openBtn");
  // One dialog at a time. The chooser is a BLOCKING native window (it can open
  // behind the browser, so a click can look like "nothing happened"); without
  // this guard, repeated clicks stack up hidden dialogs that then re-surface one
  // after another. Re-clicking now just re-nudges the reminder instead.
  // Clicking again while one is open used to repeat "look behind this window"
  // for the full two-minute timeout — advice that is actively wrong when the
  // dialog never appeared at all. Offer the way out instead.
  if (state.browsing) {
    const secs = Math.round((Date.now() - (state.browsingSince || Date.now())) / 1000);
    if (confirm(`A folder chooser has been open for ${secs}s.\n\n`
                + "If you cannot see it (check other monitors and behind this "
                + "window), click OK to abandon it so you can try again.")) {
      try { await postJSON("/api/browse-folder/cancel", {}); } catch (e) { /* it may have just closed */ }
      setStatus("Abandoned the folder chooser — click Open Folder to try again, "
                + "or paste a path", "dirty");
    }
    return;
  }
  state.browsing = true;
  state.browsingSince = Date.now();
  btn.disabled = false;   // stays clickable ON PURPOSE — see the guard above
  setStatus("Opening folder chooser… (if you don't see it, check behind this window "
            + "and on your other monitors)", "dirty");
  try {
    const res = await postJSON("/api/browse-folder", {});
    if (res.path) { const fp = $("#folderPath"); fp.value = res.path; fp.title = res.path; openFolder(res.path); }
    else if (res.already_open) setStatus("Folder chooser is already open — look behind this window", "dirty");
    // A launch that FAILED is no longer indistinguishable from a cancel: say
    // what went wrong and where the log is, so the next report carries evidence.
    else if (res.failed) {
      setStatus(res.error + (res.log ? `  ·  logged to ${res.log}` : ""), "err");
    } else if (res.abandoned) { /* the message is already on screen */ }
    else setStatus("No folder selected");
  } catch (e) {
    setStatus("Native dialog unavailable — paste a path instead", "err");
  } finally {
    state.browsing = false;
    state.browsingSince = null;
    btn.disabled = false;
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
  closeAllPanels();      // a new folder always starts from a clean editor

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

// Ask, ONCE per folder, whether its JSON files are SCAN or WMS — shown only
// when the server couldn't tell from any name and no answer is stored yet.
// Answering re-reads the folder so the keys shown are the right ones.
// The SCAN/WMS folder prompt is GONE. A Calgary JSON file says which source it
// came from — a populated headerASNid means WMS, an empty one means SCAN — so
// there is nothing to ask and no per-folder answer to remember. Each file now
// carries its own badge in the tree.

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
  if (node.json) {                     // Calgary JSON layout — flag the format
    const jtag = el("span", "json-tag", "JSON");
    jtag.title = "JSON layout (" + (node.layout || "") + ")";
    row.appendChild(jtag);
  }
  // SCAN or WMS, read from the file's OWN headerASNid. Shown for every Calgary
  // layout — on CartonLabel it is informational, since pickListId is the key
  // either way.
  if (node.source) {
    const stag = el("span", "src-badge src-badge-" + node.source.toLowerCase(), node.source);
    // A JSON file's source is read from its own headerASNid; an .OK file's is
    // fixed by its layout (EU/EWMS = WMS, the NA layouts = SCAN), so each kind
    // has to explain itself differently.
    if (node.json) {
      stag.title = node.source === "WMS"
        ? `WMS — this file has a headerASNid. Key: ${node.key_field || "?"}`
        : `SCAN — this file has no headerASNid. Key: ${node.key_field || "?"}`;
    } else {
      stag.title = `${node.source} — every ${node.layout || ".OK"} file comes from `
        + `${node.source}. Key: ${node.key_field || "?"} (not affected by the source)`;
    }
    row.appendChild(stag);
  }
  row.appendChild(nameEl);
  // The summed section is empty, so this file's header total is the quantity
  // itself rather than a sum. Informational, NOT a warning: this is a normal
  // shape in the new system, and it is the marker that says "if this total is
  // wrong, only a person can fix it — nothing here can be recomputed".
  if (node.no_rollup_rows) {
    const tag = el("span", "no-rows-tag", "NoSzLines");
    tag.title = "No detail rows — this file's total quantity is the printed "
      + "quantity, and is never recalculated. Check it is the value you want.";
    row.appendChild(tag);
  }
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
  // Cmd/Ctrl- and Shift-click only ADJUST the selection — the user is still
  // working in the bulk panel, so leave it open. A plain click opens a file
  // (loadFile closes the panels).
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
  // Bulk actions work on any non-empty selection — including a single file
  // (a plain click already selects the file it opens), so the button shows
  // for 1+ files. It only hides when nothing is selected.
  if (btn) { btn.classList.toggle("hidden", n < 1); btn.textContent = `Bulk Actions (${n}) ▾`; }
  // Run TOSCA Script — separate from Bulk (a fundamentally different action);
  // shows for any non-empty selection, like Bulk Actions.
  const tb = $("#toscaBtn");
  if (tb) { tb.classList.toggle("hidden", n < 1); tb.textContent = `▶ Run TOSCA (${n})`; }
  // Close the bulk panel only when the selection is emptied.
  if (isBulkOpen() && n < 1) exitBulkMode();
}

function isBulkOpen() {
  return !$("#bulkPanel").classList.contains("hidden");
}
function isRenameOpen() {
  return !$("#renamePanel").classList.contains("hidden");
}

// ---- bulk edit ----
// TWO panels, because they do genuinely different things:
//   * FIELD VALUES  — many fields at once, across every section (multi mode).
//   * ROWS & SEQUENCES — add / keep-first-N / set-unique (single-op mode).
// They are not two doors to one job: field edits are order-independent (each
// writes a different field), while row ops are not — "keep 0 rows" then "set
// qty" writes nothing. Keeping them apart is what lets the field panel drop the
// Operation dropdown entirely and show every section at once.
let bulkMode = "fields";               // "fields" | "rows"

async function enterBulkMode(mode) {
  bulkMode = mode || "fields";
  if (state.selection.size < 1) return;
  if (!confirmDiscardIfDirty()) return;
  closeAllPanels("bulk");        // only one bulk mode open at a time
  state.file = null; state.view = null; state.edits = {}; state.ops = []; state.normalized = 0;
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
    if (bulkMode === "fields") renderBulkFieldsPanel(scope);
    else renderBulkPanel(scope);
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
  restoreEditorAfterPanel();
}

// Put the editor back the way a panel found it: show the open file's editor, or
// the empty-state when no file is open.
function restoreEditorAfterPanel() {
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

// Bulk Edit / Bulk Rename / Generate are full-screen modes that replace the
// editor — only one may be open, and NONE may survive navigating to a file or
// folder. Previously each panel closed under its own rules (rename on any tree
// click, bulk only when the selection emptied, generate never), so a finished
// bulk screen lingered under the editor.
function closeAllPanels(keep) {
  if (keep !== "bulk") exitBulkMode();
  if (keep !== "rename") exitRenameMode();
  if (keep !== "generate") exitGenerateMode();
}


// ---- Bulk Edit: FIELD VALUES (many fields, every section, one apply) --------
//
// Shaped like Volume Generate on purpose — the user asked for the same controls
// — but applied to the SELECTED files instead of generating new ones. Two
// differences that matter and are stated in the panel, because both are easy to
// assume the other way round:
//
//   * an UNTICKED field keeps each file's OWN value (Generate inherits the
//     template's); nothing is blanked or made uniform.
//   * a min/max RANGE varies per file — 12 files get 12 different values. To
//     make them all the same, give one value, not a range.
//
// Every control maps onto a bulk op that already exists, so nothing new can be
// written: one value or a comma list -> `list`, a range -> `random`, a date
// range -> `random_date`. Zero-padding, chain isolation, date coercion and the
// Total Qty roll-up all fire on those ops exactly as they always have.
//
// NOTE this picker is a PARALLEL implementation of Generate's, not a shared
// one — the rows differ (a values box is primary here, and there are no row
// counts), and parameterising a working panel was the riskier move. They can
// drift; see PLAN §6.
// The sentence a panel shows when a section holds no rows with data. A field
// value aimed at one is SKIPPED (D75), and nothing in the panel could say so
// until `bulk_scope` began carrying the counts — so the user found out only
// after applying to the whole selection.
//
// Worded from the COUNT, because blankness is per FILE: a section can be blank
// in some of a selection and full in the rest, and one ticked field applies to
// all of them.
function noDataNote(sec) {
  if (!sec || sec.no_data_files === undefined || !sec.no_data_files) return null;
  const n = sec.no_data_files;
  const total = sec.files || n;
  const scope = n >= total ? (total === 1 ? "this file" : `all ${total} files`)
                           : `${n} of ${total} files`;
  return `⚠ ${sec.name} has no rows with data in ${scope}. `
       + `A value here is skipped for ${n === 1 && total === 1 ? "it" : "those"} `
       + `— add rows first (Bulk Edit — rows & sequences → Add rows).`;
}

function renderBulkFieldsPanel(scope) {
  const panel = $("#bulkPanel");
  panel.innerHTML = "";
  const layouts = Object.keys(scope.layouts || {});
  if (!layouts.length) {
    panel.appendChild(el("div", "bulk-note", "No recognised files in the selection."));
    return;
  }
  let selectedLayout = layouts[0];

  panel.appendChild(el("div", "bulk-title", "Bulk Edit — field values"));
  const scopeBox = el("div", "bulk-scope");
  scopeBox.appendChild(el("span", "bulk-label",
    `${(scope.files || []).length} file(s) selected · layout:`));
  layouts.forEach((name) => {
    const lbl = el("label", "bulk-layout");
    const rb = el("input"); rb.type = "radio"; rb.name = "bulkFieldsLayout";
    if (name === selectedLayout) rb.checked = true;
    rb.addEventListener("change", () => { selectedLayout = name; build(); });
    lbl.appendChild(rb);
    lbl.appendChild(document.createTextNode(` ${name} (${scope.layouts[name]})`));
    scopeBox.appendChild(lbl);
  });
  panel.appendChild(scopeBox);

  // Which SOURCE the selected files came from. Shown because on a Calgary
  // StyleHeader/DistLabel it is what DECIDES the key — `keytrol` for SCAN,
  // `headerASNid` for WMS — so the greyed field below only makes sense beside
  // it. Read from each file's own payload, never from the folder name.
  const srcBox = el("div", "bulk-sources");
  panel.appendChild(srcBox);
  function renderSources() {
    srcBox.innerHTML = "";
    const counts = (scope.sources || {})[selectedLayout];
    if (!counts || !Object.keys(counts).length) { srcBox.hidden = true; return; }
    srcBox.hidden = false;
    srcBox.appendChild(el("span", "bulk-label", "source:"));
    Object.keys(counts).sort().forEach((s) => {
      // Same badge classes as the tree, so SCAN/WMS look identical everywhere.
      srcBox.appendChild(el("span", `src-badge src-badge-${s.toLowerCase()}`,
                            `${s} (${counts[s]})`));
    });
    const keys = Object.keys((scope.key_fields || {})[selectedLayout] || {});
    if (keys.length) {
      srcBox.appendChild(el("span", "bulk-note",
        keys.length > 1
          ? `mixed selection — ${keys.join(" and ")} are both keys here, so both are locked`
          : `key: ${keys[0]}`));
    }
  }

  panel.appendChild(el("div", "bulk-note",
    "Tick the fields to change. One value sets it on every file; a comma list "
    + "gives each file (or row) a random pick; a range varies per file — so for "
    + "the same value everywhere, give one value, not a range. Fields you leave "
    + "unticked keep each file's own value."));

  const groups = el("div", "bulkf-groups");
  panel.appendChild(groups);

  const actions = el("div", "bulk-actions");
  const previewBtn = el("button", "btn", "Preview");
  const applyBtn = el("button", "btn btn-primary", "Apply"); applyBtn.disabled = true;
  actions.appendChild(previewBtn); actions.appendChild(applyBtn);
  panel.appendChild(actions);
  const previewBox = el("div", "bulk-preview");
  const resultsBox = el("div", "bulk-results");
  panel.appendChild(previewBox); panel.appendChild(resultsBox);
  const reset = () => { applyBtn.disabled = true; previewBox.innerHTML = ""; resultsBox.innerHTML = ""; };

  // One row per field: tick it, then fill EITHER the values box or the range.
  function fieldRow(sectionName, f) {
    const row = el("label", "bulkf-field");
    const cb = el("input", "bulkf-on"); cb.type = "checkbox";
    cb.dataset.section = sectionName;
    cb.dataset.field = f.name;
    // A locked field is SHOWN, greyed, with the reason — omitting it reads as
    // "OkGen forgot this field" rather than "you may not change it".
    const locked = f.editable === false;
    if (locked) { cb.disabled = true; row.classList.add("bulkf-locked"); }
    row.appendChild(cb);
    row.appendChild(el("span", "bulkf-name",
                       f.date ? `${f.name} (date)` : `${f.name} (${f.size != null ? f.size : "?"})`));
    const isDate = !!f.date;
    const vals = el("input", "bulkf-vals"); vals.type = "text";
    vals.placeholder = isDate ? "2024-06-30  (or a list)"
                              : "value, or 10,20,30   (' ' = blank)";
    const mn = el("input", "bulkf-min"); mn.type = "text";
    const mx = el("input", "bulkf-max"); mx.type = "text";
    mn.placeholder = isDate ? "from 2024-01-01" : "min";
    mx.placeholder = isDate ? "to 2024-12-31" : "max";
    [vals, mn, mx].forEach((i) => { i.disabled = true; });
    // NO option list here, deliberately. Volume Generate offers none either, and
    // a field's known values are inconsistent across layouts today (some lists
    // are the whole truth, some are suggestions) — so a picker in one place and
    // not another reads as a bug. Plain text everywhere until the lists are
    // worth trusting; the value is validated on the write path regardless.
    const sync = () => {
      if (locked) { [vals, mn, mx].forEach((i) => { i.disabled = true; }); return; }
      [vals, mn, mx].forEach((i) => { i.disabled = !cb.checked; });
      // A range and a value list are alternatives — filling one greys the other,
      // so an op can never be ambiguous about which the server should use.
      if (cb.checked) {
        const usingVals = vals.value.trim() !== "";
        mn.disabled = mx.disabled = usingVals;
        vals.disabled = !usingVals && (mn.value.trim() !== "" || mx.value.trim() !== "");
      }
    };
    cb.addEventListener("change", () => { sync(); reset(); });
    [vals, mn, mx].forEach((i) => i.addEventListener("input", () => { sync(); reset(); }));
    row.appendChild(vals); row.appendChild(mn); row.appendChild(mx);
    if (locked) {
      row.appendChild(el("span", "bulkf-lockreason", f.locked_reason || "read-only"));
    }

    // A roll-up field is not written as typed — say so here too, in the same
    // words as the editor badge and the single-op panel.
    const rspec = rollupSpecFor(scope.rollups, selectedLayout, f.name);
    if (rspec) row.appendChild(el("div", "bulk-rollup-note", rollupWarning(rspec)));
    return row;
  }

  function group(title, sectionName, fields, note) {
    const box = el("div", "bulkf-group");
    box.appendChild(el("div", "bulk-label", title));
    if (note) box.appendChild(el("div", "bulkf-note", note));
    if (!fields.length) {
      box.appendChild(el("div", "bulk-note", "No editable fields here."));
      return box;
    }
    fields.forEach((f) => box.appendChild(fieldRow(sectionName, f)));
    return box;
  }

  function build() {
    groups.innerHTML = "";
    groups.appendChild(group("Header fields", "Header",
                             scope.header_fields[selectedLayout] || []));
    (scope.detail_sections[selectedLayout] || []).forEach((d) => {
      const box = group(`“${d.name}” row fields`, d.name, d.fields || [],
                        "applies to every row");
      const note = noDataNote(d);
      if (note) box.appendChild(el("div", "bulk-nodata-note", note));
      groups.appendChild(box);
    });
    renderSources();
    reset();
  }
  build();

  // Build the op list from the ticked rows. Each control maps onto an op that
  // already exists — nothing here can write something bulk could not write
  // before.
  function buildOps() {
    const ops = [];
    descendantsOf(groups, "bulkf-field").forEach((row) => {
      const cb = row.querySelector(".bulkf-on");
      if (!cb || !cb.checked || cb.disabled) return;
      const section = cb.dataset.section, field = cb.dataset.field;
      const vals = (row.querySelector(".bulkf-vals") || {}).value || "";
      const mn = (row.querySelector(".bulkf-min") || {}).value || "";
      const mx = (row.querySelector(".bulkf-max") || {}).value || "";
      const isDate = /\(date\)/.test((row.querySelector(".bulkf-name") || {}).textContent || "");
      if (vals.trim() !== "") {
        // A single entry IS "set this value" — the server's list op picks from
        // a one-item list, so no separate `set` case is needed.
        ops.push({ section, type: "list", field, values: vals });
      } else if (mn.trim() !== "" || mx.trim() !== "") {
        if (isDate) ops.push({ section, type: "random_date", field, from: mn, to: mx });
        else {
          const o = { section, type: "random", field };
          if (mn.trim() !== "") o.min = Number(mn);
          if (mx.trim() !== "") o.max = Number(mx);
          ops.push(o);
        }
      }
    });
    return ops;
  }

  async function run(url, box, applied) {
    const ops = buildOps();
    if (!ops.length) {
      setStatus("Tick at least one field and give it a value or a range", "dirty");
      return null;
    }
    if (!beginBusy(applied ? "Applying…" : "Previewing…")) {
      setStatus("Please wait — an operation is already running…", "dirty"); return null;
    }
    previewBtn.disabled = true; applyBtn.disabled = true;
    box.innerHTML = `<div class='bulk-loading'><span class='spinner'></span> ${applied ? "Applying" : "Previewing"}…</div>`;
    try {
      return await postJSON(url, { paths: scope.files.map((x) => x.path),
                                   layout: selectedLayout, ops });
    } catch (e) {
      box.innerHTML = "";
      setStatus((applied ? "Apply" : "Preview") + " failed: " + e.message, "err");
      return null;
    } finally {
      state.busy = false; previewBtn.disabled = false;
    }
  }

  previewBtn.addEventListener("click", async () => {
    resultsBox.innerHTML = "";
    const res = await run("/api/bulk/multi/preview", previewBox, false);
    if (!res) return;
    renderBulkFieldsTable(previewBox, res.results, false);
    applyBtn.disabled = !res.results.some((r) => r.status === "change");
  });

  applyBtn.addEventListener("click", async () => {
    const ops = buildOps();
    if (!confirm(`Apply ${ops.length} field change(s) to the ${selectedLayout} files?\n`
                 + "A .bak backup is made for each changed file.")) return;
    const res = await run("/api/bulk/multi/apply", resultsBox, true);
    if (!res) return;
    renderBulkFieldsTable(resultsBox, res.results, true);
    new Set(res.results.filter((r) => r.status === "changed").map((r) => folderOf(r.path)))
      .forEach((fp) => refreshFolder(fp));
    setStatus(`Bulk applied: ${res.results.filter((r) => r.status === "changed").length} changed`, "ok");
    applyBtn.disabled = true;
  });
}

// Elements under `host` carrying `cls` — the stub DOM has no :scope support, so
// this stays a plain filter rather than a fancy selector.
function descendantsOf(host, cls) {
  return Array.prototype.slice.call(host.querySelectorAll("." + cls));
}

// PER-FIELD reporting. One summary line per file cannot say which field moved
// and which was corrected — and a roll-up rewrites a typed total on its own
// (D58), which is exactly the kind of thing v0.78.0 exists to stop hiding.
function renderBulkFieldsTable(host, results, applied) {
  host.innerHTML = "";
  const counts = {};
  results.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  host.appendChild(el("div", "bulk-summary", (applied ? "Results:  " : "Preview:  ")
    + Object.entries(counts).map(([k, v]) => `${v} ${k}`).join("  ·  ")));
  const table = el("table", "bulk-table");
  const thead = el("thead"); const htr = el("tr");
  ["File", "Fields", "Status"].forEach((h) => htr.appendChild(el("th", null, h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = el("tbody");
  results.forEach((r) => {
    const tr = el("tr", "st-" + r.status);
    tr.appendChild(el("td", null, r.name));
    const cell = el("td", "mono");
    (r.fields || []).forEach((f) => {
      const line = el("div", "bulkf-line" + (f.status === "change" ? "" : " bulkf-quiet"));
      // `format: A → B` is what the single-field panel used to say and what
      // people read the preview FOR — "changed" alone does not let you check
      // the change before applying it to a whole selection.
      if (f.error) {
        line.textContent = `${f.field}: ${f.error}`;
      } else if (f.before !== undefined) {
        const rows = f.rows > 1 ? `  (${f.moved}/${f.rows} rows${f.varies ? ", varies" : ""})` : "";
        line.textContent = `${f.field}: ${f.before} → ${f.after}${rows}`;
        // A roll-up is not written as typed (D58). Say where the value came
        // from AND what was discarded — a corrected total that looks like an
        // ordinary change is the thing v0.78.0 exists to prevent.
        if (f.rollup) {
          line.classList.add("bulkf-roll");
          // `(sum of N size lines)` is the SHIPPED phrase — identical to the
          // single-op bulk preview and the editor badge, so one rule reads the
          // same wherever it is met. The discarded value is named only when
          // there IS one, since that is the "where did my number go" case.
          const sec = String(f.rollup.section).toLowerCase();
          line.textContent += ` (sum of ${f.rollup.rows} ${sec} lines)`
            + (f.rollup.typed ? ` — your ${f.rollup.typed} was not used` : "");
        }
      } else if (f.rollup) {
        line.classList.add("bulkf-roll");
        // Nothing moved: the typed value already equalled the sum. Same
        // parenthetical as every other surface.
        line.textContent = `${f.field}: unchanged `
          + `(sum of ${f.rollup.rows} ${String(f.rollup.section).toLowerCase()} lines)`;
      } else {
        line.textContent = `${f.field}: ${f.detail || f.status}`;
      }
      cell.appendChild(line);
    });
    // What the SAVE corrected on its own, never silent.
    (r.rollups || []).forEach((x) => {
      cell.appendChild(el("div", "bulkf-line bulkf-roll",
        `${x.field} → ${x.to} (sum of ${x.rows} ${String(x.section).toLowerCase()} lines)`));
    });
    if (!(r.fields || []).length) cell.textContent = r.error || r.status;
    tr.appendChild(cell);
    tr.appendChild(el("td", null, r.status + (r.error && !(r.fields || []).length ? `: ${r.error}` : "")));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); host.appendChild(table);
}

function renderBulkPanel(scope) {
  const panel = $("#bulkPanel");
  panel.innerHTML = "";
  const layoutNames = Object.keys(scope.layouts);

  const head = el("div", "bulk-head");
  head.appendChild(el("h3", null, `Bulk Edit — rows & sequences — ${scope.files.length} ${scope.files.length === 1 ? "file" : "files"} selected`));
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
  // Sits under the Section/Operation row and is refilled by rebuildOps, so it
  // always describes the section currently chosen.
  const noteBox = el("div", "bulk-nodata-host");
  panel.appendChild(noteBox);

  // A roll-up header field (config/rollup_fields.yaml) is not written as typed:
  // where the detail section HAS rows the sum wins, so the value only lands on
  // files with no rows. Say so the moment the field is picked, rather than
  // letting the user build the whole operation and find out from the preview.
  const rollupNote = el("div", "bulk-rollup-note");
  panel.appendChild(rollupNote);
  function updateRollupNote() {
    rollupNote.textContent = "";
    const sec = curSection();
    if (!sec || !sec.isHeader) return;
    const fieldSel = row2.querySelector("select.bulk-field");
    if (!fieldSel) return;
    const spec = rollupSpecFor(scope.rollups, selectedLayout, fieldSel.value);
    if (spec) rollupNote.textContent = rollupWarning(spec);
  }

  const actions = el("div", "bulk-actions");
  const previewBtn = el("button", "btn", "Preview");
  const applyBtn = el("button", "btn btn-primary", "Apply"); applyBtn.disabled = true;
  actions.appendChild(previewBtn); actions.appendChild(applyBtn);
  panel.appendChild(actions);

  const previewBox = el("div", "bulk-preview");
  const resultsBox = el("div", "bulk-results");
  panel.appendChild(previewBox); panel.appendChild(resultsBox);

  // Sections for the current layout: Header + detail sections.
  // ROWS & SEQUENCES only. The Header is deliberately absent: unique / add /
  // keep-first-N are all row operations and none of them exists for a section
  // that holds a single record. Field VALUES — including header fields — live
  // in the multi-field panel now.
  function sectionsFor() {
    return (scope.detail_sections[selectedLayout] || [])
      .map((d) => ({ ...d, isHeader: false }));
  }
  const curSection = () => sectionsFor().find((s) => s.name === sectionSel.value);
  const reset = () => { applyBtn.disabled = true; previewBox.innerHTML = ""; resultsBox.innerHTML = ""; };

  // The field-VALUE ops (set / list / random / random_date) moved to the
  // multi-field panel, where several fields are done in one apply. What stays
  // is what could not move: `unique` numbers each row differently (it has no
  // equivalent in a "give this field a value" grid), and the row ops change how
  // many rows there are, where ORDER matters — "keep 0 rows" then "set qty"
  // writes nothing, which is exactly why they are not mixed into that panel.
  function opsForSection(sec) {
    return [
      { v: "unique", t: "Set unique value (each row)" },
      { v: "add", t: "Add rows" },
      { v: "keep", t: "Keep first N rows" },
    ];
  }

  // Only sections holding a field configured as temporal (date_fields.yaml)
  // can offer a date range — nothing else has a format to render into.
  function hasDateField(sec) {
    return !!(sec && (sec.fields || []).some((f) => f.date));
  }

  function rebuildInputs() {
    row2.innerHTML = "";
    const sec = curSection(); if (!sec) return;
    const op = opSel.value;
    if (op === "set" || op === "random" || op === "unique" || op === "list"
        || op === "random_date") {
      const fieldSel = el("select", "bulk-field");
      // A date range applies only to the temporal fields; every other op keeps
      // the full list.
      const pickable = (sec.fields || []).filter((f) => f.editable !== false);
      (op === "random_date" ? pickable.filter((f) => f.date) : pickable)
        .forEach((f) => fieldSel.appendChild(
          new Option(`${f.name} (${f.size != null ? f.size : "?"})`, f.name)));
      row2.appendChild(el("span", "bulk-label", "Field:"));
      row2.appendChild(fieldSel);
      // Bound to the control itself, not delegated from the row: every branch
      // below adds its own `change` listener, and the note must refresh on all
      // of them.
      fieldSel.addEventListener("change", updateRollupNote);

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
      } else if (op === "list") {
        const listInp = el("input", "bulk-value bulk-list");
        listInp.type = "text";
        listInp.placeholder = "e.g. 10, 20, 30  ('  msg' keeps spaces · ' ' = blank)";
        listInp.style.width = "320px";
        row2.appendChild(el("span", "bulk-label", "Allowed values:"));
        row2.appendChild(listInp);
        row2.appendChild(el("span", "bulk-section",
          `· comma separated — each ${sec.isHeader ? "file" : "row"} gets one at random; use ' ' for a blank value`));
        listInp.addEventListener("input", reset);
        fieldSel.addEventListener("change", reset);
      } else if (op === "random_date") {
        const from = el("input", "bulk-value bulk-dfrom"); from.type = "text";
        from.placeholder = "2024-01-01"; from.style.width = "150px";
        const to = el("input", "bulk-value bulk-dto"); to.type = "text";
        to.placeholder = "2024-12-31"; to.style.width = "150px";
        row2.appendChild(el("span", "bulk-label", "Between:"));
        row2.appendChild(from);
        row2.appendChild(el("span", "bulk-section", "and"));
        row2.appendChild(to);
        row2.appendChild(el("span", "bulk-section",
          `· each ${sec.isHeader ? "file" : "row"} gets its own random moment — ` +
          `a date, or '2024-01-01 14:30'`));
        [from, to].forEach((i) => i.addEventListener("input", reset));
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
    updateRollupNote();
    reset();
  }

  function rebuildOps() {
    const sec = curSection();
    opSel.innerHTML = "";
    opsForSection(sec).forEach((o) => opSel.appendChild(new Option(o.t, o.v)));
    // Follows the SECTION dropdown, so switching sections re-answers it.
    // Shown here as well as in the field-values panel because a field VALUE op
    // (set / list / random) lives in both, and it is the value ops that get
    // skipped — the row ops right beside them are the remedy and still work.
    noteBox.innerHTML = "";
    const msg = noDataNote(sec);
    if (msg) noteBox.appendChild(el("div", "bulk-nodata-note", msg));
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
    if (op === "random_date") {
      return { type: "random_date", field: fieldSel.value,
               from: (row2.querySelector(".bulk-dfrom") || {}).value || "",
               to: (row2.querySelector(".bulk-dto") || {}).value || "" };
    }
    if (op === "list") {
      // Send the raw string, NOT a pre-split/trimmed array — the server's
      // _clean_values splits on comma, trims bare entries, and unwraps quoted
      // entries so significant spaces survive ('   msg01' keeps its spaces).
      const raw = (row2.querySelector(".bulk-list") || {}).value || "";
      return { type: "list", field: fieldSel.value, values: raw };
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
    if (op.type === "list") {
      // op.values is the raw string now — split just for the human summary.
      const vals = String(op.values || "").split(",").map((v) => v.trim()).filter((v) => v !== "");
      return `${sec}: set ${op.field} randomly from ${vals.length} value(s)`
             + (vals.length ? ` [${vals.slice(0, 6).join(", ")}` +
                (vals.length > 6 ? ", …]" : "]") : "");
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

// Status -> the word a user reads. Anything not listed falls through to the
// token itself, so a NEW status is merely terse rather than invisible.
const BULK_STATUS_WORDS = {
  changed: "changed", change: "will change", unchanged: "unchanged",
  no_section: "skipped", no_data: "skipped", missing_field: "no such field",
  too_wide: "too long", error: "error",
};

function renderBulkTable(host, results, applied) {
  host.innerHTML = "";
  const counts = {};
  results.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });
  // The summary reads in the same words as the rows — it used to count raw
  // tokens ("1 no_section"), which is the same defect one line up.
  const summary = Object.entries(counts)
    .map(([k, v]) => `${v} ${BULK_STATUS_WORDS[k] || k}`).join("  ·  ");
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
    // A STATUS is a value the code branches on; the user reads a word. The
    // raw token used to be printed here — `no_section` with an empty Change
    // column said nothing about what had happened or what to do. The
    // explanation is the server's `detail`, shown beside it.
    tr.appendChild(el("td", null, BULK_STATUS_WORDS[r.status] || r.status)
                   ).title = r.detail || r.error || "";
    if (r.error) tr.children[2].textContent += `: ${r.error}`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody); host.appendChild(table);
}

// ---- Bulk Rename ----
async function enterRenameMode() {
  if (!state.selection.size) return;
  if (!confirmDiscardIfDirty()) return;
  closeAllPanels("rename");      // only one bulk mode open at a time
  state.file = null; state.view = null; state.edits = {}; state.ops = []; state.normalized = 0;
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
  restoreEditorAfterPanel();
}

function jsBuildName(parts, sample, sep, ext) {
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
  // Keep the file's own extension — a Calgary file stays .json. The server
  // does the same when it applies the rename; this is only the preview.
  return out + (ext || ".OK");
}

// A path's extension, defaulting to .OK for the fixed-width/delimited layouts.
function fileExt(path) {
  const m = /(\.[A-Za-z0-9]+)$/.exec(String(path || ""));
  return m ? m[1] : ".OK";
}

function renderRenamePanel(scope) {
  const panel = $("#renamePanel");
  panel.innerHTML = "";
  const head = el("div", "bulk-head");
  head.appendChild(el("h3", null, `Bulk Rename — ${scope.files.length} ${scope.files.length === 1 ? "file" : "files"}`));
  const close = el("button", "btn", "✕ Close");
  close.addEventListener("click", exitRenameMode);
  head.appendChild(close);
  panel.appendChild(head);
  if (!scope.files.length) { panel.appendChild(el("div", "bulk-note", "No files.")); return; }

  const hasPresets = (scope.presets || []).length > 0;

  // 1) Choose a pattern (the main path for most users)
  const presetRow = el("div", "bulk-edit-row");
  presetRow.appendChild(el("span", "bulk-label", "Choose a pattern:"));
  const presetSel = el("select", "bulk-field");
  presetSel.appendChild(new Option(hasPresets ? "— choose a pattern —" : "— no saved patterns —", ""));
  (scope.presets || []).forEach((p, i) => presetSel.appendChild(new Option(p.name, String(i))));
  presetSel.disabled = !hasPresets;
  presetSel.addEventListener("change", () => {
    if (presetSel.value === "") return;
    applyPreset(scope.presets[Number(presetSel.value)]);
  });
  presetRow.appendChild(presetSel);
  panel.appendChild(presetRow);

  // 2) Live example + Preview/Apply — what casual users actually use
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

  // 3) Advanced — the parts builder, tucked away (collapsed by default)
  const advToggle = el("button", "rn-adv-toggle", "▸ Customize parts (advanced)");
  panel.appendChild(advToggle);
  const adv = el("div", "rn-advanced hidden");
  panel.appendChild(adv);
  advToggle.addEventListener("click", () => {
    const hidden = adv.classList.toggle("hidden");
    advToggle.textContent = (hidden ? "▸" : "▾") + " Customize parts (advanced)";
  });

  const sepRow = el("div", "bulk-edit-row");
  sepRow.appendChild(el("span", "bulk-label", "Separator:"));
  const sepSel = el("select", "bulk-field");
  [["_", "_ underscore"], ["-", "- dash"], [".", ". dot"], ["", "(none)"], ["__custom__", "custom…"]]
    .forEach(([v, t]) => sepSel.appendChild(new Option(t, v)));
  const sepCustom = el("input", "bulk-value"); sepCustom.style.width = "60px"; sepCustom.placeholder = "sep"; sepCustom.classList.add("hidden");
  sepSel.addEventListener("change", () => { sepCustom.classList.toggle("hidden", sepSel.value !== "__custom__"); updateLive(); });
  sepCustom.addEventListener("input", updateLive);
  sepRow.appendChild(sepSel); sepRow.appendChild(sepCustom);
  adv.appendChild(sepRow);

  const partsBox = el("div", "rn-parts");
  adv.appendChild(partsBox);
  const addRow = el("div", "bulk-actions");
  const addBtn = el("button", "btn", "＋ Add part");
  addRow.appendChild(addBtn);
  adv.appendChild(addRow);

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
    if (!parts.length) {
      live.textContent = hasPresets
        ? "Choose a pattern above — or expand “Customize parts” to build one."
        : "Expand “Customize parts” and add parts to build a name.";
      previewBtn.disabled = true;
    } else {
      live.textContent = "Example (file 1):  " +
        jsBuildName(parts, scope.sample, sep(),
                    fileExt(scope.files[0] && scope.files[0].path));
      previewBtn.disabled = false;
    }
    previewBox.innerHTML = ""; resultsBox.innerHTML = ""; applyBtn.disabled = true;
  }
  addBtn.addEventListener("click", () => addPartRow());

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

  // No saved patterns? open the builder so they can start. Otherwise keep it
  // tucked away — most users just pick a pattern, review, preview, apply.
  if (!hasPresets) {
    adv.classList.remove("hidden");
    advToggle.textContent = "▾ Customize parts (advanced)";
    addPartRow();
  }
  updateLive();
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
  closeAllPanels();   // clicking a folder leaves any bulk mode too
  setSelection([]);   // clicking a folder clears any file multi-selection
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

// Human summary of what auto-clean removed on open (blank lines / trimmed
// trailing spaces). Both counts are junk only — no field is ever touched.
function describeCleanup(blanks, trimmed) {
  const parts = [];
  if (blanks) parts.push(`removed ${blanks} blank junk line${blanks > 1 ? "s" : ""}`);
  if (trimmed) parts.push(`trimmed trailing spaces on ${trimmed} line${trimmed > 1 ? "s" : ""}`);
  return parts.join(" and ") || "cleaned junk";
}

// ---- editor ----
async function loadFile(path) {
  try {
    // Opening a file always leaves bulk mode — otherwise the finished bulk
    // screen stays parked under the editor.
    closeAllPanels();
    const view = await getParse(path);
    state.file = path;
    state.view = view;
    state.edits = {};
    state.ops = [];   // staged rows belong to the file we just left
    const blanks = view.blank_lines_removed || 0, trimmed = view.lines_space_trimmed || 0;
    state.normalized = blanks + trimmed;
    state.cleanupDesc = describeCleanup(blanks, trimmed);
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    updateTreeBadge(path, view.chain_info, view.chain);  // reflect chain edits in the tree
    if (state.normalized) {
      const desc = state.cleanupDesc;
      if (autoCleanEnabled()) {
        // Persist the cleanup automatically — junk-only (the /api/clean path
        // writes okf.to_bytes(), which is exactly the file minus blank lines and
        // post-terminator padding — no field is touched). Locked/read-only files
        // fall back to the manual Save path instead of erroring.
        try {
          const res = await postJSON("/api/clean/bulk", { paths: [path] });
          const r = (res.results || [])[0] || {};
          if (r.status === "error") {
            setStatus(`Cleaned this file (${desc}), but couldn't save automatically ` +
                      `(the file may be open in another program); click Save to keep`, "dirty");
          } else {
            state.normalized = 0;              // now clean on disk
            updateSaveButtons();               // greys out Save, clears the raw note
            setStatus(`Cleaned and saved — ${desc}`, "ok");
          }
        } catch (e) {
          setStatus(`Cleaned this file (${desc}) — auto-save failed (${e.message}); ` +
                    `click Save to keep`, "dirty");
        }
      } else {
        setStatus(`Loaded — ${desc}; Save to keep`, "dirty");
      }
    } else {
      setStatus(view.roundtrip_ok ? "Loaded (round-trip OK)" : "Loaded (round-trip DIFFERS!)",
                view.roundtrip_ok ? "ok" : "err");
    }
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
  // Badge the roll-up total from the file AS OPENED — a mismatch is shown, not
  // silently corrected: opening a file never writes (the fix lands on save).
  refreshRollup(false);
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
  const fieldEdits = Object.keys(state.edits).length;
  const ops = pendingOps().length;
  if (fieldEdits) {
    // Staged row ops ARE rendered here (the preview is built from them), but
    // field edits typed since the last one are still only in the form.
    banner.textContent = ops
      ? "⚠ Includes unsaved row changes, but not the field edits you just typed. Save to refresh this view."
      : "⚠ Showing the last saved file — you have unsaved edits. Save to refresh this view.";
    banner.className = "raw-banner warn";
  } else if (ops) {
    banner.textContent = "⚠ Preview of unsaved row changes — nothing has been written to disk yet.";
    banner.className = "raw-banner warn";
  } else if (state.normalized) {
    banner.textContent = `🧹 Cleaned on open (${state.cleanupDesc || "removed junk"}) — Save to keep (nothing written yet).`;
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
  wrap.appendChild(head);

  const body = el("div", "section-body");
  // Header = form (single record); detail sections = table with per-row controls
  // (always a table, so even a 1-row section can grow via the row's ＋).
  if (sec.is_header) {
    body.appendChild(renderForm(sec));
  } else {
    body.appendChild(renderTable(sec));
  }
  wrap.appendChild(body);
  return wrap;
}

async function addRowAfter(recordIndex) {
  if (!state.file) return;
  if (!beginBusy("Adding row…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const journal = journalWith({ type: "add", after_index: recordIndex });
    const view = await postJSON("/api/record/add", {
      path: state.file,
      after_index: recordIndex,
      ops: pendingOps(),
      edits: collectEdits(),
      preview: true,
    });
    commitJournal(journal);
    state.view = view;
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("Row added — copied below (unsaved)", "dirty");
  } catch (e) {
    setStatus("Add failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

// Seed the first row into an empty section (there's no existing row to copy
// from, so the server seeds it from the section's reference sample line).
async function addRowToSection(sectionIndex) {
  if (!state.file) return;
  if (!beginBusy("Adding row…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const journal = journalWith({ type: "add", section_index: sectionIndex });
    const view = await postJSON("/api/record/add", {
      path: state.file,
      section_index: sectionIndex,
      ops: pendingOps(),
      edits: collectEdits(),
      preview: true,
    });
    commitJournal(journal);
    state.view = view;
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("First row added to section (unsaved)", "dirty");
  } catch (e) {
    setStatus("Add failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

function editKey(s, r, f) { return `${s}|${r}|${f}`; }

// ----- derived (computed) fields: mirror config/derived_fields.yaml logic ---
function derivedCondMatch(v, cond) {
  const t = String(v == null ? "" : v).trim();
  if (cond && typeof cond === "object") {
    if ("eq" in cond) return t === String(cond.eq).trim();
    if ("neq" in cond) return t !== String(cond.neq).trim();
    if ("in" in cond) return (cond.in || []).map((x) => String(x).trim()).includes(t);
    if ("nin" in cond) return !(cond.nin || []).map((x) => String(x).trim()).includes(t);
    return false;
  }
  return t === String(cond).trim();
}

function evalDerived(spec, getVal) {
  for (const rule of spec.rules || []) {
    const when = rule.when || {};
    if (Object.keys(when).every((f) => derivedCondMatch(getVal(f), when[f]))) return rule.value;
  }
  return spec.default || "";
}

// Recompute every derived field in a section/record from the live control
// values (falling back to parsed values for unrendered inputs).
function refreshDerived(si, ri) {
  if (!state.view) return;
  const sec = state.view.sections[Number(si)];
  if (!sec) return;
  const derived = sec.fields.filter((f) => f.derived);
  if (!derived.length) return;
  const host = $("#editor");
  const getVal = (f) => {
    const ctrl = host.querySelector(
      `.fval[data-section="${si}"][data-record="${ri}"][data-field="${CSS.escape(f)}"]`);
    if (ctrl) return ctrl.value;
    const rec = sec.records.find((r) => String(r.index) === String(ri));
    return rec ? (rec.values[f] != null ? rec.values[f] : "") : "";
  };
  derived.forEach((spec) => {
    const span = host.querySelector(
      `.fval-ro[data-derived="${CSS.escape(spec.name)}"][data-section="${si}"][data-record="${ri}"]`);
    if (!span) return;
    const val = evalDerived(spec, getVal);
    span.textContent = val;
    span.title = val;
  });
}

// ----- roll-up totals: mirror config/rollup_fields.yaml (server is the truth) -
// The header total must equal the sum of a detail section's rows. The control
// is NEVER locked (D51/D56: a lock is a UI hint the save path doesn't check) —
// instead the total follows the rows live, and a value the user types that
// disagrees is badged with what the save will do. When the section has no rows
// the total IS the quantity, and the badge says so rather than warning.
function rollupSpec() {
  const rs = (state.view && state.view.rollups) || [];
  return rs.length ? rs[0] : null;
}

// ---- shared roll-up wording for the BULK paths --------------------------- //
// Bulk Edit and Volume Generate meet the same rule at a different moment: the
// value is typed once and applied to a whole selection, so the warning has to
// come up front. Both read the spec the server sends with their scope — the
// client must never assume which field is a roll-up.

// The spec for `field` on `layout`. `rollups` is Bulk's {layout: [spec]} map or
// Generate's plain [spec] list for its single layout.
function rollupSpecFor(rollups, layout, field) {
  if (!rollups || !field) return null;
  const list = Array.isArray(rollups) ? rollups : (rollups[layout] || []);
  return list.find((s) => s.field === field) || null;
}

// One sentence, every word from config so a future roll-up words itself: the
// rule, then the control that actually changes the value.
function rollupWarning(spec) {
  const lines = `${String(spec.section).toLowerCase()} lines`;
  return `⚠ ${spec.field} is the sum of the ${lines}. Only files with no `
    + `${lines} take this value — for the rest, set ${spec.section} › `
    + `${spec.source} instead.`;
}

function rollupHeaderField() {
  const spec = rollupSpec();
  if (!spec || !state.view) return null;
  const sec = state.view.sections[0];
  if (!sec) return null;
  const field = sec.fields.find((f) => f.name === spec.field);
  return field ? { sec: sec, rec: sec.records[0], field: field, spec: spec } : null;
}

// Live value of a control, falling back to the parsed record for a field whose
// input isn't rendered (a collapsed tab renders no DOM).
function liveValue(si, ri, name, rec) {
  const c = $("#editor").querySelector(
    `.fval[data-section="${si}"][data-record="${ri}"][data-field="${CSS.escape(name)}"]`);
  if (c) return c.value;
  return rec && rec.values[name] != null ? rec.values[name] : "";
}

function rowIsBlank(sec, rec) {
  return sec.fields.every((f) => {
    if (f.hidden) return true;
    const v = String(liveValue(sec.index, rec.index, f.name, rec) || "");
    return v.replace(/[0 ]/g, "") === "";
  });
}

// {rows, total, expected, current, matches, authoritative, overflow} — computed
// from what is on screen RIGHT NOW, so it tracks typing before any save.
function rollupLive() {
  const h = rollupHeaderField();
  if (!h) return null;
  const src = state.view.sections.find((s) => s.name === h.spec.section);
  const current = liveValue(h.sec.index, h.rec.index, h.field.name, h.rec);
  const out = { rows: 0, total: 0, current: current, expected: null,
                matches: true, authoritative: true, overflow: false,
                bad: null, size: h.field.size || 0, name: h.field.name,
                section: h.spec.section };
  if (!src) return out;
  const rows = (src.records || []).filter((r) => !rowIsBlank(src, r));
  out.rows = rows.length;
  out.authoritative = rows.length === 0;
  if (!rows.length) return out;
  let total = 0;
  for (const r of rows) {
    const raw = String(liveValue(src.index, r.index, h.spec.source, r) || "").trim();
    if (raw === "") continue;
    if (!/^\d+$/.test(raw)) { out.bad = raw; out.matches = false; return out; }
    total += parseInt(raw, 10);
  }
  out.total = total;
  out.overflow = String(total).length > out.size;
  out.expected = String(total).padStart(out.size, "0");
  out.matches = current === out.expected;
  return out;
}

// Paint the badge, and push the new total into the input when the ROWS moved.
// Typing in the total itself never has its own keystrokes overwritten — it is
// badged with what the save will do instead.
function refreshRollup(fromRows) {
  const h = rollupHeaderField();
  if (!h) return;
  const badge = $("#editor").querySelector(
    `.rollup-badge[data-rollup="${CSS.escape(h.field.name)}"]`);
  const live = rollupLive();
  if (!live) return;
  if (fromRows && !live.authoritative && !live.overflow && !live.bad
      && live.expected != null) {
    const c = $("#editor").querySelector(
      `.fval[data-section="${h.sec.index}"][data-record="${h.rec.index}"]`
      + `[data-field="${CSS.escape(h.field.name)}"]`);
    if (c && c.value !== live.expected) {
      c.value = live.expected;
      const key = editKey(c.dataset.section, c.dataset.record, c.dataset.field);
      if (c.value !== c.dataset.orig) { state.edits[key] = c.value; c.classList.add("dirty"); }
      else { delete state.edits[key]; c.classList.remove("dirty"); }
      updateSaveButtons();
    }
    live.current = live.expected;
    live.matches = true;
  }
  if (!badge) return;
  badge.classList.remove("rollup-ok", "rollup-warn", "rollup-info");
  const n = live.rows;
  const lines = `${n} ${live.section.toLowerCase()} line${n === 1 ? "" : "s"}`;
  if (live.bad != null) {
    badge.classList.add("rollup-warn");
    badge.textContent = "⚠ a row quantity is not a number";
    badge.title = `"${live.bad}" cannot be totalled — the save will refuse it`;
  } else if (live.authoritative) {
    badge.classList.add("rollup-info");
    badge.textContent = `ⓘ no ${live.section.toLowerCase()} lines — this is the quantity`;
    badge.title = "With no detail rows this total is not a sum: it is the "
      + "quantity itself, and is saved exactly as you type it.";
  } else if (live.overflow) {
    badge.classList.add("rollup-warn");
    badge.textContent = `⚠ ${live.total} needs ${String(live.total).length} digits`;
    badge.title = `${live.name} holds ${live.size} — the save will refuse this `
      + "rather than write a truncated total.";
  } else if (live.matches) {
    badge.classList.add("rollup-ok");
    badge.textContent = `= sum of ${lines}`;
    badge.title = "This total matches its rows.";
  } else {
    badge.classList.add("rollup-warn");
    // Say WHERE the value comes from and how to change it — the rule (rows
    // drive the total) was only ever in the hover tooltip, which nobody hovers,
    // so "it is not taking my value" read as a bug rather than a rule. One
    // wording for both flavours of mismatch (typed by the user, or the file's
    // own): naming the typed number back would only restate what is on screen.
    const secLines = `${live.section.toLowerCase()} lines`;
    badge.textContent = `⚠ will be set to the sum of the ${secLines} `
      + `(${live.expected}) on save — to change ${live.name}, edit the ${secLines}`;
    badge.title = `The ${lines} total ${live.total}. This field always follows `
      + "them, so change the row quantities to change the total.";
  }
}

function optionLabel(label, code) {
  if (code === "") return label;            // the explicit blank choice
  return label === code ? code : `${label} (${code})`;
}

// The first entry of a FREEFORM field's dropdown. It is not a value — choosing
// it empties the box so something outside the list can be typed. These lists
// are suggestions, not the whole truth (D56/D57), and nothing in a plain text
// box says so; a user who saw only known values reasonably read the list as
// exhaustive. Kept as one constant because the control writes it, the clearing
// handler compares against it, and the tests assert it.
const FREEFORM_HINT = "---- or type value ----";

function makeControl(sec, rec, field) {
  const value = (rec.values[field.name] != null) ? rec.values[field.name] : "";
  // Read-only field: show a static label (coded values use their friendly
  // label) with no input, so it can never be edited or collected as an edit.
  if (field.editable === false) {
    const shown = (field.options && field.options[value] != null) ? field.options[value] : value;
    const ro = el("span", "cell fval fval-ro");
    ro.textContent = shown;
    ro.title = value;
    if (field.derived) {   // recompute target: tag so edits to inputs can update it
      ro.dataset.section = sec.index;
      ro.dataset.record = rec.index;
      ro.dataset.derived = field.name;
    }
    return ro;
  }
  let ctrl;
  let orig = value;
  if (field.options && field.freeform) {
    // TYPE IT or CHOOSE IT — in ONE control, built here rather than delegated
    // to <datalist>. The list is a set of SUGGESTIONS, not the whole truth:
    // `type` needs another capitalisation of its one word, and `chain` needs
    // `homesense` as readily as `HomeSense` or `06`. What may actually be SAVED
    // is unchanged and decided server-side — _assert_layout_stable for `type`,
    // can_change_chain for `chain`, which still refuses Europe on NA layouts.
    //
    // WHY NOT <datalist>, having twice tried it. A datalist filters its options
    // by what is already in the box, by PREFIX — so on a populated field (which
    // these always are) the list collapses to the current value and the user
    // sees no choices at all. User-reported on Safari, but it is not a Safari
    // bug: Chrome and Firefox additionally show nothing until the user types or
    // deletes. Safari compounds it by rendering only an option's VALUE and
    // never its label. So the browsers cannot show "here is every value you may
    // pick" for a field that already holds one, which is the entire job.
    //
    // The `pick…` <select> that used to sit beside the box did do that job —
    // a <select> shows every option regardless of the field's value — which is
    // why removing it as "redundant" was wrong: the box handled TYPING and the
    // picker handled BROWSING. This control does both, in one place.
    ctrl = el("input", "cell fval");
    ctrl.type = "text";
    orig = value;
    ctrl.value = orig;
    if (field.size != null) ctrl.maxLength = field.size;
    // Ours is the only list: the native one would sit on top of it.
    ctrl.setAttribute("autocomplete", "off");
    ctrl.setAttribute("role", "combobox");
    ctrl.setAttribute("aria-expanded", "false");
    ctrl.title = "Type any value, or click ▾ for the full list. "
               + `These are suggestions, not the only allowed values — "${FREEFORM_HINT}" `
               + "clears the box so you can type your own. "
               + "A value this layout does not allow is refused on save.";

    const menu = el("div", "fval-menu hidden");
    const rows = [];
    let active = -1;

    // The menu hangs off <body>, not off the field, and is only there while
    // OPEN. A detail-line control lives inside `.rec-table`, which is
    // `overflow: auto` — and an overflow container CLIPS absolutely-positioned
    // descendants on both axes, so a menu anchored in the cell would be cut off
    // wherever it extended past the table's box (worst on the last rows, which
    // is exactly where a dropdown opens downward). `position: fixed` on <body>
    // is outside every scroller. Removing it on close rather than leaving it
    // parked is what stops one accumulating per rendered row.
    const detach = () => {
      if (menu.parentNode && menu.parentNode.removeChild) {
        menu.parentNode.removeChild(menu);
      }
    };
    const closeMenu = () => {
      menu.classList.add("hidden");
      detach();
      ctrl.setAttribute("aria-expanded", "false");
      active = -1;
      rows.forEach((r) => r.row.classList.remove("active"));
    };
    // Fixed coordinates are read from the box itself, so the menu tracks the
    // field wherever it is on screen. Flips ABOVE when there is not enough room
    // below — a field near the bottom of the window would otherwise open into
    // nothing.
    const placeMenu = () => {
      if (!ctrl.getBoundingClientRect) return;
      const r = ctrl.getBoundingClientRect();
      const vh = (typeof window !== "undefined" && window.innerHeight) || 0;
      menu.style.left = r.left + "px";
      menu.style.minWidth = r.width + "px";
      const below = vh - r.bottom;
      if (vh && below < 160 && r.top > below) {
        menu.style.top = "";
        menu.style.bottom = (vh - r.top) + "px";
      } else {
        menu.style.bottom = "";
        menu.style.top = r.bottom + "px";
      }
    };
    const commit = (v) => {
      ctrl.value = v;
      closeMenu();
      onEdit({ target: ctrl });          // same path a keystroke takes
      if (ctrl.okgenForm) ctrl.okgenForm(v);
      try { ctrl.focus(); } catch (_) {}
    };
    const addRow = (text, v, kind, extra) => {
      const row = el("div", "fval-opt" + (extra ? " " + extra : ""));
      row.appendChild(el("span", "fval-opt-text", text));
      // The code/name tag, in the list as well as beside the box — picking is
      // exactly when you want to know which FORM you are about to store.
      if (kind) row.appendChild(el("span", "form-badge form-badge-" + kind, kind));
      row.dataset.value = v;
      // mousedown, NOT click: a click on the menu fires after the input's blur,
      // and blur closes the menu — so the choice would never register.
      row.addEventListener("mousedown", (e) => {
        if (e && e.preventDefault) e.preventDefault();
        commit(v);
      });
      menu.appendChild(row);
      rows.push({ row, value: v, text });
    };

    // First, and never filtered out: it is the escape hatch, so hiding it when
    // nothing matches would remove it exactly when it is most wanted. Its value
    // is empty, so choosing it clears the box — which IS "let me type my own".
    addRow(FREEFORM_HINT, "", null, "fval-opt-hint");
    Object.keys(field.options).forEach((code) => {
      const label = field.options[code];
      const kind = field.value_forms ? field.value_forms[code] : null;
      // "01 — TJMAXX" for a coded value, but plain "Winners" where the label IS
      // the value ("Winners — Winners" reads as two different things).
      addRow(label && label !== code ? `${code} — ${label}` : code, code, kind);
    });

    const applyFilter = (needle) => {
      const q = String(needle || "").toLowerCase();
      rows.forEach((r) => {
        const hit = !q || r.value === "" || r.text.toLowerCase().indexOf(q) >= 0;
        if (hit) r.row.classList.remove("hidden");
        else r.row.classList.add("hidden");
      });
    };
    const openMenu = (filtered) => {
      applyFilter(filtered ? ctrl.value : "");   // ▾ always shows EVERYTHING
      if (document.body && menu.parentNode !== document.body) {
        document.body.appendChild(menu);
      }
      menu.classList.remove("hidden");
      placeMenu();
      ctrl.setAttribute("aria-expanded", "true");
    };

    const arrow = el("button", "fval-arrow", "▾");
    arrow.type = "button";
    arrow.tabIndex = -1;                  // a mouse affordance; the box is the control
    arrow.title = "Show all values";
    arrow.addEventListener("mousedown", (e) => {
      if (e && e.preventDefault) e.preventDefault();
      if (menu.classList.contains("hidden")) openMenu(false);
      else closeMenu();
    });

    // Registered BEFORE the badge painter and before makeControl's own onEdit
    // wiring, so both of those see the cleared value, never the hint text.
    ctrl.addEventListener("input", () => {
      if (ctrl.value === FREEFORM_HINT) ctrl.value = "";
      openMenu(true);
    });
    ctrl.addEventListener("focus", () => openMenu(false));
    ctrl.addEventListener("blur", () => closeMenu());
    ctrl.addEventListener("keydown", (e) => {
      const key = e && e.key;
      if (key === "Escape") { closeMenu(); return; }
      const vis = rows.filter((r) => !r.row.classList.contains("hidden"));
      if (key === "ArrowDown" || key === "ArrowUp") {
        if (e.preventDefault) e.preventDefault();
        if (menu.classList.contains("hidden")) { openMenu(false); return; }
        if (!vis.length) return;
        const cur = vis.findIndex((r) => r.row.classList.contains("active"));
        const next = key === "ArrowDown"
          ? (cur + 1) % vis.length
          : (cur <= 0 ? vis.length - 1 : cur - 1);
        rows.forEach((r) => r.row.classList.remove("active"));
        vis[next].row.classList.add("active");
        active = next;
        return;
      }
      if (key === "Enter") {
        const hit = vis.filter((r) => r.row.classList.contains("active"))[0];
        if (hit) { if (e.preventDefault) e.preventDefault(); commit(hit.value); }
      }
    });

    // Which FORM the value is in — a brand name or a chain code. Both are
    // valid and a file may carry either (D41/D57), so the editor says which
    // one is on disk rather than leaving the user to infer it from the text.
    if (field.value_forms) {
      const badge = el("span", "form-badge");
      const formOf = (v) => {
        if (v == null || v === "") return "";
        const keys = Object.keys(field.value_forms);
        const hit = keys.find((k) => k === v)
                 || keys.find((k) => k.toLowerCase() === String(v).toLowerCase());
        return hit ? field.value_forms[hit] : "unknown";
      };
      const paint = (v) => {
        const kind = formOf(v);
        badge.textContent = kind;
        badge.className = "form-badge" + (kind ? " form-badge-" + kind : "");
        badge.title = kind === "code"
          ? "This file stores the chain as a CODE. A brand name is equally valid here."
          : kind === "name"
            ? "This file stores the chain as a brand NAME. A code is equally valid here."
            : kind === "unknown"
              ? "Not a chain this layout knows — it will be refused on save."
              : "";
      };
      paint(value);
      ctrl.okgenForm = paint;
      ctrl.addEventListener("input", () => paint(ctrl.value));
      ctrl.okgenBadge = badge;
    }
    // The box, its arrow and its menu travel as ONE positioned unit. Without a
    // wrapper the menu would anchor to `.field`, which is a flex COLUMN holding
    // the label above and the badge below — so the arrow would sit halfway up
    // the field rather than inside the box, and the menu would hang below the
    // badge. The <input> stays the element carrying the data-* attributes, so
    // every `.fval[data-section=…]` lookup still finds it through the wrapper.
    const wrap = el("div", "fval-wrap");
    wrap.appendChild(ctrl);
    wrap.appendChild(arrow);      // the menu is attached to <body> when opened
    ctrl.okgenWrap = wrap;
    ctrl.okgenMenu = menu;
    ctrl.okgenArrow = arrow;
  } else if (field.options) {
    ctrl = el("select", "cell fval");
    const codes = Object.keys(field.options);
    if (!codes.includes(value)) {
      ctrl.appendChild(new Option(value + " (current)", value));
    }
    // "TJMAXX (01)" for a coded value, but just "Winners" when the label IS
    // the stored value (Calgary chains and `type` map each value to itself) —
    // "Winners (Winners)" reads like two different things.
    codes.forEach((code) => ctrl.appendChild(new Option(optionLabel(field.options[code], code), code)));
    ctrl.value = value;
  } else {
    ctrl = el("input", "cell fval");
    ctrl.type = "text";
    // Fixed-width fields arrive padded with spaces (left- or right-justified),
    // which fills the input to its maxLength — so the field looks empty with
    // the cursor floating mid-field and there's no room to type. Strip the pad
    // spaces for editing; the server re-pads to the field width on save
    // (okfile._fit), and untouched fields are never re-sent, so the file still
    // round-trips byte-for-byte.
    //
    // EXCEPT literal fields (messages, facts, descriptions…), where LEADING and
    // middle spaces are the user's data and must survive. Only the TRAILING pad
    // is stripped for display: the field is fixed width, so the server re-pads
    // the tail on save (Record.set literal -> ljust), which reproduces exactly
    // what was stored — an untouched field is never re-sent, so it stays
    // byte-exact. Keeping the trailing pad here instead would fill the input to
    // maxLength and make it impossible to TYPE a leading space.
    if (field.literal) {
      orig = value.replace(/ +$/, "");   // drop trailing pad only; keep leading/middle
      ctrl.value = orig;
      ctrl.classList.add("fval-literal");
      ctrl.title = "Saved exactly as typed — leading and middle spaces are "
                 + "kept, the tail is space-filled to width, nothing is zero-padded.";
      ctrl.addEventListener("focus", () => {
        const end = ctrl.value.length;
        try { ctrl.setSelectionRange(end, end); } catch (_) {}
      });
    } else {
      orig = value.replace(/^ +| +$/g, "");
      ctrl.value = orig;
    }
    if (field.size != null) ctrl.maxLength = field.size;
  }
  ctrl.dataset.section = sec.index;
  ctrl.dataset.record = rec.index;
  ctrl.dataset.field = field.name;
  ctrl.dataset.orig = orig;
  ctrl.addEventListener("input", onEdit);
  ctrl.addEventListener("change", onEdit);
  return ctrl;
}

// A freeform menu lives on <body> while open (see makeControl). Anything that
// REPLACES the editor — loading another file, switching section, adding a row —
// destroys the box the menu belongs to without the blur that would close it, so
// the menu would be orphaned on <body> and outlive its own field. Swept before
// each render rather than tracked, because the sweep is correct however the
// menu got there.
function closeStrayFieldMenus() {
  const body = document.body;
  if (!body || !body.querySelectorAll) return;
  Array.prototype.slice.call(body.querySelectorAll(".fval-menu"))
    .forEach((m) => { if (m.parentNode) m.parentNode.removeChild(m); });
}

function renderForm(sec) {
  closeStrayFieldMenus();
  const grid = el("div", "form-grid");
  const rec = sec.records[0];
  const keyField = state.view && state.view.key_field;
  const colors = window.OKGEN_FIELD_COLORS || {};
  sec.fields.forEach((field) => {
    if (field.hidden) return;   // structural/marker field — never shown
    const isKey = field.name === keyField;
    const f = el("div", "field" + (isKey ? " field-key" : ""));
    const color = colors[field.name];
    const label = el("label", "field-label" + (!color && field.options ? " field-coded" : ""));
    label.textContent = field.derived
      ? `${field.name}  ·  derived`
      : `${field.name}  ·  ${field.size != null ? field.size : "?"}ch`;
    if (color) { label.style.color = color; label.style.fontWeight = "700"; }  // configured field color
    if (isKey) {
      label.appendChild(el("span", "key-tag", "🔑 unique"));
      // For Calgary JSON the key field depends on where the file came from, so
      // say which source was used and how it was decided — the passive
      // confirmation that this really is the field Make Unique will renumber.
      const js = state.view && state.view.json_source;
      if (js) {
        const tag = el("span", "src-tag", js.source);
        tag.title = `source: ${js.source} (${js.reason})` +
          (js.resolved ? "" : " — could not read this file's headerASNid, so "
                            + "the configured default was assumed");
        if (!js.resolved) tag.classList.add("src-tag-assumed");
        label.appendChild(tag);
      }
    }
    f.appendChild(label);
    const ctl = makeControl(sec, rec, field);
    // A freeform field arrives wrapped with its arrow and menu (see
    // makeControl); everything else is the bare control.
    f.appendChild(ctl.okgenWrap || ctl);
    if (ctl.okgenBadge) f.appendChild(ctl.okgenBadge);
    // Roll-up total: a sibling badge saying whether this agrees with the rows
    // it sums, or — when there are none — that it is the quantity itself.
    const rspec = rollupSpec();
    if (rspec && field.name === rspec.field) {
      const badge = el("span", "rollup-badge");
      badge.dataset.rollup = field.name;
      f.appendChild(badge);
    }
    grid.appendChild(f);
  });
  return grid;
}

function renderTable(sec) {
  closeStrayFieldMenus();
  const box = el("div", "rec-table");
  const table = el("table");
  const thead = el("thead");
  const htr = el("tr");
  htr.appendChild(el("th", null, "#"));
  sec.fields.forEach((f) => {
    if (f.hidden) return;   // structural/marker field — never shown
    htr.appendChild(el("th", null, `${f.name} (${f.size != null ? f.size : "?"})`));
  });
  htr.appendChild(el("th", null, ""));   // row actions column
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = el("tbody");
  const n = sec.records.length;
  if (n === 0) {
    // Empty section: show a single "None" row spanning every column so the
    // section is still visible (and stays in place) instead of vanishing —
    // with an Add button so the user can start populating it.
    const shown = sec.fields.filter((f) => !f.hidden).length;
    const tr = el("tr", "rec-empty");
    const td = el("td");
    td.colSpan = shown + 2;   // "#" column + fields + row-actions column
    td.appendChild(document.createTextNode("None  "));
    const addFirst = el("button", "row-add", "＋ Add row");
    addFirst.title = "Add the first row to this section";
    addFirst.addEventListener("click", () => addRowToSection(sec.index));
    td.appendChild(addFirst);
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
  sec.records.forEach((rec, i) => {
    const tr = el("tr");
    const num = el("td"); num.appendChild(el("span", "rownum", String(i + 1))); tr.appendChild(num);  // per-section row #
    sec.fields.forEach((field) => {
      if (field.hidden) return;   // structural/marker field — never shown
      const td = el("td");
      const ctl = makeControl(sec, rec, field);
      td.appendChild(ctl.okgenWrap || ctl);
      if (ctl.okgenBadge) td.appendChild(ctl.okgenBadge);
      tr.appendChild(td);
    });
    const atMax = sec.max_records != null && n >= sec.max_records;
    const actTd = el("td", "del-cell");
    const up = el("button", "row-move", "↑"); up.title = "Move up"; up.disabled = i === 0;
    up.addEventListener("click", () => moveRow(rec.index, "up"));
    const down = el("button", "row-move", "↓"); down.title = "Move down"; down.disabled = i === n - 1;
    down.addEventListener("click", () => moveRow(rec.index, "down"));
    const addB = el("button", "row-add", "＋"); addB.disabled = atMax;
    addB.title = atMax ? `Limit of ${sec.max_records} reached` : "Add a copy below";
    addB.addEventListener("click", () => addRowAfter(rec.index));
    const delBtn = el("button", "row-del", "✕");
    delBtn.title = "Delete this row";
    delBtn.addEventListener("click", () => deleteRow(rec.index));
    actTd.append(up, down, addB, delBtn);
    tr.appendChild(actTd);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  box.appendChild(table);
  return box;
}

async function moveRow(recordIndex, direction) {
  if (!state.file) return;
  if (!beginBusy(direction === "up" ? "Moving row up…" : "Moving row down…")) {
    setStatus("Please wait — an operation is already running…", "dirty"); return;
  }
  try {
    const journal = journalWith({ type: "move", record_index: recordIndex, direction });
    const view = await postJSON("/api/record/move", {
      path: state.file, record_index: recordIndex, direction,
      ops: pendingOps(), edits: collectEdits(), preview: true,
    });
    commitJournal(journal);
    state.view = view;
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("Row moved (unsaved)", "dirty");
  } catch (e) {
    setStatus("Move failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

async function deleteRow(recordIndex) {
  if (!state.file) return;
  if (!confirm("Delete this row?")) return;
  if (!beginBusy("Deleting row…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const journal = journalWith({ type: "delete", record_index: recordIndex });
    const view = await postJSON("/api/record/delete", {
      path: state.file,
      record_index: recordIndex,
      ops: pendingOps(),
      edits: collectEdits(),
      preview: true,
    });
    commitJournal(journal);
    state.view = view;
    renderEditor(view);
    updateSaveButtons();
    updateDirtyIndicator();
    setStatus("Row deleted (unsaved)", "dirty");
  } catch (e) {
    setStatus("Delete failed: " + e.message, "err");
  } finally {
    state.busy = false;
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
  refreshDerived(c.dataset.section, c.dataset.record);   // driving field may feed a derived one
  // A roll-up moves when either side is touched: editing a summed row pushes
  // the new total into the header field; editing the total itself only re-badges
  // it, so the user's keystrokes are never overwritten as they type.
  const rspec = rollupSpec();
  if (rspec) {
    const sec = state.view && state.view.sections[Number(c.dataset.section)];
    const fromRows = !!(sec && sec.name === rspec.section
                        && c.dataset.field === rspec.source);
    if (fromRows || c.dataset.field === rspec.field) refreshRollup(fromRows);
  }
  updateSaveButtons();
}

function updateSaveButtons() {
  // `dirty` counts EVERY kind of pending change: field edits (state.edits) and
  // staged row add/delete/move (state.ops). Both buttons light up on any of
  // them. Save As stays enabled with no changes too, so it can still copy a
  // file to a new name — it is styled as clearly live rather than greyed.
  const dirty = dirtyCount();
  // Save is also enabled when opening the file dropped stray trailing blank
  // lines (state.normalized) so the user can persist that cleanup in one click.
  $("#saveBtn").disabled = !state.file || (dirty === 0 && !state.normalized);
  $("#saveAsBtn").disabled = !state.file;
  const saveAs = $("#saveAsBtn");
  saveAs.classList.toggle("has-changes", dirty > 0);
  saveAs.title = dirty > 0
    ? `Save all ${dirty} unsaved change(s) to a NEW file — this file stays as it is`
    : "Save a copy of this file under a new name";
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
  if (!beginBusy("Saving…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  const edits = collectEdits();
  const ops = pendingOps();
  try {
    // Save As sends the same staged work to a NEW path — the file we opened is
    // never written to, so it keeps the rows/values it had on disk.
    const res = await postJSON("/api/save", {
      path: state.file,
      edits,
      ops,
      target_path: targetPath || null,
    });
    state.edits = {};  // persisted — clear so refresh isn't treated as dirty
    state.ops = [];
    const changes = res.edits_applied + (res.ops_applied || 0);
    // A roll-up the save corrected on its own is stated, never silent — the
    // user typed one value and a different one is now on disk.
    const rolled = (res.rollups || []).map((r) => r.reason === "seeded"
      ? `${r.field} set to ${r.to} (no ${r.section.toLowerCase()} lines — a starting quantity)`
      : `${r.field} corrected ${r.from} → ${r.to} to match ${r.rows} `
        + `${r.section.toLowerCase()} line(s)`);
    setStatus(`Saved ${changes} change(s) to ${baseName(res.path)}` +
              (rolled.length ? ` · ${rolled.join("; ")}` : "") +
              (res.roundtrip_ok ? "" : " (round-trip DIFFERS!)"),
              res.roundtrip_ok ? "ok" : "err");
    const openPath = targetPath || state.file;
    if (targetPath) await refreshFolder(folderOf(targetPath));
    await loadFile(openPath);
  } catch (e) {
    setStatus("Save failed: " + e.message, "err");
  } finally {
    state.busy = false;
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
  menu.appendChild(el("div", "ctx-sep"));
  // BOTH bulk modes, as the Bulk Actions dropdown offers them. A single
  // "Bulk Edit" entry here called enterBulkMode() with no argument, which
  // defaults to "fields" — so rows & sequences was unreachable from the
  // right-click menu entirely. They are not two doors to one job (D59): field
  // edits are order-independent and batch, row ops do not, which is why the
  // split exists and why one entry cannot stand for both.
  add(count > 1 ? `Bulk Edit — field values (${count})` : "Bulk Edit — field values",
      () => enterBulkMode("fields"));
  add(count > 1 ? `Bulk Edit — rows & sequences (${count})` : "Bulk Edit — rows & sequences",
      () => enterBulkMode("rows"));
  add(count > 1 ? `Bulk Rename (${count})…` : "Bulk Rename…", () => enterRenameMode());
  add(count > 1 ? `Make keys unique (${count})` : "Make keys unique", () => makeUniqueSelection());
  add(count > 1 ? `🧹  Clean up ${count} files` : "🧹  Clean up file", () => cleanUpSelection());
  add(count > 1 ? `🔢  Total Qty check (${count})…` : "🔢  Total Qty check…", () => totalQtySelection());
  add(count > 1 ? `⇄  Convert ${count} files to JSON…` : "⇄  Convert to JSON…",
      () => convertToJson());
  add(sendMenuLabel(count), () => sendToNiceLabel());
  add(count > 1 ? `▶  Run TOSCA Script (${count})` : "▶  Run TOSCA Script", () => runTosca());
  menu.appendChild(el("div", "ctx-sep"));
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
function baseName(p) { const i = Math.max(p.lastIndexOf("/"), p.lastIndexOf("\\")); return p.slice(i + 1); }

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

function beginBusy(message, overlay = true) {
  if (state.busy) return false;
  state.busy = true;
  if (overlay) showActivity(message);   // prominent spinner up front
  setStatus(message, "dirty");          // (also the small top-right status)
  return true;
}

async function makeUniqueFolder(path) {
  if (!beginBusy("Making keys unique…")) {
    setStatus("Please wait — an operation is already running…", "dirty");
    return;
  }
  try {
    const res = await postJSON("/api/unique/folder",
                               { path });
    await refreshFolder(path);
    setStatus(rekeyedSummary(res.rekeyed), "ok");
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
    const res = await postJSON("/api/unique/bulk",
                               { paths });
    new Set(paths.map(folderOf)).forEach((f) => refreshFolder(f));
    setStatus(rekeyedSummary((res.folders || []).flatMap((f) => f.rekeyed || [])), "ok");
  } catch (e) {
    setStatus("Make unique failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

// What Make Unique actually did. Normally just a count — but when a selection
// spanned MORE THAN ONE key field it says which, because that only happens
// when the files resolved to different sources (a SCAN-named file inside a WMS
// folder, say) and nothing else would reveal it: no file is open during a bulk
// run, so the editor's key row can't show it. Silent in the single-source case.
function rekeyedSummary(rekeyed) {
  const done = (rekeyed || []).filter((r) => r.to);
  if (!done.length) return "Keys already unique";
  const base = `Made keys unique: ${done.length} file(s) re-keyed`;
  const byField = new Map();
  done.forEach((r) => {
    const label = r.source ? `${r.field} (${r.source})` : r.field;
    byField.set(label, (byField.get(label) || 0) + 1);
  });
  if (byField.size < 2) return base;
  const parts = [...byField.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([label, n]) => `${n} ${label}`);
  return `${base} — ${parts.join(", ")}`;
}

// ---- Clean up files (remove stray trailing blank lines) ----
async function cleanUpSelection() {
  const paths = [...state.selection];
  if (!paths.length) return;
  if (!beginBusy("Cleaning up files…")) {
    setStatus("Please wait — an operation is already running…", "dirty");
    return;
  }
  try {
    const res = await postJSON("/api/clean/bulk", { paths });
    new Set(paths.map(folderOf)).forEach((f) => refreshFolder(f));
    const errs = (res.results || []).filter((r) => r.status === "error").length;
    let msg = res.cleaned
      ? `Cleaned ${res.cleaned} of ${res.total} file(s)` +
        (res.cleaned < res.total ? " (rest already clean)" : "")
      : `All ${res.total} file(s) already clean`;
    if (errs) msg += ` — ${errs} couldn't be cleaned (open elsewhere or read-only?)`;
    setStatus(msg, errs ? "dirty" : "ok");
    // If the open file was one of them, reload so the editor drops the junk too.
    if (state.file && paths.includes(state.file)) loadFile(state.file);
  } catch (e) {
    setStatus("Clean up failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

// ---- Total Qty: inventory a selection, then optionally fix it ----
// Always PREVIEW first. This is the only bulk action that rewrites field
// CONTENT rather than junk, so the user sees every old -> new before a byte
// moves — and, just as importantly, sees the files it will NOT touch: one whose
// size section is empty has a total that is the quantity itself, and zeroing
// those would silently destroy the print quantity on the shape the new system
// produces most.
async function totalQtySelection() {
  const paths = [...state.selection];
  if (!paths.length) return;
  if (!beginBusy("Checking total quantities…")) {
    setStatus("Please wait — an operation is already running…", "dirty");
    return;
  }
  let res;
  try {
    res = await postJSON("/api/total-qty/scan", { paths });
  } catch (e) {
    setStatus("Total Qty check failed: " + e.message, "err");
    return;
  } finally {
    state.busy = false;
  }
  showTotalQtyReport(res, paths);
}

function showTotalQtyReport(res, paths) {
  const sum = res.summary || {};
  const applied = !!sum.applied;
  const ov = el("div", "modal-overlay");
  const card = el("div", "modal-card modal-wide");
  card.appendChild(el("h3", "modal-title", applied
    ? `Total Qty — ${sum.fixed} file(s) fixed`
    : `Total Qty preview — ${sum.would_fix} to fix, ${sum.no_rows} with no size lines`));

  const body = el("div", "modal-body");
  card.appendChild(body);
  body.appendChild(el("div", "modal-dest",
    `${sum.total} selected  ·  ${sum.ok} already correct  ·  ${sum.skipped} not applicable`
    + (sum.errors ? `  ·  ${sum.errors} error(s)` : "")));
  if (!applied && sum.no_rows) {
    const warn = el("div", "modal-warn");
    warn.appendChild(el("span", "modal-warn-icon", "ⓘ"));
    warn.appendChild(el("span", "modal-warn-text",
      `${sum.no_rows} file(s) have no size lines. Their total IS the quantity to `
      + `print, so they are listed (largest first) and left untouched — update `
      + `them yourself with Bulk Edit if a value looks wrong.`));
    body.appendChild(warn);
  }
  const pre = el("pre", "send-report-text");
  pre.textContent = res.report || "(no report)";
  body.appendChild(pre);
  if (res.log) body.appendChild(el("div", "send-report-log", `Also written to: ${res.log}`));

  const acts = el("div", "modal-actions");
  const close = el("button", "btn", applied ? "Close" : "Cancel");
  close.addEventListener("click", () => ov.remove());
  acts.appendChild(close);
  if (!applied && sum.would_fix) {
    const go = el("button", "btn btn-primary", `Fix ${sum.would_fix} file(s)`);
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        const done = await postJSON("/api/total-qty/fix", { paths });
        ov.remove();
        new Set(paths.map(folderOf)).forEach((f) => refreshFolder(f));
        showTotalQtyReport(done, paths);
        if (state.file && paths.includes(state.file)) loadFile(state.file);
      } catch (e) {
        setStatus("Total Qty fix failed: " + e.message, "err");
        ov.remove();
      }
    });
    acts.appendChild(go);
  }
  card.appendChild(acts);
  ov.appendChild(card); document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  close.focus();
}

// ---- Send to NiceLabel ----
const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// The two hand-offs read differently in the menu: .OK files are COPIED to the
// watched hot folder, .json files are POSTed to an endpoint. A mixed selection
// keeps the generic wording — the server refuses it and says why.
function sendMenuLabel(count) {
  const paths = [...state.selection];
  const allJson = paths.length > 0 && paths.every((p) => /\.json$/i.test(p));
  const verb = allJson ? "POST" : "Send";
  const icon = allJson ? "📤" : "🏷️";
  return count > 1 ? `${icon}  ${verb} ${count} to NiceLabel`
                   : `${icon}  ${verb} to NiceLabel`;
}

// Custom confirm modal for Send: yellow warning + a guard checkbox that must
// be ticked before the Send button enables. Resolves true (send) / false.
//
// `scope` comes from /api/send/scope and says which hand-off this selection
// gets — .OK files are COPIED to the watched hot folder, .json files are POSTed
// to an endpoint — so the dialog can name the real destination either way.
function confirmSend(scope) {
  const count = scope.count;
  const post = scope.mode === "post";
  return new Promise((resolve) => {
    const ov = el("div", "modal-overlay");
    const card = el("div", "modal-card");
    card.appendChild(el("h3", "modal-title", post
      ? `POST ${count} JSON file(s) to NiceLabel?`
      : `Send ${count} file(s) to NiceLabel?`));
    if (scope.destination) card.appendChild(el("div", "modal-dest", scope.destination));
    if (post) {
      const bits = [];
      if (scope.username) bits.push(`as ${scope.username}`);
      if (scope.folder) bits.push(`staged in ${scope.folder}`);
      if (bits.length) card.appendChild(el("div", "modal-sub", bits.join(" · ")));
    }

    if (scope.warning) {
      const box = el("div", "modal-warn");
      box.appendChild(el("span", "modal-warn-icon", "⚠"));
      box.appendChild(el("span", "modal-warn-text", scope.warning));
      card.appendChild(box);
    }

    const check = el("label", "modal-check");
    const cb = el("input");
    cb.type = "checkbox";
    check.appendChild(cb);
    check.appendChild(el("span", null, post
      ? "I've confirmed this is the right endpoint, and these files are ready to go live."
      : "I've confirmed the correct NiceLabel trigger(s) are running."));
    card.appendChild(check);

    const acts = el("div", "modal-actions");
    const cancel = el("button", "btn", "Cancel");
    const send = el("button", "btn btn-primary", "Send");
    send.disabled = true;
    acts.appendChild(cancel);
    acts.appendChild(send);
    card.appendChild(acts);

    ov.appendChild(card);
    document.body.appendChild(ov);

    const close = (val) => {
      document.removeEventListener("keydown", onKey);
      ov.remove();
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close(false);
      else if (e.key === "Enter" && cb.checked) close(true);
    };

    cb.addEventListener("change", () => { send.disabled = !cb.checked; });
    cancel.addEventListener("click", () => close(false));
    send.addEventListener("click", () => { if (cb.checked) close(true); });
    ov.addEventListener("click", (e) => { if (e.target === ov) close(false); });
    document.addEventListener("keydown", onKey);
    cb.focus();
  });
}

// Poll a background POST run until it finishes, keeping the animation's
// subtitle on the live counters. A 500-file run takes minutes, far longer than
// a browser or corporate proxy will hold one request open — hence the job.
async function followSendJob(jobId, total) {
  for (;;) {
    const st = await api(`/api/send/status/${encodeURIComponent(jobId)}`);
    if (st.state === "running") {
      updateSendProgress(st.done, st.total || total, st.posted, st.failed);
      await delay(700);
      continue;
    }
    if (st.state === "error") throw new Error(st.error || "the send failed");
    return st.result;
  }
}

// The JSON hand-off: confirm against the real endpoint, then run the POST as a
// background job and poll it. Kept SEPARATE from the .OK path below so that one
// stays exactly the code it has always been.
async function sendJsonToNiceLabel(paths) {
  let scope;
  try {
    scope = await postJSON("/api/send/scope", { paths });
  } catch (e) {
    setStatus("Send: " + e.message, "err");
    return;
  }
  if (!scope.configured) {
    setStatus("Send is not configured — " + (scope.error || "check config/"), "err");
    return;
  }
  if (!(await confirmSend(scope))) return;
  // overlay:false — Send has its own copy animation; don't stack the generic one.
  if (!beginBusy("Posting to NiceLabel…", false)) { setStatus("Please wait — an operation is already running…", "dirty"); return; }

  showCopyAnimation(paths.length, scope.destination, true);
  const minOnScreen = delay(2000);   // keep the animation up long enough to register
  try {
    const handle = await postJSON("/api/send/start", { paths });
    const res = await followSendJob(handle.job, paths.length);
    await minOnScreen;
    finishCopyAnimation(res);
    const s = res.sent.length, er = res.errors.length;
    setStatus(`Posted ${s} of ${paths.length} file(s) to NiceLabel` + (er ? `, ${er} failed` : ""),
              er ? "err" : "ok");
  } catch (e) {
    await minOnScreen;
    hideCopyAnimation();
    setStatus("Send failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

async function sendToNiceLabel() {
  const paths = [...state.selection];
  if (!paths.length) return;
  // Only an ALL-.json selection takes the POST route. Anything else — all .OK,
  // or a mix — runs the original hot-folder copy below, unchanged.
  if (paths.every((p) => /\.json$/i.test(p))) return sendJsonToNiceLabel(paths);

  const dest = window.OKGEN_NICELABEL || "the NiceLabel folder";
  const warn = window.OKGEN_NICELABEL_WARNING || "";
  if (!(await confirmSend({ mode: "copy", count: paths.length,
                            destination: dest, warning: warn }))) return;
  // overlay:false — Send has its own copy animation; don't stack the generic one.
  if (!beginBusy("Sending to NiceLabel…", false)) { setStatus("Please wait — an operation is already running…", "dirty"); return; }

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

// ---- Run TOSCA Script ----
// Pick a configured script (workbook), then POST the selected files; the server
// writes one row per unique Chain/Process/Format into that workbook's data sheet.
// ---- TOSCA Reports: open a script's results folder in the OS file manager ---
// Deliberately hands off to Explorer instead of listing the files here. Those
// folders hold Word and Excel documents, which no browser can render without a
// heavy vendored converter (the D31 trade) — and Explorer already does all of
// it: thumbnails, sorting, and a double-click that opens each document in its
// own app. A table imitating Explorer would be more work and permanently worse.
async function openToscaReports() {
  let info;
  try { info = await api("/api/tosca/reports"); }
  catch (e) { setStatus("Could not load TOSCA report folders: " + e.message, "err"); return; }
  const folders = info.folders || [];
  if (!folders.length) {
    setStatus("No TOSCA script declares a results folder — add a `results:` path "
              + "in config/tosca.yaml and restart OkGen", "err");
    return;
  }
  const script = await pickReportFolder(folders);
  if (!script) return;
  try {
    const res = await postJSON("/api/tosca/reports/open", { script });
    // Explorer opens OUTSIDE the browser and can land behind it, so nothing
    // visibly happens here — say what was opened rather than leaving the click
    // looking ignored (the folder-chooser lesson).
    setStatus(`Opened the ${script} results folder in Explorer — ${res.opened}`, "ok");
  } catch (e) {
    setStatus(e.message, "err");
  }
}

function pickReportFolder(folders) {
  return new Promise((resolve) => {
    const ov = el("div", "modal-overlay");
    const card = el("div", "modal-card");
    card.appendChild(el("h3", "modal-title", "TOSCA Reports"));
    card.appendChild(el("div", "modal-dest",
      "Which script's results? The folder opens in Explorer, where the "
      + "Word and Excel reports open as usual."));
    const list = el("div", "tosca-scripts");
    folders.forEach((f, i) => {
      const lab = el("label", "tosca-choice");
      const rb = el("input"); rb.type = "radio"; rb.name = "tosca-reports"; rb.value = f.name;
      if (i === 0) rb.checked = true;
      lab.appendChild(rb);
      const box = el("span", "report-choice-text");
      box.appendChild(el("span", null, f.name));
      // The full path, and a plain warning when it is not on THIS machine —
      // only some of these folders are set up, and an empty Explorer window
      // would not say why.
      box.appendChild(el("span", "report-path", f.folder));
      if (!f.exists) {
        box.appendChild(el("span", "report-missing",
                           "not found on this machine — check config/tosca.yaml"));
      }
      lab.appendChild(box);
      list.appendChild(lab);
    });
    card.appendChild(list);
    const acts = el("div", "modal-actions");
    const cancel = el("button", "btn", "Cancel");
    const open = el("button", "btn btn-primary", "Open in Explorer");
    acts.appendChild(cancel); acts.appendChild(open);
    card.appendChild(acts);
    ov.appendChild(card); document.body.appendChild(ov);
    const close = (val) => { ov.remove(); resolve(val); };
    cancel.addEventListener("click", () => close(null));
    ov.addEventListener("click", (e) => { if (e.target === ov) close(null); });
    open.addEventListener("click", () => {
      const sel = card.querySelector("input[name=tosca-reports]:checked");
      close(sel ? sel.value : null);
    });
    open.focus();
  });
}

function pickTosca(scripts, count, warning) {
  return new Promise((resolve) => {
    const ov = el("div", "modal-overlay");
    const card = el("div", "modal-card");
    card.appendChild(el("h3", "modal-title", `Run TOSCA on ${count} file(s)`));
    card.appendChild(el("div", "modal-dest", "Choose the script whose input sheet to populate, then run:"));
    const list = el("div", "tosca-scripts");
    scripts.forEach((s, i) => {
      const lab = el("label", "tosca-choice");
      const rb = el("input"); rb.type = "radio"; rb.name = "tosca-script"; rb.value = s.name;
      if (i === 0) rb.checked = true;
      lab.appendChild(rb);
      lab.appendChild(el("span", null, s.name));
      list.appendChild(lab);
    });
    card.appendChild(list);

    // Production action — warn about the common PowerForms-link mistake and gate
    // Run behind an acknowledgement checkbox (like Send to NiceLabel).
    if (warning) {
      const box = el("div", "modal-warn");
      box.appendChild(el("span", "modal-warn-icon", "⚠"));
      box.appendChild(el("span", "modal-warn-text", warning));
      card.appendChild(box);
    }
    const check = el("label", "modal-check");
    const cb = el("input"); cb.type = "checkbox";
    check.appendChild(cb);
    check.appendChild(el("span", null, "I've verified the correct PowerForms link is in the input sheet."));
    card.appendChild(check);

    const acts = el("div", "modal-actions");
    const cancel = el("button", "btn", "Cancel");
    const run = el("button", "btn btn-primary", "Run TOSCA");
    run.disabled = true;
    cb.addEventListener("change", () => { run.disabled = !cb.checked; });
    acts.appendChild(cancel); acts.appendChild(run);
    card.appendChild(acts);
    ov.appendChild(card); document.body.appendChild(ov);
    const close = (val) => { ov.remove(); resolve(val); };
    cancel.addEventListener("click", () => close(null));
    ov.addEventListener("click", (e) => { if (e.target === ov) close(null); });
    run.addEventListener("click", () => {
      if (run.disabled) return;
      const sel = card.querySelector("input[name=tosca-script]:checked");
      close(sel ? sel.value : null);
    });
  });
}

function showToscaResult(res) {
  // Pinned to the BOTTOM-RIGHT (see .modal-corner): the TOSCA script opens its
  // own console window on top of the browser, which covered a centred card, so
  // these messages sit beside it rather than behind it.
  const ov = el("div", "modal-overlay modal-corner");
  const card = el("div", "modal-card modal-wide");
  card.appendChild(el("h3", "modal-title", `TOSCA '${res.script}' — ${res.written} row(s) written`));
  // Everything below the title scrolls, so long .bat/workbook paths and long row
  // lists stay inside the window instead of running off it.
  const body = el("div", "modal-body");
  card.appendChild(body);
  body.appendChild(el("div", "modal-dest", res.workbook));
  if (res.launched) {
    const ok = el("div", "tosca-launch ok",
      "▶ TOSCA started (fire-and-forget). Launched .bat: " + (res.bat || "?"));
    body.appendChild(ok);
  } else if (res.launch_error) {
    const bad = el("div", "tosca-launch warn", "TOSCA not started: " + res.launch_error);
    body.appendChild(bad);
  }
  const rows = res.rows || [];
  if (rows.length) {
    const tbl = el("div", "tosca-rows");
    rows.forEach((r) => tbl.appendChild(
      el("div", "tosca-row", `${r.chain} · ${r.process} · ${r.format}`)));
    body.appendChild(tbl);
  }
  // Files this script doesn't apply to (.OK selected for a JSON script, or vice
  // versa) — reported, never silently dropped.
  const skipped = res.skipped || [];
  if (skipped.length) {
    const box = el("div", "modal-warn");
    box.appendChild(el("span", "modal-warn-icon", "⏭"));
    box.appendChild(el("span", "modal-warn-text",
      `${skipped.length} file(s) not applicable to this script: `
      + skipped.map((s) => s.file).join(", ")));
    body.appendChild(box);
  }
  const errs = res.errors || [];
  if (errs.length) {
    const box = el("div", "modal-warn");
    box.appendChild(el("span", "modal-warn-icon", "⚠"));
    box.appendChild(el("span", "modal-warn-text",
      `${errs.length} file(s) could not be used: ` + errs.map((e) => `${e.file} (${e.error})`).join("; ")));
    body.appendChild(box);
  }
  const acts = el("div", "modal-actions");
  const ok = el("button", "btn btn-primary", "Close");
  acts.appendChild(ok); card.appendChild(acts);
  ov.appendChild(card); document.body.appendChild(ov);
  const close = () => ov.remove();
  ok.addEventListener("click", close);
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
}

async function runTosca() {
  const paths = [...state.selection];
  if (!paths.length) return;
  let info;
  // POST so the server can filter the list to the selection's engine: .OK and
  // JSON have separate workbooks, so only the applicable scripts are offered.
  try { info = await postJSON("/api/tosca/scripts", { paths }); }
  catch (e) { setStatus("Could not load TOSCA scripts: " + e.message, "err"); return; }
  const all = info.scripts || [];
  if (!all.length) { setStatus("No TOSCA scripts configured (config/tosca.yaml)", "err"); return; }
  const scripts = all.some(s => s.matches > 0) ? all.filter(s => s.matches > 0) : all;
  const script = await pickTosca(scripts, paths.length, info.warning);
  if (!script) return;
  if (!beginBusy("Running TOSCA…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const res = await postJSON("/api/tosca/run", { paths, script });
    const er = (res.errors || []).length;
    const sk = (res.skipped || []).length;
    const launch = res.launched ? " — TOSCA started" : (res.launch_error ? " — not started" : "");
    setStatus(`TOSCA '${script}': wrote ${res.written} row(s)`
              + (er ? `, ${er} error(s)` : "")
              + (sk ? `, skipped ${sk} file(s) not applicable to this script` : "")
              + launch,
              res.launch_error ? "dirty" : "ok");
    showToscaResult(res);
  } catch (e) {
    setStatus("TOSCA run failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

// ---- Send animation: a different little scene each time, for fun ----
// Each scene fills .send-scene; movers live in the .send-mid band and ride
// from left:0 → left:100% so the start/end emoji anchors frame the journey.
const SEND_SCENES = [
  { cls: "scene-fly", html:                       // ✈ original: papers flutter across
    `<span class="send-folder">📂</span>
     <span class="send-mid"><span class="send-paper"></span><span class="send-paper"></span><span class="send-paper"></span></span>
     <span class="send-folder">🏷️</span>` },
  { cls: "scene-rocket", html:                     // 🚀 rocket blasts the files over
    `<span class="send-folder">🛰️</span>
     <span class="send-mid"><span class="rk-spark">✨</span><span class="rk-rocket">🚀</span></span>
     <span class="send-folder">🏷️</span>` },
  { cls: "scene-truck", html:                      // 🚚 delivery truck hauls them
    `<span class="send-folder">🏭</span>
     <span class="send-mid"><span class="tk-truck">🚚</span></span>
     <span class="send-folder">🏬</span>` },
  { cls: "scene-belt", html:                       // 📦 conveyor belt of cartons
    `<span class="send-folder">📥</span>
     <span class="send-mid"><span class="bl-box">📦</span><span class="bl-box">📦</span><span class="bl-box">📦</span><span class="bl-box">📦</span></span>
     <span class="send-folder">📤</span>` },
  { cls: "scene-printer", html:                    // 🖨️ printer spitting out labels
    `<span class="send-folder pr-printer">🖨️</span>
     <span class="send-mid"><span class="pr-lab">🏷️</span><span class="pr-lab">🏷️</span><span class="pr-lab">🏷️</span></span>
     <span class="send-folder">📥</span>` },
  { cls: "scene-plane", html:                      // ✈️ airmail express
    `<span class="send-folder">📨</span>
     <span class="send-mid"><span class="pl-plane">✈️</span></span>
     <span class="send-folder">📬</span>` },
  { cls: "scene-beam", html:                        // 🛸 beamed up to the mothership
    `<span class="send-folder">🗂️</span>
     <span class="send-mid"><span class="bm-file">📄</span><span class="bm-file">📄</span><span class="bm-file">📄</span></span>
     <span class="send-folder bm-ship">🛸</span>` },
];

// Configurable via config/nicelabel.yaml (quips / done_quips); fall back to these.
const _DEFAULT_QUIPS = [
  "Beaming labels to NiceLabel…", "Folding the OK files neatly…",
  "Greasing the conveyor belt…", "Waking up the print triggers…",
  "Stamping fresh barcodes…", "Loading the delivery truck…",
  "Sprinkling magic toner…", "Negotiating with the printer…",
  "Aligning the perforations…", "Routing through the hot folder…",
  "Counting the cartons…", "Polishing the price tags…",
  "Teleporting to the DC…", "Warming up the label rollers…",
  "Convincing NiceLabel to cooperate…", "Untangling the ribbon…",
  "Double-checking the SKUs…", "Lining up the carton labels…",
];

const _DEFAULT_DONE_QUIPS = [
  "Off to the printers! 🎉", "Labels are on their way!",
  "NiceLabel has the ball now.", "Delivered to the hot folder!",
  "Wheels up — bon voyage! ✈️", "Cartons loaded and rolling.",
];

const SEND_QUIPS = (Array.isArray(window.OKGEN_SEND_QUIPS) && window.OKGEN_SEND_QUIPS.length)
  ? window.OKGEN_SEND_QUIPS : _DEFAULT_QUIPS;
const SEND_DONE_QUIPS = (Array.isArray(window.OKGEN_SEND_DONE_QUIPS) && window.OKGEN_SEND_DONE_QUIPS.length)
  ? window.OKGEN_SEND_DONE_QUIPS : _DEFAULT_DONE_QUIPS;

let sendQuipTimer = null;
const _pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

function showCopyAnimation(n, dest, post) {
  hideCopyAnimation();
  const scene = _pick(SEND_SCENES);
  const overlay = el("div", "send-overlay");
  overlay.id = "sendOverlay";
  const safeDest = (dest || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
  overlay.innerHTML = `
    <div class="send-card">
      <div class="send-scene ${scene.cls}">${scene.html}</div>
      <div class="send-title">${post ? "Posting" : "Sending"} ${n} file(s) to NiceLabel…</div>
      <div class="send-quip"></div>${post ? `
      <div class="send-progress">0 of ${n}</div>` : ""}
      <div class="send-sub">${safeDest}</div>
    </div>`;
  document.body.appendChild(overlay);

  // Rotate playful status lines while the send is in flight.
  const quipEl = overlay.querySelector(".send-quip");
  let last = -1;
  const tick = () => {
    let i;
    do { i = Math.floor(Math.random() * SEND_QUIPS.length); }
    while (i === last && SEND_QUIPS.length > 1);
    last = i;
    quipEl.textContent = SEND_QUIPS[i];
    quipEl.classList.remove("q-show");
    void quipEl.offsetWidth;          // reflow so the fade-in replays
    quipEl.classList.add("q-show");
  };
  tick();
  sendQuipTimer = setInterval(tick, 1300);
}

// Live counters while a POST run is in flight ("124 of 500 · 3 failed").
function updateSendProgress(done, total, posted, failed) {
  const box = $("#sendOverlay") && $("#sendOverlay").querySelector(".send-progress");
  if (!box) return;
  box.textContent = `${done} of ${total}` +
    (posted ? ` · ${posted} posted` : "") +
    (failed ? ` · ${failed} failed` : "");
}

// Per-file failures, so "3 failed" is actionable instead of just alarming.
function buildSendReport(res) {
  const box = el("div", "send-report");
  const sum = res.summary;
  if (sum) {
    if (sum.aborted) box.appendChild(el("div", "send-report-abort", sum.aborted));
    const causes = Object.entries(sum.failures_by_cause || {});
    if (causes.length) {
      box.appendChild(el("div", "send-report-causes",
        causes.map(([c, n]) => `${n} ${c}`).join(" · ")));
    }
  }
  const bad = (res.results || []).filter((r) => r.outcome !== "posted" && r.outcome !== "skipped");
  const rows = bad.length ? bad.map((r) => `${r.name} — ${r.message}`)
                          : (res.errors || []).map((e) => `${e.path} — ${e.error}`);
  if (rows.length) {
    const list = el("div", "send-report-list");
    rows.slice(0, 50).forEach((line) => list.appendChild(el("div", "send-report-row", line)));
    if (rows.length > 50) list.appendChild(el("div", "send-report-row", `…and ${rows.length - 50} more`));
    box.appendChild(list);
  }
  if (sum && sum.log) box.appendChild(el("div", "send-report-log", `Log: ${sum.log}`));
  return box;
}

// The full run report — same text as the log file, shown in OkGen so nobody has
// to go looking for it, with one click to put it on the clipboard.
function showSendReport(res) {
  const sum = res.summary || {};
  const ov = el("div", "modal-overlay");
  const card = el("div", "modal-card modal-wide");
  card.appendChild(el("h3", "modal-title",
    `Send report — ${sum.posted || 0} posted, ${sum.failed || 0} failed`
    + (sum.skipped ? `, ${sum.skipped} skipped` : "")));

  const body = el("div", "modal-body");
  card.appendChild(body);

  const meta = [];
  if (sum.endpoint) meta.push(sum.endpoint + (sum.username ? `  (user: ${sum.username})` : ""));
  if (sum.elapsed_seconds != null) {
    meta.push(`${sum.elapsed_seconds}s`
      + (sum.files_per_second ? `  ·  ${sum.files_per_second} files/sec` : ""));
  }
  if (meta.length) body.appendChild(el("div", "modal-dest", meta.join("   ·   ")));
  if (sum.aborted) {
    const warn = el("div", "modal-warn");
    warn.appendChild(el("span", "modal-warn-icon", "⚠"));
    warn.appendChild(el("span", "modal-warn-text", sum.aborted));
    body.appendChild(warn);
  }
  const pre = el("pre", "send-report-text");
  pre.textContent = sum.report || "(no report)";
  body.appendChild(pre);
  if (sum.log) body.appendChild(el("div", "send-report-log", `Also written to: ${sum.log}`));

  const acts = el("div", "modal-actions");
  const copy = el("button", "btn", "Copy report");
  copy.addEventListener("click", async () => {
    const text = sum.report || "";
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // Clipboard API needs a secure context; fall back to a scratch textarea.
      const ta = el("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    copy.textContent = "Copied ✓";
    setTimeout(() => { copy.textContent = "Copy report"; }, 1600);
  });
  const ok = el("button", "btn btn-primary", "Close");
  ok.addEventListener("click", () => ov.remove());
  acts.appendChild(copy); acts.appendChild(ok); card.appendChild(acts);
  ov.appendChild(card); document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  ok.focus();
}

function finishCopyAnimation(res) {
  const overlay = $("#sendOverlay");
  if (!overlay) return;
  if (sendQuipTimer) { clearInterval(sendQuipTimer); sendQuipTimer = null; }
  const s = res.sent.length, er = res.errors.length;
  // Only the JSON POST result carries a summary; the .OK copy is reported
  // exactly as it always was.
  const post = res.mode === "post";
  const card = overlay.querySelector(".send-card");
  // Keep the scene playing for enjoyment; just show the result + OK.
  const title = card.querySelector(".send-title");
  if (title) title.innerHTML = post
    ? `<span class="send-ok-check">✓</span> Posted ${s} of ${res.summary.total} file(s)${er ? ` · ${er} failed` : ""}`
    : `<span class="send-ok-check">✓</span> Sent ${s} file(s) to NiceLabel${er ? ` · ${er} failed` : ""}`;
  const quip = card.querySelector(".send-quip");
  if (quip) {
    quip.textContent = er ? "Some files didn't make it — check the list." : _pick(SEND_DONE_QUIPS);
    quip.classList.add("q-show");
  }
  const prog = card.querySelector(".send-progress");
  if (prog) prog.remove();
  const sub = card.querySelector(".send-sub");
  if (sub) sub.remove();
  if (post && (er || res.summary.skipped)) card.appendChild(buildSendReport(res));
  if (!card.querySelector(".send-ok-btn")) {
    const row = el("div", "send-ok-row");
    if (post && res.summary && res.summary.report) {
      const view = el("button", "btn send-ok-btn", "View report");
      view.addEventListener("click", () => { hideCopyAnimation(); showSendReport(res); });
      row.appendChild(view);
    }
    const btn = el("button", "btn btn-primary send-ok-btn", "OK");
    btn.addEventListener("click", hideCopyAnimation);
    row.appendChild(btn);
    card.appendChild(row);
    btn.focus();   // Enter/Space closes it
  }
}

function hideCopyAnimation() {
  if (sendQuipTimer) { clearInterval(sendQuipTimer); sendQuipTimer = null; }
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
    const res = await postJSON("/api/file/copy-batch",
      { srcs: state.clipboard, dst_dir: folder });
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
$("#bulkBtn").addEventListener("click", (e) => { e.stopPropagation(); showBulkMenu(); });
$("#toscaBtn").addEventListener("click", (e) => { e.stopPropagation(); runTosca(); });
$("#toscaReportsBtn").addEventListener("click", (e) => { e.stopPropagation(); openToscaReports(); });

// Dropdown of bulk actions, anchored under the "Bulk Actions" button. It and
// the right-click menu are meant to offer the same bulk actions, and drifted
// once already: this list gained the D59 field/rows split while the right-click
// menu kept a single "Bulk Edit", leaving rows & sequences reachable from only
// one of the two. `tests/js/test_bulk_menus.js` now pins that they agree.
// They are NOT identical lists. This one has no file-management entries
// (Open/Copy/Rename/Delete), and "Generate volume files…" is deliberately here
// only — it works from ONE template, so it does not belong on a menu opened by
// right-clicking a file in a multi-file selection (the user's explicit call).
// Run TOSCA was missing here purely by omission and is now offered in both.
function showBulkMenu() {
  const n = state.selection.size;
  if (n < 1) return;
  const menu = $("#ctxMenu");
  menu.innerHTML = "";
  const add = (label, fn, disabled) => {
    const it = el("div", "ctx-item", label);
    if (disabled) it.classList.add("disabled");
    else it.addEventListener("click", () => { hideCtxMenu(); fn(); });
    menu.appendChild(it);
  };
  add(`Bulk Edit — field values (${n})`, () => enterBulkMode("fields"));
  add(`Bulk Edit — rows & sequences (${n})`, () => enterBulkMode("rows"));
  add(`Bulk Rename (${n})`, () => enterRenameMode());
  add(`Make keys unique (${n})`, () => makeUniqueSelection());
  add(n === 1 ? "🧹  Clean up file" : `🧹  Clean up ${n} files`, () => cleanUpSelection());
  add(n === 1 ? "🔢  Total Qty check…" : `🔢  Total Qty check (${n})…`, () => totalQtySelection());
  // Volume generation works from exactly ONE template file.
  add(n === 1 ? "Generate volume files…" : `Generate volume files… (${n} source files)`,
      () => enterGenerateMode());
  add(n === 1 ? "⇄  Convert to JSON…" : `⇄  Convert ${n} files to JSON…`,
      () => convertToJson());
  menu.appendChild(el("div", "ctx-sep"));
  add(sendMenuLabel(n), () => sendToNiceLabel());
  // Same order as the right-click menu (Send, then TOSCA), where this has
  // always been offered — it was missing here only by omission.
  add(n === 1 ? "▶  Run TOSCA Script" : `▶  Run TOSCA Script (${n})`, () => runTosca());
  const r = $("#bulkBtn").getBoundingClientRect();
  menu.style.left = Math.max(8, r.right - 220) + "px";
  menu.style.top = (r.bottom + 4) + "px";
  menu.classList.remove("hidden");
}
$("#folderPath").addEventListener("keydown", (e) => { if (e.key === "Enter") openFolder(e.target.value.trim()); });
// Show the full folder path on hover (the box is usually too narrow to see it).
$("#folderPath").addEventListener("input", (e) => { e.target.title = e.target.value; });
$("#saveBtn").addEventListener("click", () => save(null));
$("#saveAsBtn").addEventListener("click", () => {
  // Save As must not silently change a .json file into a .OK one.
  const ext = fileExt(state.file);
  const dflt = state.file
    ? state.file.replace(new RegExp(ext.replace(".", "\\.") + "$", "i"), "_copy" + ext)
    : "";
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


// ---- Generate volume files (from ONE template file) --------------------------
// Produces N copies of a template, each with a fresh unique key plus optional
// randomized header/detail fields and varying row counts. Names are built from
// the same token model as Bulk Rename.
function exitGenerateMode() {
  const panel = $("#generatePanel");
  if (panel.classList.contains("hidden")) return;
  panel.classList.add("hidden");
  panel.innerHTML = "";
  restoreEditorAfterPanel();
}

async function enterGenerateMode() {
  if (state.selection.size < 1) return;
  if (!confirmDiscardIfDirty()) return;
  const paths = [...state.selection];
  closeAllPanels("generate");    // only one bulk mode open at a time
  state.file = null; state.view = null; state.edits = {}; state.ops = []; state.normalized = 0;
  $("#editorTabs").classList.add("hidden");
  $("#editor").classList.add("hidden");
  $("#rawView").classList.add("hidden");
  $("#editorEmpty").style.display = "none";
  $("#fileTitle").textContent = "";
  updateSaveButtons();

  const panel = $("#generatePanel");
  panel.classList.remove("hidden");
  panel.innerHTML = "<div class='bulk-loading'><span class='spinner'></span> Reading source file(s)…</div>";

  let scope;
  try {
    scope = await postJSON("/api/generate/scope",
                           { paths });
  } catch (e) {
    panel.innerHTML = "";
    panel.appendChild(el("div", "bulk-note", "Could not read the source file(s): " + e.message));
    return;
  }
  renderGeneratePanel(panel, paths, scope);
}

function renderGeneratePanel(panel, paths, scope) {
  panel.innerHTML = "";
  // Declared first: refresh() runs while the panel is still being built (the
  // default filename chips call it), so these must already be initialised —
  // reading a `let` before its declaration throws and aborts the whole render.
  let timer = null;
  let armed = false, armTimer = null;
  const nTemplates = scope.template_count || (Array.isArray(paths) ? paths.length : 1);
  const head = el("div", "bulk-head");
  head.appendChild(el("h3", null,
    nTemplates === 1 ? `Generate volume files — ${scope.name}`
                     : `Generate volume files — ${nTemplates} source files (${scope.layout})`));
  head.appendChild(el("span", "bulk-label",
    (nTemplates === 1 ? "" : "each file drawn from a random source file · ") +
    `key ${scope.key_field} is assigned uniquely to every file`));
  head.appendChild(el("span", "bulk-label gen-build", `build ${OKGEN_BUILD}`));
  const topGen = el("button", "btn btn-primary", "Generate");
  head.appendChild(topGen);
  const close = el("button", "btn", "Close");
  close.addEventListener("click", exitGenerateMode);
  head.appendChild(close);
  panel.appendChild(head);

  // --- how many -------------------------------------------------------------
  const countRow = el("div", "bulk-edit-row");
  countRow.appendChild(el("span", "bulk-label", "How many files"));
  const countInput = el("input", "bulk-value");
  countInput.type = "number"; countInput.min = "1"; countInput.max = String(scope.max_count);
  countInput.value = "100"; countInput.style.width = "90px";
  [100, 200, 500, 1000].forEach((n) => {
    const b = el("button", "btn", String(n));
    b.addEventListener("click", () => { countInput.value = String(n); refresh(); });
    countRow.appendChild(b);
  });
  countRow.appendChild(countInput);
  countRow.appendChild(el("span", "bulk-label", `max ${scope.max_count}`));
  panel.appendChild(countRow);

  // --- randomized fields ----------------------------------------------------
  // One builder used for the header and for each detail section.
  function fieldPicker(title, fields, hostClass) {
    const box = el("div", "gen-group");
    box.appendChild(el("div", "bulk-label", title));
    if (!fields.length) {
      box.appendChild(el("div", "bulk-note", "No numeric fields available here."));
      box.classList.add(hostClass);   // still scanned for its row-count setting
      return box;
    }
    fields.forEach((f) => {
      const row = el("label", "gen-field");
      const cb = el("input", "gen-on"); cb.type = "checkbox";
      cb.dataset.field = f.name; cb.dataset.size = f.size;
      if (f.date) cb.dataset.date = "1";
      // Locked fields are shown greyed rather than omitted — omitted they read
      // as missing. Same treatment as Bulk Edit; the reason comes from the
      // server, since only it knows WHY (signature, key, or derived).
      const genLocked = f.editable === false;
      if (genLocked) { cb.disabled = true; row.classList.add("gen-locked"); }
      // A temporal field (config/date_fields.yaml) takes a DATE range instead
      // of a numeric one — each generated file (or row) gets its own instant.
      const isDate = !!f.date;
      const min = el("input", "gen-min");
      const max = el("input", "gen-max");
      min.type = max.type = isDate ? "text" : "number";
      min.placeholder = isDate ? "from  2024-01-01" : "min";
      max.placeholder = isDate ? "to  2024-12-31" : "max";
      min.disabled = max.disabled = true;
      if (isDate) { min.style.width = max.style.width = "140px"; }
      // A value list wins over the min/max range when it is filled in, so the
      // generated files only ever contain values the user allowed.
      const list = el("input", "gen-list"); list.type = "text";
      list.placeholder = isDate ? "or list: 2024-01-01, 2024-06-30"
                                : "or list: 10,20,'  msg',' '";
      list.disabled = true;
      list.title = "Comma-separated. When filled, values are picked from this "
                 + "list instead of the min/max range. Use ' ' for a blank value.";
      // A roll-up field's generated value is DISCARDED on any template that has
      // detail rows (the sum wins), so warn as soon as it is ticked rather than
      // letting the user read the sum in the preview and think it a glitch.
      const rspec = rollupSpecFor(scope.rollups, scope.layout, f.name);
      const note = rspec ? el("div", "gen-rollup-note", rollupWarning(rspec)) : null;
      if (note) note.style.display = "none";
      const syncRange = () => {
        if (genLocked) {
          min.disabled = max.disabled = list.disabled = true;
          if (note) note.style.display = "none";
          return;
        }
        const usingList = list.value.trim() !== "";
        min.disabled = max.disabled = !cb.checked || usingList;
        list.disabled = !cb.checked;
        if (note) note.style.display = cb.checked ? "" : "none";
      };
      cb.addEventListener("change", () => {
        if (cb.checked && min.value === "" && list.value.trim() === "" && !isDate) {
          min.value = "1"; max.value = String(Math.pow(10, f.size) - 1);
        }
        syncRange();
        refresh();
      });
      min.addEventListener("input", refresh);
      max.addEventListener("input", refresh);
      list.addEventListener("input", () => { syncRange(); refresh(); });
      row.appendChild(cb);
      row.appendChild(el("span", "gen-name",
                         isDate ? `${f.name} (date)` : `${f.name} (${f.size})`));
      row.appendChild(min); row.appendChild(max); row.appendChild(list);
      if (genLocked) {
        row.appendChild(el("span", "gen-lockreason", f.locked_reason || "read-only"));
      }
      box.appendChild(row);
      if (note) box.appendChild(note);
    });
    box.classList.add(hostClass);
    return box;
  }

  const fieldsWrap = el("div", "gen-cols");
  const headerBox = fieldPicker("Randomize header fields", scope.header_fields, "gen-header");
  fieldsWrap.appendChild(headerBox);

  scope.sections.forEach((sec) => {
    const box = fieldPicker(`Randomize “${sec.name}” row fields`, sec.fields, "gen-detail");
    box.dataset.section = sec.name;
    // per-section row-count variation
    const rc = el("div", "gen-rows");
    const on = el("input"); on.type = "checkbox"; on.className = "gen-rows-on";
    const lo = el("input", "gen-rows-min"); lo.type = "number"; lo.placeholder = "min rows"; lo.disabled = true;
    const hi = el("input", "gen-rows-max"); hi.type = "number"; hi.placeholder = "max rows"; hi.disabled = true;
    on.addEventListener("change", () => {
      lo.disabled = hi.disabled = !on.checked;
      if (on.checked && lo.value === "") {
        lo.value = "1";
        hi.value = String(sec.max_records || Math.max(sec.rows, 1));
      }
      refresh();
    });
    lo.addEventListener("input", refresh); hi.addEventListener("input", refresh);
    rc.appendChild(on);
    rc.appendChild(el("span", "gen-name",
      `Vary row count (now ${sec.rows}${sec.max_records ? `, max ${sec.max_records}` : ""})`));
    rc.appendChild(lo); rc.appendChild(hi);
    box.insertBefore(rc, box.children[1] || null);

    // A section with no DATA cannot take a field value the way it looks like
    // it will, and `rows` alone does not reveal that: an emptied JSON section
    // still holds ONE blank marker row, so it reports `rows: 1` exactly like a
    // section with one real row. Said up front, because the alternative is
    // finding out after generating the whole batch.
    if (sec.has_data === false) {
      const n = sec.no_data_templates || 0;
      const many = (scope.template_count || 1) > 1;
      box.appendChild(el("div", "gen-nodata-note",
        `⚠ ${sec.name} has no rows with data`
        + (many ? ` in ${n} of ${scope.template_count} templates` : "")
        + `. A value here lands on a blank placeholder row (or nowhere, if the `
        + `section is empty) — tick “Vary row count” above to create real rows.`));
    }
    fieldsWrap.appendChild(box);
  });
  panel.appendChild(fieldsWrap);

  // --- filename pattern (same token model as Bulk Rename) -------------------
  const nameRow = el("div", "bulk-edit-row");
  nameRow.appendChild(el("span", "bulk-label", "File name"));
  const partsBox = el("div", "gen-parts");
  nameRow.appendChild(partsBox);
  const tokenSel = el("select", "bulk-field");
  const palette = scope.palette || {};
  const tokenNames = []
    .concat(palette.derived || [])
    .concat(palette.header_fields || []);
  ["layout", "chain", "brand", "key", "seq", "orig"].forEach((t) => {
    if (!tokenNames.includes(t)) tokenNames.unshift(t);
  });
  tokenSel.appendChild(new Option("+ add token…", ""));
  tokenNames.forEach((t) => tokenSel.appendChild(new Option(t, t)));
  tokenSel.addEventListener("change", () => {
    if (!tokenSel.value) return;
    addPart(tokenSel.value);
    tokenSel.value = "";
  });
  nameRow.appendChild(tokenSel);
  const sepSel = el("select", "bulk-field");
  [["_", "_ underscore"], ["-", "- hyphen"], ["", "(none)"]].forEach(([v, label]) =>
    sepSel.appendChild(new Option(label, v)));
  sepSel.addEventListener("change", refresh);
  nameRow.appendChild(el("span", "bulk-label", "separator"));
  nameRow.appendChild(sepSel);
  panel.appendChild(nameRow);

  function addPart(name) {
    const chip = el("span", "gen-chip");
    chip.appendChild(el("span", null, name));
    const x = el("button", "gen-chip-x", "✕");
    x.title = "remove";
    x.addEventListener("click", () => { chip.remove(); refresh(); });
    chip.appendChild(x);
    chip.dataset.token = name;
    partsBox.appendChild(chip);
    refresh();
  }
  ["layout", "key", "seq"].forEach(addPart);        // sensible default pattern

  // --- preview + generate ---------------------------------------------------
  const results = el("div", "gen-results");
  panel.appendChild(results);              // ABOVE the sticky bar, so messages show
  const actions = el("div", "bulk-actions gen-actions");
  const genBtn = el("button", "btn btn-primary gen-go", "Generate");
  actions.appendChild(genBtn);
  const folderNote = el("span", "bulk-label", "");
  actions.appendChild(folderNote);
  panel.appendChild(actions);
  // Both Generate buttons (header + sticky bar) do the same thing.
  topGen.addEventListener("click", () => runGenerate());

  function buildSpec() {
    const spec = {
      count: Number(countInput.value) || 0,
      header_fields: [], detail_fields: [], row_counts: [],
      name_parts: [...partsBox.querySelectorAll(".gen-chip")]
        .map((c) => ({ type: "token", name: c.dataset.token })),
      separator: sepSel.value,
    };
    // The same two boxes carry a numeric range OR a date range. The server
    // reads `from`/`to` for a temporal field and `min`/`max` otherwise, so send
    // whichever this field actually means.
    const rangeOf = (row, cb) => {
      const lo = row.querySelector(".gen-min").value;
      const hi = row.querySelector(".gen-max").value;
      return cb.dataset.date === "1" ? { from: lo, to: hi } : { min: lo, max: hi };
    };
    headerBox.querySelectorAll(".gen-on:checked").forEach((cb) => {
      if (cb.disabled) return;          // locked: never sent, whatever the DOM says
      const row = cb.closest(".gen-field");
      spec.header_fields.push({
        name: cb.dataset.field,
        ...rangeOf(row, cb),
        values: row.querySelector(".gen-list").value,
      });
    });
    panel.querySelectorAll(".gen-detail").forEach((box) => {
      const section = box.dataset.section;
      box.querySelectorAll(".gen-on:checked").forEach((cb) => {
        if (cb.disabled) return;        // locked: never sent
        const row = cb.closest(".gen-field");
        spec.detail_fields.push({
          section, name: cb.dataset.field,
          ...rangeOf(row, cb),
          values: row.querySelector(".gen-list").value,
        });
      });
      const on = box.querySelector(".gen-rows-on");
      if (on && on.checked) {
        spec.row_counts.push({
          section,
          min: box.querySelector(".gen-rows-min").value,
          max: box.querySelector(".gen-rows-max").value,
        });
      }
    });
    return spec;
  }

  function refresh() { clearTimeout(timer); timer = setTimeout(doPreview, 250); }

  async function doPreview() {
    const spec = buildSpec();
    if (!spec.count) { results.innerHTML = ""; return; }
    let pv;
    try {
      pv = await postJSON("/api/generate/preview",
                          { paths, spec });
    } catch (e) {
      results.innerHTML = "";
      results.appendChild(el("div", "bulk-note", "Preview failed: " + e.message));
      return;
    }
    folderNote.textContent =
      `${pv.count} file(s) → ${pv.folder.split(/[\\/]/).pop()}/`;
    if (!armed) {
      genBtn.textContent = `Generate ${pv.count} files`;
      topGen.textContent = `Generate ${pv.count} files`;
    }
    results.innerHTML = "";
    results.appendChild(el("div", "bulk-summary",
      `Preview of the first ${pv.sample.length} of ${pv.count} files — nothing written yet`));
    const multi = (pv.templates || 1) > 1;
    const table = el("table", "bulk-table");
    const cols = ["file name", "key"]
      .concat(multi ? ["from source file"] : [])
      .concat(Object.keys(pv.sample[0] ? pv.sample[0].values : {}),
              Object.keys(pv.sample[0] ? pv.sample[0].rows : {}).map((s) => s + " rows"));
    const thead = el("thead"); const htr = el("tr");
    cols.forEach((c) => htr.appendChild(el("th", null, c)));
    thead.appendChild(htr); table.appendChild(thead);
    const tb = el("tbody");
    pv.sample.forEach((r) => {
      const tr = el("tr", "st-change");
      tr.appendChild(el("td", "mono", r.name));
      tr.appendChild(el("td", "mono", r.key));
      if (multi) tr.appendChild(el("td", "mono", r.template || ""));
      Object.keys(r.values).forEach((k) => tr.appendChild(el("td", "mono", r.values[k])));
      Object.keys(r.rows).forEach((k) => tr.appendChild(el("td", null, String(r.rows[k]))));
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    results.appendChild(table);
  }

  // Confirmation is INLINE, not a native confirm(): browsers let a user tick
  // "prevent this page from creating additional dialogs", after which every
  // confirm() returns false instantly and the click silently does nothing.
  const cancelBtn = el("button", "btn", "Cancel");
  cancelBtn.classList.add("hidden");
  actions.insertBefore(cancelBtn, folderNote);
  function disarm() {
    armed = false;
    clearTimeout(armTimer);
    cancelBtn.classList.add("hidden");
    actions.classList.remove("armed");
    const label = `Generate ${Number(countInput.value) || 0} files`;
    genBtn.textContent = label;
    topGen.textContent = label;
  }
  cancelBtn.addEventListener("click", disarm);

  // A refusal must be visible where the user is looking, not only in the
  // top-right status text.
  function panelMessage(text, kind) {
    results.innerHTML = "";
    const box = el("div", kind === "err" ? "bulk-note gen-err" : "bulk-summary", text);
    results.appendChild(box);
  }

  async function runGenerate() {
    const spec = buildSpec();
    if (!spec.count) {
      panelMessage("Enter how many files to generate.", "err");
      setStatus("Enter how many files to generate", "err");
      return;
    }
    if (!armed) {                       // first click: arm, don't write yet
      armed = true;
      actions.classList.add("armed");
      cancelBtn.classList.remove("hidden");
      genBtn.textContent = `Click again to write ${spec.count} files`;
      topGen.textContent = `Click again to write ${spec.count} files`;
      armTimer = setTimeout(disarm, 10000);
      return;
    }
    clearTimeout(armTimer);
    if (!beginBusy(`Generating ${spec.count} files…`)) {
      panelMessage("Another operation is still running — wait for it to finish, "
                   + "then click Generate again.", "err");
      setStatus("Please wait — an operation is already running…", "dirty");
      disarm();
      return;
    }
    genBtn.disabled = true; topGen.disabled = true; cancelBtn.disabled = true;
    try {
      const res = await postJSON("/api/generate/apply",
                                 { paths, spec });
      setStatus(`Generated ${res.written} file(s)`, "ok");
      activityResult(`Generated ${res.written} files`, "ok");
      panelMessage(`Wrote ${res.written} file(s) into ${res.folder}`);
      // A run that could not apply a field as asked must SAY so. It used to
      // report only the file count, which reads as "everything you asked for".
      (res.no_data || []).forEach((n) =>
        results.appendChild(el("div", "gen-nodata-note", "⚠ " + n.message)));
      await refreshFolder(folderOf(paths[0]));   // the source files' folder
    } catch (e) {
      setStatus("Generate failed: " + e.message, "err");
      panelMessage("Generate failed: " + e.message, "err");
    } finally {
      state.busy = false;
      genBtn.disabled = false; topGen.disabled = false; cancelBtn.disabled = false;
      disarm();
    }
  }
  genBtn.addEventListener("click", runGenerate);

  refresh();
}

// ---- Convert .OK -> Calgary JSON (test data) ----
// The first action that CREATES data rather than preserving it, so it is gated
// behind an explicit acknowledgement (like Send to NiceLabel) and shows exactly
// how much of the output comes from the .OK file vs the vendor template.
async function convertToJson() {
  const paths = [...state.selection];
  if (!paths.length) return;
  let pv;
  try { pv = await postJSON("/api/convert/preview", { paths }); }
  catch (e) { setStatus("Convert failed: " + e.message, "err"); return; }

  const scope = pv.scope || {};
  if (!scope.convertible) {
    const why = (scope.blocked || []).map((b) => b.error)[0] || "no convertible files";
    setStatus("Nothing to convert — " + why, "err");
    return;
  }
  const ok = await confirmConvert(pv);
  if (!ok) return;

  if (!beginBusy("Converting…")) { setStatus("Please wait — an operation is already running…", "dirty"); return; }
  try {
    const res = await postJSON("/api/convert/apply", { paths });
    const er = (res.errors || []).length;
    setStatus(`Converted ${res.written} file(s) to ${res.target} in ${res.folder.split(/[\\/]/).pop()}`
              + (er ? `, ${er} skipped` : ""), er ? "dirty" : "ok");
    showConvertResult(res);
    if (state.rootDir) openFolder(state.rootDir);   // the new folder appears in the tree
  } catch (e) {
    setStatus("Convert failed: " + e.message, "err");
  } finally {
    state.busy = false;
  }
}

function confirmConvert(pv) {
  return new Promise((resolve) => {
    const scope = pv.scope || {};
    const s0 = (pv.samples || [])[0];
    const cov = (s0 && s0.coverage) || {};
    const ov = el("div", "modal-overlay");
    const card = el("div", "modal-card");
    card.appendChild(el("h3", "modal-title",
      `Convert ${scope.convertible} file(s) to ${scope.target}`));

    const box = el("div", "modal-warn");
    box.appendChild(el("span", "modal-warn-icon", "⚠"));
    box.appendChild(el("span", "modal-warn-text",
      "This CREATES new test-data files. Fields the .OK file cannot supply are "
      + "taken from a real vendor sample, so the output is realistic but not a "
      + "faithful record. Source .OK files are never modified."));
    card.appendChild(box);

    if (s0) {
      const tbl = el("div", "tosca-rows");
      // `generated` is named explicitly: a field OkGen invents (a `now` stamp
      // config declares for a field the .OK has no source for) must be visible
      // here, or the one provenance that is neither read nor inherited would be
      // the only one the summary hides.
      tbl.appendChild(el("div", "tosca-row",
        `From the .OK file: ${cov.ok || 0} fields · derived: ${cov.derived || 0} `
        + `· generated: ${cov.generated || 0} · from the template: `
        + `${cov.template || 0}`));
      // The converted file is SCAN because it carries no headerASNid — its own
      // content says so (D38). The output folder's name is only a label; the
      // batch resolves the same after the folder is renamed.
      tbl.appendChild(el("div", "tosca-row",
        `Output will be ${scope.source} — the converted file carries no `
        + `headerASNid, which is what decides it`));
      card.appendChild(tbl);
    }
    (pv.errors || []).slice(0, 5).forEach((e) =>
      card.appendChild(el("div", "tosca-row", `skipped ${e.file}: ${e.error}`)));

    const check = el("label", "modal-check");
    const cb = el("input");
    cb.type = "checkbox";
    check.appendChild(cb);
    check.appendChild(el("span", null,
      "I understand these are generated test files, not a faithful record."));
    card.appendChild(check);

    const acts = el("div", "modal-actions");
    const cancel = el("button", "btn", "Cancel");
    const go = el("button", "btn btn-primary", "Convert");
    go.disabled = true;
    cb.addEventListener("change", () => { go.disabled = !cb.checked; });
    acts.appendChild(cancel); acts.appendChild(go); card.appendChild(acts);
    ov.appendChild(card); document.body.appendChild(ov);
    const done = (v) => { ov.remove(); resolve(v); };
    cancel.addEventListener("click", () => done(false));
    go.addEventListener("click", () => done(true));
    ov.addEventListener("click", (e) => { if (e.target === ov) done(false); });
  });
}

function showConvertResult(res) {
  const ov = el("div", "modal-overlay");
  const card = el("div", "modal-card modal-wide");
  card.appendChild(el("h3", "modal-title", `Converted ${res.written} file(s)`));
  const body = el("div", "modal-body");
  card.appendChild(body);
  body.appendChild(el("div", "modal-dest", res.folder));
  const tbl = el("div", "tosca-rows");
  (res.files || []).forEach((f) =>
    tbl.appendChild(el("div", "tosca-row", `${f.source}  →  ${f.name}`)));
  body.appendChild(tbl);
  (res.errors || []).forEach((e) => {
    const box = el("div", "modal-warn");
    box.appendChild(el("span", "modal-warn-icon", "⚠"));
    box.appendChild(el("span", "modal-warn-text", `${e.file}: ${e.error}`));
    body.appendChild(box);
  });
  // Written, but past Windows' path limit — OkGen can reopen these, other
  // programs may not. A warning, not an error: the files are on disk.
  const long = res.long_paths || [];
  if (long.length) {
    const box = el("div", "modal-warn");
    box.appendChild(el("span", "modal-warn-icon", "⚠"));
    const names = long.map((f) => `${f.name} (${f.length})`).join(", ");
    box.appendChild(el("span", "modal-warn-text",
      `${long.length} file(s) were written to a path longer than Windows' ` +
      `${res.max_path || 260}-character limit, so Explorer or the program that ` +
      `consumes them may not be able to open them. Convert into a folder closer ` +
      `to the drive root, or shorten the file names: ${names}`));
    body.appendChild(box);
  }
  const acts = el("div", "modal-actions");
  const ok = el("button", "btn btn-primary", "Close");
  acts.appendChild(ok); card.appendChild(acts);
  ov.appendChild(card); document.body.appendChild(ov);
  const close = () => ov.remove();
  ok.addEventListener("click", close);
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
}
