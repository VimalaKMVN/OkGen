// Executes the real app.js chain control against a stub DOM.
//
// A Calgary JSON `chain` may be written as a CODE (`04`) or as a brand NAME
// (`Winners`) — both are valid and the real vendor samples carry both (D41,
// D57). Two things follow, and this asserts them:
//
//   * the editor must SAY which form the file on disk is using, because the
//     text box alone shows a value and leaves the user to infer the rest; and
//   * the field must still be PICKABLE from a list, not only typeable — making
//     it a text box for the sake of capitalisation must not cost the dropdown.
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
// The dropdown is still there
// --------------------------------------------------------------------------
const r = render("04");
check("a picker is rendered beside the box", !!r.pick && r.pick.tagName === "SELECT");
check("it offers every chain the server allowed",
      r.pick && descendants(r.pick).length === Object.keys(OPTIONS).length + 1);
check("its first entry is a placeholder, not a value",
      r.pick && descendants(r.pick)[0].value === "");
check("the box itself is still typeable",
      r.input && r.input.tagName === "INPUT" && r.input.type === "text"
      && !r.input.disabled);
check("the keyboard path keeps the same values",
      r.list && descendants(r.list).length === Object.keys(OPTIONS).length);
check("Europe is offered nowhere — the server filtered it and the client adds nothing",
      !descendants(r.pick).some((o) => /europe|^05$/i.test(o.value || ""))
      && !descendants(r.list).some((o) => /europe|^05$/i.test(o.value || "")));

// --------------------------------------------------------------------------
// Picking has to behave exactly like typing
// --------------------------------------------------------------------------
const p = render("04");
p.pick.value = "HomeSense";
(p.pick._handlers.change || []).forEach((fn) => fn({ target: p.pick }));

check("picking fills the box", p.input.value === "HomeSense");
check("picking records the edit, so Save picks it up",
      Object.values(api.state.edits || {}).includes("HomeSense"));
check("the box is marked dirty", (p.input.className || "").includes("dirty"));
check("the badge follows the picked value", p.badge.textContent === "name");
check("the picker resets to its placeholder, so it never shows a stale value",
      p.pick.value === "");

// Typing back the ORIGINAL value must clear the edit, not leave a phantom one.
p.input.value = "04";
(p.input._handlers.input || []).forEach((fn) => fn({ target: p.input }));
check("returning to the original value drops the edit",
      !Object.values(api.state.edits || {}).includes("HomeSense"));
check("...and the badge goes back to code", p.badge.textContent === "code");

process.exit(failures ? 1 : 0);
