// Executes the real app.js chain control against a stub DOM.
//
// A Calgary JSON `chain` may be written as a CODE (`04`) or as a brand NAME
// (`Winners`) — both are valid and the real vendor samples carry both (D41,
// D57). Two things follow, and this asserts them:
//
//   * the editor must SAY which form the file on disk is using, because the
//     text box alone shows a value and leaves the user to infer the rest; and
//   * the field must still be CHOOSABLE from a list, not only typeable — making
//     it a text box for the sake of capitalisation must not cost the dropdown.
//
// That dropdown now lives IN the box (an <input list=> + its <datalist>). The
// separate `pick…` <select> that used to sit beside it was withdrawn as
// confusing — see the middle section for what had to move with it.
//
// What may be SAVED is server-side and unchanged (can_change_chain still
// refuses Europe on these North-America layouts, by code or by name).
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

// The chain descriptor as the server builds it: Europe already filtered out by
// isolation, and every offered value labelled code-or-name.
const OPTIONS = {
  "01": "TJMAXX", "02": "Marshalls", "03": "Homegoods", "04": "Winners",
  "06": "HomeSense",
  TJMAXX: "TJMAXX", Marshalls: "Marshalls", Homegoods: "Homegoods",
  Winners: "Winners", HomeSense: "HomeSense",
};
const VALUE_FORMS = {
  "01": "code", "02": "code", "03": "code", "04": "code", "06": "code",
  TJMAXX: "name", Marshalls: "name", Homegoods: "name",
  Winners: "name", HomeSense: "name",
};

function section(chainValue) {
  return {
    index: 0,
    name: "Header",
    fields: [
      { name: "chain", start: null, size: 9, type: "char", options: OPTIONS,
        value_forms: VALUE_FORMS, hidden: false, editable: true,
        literal: false, freeform: true },
    ],
    records: [{ index: 0, values: { chain: chainValue } }],
  };
}

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { state, renderForm };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

function render(value) {
  const nodes = descendants(api.renderForm(section(value)));
  const cls = (e, c) => (e.className || "").split(/\s+/).includes(c);
  const menu = nodes.filter((e) => cls(e, "fval-menu"))[0];
  return {
    nodes,
    input: nodes.filter((e) => e.dataset && e.dataset.field === "chain")[0],
    pick: nodes.filter((e) => cls(e, "fval-pick"))[0],
    // The badge beside the box — NOT the per-row tags inside the menu, which
    // carry the same class. Taking [0] blindly would silently assert a row.
    badge: nodes.filter((e) => cls(e, "form-badge")
                            && !(e.parentNode && cls(e.parentNode, "fval-opt")))[0],
    arrow: nodes.filter((e) => cls(e, "fval-arrow"))[0],
    menu,
    rows: menu ? descendants(menu).filter((e) => cls(e, "fval-opt")) : [],
  };
}
const fire = (el, ev, arg) =>
  ((el && el._handlers && el._handlers[ev]) || [])
    .forEach((fn) => fn(arg || { preventDefault() {} }));
// If the control is missing entirely (an older app.js, or a regression), every
// check below must FAIL rather than the suite throwing on the first one — a
// crash truncates the run and hides every assertion after it.
const MISSING = { classList: { contains: () => false, remove() {}, add() {} },
                  className: "", textContent: "", _handlers: {} };
const or = (x) => x || MISSING;
const rowText = (r) => descendants(r)
  .filter((e) => (e.className || "").includes("fval-opt-text"))
  .map((e) => e.textContent)[0];
const visible = (rows) => rows.filter((r) => !r.classList.contains("hidden"));

// --------------------------------------------------------------------------
// Which form is this file using?
// --------------------------------------------------------------------------
const asCode = render("04");
check("a file storing a CODE is labelled code",
      asCode.badge && asCode.badge.textContent === "code");
check("...and says a name would be equally valid",
      asCode.badge && /brand name is equally valid/i.test(asCode.badge.title || ""));

const asName = render("Winners");
check("a file storing a NAME is labelled name",
      asName.badge && asName.badge.textContent === "name");
check("...and says a code would be equally valid",
      asName.badge && /code is equally valid/i.test(asName.badge.title || ""));

const lower = render("homesense");
check("the form is recognised whatever the capitalisation",
      lower.badge && lower.badge.textContent === "name");

const junk = render("Sainsburys");
check("a value this layout does not know is called out",
      junk.badge && junk.badge.textContent === "unknown");
check("...and warns it will not save",
      junk.badge && /refused on save/i.test(junk.badge.title || ""));

