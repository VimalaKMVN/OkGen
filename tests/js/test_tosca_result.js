// Executes the real app.js TOSCA result window against a stub DOM.
//
// Running a TOSCA script launches its OWN console window (D21: CREATE_NEW_CONSOLE
// + `cmd /k`, deliberately visible). That window opens on top of the browser and
// covered a CENTRED result card, so OkGen's own messages were rendered but
// unreadable. The fix pins this one window to the bottom-right corner.
//
// Asserts the corner variant is applied HERE and nowhere else, and that the
// window still carries everything it did before (workbook, launched .bat, rows,
// skipped files, errors, a working Close).
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

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

const RESULT = {
  script: "FUN_LASER",
  written: 3,
  workbook: "D:\\ToscaAutomation\\TestData\\FUN_LASER_TestData.xlsm",
  launched: true,
  bat: "D:\\ToscaAutomation\\Scripts\\FUN_LASER_ExecutionScript.bat",
  rows: [
    { chain: "TJMAXX", process: "Style Header", format: "A - Regular Tag" },
    { chain: "Winners", process: "Pre-Ticket", format: "B - Blue Gum" },
  ],
  skipped: [{ file: "StyleHeader.OK", error: "not applicable" }],
  errors: [{ file: "Broken.json", error: "no chain" }],
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { showToscaResult, showConvertResult: typeof showConvertResult === 'function'"
      + " ? showConvertResult : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

api.showToscaResult(RESULT);

const all = descendants(doc.body);
const overlay = all.find((e) => (e.className || "").includes("modal-overlay"));
check("a result window is rendered", !!overlay);
check("it is pinned to the corner", !!overlay
      && (overlay.className || "").includes("modal-corner"));
check("it keeps the wide/scrolling result card", all.some(
  (e) => (e.className || "").includes("modal-card")
      && (e.className || "").includes("modal-wide")));

const text = all.map((e) => e.textContent || "").join(" | ");
check("it still names the script and row count", /FUN_LASER/.test(text) && /3 row/.test(text));
check("it still shows the workbook path", /FUN_LASER_TestData\.xlsm/.test(text));
check("it still shows the launched .bat", /FUN_LASER_ExecutionScript\.bat/.test(text));
check("it still lists the written rows", /Regular Tag/.test(text) && /Blue Gum/.test(text));
check("it still reports skipped files", /StyleHeader\.OK/.test(text));
check("it still reports errors", /Broken\.json/.test(text));

// Close still works — a corner window that cannot be dismissed is worse than a
// covered one.
const closeBtn = all.find((e) => (e.textContent || "") === "Close");
check("it has a Close button", !!closeBtn);
if (closeBtn) {
  closeBtn.click();
  check("Close removes the window",
        !descendants(doc.body).some((e) => (e.className || "").includes("modal-overlay")));
}

// ---- the corner treatment is scoped to THIS window -----------------------
// Count the class being APPLIED (a className string), not merely mentioned —
// the code comment above the call names it too.
const cornerUses = (src.match(/"modal-overlay modal-corner"/g) || []).length;
check("app.js applies modal-corner exactly once", cornerUses === 1);
check("every other modal overlay stays centred",
      (src.match(/el\("div",\s*"modal-overlay"\)/g) || []).length >= 1);
check("that one use is in showToscaResult",
      /function showToscaResult[\s\S]{0,400}?modal-corner/.test(src));

// ---- the stylesheet actually positions it -------------------------------
check("styles.css defines the corner variant", /\.modal-overlay\.modal-corner\s*\{/.test(css));
const block = (css.match(/\.modal-overlay\.modal-corner\s*\{([^}]*)\}/) || [])[1] || "";
check("it anchors to the bottom", /align-items:\s*flex-end/.test(block));
check("it anchors to the right", /justify-content:\s*flex-end/.test(block));
check("the centred overlay is untouched (still a flex box)",
      /\.modal-overlay\s*\{[^}]*align-items:\s*center[^}]*\}/.test(css));

process.exit(failures ? 1 : 0);
