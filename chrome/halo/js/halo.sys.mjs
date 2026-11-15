// ==UserScript==
// @name           Halo DevTools
// @description    JS mods for the Halo Firefox DevTools theme. Every feature is a pref under halo.devtools.* in about:config.
// @version        1.0.0
// @onlyonce
// ==/UserScript==

/* Halo DevTools — privileged module.
 *
 * Loaded once per browser session (parent process) by an autoconfig loader
 * (see loader/ or fx-autoconfig). It:
 *   1. registers default values for every halo.devtools.* pref, so they all
 *      show up in about:config ready to toggle (CSS reads the same prefs),
 *   2. watches for DevTools documents (toolbox + every panel iframe),
 *   3. attaches/detaches small UI mods per document and re-syncs live
 *      whenever a halo.devtools.* pref changes.
 * Nothing is injected into web content.
 */

const PREFIX = "halo.devtools.";

/** name → [default, label]. Booleans without "js." are read by the CSS. */
const PREFS = {
  "enabled":          [true,  "Halo theme"],
  "preset":           ["halo", "Theme preset"],
  "palette":          [true,  "Colour palette"],
  "font":             [true,  "Theme UI font"],
  "mono-font":        [true,  "Theme code font"],
  "tabs":             [true,  "Pill tabs"],
  "toolbars":         [true,  "Toolbars & inputs"],
  "console":          [true,  "Console styling"],
  "inspector":        [true,  "Inspector styling"],
  "debugger":         [true,  "Debugger / editor styling"],
  "netmonitor":       [true,  "Network styling"],
  "scrollbars":       [true,  "Slim scrollbars"],
  "motion":           [true,  "Motion"],
  "glow":             [true,  "Accent glow"],
  "compact":          [false, "Compact density"],
  "terminal.scanlines": [true, "Terminal scanlines"],

  "js.enabled":       [true,  "JS mods"],
  "accent":           ["",    "Accent"],
  "ui-font-size":     [12,    "UI font size"],
  "code-font-size":   [12,    "Code font size"],
  "ui-font-family":   ["",    "UI font family"],
  "mono-font-family": ["",    "Code font family"],
  "js.tab-indicator": [true,  "Sliding tab indicator"],
  "js.ripple":        [true,  "Click ripple"],
  "js.tab-scrollbar": [true,  "Slim sidebar-tab scrollbar"],
  "js.console-copy":  [true,  "Console copy button"],
  "js.toasts":        [true,  "Pref toasts"],
  "js.hotkeys":       [true,  "Hotkeys"],
};

/** Named accents → [light theme, dark theme]. Any CSS colour also works. */
const ACCENTS = {
  violet: ["#6d4dff", "#a48bff"],
  blue:   ["#2563eb", "#7aa7ff"],
  cyan:   ["#0e7490", "#5fd4e6"],
  green:  ["#15803d", "#6fdc9a"],
  amber:  ["#b45309", "#f5b75a"],
  rose:   ["#e11d48", "#ff8aa5"],
  mono:   ["#3f4456", "#d6dbe6"],
};
// "" = the active preset's own accent.
const ACCENT_ORDER = ["", ...Object.keys(ACCENTS)];

/** Presets that ship a presets/<name>.css. Ctrl+Alt+Shift+P cycles them. */
const PRESETS = ["halo", "glass", "ember", "terminal", "daylight"];

const errors = [];
function report(where, e) {
  errors.push(`${where}: ${e}`);
  console.error(`[Halo] ${where}:`, e);
}

/* ------------------------------------------------------------------ prefs */

const branch = Services.prefs.getBranch(PREFIX);

function registerDefaults() {
  const def = Services.prefs.getDefaultBranch(PREFIX);
  for (const [name, [value]] of Object.entries(PREFS)) {
    try {
      if (typeof value === "boolean") def.setBoolPref(name, value);
      else if (typeof value === "number") def.setIntPref(name, value);
      else def.setStringPref(name, value);
    } catch (e) {
      // A user value of a different type already exists; leave it alone.
      report(`default ${name}`, e);
    }
  }
}

