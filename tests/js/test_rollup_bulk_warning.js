// The roll-up rule (D58), stated UP FRONT on the two bulk paths.
//
// In the editor the rule is visible while you type — the badge tracks the rows
// live. Bulk Edit and Volume Generate have no such feedback: the value is typed
// once and applied to a whole selection, so by the time the preview shows the
// sum the user has already built the operation. Both panels therefore warn the
// moment a roll-up field is picked, with one sentence naming the control that
// DOES change the total (Size › qty).
//
// The client must never guess which field is a roll-up — both panels read the
// spec the server sends with their scope. A panel that hardcoded `tot_qty`
// would go silent the day a second roll-up is configured, which is exactly the
// drift config-driven behaviour exists to prevent.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

global.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderBulkFieldsPanel, renderBulkFieldsTable, renderGeneratePanel, rollupSpecFor, rollupWarning };");

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

const ROLLUP = { field: "tot_qty", section: "Size", source: "qty" };

// --------------------------------------------------------------------------
// The wording itself — built from config, so a future roll-up words itself
// --------------------------------------------------------------------------
const msg = api.rollupWarning(ROLLUP);
check("the warning names the field and where its value comes from",
      /tot_qty is the sum of the size lines/i.test(msg));
check("...says which files DO take the typed value",
      /only files with no size lines take this value/i.test(msg));
check("...and points at the control that actually changes it",
      /set Size › qty instead/i.test(msg));

const other = api.rollupWarning({ field: "total", section: "Lane", source: "units" });
check("every word comes from the spec, not from hardcoded StyleHeader terms",
      /total is the sum of the lane lines/i.test(other)
      && /set Lane › units instead/i.test(other));

// A spec is found in Bulk's {layout: [...]} map and in Generate's plain list.
check("a roll-up field is recognised from the bulk scope map",
      !!api.rollupSpecFor({ StyleHeader: [ROLLUP] }, "StyleHeader", "tot_qty"));
check("a roll-up field is recognised from the generate scope list",
      !!api.rollupSpecFor([ROLLUP], "StyleHeader", "tot_qty"));
check("a plain field is not mistaken for one",
      !api.rollupSpecFor({ StyleHeader: [ROLLUP] }, "StyleHeader", "dept"));
check("a layout with no roll-up configured stays silent",
      !api.rollupSpecFor({}, "Preticket", "tot_qty"));

// --------------------------------------------------------------------------
// Bulk Edit — FIELD VALUES: the note sits on the roll-up field's own row
//
// `tot_qty` is a HEADER field, and header field values moved to the multi-field
// panel; the rows & sequences panel keeps only unique / add / keep, which are
// row operations and have no header section at all. So the warning moved with
// the field — it must be on the tot_qty row here, and nowhere near `dept`.
// --------------------------------------------------------------------------
const bulkScope = {
  files: [{ path: "/tmp/SH.OK", name: "SH.OK", layout: "StyleHeader", chain: "03" }],
  layouts: { StyleHeader: 1 },
  header_fields: {
    // `chain` carries an option map on purpose: the panel must NOT turn it
    // into a picker, and a fixture without options could not prove that.
    StyleHeader: [{ name: "tot_qty", size: 7 }, { name: "dept", size: 2 },
                  { name: "chain", size: 2, options: { "01": "TJMAXX", "03": "Homegoods" } }],
  },
  detail_sections: {
    StyleHeader: [{ name: "Size", fields: [{ name: "qty", size: 5 }],
                    max_records: null, count_field: null }],
  },
  rollups: { StyleHeader: [ROLLUP] },
};

const bulkPanel = doc.querySelector("#bulkPanel");
try {
  api.renderBulkFieldsPanel(bulkScope);
} catch (e) {
  console.error("FAIL: renderBulkFieldsPanel threw:", e.message);
  process.exit(1);
}

const rowsOf = () => descendants(bulkPanel)
  .filter((e) => e.classList && e.classList.contains("bulkf-field"));
const notesOf = () => descendants(bulkPanel)
  .filter((e) => e.classList && e.classList.contains("bulk-rollup-note"));

check("the field-values panel renders a row per field", rowsOf().length === 4);

// NO pickers here, deliberately. Volume Generate offers none either, and the
// known-value lists are inconsistent across layouts today — a picker on one
// panel and not the other reads as a bug rather than a feature.
const panelKids = descendants(bulkPanel);
check("no <select> anywhere in the field-values panel",
      !panelKids.some((e) => e.tagName === "SELECT"));
check("no <datalist> suggestions either",
      !panelKids.some((e) => e.tagName === "DATALIST"));
check("...and no value input carries a `list` attribute",
      !panelKids.some((e) => e.getAttribute && e.getAttribute("list")));
check("exactly ONE roll-up note is rendered", notesOf().length === 1);
check("...carrying the rule and what to do about it",
      notesOf()[0] && /sum of the size lines/i.test(notesOf()[0].textContent)
      && /set Size › qty instead/i.test(notesOf()[0].textContent));

