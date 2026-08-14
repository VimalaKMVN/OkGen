// The TOSCA run confirmation now shows what a run will do to the INPUT FOLDERS,
// and the result window reports what it did.
//
// A run stages the selected files into TOSCA's own input tree, and staging
// CLEARS each folder first — so the dialog that gates the run is the last point
// at which a delete can be called off. That makes this a confirmation about
// destruction, not just about a script choice, and the things worth pinning are
// the ones that make it checkable: the folder PATH, the count, and the NAMES of
// the files about to be deleted. A count alone cannot show you it is about to
// delete the wrong thing.
//
// Also pinned: a combination that will NOT run (its format folder is missing,
// or is named differently from the workbook's Key sheet) is called out HERE,
// before the run, rather than only in the report afterwards — the fix for it is
// in the Key sheet and there is nothing OkGen can do about it at run time.
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

const PREVIEW = {
  script: "OK Functional Laser",
  enabled: true,
  configured: true,
  rows: 1,
  remove_total: 2,
  copy_total: 1,
  targets: [{
    path: "D:\\ToscaAutomation\\FUN_LASER_OK_Files\\T.J. Maxx\\Style Header\\A - Purple Tag",
    chain: "T.J. Maxx", process: "Style Header", format: "A - Purple Tag",
    status: "ok",
    remove: ["previous_one.OK", "previous_two.OK"],
    copy: ["SH_0001.OK"],
  }],
  // Computed server-side (tosca.plan_combinations) so the dialog, its View
  // report and the plan log all count the same way.
  combinations: [
    { chain: "T.J. Maxx", process: "Style Header", format: "A - Purple Tag",
      status: "will_run", copy: 1, remove: 2, create: 0 },
    { chain: "T.J. Maxx", process: "Style Header", format: "T-Q-Line Small Gum Label",
      status: "will_not_run", copy: 0, remove: 0, files: ["SH_0009.OK"],
      reasons: ["the Key sheet says 'T-Q-Line Small Gum Label' but the folder is named 'T - Q-Line Small Gum Label'. TOSCA builds its input path from the Key sheet. Fix the folder name and/or the Key sheet name to the correct format name, to match the GTA UI."] },
  ],
  excluded: [{
    chain: "T.J. Maxx", process: "Style Header",
    format: "T-Q-Line Small Gum Label",
    files: ["SH_0009.OK"],
    reasons: ["the Key sheet says 'T-Q-Line Small Gum Label' but the folder is named 'T - Q-Line Small Gum Label'. TOSCA builds its input path from the Key sheet. Fix the folder name and/or the Key sheet name to the correct format name, to match the GTA UI."],
  }, {
    // a SECOND failure, so "list them all" is exercised rather than assumed —
    // a renderer that showed only the first would look identical with one
    chain: "Marshalls", process: "Pre-Ticket",
    format: "J- Rat Tail Gum Label",
    files: ["PT_0011.OK", "PT_0012.OK"],
    reasons: ["the Key sheet says 'J- Rat Tail Gum Label' but the folder is named "
              + "'J - Rat Tail Gum Label'. TOSCA builds its input path from the Key "
              + "sheet. Fix the folder name and/or the Key sheet name to the correct "
              + "format name, to match the GTA UI."],
  }],
};

// A fetch stub that answers /api/tosca/preview and records what was asked.
const asked = [];
let answer = PREVIEW;
const PLAN_LOG = {
  report: "OkGen — Run TOSCA Script: STAGING PLAN (nothing has run yet)\n"
        + "  D:\\ToscaAutomation\\FUN_LASER_OK_Files\\T.J. Maxx\\Style Header\\A - Purple Tag\n"
        + "      DELETE (2):\n        previous_one.OK, previous_two.OK\n"
        + "      COPY (1):\n        SH_0001.OK\n",
  log: "C:\\OkGen\\logs\\okgen_tosca_plan_20260813_101500.log",
};
function fetchStub(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : {};
  asked.push({ url, body });
  const payload = String(url).indexOf("plan-log") !== -1 ? PLAN_LOG : answer;
  return Promise.resolve({ ok: true, statusText: "OK", json: () => Promise.resolve(payload) });
}