const badgeClasses = [asCode, asName, junk].map((r) => r.badge && r.badge.className);
check("each form is styled distinctly",
      new Set(badgeClasses).size === 3
      && badgeClasses.every((c) => c && c.includes("form-badge")));

// --------------------------------------------------------------------------
// The dropdown is IN THE BOX, and it shows EVERY value on a populated field
// --------------------------------------------------------------------------
// This is the regression that matters. The control used to be an <input list=>
// with a <datalist>, and a datalist filters its options by what is already in
// the box, by PREFIX — so on a field holding "04" the native list collapsed to
// the single option "04" and the user saw no choices at all ("it shows only the
// value that is populated there"). Chrome and Firefox additionally show nothing
// until the user types or deletes; Safari renders only an option's value and
// never its label. So the menu is built here instead.
//
// Every check below therefore renders a POPULATED field. A test that started
// from an empty box would pass against the broken datalist too.
const r = render("04");
check("no separate picker is rendered", !r.pick);
check("no <select> is rendered for this field at all",
      !r.nodes.some((e) => e.tagName === "SELECT"));
check("no <datalist> is left — the browsers cannot show it usefully",
      !r.nodes.some((e) => e.tagName === "DATALIST"));
check("the box itself is typeable",
      r.input && r.input.tagName === "INPUT" && r.input.type === "text"
      && !r.input.disabled);
check("the box carries the file's value", or(r.input).value === "04");
check("a dropdown arrow sits in the field", !!r.arrow && r.arrow.type === "button");
check("the menu starts closed", r.menu && or(r.menu).classList.contains("hidden"));

// The whole point: open it on a populated field and every value is offered.
fire(r.arrow, "mousedown");
check("the arrow opens the menu", !or(r.menu).classList.contains("hidden"));
check("EVERY value is offered even though the box holds '04'",
      visible(r.rows).length === Object.keys(OPTIONS).length + 1);
check("...including values that do not start with what is in the box",
      visible(r.rows).some((x) => rowText(x) === "06 — HomeSense")
      && visible(r.rows).some((x) => rowText(x) === "Winners"));
check("the arrow closes it again",
      (fire(r.arrow, "mousedown"), or(r.menu).classList.contains("hidden")));

// --------------------------------------------------------------------------
// What each row SAYS — the code, its brand name, and which form it is
// --------------------------------------------------------------------------
const r2 = render("04");
fire(r2.arrow, "mousedown");
const texts = visible(r2.rows).map(rowText);
check("a CODE row shows the code and the brand name", texts.includes("01 — TJMAXX"));
check("...for every code", ["02 — Marshalls", "03 — Homegoods", "04 — Winners"]
      .every((t) => texts.includes(t)));
check("a NAME row is not doubled up — 'Winners — Winners' reads as two things",
      texts.includes("Winners") && !texts.includes("Winners — Winners"));
check("each row says whether it is a code or a name",
      visible(r2.rows).filter((x) => descendants(x)
        .some((e) => (e.className || "").includes("form-badge"))).length
      === Object.keys(OPTIONS).length);
check("Europe is offered nowhere — the server filtered it, the client adds nothing",
      !texts.some((t) => /europe|^05\b/i.test(t)));

// --------------------------------------------------------------------------
// "---- or type value ----" — the entry that is not a value
// --------------------------------------------------------------------------
const HINT = "---- or type value ----";
const r3 = render("04");
fire(r3.arrow, "mousedown");
const hintRow = r3.rows.filter((x) => rowText(x) === HINT)[0];
check("the hint is offered", !!hintRow);
check("it is FIRST, so it reads as a heading for the list",
      r3.rows[0] === hintRow);
check("it is styled as an instruction, not as a storable value",
      hintRow && (hintRow.className || "").includes("fval-opt-hint"));
check("it carries no code/name tag — it is not a value",
      hintRow && !descendants(hintRow)
        .some((e) => (e.className || "").includes("form-badge")));

// Choosing it must EMPTY the box so something else can be typed.
fire(hintRow, "mousedown");
check("choosing the hint clears the box", or(r3.input).value === "");
check("...and the hint text itself never lands in the field",
      or(r3.input).value !== HINT);
check("the badge shows no form for an empty box", or(r3.badge).textContent === "");
check("choosing it closes the menu", or(r3.menu).classList.contains("hidden"));

// --------------------------------------------------------------------------
// Typing filters — but never hides the escape hatch
// --------------------------------------------------------------------------
const r4 = render("04");
r4.input.value = "Home";
fire(r4.input, "input", { target: r4.input });
const shown = visible(r4.rows).map(rowText);
check("typing opens the menu", !or(r4.menu).classList.contains("hidden"));
check("typing narrows the list", shown.length < r4.rows.length);
check("it matches anywhere in the row, not only the start",
      shown.includes("03 — Homegoods") && shown.includes("06 — HomeSense"));
