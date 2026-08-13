// Executes the real app.js TOSCA result window against a stub DOM.
//
// Running a TOSCA script launches its OWN console window (D21: CREATE_NEW_CONSOLE
// + `cmd /k`, deliberately visible). That window opens on top of the browser and
// covered a CENTRED result card, so OkGen's own messages were rendered but
// unreadable. The fix pins this one window to the bottom-right corner.
//
// Asserts the corner variant is applied HERE and nowhere else.
//
// The window is now a SUMMARY: counts, and one line per Chain/Process/Format.
// Every file name and absolute path moved into the report behind "View report"
// (and into logs/okgen_tosca_*.log). So the checks below come in pairs — the
// summary must NOT carry a detail, and the report MUST — because "the path is
// gone from the window" is only correct if it is reachable somewhere, and a
// test that asserted only its absence would pass on a build that lost it.
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
  staging: { enabled: true, configured: true, copied: 9, removed: 4, created: 1,
             folders: [{ path: "D:\\in\\TJMAXX\\Style Header\\A",
                         chain: "TJMAXX", process: "Style Header",
                         format: "A - Regular Tag",
                         copied: ["a.json", "b.json"], removed: ["old.json"] }],
             // Carried BOTH here and in `combinations`, exactly as the server
             // sends it: the count is read from `excluded` (the planner's own
             // field, always present) and the table from `combinations`.
             excluded: [{ chain: "Homegoods", process: "Pre-Ticket",
                          format: "J - Rat Tail", files: ["x.json", "y.json"],
                          reasons: ["format 'J' not in Key column E"] }] },
  combinations: [
    { chain: "TJMAXX", process: "Style Header", format: "A - Regular Tag",
      status: "written", copied: 6, removed: 4, created: 1, files: ["a.json"] },
    { chain: "Winners", process: "Pre-Ticket", format: "B - Blue Gum",
      status: "written", copied: 3, removed: 0, created: 0, files: ["c.json"] },
    { chain: "Homegoods", process: "Pre-Ticket", format: "J - Rat Tail",
      status: "not_run", copied: 0, removed: 0, created: 0,
      files: ["x.json", "y.json"], reasons: ["format 'J' not in Key column E"] },
  ],
  skipped: [{ file: "StyleHeader.OK", error: "not applicable" }],
  errors: [{ file: "Broken.json", error: "no chain" }],
  started: "2026-08-13 09:41:07",
  elapsed_seconds: 1.8,
  report: "OkGen — Run TOSCA Script\nWorkbook : D:\\ToscaAutomation\\TestData\\"
        + "FUN_LASER_TestData.xlsm\n  [NOT RUN ] Homegoods  Pre-Ticket  J - Rat Tail\n"
        + "  files : x.json, y.json\n  StyleHeader.OK   not applicable\n"
        + "  Broken.json   no chain\n  started : FUN_LASER_ExecutionScript.bat\n",
  log: "C:\\OkGen\\logs\\okgen_tosca_20260813_094107.log",
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  // Every symbol is probed with `typeof` rather than named directly: a build
  // that lacks one throws a ReferenceError while the module loads, and the
  // suite then reports "1 failure" having run no checks at all — the
  // truncated-run tell this file's family keeps producing. Absent must FAIL,
  // never crash, or the differential against an older tag measures nothing.
  src + "\n;return {"
      + " showToscaResult: typeof showToscaResult === 'function' ? showToscaResult : null,"
      + " showToscaReport: typeof showToscaReport === 'function' ? showToscaReport : null,"
      + " showConvertResult: typeof showConvertResult === 'function' ? showConvertResult : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

check("app.js exports showToscaResult", typeof api.showToscaResult === "function");
check("app.js exports showToscaReport", typeof api.showToscaReport === "function");
if (!api.showToscaResult) { console.log("FAIL no result window to test"); process.exit(1); }
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

// ---- the summary: counts and combinations, nothing else ------------------
check("it shows the four counts", all.some((e) => (e.className || "") === "tosca-stats"));
const statLabels = all.filter((e) => (e.className || "") === "tosca-stat-l")
                      .map((e) => e.textContent || "");
check("the counts are rows / staged / removed / not-run",
      statLabels.join(",") === "Rows written,Files staged,Files removed,Not run");
const statNums = all.filter((e) => (e.className || "").startsWith("tosca-stat-n"))
                    .map((e) => e.textContent || "");
check("the counts carry the run's real numbers", statNums.join(",") === "3,9,4,1");
// Amber ONLY above zero: a permanently-amber "Not run: 0" would make every
// clean run read as a warning, which is how a real one stops being noticed.
const notRunNum = all.filter((e) => (e.className || "").startsWith("tosca-stat-n"))[3];
check("a non-zero 'not run' is amber", !!notRunNum
      && (notRunNum.className || "").includes("warn"));