function pref(name) {
  const fallback = PREFS[name]?.[0];
  try {
    switch (branch.getPrefType(name)) {
      case Ci.nsIPrefBranch.PREF_BOOL: return branch.getBoolPref(name);
      case Ci.nsIPrefBranch.PREF_INT: return branch.getIntPref(name);
      case Ci.nsIPrefBranch.PREF_STRING: return branch.getStringPref(name);
    }
  } catch (e) {}
  return fallback;
}

function setPref(name, value) {
  if (typeof value === "boolean") branch.setBoolPref(name, value);
  else if (typeof value === "number") branch.setIntPref(name, value);
  else branch.setStringPref(name, value);
}

/* -------------------------------------------------------------- utilities */

const isToolboxDoc = doc =>
  doc.documentURI.startsWith("about:devtools-toolbox") ||
  doc.documentURI.startsWith("chrome://devtools/content/framework/toolbox.xhtml");

const isConsoleDoc = doc =>
  doc.documentURI.startsWith("chrome://devtools/content/webconsole/");

function isDevtoolsDoc(doc) {
  const uri = doc?.documentURI || "";
  if (!uri.startsWith("chrome://devtools/content/") && !uri.startsWith("about:devtools-toolbox")) {
    return false;
  }
  return !!doc.nodePrincipal?.isSystemPrincipal;
}

function el(doc, tag, cls, text) {
  const n = doc.createElementNS("http://www.w3.org/1999/xhtml", tag);
  if (cls) n.className = cls;
  if (text) n.textContent = text;
  return n;
}

function reducedMotion(win) {
  return win.matchMedia("(prefers-reduced-motion: reduce)").matches || !pref("motion");
}

/* --------------------------------------------------------------- features
 * Each feature: { pref, applies(ctl), start(ctl) → stop() }.
 * `ignoresMaster` keeps a feature alive while halo.devtools.enabled is off
 * (so the hotkey can switch the theme back on).
 */

const FEATURES = {};

