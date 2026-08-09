// The tree marker for a file whose summed section is EMPTY — "NoSzLines".
//
// User-reported: it "is not visible clearly". Measured, the fault was the TEXT
// rather than the outline — `no sizes` at 9px in #6b7280 grey is 3.45:1 against
// the #1e1e1e page, below the 4.5:1 floor for small text, inside a near-white
// #d1d5db border that was itself 11.31:1. So the chip announced itself as a
// shape while the word inside it could not be read: a light-theme palette never
// re-checked against the dark one. It is now a filled chip in the app's accent
// family (label 5.82:1 on its own background), reading `NoSzLines`.
//
// The marker had NO test of any kind before this, which is the real reason a
// light-theme rule survived: nothing rendered it. These checks cover what a
// stub can judge — the text, the class, that it appears only for the files that
// warrant it, and that it still explains itself on hover — plus a guard on the
// stylesheet, since the defect was entirely in CSS and a DOM assertion would
// have passed the whole time it was invisible.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

const STATIC = path.join(__dirname, "..", "..", "src", "okgen", "web", "static");
const src = fs.readFileSync(path.join(STATIC, "app.js"), "utf8");
const css = fs.readFileSync(path.join(STATIC, "styles.css"), "utf8");

let failures = 0;
function check(label, cond) {
  console.log((cond ? "ok   " : "FAIL ") + label);
  if (!cond) failures++;
}

// `no_rollup_rows` is the server's flag: this file's roll-up section has no
// rows, so its header total IS the quantity rather than a sum (D58).
const TREE = {
  type: "folder", name: "Batch11", path: "/d/Batch11", children: [
    { type: "file", name: "empty.OK", path: "/d/Batch11/empty.OK", json: false,
      layout: "StyleHeader", chain: "03", chain_info: null,
      key_field: "keytrol", key_value: "550001", duplicate: false,
      no_rollup_rows: true },
    { type: "file", name: "sized.OK", path: "/d/Batch11/sized.OK", json: false,
      layout: "StyleHeader", chain: "03", chain_info: null,
      key_field: "keytrol", key_value: "550002", duplicate: false,
      no_rollup_rows: false },
    // A layout with no roll-up configured at all sends nothing.
    { type: "file", name: "Preticket.OK", path: "/d/Batch11/Preticket.OK", json: false,
      layout: "Preticket", chain: "03", chain_info: null,
      key_field: "po", key_value: "700001", duplicate: false },
  ],
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { state, renderTree };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

api.renderTree(TREE);
const nodes = descendants(doc.querySelector("#tree"));
const tags = nodes.filter((e) => (e.className || "").includes("no-rows-tag"));

// Assert the SET first — an every() over an empty list passes vacuously, which
// is exactly the failure these checks exist to catch.
check("exactly one file is marked — the one with no size lines", tags.length === 1);
check("it reads NoSzLines", tags[0] && tags[0].textContent === "NoSzLines");
check("...not the old wording", !/no sizes/.test(nodes.map((e) => e.textContent).join(" ")));
check("it still explains itself on hover",
      tags[0] && /total quantity/i.test(tags[0].title || "")
      && /never recalculated/i.test(tags[0].title || ""));

// --------------------------------------------------------------------------
// The defect was in the STYLESHEET, so a DOM assertion alone cannot catch a
// regression to it. These pin the properties that made it invisible.
// --------------------------------------------------------------------------
const rule = (css.match(/\.no-rows-tag\s*\{[^}]*\}/) || [""])[0];
check("the marker has a rule at all", rule.length > 0);
check("it is FILLED, not transparent — a background is what makes it read",
      /background:\s*[^;]*(var\(--accent\)|#[0-9a-f]{3,8})/i.test(rule));
check("...and its text is light, for a dark background",
      /color:\s*#(e|f|d)[0-9a-f]{5}/i.test(rule));
check("the light-theme grey/near-white pair that made the label unreadable is gone",
      !/#6b7280/i.test(rule) && !/#d1d5db/i.test(rule));
check("it is not amber — an empty size section is normal, not a fault",
      !/#e0b000/i.test(rule));
check("it does not use the outlined source-badge class, which is a different thing",
      !/src-badge/.test(rule));

process.exit(failures ? 1 : 0);
