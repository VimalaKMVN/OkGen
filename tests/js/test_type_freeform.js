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
  src + "\n;return { state, renderForm, renderTable };");

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

// --------------------------------------------------------------------------
// The OTHER `type` — the one on a DETAIL line
// --------------------------------------------------------------------------
// `freeform:` is declared per LAYOUT and matched by field NAME, with no section
// scope, so the entry meant for the header's document discriminator also lands
// on CalgaryStyleHeader.Details.type — a different field entirely, with nine
// coded values (`1` -> Type 1 ... `9` -> Type 9).
//
// Two things follow. It renders through renderTable, a SECOND site that had to
// lose the picker as well; and its labels are the whole meaning of the field —
// a dropdown of bare 1..9 says nothing. Before the picker went, that meaning
// lived ONLY in the picker, so removing it without labelling the options would
// have destroyed it here while looking fine on `chain`.
const tableApi = run(doc, global.window, global.localStorage, global.Option,
                     () => Promise.resolve({}), global.confirm, global.prompt,
                     global.alert);
const DETAIL_OPTS = {
  "1": "Type 1", "2": "Type 2", "3": "Type 3", "4": "Type 4", "5": "Type 5",
  "6": "Type 6", "7": "Type 7", "8": "Type 8", "9": "Type 9",
};
const detailNodes = descendants(tableApi.renderTable({
  index: 4, name: "Details", max_records: null,
  fields: [{ name: "type", start: null, size: 1, type: "char",
             options: DETAIL_OPTS, hidden: false, editable: true,
             literal: false, freeform: true }],
  records: [{ index: 4, values: { type: "1" } }],
}));
const dCtl = detailNodes.filter((e) => e.dataset && e.dataset.field === "type")[0];
const dList = detailNodes.filter((e) => e.tagName === "DATALIST")[0];

check("the detail-line type is a typeable box",
      dCtl && dCtl.tagName === "INPUT" && dCtl.type === "text");
check("no picker survives in the detail TABLE either",
      !detailNodes.some((e) => e.tagName === "SELECT")
      && !detailNodes.some((e) => (e.className || "").includes("fval-pick")));
check("its dropdown is in the box, wired by id",
      dList && !!dList.id && dCtl.getAttribute("list") === dList.id);
const dReal = dList ? descendants(dList).filter((o) => o.value !== "") : [];
check("every coded value carries the label that gives it meaning",
      dReal.length === 9
      && dReal.every((o) => o.getAttribute("label") === DETAIL_OPTS[o.value]));
check("the option still inserts the CODE, not the label",
      dReal.every((o) => /^[1-9]$/.test(o.value)));

// The type-it hint reaches the detail table too — and this is the field that
// proves it must ride in the LABEL. `type` here is ONE character wide, so a
// hint carried as a value would be inserted and then cut to a single "-".
const dHint = dList
  ? descendants(dList).filter((o) => (o.getAttribute("label") || "")
                                     === "---- or type value ----")[0]
  : null;
check("the detail line offers the type-it hint as well", !!dHint);
check("its value is empty, so a 1-char field cannot truncate it",
      dHint && dHint.value === "" && dCtl.maxLength === 1);
check("choosing it clears the box rather than writing a stray '-'",
      (() => {
        dCtl.value = "---- or type value ----";
        (dCtl._handlers.input || []).forEach((fn) => fn({ target: dCtl }));
        return dCtl.value === "";
      })());

process.exit(failures ? 1 : 0);