FEATURES.tabIndicator = {
  pref: "js.tab-indicator",
  applies: ctl => ctl.isToolbox && pref("tabs"),
  start({ doc, win }) {
    const root = doc.documentElement;
    const ind = el(doc, "div", "halo-tab-indicator");
    ind.setAttribute("halo-instant", "");
    ind.style.opacity = "0";
    root.appendChild(ind);
    root.setAttribute("halo-tab-indicator", "");

    let raf = 0;
    let first = true;
    const watched = new WeakSet();
    const update = () => {
      raf = 0;
      const sel = doc.querySelector(".devtools-tab.selected");
      const r = sel?.getBoundingClientRect();
      if (!r || !r.width) {
        ind.style.opacity = "0";
        return;
      }
      if (!watched.has(sel)) {
        watched.add(sel);
        ro.observe(sel);
      }
      Object.assign(ind.style, {
        left: `${r.left}px`, top: `${r.top}px`,
        width: `${r.width}px`, height: `${r.height}px`, opacity: "1",
      });
      if (first) {
        first = false;
        win.requestAnimationFrame(() => win.requestAnimationFrame(() => ind.removeAttribute("halo-instant")));
      }
    };
    const schedule = () => { raf ||= win.requestAnimationFrame(update); };
    const mo = new win.MutationObserver(schedule);
    mo.observe(doc.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
    const ro = new win.ResizeObserver(schedule);
    ro.observe(root);
    win.addEventListener("resize", schedule);
    doc.fonts?.ready.then(schedule);
    schedule();

    return () => {
      mo.disconnect();
      ro.disconnect();
      win.removeEventListener("resize", schedule);
      if (raf) win.cancelAnimationFrame(raf);
      ind.remove();
      root.removeAttribute("halo-tab-indicator");
    };
  },
};

FEATURES.ripple = {
  pref: "js.ripple",
  applies: () => true,
  start({ doc, win }) {
    const TARGETS = "button, .devtools-tab, .tabs-menu-item, .devtools-togglebutton, [role='button'], .request-list-item";
    const onDown = e => {
      if (e.button !== 0 || reducedMotion(win)) return;
      const t = e.target?.closest?.(TARGETS);
      if (!t) return;
      const r = t.getBoundingClientRect();
      if (!r.width || r.width > 600 || r.height > 60) return;
      const host = el(doc, "div", "halo-ripple-host");
      Object.assign(host.style, {
        left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
        borderRadius: win.getComputedStyle(t).borderRadius,
      });
      const size = 2 * Math.hypot(Math.max(e.clientX - r.left, r.right - e.clientX), Math.max(e.clientY - r.top, r.bottom - e.clientY));
      const dot = el(doc, "span", "halo-ripple");
      Object.assign(dot.style, {
        width: `${size}px`, height: `${size}px`,
        left: `${e.clientX - r.left - size / 2}px`, top: `${e.clientY - r.top - size / 2}px`,
      });
      host.appendChild(dot);
      doc.documentElement.appendChild(host);
      const done = () => host.remove();
      dot.addEventListener("animationend", done, { once: true });
      win.setTimeout(done, 900);
    };
    win.addEventListener("pointerdown", onDown, true);
    return () => {
      win.removeEventListener("pointerdown", onDown, true);
      for (const h of doc.querySelectorAll(".halo-ripple-host")) h.remove();
    };
  },
};

/* Sidebar tab strips (Inspector Layout/Computed/Changes…, Netmonitor details)
 * scroll sideways. Firefox can't draw a scrollbar thinner than `thin`, so the
 * native one is hidden (via :root[halo-tab-scroll] in mods.css) and replaced by
 * a 3px overlay bar that can be dragged or clicked; the plain mouse wheel also
 * scrolls the strip sideways. */
FEATURES.tabScroll = {
  pref: "js.tab-scrollbar",
  applies: () => true,
  start({ doc, win }) {
    const root = doc.documentElement;
    root.setAttribute("halo-tab-scroll", "");
    const strips = new Map(); // .tabs-navigation → { bar, thumb, scroller, geo }
    let raf = 0;

    // The element that actually overflows (stock CSS has varied between versions).
    const scrollerOf = nav => {
      for (const n of [nav.querySelector(":scope > .tabs-menu"), nav]) {
        if (n && n.scrollWidth > n.clientWidth + 1) return n;
      }
      return null;
    };

    const update = () => {
      raf = 0;
      const navs = new Set(doc.querySelectorAll(".tabs .tabs-navigation"));
      for (const [nav, s] of strips) {
        if (!navs.has(nav) || !nav.isConnected) {
          s.bar.remove();
          strips.delete(nav);
        }
      }
      for (const nav of navs) {
        let s = strips.get(nav);
        if (!s) {
          const bar = el(doc, "div", "halo-hscroll");
          const thumb = el(doc, "div", "halo-hscroll-thumb");
          bar.appendChild(thumb);
          root.appendChild(bar);
          s = { bar, thumb, scroller: null, geo: null };
          bindDrag(s);
          strips.set(nav, s);
          ro.observe(nav);
        }
        const sc = scrollerOf(nav);
        const nr = nav.getBoundingClientRect();
        s.scroller = sc;
        if (!sc || !nr.width || !nr.height) {
          s.bar.removeAttribute("halo-visible");
          s.geo = null;
          continue;
        }
        const r = sc.getBoundingClientRect();
        const max = sc.scrollWidth - sc.clientWidth;
        const thumbW = Math.max(24, r.width * (sc.clientWidth / sc.scrollWidth));
        const travel = Math.max(1, r.width - thumbW);
        s.geo = { max, travel, thumbW, left: r.left };
        Object.assign(s.bar.style, {
          left: `${r.left}px`, width: `${r.width}px`, top: `${nr.bottom - 6}px`,
        });
        Object.assign(s.thumb.style, {
          width: `${thumbW}px`,
          transform: `translateX(${(Math.abs(sc.scrollLeft) / max) * travel}px)`,
        });
        s.bar.setAttribute("halo-visible", "");
      }
    };
    const schedule = () => { raf ||= win.requestAnimationFrame(update); };

    function bindDrag(s) {
      let startX = 0, startScroll = 0, dragging = false;
      s.bar.addEventListener("pointerdown", e => {
        const { scroller: sc, geo } = s;
        if (e.button !== 0 || !sc || !geo) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.target !== s.thumb) {
          // Click on the track: centre the thumb under the pointer.
          const x = e.clientX - geo.left - geo.thumbW / 2;
          sc.scrollLeft = Math.min(1, Math.max(0, x / geo.travel)) * geo.max;
        }
        dragging = true;
        startX = e.clientX;
        startScroll = sc.scrollLeft;
        s.bar.setPointerCapture(e.pointerId);
        s.bar.setAttribute("halo-dragging", "");
      });
      s.bar.addEventListener("pointermove", e => {
        if (!dragging || !s.scroller || !s.geo) return;
        s.scroller.scrollLeft = startScroll + (e.clientX - startX) * (s.geo.max / s.geo.travel);
      });
      const end = () => {
        dragging = false;
        s.bar.removeAttribute("halo-dragging");
      };
      s.bar.addEventListener("pointerup", end);
      s.bar.addEventListener("pointercancel", end);
    }

    // Vertical wheel over a strip (or its bar) scrolls it sideways.
    const onWheel = e => {
      if (e.ctrlKey) return;
      const t = e.target;
      let s = null;
      for (const [nav, entry] of strips) {
        if (nav.contains(t) || entry.bar.contains(t)) { s = entry; break; }
      }
      const sc = s?.scroller;
      if (!sc || !s.geo) return;
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? sc.clientWidth : 1;
      const d = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) * unit;
      if (!d) return;
      e.preventDefault();
      sc.scrollLeft += d;
    };

    const mo = new win.MutationObserver(schedule);
    mo.observe(root, { subtree: true, childList: true });
    const ro = new win.ResizeObserver(schedule);
    ro.observe(root);
    win.addEventListener("resize", schedule);
    doc.addEventListener("scroll", schedule, true);
    doc.addEventListener("wheel", onWheel, { capture: true, passive: false });
    doc.fonts?.ready.then(schedule);
    schedule();

    return () => {
      mo.disconnect();
      ro.disconnect();
      win.removeEventListener("resize", schedule);
      doc.removeEventListener("scroll", schedule, true);
      doc.removeEventListener("wheel", onWheel, { capture: true });
      if (raf) win.cancelAnimationFrame(raf);
      for (const s of strips.values()) s.bar.remove();
      strips.clear();
      root.removeAttribute("halo-tab-scroll");
    };
  },
};

