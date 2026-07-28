// Minimal DOM stub — just enough to LOAD app.js and render a panel in Node.
// No dependencies, so it works on the locked-down offline boxes too.
function mkClassList(el) {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => x && set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c, on) => { const has = set.has(c); const want = on === undefined ? !has : on;
                         if (want) set.add(c); else set.delete(c); return want; },
    contains: (c) => set.has(c),
    _set: set,
  };
}
function mkEl(tag = "div") {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [], childNodes: [], parentNode: null,
    dataset: {}, style: {}, _handlers: {},
    _text: "", value: "", disabled: false, type: "", checked: false,
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html || ""; },
    set innerHTML(v) { this._html = v; this.children = []; this.childNodes = []; },
    appendChild(c) { c.parentNode = this; this.children.push(c); this.childNodes.push(c); return c; },
    insertBefore(c, ref) {
      c.parentNode = this;
      const i = ref ? this.children.indexOf(ref) : -1;
      if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
      return c;
    },
    remove() { const p = this.parentNode; if (p) p.children = p.children.filter((x) => x !== this); },
    addEventListener(ev, fn) { (this._handlers[ev] ||= []).push(fn); },
    removeEventListener() {},
    click() { (this._handlers.click || []).forEach((f) => f({ preventDefault() {}, stopPropagation() {} })); },
    getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    // Returns the first real match, else a detached element. innerHTML is not
    // parsed by this stub, so code that writes markup then queries into it
    // would otherwise hit null — a harness limitation, not an app bug.
    querySelector(sel) { return this.querySelectorAll(sel)[0] || mkEl(); },
    querySelectorAll(sel) { return descendants(this).filter((e) => matches(e, sel)); },
    contains() { return false; },
    closest(sel) {
      let n = el;
      while (n) { if (matches(n, sel)) return n; n = n.parentNode; }
      return null;
    },
    focus() {}, blur() {}, setAttribute() {}, getAttribute() { return null; },
  };
  el.classList = mkClassList(el);
  // app.js assigns `el.className = "a b"`; keep classList in sync with it or
  // every class-based lookup silently misses.
  Object.defineProperty(el, "className", {
    get() { return [...el.classList._set].join(" "); },
    set(v) {
      el.classList._set.clear();
      String(v).split(/\s+/).filter(Boolean).forEach((c) => el.classList._set.add(c));
    },
  });
  return el;
}
function descendants(root, out = []) {
  for (const c of root.children) { out.push(c); descendants(c, out); }
  return out;
}
function matches(el, sel) {
  return String(sel).split(",").map((s) => s.trim()).some((s) => {
    const checked = s.endsWith(":checked");
    if (checked) { s = s.slice(0, -":checked".length); if (!el.checked) return false; }
    if (s.startsWith(".")) return el.classList.contains(s.slice(1));
    if (s.startsWith("#")) return el._id === s.slice(1);
    return el.tagName === s.toUpperCase();
  });
}
function install() {
  const registry = new Map();
  const doc = {
    createElement: (t) => mkEl(t),
    createTextNode: (t) => ({ textContent: t }),
    querySelector(sel) {
      if (sel.startsWith("#")) {
        const id = sel.slice(1);
        if (!registry.has(id)) { const e = mkEl(); e._id = id; registry.set(id, e); }
        return registry.get(id);
      }
      return mkEl();
    },
    querySelectorAll: () => [],
    addEventListener() {}, body: mkEl(), documentElement: mkEl(),
  };
  const errors = [];
  global.document = doc;
  global.window = {
    addEventListener(ev, fn) { if (ev === "error" || ev === "unhandledrejection") errors.push(fn); },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  // A REAL in-memory store, not a no-op: anything the app remembers across
  // calls (the per-folder SCAN/WMS answer, toggles) can't be tested if reads
  // always come back null.
  const store = new Map();
  global.localStorage = {
    getItem: (k) => (store.has(String(k)) ? store.get(String(k)) : null),
    setItem: (k, v) => store.set(String(k), String(v)),
    removeItem: (k) => store.delete(String(k)),
    clear: () => store.clear(),
  };
  global.Option = function (label, value) { const o = mkEl("option"); o.textContent = label; o.value = value; return o; };
  global.fetch = async () => ({ ok: true, json: async () => ({}) });
  global.setTimeout = global.setTimeout; global.clearTimeout = global.clearTimeout;
  global.confirm = () => true; global.prompt = () => null; global.alert = () => {};
  return { doc, registry, mkEl, descendants };
}
module.exports = { install, mkEl, descendants, matches };
