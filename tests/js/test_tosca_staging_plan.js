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
  excluded: [{
    chain: "T.J. Maxx", process: "Style Header",
    format: "T-Q-Line Small Gum Label",
    files: ["SH_0009.OK"],
    reasons: ["the sheet says 'T-Q-Line Small Gum Label' but the folder is named "
              + "'T - Q-Line Small Gum Label' — TOSCA looks for the sheet's "
              + "spelling, so this combination cannot run until the workbook's "
              + "Key sheet is corrected"],
  }],
};

// A fetch stub that answers /api/tosca/preview and records what was asked.
const asked = [];
let answer = PREVIEW;
function fetchStub(url, opts) {
  const body = opts && opts.body ? JSON.parse(opts.body) : {};
  asked.push({ url, body });
  return Promise.resolve({ ok: true, statusText: "OK", json: () => Promise.resolve(answer) });
}

// Every export is resolved DEFENSIVELY. A missing function must make the checks
// below FAIL one by one; naming it directly throws at load and reports a
// truncated run instead, which is how a suite hides the failures under it.
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return {"
      + " pickTosca: typeof pickTosca === 'function' ? pickTosca : null,"
      + " showToscaResult: typeof showToscaResult === 'function' ? showToscaResult : null,"
      + " renderToscaPlan: typeof renderToscaPlan === 'function' ? renderToscaPlan : null };");

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
  check("it names the target folder in full", p.indexOf("A - Purple Tag") !== -1
        && p.indexOf("FUN_LASER_OK_Files") !== -1);
  check("it says how many files will be copied", /1 file\(s\) will be copied/.test(p));
  check("it says how many will be DELETED", /2 existing file\(s\) that will be DELETED/.test(p));
  // The names, not just the count — this is the assertion that makes the
  // confirmation checkable rather than merely reassuring.
  check("it names the files it will delete",
        p.indexOf("previous_one.OK") !== -1 && p.indexOf("previous_two.OK") !== -1);
  check("it names the files it will copy", p.indexOf("SH_0001.OK") !== -1);

  // ---- the combination that cannot run -----------------------------------
  check("it warns about the excluded combination", /will NOT run/.test(p));
  check("the warning names the file", p.indexOf("SH_0009.OK") !== -1);
  check("the warning quotes BOTH spellings",
        p.indexOf("T-Q-Line Small Gum Label") !== -1
        && p.indexOf("T - Q-Line Small Gum Label") !== -1);
  check("the warning points at the Key sheet", /Key sheet/.test(p));
  check("the exclusion is rendered as a warning, not plain text",
        byClass("tosca-plan").length > 0
        && descendants(byClass("tosca-plan")[0]).some(
             (e) => (e.className || "").includes("modal-warn")));

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
  check("the report says what was staged", /1 copied into 1 folder\(s\)/.test(t));
  check("the report says what was removed", /2 previous file\(s\) removed/.test(t));
  // The folder PATH, not its format segment — the written-rows list carries
  // "A - Purple Tag" too, so matching on that alone passed without any staging
  // report at all.
  check("the report names the staged folder path", t.indexOf("D:\\tree\\") !== -1);
  check("the report repeats the combination that was NOT run", /was NOT run/.test(t));

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

  process.exit(failures ? 1 : 0);
})();