FEATURES.consoleCopy = {
  pref: "js.console-copy",
  applies: ctl => isConsoleDoc(ctl.doc),
  start({ doc, win }) {
    const btn = el(doc, "button", "halo-copy-btn", "Copy");
    btn.type = "button";
    btn.title = "Copy message text (Halo)";
    doc.documentElement.appendChild(btn);
    let current = null;
    let resetTimer = 0;

    const hide = () => {
      current = null;
      btn.removeAttribute("halo-visible");
    };
    const place = msg => {
      const r = msg.getBoundingClientRect();
      const loc = msg.querySelector(".message-location");
      const lr = loc?.getBoundingClientRect();
      const rightEdge = lr && lr.width ? lr.left - 6 : r.right - 8;
      btn.style.top = `${r.top + Math.max(2, Math.min(r.height, 22) / 2 - 10)}px`;
      btn.style.left = "auto";
      btn.style.right = `${doc.documentElement.clientWidth - rightEdge}px`;
      btn.setAttribute("halo-visible", "");
    };
    const onOver = e => {
      if (btn.contains(e.target)) return;
      const msg = e.target?.closest?.(".webconsole-output .message");
      if (!msg) return hide();
      if (msg !== current) {
        current = msg;
        btn.textContent = "Copy";
        btn.removeAttribute("halo-copied");
        place(msg);
      }
    };
    const onClick = e => {
      e.preventDefault();
      e.stopPropagation();
      if (!current) return;
      const body = current.querySelector(".message-body") || current;
      const text = (body.innerText || body.textContent || "").trim();
      Cc["@mozilla.org/widget/clipboardhelper;1"].getService(Ci.nsIClipboardHelper).copyString(text);
      btn.textContent = "Copied";
      btn.setAttribute("halo-copied", "");
      win.clearTimeout(resetTimer);
      resetTimer = win.setTimeout(() => {
        btn.textContent = "Copy";
        btn.removeAttribute("halo-copied");
      }, 1200);
    };
    const onLeave = e => { if (!e.relatedTarget) hide(); };

    doc.addEventListener("pointerover", onOver, true);
    doc.addEventListener("scroll", hide, true);
    doc.documentElement.addEventListener("pointerleave", onLeave);
    btn.addEventListener("click", onClick);
    return () => {
      doc.removeEventListener("pointerover", onOver, true);
      doc.removeEventListener("scroll", hide, true);
      doc.documentElement.removeEventListener("pointerleave", onLeave);
      win.clearTimeout(resetTimer);
      btn.remove();
    };
  },
};

