// Both Bulk Edit panels warn UP FRONT when a section holds no rows with data.
//
// Volume Generate has warned in its panel since v0.113.0. Bulk skipped the
// field correctly but said nothing until AFTER the apply — by which point the
// whole selection had been processed. `bulk_scope` now carries the counts and
// these are the two places that read them.
//
// The wording is COUNT-based because blankness is per FILE: a section can be
// blank in some of a selection and full in the rest, and one ticked field
// applies to all of them.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

let failures = 0;
function check(label, cond) {
  console.log((cond ? "ok   " : "FAIL ") + label);
  if (!cond) failures++;
}

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderBulkFieldsPanel, renderBulkPanel };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => new Promise(() => {}), () => true, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

// Four files of one layout. `Sizes` is blank in 2 of them, `Lanes` in all 4,
// `Details` in none — the three cases the wording has to tell apart.
const scope = {
  files: [0, 1, 2, 3].map((i) => ({ path: `/tmp/f${i}.json`, name: `f${i}.json`,
                                    layout: "CalgaryStyleHeader", chain: "04",
                                    source: "WMS" })),
  layouts: { CalgaryStyleHeader: 4 },
  header_fields: { CalgaryStyleHeader: [{ name: "department", size: 2 }] },
  detail_sections: {
    CalgaryStyleHeader: [
      { name: "Lanes", max_records: null, no_data_files: 4, files: 4,
        has_data: false, fields: [{ name: "lane", size: 8 }] },
      { name: "Sizes", max_records: null, no_data_files: 2, files: 4,
        has_data: true, fields: [{ name: "size", size: 6 }] },
      { name: "Details", max_records: null, no_data_files: 0, files: 4,
        has_data: true, fields: [{ name: "style", size: 6 }] },
    ],
  },
  rollups: {}, sources: {}, key_fields: {},
};

const cls = (e, c) => (e.className || "").split(/\s+/).includes(c);
const notesIn = (host) => descendants(host)
  .filter((e) => cls(e, "bulk-nodata-note"))
  .map((e) => e.textContent || "");

// --------------------------------------------------------------------------
// Panel 1 — Bulk Edit — field values
// --------------------------------------------------------------------------
const p1 = doc.querySelector("#bulkPanel");
p1.innerHTML = "";
try {
  api.renderBulkFieldsPanel(scope);
} catch (e) {
  console.log("FAIL renderBulkFieldsPanel threw: " + (e && e.message));
  process.exit(1);
}
const notes1 = notesIn(p1);

check("the field-values panel warns about a blank section", notes1.length > 0);
check("a section blank in EVERY file says so",
      notes1.some((t) => /Lanes/.test(t) && /all 4 files/.test(t)));
check("a section blank in SOME files gives the count",
      notes1.some((t) => /Sizes/.test(t) && /2 of 4 files/.test(t)));
check("a section with data everywhere is not warned about",
      !notes1.some((t) => /Details/.test(t)));
check("exactly the two affected sections are flagged", notes1.length === 2);
check("the note says the value will be SKIPPED",
      notes1.every((t) => /skipped/i.test(t)));
check("...and names the remedy",
      notes1.every((t) => /add rows/i.test(t)));

// A warning must not disable anything — writing is still legitimate elsewhere
// in the selection, and the row ops are the way out.
const boxes = descendants(p1).filter((e) => e.dataset && e.dataset.section === "Sizes");
check("nothing is disabled by the warning",
      descendants(p1).filter((e) => e.tagName === "INPUT" && e.disabled
                                    && e.type === "checkbox").length === 0);

// --------------------------------------------------------------------------
// Panel 2 — Bulk Edit — rows & sequences
// --------------------------------------------------------------------------
const p2 = doc.querySelector("#bulkPanel2");
p2.innerHTML = "";
let threw = null;
try {
  api.renderBulkPanel(scope);
} catch (e) {
  threw = e;
}
check("the rows & sequences panel renders", !threw);

// It follows the SECTION dropdown, so whichever section is selected first is
// the one described. The panel picks the first section, which is Lanes here.
const host = doc.querySelector("#bulkPanel");
const notes2 = notesIn(host).concat(notesIn(p2));
check("it warns for the section it opens on",
      notes2.some((t) => /Lanes/.test(t)));

// --------------------------------------------------------------------------
// A scope with no counts at all must not produce a note
// --------------------------------------------------------------------------
const bare = JSON.parse(JSON.stringify(scope));
bare.detail_sections.CalgaryStyleHeader.forEach((d) => {
  delete d.no_data_files; delete d.files; delete d.has_data;
});
const p3 = doc.querySelector("#bulkPanel3");
p3.innerHTML = "";
doc.querySelector("#bulkPanel").innerHTML = "";
api.renderBulkFieldsPanel(bare);
check("an older scope without the counts renders no note, not a broken one",
      notesIn(doc.querySelector("#bulkPanel")).length === 0);

process.exit(failures ? 1 : 0);
