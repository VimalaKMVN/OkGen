// The 📄 TOSCA Reports button and its picker, driven against the stub DOM.
//
// The button is ALWAYS visible, unlike Bulk Actions and ▶ Run TOSCA, which hide
// whenever nothing is selected. That is the whole point: a TOSCA run takes time,
// so its reports are wanted later — often with no folder open and no selection
// at all. A button placed beside Run TOSCA would vanish exactly when it is
// needed, which is the mistake this asserts against.
//
// Clicking a choice opens the folder in Explorer, OUTSIDE the browser, so the
// page shows nothing on its own. The status line has to say what happened, or
// the click looks ignored — the same lesson as the native folder chooser.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

const calls = [];
global.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, body });
  let json = {};
  let ok = true;
  if (url.includes("/api/tosca/reports/open")) {
    if (body.script === "Missing Folder") {
      ok = false;
      json = { error: "the results folder for Missing Folder does not exist on this machine:\nD:\\nope" };
    } else {
      json = { opened: "D:\\ToscaAutomation\\...\\Thermal_TestResults", script: body.script };
    }
  } else if (url.includes("/api/tosca/reports")) {
    json = { folders: [
      { name: "OK Regression Thermal", exists: true,
        folder: "D:\\ToscaAutomation\\Regression_Testing_Automation\\REG_THERMAL_SH_PT_CL_DL_Comparison\\Thermal_TestResults" },
      { name: "Missing Folder", exists: false, folder: "D:\\nope" },
    ] };
  }
  return { ok, status: ok ? 200 : 422, json: async () => json };
};

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { openToscaReports, pickReportFolder };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            global.fetch, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.error("FAIL: app.js threw while loading:", e.message);
  process.exit(1);
}

let failures = 0;
function check(label, cond) {
  console.log((cond ? "ok   " : "FAIL ") + label);
  if (!cond) failures++;
}

// --------------------------------------------------------------------------
// The button exists and is NOT selection-gated
// --------------------------------------------------------------------------
const html = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "okgen", "web", "templates", "index.html"), "utf8");

check("the toolbar carries a TOSCA Reports button",
      /id="toscaReportsBtn"/.test(html));
check("...labelled so it reads as reports, not a run",
      /📄 TOSCA Reports/.test(html));
check("...and it does NOT ship hidden, unlike Bulk Actions / Run TOSCA",
      !/id="toscaReportsBtn"[^>]*class="[^"]*hidden/.test(html));
check("Bulk Actions and Run TOSCA DO ship hidden (the contrast this relies on)",
      /id="bulkBtn" class="btn hidden"/.test(html)
      && /id="toscaBtn" class="btn hidden"/.test(html));
check("nothing in app.js hides the Reports button on an empty selection",
      !/toscaReportsBtn[^\n]*hidden/.test(src));
check("it sits in the always-visible group, before the spacer",
      html.indexOf('id="toscaReportsBtn"') < html.indexOf('class="spacer"'));

// --------------------------------------------------------------------------
// The picker lists every configured folder, with its path
// --------------------------------------------------------------------------
const pending = api.openToscaReports();

setTimeout(async () => {
  const cards = descendants(doc.body).filter(
    (e) => e.classList && e.classList.contains("modal-card"));
  check("a picker modal opened", cards.length === 1);
  const card = cards[0];
  const all = descendants(card);

  const radios = all.filter((e) => e.type === "radio");
  check("one choice per configured folder", radios.length === 2);
  check("the first is preselected, so Open always has a target",
        radios[0] && radios[0].checked === true);

  const text = all.map((e) => e.textContent || "").join(" ");
  check("each choice names its script", /OK Regression Thermal/.test(text));
  check("...and shows the full folder path", /Thermal_TestResults/.test(text));
  check("a folder missing on THIS machine says so",
        all.some((e) => e.classList && e.classList.contains("report-missing")));
  check("...and a present one does not",
        all.filter((e) => e.classList && e.classList.contains("report-missing")).length === 1);
  check("the wording says Explorer opens it, so nobody expects it inline",
        /Explorer/i.test(text));

  // ---- choosing one opens it and SAYS so -----------------------------------
  const openBtn = all.filter((e) => e.tagName === "BUTTON"
                                    && /open in explorer/i.test(e.textContent))[0];
  check("an Open in Explorer button exists", !!openBtn);
  openBtn.click();
  await pending;
  check("the picker closes once a choice is made",
        !descendants(doc.body).some((e) => e.classList
                                           && e.classList.contains("modal-card")));

  await new Promise((r) => setTimeout(r, 0));
  const opened = calls.filter((c) => c.url.includes("/reports/open"));
  check("the chosen folder was requested", opened.length === 1);
  check("...by script name, never by a path from the client",
        opened[0] && opened[0].body.script === "OK Regression Thermal"
        && !("folder" in opened[0].body));

  const status = doc.querySelector("#status");
  check("the status line says what was opened — Explorer is outside the browser",
        status && /Opened/.test(status.textContent) && /Explorer/i.test(status.textContent));

  console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
  process.exit(failures ? 1 : 0);
}, 0);