FEATURES.toasts = {
  pref: "js.toasts",
  applies: ctl => ctl.isToolbox,
  start(ctl) {
    const { doc, win } = ctl;
    const toast = el(doc, "div", "halo-toast");
    toast.setAttribute("role", "status");
    doc.documentElement.appendChild(toast);
    let timer = 0;
    ctl.toast = (name, value) => {
      const label = PREFS[name]?.[1] ?? name;
      let shown = value;
      if (typeof value === "boolean") shown = value ? "On" : "Off";
      else if (name.endsWith("font-size")) shown = `${value}px`;
      else if (value === "") shown = name === "accent" ? "Preset default" : "Default";
      else if (name === "preset") shown = String(value).charAt(0).toUpperCase() + String(value).slice(1);
      toast.replaceChildren(
        el(doc, "span", "halo-toast-dot"),
        el(doc, "span", "halo-toast-label", label),
        el(doc, "span", "halo-toast-pref", PREFIX + name),
        el(doc, "span", "halo-toast-value", String(shown)),
      );
      toast.setAttribute("halo-state", value === false ? "off" : "on");
      // restart the entrance transition
      toast.removeAttribute("halo-visible");
      void toast.offsetWidth;
      toast.setAttribute("halo-visible", "");
      win.clearTimeout(timer);
      timer = win.setTimeout(() => toast.removeAttribute("halo-visible"), 2200);
    };
    return () => {
      win.clearTimeout(timer);
      delete ctl.toast;
      toast.remove();
    };
  },
};

