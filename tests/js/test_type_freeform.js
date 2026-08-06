// Executes the real app.js field rendering against a stub DOM.
//
// A Calgary JSON document `type` IS editable — but only in its capitalisation
// (D51/D64): detection lower-cases before matching, so `STYLEHEADERS` is the
// same document type as `styleHeaders`, while a cross-layout change is refused
// on save because the rest of the document keeps its shape.
//
// The editor made that impossible to use. A field with `options` renders as a
// <select>, and this field's option list holds the ONE word this layout's
// documents carry — so the control offered a single choice and there was no way
// to type another casing of it. The user reported it as "type is not allowing
// me to edit".
//
// It now renders as a text box with the known values SUGGESTED via a datalist,
// driven by `freeform` in field_display.yaml. What may actually be SAVED is
// unchanged and still server-side — this file only asserts the control.
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

// One section as the server describes it: `type` freeform with its single known
// value, `chain` a normal coded dropdown, `format` the same, `keytrol` plain.
const SECTION = {
  index: 0,
  name: "Header",
  fields: [
    { name: "type", start: null, size: 20, type: "char",
      options: { styleHeaders: "styleHeaders" },
      hidden: false, editable: true, literal: false, freeform: true },
    { name: "chain", start: null, size: 9, type: "char",
      options: { "04": "Winners", "06": "HomeSense" },
      hidden: false, editable: true, literal: false, freeform: false },
    { name: "keytrol", start: null, size: 10, type: "char",
      options: null, hidden: false, editable: true, literal: false },
  ],
  records: [{
    index: 0,
    values: { type: "styleHeaders", chain: "04", keytrol: "550000" },
  }],
};

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

const form = api.renderForm(SECTION);
const nodes = descendants(form);
const byField = (name) =>
  nodes.filter((e) => e.dataset && e.dataset.field === name)[0];

const typeCtl = byField("type");
const chainCtl = byField("chain");
const keyCtl = byField("keytrol");

// --------------------------------------------------------------------------
// The report: `type` must be typeable
// --------------------------------------------------------------------------
check("a control is rendered for type", !!typeCtl);
check("type is a text input, NOT a dropdown",
      typeCtl && typeCtl.tagName === "INPUT" && typeCtl.type === "text");
check("it carries the file's current value",
      typeCtl && typeCtl.value === "styleHeaders");
check("it is not disabled or read-only",
      typeCtl && !typeCtl.disabled && !(typeCtl.className || "").includes("fval-ro"));
check("its length limit is the field's declared size",
      typeCtl && typeCtl.maxLength === 20);
check("it explains that any capitalisation is accepted",
      typeCtl && /capitalisation/i.test(typeCtl.title || ""));
check("...and that a cross-layout change is refused",
      typeCtl && /refused/i.test(typeCtl.title || ""));

// --------------------------------------------------------------------------
// The known value stays discoverable
// --------------------------------------------------------------------------
const lists = nodes.filter((e) => e.tagName === "DATALIST");
check("a datalist of known values is rendered", lists.length === 1);
check("the input points at it by id",
      typeCtl && lists[0] && typeCtl.attrs
      && typeCtl.attrs.list === lists[0].id && !!lists[0].id);
check("the datalist offers the layout's own type word",
      lists[0] && descendants(lists[0]).some((o) => o.value === "styleHeaders"));
check("the datalist is a SIBLING, never a child of the void <input>",
      typeCtl && !descendants(typeCtl).some((e) => e.tagName === "DATALIST"));

// --------------------------------------------------------------------------
// Nothing else changes shape
// --------------------------------------------------------------------------
check("a normal coded field is still a dropdown",
      chainCtl && chainCtl.tagName === "SELECT");
check("the coded dropdown still offers its labelled values",
      chainCtl && descendants(chainCtl).length >= 2);
check("a plain field is still a plain text input",
      keyCtl && keyCtl.tagName === "INPUT" && !(keyCtl.attrs || {}).list);
check("only the freeform field gets a datalist", lists.length === 1);

// --------------------------------------------------------------------------
// Edits are still collected from it
// --------------------------------------------------------------------------
check("the control is wired for edit collection",
      typeCtl && typeCtl.dataset.section === 0 && typeCtl.dataset.record === 0);
check("its original value is recorded, so an untouched field is not re-sent",
      typeCtl && typeCtl.dataset.orig === "styleHeaders");

process.exit(failures ? 1 : 0);
