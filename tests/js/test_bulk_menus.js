// The two menus that offer bulk actions must agree about which bulk EDITS exist.
//
// User-reported: right-clicking a file showed a single "Bulk Edit", and taking
// it always landed on field values — "no way to go to Bulk rows options through
// right-click". The Bulk Actions dropdown had carried both since the D59 split,
// so the two menus had drifted, and the right-click entry called
// `enterBulkMode()` with NO argument, which defaults to "fields".
//
// This is not cosmetic. Field edits and row ops are deliberately different
// tools (D59) — field edits are order-independent and batch into one write, row
// ops are not — so one entry cannot stand for both, and the mode that was
// unreachable was the one that empties sections and renumbers sequences.
//
// The drift is the real defect, so this suite compares the two menus rather
// than checking either alone: a future entry added to one and not the other
// fails here.
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
  src + "\n;return { state, showCtxMenu, showBulkMenu, getBulkMode: () => bulkMode };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => new Promise(() => {}),      // never resolves: no async side effects
            () => true, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

// showBulkMenu positions itself against the button; the stub has no layout.
doc.querySelector("#bulkBtn").getBoundingClientRect =
  () => ({ left: 0, top: 0, bottom: 0, right: 0, width: 0, height: 0 });

const FILE = "C:/data/SH_0001.OK";
function select(paths) {
  api.state.selection = new Set(paths);
  api.state.clipboard = api.state.clipboard || [];
}

function itemsOf(build) {
  const menu = doc.querySelector("#ctxMenu");
  menu.innerHTML = "";
  build();
  return descendants(menu).filter((e) => (e.className || "").includes("ctx-item"));
}

const ctxItems = () => itemsOf(() =>
  api.showCtxMenu({ preventDefault() {}, clientX: 10, clientY: 10 },
                  { path: FILE }, null));
const bulkItems = () => itemsOf(() => api.showBulkMenu());

// --------------------------------------------------------------------------
// The report: both bulk modes reachable from the right-click menu
// --------------------------------------------------------------------------
select([FILE]);
const ctx = ctxItems();
const labels = ctx.map((e) => e.textContent || "");

check("the right-click menu offers FIELD VALUES",
      labels.some((l) => /Bulk Edit — field values/.test(l)));
check("the right-click menu offers ROWS & SEQUENCES",
      labels.some((l) => /Bulk Edit — rows & sequences/.test(l)));
check("no bare 'Bulk Edit' entry is left to be ambiguous",
      !labels.some((l) => /^Bulk Edit(\s*\(\d+\))?$/.test(l.trim())));

// --------------------------------------------------------------------------
// Each entry actually reaches its OWN mode
// --------------------------------------------------------------------------
// The old entry was present and clickable — it simply always meant "fields".
// A label assertion alone would have passed the whole time it was broken, so
// the modes are read back from the app's own state.
function clickMode(items, re) {
  const item = items.filter((e) => re.test(e.textContent || ""))[0];
  if (!item) return null;
  try { (item._handlers.click || []).forEach((fn) => fn({})); } catch (_) {}
  return api.getBulkMode();
}

select([FILE]);
check("right-click › field values opens the FIELD panel",
      clickMode(ctxItems(), /field values/) === "fields");
select([FILE]);
check("right-click › rows & sequences opens the ROW panel",
      clickMode(ctxItems(), /rows & sequences/) === "rows");
select([FILE]);
check("the two entries do not both open the same panel",
      clickMode(ctxItems(), /field values/)
      !== (select([FILE]), clickMode(ctxItems(), /rows & sequences/)));

// --------------------------------------------------------------------------
// The two menus agree about the bulk EDIT modes
// --------------------------------------------------------------------------
select([FILE, "C:/data/SH_0002.OK"]);
const bulkLabels = bulkItems().map((e) => e.textContent || "");
const ctxLabels2 = ctxItems().map((e) => e.textContent || "");
const modeRe = /Bulk Edit — (field values|rows & sequences)/;
const modesIn = (ls) => ls.filter((l) => modeRe.test(l))
                          .map((l) => l.match(modeRe)[1]).sort();

check("the Bulk Actions dropdown still offers both",
      modesIn(bulkLabels).length === 2);
check("both menus offer exactly the same bulk edit modes",
      JSON.stringify(modesIn(ctxLabels2)) === JSON.stringify(modesIn(bulkLabels)));
check("both count the selection in their labels",
      ctxLabels2.some((l) => /field values \(2\)/.test(l))
      && bulkLabels.some((l) => /field values \(2\)/.test(l)));

// A single file drops the count in the right-click menu, which is its own
// convention ("Bulk Edit", not "Bulk Edit (1)") — keep it through the split.
select([FILE]);
const one = ctxItems().map((e) => e.textContent || "");
check("one file reads without a count",
      one.some((l) => l.trim() === "Bulk Edit — field values")
      && one.some((l) => l.trim() === "Bulk Edit — rows & sequences"));

