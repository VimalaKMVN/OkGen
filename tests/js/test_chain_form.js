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
  return {
    input: nodes.filter((e) => e.dataset && e.dataset.field === "chain")[0],
    pick: nodes.filter((e) => (e.className || "").includes("fval-pick"))[0],
    badge: nodes.filter((e) => (e.className || "").includes("form-badge"))[0],
    list: nodes.filter((e) => e.tagName === "DATALIST")[0],
  };
}

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
// The dropdown is IN THE BOX — there is no second control
// --------------------------------------------------------------------------
// User-reported: the separate `pick…` <select> beside the field was confusing.
// It always was two controls for one field, and the second could never show the
// field's own value (its first entry had to be a placeholder), so it read as a
// rival input. The <datalist> the box already carried IS a dropdown in the box.
const r = render("04");
check("no separate picker is rendered", !r.pick);
check("no <select> is rendered for this field at all",
      !descendants(api.renderForm(section("04"))).some((e) => e.tagName === "SELECT"));
check("the box itself is typeable",
      r.input && r.input.tagName === "INPUT" && r.input.type === "text"
      && !r.input.disabled);
check("the box owns a dropdown, wired by id",
      r.list && !!r.list.id && r.input.getAttribute("list") === r.list.id);
check("the dropdown offers every chain the server allowed, plus the type-it hint",
      r.list && descendants(r.list).length === Object.keys(OPTIONS).length + 1);
check("Europe is offered nowhere — the server filtered it, the client adds nothing",
      !descendants(r.list).some((o) => /europe|^05$/i.test(o.value || "")));
check("the tooltip points at the box, not at a neighbour",
      /list in this box/i.test(r.input.title || "")
      && !/beside/i.test(r.input.title || ""));

// The label is the whole reason the picker existed — a bare `01` means nothing.
// It has to survive the removal, carried by the option itself.
const all = descendants(r.list);
const opts = all.filter((o) => o.value !== "");   // real values, minus the hint
const byValue = (v) => opts.filter((o) => o.value === v)[0];
check("a CODE carries its brand name as the option label",
      byValue("01") && byValue("01").getAttribute("label") === "TJMAXX");
check("...on every code, not just the first",
      ["02", "03", "04", "06"].every(
        (c) => byValue(c) && byValue(c).getAttribute("label") === OPTIONS[c]));
check("...and as text too, for browsers that render the text not @label",
      byValue("01") && byValue("01").textContent === "TJMAXX");
check("a NAME is not labelled with itself — 'Winners (Winners)' reads as two things",
      byValue("Winners") && !byValue("Winners").hasAttribute("label"));
check("every option still inserts the VALUE, never the label",
      opts.every((o) => Object.keys(OPTIONS).includes(o.value)));

// --------------------------------------------------------------------------
// "---- or type value ----" — the entry that is not a value
// --------------------------------------------------------------------------
// The lists are SUGGESTIONS, not the whole truth, and a plain box says nothing
// about that. This entry says it, and clears the box when chosen.
const HINT = "---- or type value ----";
const hint = all.filter((o) => (o.getAttribute("label") || "") === HINT)[0];
check("the hint is offered in the dropdown", !!hint);
check("it is the FIRST entry, so it reads as a heading for the list",
      all[0] === hint);
check("it carries the hint as a LABEL, not as a value",
      hint && hint.value === "" && hint.getAttribute("label") === HINT);
check("...as text too, for browsers that render text rather than @label",
      hint && hint.textContent === HINT);
// This is the load-bearing one. A chosen suggestion inserts the option's VALUE,
// and that insert is subject to maxLength — 9 on chain, 1 on a detail `type`.
// A hint carried as a value would arrive truncated to nonsense.
check("the hint's value fits any field, however narrow",
      hint && hint.value.length === 0);
check("no real option was displaced by it",
      opts.length === Object.keys(OPTIONS).length);

// Choosing it must EMPTY the box, not write the hint into it.
const h = render("04");
const hCtl = h.input;
hCtl.value = "";                                   // what the browser inserts
(hCtl._handlers.input || []).forEach((fn) => fn({ target: hCtl }));
check("choosing the hint clears the box", hCtl.value === "");
check("the badge shows no form for an empty box", h.badge.textContent === "");

// Belt and braces: if a browser inserts the LABEL instead (Firefox renders the
// label in place of the value, so the two are easy to conflate), still clear.
const h2 = render("04");
h2.input.value = HINT;
(h2.input._handlers.input || []).forEach((fn) => fn({ target: h2.input }));
check("the hint text never survives in the box, whichever the browser inserts",
      h2.input.value === "");
check("...and it is never collected as an edit",
      !Object.values(api.state.edits || {}).includes(HINT));

// --------------------------------------------------------------------------
// Choosing from the box IS typing — same event, same handlers
// --------------------------------------------------------------------------
// A datalist selection fires `input` on the box, so there is no separate path
// left to keep in step; that is the simplification, and this pins it.
const p = render("04");
p.input.value = "HomeSense";
(p.input._handlers.input || []).forEach((fn) => fn({ target: p.input }));

check("choosing a value fills the box", p.input.value === "HomeSense");
check("choosing records the edit, so Save picks it up",
      Object.values(api.state.edits || {}).includes("HomeSense"));
check("the box is marked dirty", (p.input.className || "").includes("dirty"));
check("the badge follows the chosen value", p.badge.textContent === "name");

// Typing back the ORIGINAL value must clear the edit, not leave a phantom one.
p.input.value = "04";
(p.input._handlers.input || []).forEach((fn) => fn({ target: p.input }));
check("returning to the original value drops the edit",
      !Object.values(api.state.edits || {}).includes("HomeSense"));
check("...and the badge goes back to code", p.badge.textContent === "code");

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
check("app.js creates no fval-pick element anywhere",
      !/["']fval-pick["']/.test(src));
check("no render site still appends a picker", !/okgenPicker/.test(src));
// A QUOTED string literal only. The comment above the control explains why the
// picker went and names it in backticks, so a bare /pick…/ — or one that counts
// a backtick as a quote — matches that prose and fails forever.
check("no 'pick…' placeholder string is left in any control",
      !/["']pick…/.test(src));

process.exit(failures ? 1 : 0);
