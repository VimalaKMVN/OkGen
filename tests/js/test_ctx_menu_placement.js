// The right-click menu must be fully visible, and must go away when you click
// somewhere else.
//
// Both user-reported, and both are about the SHARED `#ctxMenu` element that the
// file menu, the folder menu and the Bulk Actions dropdown all reuse.
//
// 1. PLACEMENT. Each caller set `left`/`top` straight from the click with no
//    clamping, and `.ctx-menu` is `position: fixed` — which is why scrolling
//    never helped, the clipping is against the VIEWPORT, not the page. The file
//    menu is ~446px tall (15 items x 28px + separators + padding), so on an
//    800px window every right-click below y=354 was cut off: the bottom 44% of
//    the tree. It now prefers below, FLIPS above, and — when it fits in neither
//    direction, which a 446px menu does not on a short window — pins to the top
//    and caps its height so the list scrolls inside itself.
//
// 2. DISMISSAL. `document.addEventListener("click", hideCtxMenu)` existed but
//    was on the BUBBLE phase, so the four handlers calling stopPropagation()
//    suppressed it. The worst is the FOLDER row (a nested folder must not
//    toggle its parent) — while FILE rows do not stop propagation, so clicking
//    a file closed the menu and clicking a folder did not, which is why it
//    looked intermittent.
//
// What the stub CANNOT prove, stated so the coverage is not overread: it does
// not implement propagation at all, so "capture beats stopPropagation" is not
// observable here. That half is asserted against the source instead.
const fs = require("fs");
const path = require("path");
const { install } = require("./dom-stub.js");

const { doc } = install();

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const CSS = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "styles.css");
const src = fs.readFileSync(APP, "utf8");
const css = fs.readFileSync(CSS, "utf8");

let failures = 0;
function check(label, cond) {
  console.log((cond ? "ok   " : "FAIL ") + label);
  if (!cond) failures++;
}

// The menu element the app looks up. Without a real one, `$("#ctxMenu")` hands
// back a DETACHED element and every placement assertion would be measuring a
// throwaway object — passing regardless of what the app did.
// Taken from the document REGISTRY, not hand-built: the stub resolves
// `#id` through a registry keyed by an internal `_id`, so an element merely
// carrying `id = "ctxMenu"` is NOT the one `$("#ctxMenu")` returns — every
// assertion would then be measuring a throwaway object while the app moved a
// different element entirely.
const menu = doc.querySelector("#ctxMenu");
menu.className = "ctx-menu hidden";
doc.body.appendChild(menu);

