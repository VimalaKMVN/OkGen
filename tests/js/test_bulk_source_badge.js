// Bulk Edit — field values: the SCAN/WMS split of the selected files.
//
// On a Calgary StyleHeader or DistLabel the source is not decoration — it is
// what DECIDES the key: `keytrol` for SCAN, `headerASNid` for WMS. The server
// resolves it per FILE (from that file's own `headerASNid`) and greys whichever
// field is the key; without the source stated beside it, a greyed `keytrol` on
// a SCAN selection reads as arbitrary.
//
// The bug this pins: `bulk_scope` used to ask for the key by LAYOUT, with no
// file in hand, so it got the configured default (WMS) for everything — and on
// a SCAN selection greyed `headerASNid` while leaving `keytrol`, the real key,
// editable. The panel could not have shown the difference because the server
// never sent it.
//
// The client renders what the server sends and NEVER derives a source or a key
// from a file name — that is the whole point of D38.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderBulkFieldsPanel };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            global.fetch, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.error("FAIL: app.js threw while loading:", e.message);
  process.exit(1);
}

let failures = 0;
function check(label, cond) {
  console.log((cond ? "ok   " : "FAIL ") + label);
  if (!cond) failures++;
}

const panel = doc.querySelector("#bulkPanel");
const badges = () => descendants(panel)
  .filter((e) => e.classList && e.classList.contains("src-badge"));
const sourceBox = () => descendants(panel)
  .find((e) => e.classList && e.classList.contains("bulk-sources"));
// The stub's `textContent` is an element's OWN text, not its descendants' — a
// real browser aggregates, so read the subtree explicitly rather than writing
// an assertion that passes here for the wrong reason.
const sourceText = () => descendants(sourceBox() || {})
  .map((e) => e.textContent || "").join(" ");
const lockedNames = () => descendants(panel)
  .filter((e) => e.classList && e.classList.contains("bulkf-lockreason"))
  .map((e) => e.textContent);

function fields(names) {
  return names.map((n) => (typeof n === "string" ? { name: n, size: 6 } : n));
}

// --------------------------------------------------------------------------
// A pure-SCAN selection: keytrol is the key, headerASNid is an ordinary field
// --------------------------------------------------------------------------
const scanScope = {
  files: [{ name: "a.json", layout: "CalgaryStyleHeader", source: "SCAN" },
          { name: "b.json", layout: "CalgaryStyleHeader", source: "SCAN" }],
  layouts: { CalgaryStyleHeader: 2 },
  sources: { CalgaryStyleHeader: { SCAN: 2 } },
  key_fields: { CalgaryStyleHeader: {
    keytrol: "the unique key for these SCAN files — use Make keys unique" } },
  header_fields: { CalgaryStyleHeader: fields([
    { name: "keytrol", size: 9, editable: false,
      locked_reason: "the unique key for these SCAN files — use Make keys unique" },
    "headerASNid", "dept"]) },
  detail_sections: { CalgaryStyleHeader: [] },
  rollups: {},
};

api.renderBulkFieldsPanel(scanScope);

check("the panel states the source of the selected files",
      badges().length === 1 && /SCAN \(2\)/.test(badges()[0].textContent));
check("...using the same badge class as the tree, so they look alike",
      badges()[0].classList.contains("src-badge-scan"));
check("...and names the key that follows from it",
      /key: keytrol/.test(sourceText()));
check("the greyed field's reason names the SOURCE, not just 'the unique key'",
      lockedNames().length === 1 && /SCAN/.test(lockedNames()[0]));
check("headerASNid is NOT greyed on a SCAN selection — it is not the key here",
      !lockedNames().some((t) => /headerASNid/.test(t))
      && descendants(panel).some((e) => e.dataset && e.dataset.field === "headerASNid"
                                        && e.disabled !== true));

