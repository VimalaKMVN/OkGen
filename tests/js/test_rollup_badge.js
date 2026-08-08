// Executes the real app.js roll-up logic against a stub DOM.
//
// The header total (StyleHeader's `tot_qty`) must equal the sum of every Size
// row's `qty`. The control is never locked — the value is enforced on the WRITE
// path (D51/D56) — so the editor's whole job is to make the truth visible:
//
//   * rows present, agrees      -> quiet "= sum of N size lines"
//   * rows present, disagrees   -> ⚠ naming what the save will write
//   * rows edited               -> the total FOLLOWS them, live
//   * total typed by hand       -> badged, never overwritten mid-keystroke
//   * no rows at all            -> ⓘ this total IS the quantity (not a warning:
//                                  an empty size section is a normal shape)
//
// The server is the authority on all of it; this asserts the client says the
// same thing, because a stub DOM is the only place the badge is ever exercised.
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

const HEADER = {
  index: 0,
  name: "Header",
  is_header: true,
  fields: [
    { name: "chain", start: 1, size: 2, type: "char", options: null,
      hidden: false, editable: true, literal: false },
    { name: "tot_qty", start: 141, size: 7, type: "char", options: null,
      hidden: false, editable: true, literal: false },
  ],
  records: [{ index: 0, values: { chain: "03", tot_qty: "0000022" } }],
};

function sizeSection(qtys) {
  return {
    index: 1,
    name: "Size",
    fields: [
      { name: "size", start: 1, size: 6, type: "char", options: null,
        hidden: false, editable: true, literal: false },
      { name: "qty", start: 7, size: 5, type: "char", options: null,
        hidden: false, editable: true, literal: false },
    ],
    records: qtys.map((q, i) => ({
      index: i + 1, values: { size: "XL", qty: String(q).padStart(5, "0") },
    })),
  };
}

const ROLLUP = { field: "tot_qty", section: "Size", source: "qty" };

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { state, renderForm, renderSection, refreshRollup, rollupLive, onEdit };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

// Mount a whole view into #editor so the live-value lookups find real controls.
function mount(qtys, totQty) {
  const header = JSON.parse(JSON.stringify(HEADER));
  header.records[0].values.tot_qty = totQty;
  const size = sizeSection(qtys);
  api.state.view = { sections: [header, size], rollups: [Object.assign(
    { rows: qtys.length, current: totQty, expected: null, matches: true,
      authoritative: !qtys.length }, ROLLUP)] };
  api.state.edits = {};
  const host = doc.querySelector("#editor");
  host.innerHTML = "";
  host.appendChild(api.renderSection(header));
  host.appendChild(api.renderSection(size));
  return host;
}

const nodesOf = (host) => descendants(host);
const badgeOf = (host) =>
  nodesOf(host).filter((e) => e.dataset && e.dataset.rollup === "tot_qty")[0];
const ctlOf = (host, si, ri, name) =>
  nodesOf(host).filter((e) => e.dataset && String(e.dataset.section) === String(si)
    && String(e.dataset.record) === String(ri) && e.dataset.field === name)[0];

// --------------------------------------------------------------------------
// The badge exists and reports a mismatch as the file was opened
// --------------------------------------------------------------------------
let host = mount([2, 2, 2, 2], "0000022");
api.refreshRollup(false);
let badge = badgeOf(host);
check("a badge is rendered beside the roll-up field", !!badge);
check("a mismatch is flagged", badge && (badge.className || "").includes("rollup-warn"));
check("it names the value the save will write",
      badge && badge.textContent.includes("0000008"));
check("its tooltip explains where that came from",
      badge && /size line/i.test(badge.title || ""));
check("opening does NOT rewrite the control",
      ctlOf(host, 0, 0, "tot_qty").value === "0000022");
// The badge must say WHERE that value comes from and what to do about it —
// "will be set to 0000008" alone reads as the editor refusing to take a value.
check("...and says the value is the sum of the size lines",
      badge && /sum of the size lines/i.test(badge.textContent));
