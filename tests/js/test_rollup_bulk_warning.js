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
  src + "\n;return { renderBulkPanel, renderGeneratePanel, rollupSpecFor, rollupWarning };");

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
// Bulk Edit: the note appears when tot_qty is the selected header field
// --------------------------------------------------------------------------
const bulkScope = {
  files: [{ path: "/tmp/SH.OK", name: "SH.OK", layout: "StyleHeader", chain: "03" }],
  layouts: { StyleHeader: 1 },
  header_fields: {
    StyleHeader: [{ name: "tot_qty", size: 7 }, { name: "dept", size: 2 }],
  },
  detail_sections: {
    StyleHeader: [{ name: "Size", fields: [{ name: "qty", size: 5 }],
                    max_records: null, count_field: null }],
  },
  rollups: { StyleHeader: [ROLLUP] },
};

const bulkPanel = doc.querySelector("#bulkPanel");
try {
  api.renderBulkPanel(bulkScope);
} catch (e) {
  console.error("FAIL: renderBulkPanel threw:", e.message);
  process.exit(1);
}

const noteOf = () => descendants(bulkPanel)
  .filter((e) => e.classList && e.classList.contains("bulk-rollup-note"))[0];

check("Bulk Edit renders a note element", !!noteOf());
check("...warning as soon as tot_qty is the selected field",
      noteOf() && /sum of the size lines/i.test(noteOf().textContent));

// Switching to a field that is NOT a roll-up must clear it — a warning left
// standing beside `dept` would be worse than no warning at all.
const fieldSel = descendants(bulkPanel)
  .filter((e) => e.classList && e.classList.contains("bulk-field")
                 && (e.options || []).some((o) => o.value === "tot_qty"))[0];
check("the header field selector was found", !!fieldSel);
if (fieldSel) {
  fieldSel.value = "dept";
  fieldSel.dispatchEvent({ type: "change" });
  check("...and it clears when a plain field is selected",
        noteOf() && noteOf().textContent === "");
  fieldSel.value = "tot_qty";
  fieldSel.dispatchEvent({ type: "change" });
  check("...and comes back when the roll-up field is selected again",
        noteOf() && /sum of the size lines/i.test(noteOf().textContent));
}

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
