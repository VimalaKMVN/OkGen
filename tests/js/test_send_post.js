// Executes the real app.js JSON Send-to-NiceLabel flow against a stub DOM.
//
// The JSON hand-off POSTs each file to an endpoint as a BACKGROUND job the
// client has to poll — scope -> confirm -> start -> poll -> report. All of that
// is client-side, so without this it would rest on code review alone, which is
// exactly how a silent render abort shipped once before. Asserts: the mode is
// resolved BEFORE the dialog is drawn, the dialog names the real destination
// and stays gated on the checkbox, the job is polled until it finishes, and the
// per-file failures come out somewhere the user can read them.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

// Scripted endpoint: records every call, and reports the job as running once
// before it completes, so the polling loop has to go round at least twice.
const calls = [];
let statusPolls = 0;
const RESULT = {
  mode: "post",
  sent: ["A.json", "B.json"],
  errors: [{ path: "/d/C.json", error: "HTTP 500" }],
  summary: {
    total: 3, posted: 2, failed: 1, skipped: 0, not_attempted: 0,
    failures_by_cause: { server: 1 }, aborted: "",
    log: "/stage/okgen_send_20260728_101500.log",
    failed_dir: "/stage/failed",
  },
  results: [
    { name: "A.json", outcome: "posted", message: "HTTP 200" },
    { name: "B.json", outcome: "posted", message: "HTTP 200" },
    { name: "C.json", outcome: "failed", message: "HTTP 500 Internal Server Error" },
  ],
};

global.fetch = async (url, opts) => {
  calls.push(url);
  let body = {};
  if (url === "/api/send/scope") {
    body = { mode: "post", count: 3, configured: true,
             destination: "https://labels.example/api",
             folder: "/stage", username: "labeluser",
             warning: "Going to the LIVE endpoint." };
  } else if (url === "/api/send/start") {
    body = { job: "job123", mode: "post", total: 3 };
  } else if (url.startsWith("/api/send/status/")) {
    statusPolls += 1;
    body = statusPolls === 1
      ? { state: "running", done: 1, total: 3, posted: 1, failed: 0 }
      : { state: "done", done: 3, total: 3, posted: 2, failed: 1, result: RESULT };
  }
  return { ok: true, status: 200, json: async () => body };
};

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { state, sendToNiceLabel, confirmSend, buildSendReport," +
        "\n           sendMenuLabel, showCopyAnimation, updateSendProgress };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            (...a) => global.fetch(...a), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.error("FAIL: app.js threw while loading:", e.message);
  process.exit(1);
}

const checks = [];
const check = (name, ok) => checks.push([name, ok]);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// --- the menu says which hand-off you are about to get ---------------------
api.state.selection = new Set(["/d/A.json", "/d/B.json"]);
check("an all-JSON selection offers POST", /POST 2 to NiceLabel/.test(api.sendMenuLabel(2)));
api.state.selection = new Set(["/d/A.OK"]);
check("an .OK selection still says Send", /Send to NiceLabel/.test(api.sendMenuLabel(1)));
api.state.selection = new Set(["/d/A.OK", "/d/B.json"]);
check("a mixed selection keeps the generic wording",
      /Send 2 to NiceLabel/.test(api.sendMenuLabel(2)));

// --- the confirm dialog names the REAL destination -------------------------
const scope = { mode: "post", count: 3, configured: true,
                destination: "https://labels.example/api", folder: "/stage",
                username: "labeluser", warning: "Going to the LIVE endpoint." };
const pending = api.confirmSend(scope);
const modal = descendants(doc.body).filter((e) => e.classList.contains("modal-card"))[0];
const modalText = modal ? descendants(modal).map((e) => e.textContent).join(" | ") : "";
check("the dialog is drawn", !!modal);
check("it says POST, not copy", /POST 3 JSON file\(s\)/.test(modalText));
check("it shows the endpoint", /labels\.example/.test(modalText));
check("it shows the staging folder and user", /\/stage/.test(modalText) && /labeluser/.test(modalText));
check("it shows the warning", /LIVE endpoint/.test(modalText));

const buttons = descendants(modal).filter((e) => e.tagName === "BUTTON");
const sendBtn = buttons.find((b) => b.textContent === "Send");
const box = descendants(modal).filter((e) => e.type === "checkbox")[0];
check("Send is disabled until the box is ticked", sendBtn && sendBtn.disabled === true);
box.checked = true;
(box._handlers.change || []).forEach((f) => f({}));
check("ticking the box enables Send", sendBtn.disabled === false);
sendBtn.click();

// --- the failure report is readable ----------------------------------------
const report = api.buildSendReport(RESULT);
const reportText = descendants(report).map((e) => e.textContent).join(" | ");
check("the failed file is named with its reason",
      /C\.json — HTTP 500 Internal Server Error/.test(reportText));
check("failures are grouped by cause", /1 server/.test(reportText));
check("the log path is offered", /okgen_send_20260728/.test(reportText));
check("it says where the failed copies went", /\/stage\/failed/.test(reportText));

const aborted = api.buildSendReport({
  ...RESULT,
  summary: { ...RESULT.summary, aborted: "Stopped after an authentication failure" },
});
check("an aborted run says so",
      /authentication failure/.test(descendants(aborted).map((e) => e.textContent).join(" ")));

// --- end to end: scope -> start -> poll until done --------------------------
(async () => {
  await pending;                       // the dialog above resolved on the click
  api.state.selection = new Set(["/d/A.json", "/d/B.json", "/d/C.json"]);
  api.state.busy = false;
  calls.length = 0;

  const flow = api.sendToNiceLabel();
  // Tick the second dialog through as soon as it appears.
  for (let i = 0; i < 100; i++) {
    const m = descendants(doc.body).filter((e) => e.classList.contains("modal-card")).pop();
    const btn = m && descendants(m).filter((e) => e.tagName === "BUTTON")
                                  .find((b) => b.textContent === "Send");
    if (btn) {
      const cb = descendants(m).filter((e) => e.type === "checkbox")[0];
      cb.checked = true;
      (cb._handlers.change || []).forEach((f) => f({}));
      btn.click();
      break;
    }
    await wait(10);
  }
  await flow;

  check("it asks for the mode before doing anything", calls[0] === "/api/send/scope");
  check("it starts the background job", calls.includes("/api/send/start"));
  check("it polls the job until it finishes", statusPolls >= 2);
  check("it never used the .OK copy route", !calls.includes("/api/send"));
  check("the final status counts posted and failed",
        /Posted 2 of 3/.test(doc.querySelector("#status").textContent) &&
        /1 failed/.test(doc.querySelector("#status").textContent));

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log((ok ? "ok   " : "FAIL ") + name);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
})();