check("...and points at the control that actually changes it",
      badge && /to change tot_qty, edit the size lines/i.test(badge.textContent));

// --------------------------------------------------------------------------
// Agreement is quiet, not silent
// --------------------------------------------------------------------------
host = mount([2, 2, 2, 2], "0000008");
api.refreshRollup(false);
badge = badgeOf(host);
check("a matching total reads as OK", badge && (badge.className || "").includes("rollup-ok"));
check("...and still says what it agrees with",
      badge && /sum of 4 size lines/i.test(badge.textContent));

// --------------------------------------------------------------------------
// Editing a row moves the total, live
// --------------------------------------------------------------------------
host = mount([2, 2, 2, 2], "0000008");
api.refreshRollup(false);
let qty = ctlOf(host, 1, 1, "qty");
qty.value = "99";
api.onEdit({ target: qty });
check("editing a row quantity rewrites the header total",
      ctlOf(host, 0, 0, "tot_qty").value === "0000105");
check("...and the badge follows it back to OK",
      (badgeOf(host).className || "").includes("rollup-ok"));
check("...and the total is staged as an edit to save",
      Object.values(api.state.edits).includes("0000105"));

// --------------------------------------------------------------------------
// Typing in the total itself is never overwritten mid-keystroke
// --------------------------------------------------------------------------
host = mount([2, 2, 2, 2], "0000008");
api.refreshRollup(false);
const tot = ctlOf(host, 0, 0, "tot_qty");
tot.value = "5000";
api.onEdit({ target: tot });
check("the typed value survives the keystroke", tot.value === "5000");
check("but it is badged as one the save will correct",
      (badgeOf(host).className || "").includes("rollup-warn"));
check("...naming the sum that will win",
      badgeOf(host).textContent.includes("0000008"));
// A value the user TYPED gets the same explanation as a mismatch the file
// arrived with — one wording, because the point is the rule, not their number.
check("...and explains the rule rather than echoing what they typed",
      /sum of the size lines/i.test(badgeOf(host).textContent)
      && /to change tot_qty, edit the size lines/i.test(badgeOf(host).textContent));

// --------------------------------------------------------------------------
// No rows: the total is the quantity, and that is INFORMATIONAL
// --------------------------------------------------------------------------
host = mount([], "0000022");
api.refreshRollup(false);
badge = badgeOf(host);
check("an empty size section reads as info, not a warning",
      badge && (badge.className || "").includes("rollup-info")
      && !(badge.className || "").includes("rollup-warn"));
check("it says the total is the quantity",
      badge && /quantity/i.test(badge.textContent));
check("the total is left exactly as it is",
      ctlOf(host, 0, 0, "tot_qty").value === "0000022");
check("nothing is staged for saving", Object.keys(api.state.edits).length === 0);

// --------------------------------------------------------------------------
// Refusals are shown rather than quietly truncated
// --------------------------------------------------------------------------
host = mount([99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999, 99999,
              99999, 99999, 99999, 99999, 99999, 99999, 99999], "0000022");
api.refreshRollup(false);
badge = badgeOf(host);
check("a total too wide for the field is flagged",
      badge && (badge.className || "").includes("rollup-warn"));
check("...and says the save will refuse rather than truncate",
      badge && /refuse/i.test(badge.title || ""));
check("...and the control is NOT filled with a truncated value",
      ctlOf(host, 0, 0, "tot_qty").value === "0000022");

host = mount([2, 2], "0000004");
api.refreshRollup(false);
qty = ctlOf(host, 1, 1, "qty");
qty.value = "AB";
api.onEdit({ target: qty });
check("a non-numeric row quantity is called out",
      (badgeOf(host).className || "").includes("rollup-warn")
      && /not a number/i.test(badgeOf(host).textContent));

// --------------------------------------------------------------------------
// A layout with no roll-up declared renders no badge at all
// --------------------------------------------------------------------------
api.state.view = { sections: [HEADER], rollups: [] };
const plain = api.renderForm(HEADER);
check("no roll-up config -> no badge",
      !descendants(plain).some((e) => e.dataset && e.dataset.rollup));

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