// Every symbol is typeof-probed: naming one directly throws while the module
// loads, which reports a single error having run ZERO checks — the truncated
// run this repo keeps hitting when diffing against an older tag.
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return {"
      + " placeCtxMenu: typeof placeCtxMenu === 'function' ? placeCtxMenu : null,"
      + " hideCtxMenu: typeof hideCtxMenu === 'function' ? hideCtxMenu : null,"
      + " showBulkMenu: typeof showBulkMenu === 'function' ? showBulkMenu : null,"
      + " state: typeof state !== 'undefined' ? state : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => new Promise(() => {}), global.confirm, global.prompt, () => {});
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

check("app.js exposes a shared placeCtxMenu", typeof api.placeCtxMenu === "function");
check("app.js exposes hideCtxMenu", typeof api.hideCtxMenu === "function");
// Deliberately NOT bailing out here. Exiting when the symbol is absent makes a
// run against an older tag report two checks and stop, which is the truncated
// run this repo keeps recording — the differential then measures nothing. Each
// placement check fails on its own terms instead.

const H = 446;                 // the real file menu, measured from the CSS
const W = 220;
function place(x, y, h, w) {
  if (typeof api.placeCtxMenu !== "function") return {};
  menu.classList.add("hidden");
  menu.style = {};
  menu._rect = { top: 0, left: 0, right: 0, bottom: 0,
                 height: h === undefined ? H : h, width: w === undefined ? W : w };
  api.placeCtxMenu(x, y);
  return menu.style;
}
const px = (v) => parseInt(String(v || "").replace("px", ""), 10);

// --------------------------------------------------------------------------
// 1. it opens at the cursor when there is room
// --------------------------------------------------------------------------
let st = place(300, 100);
check("with room below, the menu opens AT the cursor", px(st.top) === 100);
check("...and at the cursor horizontally", px(st.left) === 300);
check("...and is no longer hidden", !menu.classList.contains("hidden"));
check("...with no height cap", !st.maxHeight);

// --------------------------------------------------------------------------
// 2. it FLIPS above when the bottom would clip — the reported bug
// --------------------------------------------------------------------------
st = place(300, 700);                       // 700 + 446 = 1146 > 800
check("near the bottom it flips ABOVE the cursor", px(st.top) === 700 - H);
check("...so its bottom edge lands on the cursor", px(st.top) + H === 700);
check("...and it stays on screen", px(st.top) >= 0 && px(st.top) + H <= 800);
check("...without needing to scroll", !st.maxHeight);

// The boundaries, and they are THREE bands rather than two — which is the
// thing that makes "flip it upwards" insufficient on its own. With a 446px
// menu in an 800px window:
//
//     y <= 346        fits below            -> open at the cursor
//     347 .. 453      fits NEITHER way      -> pin to the top and scroll
//     y >= 454        fits above            -> flip
//
// The middle band is 107px wide here and grows as the window shrinks; on a
// window shorter than 446+16 it swallows the screen entirely. A build that
// only flipped would clip every click inside it.
st = place(300, 346);
check("last y that fits below: no flip", px(st.top) === 346 && !st.maxHeight);
st = place(300, 347);
check("one pixel further: fits neither way, so it pins and scrolls",
      px(st.top) === 8 && px(st.maxHeight) === 800 - 16);
st = place(300, 453);
check("still in the neither-fits band at 453", px(st.top) === 8 && !!st.maxHeight);
st = place(300, 454);
check("at 454 there is finally room ABOVE, so it flips",
      px(st.top) === 454 - H && !st.maxHeight);

// --------------------------------------------------------------------------
// 3. taller than the window: pin + scroll, because no position fits
// --------------------------------------------------------------------------
st = place(300, 700, 900);                  // 900px menu in an 800px window
check("a menu taller than the window pins to the top", px(st.top) === 8);
check("...and caps its height to the window", px(st.maxHeight) === 800 - 16);
check("...so every option stays reachable by scrolling",
      px(st.maxHeight) > 0 && px(st.maxHeight) <= 800);
check("the stylesheet lets that height scroll",
      /\.ctx-menu\s*\{[^}]*overflow-y:\s*auto/.test(css));

// --------------------------------------------------------------------------
// 4. the right edge gets the same treatment
// --------------------------------------------------------------------------
st = place(1150, 100);                      // 1150 + 220 = 1370 > 1200
check("near the right edge it shifts LEFT", px(st.left) === 1150 - W);
check("...and stays on screen", px(st.left) >= 0 && px(st.left) + W <= 1200);
st = place(300, 100);
check("with room to the right it does not shift", px(st.left) === 300);

// never negative, however extreme the click
st = place(5, 5, 2000, 2000);
check("an absurd menu never gets a negative offset",
      px(st.top) >= 0 && px(st.left) >= 0);

// --------------------------------------------------------------------------
// 5. all three menus share the one placement function
// --------------------------------------------------------------------------
check("the file menu uses the shared placement",
      /showCtxMenu[\s\S]{0,4000}?placeCtxMenu\(e\.clientX, e\.clientY\)/.test(src));
check("the folder menu uses it too",
      (src.match(/placeCtxMenu\(e\.clientX, e\.clientY\)/g) || []).length === 2);
check("the Bulk Actions dropdown uses it too",
      /placeCtxMenu\(Math\.max\(8, r\.right - 220\), r\.bottom \+ 4\)/.test(src));
check("no menu sets a raw top/left any more",
      !/menu\.style\.top = e\.clientY/.test(src));

// --------------------------------------------------------------------------
// 6. dismissal
// --------------------------------------------------------------------------
function openMenu() { menu.classList.remove("hidden"); }
const isOpen = () => !menu.classList.contains("hidden");

const outside = doc.createElement("div");
outside.className = "tree-row";
doc.body.appendChild(outside);

openMenu();
check("the menu is open before the outside click", isOpen());
doc.dispatchEvent({ type: "click", target: outside });
check("a click OUTSIDE closes it", !isOpen());

// a press on a menu ROW must NOT be treated as a dismissal — that is the one
// press that has to survive, or every menu choice is swallowed
const item = doc.createElement("div");
item.className = "ctx-item";
menu.appendChild(item);
openMenu();
doc.dispatchEvent({ type: "click", target: item });
check("a click on a menu ITEM does not dismiss it", isOpen());

openMenu();
doc.dispatchEvent({ type: "keydown", key: "Escape" });
check("Escape closes it", !isOpen());

openMenu();
doc.dispatchEvent({ type: "keydown", key: "a" });
check("another key does not", isOpen());

// ***The reported bug, reproduced.*** A FOLDER row stops propagation so a
// nested folder does not toggle its parent — which suppressed the old
// bubble-phase listener and left the menu on screen. Dispatched with
// `_propagate`, so the stub runs the target's own handler and honours its
// stopPropagation; a capture listener must survive it.
//
// This is the check that distinguishes the builds. Without it the suite could
// not tell the fix from the bug at all: a stub that models no propagation
// fires a bubble listener just as happily, so "a click outside closes it"
// passes on BOTH tags.
const folderRow = doc.createElement("div");
folderRow.className = "tree-row";
doc.body.appendChild(folderRow);
folderRow.addEventListener("click", (e) => { e.stopPropagation(); });

openMenu();
doc.dispatchEvent({ type: "click", target: folderRow, _propagate: true });
check("a click on a FOLDER row (which stops propagation) still closes it",
      !isOpen());

// and the same press must NOT be swallowed for the folder's own purpose
let toggled = 0;
const folder2 = doc.createElement("div");
doc.body.appendChild(folder2);
folder2.addEventListener("click", (e) => { e.stopPropagation(); toggled++; });
openMenu();
doc.dispatchEvent({ type: "click", target: folder2, _propagate: true });
check("...while the folder's own handler still runs", toggled === 1);

check("the dismiss listener is registered on CAPTURE",
      /document\.addEventListener\("click",[\s\S]{0,600}?\}, true\);/.test(src));
