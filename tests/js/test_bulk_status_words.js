// The rows & sequences panel must READ as words, not as status tokens.
//
// `renderBulkTable` printed `r.status` straight into the Status column, so a
// file whose section had no rows showed the bare word `no_section` with an
// EMPTY Change column — nothing about what happened or what to do. The
// multi-field panel had this fixed long ago; this is the same class surviving
// in the other panel, found while checking what an emptied section does on the
// `.OK` side.
//
// Two halves, and only the first is visible to a server test: the SENTENCE
// comes from the server's `detail`, the WORD is mapped here.
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
  src + "\n;return { renderBulkTable };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

// Every status the single-op path can return, as the server returns it.
const RESULTS = [
  { name: "a.OK", status: "changed", detail: "set lane1 on 10/10 row(s)" },
  { name: "b.OK", status: "no_section",
    detail: "no Lane rows in this file — nothing to change. Add rows to Lane first." },
  { name: "c.json", status: "no_data",
    detail: "Sizes has no rows with data — size skipped. Add rows to Sizes first, then set its fields." },
  { name: "d.OK", status: "missing_field", detail: "no field 'nope' in Size on StyleHeader" },
  { name: "e.OK", status: "too_wide", detail: "value too long for size" },
  { name: "f.OK", status: "unchanged" },
];

const host = doc.createElement("div");
api.renderBulkTable(host, RESULTS, true);

const rows = descendants(host).filter((e) => e.tagName === "TR").slice(1);
const cells = rows.map((tr) =>
  descendants(tr).filter((e) => e.tagName === "TD").map((e) => e.textContent || ""));
const statusCol = cells.map((c) => c[2]);
const changeCol = cells.map((c) => c[1]);
const summary = descendants(host)
  .filter((e) => (e.className || "").includes("bulk-summary"))
  .map((e) => e.textContent || "")[0] || "";

// A status is a value the code branches on. None may reach the screen.
const TOKENS = ["no_section", "no_data", "missing_field", "too_wide"];

check("a row is rendered for every result", rows.length === RESULTS.length);
check("no raw status token appears in the Status column",
      !statusCol.some((s) => TOKENS.some((t) => s.includes(t))));
check("no raw status token appears in the SUMMARY either",
      !TOKENS.some((t) => summary.includes(t)));
check("the two 'nothing happened' statuses both read as skipped",
      statusCol[1] === "skipped" && statusCol[2] === "skipped");
check("a width failure says so in words", statusCol[4] === "too long");
check("a missing field says so in words", statusCol[3] === "no such field");
check("a real change still reads as changed", statusCol[0] === "changed");
check("an untouched file still reads as unchanged", statusCol[5] === "unchanged");

// The WORD alone is not enough — the explanation has to be on screen too.
check("the Change column carries the server's sentence",
      changeCol[1].includes("Add rows to Lane"));
check("...for the blank-section case as well",
      changeCol[2].includes("Add rows to Sizes"));
check("a skipped row is never left with an empty explanation",
      changeCol[1].trim() !== "" && changeCol[2].trim() !== "");

// An unmapped status must still show SOMETHING, or a new one goes invisible.
const host2 = doc.createElement("div");
api.renderBulkTable(host2, [{ name: "z.OK", status: "brand_new_status" }], true);
const fallback = descendants(host2).filter((e) => e.tagName === "TR").slice(1)
  .map((tr) => descendants(tr).filter((e) => e.tagName === "TD")
    .map((e) => e.textContent || "")[2])[0];
check("an unmapped status falls through to itself rather than vanishing",
      fallback === "brand_new_status");

process.exit(failures ? 1 : 0);
