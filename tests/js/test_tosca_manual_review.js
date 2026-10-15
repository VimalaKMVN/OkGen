// Manual Orders WorkPending — the confirmation plan, the REVIEW window, and the
// "started" window, driven against the real app.js.
//
// The review window is the only place a prepared Manual Orders run can be
// started from, so what is pinned here is that it cannot be lost or bypassed
// by accident:
//   * "Open workbook" does NOT close it (the review happens in Excel, beside it);
//   * a click outside does NOT close it;
//   * a refused launch (unsaved edits in Excel) keeps it open, says why, and
//     leaves Run TOSCA clickable again;
//   * Run TOSCA posts the script AND the run's log, and only on success swaps
//     to the corner "started" window, whose report carries the launch line;
//   * "Close without running" closes it and says how to finish later.
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

const calls = [];
let launchReply = { ok: false, body: { error: "FUN.xlsm is open in Excel with UNSAVED changes." } };
function fakeFetch(url, opts) {
  calls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  let reply = { ok: true, body: {} };
  if (url === "/api/tosca/launch") reply = launchReply;
  if (url === "/api/tosca/open-workbook") reply = { ok: true, body: { opened: "x.xlsm" } };
  return Promise.resolve({ ok: reply.ok, statusText: "err",
                           json: () => Promise.resolve(reply.body) });
}
const tick = () => new Promise((r) => setTimeout(r, 0));

