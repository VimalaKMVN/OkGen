// Executes the real app.js Generate panel against a stub DOM, so a runtime
// error (like the temporal-dead-zone throw that silently aborted the render)
// fails here instead of in the browser.
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

// Record every request the panel makes, and answer with realistic payloads so
// the debounced preview and the write path both run for real.
const calls = [];
global.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, body });
  let json = {};
  if (url.includes("/api/generate/preview")) {
    json = {
      count: body.spec.count, folder: "/tmp/generated_StyleHeader_100", truncated: true,
      sample: [{ name: "StyleHeader_550001_0001.OK", key: "550001",
                 values: { dept: "42" }, rows: { Lane: 10, Size: 3 } }],
    };
  } else if (url.includes("/api/generate/apply")) {
    json = { folder: "/tmp/generated_StyleHeader_100", written: body.spec.count,
             files: [], errors: [], template: "StyleHeader.OK" };
  }
  return { ok: true, status: 200, json: async () => json };
};
const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");

// app.js is a plain script: evaluate it, then reach its top-level functions.
const sandbox = { module: undefined, exports: undefined };
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderGeneratePanel, OKGEN_BUILD };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            global.fetch, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.error("FAIL: app.js threw while loading:", e.message);
  process.exit(1);
}

const scope = {
  path: "/tmp/StyleHeader.OK", name: "StyleHeader.OK", layout: "StyleHeader",
  key_field: "keytrol", key_size: 6, max_count: 5000,
  header_fields: [{ name: "dept", size: 2 }, { name: "date", size: 8 },
                  { name: "timestamp", size: null, date: true }],
  sections: [
    { name: "Lane", rows: 10, max_records: 10, fields: [{ name: "lane1", size: 4 }] },
    { name: "Size", rows: 4, max_records: null, fields: [{ name: "qty", size: 5 }] },
    { name: "NoNumeric", rows: 0, max_records: null, fields: [] },
  ],
  palette: { derived: ["brand", "layout"], header_fields: ["chain", "dept"], custom: {} },
  template_count: 1,
  default_folder: "/tmp/generated_StyleHeader_0",
};

const panel = doc.querySelector("#generatePanel");
try {
  api.renderGeneratePanel(panel, [scope.path], scope);
} catch (e) {
  console.error("FAIL: renderGeneratePanel threw:", e.message);
  process.exit(1);
}

const all = descendants(panel);
const buttons = all.filter((e) => e.tagName === "BUTTON");
const labels = buttons.map((b) => b.textContent);
const gen = buttons.filter((b) => /generate/i.test(b.textContent));

const checks = [
  ["panel rendered something", all.length > 10],
  ["a Generate button exists", gen.length >= 1],
  ["Generate has a click handler", gen.some((b) => (b._handlers.click || []).length > 0)],
  ["sticky action bar rendered", all.some((e) => e.classList.contains("gen-actions"))],
  ["results area rendered", all.some((e) => e.classList.contains("gen-results"))],
  ["results sit ABOVE the action bar",
    panel.children.findIndex((e) => e.classList.contains("gen-results")) <
    panel.children.findIndex((e) => e.classList.contains("gen-actions"))],
  ["build marker present", all.some((e) => /build v/.test(e.textContent))],
  ["a section with no numeric fields still gets .gen-detail",
    all.filter((e) => e.classList.contains("gen-detail")).length === 3],
];

let bad = 0;
for (const [name, ok] of checks) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
  if (!ok) bad++;
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }

// First click must ARM (not write), second click must attempt the write.
const btn = gen[0];
btn.click();
const armedLabel = /click again/i.test(btn.textContent);
console.log(`  ${armedLabel ? "PASS" : "FAIL"}  first click arms the confirmation ("${btn.textContent}")`);
if (!armedLabel) process.exit(1);