check("it is click, not mousedown (hiding on mousedown swallows the choice)",
      !/document\.addEventListener\("mousedown",[\s\S]{0,300}?hideCtxMenu/.test(src));
check("the folder row still stops propagation (its own reason is unchanged)",
      /row\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); toggleFolder/.test(src));

// --------------------------------------------------------------------------
// 7. the dismisser must not eat the click that OPENS a menu
// --------------------------------------------------------------------------
// The capture listener fires on EVERY click, including the one on the Bulk
// Actions button. Capture runs BEFORE the button's own handler, so the correct
// sequence is hide-then-open; if it ran after, the button would open a menu
// that was immediately closed and the control would look dead.
if (api.showBulkMenu && api.state) {
  const btn = doc.querySelector("#bulkBtn");
  btn._rect = { top: 0, left: 0, right: 300, bottom: 40, width: 80, height: 40 };
  api.state.selection = new Set(["/f/a.OK"]);   // it early-returns on none
  menu._rect = { top: 0, left: 0, right: 0, bottom: 0, height: 300, width: 220 };
  openMenu();
  doc.dispatchEvent({ type: "click", target: btn });
  check("the capture dismisser closes the old menu first", !isOpen());
  api.showBulkMenu();
  check("...and the button's own handler still opens the new one", isOpen());
} else {
  check("Bulk Actions menu reachable for the ordering check", false);
}

console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
process.exit(failures ? 1 : 0);
