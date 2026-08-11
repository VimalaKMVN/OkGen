// A temporal field is labelled by its WIDTH, and hints with its OWN stored shape.
//
// User-reported: `timestamp (date)` in both bulk panels and Volume Generate,
// when every other field reads `name (width)` — so the 30 declared at v0.110.0
// was invisible in exactly the two panels the user looks at. Both panels ran
//
//     f.date ? `${f.name} (date)` : `${f.name} (${f.size})`
//
// so a field carrying a date FORMAT never reached the width half at all. (The
// rows & sequences dropdown and the editor have no such branch, which is where
// v0.110.0's fix was actually visible — PLAN overstated it as universal.)
//
// Two things had to come with the rename, and they are the load-bearing part:
//
// 1. THE OP TYPE WAS DERIVED FROM THE LABEL TEXT. `buildOps` matched
//    /\(date\)/ against the rendered name, so renaming the label would have
//    silently turned a date range into a numeric `random` carrying
//    min: NaN, max: NaN — a wrong op in a bulk WRITE path, produced by a
//    cosmetic edit. It now reads `cb.dataset.date`, as Generate always has.
//    The first check below is the one that would have caught that.
//
// 2. The hint is a SPECIMEN from the server, rendered through the field's own
//    declared format (config/date_fields.yaml takes strftime patterns as well
//    as rfc3339_nano). A hardcoded "2024-06-30" in the client was wrong for
//    `timestamp` the whole time, and would be wrong again the day a "%Y%m%d"
//    field is declared. At the panel's 120px the stamp is clipped by design —
//    the user chose that over widening the box — so the whole value must be
//    reachable on hover, and that is asserted rather than assumed.
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

const STAMP = "2026-01-08T11:36:21.944107946Z";   // what the server renders

let captured = null;
const fetchStub = async (url, opts) => {
  captured = JSON.parse((opts || {}).body || "{}");
  return { ok: true, status: 200, json: async () => ({ results: [] }) };
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderBulkFieldsPanel, renderGeneratePanel };");
let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            fetchStub, () => true, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

const cls = (e, c) => (e.className || "").split(/\s+/).includes(c);
const kids = (e) => Array.prototype.slice.call(e.childNodes || []);
const partOf = (r, c) => kids(r).find((k) => cls(k, c));

// --------------------------------------------------------------------------
// Bulk Edit — field values
// --------------------------------------------------------------------------
const scope = {
  files: [{ path: "/tmp/a.json", name: "a.json", layout: "CalgaryStyleHeader" }],
  layouts: { CalgaryStyleHeader: 1 },
  header_fields: { CalgaryStyleHeader: [
    { name: "department", size: 2 },
    { name: "timestamp", size: 30, date: true, date_example: STAMP },
  ] },
  detail_sections: { CalgaryStyleHeader: [] },
  rollups: {}, sources: {}, key_fields: {},
};

const panel = doc.querySelector("#bulkPanel");
panel.innerHTML = "";
try {
  api.renderBulkFieldsPanel(scope);
} catch (e) {
  console.log("FAIL renderBulkFieldsPanel threw: " + (e && e.message));
  process.exit(1);
}

const rows = descendants(panel).filter((e) => cls(e, "bulkf-field"));
const rowFor = (name) => rows.find((r) => {
  const cb = partOf(r, "bulkf-on");
  return cb && cb.dataset.field === name;
});
const tsRow = rowFor("timestamp");
const deptRow = rowFor("department");
const nameOf = (r) => ((partOf(r, "bulkf-name") || {}).textContent || "").trim();

check("the timestamp row renders at all", !!tsRow);
check("it is labelled with its WIDTH, like every other field",
      nameOf(tsRow) === "timestamp (30)");
check("...and no longer as a bare (date)", !/\(date\)/.test(nameOf(tsRow)));
check("a plain field is unchanged", nameOf(deptRow) === "department (2)");

const tsVals = tsRow && partOf(tsRow, "bulkf-vals");
check("the values box hints with the field's OWN stored shape",
      tsVals && tsVals.placeholder === STAMP);
check("...and the whole stamp is on hover, since 120px clips it",
      tsVals && tsVals.title.indexOf(STAMP) !== -1);
check("the hover text still says a short date may be typed",
      tsVals && /2026-01-08/.test(tsVals.title) && /now/.test(tsVals.title));
check("the range boxes name the specimen too",
      (partOf(tsRow, "bulkf-min") || {}).title.indexOf(STAMP) !== -1
      && (partOf(tsRow, "bulkf-max") || {}).title.indexOf(STAMP) !== -1);
check("a plain field's box is untouched",
      (partOf(deptRow, "bulkf-vals") || {}).placeholder === "value or list");