// Tick a header field and give it a VALUE LIST — the spec must carry it, and
// the min/max range must go disabled so only listed values can be produced.
const fieldRows = all.filter((e) => e.classList.contains("gen-field"));
const firstRow = fieldRows[0];
const cb = firstRow.children.find((e) => e.classList.contains("gen-on"));
const listInput = firstRow.children.find((e) => e.classList.contains("gen-list"));
const minInput = firstRow.children.find((e) => e.classList.contains("gen-min"));
cb.checked = true;
(cb._handlers.change || []).forEach((f) => f({}));
listInput.value = "11, 22, 33";
(listInput._handlers.input || []).forEach((f) => f({}));
const listOk = minInput.disabled === true;
console.log(`  ${listOk ? "PASS" : "FAIL"}  a value list disables the min/max range`);
if (!listOk) process.exit(1);

// A DATE field must offer a date range (from/to text inputs), not a numeric
// one, and must send `from`/`to` rather than `min`/`max` — the server reads a
// different pair for temporal fields.
const dateRow = fieldRows.find((r) =>
  r.children.some((e) => /timestamp/.test(e.textContent || "")));
const dcb = dateRow.children.find((e) => e.classList.contains("gen-on"));
const dmin = dateRow.children.find((e) => e.classList.contains("gen-min"));
const dmax = dateRow.children.find((e) => e.classList.contains("gen-max"));
const dateChecks = [
  ["a date field renders text inputs, not number", dmin.type === "text"],
  ["the date row is labelled as a date",
   dateRow.children.some((e) => /timestamp \(date\)/.test(e.textContent || ""))],
];
dcb.checked = true;
(dcb._handlers.change || []).forEach((f) => f({}));
dateChecks.push(["ticking a date field does not prefill a numeric range",
                 dmin.value === ""]);
dmin.value = "2024-01-01"; dmax.value = "2024-12-31";
(dmin._handlers.input || []).forEach((f) => f({}));
for (const [name, ok] of dateChecks) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
  if (!ok) bad++;
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }

// Second click must actually POST the write request.
btn.click();

setTimeout(() => {
  const applied = calls.filter((c) => c.url.includes("/api/generate/apply"));
  const usesPaths = applied.length && Array.isArray(applied[0].body.paths);
  const previewed = calls.filter((c) => c.url.includes("/api/generate/preview"));
  const ok2 = applied.length === 1;
  const ok3 = previewed.length >= 1;
  console.log(`  ${ok3 ? "PASS" : "FAIL"}  preview request issued (${previewed.length})`);
  console.log(`  ${ok2 ? "PASS" : "FAIL"}  second click POSTs /api/generate/apply (${applied.length})`);
  console.log(`  ${usesPaths ? "PASS" : "FAIL"}  apply request carries a paths[] array`);
  if (!ok2 || !ok3 || !usesPaths) process.exit(1);
  const spec = applied[0].body.spec;
  const ok4 = spec.count === 100 && Array.isArray(spec.name_parts) && spec.name_parts.length === 3;
  console.log(`  ${ok4 ? "PASS" : "FAIL"}  spec carries count + name parts (${JSON.stringify(spec.name_parts)})`);
  if (!ok4) process.exit(1);
  const hf = (spec.header_fields || [])[0];
  const ok5 = hf && hf.values === "11, 22, 33";
  console.log(`  ${ok5 ? "PASS" : "FAIL"}  spec carries the value list (${hf && JSON.stringify(hf.values)})`);
  if (!ok5) process.exit(1);

  // The apply must COMPLETE without a post-request error. A stray variable in
  // the success path — e.g. folderOf(path) after the paths[] rename — is caught
  // by runGenerate and reported via setStatus as "Generate failed", so the
  // status bar shows the failure instead of "Generated N". (The panel's own
  // text is racy: a late debounced preview can overwrite it, so check #status.)
  const status = (doc.querySelector("#status").textContent || "");
  const ok6 = /Generated \d+ file/.test(status) && !/failed/i.test(status);
  console.log(`  ${ok6 ? "PASS" : "FAIL"}  apply completes without a post-request error (status: "${status}")`);
  if (!ok6) process.exit(1);

  console.log(`\nAll checks passed (build ${api.OKGEN_BUILD})`);
}, 400);