// --------------------------------------------------------------------------
// Run TOSCA belongs on BOTH menus
// --------------------------------------------------------------------------
// It was offered only from the right-click menu, by omission rather than
// design — a run works on any selection, so there is no reason it should be
// reachable from one entry point and not the other.
select([FILE]);
const c1 = ctxItems().map((e) => e.textContent || "");
const b1 = bulkItems().map((e) => e.textContent || "");
const has = (ls, re) => ls.some((l) => re.test(l));

check("right-click offers Run TOSCA Script", has(c1, /Run TOSCA Script/));
check("Bulk Actions offers Run TOSCA Script too", has(b1, /Run TOSCA Script/));
// Guarded: if either menu loses the entry, this must FAIL, not throw on
// `undefined.trim()` — a crash halts the suite and hides every check below it.
const toscaIn = (ls) => {
  const hit = ls.filter((l) => /Run TOSCA/.test(l))[0];
  return hit === undefined ? null : hit.trim();
};
check("both spell it the same way",
      toscaIn(c1) !== null && toscaIn(c1) === toscaIn(b1));

select([FILE, "C:/data/SH_0002.OK"]);
const b2 = bulkItems().map((e) => e.textContent || "");
check("...and it counts the selection like its neighbours",
      b2.some((l) => /Run TOSCA Script \(2\)/.test(l)));
check("it sits after Send, as in the right-click menu",
      (() => {
        const ls = bulkItems().map((e) => e.textContent || "");
        return ls.findIndex((l) => /Run TOSCA/.test(l))
             > ls.findIndex((l) => /NiceLabel|Send/i.test(l));
      })());

// --------------------------------------------------------------------------
// Generate volume files belongs on BOTH menus
// --------------------------------------------------------------------------
// It was Bulk-Actions-only at v0.99.0, on the user's explicit call, because
// generation worked from ONE template file. That reason expired:
// `enterGenerateMode` takes the whole selection and the panel draws each
// generated file from a random source. This suite used to PRINT the asymmetry
// as deliberate — now it asserts the parity, which is the same job done the
// other way round.
select([FILE]);
const g1 = ctxItems().map((e) => e.textContent || "");
const gb1 = bulkItems().map((e) => e.textContent || "");
check("right-click offers Generate volume files", has(g1, /Generate volume/));
check("Bulk Actions offers it too", has(gb1, /Generate volume/));
// Guarded like the TOSCA pair: a missing entry must FAIL, never throw on
// `undefined.trim()` — a crash halts the suite and hides the checks below.
const genIn = (ls) => {
  const hit = ls.filter((l) => /Generate volume/.test(l))[0];
  return hit === undefined ? null : hit.trim();
};
check("both spell it the same way", genIn(g1) !== null && genIn(g1) === genIn(gb1));
check("one file reads without a count", genIn(g1) === "Generate volume files…");

select([FILE, "C:/data/SH_0002.OK"]);
const g2 = ctxItems().map((e) => e.textContent || "");
const gb2 = bulkItems().map((e) => e.textContent || "");
check("a multi-file selection counts SOURCE files in both",
      genIn(g2) === "Generate volume files… (2 source files)"
      && genIn(g2) === genIn(gb2));
// Position, not just presence: it sits between Total Qty and Convert on both,
// so the two menus read in the same order rather than merely holding the same
// entries. Drift in ORDER is what makes one menu feel like a different tool.
const between = (ls) => {
  const g = ls.findIndex((l) => /Generate volume/.test(l));
  const t = ls.findIndex((l) => /Total Qty/.test(l));
  const c = ls.findIndex((l) => /Convert/.test(l));
  return g > t && g < c;
};
check("it sits after Total Qty and before Convert in the right-click menu", between(g2));
check("...and in the same place on Bulk Actions", between(gb2));

// Clicking it must actually OPEN the generate panel. A label-only assertion
// would pass on an entry wired to the wrong handler — the exact defect this
// suite was written for (v0.99.0's single "Bulk Edit" always meant "fields").
select([FILE]);
const genItem = ctxItems().filter((e) => /Generate volume/.test(e.textContent || ""))[0];
if (!genItem) {
  check("the right-click entry opens the generate panel", false);
} else {
  const gp = doc.querySelector("#generatePanel");
  // Stated so the check below cannot pass on a panel that was already open —
  // a "still visible" assertion would be green whatever the entry is wired to.
  check("the generate panel is hidden before the entry is clicked",
        gp.classList.contains("hidden"));
  genItem.click();
  check("the right-click entry opens the generate panel",
        !gp.classList.contains("hidden"));
}

process.exit(failures ? 1 : 0);
