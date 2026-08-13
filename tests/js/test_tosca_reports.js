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

  // ------------------------------------------------------------------------
  // A folder that is not on this machine is shown, and UNSELECTABLE
  // ------------------------------------------------------------------------
  // The red line alone left the choice takeable, and taking it could only ever
  // fail — so the entry stays visible (that a script's folder is not set up
  // here is worth knowing) while the control is disabled.
  const pick = api.pickReportFolder;
  const css = fs.readFileSync(
    path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "styles.css"), "utf8");

  function render(folders) {
    doc.body.innerHTML = "";
    if (typeof pick === "function") pick(folders);
    const all = descendants(doc.body);
    return {
      all,
      radios: all.filter((e) => e.type === "radio"),
      rows: all.filter((e) => (e.className || "").includes("tosca-choice")),
      text: all.map((e) => e.textContent || "").join(" | "),
      openBtn: all.filter((e) => e.tagName === "BUTTON"
                                 && /open in explorer/i.test(e.textContent || ""))[0],
    };
  }

  check("app.js exposes the report picker", typeof pick === "function");

  const mixed = render([
    { name: "Present", exists: true, folder: "D:\\here" },
    { name: "Absent", exists: false, folder: "D:\\nope" },
  ]);
  check("a present folder stays selectable",
        !!mixed.radios[0] && mixed.radios[0].disabled !== true);
  check("a missing folder cannot be selected",
        !!mixed.radios[1] && mixed.radios[1].disabled === true);
  check("...and its row is marked as greyed out",
        !!mixed.rows[1] && (mixed.rows[1].className || "").includes("tosca-choice-off"));
  check("...while the present row is not",
        !!mixed.rows[0] && !(mixed.rows[0].className || "").includes("tosca-choice-off"));
  check("the missing entry is still SHOWN, not hidden", /Absent/.test(mixed.text));
  check("...and still says it is not found here", /not found on this machine/.test(mixed.text));
  check("...and says what would make it usable", /until that folder is created/.test(mixed.text));

  // The default must land on a row that can actually be opened. A checked but
  // disabled radio would leave Open enabled with nothing behind it.
  const firstMissing = render([
    { name: "Absent", exists: false, folder: "D:\\nope" },
    { name: "Present", exists: true, folder: "D:\\here" },
  ]);
  check("the preselection skips a disabled row",
        !!firstMissing.radios[0] && firstMissing.radios[0].checked !== true);
  check("...and lands on the first selectable one",
        !!firstMissing.radios[1] && firstMissing.radios[1].checked === true);

  // Nothing selectable at all: Open would silently do nothing, which reads as a
  // broken button rather than as an unconfigured machine.
  const none = render([
    { name: "A", exists: false, folder: "D:\\nope" },
    { name: "B", exists: false, folder: "D:\\nada" },
  ]);
  check("with no folder present, Open is disabled",
        !!none.openBtn && none.openBtn.disabled === true);
  check("...and the dialog says why rather than looking broken",
        /None of these folders exists on this machine/.test(none.text));
  check("...while both entries are still listed", /A/.test(none.text) && /B/.test(none.text));

  // ---- the stylesheet, because the greying is CSS-only -------------------
  check("styles.css greys the disabled row's NAME",
        /\.tosca-choice-off\s+\.report-name\s*\{[^}]*color:/.test(css));
  check("...and shows a not-allowed cursor on it",
        /\.tosca-choice-off\s*\{[^}]*cursor:\s*not-allowed/.test(css));
  // Load-bearing: an `opacity` on the row would fade the red "not found" line
  // too — the explanation for the disabled state is the one thing that must
  // NOT get harder to read.
  // The rule must EXIST and lack `opacity` — testing only for its absence
  // passes on any build that has no such rule at all, which is vacuous.
  const offRule = css.match(/\.tosca-choice-off\s*\{([^}]*)\}/);
  check("the row is NOT dimmed wholesale, which would fade its own warning",
        !!offRule && !/opacity/.test(offRule[1]));

  console.log(failures ? `\n${failures} check(s) failed` : "\nall checks passed");
  process.exit(failures ? 1 : 0);
}, 0);