check("a non-matching value is dropped", !shown.includes("01 — TJMAXX"));
check("the hint survives every filter — it is the escape hatch",
      shown.includes(HINT));

const r5 = render("04");
r5.input.value = "zzzz";
fire(r5.input, "input", { target: r5.input });
check("even when nothing matches, the hint is still offered",
      visible(r5.rows).map(rowText).join("") === HINT);

// --------------------------------------------------------------------------
// Choosing a value behaves exactly like typing one
// --------------------------------------------------------------------------
// A CODE row stores the code — "06 — HomeSense" is one value shown two ways,
// not two values, and what lands in the file is `06`.
const p = render("04");
fire(p.arrow, "mousedown");
fire(p.rows.filter((x) => rowText(x) === "06 — HomeSense")[0], "mousedown");
check("choosing a code row stores the CODE, not the brand name",
      or(p.input).value === "06");
check("choosing records the edit, so Save picks it up",
      Object.values(api.state.edits || {}).includes("06"));
check("the box is marked dirty", (or(p.input).className || "").includes("dirty"));
check("the badge says it is now a code", or(p.badge).textContent === "code");
check("the menu closes after a choice", or(p.menu).classList.contains("hidden"));

// ...and the NAME row stores the name. Both forms are legitimate (D57), so the
// list offers each one separately and the badge reports which you picked.
const pn = render("04");
fire(pn.arrow, "mousedown");
fire(pn.rows.filter((x) => rowText(x) === "HomeSense")[0], "mousedown");
check("choosing a name row stores the NAME", or(pn.input).value === "HomeSense");
check("the badge says it is now a name", or(pn.badge).textContent === "name");
check("Escape closes the menu",
      (fire(p.arrow, "mousedown"),
       fire(p.input, "keydown", { key: "Escape", preventDefault() {} }),
       or(p.menu).classList.contains("hidden")));
// --------------------------------------------------------------------------
// The picker is gone from the SOURCES too, not just from this render
// --------------------------------------------------------------------------
// A DOM assertion cannot see a stale stylesheet rule or a second render site
// that still appends a picker, so both are read directly. `renderTable` (detail
// rows) appended `okgenPicker` alongside `renderForm` — a fix to one that
// missed the other would leave the confusing control on exactly the sections
// with the most fields on screen.
const CSS = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "styles.css"),
  "utf8");
check("no .fval-pick rule is left in the stylesheet",
      !/^\s*select\.fval-pick\b/m.test(CSS) && !/\.fval-pick\s*\{/.test(CSS));

// The menu is absolutely positioned. If its container is not itself
// positioned, it escapes to the corner of the PAGE instead of hanging under
// the field — a defect no DOM assertion can see, and the exact class that made
// the `NoSzLines` chip unreadable while its tests passed.
check("the menu is positioned, and hangs below the field",
      /\.fval-menu\s*\{[^}]*position:\s*absolute/m.test(CSS)
      && /\.fval-menu\s*\{[^}]*top:\s*100%/m.test(CSS));
check("its wrapper is the positioning context, not the flex-column field",
      /\.fval-wrap\s*\{[^}]*position:\s*relative/m.test(CSS));
check("it stacks above the fields below it",
      /\.fval-menu\s*\{[^}]*z-index:\s*\d+/m.test(CSS));
check("a long list scrolls instead of running off the window",
      /\.fval-menu\s*\{[^}]*overflow-y:\s*auto/m.test(CSS)
      && /\.fval-menu\s*\{[^}]*max-height:/m.test(CSS));
check("the arrow sits inside the box, with room made for it",
      /\.fval-arrow\s*\{[^}]*position:\s*absolute/m.test(CSS)
      && /input\.fval\[role="combobox"\]\s*\{[^}]*padding-right:/m.test(CSS));
check("the hint row is styled apart from real values",
      /\.fval-opt-hint\s*\{/.test(CSS));
check("a row shows it is selectable", /\.fval-opt.*:hover/.test(CSS));
check("app.js creates no fval-pick element anywhere",
      !/["']fval-pick["']/.test(src));
check("no render site still appends a picker", !/okgenPicker/.test(src));
// A QUOTED string literal only. The comment above the control explains why the
// picker went and names it in backticks, so a bare /pick…/ — or one that counts
// a backtick as a quote — matches that prose and fails forever.
check("no 'pick…' placeholder string is left in any control",
      !/["']pick…/.test(src));

process.exit(failures ? 1 : 0);
