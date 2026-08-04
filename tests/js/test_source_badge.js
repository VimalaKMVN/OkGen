// Executes the real app.js SCAN/WMS rendering against a stub DOM.
//
// This replaces test_source_ask.js. The "ask once per folder and remember it"
// flow is GONE: a Calgary JSON file states its own source (a populated
// headerASNid means WMS, an empty one means SCAN), so the client no longer
// prompts, stores answers, or sends a source with any request. What it does
// instead is show a per-file badge — which is what this asserts, along with the
// absence of the machinery that used to contradict the file in front of you.
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

// The tree the server now returns: a source on every Calgary JSON file node.
const TREE = {
  type: "folder", name: "Batch07", path: "/d/Batch07", children: [
    { type: "file", name: "wms.json", path: "/d/Batch07/wms.json", json: true,
      layout: "CalgaryStyleHeader", chain: "04", chain_info: null,
      source: "WMS", key_field: "headerASNid", key_value: "S403A", duplicate: false },
    { type: "file", name: "scan.json", path: "/d/Batch07/scan.json", json: true,
      layout: "CalgaryStyleHeader", chain: "04", chain_info: null,
      source: "SCAN", key_field: "keytrol", key_value: "140038", duplicate: false },
    { type: "file", name: "carton.json", path: "/d/Batch07/carton.json", json: true,
      layout: "CalgaryCartonLabel", chain: "04", chain_info: null,
      source: "SCAN", key_field: "pickListId", key_value: "10000", duplicate: false },
    // .OK files carry a source too, but it comes from the LAYOUT (EU/EWMS = WMS,
    // the NA layouts = SCAN) rather than from reading the file, so the tooltip
    // has to explain itself differently.
    { type: "file", name: "Plain.OK", path: "/d/Batch07/Plain.OK", json: false,
      layout: "StyleHeader", chain: "03", chain_info: null,
      source: "SCAN", source_reason: "the layout's own source",
      key_field: "keytrol", key_value: "550000", duplicate: false },
    { type: "file", name: "EUStyle.OK", path: "/d/Batch07/EUStyle.OK", json: false,
      layout: "EUStyleHeader", chain: "05", chain_info: null,
      source: "WMS", source_reason: "the layout's own source",
      key_field: "keytrol", key_value: "126539Q", duplicate: false },
  ],
};

const run = new Function(
  "document", "window", "localStorage", "Option", "fetch", "confirm", "prompt", "alert",
  src + "\n;return { state, renderTree };");

let api;
try {
  api = run(doc, global.window, global.localStorage, global.Option,
            () => Promise.resolve({}), global.confirm, global.prompt, global.alert);
} catch (e) {
  console.log("FAIL app.js threw while loading: " + (e && e.stack || e));
  process.exit(1);
}

api.renderTree(TREE);
// The stub registers elements by id on demand, so the tree lives under #tree
// rather than under body.
const nodes = descendants(doc.querySelector("#tree"));
const badges = nodes.filter((e) => (e.className || "").includes("src-badge"));
const text = nodes.map((e) => e.textContent).join(" | ");

check("a badge is drawn for every file that has a source", badges.length === 5);
check("the WMS file is badged WMS", badges.some((b) => b.textContent === "WMS"));
check("the SCAN file is badged SCAN", badges.some((b) => b.textContent === "SCAN"));
check("carton labels are badged too",
      badges.filter((b) => b.textContent === "SCAN").length === 3);
check("the badge is styled per source",
      badges.some((b) => (b.className || "").includes("src-badge-wms"))
      && badges.some((b) => (b.className || "").includes("src-badge-scan")));
check("the tooltip names the key field the source selects",
      badges.some((b) => /headerASNid/.test(b.title || ""))
      && badges.some((b) => /keytrol/.test(b.title || "")));
// .OK files are badged from their layout: EU/EWMS = WMS, the NA layouts = SCAN.
const okBadges = nodes.filter(
  (e) => (e.className || "").includes("src-badge") && /comes from/.test(e.title || ""));
// Assert the SET first: an `every()` over an empty list passes vacuously, which
// would hide exactly the failure these checks exist to catch.
check("both .OK files are badged", okBadges.length === 2);
check("an NA .OK file is badged SCAN",
      okBadges.some((b) => b.textContent === "SCAN"));
check("an EU .OK file is badged WMS",
      okBadges.some((b) => b.textContent === "WMS"));
check("the .OK tooltip does NOT claim anything about headerASNid",
      okBadges.every((b) => !/headerASNid/.test(b.title || "")));
check("the .OK tooltip says the key is source-independent",
      okBadges.every((b) => /not affected by the source/.test(b.title || "")));
check("the JSON tooltip still explains itself from the payload",
      badges.filter((b) => /headerASNid/.test(b.title || "")).length === 3);

// The machinery that used to contradict the file is gone.
check("no SCAN/WMS prompt is rendered", !/SCAN or WMS\?/.test(text));
check("nothing is remembered against a folder",
      Object.keys(global.localStorage.store || {}).every((k) => !k.startsWith("okgen.source:")));
check("app.js no longer reads a stored source", !/sourceFor\s*\(/.test(src));
check("app.js no longer writes one", !/rememberSource/.test(src));

process.exit(failures ? 1 : 0);