// Every export is resolved DEFENSIVELY. A missing function must make the checks
// below FAIL one by one; naming it directly throws at load and reports a
// truncated run instead, which is how a suite hides the failures under it.
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return {"
      + " pickTosca: typeof pickTosca === 'function' ? pickTosca : null,"
      + " showToscaResult: typeof showToscaResult === 'function' ? showToscaResult : null,"
      + " renderToscaPlan: typeof renderToscaPlan === 'function' ? renderToscaPlan : null,"
      + " showToscaPlanReport: typeof showToscaPlanReport === 'function' ? showToscaPlanReport : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            fetchStub, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

const SCRIPTS = [{ name: "OK Functional Laser" }, { name: "OK Regression Laser" }];
const PATHS = ["C:/data/SH_0001.OK", "C:/data/SH_0009.OK"];

function texts() {
  return descendants(doc.body).map((e) => e.textContent || "").join(" | ");
}
function byClass(cls) {
  return descendants(doc.body).filter((e) => (e.className || "").includes(cls));
}
// Read the PLAN BLOCK, not the whole dialog.
//
// Load-bearing: the older confirmation takes (scripts, COUNT, warning), so
// handing it the paths array renders "Run TOSCA on C:/data/SH_0001.OK,… file(s)"
// in its TITLE — and a check that merely searched the document for a file name
// passed against the very build that has no plan at all. Three of these were
// vacuous before this helper existed.
function planText() {
  const box = byClass("tosca-plan")[0];
  if (!box) return "";
  return [box].concat(descendants(box)).map((e) => e.textContent || "").join(" | ");
}
const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  check("app.js exposes the run confirmation", typeof api.pickTosca === "function");
  check("app.js exposes the input-folder plan renderer",
        typeof api.renderToscaPlan === "function");
  check("app.js exposes the result window", typeof api.showToscaResult === "function");
  if (api.pickTosca) {
    // Older builds take (scripts, COUNT, warning); passing the paths array is
    // harmless there and the plan checks below simply find nothing.
    api.pickTosca(SCRIPTS, PATHS, "Check the PowerForms link.");
  }

  // ---- the dialog asks for the plan, for the SELECTED script -------------
  await tick(); await tick();
  check("the dialog requests the staging preview",
        asked.some((a) => a.url === "/api/tosca/preview"));
  const req = asked.find((a) => a.url === "/api/tosca/preview") || { body: {} };
  check("it asks about the script that is selected",
        req.body.script === "OK Functional Laser");
  check("it sends the selected paths",
        Array.isArray(req.body.paths) && req.body.paths.length === 2);

  // ---- what it shows (asserted INSIDE the plan block) ---------------------
  let p = planText();
  check("it renders a plan block", byClass("tosca-plan").length > 0);
  check("it says how many files will be copied", /1 file\(s\) will be copied/.test(p));
  check("it says how many will be DELETED", /2 existing file\(s\) that will be DELETED/.test(p));
  // COUNTS ONLY in the dialog — the user's call: the delete list made this
  // window too tall to use, and the line above plus the Delete column say a
  // delete is coming. The names move to View report and the plan log, which the
  // checks further down prove they actually reach — an absence assertion alone
  // would pass on a build that simply lost them.
  check("it does NOT name the files it will delete",
        p.indexOf("previous_one.OK") === -1 && p.indexOf("previous_two.OK") === -1);
  check("it does NOT name the files it will copy", p.indexOf("SH_0001.OK") === -1);
  check("it does NOT print the full folder path", p.indexOf("FUN_LASER_OK_Files") === -1);
  // …but the combination is still there as a row with its two counts
  check("each combination is a row with copy/delete counts",
        byClass("tosca-mini").length > 0
        && p.indexOf("A - Purple Tag") !== -1);

  // ---- the combination that cannot run -----------------------------------
  // The ONE piece of detail that keeps its full text here: the fix is in the
  // workbook's Key sheet, so a bare count would send the user looking in the
  // wrong place entirely.
  check("it warns about the excluded combination", /will NOT run|will not run/.test(p));
  check("the warning names the file count", /1 file\(s\)/.test(p));
  check("the warning quotes BOTH spellings",
        p.indexOf("T-Q-Line Small Gum Label") !== -1
        && p.indexOf("T - Q-Line Small Gum Label") !== -1);
  check("the warning points at the Key sheet", /Key sheet/.test(p));
  check("the exclusion is rendered in the error colour, not plain text",
        byClass("tosca-plan").length > 0
        && descendants(byClass("tosca-plan")[0]).some(
             (e) => (e.className || "").includes("tosca-plan-bad")));
  // headings green + bold, failures red — asserted on the STYLESHEET, because
  // a class name in the DOM proves nothing about what is painted
  check("the plan headings use the green token",
        /\.tosca-plan-head\s*\{[^}]*color:\s*var\(--ok\)/.test(css));
  check("a failure heading uses the error token",
        /\.tosca-sec-label\.bad\s*\{[^}]*color:\s*var\(--err\)/.test(css));
  check("the failure text uses the error token",
        /\.tosca-plan-bad\s*\{[^}]*color:\s*var\(--err\)/.test(css));

  // ---- View report: the names the dialog no longer shows -----------------
  check("app.js exposes the plan report window",
        typeof api.showToscaPlanReport === "function");
  const viewBtn = descendants(doc.body).find((e) => (e.textContent || "") === "View report");
  check("the dialog offers View report", !!viewBtn);
  if (viewBtn) {
    viewBtn.click();
    await tick(); await tick(); await tick();
    check("it asks the server to build and LOG the plan",
          asked.some((a) => String(a.url).indexOf("/api/tosca/plan-log") !== -1));
    const req2 = asked.find((a) => String(a.url).indexOf("plan-log") !== -1) || { body: {} };
    check("it logs the plan for the SELECTED script",
          req2.body.script === "OK Functional Laser");
    const rt = texts();
    // The pair that makes moving the names out of the dialog safe: absent
    // there, PRESENT here. An absence check alone would pass on a build that
    // simply lost them.
    check("the plan report names the files to be DELETED",
          rt.indexOf("previous_one.OK") !== -1 && rt.indexOf("previous_two.OK") !== -1);
    check("the plan report names the files to be copied", rt.indexOf("SH_0001.OK") !== -1);
    check("the plan report names the folder path", rt.indexOf("FUN_LASER_OK_Files") !== -1);
    check("it says nothing has run yet", /nothing has run yet/.test(rt));
    check("it names the plan log it was written to",
          /okgen_tosca_plan_20260813_101500\.log/.test(rt));
    check("it uses the Send report's monospace block", descendants(doc.body).some(
      (e) => (e.className || "") === "send-report-text"));
    check("it offers Copy report",
          descendants(doc.body).some((e) => (e.textContent || "") === "Copy report"));
    // close it again so the gate checks below act on the dialog
    const pc = descendants(doc.body).filter((e) => (e.textContent || "") === "Close").pop();
    if (pc) pc.click();
  }

  // ---- the run is still gated, and the gate mentions the deletion --------
  const runBtn = descendants(doc.body).find((e) => (e.textContent || "") === "Run TOSCA");
  check("the Run button exists and starts disabled", !!runBtn && runBtn.disabled === true);
  check("the acknowledgement mentions the folders being cleared",
        /input folders above will be cleared/.test(texts()));
  const cb = descendants(doc.body).find((e) => e.type === "checkbox");
  if (cb) { cb.checked = true; cb.dispatchEvent({ type: "change" }); }
  check("ticking it enables Run", !!runBtn && runBtn.disabled === false);

  // ---- changing the script re-asks --------------------------------------
  const before = asked.length;
  const radios = descendants(doc.body).filter((e) => e.type === "radio");
  if (radios[1]) { radios[1].checked = true; radios[1].dispatchEvent({ type: "change" }); }
  await tick(); await tick();
  check("choosing another script reloads the plan", asked.length > before);
  const last = asked.length ? asked[asked.length - 1] : { body: {} };
  check("the reload asks about the NEW script",
        last.body.script === "OK Regression Laser");

  // ---- staging OFF says so rather than showing an empty plan -------------
  doc.body.innerHTML = "";
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  const plan = api.renderToscaPlan || (() => {});
  plan(box, { enabled: false });
  check("staging switched off is stated, not left blank",
        /staging is off/.test(box.textContent || texts()));
  box.innerHTML = "";
  plan(box, { enabled: true, configured: false });
  check("a script with no input_folders is stated too",
        /no input_folders/.test(box.textContent || texts()));
  box.innerHTML = "";
  plan(box, { enabled: true, configured: true, targets: [],
              remove_total: 0, copy_total: 0, excluded: [] });
  check("nothing to remove reads as such, not as a bare 0",
        /No existing files need removing/.test(box.textContent || texts()));

  // ---- the RESULT window reports what actually happened ------------------
  doc.body.innerHTML = "";
  (api.showToscaResult || (() => {}))({
    script: "OK Functional Laser", written: 1, workbook: "D:\\wb.xlsm",
    launched: true, bat: "D:\\run.bat",
    rows: [{ chain: "T.J. Maxx", process: "Style Header", format: "A - Purple Tag" }],
    staging: {
      enabled: true, configured: true, removed: 2, copied: 1, created: 0,
      folders: [{ path: "D:\\tree\\T.J. Maxx\\Style Header\\A - Purple Tag",
                  removed: ["previous_one.OK", "previous_two.OK"],
                  copied: ["SH_0001.OK"], created: false }],
      excluded: PREVIEW.excluded,
    },
  });
  const t = texts();
  // The run window is a SUMMARY now: what was staged and removed are COUNTS,
  // and the folder paths and file names live in the report behind
  // "View report" (and in logs/okgen_tosca_*.log).
  const nums = descendants(doc.body)
    .filter((e) => (e.className || "").startsWith("tosca-stat-n"))
    .map((e) => e.textContent || "");
  check("the summary counts what was staged", nums[1] === "1");
  check("the summary counts what was removed", nums[2] === "2");
  check("the folder PATH is not in the summary", t.indexOf("D:\\tree\\") === -1);
  // Not run stays visible as a count AND as a table row — it is the one
  // outcome that looks like success once the file names are hidden.
  // TWO now, and that is the stronger assertion: the count is derived from
  // `staging.excluded` rather than hard-coded, so adding a second failure to
  // the fixture must move it.
  check("the summary counts EVERY combination that was NOT run",
        nums[3] === String(PREVIEW.excluded.length) && nums[3] === "2");
  check("that combination is still named in the table", /Homegoods|not run/i.test(t));

  // A run that staged nothing must SAY so — "written: 1" with no files copied
  // is exactly the success-looking outcome this reports against.
  doc.body.innerHTML = "";
  (api.showToscaResult || (() => {}))({ script: "x", written: 1, workbook: "w", rows: [],
                        staging: { enabled: true, configured: false } });
  check("a run that staged nothing says why", /No input files were staged/.test(texts()));

  // ---- the stylesheet, because a long path that clips is a CSS defect ----
  check("styles.css styles the plan block", /\.tosca-plan\s*\{/.test(css));
  const pathRule = (css.match(/\.tosca-plan-path\s*\{([^}]*)\}/) || [])[1] || "";
  check("a target path wraps instead of clipping",
        /overflow-wrap:\s*anywhere/.test(pathRule));
  const filesRule = (css.match(/\.tosca-plan-files\s*\{([^}]*)\}/) || [])[1] || "";
  check("the file list wraps too", /overflow-wrap:\s*anywhere/.test(filesRule));

  // ---- the FAILURE reason must be readable in full -----------------------
  // The wide modal's body is `overflow-x: hidden`, so a line wider than the
  // card is CLIPPED with no way to reveal it — and these reasons quote folder
  // names and Key-sheet cells, which are long and can contain runs with no
  // space to break at. Asserted on the stylesheet because it is a CSS-only
  // defect: every control renders either way, so the DOM looks identical.
  const badRule = (css.match(/\.tosca-plan-bad\s*\{([^}]*)\}/) || [])[1] || "";
  check("a failure reason wraps instead of clipping",
        /overflow-wrap:\s*anywhere/.test(badRule));
  check("...and breaks inside a long unbroken run",
        /word-break:\s*break-word/.test(badRule));

  // ---- every failure is shown, with its own reason in full ---------------
  // Render the PLAN itself — by this point doc.body holds the RESULT window,
  // and asserting there would have found no plan rows at all: the checks would
  // fail for the wrong reason, or pass vacuously if written the other way.
  const badBox = doc.createElement("div");
  doc.body.appendChild(badBox);
  (api.renderToscaPlan || (() => {}))(badBox, PREVIEW);
  const badRows = descendants(badBox)
    .filter((e) => (e.className || "").indexOf("tosca-plan-bad") !== -1);
  check("EVERY combination that will not run is listed",
        badRows.length === PREVIEW.excluded.length && badRows.length === 2);
  const badText = descendants(badBox).map((e) => e.textContent || "").join(" ");
  PREVIEW.excluded.forEach((x, i) => {
    check(`failure ${i + 1} names its chain and format`,
          badText.indexOf(x.chain) !== -1 && badText.indexOf(x.format) !== -1);
    // the WHOLE reason, not a truncation — the last words carry the fix
    check(`failure ${i + 1} carries its reason to the last word`,
          badText.indexOf(x.reasons[0]) !== -1);
  });
  check("both reasons point at the GTA UI",
        (badText.match(/GTA UI/g) || []).length === 2);
  check("neither prescribes only the Key sheet",
        badText.indexOf("Key sheet is corrected") === -1);

  process.exit(failures ? 1 : 0);
})();
