// Executes the real app.js SCAN/WMS logic against a stub DOM.
//
// The whole "ask once per folder and remember it" flow lives in the client, so
// without this it would rest on code review alone — which is exactly how a
// silent render abort shipped once before. Asserts: the prompt appears only for
// an unlabelled folder, answering stores the choice against that folder, and a
// stored answer is found from a SUBFOLDER (so a subfolder doesn't silently fall
// back to the default after its parent was answered).
const fs = require("fs");
const path = require("path");
const { install, descendants } = require("./dom-stub.js");

const { doc } = install();

const calls = [];
global.fetch = async (url) => {
  calls.push(url);
  return { ok: true, status: 200, json: async () => ({ children: [] }) };
};

const APP = path.join(__dirname, "..", "..", "src", "okgen", "web", "static", "app.js");
const src = fs.readFileSync(APP, "utf8");
const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { renderSourceAsk, sourceFor, rememberSource, srcParam, dirOf };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            global.fetch, global.confirm, global.prompt, global.alert);
} catch (e) {
  console.error("FAIL: app.js threw while loading:", e.message);
  process.exit(1);
}

const box = doc.querySelector("#srcAsk");
const checks = [];
const check = (name, ok) => checks.push([name, ok]);

// --- a folder whose source is already known must NOT ask -------------------
api.renderSourceAsk({
  path: "/d/Calgary_SCAN_2026",
  json_source: { source: "SCAN", reason: "folder name", resolved: true, hint: null },
});
check("a resolved folder asks nothing", box.classList.contains("hidden"));

// --- a folder with no source-dependent JSON must NOT ask -------------------
api.renderSourceAsk({ path: "/d/okfiles", json_source: null });
check("a folder with no JSON asks nothing", box.classList.contains("hidden"));

// --- an unlabelled folder MUST ask, and offer both sources ----------------
api.renderSourceAsk({
  path: "/d/unlabelled",
  json_source: {
    source: "WMS", reason: "default", resolved: false,
    hint: { field: "keytrol", message: "several files here share the same keytrol" },
  },
});
const shown = !box.classList.contains("hidden");
const all = descendants(box);
const buttons = all.filter((e) => e.tagName === "BUTTON");
const labels = buttons.map((b) => b.textContent).sort();

check("an unlabelled folder asks", shown);
check("both sources are offered", labels.join(",") === "SCAN,WMS");
check("it says which source is being assumed",
      all.some((e) => /being read as WMS/.test(e.textContent)));
check("the collision hint is surfaced",
      all.some((e) => /share the same keytrol/.test(e.textContent)));

// --- answering stores the choice AGAINST THAT FOLDER ----------------------
buttons.find((b) => b.textContent === "SCAN").click();
check("answering remembers the folder's source",
      api.sourceFor("/d/unlabelled") === "SCAN");
check("answering reloads the folder", calls.some((u) => u.includes("/api/tree")));
check("the reload carries the answer", calls.some((u) => /source=SCAN/.test(u)));

// --- a stored answer applies to files and subfolders beneath it -----------
check("a file under that folder resolves to the stored source",
      api.sourceFor(api.dirOf("/d/unlabelled/a.json")) === "SCAN");
check("a SUBfolder inherits the stored answer",
      api.sourceFor("/d/unlabelled/sub/deeper") === "SCAN");
check("an unrelated folder is unaffected",
      api.sourceFor("/d/somewhere_else") === null);
check("no source stored means no query param",
      api.srcParam("/d/somewhere_else") === "");

// --- an explicit answer must beat a name, so a wrongly-named folder is fixable
api.rememberSource("/d/Calgary_WMS_typo", "SCAN");
check("a stored answer overrides what the folder is named",
      api.sourceFor("/d/Calgary_WMS_typo") === "SCAN");

let bad = 0;
for (const [name, ok] of checks) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
  if (!ok) bad++;
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nsource-ask checks passed");