check("it lists every combination", /Regular Tag/.test(text) && /Blue Gum/.test(text));
// The excluded combination stays IN the table: once the names are hidden, a
// combination that silently did not run looks exactly like a successful one.
check("an excluded combination is still shown", /Rat Tail/.test(text));
check("it is marked as not run", all.some(
  (e) => (e.className || "") === "tosca-chip" && /not run/.test(e.textContent || "")));
check("its row is styled as excluded", all.some(
  (e) => (e.className || "") === "excluded"));
// Read the CELLS, not the table's own textContent — the stub does not
// aggregate descendants, so a container check would compare "" and pass or
// fail for reasons that have nothing to do with the counts.
const numCells = all.filter((e) => (e.className || "") === "num")
                    .map((e) => e.textContent || "");
const numText = all.filter((e) => (e.className || "") === "num")
                   .concat(all.filter((e) => e.tagName === "B"))
                   .map((e) => e.textContent || "").join(",");
check("per-combination copied/removed counts are on the row",
      /(^|,)6(,|$)/.test(numText) && /(^|,)4(,|$)/.test(numText)
      && /(^|,)3(,|$)/.test(numText));
// The excluded row must not just leave the two count columns blank — a pair of
// empty cells reads as "zero copied, zero removed", which is a different claim
// from "this did not run at all". It spans them with the chip instead.
check("the excluded row spans the count columns with its chip",
      all.some((e) => (e.className || "") === "num" && e.colSpan === 2
                   && (e.children || []).some(
                        (c) => (c.className || "") === "tosca-chip")));

// ---- the detail is GONE from the summary ---------------------------------
check("the workbook path is NOT in the summary", !/FUN_LASER_TestData\.xlsm/.test(text));
check("the .bat path is NOT in the summary", !/FUN_LASER_ExecutionScript\.bat/.test(text));
check("staged file names are NOT in the summary", !/a\.json/.test(text));
check("removed file names are NOT in the summary", !/old\.json/.test(text));
check("the excluded files' names are NOT in the summary", !/x\.json/.test(text));
// …but the FACT of each is, in counts, so nothing becomes invisible.
check("skipped and error counts are still stated",
      /not applicable to this script/.test(text) && /could not be used/.test(text));

// ---- View report carries the detail --------------------------------------
const viewBtn = all.find((e) => (e.textContent || "") === "View report");
check("there is a View report button", !!viewBtn);
if (viewBtn) {
  viewBtn.click();
  const after = descendants(doc.body);
  const rtext = after.map((e) => e.textContent || "").join(" | ");
  check("the report window opens", after.filter(
    (e) => (e.className || "").includes("modal-overlay")).length === 2);
  check("the report is NOT pinned to the corner", after.some(
    (e) => (e.className || "") === "modal-overlay"));
  check("the report shows the workbook path", /FUN_LASER_TestData\.xlsm/.test(rtext));
  check("the report shows the .bat", /FUN_LASER_ExecutionScript\.bat/.test(rtext));
  check("the report shows the excluded files", /x\.json/.test(rtext));
  check("the report shows the skipped file", /StyleHeader\.OK/.test(rtext));
  check("the report shows the errored file", /Broken\.json/.test(rtext));
  check("the report uses the Send report's monospace block", after.some(
    (e) => (e.className || "") === "send-report-text"));
  check("it names the log file it was also written to",
        /okgen_tosca_20260813_094107\.log/.test(rtext));
  check("it offers Copy report", after.some((e) => (e.textContent || "") === "Copy report"));
  // close the report again so the Close checks below act on the summary
  const rc = after.filter((e) => (e.textContent || "") === "Close").pop();
  if (rc) rc.click();
}

// A log that could not be written must not be claimed. Rendered separately so
// the assertion is about the ABSENCE of the line, not about a falsy string
// being printed as "undefined".
// Guarded on the function EXISTING: without the guard, a build with no report
// window at all renders nothing, the "no log line" assertion finds nothing, and
// it passes — proving the opposite of what it claims. Both halves are asserted,
// so the pair fails on a build that lacks the window rather than one of them
// passing vacuously.
if (!api.showToscaReport) {
  check("with a log, an 'Also written to' line", false);
  check("with no log written, no 'Also written to' line", false);
} else {
  api.showToscaReport(RESULT);                        // log present -> line shown
  check("with a log, an 'Also written to' line", descendants(doc.body).some(
    (e) => (e.className || "") === "send-report-log"));
  let c = descendants(doc.body).filter((e) => (e.textContent || "") === "Close").pop();
  if (c) c.click();

  const noLog = Object.assign({}, RESULT, { log: null });
  api.showToscaReport(noLog);
  const nodes = descendants(doc.body);
  check("with no log written, no 'Also written to' line", !nodes.some(
    (e) => (e.className || "") === "send-report-log"));
  c = nodes.filter((e) => (e.textContent || "") === "Close").pop();
  if (c) c.click();
}

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
