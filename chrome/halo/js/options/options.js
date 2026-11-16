/* Halo DevTools settings page.
 * A thin view over about:config: every control reads/writes one
 * halo.devtools.* pref; the module and the CSS react to the pref change.
 * The page keeps no state of its own, so it, about:config and user.js agree. */
const { Halo } = ChromeUtils.importESModule("chrome://halo/content/halo.sys.mjs");
const P = Services.prefs;
const X = Halo.PREFIX;
const $ = s => document.querySelector(s);

/* Layout + help text. Labels, defaults and types come from Halo.PREFS. */
const SECTIONS = [
  {
    title: "Theme",
    items: {
      "preset": "The overall look. Glass, Ember and Terminal are dark-only; Halo and Daylight follow the DevTools light/dark setting.",
      "accent": "Highlight colour. “Preset” uses the preset’s own.",
      "compact": "Denser layout with a shorter tab strip.",
      "terminal.scanlines": "Terminal preset only: the scanline and vignette overlay.",
    },
  },
  {
    title: "Modules",
    sub: "These apply to every preset. Turning one off returns that area to stock Firefox styling.",
    items: {
      "palette": "Recolours Firefox’s own colour variables, so unstyled panels follow the preset too.",
      "font": "Use the preset’s bundled interface font.",
      "mono-font": "Use the preset’s bundled code font.",
      "tabs": "Toolbox and sidebar tab styling.",
      "toolbars": "Inputs, buttons, splitters, popups, notification bars.",
      "console": "Console panel styling.",
      "inspector": "Inspector panel styling.",
      "debugger": "Debugger and editor styling.",
      "netmonitor": "Network panel styling.",
      "scrollbars": "Thin scrollbars.",
      "motion": "Transitions, panel fade-in, popup scale-in. Respects reduced motion.",
      "glow": "Accent glow (and the text glow in Terminal).",
    },
  },
  {
    title: "Typography",
    sub: "Applied by the JS module.",
    items: {
      "ui-font-size": "Interface text size (8–28 px).",
      "code-font-size": "Code and console text size (8–28 px).",
      "ui-font-family": "Overrides the preset’s interface font, e.g. Inter. Empty = preset font.",
      "mono-font-family": "Overrides the preset’s code font, e.g. \"JetBrains Mono\". Empty = preset font.",
    },
  },
  {
    title: "JS mods",
    items: {
      "js.enabled": "Master switch for every JS mod. The CSS theme keeps working without them.",
      "js.tab-indicator": "The selected-tab shape slides between tabs.",
      "js.ripple": "Accent ripple on clicks.",
      "js.tab-scrollbar": "3px draggable scrollbar on sidebar tab strips; the wheel scrolls them sideways.",
      "js.console-copy": "Hover a console message to get a Copy button.",
      "js.toasts": "A small toast in DevTools confirms each setting change.",
      "js.hotkeys": "The Ctrl+Alt+Shift hotkeys listed at the bottom of this page.",
    },
  },
];