// --------------------------------------------------------------------------
// The mirror: a pure-WMS selection
// --------------------------------------------------------------------------
const wmsScope = {
  files: [{ name: "a.json", layout: "CalgaryDistLabel", source: "WMS" }],
  layouts: { CalgaryDistLabel: 1 },
  sources: { CalgaryDistLabel: { WMS: 1 } },
  key_fields: { CalgaryDistLabel: {
    headerASNid: "the unique key for these WMS files — use Make keys unique" } },
  header_fields: { CalgaryDistLabel: fields([
    "keytrol",
    { name: "headerASNid", size: 9, editable: false,
      locked_reason: "the unique key for these WMS files — use Make keys unique" }]) },
  detail_sections: { CalgaryDistLabel: [] },
  rollups: {},
};

api.renderBulkFieldsPanel(wmsScope);
check("a WMS selection badges WMS", badges().length === 1
      && /WMS \(1\)/.test(badges()[0].textContent)
      && badges()[0].classList.contains("src-badge-wms"));
check("...and greys headerASNid instead", lockedNames().length === 1
      && /WMS/.test(lockedNames()[0]));

// --------------------------------------------------------------------------
// A MIXED selection — both fields are a key for SOME of the files
// --------------------------------------------------------------------------
const mixedScope = {
  files: [{ name: "s.json", layout: "CalgaryStyleHeader", source: "SCAN" },
          { name: "w.json", layout: "CalgaryStyleHeader", source: "WMS" }],
  layouts: { CalgaryStyleHeader: 2 },
  sources: { CalgaryStyleHeader: { SCAN: 1, WMS: 1 } },
  key_fields: { CalgaryStyleHeader: {
    keytrol: "the unique key for the SCAN files in this selection — use Make keys unique",
    headerASNid: "the unique key for the WMS files in this selection — use Make keys unique" } },
  header_fields: { CalgaryStyleHeader: fields([
    { name: "keytrol", size: 9, editable: false,
      locked_reason: "the unique key for the SCAN files in this selection — use Make keys unique" },
    { name: "headerASNid", size: 9, editable: false,
      locked_reason: "the unique key for the WMS files in this selection — use Make keys unique" }]) },
  detail_sections: { CalgaryStyleHeader: [] },
  rollups: {},
};

api.renderBulkFieldsPanel(mixedScope);
check("a mixed selection shows BOTH sources with their counts",
      badges().length === 2
      && /SCAN \(1\)/.test(badges()[0].textContent)
      && /WMS \(1\)/.test(badges()[1].textContent));
check("...and says both fields are locked because both are keys here",
      /keytrol and headerASNid are both keys/.test(sourceText()));
check("...with both rows greyed", lockedNames().length === 2);

// --------------------------------------------------------------------------
// An .OK selection has no source at all — the box must not appear empty
// --------------------------------------------------------------------------
const okScope = {
  files: [{ name: "SH.OK", layout: "StyleHeader", source: null }],
  layouts: { StyleHeader: 1 },
  sources: {},
  key_fields: { StyleHeader: { keytrol: "the unique key — use Make keys unique" } },
  header_fields: { StyleHeader: fields([
    { name: "keytrol", size: 6, editable: false,
      locked_reason: "the unique key — use Make keys unique" }, "dept"]) },
  detail_sections: { StyleHeader: [] },
  rollups: {},
};

api.renderBulkFieldsPanel(okScope);
check("an .OK selection shows no source badge", badges().length === 0);
check("...and the source row is hidden rather than left blank",
      sourceBox() && sourceBox().hidden === true);
check("...while the key is still greyed, with no source in the reason",
      lockedNames().length === 1 && !/SCAN|WMS/.test(lockedNames()[0]));

// A scope from an older server (no `sources`/`key_fields`) must still render.
const legacy = {
  files: [{ name: "SH.OK", layout: "StyleHeader" }],
  layouts: { StyleHeader: 1 },
  header_fields: { StyleHeader: fields(["dept"]) },
  detail_sections: { StyleHeader: [] },
  rollups: {},
};
let threw = false;
try { api.renderBulkFieldsPanel(legacy); } catch (e) { threw = true; }
check("a scope missing the new keys does not throw", !threw && badges().length === 0);

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