const totRow = rowsOf().find((r) => descendants(r).some(
  (c) => c.dataset && c.dataset.field === "tot_qty"));
const deptRow = rowsOf().find((r) => descendants(r).some(
  (c) => c.dataset && c.dataset.field === "dept"));
// The note is a SIBLING of its row, not a child of it — a block element inside
// the flex row needed `flex-wrap: wrap`, and that wrap is what let the ROW
// itself break across two lines. Which field it belongs to is now carried by
// position: it is the element immediately after that field's row.
const afterRow = (row) => {
  if (!row || !row.parentNode) return null;
  const sibs = row.parentNode.childNodes;
  const i = Array.prototype.indexOf.call(sibs, row);
  return i >= 0 ? sibs[i + 1] || null : null;
};
const isNote = (n) => !!(n && n.classList && n.classList.contains("bulk-rollup-note"));
check("the note is not INSIDE any field row",
      !rowsOf().some((r) => descendants(r).some(isNote)));
check("the note follows the tot_qty row", isNote(afterRow(totRow)));
check("...and does NOT follow a plain field's row", !isNote(afterRow(deptRow)));

// --------------------------------------------------------------------------
// The RESULT line: `(sum of N size lines)` is the shipped phrase, identical to
// the editor badge and the single-op bulk preview. One rule met in three
// places must not read three different ways — which it did until now.
// --------------------------------------------------------------------------
const host = doc.createElement("div");
api.renderBulkFieldsTable(host, [{
  name: "SH.OK", status: "change", path: "/tmp/SH.OK",
  fields: [
    { section: "Header", field: "tot_qty", status: "change",
      before: "0000022", after: "0000008", rows: 1, moved: 1, varies: false,
      rollup: { reason: "sum", rows: 4, section: "Size", typed: "0000500" } },
    { section: "Size", field: "qty", status: "change",
      before: "00002", after: "00125", rows: 4, moved: 4, varies: false },
  ],
}], false);
const lineText = descendants(host)
  .filter((e) => e.classList && e.classList.contains("bulkf-line"))
  .map((e) => e.textContent);

check("the roll-up line uses the SHIPPED parenthetical",
      lineText.some((t) => /\(sum of 4 size lines\)/.test(t)));
check("...and names the value that was discarded",
      lineText.some((t) => /your 0000500 was not used/.test(t)));
check("...showing what will really land, not what was typed",
      lineText.some((t) => /tot_qty: 0000022 → 0000008/.test(t)));
check("a plain field keeps its transition and row count",
      lineText.some((t) => /qty: 00002 → 00125/.test(t) && /4\/4 rows/.test(t)));
check("the roll-up line is marked so it reads as a correction",
      descendants(host).some((e) => e.classList
        && e.classList.contains("bulkf-roll")));

// --------------------------------------------------------------------------
// Volume Generate: the note is tied to the field's checkbox
// --------------------------------------------------------------------------
const genScope = {
  path: "/tmp/SH.OK", name: "SH.OK", layout: "StyleHeader",
  key_field: "keytrol", key_size: 6, max_count: 5000,
  header_fields: [{ name: "tot_qty", size: 7 }, { name: "dept", size: 2 }],
  sections: [{ name: "Size", rows: 4, max_records: null,
               fields: [{ name: "qty", size: 5 }] }],
  palette: { derived: [], header_fields: [], custom: {} },
  template_count: 1,
  default_folder: "/tmp/gen_0",
  rollups: [ROLLUP],
};

const genPanel = doc.querySelector("#generatePanel");
try {
  api.renderGeneratePanel(genPanel, [genScope.path], genScope);
} catch (e) {
  console.error("FAIL: renderGeneratePanel threw:", e.message);
  process.exit(1);
}

const genNotes = descendants(genPanel)
  .filter((e) => e.classList && e.classList.contains("gen-rollup-note"));
check("Generate renders one note, for the roll-up field only",
      genNotes.length === 1);
check("...hidden until the field is actually ticked",
      genNotes[0] && genNotes[0].style.display === "none");

const genRow = descendants(genPanel)
  .filter((e) => e.classList && e.classList.contains("gen-field"))
  .find((r) => r.children.some((c) => c.dataset && c.dataset.field === "tot_qty"));
check("the tot_qty row was found", !!genRow);
if (genRow) {
  const cb = genRow.children.find((c) => c.classList && c.classList.contains("gen-on"));
  cb.checked = true;
  cb.dispatchEvent({ type: "change" });
  check("...shown once tot_qty is ticked for randomizing",
        genNotes[0].style.display !== "none");
  check("...carrying the same sentence as Bulk Edit",
        /sum of the size lines/i.test(genNotes[0].textContent)
        && /set Size › qty instead/i.test(genNotes[0].textContent));
  cb.checked = false;
  cb.dispatchEvent({ type: "change" });
  check("...and hidden again when it is unticked",
        genNotes[0].style.display === "none");
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