/** Keys applied by the JS module (greyed out while js.enabled is off). */
const NEEDS_JS = new Set(["accent", "ui-font-size", "code-font-size", "ui-font-family", "mono-font-family"]);
const PRESET_NOTES = {
  halo: "Graphite, violet · light & dark",
  glass: "Aurora, frosted panes · dark",
  ember: "Warm IDE, copper · dark",
  terminal: "Phosphor on black · dark",
  daylight: "Floating panes, cobalt · light & dark",
};
const FAMILY_OK = /^[\w\s"',-]+$/;

// Any key missing from SECTIONS still gets a control.
{
  const listed = new Set(SECTIONS.flatMap(s => Object.keys(s.items)));
  const rest = Object.keys(Halo.PREFS).filter(k => k !== "enabled" && !listed.has(k));
  if (rest.length) SECTIONS.push({ title: "Other", items: Object.fromEntries(rest.map(k => [k, ""])) });
}

/* ---------------------------------------------------------------- helpers */

function el(tag, attrs = {}, kids = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  n.append(...[].concat(kids).filter(k => k != null && k !== false));
  return n;
}

const get = key => Halo.pref(key);
const set = (key, v) => Halo.setPref(key, v);
// install.ps1 / user.js seed every pref as a user value equal to its default;
// only a value that actually differs counts as "changed".
const isUser = key => P.prefHasUserValue(X + key) && get(key) !== Halo.PREFS[key]?.[0];
const reset = key => P.clearUserPref(X + key);
const fieldId = key => "f-" + key.replace(/\W/g, "-");

/** The accent the page itself uses, so it previews the current choice. */
function currentAccent() {
  // The accent pref is applied by the JS module; without it DevTools shows the preset's.
  const a = get("js.enabled") ? String(get("accent") || "").trim().toLowerCase() : "";
  if (Halo.ACCENTS[a]) return `light-dark(${Halo.ACCENTS[a][0]}, ${Halo.ACCENTS[a][1]})`;
  if (a && CSS.supports("color", a)) return a;
  return Halo.PRESET_ACCENTS[get("preset")] || Halo.PRESET_ACCENTS.halo;
}

/* --------------------------------------------------------------- controls */

function boolControl(key) {
  const input = el("input", { type: "checkbox", class: "switch", id: fieldId(key) });
  input.checked = !!get(key);
  input.addEventListener("change", () => set(key, input.checked));
  return input;
}

function presetControl(key) {
  const cur = get(key);
  const known = Halo.PRESETS.includes(cur);
  const wrap = el("div", { class: "presets", role: "radiogroup", "aria-label": "Theme preset", id: fieldId(key) });
  for (const name of Halo.PRESETS) {
    const id = `preset-${name}`;
    const input = el("input", { type: "radio", name: "preset", id, value: name });
    input.checked = cur === name;
    input.addEventListener("change", () => set(key, name));
    wrap.append(el("label", { class: "preset", for: id, "data-preset": name }, [
      input,
      el("span", { class: "chip", style: `--chip: ${Halo.PRESET_ACCENTS[name]}` }),
      el("span", { class: "pname", text: name[0].toUpperCase() + name.slice(1) }),
      el("span", { class: "pnote", text: PRESET_NOTES[name] || "" }),
    ]));
  }
  if (!known) wrap.append(el("div", { class: "msg", text: `Unknown preset “${cur}” — DevTools falls back to plain Halo.` }));
  return wrap;
}

function accentControl(key) {
  const cur = String(get(key) || "").trim();
  const lc = cur.toLowerCase();
  const isNamed = lc === "" || lc in Halo.ACCENTS;
  const wrap = el("div", { class: "accents", id: fieldId(key) });
  const swatches = el("div", { class: "swatches", role: "radiogroup", "aria-label": "Accent" });
  const choices = [["", "Preset", Halo.PRESET_ACCENTS[get("preset")] || Halo.PRESET_ACCENTS.halo],
    ...Object.entries(Halo.ACCENTS).map(([n, [l, d]]) => [n, n, `light-dark(${l}, ${d})`])];
  for (const [value, label, color] of choices) {
    const id = `accent-${value || "preset"}`;
    const input = el("input", { type: "radio", name: "accent", id, value });
    input.checked = isNamed && lc === value;
    input.addEventListener("change", () => (value === "" ? reset(key) : set(key, value)));
    swatches.append(el("label", { class: "swatch", for: id, title: label }, [
      input, el("span", { class: "dot", style: `--dot: ${color}` }), el("span", { text: label }),
    ]));
  }
  const msg = el("div", { class: "msg", role: "alert" });
  const text = el("input", { type: "text", class: "custom", placeholder: "Custom: #22c55e, rgb(…), oklch(…)", "aria-label": "Custom accent colour", spellcheck: "false" });
  if (!isNamed) text.value = cur;
  const picker = el("input", { type: "color", "aria-label": "Pick a custom accent colour" });
  if (/^#[0-9a-f]{6}$/i.test(cur)) picker.value = cur;
  const apply = v => {
    v = v.trim();
    if (!v) return reset(key);
    if (!CSS.supports("color", v) && !(v.toLowerCase() in Halo.ACCENTS)) {
      text.classList.add("bad");
      msg.textContent = "Not saved: not a valid CSS colour.";
      return;
    }
    text.classList.remove("bad");
    msg.textContent = "";
    set(key, v);
  };
  text.addEventListener("change", () => apply(text.value));
  picker.addEventListener("input", () => { text.value = picker.value; });
  picker.addEventListener("change", () => apply(picker.value));
  wrap.append(swatches, el("div", { class: "custom-row" }, [picker, text]), msg);
  return wrap;
}

function intControl(key) {
  const input = el("input", { type: "number", id: fieldId(key), min: "8", max: "28", step: "1" });
  input.value = get(key);
  const range = el("input", { type: "range", min: "8", max: "28", step: "1", "aria-label": Halo.PREFS[key][1] });
  range.value = get(key);
  const commit = v => {
    const n = Number(v);
    if (Number.isInteger(n) && n >= 8 && n <= 28) set(key, n);
    else input.value = range.value = get(key);
  };
  range.addEventListener("input", () => { input.value = range.value; });
  range.addEventListener("change", () => commit(range.value));
  input.addEventListener("change", () => commit(input.value));
  return el("div", { class: "num" }, [range, input, el("span", { class: "unit", text: "px" })]);
}

function textControl(key) {
  const family = key.endsWith("font-family");
  const input = el("input", { type: "text", id: fieldId(key), spellcheck: "false", placeholder: family ? "Preset font" : "" });
  input.value = get(key);
  const msg = el("div", { class: "msg", role: "alert" });
  input.addEventListener("change", () => {
    const v = input.value.trim();
    if (family && v && !FAMILY_OK.test(v)) {
      input.classList.add("bad");
      msg.textContent = "Not saved: use letters, digits, spaces, quotes, commas or hyphens only.";
      return;
    }
    input.classList.remove("bad");
    msg.textContent = "";
    v ? set(key, v) : reset(key);
  });
  return el("div", {}, [input, msg]);
}

function control(key) {
  if (key === "preset") return presetControl(key);
  if (key === "accent") return accentControl(key);
  const def = Halo.PREFS[key]?.[0];
  if (typeof def === "boolean") return boolControl(key);
  if (typeof def === "number") return intControl(key);
  return textControl(key);
}

/* ----------------------------------------------------------------- render */

function row(key, help, ctx) {
  const label = Halo.PREFS[key]?.[1] ?? key;
  const def = Halo.PREFS[key]?.[0];
  const wide = key === "preset" || key === "accent";
  const isBool = typeof def === "boolean";
  const off = (key !== "js.enabled" && (key.startsWith("js.") || NEEDS_JS.has(key)) && !ctx.js)
    || (key === "terminal.scanlines" && get("preset") !== "terminal");
  const tags = [];
  if (NEEDS_JS.has(key)) tags.push(el("span", { class: "tag", text: "JS" }));
  if (key === "terminal.scanlines" && get("preset") !== "terminal") tags.push(el("span", { class: "tag", text: "Terminal only" }));
  const resetBtn = isUser(key)
    ? el("button", { type: "button", class: "reset", text: "Reset", title: `Reset ${X}${key} to ${JSON.stringify(def)}`, onclick: () => reset(key) })
    : null;
  const head = el("div", { class: "head" }, [
    el("label", { for: fieldId(key), class: "label" }, [el("span", { class: "title", text: label }), ...tags]),
    el("div", { class: "desc", text: help }),
    el("code", { class: "id", text: X + key }),
  ]);
  return el("div", {
    class: "setting" + (wide ? " wide" : "") + (isBool ? " bool" : ""),
    "data-key": key, "data-off": String(off), "data-user": String(isUser(key)),
  }, wide
    ? [el("div", { class: "top" }, [head, resetBtn]), control(key)]
    : [head, el("div", { class: "ctl" }, [control(key), resetBtn])]);
}

function render() {
  const focusId = document.activeElement?.id;
  const filter = $("#filter").value.trim().toLowerCase();
  const ctx = { master: !!get("enabled"), js: !!get("js.enabled") };
  document.documentElement.style.setProperty("--accent", currentAccent());
  document.documentElement.toggleAttribute("data-theme-off", !ctx.master);

  const frag = document.createDocumentFragment();
  let shown = 0;
  for (const sec of SECTIONS) {
    const rows = Object.entries(sec.items)
      .filter(([key, help]) => key in Halo.PREFS)
      .filter(([key, help]) => !filter || `${key} ${Halo.PREFS[key][1]} ${help} ${sec.title}`.toLowerCase().includes(filter))
      .map(([key, help]) => row(key, help, ctx));
    if (!rows.length) continue;
    shown += rows.length;
    frag.append(el("section", { class: "group" }, [
      el("h2", { text: sec.title }),
      sec.sub ? el("p", { class: "sub", text: sec.sub }) : null,
      el("div", { class: "card" }, rows),
    ]));
  }
  if (!shown) frag.append(el("p", { class: "empty", text: "No settings match the filter." }));
  $("#sections").replaceChildren(frag);

  $("#master").checked = ctx.master;
  const modules = Object.keys(SECTIONS[1].items);
  const modOn = modules.filter(k => get(k)).length;
  const changed = Object.keys(Halo.PREFS).filter(isUser).length;
  $("#summary").textContent = [
    ctx.master ? `${get("preset")} preset` : "theme off",
    `${modOn}/${modules.length} modules`,
    ctx.js ? "JS mods on" : "JS mods off",
    `${changed} changed`,
    `v${Halo.version}`,
  ].join(" · ");

  $("#errors").hidden = !Halo.errors.length;
  $("#error-list").textContent = Halo.errors.join("\n");

  if (focusId && document.getElementById(focusId)) document.getElementById(focusId).focus();
}

$("#master").addEventListener("change", e => set("enabled", e.target.checked));
$("#filter").addEventListener("input", render);
$("#reset-all").addEventListener("click", () => {
  for (const key of Object.keys(Halo.PREFS)) reset(key);
});

// Stay in sync with about:config, hotkeys and other windows. Don't re-render
// while the user is typing into a field.
let timer = 0, pending = false;
const typing = () => document.activeElement?.matches("input[type=text], input[type=number], input[type=search]");
const obs = {
  observe() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (typing()) pending = true;
      else render();
    }, 120);
  },
};
document.addEventListener("focusout", () => setTimeout(() => {
  if (pending && !typing()) { pending = false; render(); }
}, 0));
P.addObserver(X, obs);
window.addEventListener("unload", () => P.removeObserver(X, obs));

render();
window.haloOptions = { render };