const RES = {
  kind: "manual_orders", script: "Manual Orders WorkPending",
  written: 2, lines: 14,
  records: [
    { n: 1, tab: "ManualOrder_Detail_1", file: "SH.OK", chain: "TJX Europe",
      process: "Style Header", format: "Q - Small Merch UPP Ticket", key: "126539Q",
      key_field: "keytrol", lines: 10, warnings: ["CBC 441 is not in config/cbc_zones.yaml"] },
    { n: 2, tab: "ManualOrder_Detail_2", file: "PT.json", chain: "Marshalls",
      process: "Pre-Ticket", format: "B - Regular Gum Label", key: "40644701",
      key_field: "purchaseOrderNumber", lines: 4, warnings: [] },
  ],
  staging: { enabled: true, configured: true, copied: 2, removed: 3, excluded: [] },
  skipped: [{ file: "CL.OK", reason: "Carton Label" }], errors: [],
  report: "OkGen — Run TOSCA Script\nLAUNCH\n  not started yet — waiting for the review\n",
  log: "C:\\OkGen\\logs\\okgen_tosca_20261013_101500.log",
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return {"
      + " showToscaReview: typeof showToscaReview === 'function' ? showToscaReview : null,"
      + " renderToscaPlan: typeof renderToscaPlan === 'function' ? renderToscaPlan : null };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            fakeFetch, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}
check("app.js exports showToscaReview", typeof api.showToscaReview === "function");
check("app.js exports renderToscaPlan", typeof api.renderToscaPlan === "function");
if (!api.showToscaReview || !api.renderToscaPlan) process.exit(1);

const overlays = () => descendants(doc.body).filter(
  (e) => (e.className || "").includes("modal-overlay"));
const textOf = (nodes) => nodes.map((e) => e.textContent || "").join(" | ");
const button = (label) => descendants(doc.body).find((e) => e.tagName === "BUTTON"
                                                       && (e.textContent || "") === label);

(async () => {
  // ---- the confirmation dialog's plan, for this kind --------------------
  const box = doc.createElement("div");
  api.renderToscaPlan(box, {
    kind: "manual_orders", records: RES.records, copy_total: 2, remove_total: 3,
    targets: [{ status: "ok" }], skipped: RES.skipped, errors: [],
  });
  const ptext = textOf(descendants(box));
  check("the plan counts records AND tabs", /2 header record\(s\) and 2 detail tab\(s\)/.test(ptext));
  check("the plan states the delete", /3 existing file\(s\) that will be DELETED/.test(ptext));
  check("the plan lists each record's chain", /TJX Europe/.test(ptext) && /Marshalls/.test(ptext));
  check("the plan says TOSCA is not started yet", /NOT started yet/.test(ptext));
  check("the plan flags the skipped file", /1 file\(s\) not applicable/.test(ptext));
  check("the plan does not fall through to the per-folder plan",
        !/input folder\(s\)/.test(ptext));

  // ---- Print Preview: the extra OrderProcessing tab is stated -------------
  const box2 = doc.createElement("div");
  api.renderToscaPlan(box2, {
    kind: "manual_orders", records: RES.records, copy_total: 2, remove_total: 0,
    targets: [{ status: "ok" }], skipped: [], errors: [],
    order_sheet: "OrderProcessing", order_rows: [{}, {}],
  });
  check("the PP plan states the OrderProcessing rows",
        /plus 2 OrderProcessing row\(s\)/.test(textOf(descendants(box2))));
  check("WorkPending's plan does not mention OrderProcessing", !/OrderProcessing/.test(ptext));

  // ---- the review window -------------------------------------------------
  api.showToscaReview(RES);
  let ovs = overlays();
  check("one review window", ovs.length === 1);
  check("it is centred, not in the corner", !(ovs[0].className || "").includes("modal-corner"));
  let text = textOf(descendants(doc.body));
  check("it is titled as an update, not a run", /Input test data updated/.test(text));
  check("it states records and tabs", /2 header record\(s\), 2 detail tab\(s\)/.test(text));
  check("it tells the user they may run directly",
        /Review the workbook in Excel first/.test(text) && /click Run TOSCA to start/.test(text));
  check("it flags the CBC note", /1 note\(s\) on the data/.test(text));
  for (const b of ["View report", "Open workbook", "Close without running", "Run TOSCA"]) {
    check(`it has "${b}"`, !!button(b));
  }
  check("there is no plain Cancel to confuse with not-running", !button("Cancel"));
  check("WorkPending's review does not mention OrderProcessing", !/OrderProcessing/.test(text));
  check("the key column shows the record key", /126539Q/.test(text));

  ovs[0].dispatchEvent({ type: "click" });
  check("a click outside does NOT close it", overlays().length === 1);

  button("Open workbook").click();
  await tick(); await tick();
  check("Open workbook asks the server to open THIS script's workbook",
        calls.some((c) => c.url === "/api/tosca/open-workbook"
                       && c.body.script === "Manual Orders WorkPending"));
  check("Open workbook leaves the review window open", overlays().length === 1);

  // A refused launch: the window stays, says why, and can be retried.
  button("Run TOSCA").click();
  await tick(); await tick();
  const launch = calls.find((c) => c.url === "/api/tosca/launch");
  check("Run TOSCA posts the launch", !!launch);
  check("…for this script", launch && launch.body.script === "Manual Orders WorkPending");
  check("…with the run's own log", launch && launch.body.log === RES.log);
  check("a refused launch keeps the window", overlays().length === 1);
  text = textOf(descendants(doc.body));
  check("…and shows the reason", /UNSAVED changes/.test(text));
  check("…and Run TOSCA can be clicked again", button("Run TOSCA") && !button("Run TOSCA").disabled);

  // Now it succeeds.
  launchReply = { ok: true, body: { launched: true, bat: "D:\\x\\MO_WP_Automation_RunScript.bat",
                                    appended: "\nLAUNCH (after review)\n  started : MO_WP\n" } };
  button("Run TOSCA").click();
  await tick(); await tick();
  ovs = overlays();
  check("a successful launch replaces the review with one window", ovs.length === 1);
  check("the started window sits in the corner", (ovs[0].className || "").includes("modal-corner"));
  text = textOf(descendants(doc.body));
  check("it says TOSCA started", /TOSCA started/.test(text));
  button("View report").click();
  text = textOf(descendants(doc.body));
  check("its report carries the launch line", /LAUNCH \(after review\)/.test(text));
  check("…on top of the run's report", /waiting for the review/.test(text));
  descendants(doc.body).filter((e) => (e.textContent || "") === "Close").forEach((b) => b.click());

  // ---- Close without running ---------------------------------------------
  descendants(doc.body).forEach((e) => { if ((e.className || "").includes("modal-overlay")) e.remove(); });
  api.showToscaReview(Object.assign({}, RES, { script: "Manual Orders Print Preview",
    order_sheet: "OrderProcessing", order_rows: [{}, {}, {}] }));
  check("the PP review states the OrderProcessing rows",
        /3 OrderProcessing row\(s\)/.test(textOf(descendants(doc.body))));
  descendants(doc.body).forEach((e) => { if ((e.className || "").includes("modal-overlay")) e.remove(); });
  api.showToscaReview(RES);
  button("Close without running").click();
  check("Close without running closes the window", overlays().length === 0);
  const status = doc.querySelector("#status").textContent || "";
  check("…and says the run was not started", /not started/.test(status));
  check("…and how to finish it later", /\.bat yourself/.test(status));

  process.exit(failures ? 1 : 0);
})().catch((e) => { console.log("FAIL threw: " + (e && e.stack || e)); process.exit(1); });