// --------------------------------------------------------------------------
// THE OP: a date range must still be random_date, not a numeric random
// --------------------------------------------------------------------------
// This is what the label rename would have broken. A label-only assertion above
// would pass while every date bulk edit silently wrote NaN bounds.
const tick = (row, mn, mx) => {
  const cb = partOf(row, "bulkf-on");
  cb.checked = true;
  (cb._handlers.change || []).forEach((f) => f({}));
  const lo = partOf(row, "bulkf-min"), hi = partOf(row, "bulkf-max");
  lo.value = mn; (lo._handlers.input || []).forEach((f) => f({}));
  hi.value = mx; (hi._handlers.input || []).forEach((f) => f({}));
};
tick(tsRow, "2026-01-01", "2026-12-31");
tick(deptRow, "1", "99");
descendants(panel)
  .filter((e) => e.tagName === "BUTTON" && /Preview/.test(e.textContent || ""))
  .forEach((b) => b.click());

setTimeout(() => {
  const ops = (captured || {}).ops || [];
  const ts = ops.find((o) => o.field === "timestamp");
  const dept = ops.find((o) => o.field === "department");
  check("a date range still emits random_date", !!ts && ts.type === "random_date");
  check("...carrying from/to, not min/max",
        !!ts && ts.from === "2026-01-01" && ts.to === "2026-12-31"
        && ts.min === undefined && ts.max === undefined);
  // Asserted as NULL, not as NaN: `Number("2026-01-01")` is NaN, and
  // JSON.stringify turns NaN into null, so by the time the body is on the wire
  // the NaN is gone and a `typeof === "number"` test could never fail. Verified
  // against a deliberately-broken build — with the label routing restored, this
  // op arrives as {"type":"random","min":null,"max":null}.
  check("...and no bound arrives as a null the server would have to guess at",
        !!ts && ts.min === undefined && ts.max === undefined
        && !Object.keys(ts).some((k) => ts[k] === null));
  check("a numeric field still emits a numeric random",
        !!dept && dept.type === "random" && dept.min === 1 && dept.max === 99);

  // ------------------------------------------------------------------------
  // Volume Generate — same label, same specimen
  // ------------------------------------------------------------------------
  const gpanel = doc.querySelector("#generatePanel");
  gpanel.innerHTML = "";
  try {
    api.renderGeneratePanel(gpanel, ["/tmp/a.json"], {
      path: "/tmp/a.json", name: "a.json", layout: "CalgaryStyleHeader",
      key_field: "headerASNid", key_size: 17, max_count: 5000,
      header_fields: [{ name: "department", size: 2 },
                      { name: "timestamp", size: 30, date: true, date_example: STAMP }],
      sections: [], palette: {}, rollups: {},
    });
  } catch (e) {
    console.log("FAIL renderGeneratePanel threw: " + (e && e.message));
    process.exit(1);
  }
  const grows = descendants(gpanel).filter((e) => cls(e, "gen-field"));
  const gts = grows.find((r) => {
    const cb = partOf(r, "gen-on");
    return cb && cb.dataset.field === "timestamp";
  });
  const gname = ((partOf(gts, "gen-name") || {}).textContent || "").trim();
  check("Generate labels it with its width too", gname === "timestamp (30)");
  check("...and not as a bare (date)", !/\(date\)/.test(gname));
  check("Generate's list box shows the same specimen",
        (partOf(gts, "gen-list") || {}).placeholder === STAMP);
  check("...with the full stamp on hover",
        (partOf(gts, "gen-list") || {}).title.indexOf(STAMP) !== -1);
  check("Generate still routes it as a date (dataset.date)",
        (partOf(gts, "gen-on") || {}).dataset.date === "1");

  // ------------------------------------------------------------------------
  // A scope with no specimen must degrade to a word, never to `undefined`
  // ------------------------------------------------------------------------
  // An older server, or a format that renders empty, must not print the string
  // "undefined" into a placeholder — the D43-class failure of an empty segment
  // travelling into the UI.
  panel.innerHTML = "";
  const bare = JSON.parse(JSON.stringify(scope));
  bare.header_fields.CalgaryStyleHeader.forEach((f) => { delete f.date_example; });
  api.renderBulkFieldsPanel(bare);
  const bareTs = descendants(panel).filter((e) => cls(e, "bulkf-field"))
    .map((r) => partOf(r, "bulkf-vals"))
    .filter(Boolean)
    .find((v) => v.placeholder !== "value or list");
  check("no specimen falls back to a word", bareTs && bareTs.placeholder === "a date");
  check("...and never prints `undefined`",
        !!bareTs && !/undefined/.test(bareTs.placeholder + " " + (bareTs.title || "")));

  process.exit(failures ? 1 : 0);
}, 10);
