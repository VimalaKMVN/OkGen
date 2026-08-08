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
    dataset: {}, style: {}, _handlers: {}, attrs: {},
    _text: "", value: "", disabled: false, type: "", checked: false,
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html || ""; },
    set innerHTML(v) { this._html = v; this.children = []; this.childNodes = []; },
    appendChild(c) {
      c.parentNode = this; this.children.push(c); this.childNodes.push(c);
      // A real <select> adopts its FIRST option's value automatically. Without
      // this the stub left `value` as "" after a panel populated a dropdown, so
      // any code that reads the current selection right after building it (the
      // bulk panel's Section/Operation chain) saw nothing selected and threw —
      // a failure that exists only in the stub, which is the worst kind.
      if (this.tagName === "SELECT" && c.tagName === "OPTION"
          && this.children.filter((x) => x.tagName === "OPTION").length === 1) {
        this.value = c.value;
      }
      return c;
    },
    // The options of a <select>, as a browser exposes them.
    get options() { return this.children.filter((c) => c.tagName === "OPTION"); },
    // Variadic sibling of appendChild. renderTable builds its row-action cell
    // with it, so a suite that renders a TABLE section (rather than only a
    // form) hits this — the reason it was missing is that none used to.
    append(...cs) { cs.forEach((c) => this.appendChild(c)); },
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
    // Fire a handler registered on THIS element. Note the stub does not bubble,
    // so a suite can only exercise a listener bound to the element itself —
    // which is the reason app.js binds `change` to the control rather than
    // relying on delegation from its row.
    dispatchEvent(e) {
      const type = e && e.type;
      (this._handlers[type] || []).forEach((f) =>
        f({ type, target: this, preventDefault() {}, stopPropagation() {} }));
      return true;
    },
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
    focus() {}, blur() {},
    // Attributes are RECORDED, not swallowed. They used to be no-ops, so a
    // suite could not tell `setAttribute("list", id)` from the call never
    // happening — and an assertion about it would pass vacuously either way.
    setAttribute(name, value) { this.attrs[String(name)] = String(value); },
    getAttribute(name) {
      const v = this.attrs[String(name)];
      return v === undefined ? null : v;
    },
    hasAttribute(name) { return String(name) in this.attrs; },
    removeAttribute(name) { delete this.attrs[String(name)]; },
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
  // A text node (createTextNode) has no children — walking into one used to
  // throw, which an empty section reaches as soon as it renders placeholder text.
  for (const c of root.children || []) { out.push(c); descendants(c, out); }
  return out;
}
// Attribute value, with data-* resolved through `dataset` exactly as a browser
// does — `[data-section="0"]` must see `el.dataset.section = 0`.
function attrValue(el, name) {
  if (name.startsWith("data-")) {
    const key = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const v = el.dataset ? el.dataset[key] : undefined;
    return v === undefined ? null : String(v);
  }
  const v = el.getAttribute ? el.getAttribute(name) : null;
  return v === null || v === undefined ? null : String(v);
}

// Supports `tag`, `.class`, `#id` and ATTRIBUTE selectors, in combination:
// `.fval[data-section="0"][data-field="qty"]`. Attributes used to be
// unsupported, so any such selector matched NOTHING — a live-value lookup came
// back empty and a suite asserting on it would pass for the wrong reason. That
// is the same class as the setAttribute no-op this stub already fixed once.
function matches(el, sel) {
  return String(sel).split(",").map((s) => s.trim()).some((s) => {
    const checked = s.endsWith(":checked");
    if (checked) { s = s.slice(0, -":checked".length); if (!el.checked) return false; }
    // Text nodes live in the tree too and carry none of this — never a match.
    if (!el || !el.classList) return false;
    const m = /^([a-zA-Z][\w-]*)?((?:[.#][\w-]+|\[[^\]]*\])*)$/.exec(s);
    if (!m) return false;
    if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
    const parts = (m[2] || "").match(/[.#][\w-]+|\[[^\]]*\]/g) || [];
    if (!m[1] && !parts.length) return false;
    for (const p of parts) {
      if (p[0] === ".") {
        if (!el.classList.contains(p.slice(1))) return false;
      } else if (p[0] === "#") {
        if (el._id !== p.slice(1)) return false;
      } else {
        const inner = p.slice(1, -1);
        const eq = inner.indexOf("=");
        if (eq < 0) {
          if (attrValue(el, inner.trim()) === null) return false;
        } else {
          const name = inner.slice(0, eq).trim();
          const want = inner.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
          if (attrValue(el, name) !== want) return false;
        }
      }
    }
    return true;
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
    // Both halves are needed: a modal registers a document-level key handler
    // and REMOVES it when it closes, so a stub without the remover throws the
    // moment a dialog is driven to completion.
    addEventListener() {}, removeEventListener() {},
    body: mkEl(), documentElement: mkEl(),
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
  // app.js escapes field names before building selectors. Without this the
  // first such lookup throws ReferenceError and takes the whole suite with it.
  global.CSS = { escape: (s) => String(s).replace(/([^\w-])/g, "\\$1") };
  return { doc, registry, mkEl, descendants };
}
module.exports = { install, mkEl, descendants, matches };
