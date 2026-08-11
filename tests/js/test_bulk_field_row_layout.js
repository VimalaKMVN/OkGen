// Bulk Edit — field values: one field is ONE ROW, exactly like Volume Generate.
//
// User-reported: "the fields show in 2 lines — checkbox, field name and min on
// one line, max on the next." The cause was geometry, not markup. The row
// declared a 170px name that could not shrink plus 500px of inputs (values 260,
// min 120, max 120) — 715px of content — inside a group box whose usable width
// was 408px (`flex: 1 1 430px` less padding), with `flex-wrap: wrap` on the row
// itself. So the row broke, and `max` landed on a second line.
//
// Volume Generate's row never breaks for three reasons together, and all three
// now hold here: its name has `flex: 1` so it SHRINKS instead of forcing the
// break, its inputs are half the width, and its row has no wrap at all.
//
// This is a CSS defect end to end, so the load-bearing checks read the
// STYLESHEET and compute the row's intrinsic width. A DOM assertion alone would
// have passed the entire time the panel was rendering two lines per field —
// every control was present, just not on one line. There is no browser on this
// machine, so measuring is the substitute for looking.
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

// The body of a rule, by exact selector. Returns "" when the selector is absent
// so a check FAILS rather than throwing — a suite that dies mid-run reports
// fewer failures than exist, which has hidden real ones here before.
function rule(selector) {
  const re = new RegExp(
    selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*\\{([^}]*)\\}");
  const m = css.match(re);
  return m ? m[1] : "";
}
// A declared px width inside a rule body, as a number (NaN when absent).
function px(body, prop) {
  const m = body.match(new RegExp("(?:^|;)\\s*" + prop + "\\s*:\\s*(-?[\\d.]+)px"));
  return m ? Number(m[1]) : NaN;
}

// --------------------------------------------------------------------------
// The stylesheet — the three properties that decide whether a row can break
// --------------------------------------------------------------------------
const fieldRule = rule(".bulkf-field");
const nameRule = rule(".bulkf-name");

check(".bulkf-field is a flex row", /display\s*:\s*flex/.test(fieldRule));
check(".bulkf-field does NOT wrap — this is what forced the second line",
      !/flex-wrap/.test(fieldRule));
check(".bulkf-name shrinks instead of forcing a break (flex: 1)",
      /flex\s*:\s*1\b/.test(nameRule));
check("...and it declares no immovable min-width",
      !/min-width\s*:\s*[1-9]/.test(nameRule));

// --------------------------------------------------------------------------
// The measurement: what the row actually needs, against Volume Generate's
// --------------------------------------------------------------------------
const CHECKBOX = 13;          // browser default, same in both panels
const valsW = px(rule(".bulkf-vals"), "width");
const minW = px(rule(".bulkf-min, .bulkf-max"), "width");
const bulkGap = px(fieldRule, "gap");

const genListW = px(rule(".gen-field input.gen-list"), "width");
const genNumW = px(rule('.gen-field input[type="number"], .gen-rows input[type="number"]'),
                   "width");
const genGap = px(rule(".gen-field, .gen-rows"), "gap");

check("the values box is Volume Generate's list width", valsW === genListW);
check("min and max are Volume Generate's number width", minW === genNumW);
check("the row gap matches too", bulkGap === genGap);

// The name contributes its declared min-width, which is the whole point: at
// `flex: 1; min-width: 0` it gives up its space before anything else does and
// adds nothing to the minimum, but the 170px it used to declare was immovable
// and went straight into the total. Four gaps, five controls.
const nameFloor = px(nameRule, "min-width") || 0;
const genNameFloor = px(rule(".gen-name"), "min-width") || 0;
const bulkMin = CHECKBOX + nameFloor + valsW + minW + minW + 4 * bulkGap;
const genMin = CHECKBOX + genNameFloor + genNumW + genNumW + genListW + 4 * genGap;
console.log(`     measured: bulk row ${bulkMin}px, generate row ${genMin}px`);
check("the row needs no more space than a Volume Generate row", bulkMin <= genMin);
check("...and is far below the 715px that used to break it", bulkMin < 400);

// The box must not SQUEEZE the row either. `.gen-group` declares no `flex`, so
// its basis is `auto` and it sizes to its content; `.bulkf-group` used to
// declare `flex: 1 1 430px`, which pinned the box at 430px however wide the row
// inside it was — the 715-in-408 squeeze. `min-width` is only a floor and never
// the constraint that broke this, so what is asserted is the absence of a
// basis, not a number.
const groupRule = rule(".bulkf-group");
const groupMin = px(groupRule, "min-width");
check("the group sizes to its content — no flex-basis to squeeze the row",
      !/(^|;)\s*flex\s*:/.test(groupRule) && !/flex-basis/.test(groupRule));
