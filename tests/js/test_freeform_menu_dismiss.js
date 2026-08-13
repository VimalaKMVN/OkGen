// A freeform dropdown must go away when you press somewhere else.
//
// User-reported against the JSON rendering view: open the list on the Header
// `type`, the Header `chain` or a detail-line `type` just to SEE which values
// exist, pick nothing, then click elsewhere or move to another part of OkGen —
// and the list stayed on screen, over everything.
//
// The shape of the bug is the point, because it explains why it looked
// intermittent. The menu had ONE interactive way to close: the input's `blur`.
// That covers a box that HAS focus, which is why choosing a value, or clicking
// into the box and then away, always worked — exactly the cases the report says
// were fine. It covers nothing when the list is opened from the ▾ ARROW, whose
// `preventDefault` deliberately keeps focus off it: there is no focus to lose,
// so no blur is ever fired and nothing else was listening. The `<body>` sweep
// ran only on a RE-RENDER, so moving somewhere that does not rebuild the editor
// left the menu hanging.
//
// So the two things worth pinning are (1) opening from the arrow leaves the box
// FOCUSED, and (2) a press anywhere outside closes the menu regardless — while
// a press on a menu row still commits, since that is the one press that must
// not be treated as a dismissal.
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

// The Header as the server describes it: `type` and `chain` both freeform,
// which is the pair the report names (the detail-line `type` is the same
// control through renderTable).
const SECTION = {
  index: 0,
  name: "Header",
  fields: [
    { name: "type", start: null, size: 20, type: "char",
      options: { styleHeaders: "styleHeaders" },
      hidden: false, editable: true, literal: false, freeform: true },
    { name: "chain", start: null, size: 9, type: "char",
      options: { "01": "TJMAXX", "04": "Winners", "06": "HomeSense" },
      hidden: false, editable: true, literal: false, freeform: true },
  ],
  records: [{ index: 0, values: { type: "styleHeaders", chain: "04" } }],
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return {"
      + " renderForm: typeof renderForm === 'function' ? renderForm : null,"
      + " closeStrayFieldMenus: typeof closeStrayFieldMenus === 'function'"
      + "   ? closeStrayFieldMenus : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => new Promise(() => {}), global.confirm, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

check("app.js renders the editor form", typeof api.renderForm === "function");
check("app.js exposes the stray-menu sweep",
      typeof api.closeStrayFieldMenus === "function");

// Render the real editor form and take one freeform box out of it, so the
// control under test is the one the user actually gets.
function build(fieldName) {
  doc.body.children = [];
  const form = api.renderForm(SECTION);
  doc.body.appendChild(form);
  const ctrl = descendants(form).filter(
    (e) => e.dataset && e.dataset.field === (fieldName || "type"))[0];
  return ctrl
    ? { ctrl, menu: ctrl.okgenMenu, arrow: ctrl.okgenArrow }
    : { ctrl: null, menu: null, arrow: null };
}
const onBody = (m) => !!m && m.parentNode === doc.body;
const isOpen = (m) => onBody(m) && !m.classList.contains("hidden");

let focused = 0;
function trackFocus(ctrl) { ctrl.focus = () => { focused++; }; }

// --------------------------------------------------------------------------
// Opening from the ARROW — the case with no focus to lose
// --------------------------------------------------------------------------
let c = build();
check("a freeform field has an arrow and a menu", !!c.arrow && !!c.menu);
check("the menu starts closed", !isOpen(c.menu));

focused = 0;
trackFocus(c.ctrl);
c.arrow.dispatchEvent({ type: "mousedown" });
check("the arrow opens the menu", isOpen(c.menu));
// THE FIX: without focus on the box there is no blur, and blur was the only
// interactive close. A menu opened from the arrow used to be unclosable.
check("...and hands focus to the box, so blur can still close it", focused === 1);

// --------------------------------------------------------------------------
// A press somewhere else closes it — whatever was pressed
// --------------------------------------------------------------------------
const elsewhere = doc.createElement("div");     // a plain div takes no focus
doc.body.appendChild(elsewhere);
doc.dispatchEvent({ type: "mousedown", target: elsewhere });
check("pressing elsewhere closes the menu", !onBody(c.menu));
check("...and the box stops announcing an open list",
      c.ctrl.getAttribute("aria-expanded") === "false");

// The same for a menu opened by FOCUSING the box, which was the working case —
// it must not regress.
c = build();
c.ctrl.dispatchEvent({ type: "focus" });
check("focusing the box also opens the menu", isOpen(c.menu));
doc.dispatchEvent({ type: "mousedown", target: elsewhere });
check("...and an outside press closes that one too", !onBody(c.menu));

// --------------------------------------------------------------------------
// The presses that must NOT be treated as a dismissal
// --------------------------------------------------------------------------
c = build();
c.ctrl.dispatchEvent({ type: "focus" });
const row = descendants(c.menu).filter((e) => (e.className || "").includes("fval-opt"))[0];
check("the menu has option rows", !!row);
doc.dispatchEvent({ type: "mousedown", target: row });
// Load-bearing: the document listener is on the BUBBLE phase, so a row's own
// mousedown commits first. Closing on the way IN would make the menu
// unusable — every choice would be swallowed.
check("pressing a menu row does NOT count as pressing elsewhere", onBody(c.menu));

c = build();
c.ctrl.dispatchEvent({ type: "focus" });
doc.dispatchEvent({ type: "mousedown", target: c.ctrl });
check("pressing the box itself does not close its own menu", onBody(c.menu));

c = build();
c.ctrl.dispatchEvent({ type: "focus" });
doc.dispatchEvent({ type: "mousedown", target: c.arrow });
check("pressing the arrow does not close it either (the arrow toggles)",
      onBody(c.menu));

// --------------------------------------------------------------------------
// The sweep still does its original job, and now tidies up after itself
// --------------------------------------------------------------------------
c = build();
c.ctrl.dispatchEvent({ type: "focus" });
check("open before the sweep", isOpen(c.menu));
api.closeStrayFieldMenus();
check("the sweep still removes an orphaned menu", !onBody(c.menu));
check("...and resets the field's aria-expanded",
      c.ctrl.getAttribute("aria-expanded") === "false");

// --------------------------------------------------------------------------
// The wiring is REAL — the stub used to swallow document listeners
// --------------------------------------------------------------------------
// If document.addEventListener were still a no-op, every dismissal check above
// would pass or fail for the wrong reason, so assert the handler is actually
// registered rather than merely accepted.
check("app.js registers a document-level mousedown handler",
      !!(doc._handlers && (doc._handlers.mousedown || []).length >= 1));
check("app.js does not close on the CAPTURE phase, which would eat a choice",
      !/addEventListener\("mousedown",[\s\S]{0,400}?,\s*true\s*\)/.test(src));

process.exit(failures ? 1 : 0);