FEATURES.hotkeys = {
  pref: "js.hotkeys",
  ignoresMaster: true,
  applies: () => true,
  start({ win }) {
    const onKey = e => {
      if (!(e.ctrlKey && e.altKey && e.shiftKey) || e.metaKey) return;
      let handled = true;
      switch (e.code) {
        case "KeyH": setPref("enabled", !pref("enabled")); break;
        case "KeyD": setPref("compact", !pref("compact")); break;
        case "KeyM": setPref("motion", !pref("motion")); break;
        case "KeyA": {
          const i = ACCENT_ORDER.indexOf(pref("accent"));
          setPref("accent", ACCENT_ORDER[(i + 1) % ACCENT_ORDER.length]);
          break;
        }
        case "KeyP": {
          const i = PRESETS.indexOf(pref("preset"));
          setPref("preset", PRESETS[(i + 1) % PRESETS.length]);
          break;
        }
        default: handled = false;
      }
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    win.addEventListener("keydown", onKey, true);
    return () => win.removeEventListener("keydown", onKey, true);
  },
};

/* ------------------------------------------------------- CSS variables */

const VAR_NAMES = ["--halo-accent", "--halo-ui-font-size", "--halo-code-font-size",
                   "--halo-ui-font-override", "--halo-mono-font-override"];

/** null = leave the accent to the active preset's CSS. */
function resolveAccent(win, value) {
  const v = String(value || "").trim().toLowerCase();
  if (!v) return null;
  const named = ACCENTS[v];
  if (named) return `light-dark(${named[0]}, ${named[1]})`;
  if (win.CSS.supports("color", value)) return String(value).trim();
  return null;
}

function clampSize(n, fallback) {
  n = Number(n);
  return Number.isFinite(n) && n >= 8 && n <= 28 ? n : fallback;
}

function applyVars({ doc, win }, on) {
  const s = doc.documentElement?.style;
  if (!s) return;
  if (!on) {
    for (const v of VAR_NAMES) s.removeProperty(v);
    return;
  }
  const accent = resolveAccent(win, pref("accent"));
  if (accent) s.setProperty("--halo-accent", accent);
  else s.removeProperty("--halo-accent");
  s.setProperty("--halo-ui-font-size", `${clampSize(pref("ui-font-size"), 12)}px`);
  s.setProperty("--halo-code-font-size", `${clampSize(pref("code-font-size"), 12)}px`);
  for (const [p, v] of [["ui-font-family", "--halo-ui-font-override"], ["mono-font-family", "--halo-mono-font-override"]]) {
    const fam = String(pref(p) || "").trim();
    if (fam && /^[\w\s"',-]+$/.test(fam)) s.setProperty(v, fam);
    else s.removeProperty(v);
  }
}

/* ------------------------------------------------- per-document control */

const controllers = new Set();

class DocController {
  constructor(doc) {
    this.doc = doc;
    this.win = doc.defaultView;
    this.isToolbox = isToolboxDoc(doc);
    this.active = new Map(); // feature name → stop()
    this.onUnload = () => this.destroy();
    this.win.addEventListener("unload", this.onUnload, { once: true });
    controllers.add(this);
    this.sync();
  }

  sync() {
    const master = pref("enabled");
    const js = pref("js.enabled");
    try {
      applyVars(this, master && js);
    } catch (e) {
      report("vars", e);
    }
    for (const [name, f] of Object.entries(FEATURES)) {
      let want = false;
      try {
        want = js && (master || f.ignoresMaster) && pref(f.pref) && f.applies(this);
      } catch (e) {
        report(`${name}.applies`, e);
      }
      const running = this.active.has(name);
      if (want && !running) {
        try {
          this.active.set(name, f.start(this));
        } catch (e) {
          report(`${name}.start`, e);
        }
      } else if (!want && running) {
        this.stop(name);
      }
    }
  }

  stop(name) {
    const stop = this.active.get(name);
    this.active.delete(name);
    try {
      stop?.();
    } catch (e) {
      report(`${name}.stop`, e);
    }
  }

  destroy() {
    for (const name of [...this.active.keys()]) this.stop(name);
    try {
      applyVars(this, false);
    } catch (e) {}
    controllers.delete(this);
  }
}

function attach(doc) {
  if (!isDevtoolsDoc(doc) || doc.__haloController) return;
  try {
    doc.__haloController = new DocController(doc);
  } catch (e) {
    report("attach", e);
  }
}

function watch(doc) {
  if (!isDevtoolsDoc(doc)) return;
  if (doc.readyState === "loading") {
    doc.defaultView?.addEventListener("DOMContentLoaded", () => attach(doc), { once: true });
  } else {
    attach(doc);
  }
}

/** Attach to devtools documents that already exist (module loaded late). */
function scanExisting() {
  const seen = new Set();
  const walk = doc => {
    if (!doc || seen.has(doc)) return;
    seen.add(doc);
    watch(doc);
    for (const f of doc.querySelectorAll("iframe, browser")) {
      try {
        walk(f.contentDocument);
      } catch (e) {}
    }
  };
  for (const win of Services.wm.getEnumerator(null)) walk(win.document);
}

/* ------------------------------------------------------------- bootstrap */

const docObserver = {
  observe(subject) {
    // Cheap string check first: this fires for every document in the parent.
    const uri = subject?.documentURI;
    if (uri && (uri.startsWith("chrome://devtools/") || uri.startsWith("about:devtools"))) {
      watch(subject);
    }
  },
};

const prefObserver = {
  observe(_subject, _topic, data) {
    const name = data.slice(PREFIX.length);
    for (const ctl of [...controllers]) {
      if (Cu.isDeadWrapper(ctl.doc) || !ctl.doc.defaultView) {
        controllers.delete(ctl);
        continue;
      }
      try {
        ctl.sync();
        if (name in PREFS) ctl.toast?.(name, pref(name));
      } catch (e) {
        report("sync", e);
      }
    }
  },
};

function init() {
  registerDefaults();
  Services.obs.addObserver(docObserver, "document-element-inserted");
  Services.prefs.addObserver(PREFIX, prefObserver);
  scanExisting();
}

init();

export const Halo = {
  version: "1.1.0",
  PREFS,
  PRESETS,
  ACCENTS,
  errors,
  controllers,
  pref,
  rescan: scanExisting,
};