check("...the same way Volume Generate's group does",
      !/(^|;)\s*flex\s*:/.test(rule(".gen-group")));
check("the group box is sized like Volume Generate's", groupMin === px(rule(".gen-group"), "min-width"));
check("...and scrolls at the same height",
      px(groupRule, "max-height") === px(rule(".gen-group"), "max-height"));
check("the section boxes tile with Generate's gap",
      px(rule(".bulkf-groups"), "gap") === px(rule(".gen-cols"), "gap"));

// The rule that made the old wrap necessary must be gone with it — left behind
// it would silently re-introduce a second line the day the note comes back.
check("no rule still parks a note INSIDE the row",
      !/\.bulkf-field\s+\.bulk-rollup-note/.test(css));

// --------------------------------------------------------------------------
// The DOM half: every control of a field is a direct child of its one row, and
// nothing block-level is in there to break it
// --------------------------------------------------------------------------
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderBulkFieldsPanel };");
let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => new Promise(() => {}), () => true, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

const scope = {
  files: [{ path: "/tmp/a.OK", name: "a.OK", layout: "StyleHeader" }],
  layouts: { StyleHeader: 1 },
  header_fields: { StyleHeader: [
    { name: "dept", size: 4 },
    { name: "tot_qty", size: 7 },
    { name: "transmitDate", size: 8, date: true },
    { name: "keytrol", size: 7, editable: false, locked_reason: "key" },
  ] },
  detail_sections: { StyleHeader: [{ name: "Size", fields: [{ name: "qty", size: 5 }] }] },
  // A roll-up on tot_qty, so the note that used to live inside the row is built.
  rollups: { StyleHeader: [{ field: "tot_qty", section: "Size", source: "qty" }] },
  sources: {}, key_fields: {},
};

const panel = doc.querySelector("#bulkPanel");
panel.innerHTML = "";
try {
  api.renderBulkFieldsPanel(scope);
} catch (e) {
  console.log("FAIL renderBulkFieldsPanel threw: " + (e && e.message));
  process.exit(1);
}

const cls = (e, c) => (e.className || "").split(/\s+/).includes(c);
const rows = descendants(panel).filter((e) => cls(e, "bulkf-field"));
check("a row is rendered per field", rows.length === 5);

// Every one of a field's controls is a direct CHILD of the row. A control
// nested a level deeper would be laid out by that wrapper, not by the flex row,
// which is how "one row" quietly becomes two again.
const partsOf = (r) => Array.prototype.slice.call(r.childNodes || []);
const dept = rows.find((r) => partsOf(r).some((c) => c.dataset && c.dataset.field === "dept"));
const tot = rows.find((r) => partsOf(r).some((c) => c.dataset && c.dataset.field === "tot_qty"));
const locked = rows.find((r) => partsOf(r).some((c) => c.dataset && c.dataset.field === "keytrol"));

check("checkbox, name, values, min and max are all children of the ONE row",
      dept && ["bulkf-on", "bulkf-name", "bulkf-vals", "bulkf-min", "bulkf-max"]
        .every((c) => partsOf(dept).some((k) => cls(k, c))));
check("a locked field keeps its reason on the same row",
      locked && partsOf(locked).some((k) => cls(k, "bulkf-lockreason")));
check("no row contains a block-level DIV",
      !rows.some((r) => descendants(r).some((k) => k.tagName === "DIV")));
check("the roll-up note is a SIBLING of its row, not a child",
      tot && !descendants(tot).some((k) => cls(k, "bulk-rollup-note"))
      && descendants(panel).some((k) => cls(k, "bulk-rollup-note")));

// The narrowed values box hands its full meaning to the tooltip rather than
// dropping it — a clipped placeholder would be its own defect.
const valsOf = (r) => partsOf(r).find((c) => cls(c, "bulkf-vals"));
check("the values box explains itself on hover",
      dept && /comma list/i.test((valsOf(dept) || {}).title || ""));
check("...including how to write a blank",
      dept && /blank/i.test((valsOf(dept) || {}).title || ""));
const dateRow = rows.find((r) => partsOf(r).some((c) => c.dataset && c.dataset.field === "transmitDate"));
check("a date field still says it takes a date",
      dateRow && /^\d{4}-\d{2}-\d{2}$/.test((valsOf(dateRow) || {}).placeholder || ""));

process.exit(failures ? 1 : 0);
