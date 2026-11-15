"""Real-browser test harness for Halo DevTools.

Builds a throwaway Firefox profile from this repo, launches Firefox through
geckodriver, switches WebDriver into chrome context, opens DevTools panels and
takes screenshots / evaluates privileged JS against the live devtools DOM.

Stdlib only.  Usage:
    python -I test/harness.py [--preset halo,glass] [--skip-shared] [--geckodriver PATH]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebDriver:
    def __init__(self, gecko: str, firefox: str, profile: Path, prefs: dict | None = None):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [gecko, "--port", str(self.port), "--log", "error"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"},
        )
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + "/status", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        caps = {
            "capabilities": {
                "alwaysMatch": {
                    "browserName": "firefox",
                    "moz:firefoxOptions": {
                        "binary": firefox,
                        "args": ["-profile", str(profile), "-no-remote"],
                        "prefs": prefs or {},
                    },
                }
            }
        }
        self.sid = None
        try:
            self.sid = self._req("POST", "/session", caps)["sessionId"]
            self._setup()
        except Exception:
            self.quit()
            raise

    def _setup(self):
        self._req("POST", f"/session/{self.sid}/moz/context", {"context": "chrome"})
        self._req("POST", f"/session/{self.sid}/window/rect", {"x": 20, "y": 20, "width": 1500, "height": 950})

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())["value"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(e.read().decode()) from None

    def js(self, script: str, *args, asynchronous: bool = False):
        ep = "execute/async" if asynchronous else "execute/sync"
        return self._req("POST", f"/session/{self.sid}/{ep}", {"script": script, "args": list(args)})

    def screenshot(self, path: Path):
        png = self._req("GET", f"/session/{self.sid}/screenshot")
        path.write_bytes(base64.b64decode(png))
        return path

    def quit(self):
        try:
            if self.sid:
                self._req("DELETE", f"/session/{self.sid}")
        except Exception:
            pass
        finally:
            self.proc.terminate()


def build_profile(dest: Path) -> Path:
    """Copy chrome/ + user.js into a fresh profile and add a test-only loader."""
    shutil.copytree(REPO / "chrome", dest / "chrome")
    shutil.copy(REPO / "user.js", dest / "user.js")
    # The local Firefox install ships an autoconfig config.js that registers
    # <profile>/chrome/utils/chrome.manifest and imports
    # chrome://userscripts/content/sine.sys.mjs.  For the throwaway profile we
    # drop in a tiny stand-in that boots Halo through the same real startup
    # path.  (Users install via loader/ or fx-autoconfig instead; see README.)
    utils = dest / "chrome" / "utils"
    utils.mkdir(parents=True, exist_ok=True)
    (utils / "chrome.manifest").write_text("content userscripts ./\n")
    (utils / "sine.sys.mjs").write_text(
        'const f = Services.dirsvc.get("UChrm", Ci.nsIFile);\n'
        'f.append("halo"); f.append("chrome.manifest");\n'
        'Components.manager.QueryInterface(Ci.nsIComponentRegistrar).autoRegister(f);\n'
        'ChromeUtils.importESModule("chrome://halo/content/halo.sys.mjs");\n'
    )
    return dest


OPEN_TOOL = """
const [tool, done] = [arguments[0], arguments[arguments.length - 1]];
(async () => {
  const { require } = ChromeUtils.importESModule("resource://devtools/shared/loader/Loader.sys.mjs");
  const { gDevTools } = require("devtools/client/framework/devtools");
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  const tb = await gDevTools.showToolboxForTab(win.gBrowser.selectedTab, { toolId: tool, hostType: "bottom" });
  await new Promise(r => win.setTimeout(r, 1200));
  done(String(tb.currentToolId));
})().catch(e => done("ERR " + e + "\\n" + e.stack));
"""

LOAD_URL = """
const [url, done] = [arguments[0], arguments[arguments.length - 1]];
const win = Services.wm.getMostRecentWindow("navigator:browser");
const b = win.gBrowser.selectedBrowser;
win.gBrowser.loadURI(Services.io.newURI(url), { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
win.setTimeout(() => done(b.currentURI.spec), 2500);
"""

SPLIT_CONSOLE = """
const [open, done] = [arguments[0], arguments[arguments.length - 1]];
(async () => {
  const { require } = ChromeUtils.importESModule("resource://devtools/shared/loader/Loader.sys.mjs");
  const { gDevTools } = require("devtools/client/framework/devtools");
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  const tb = gDevTools.getToolboxForTab(win.gBrowser.selectedTab);
  await (open ? tb.openSplitConsole() : tb.closeSplitConsole());
  await new Promise(r => win.setTimeout(r, 900));
  done("ok");
})().catch(e => done("ERR " + e));
"""

SET_PREF = """
const [name, value] = arguments;
if (typeof value === "boolean") Services.prefs.setBoolPref(name, value);
else if (typeof value === "number") Services.prefs.setIntPref(name, value);
else Services.prefs.setStringPref(name, value);
"""

# Run a function body inside a devtools document (toolbox or panel iframe).
IN_DOC = """
const [match] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
const docs = [];
(function walk(d) {
  docs.push(d);
  for (const f of d.querySelectorAll("iframe, browser")) {
    try { if (f.contentDocument) walk(f.contentDocument); } catch (e) {}
  }
})(win.document);
const doc = docs.find(d => d.documentURI.includes(match));
if (!doc) return "NO DOC " + match + " :: " + docs.map(d => d.documentURI).join(" | ");
const win2 = doc.defaultView;
return ((doc, win) => { /*BODY*/ })(doc, win2);
"""


def in_doc(wd, match, body):
    """Evaluate `body` (JS using `doc`/`win`) inside the first devtools document whose URI contains `match`."""
    return wd.js(IN_DOC.replace("/*BODY*/", body), match)


TEST_PREFS = {
    "devtools.theme": "dark",
    "devtools.toolbox.footer.height": 560,
    "devtools.everOpened": True,
}

TOOLS = ("webconsole", "inspector", "jsdebugger", "netmonitor", "styleeditor")

# Per-preset expectations. "modes" = the devtools.theme values the preset is
# designed for (dark-only presets force dark even when DevTools is light).
# "checks" = (label, doc-match, JS body, expected substring of the result).
PRESET_SPECS = {
    "halo": {
        "modes": ("dark", "light"),
        "ui_font": "Halo Geist",
        "mono_font": "Halo Geist Mono",
        "checks": [
            ("tab strip uses Halo graphite", "toolbox",
             "return win.getComputedStyle(doc.querySelector('.devtools-tabbar')).backgroundColor", "rgb(8, 9, 12)"),
            ("error count is a red pill", "toolbox",
             "const e = doc.querySelector('.toolbox-error'); return e ? win.getComputedStyle(e).borderTopLeftRadius : 'no badge'", "999px"),
        ],
    },
    "glass": {
        "modes": ("dark",),
        "ui_font": "Halo Manrope",
        "split_gap": 8,
        "mono_font": "Halo Fira Code",
        "checks": [
            ("aurora painted behind the toolbox", "toolbox",
             "return win.getComputedStyle(doc.querySelector('#toolbox-container')).backgroundImage", "radial-gradient"),
            ("panes are rounded floating frames", "toolbox",
             "const f = doc.querySelector('#toolbox-panel-webconsole[expanded] iframe, .toolbox-panel[selected] iframe'); return f ? win.getComputedStyle(f).borderTopLeftRadius : 'no iframe'", "14px"),
            ("panes sit inside 8px gutters", "toolbox",
             "const p = doc.querySelector('#toolbox-panel-webconsole[expanded], .toolbox-panel[selected]').getBoundingClientRect(); return String(Math.round(p.left)) + ',' + String(Math.round(doc.documentElement.clientWidth - p.right))", "8,8"),
            ("tab capsule hugs its tabs", "toolbox",
             "const t = doc.querySelector('.toolbox-tabs').getBoundingClientRect(), w = doc.querySelector('.toolbox-tabs-wrapper').getBoundingClientRect(); return t.width < w.width - 20 ? 'hugs' : t.width + ' vs ' + w.width", "hugs"),
        ],
    },
    "ember": {
        "modes": ("dark",),
        "ui_font": "Halo Instrument Sans",
        "mono_font": "Halo Martian Mono",
        "checks": [
            ("editor tabs: square indicator with copper-gold top line", "toolbox",
             "const i = doc.querySelector('.halo-tab-indicator'); if (!i) return 'none'; const a = win.getComputedStyle(i, '::after'); return win.getComputedStyle(i).borderTopLeftRadius + ' ' + a.top + ' ' + a.backgroundImage.slice(0, 15)", "0px 0px linear-gradient"),
            ("warm tab strip", "toolbox",
             "return win.getComputedStyle(doc.querySelector('.devtools-tabbar')).backgroundColor", "rgb(16, 13, 11)"),
            ("breadcrumbs styled as status bar", "inspector/index",
             "const b = doc.querySelector('#inspector-breadcrumbs-toolbar'); return b ? win.getComputedStyle(b).backgroundColor : 'none'", "rgb(16, 13, 11)"),
            ("rules are a flat list, not cards", "inspector/index",
             "const r = doc.querySelector('.ruleview-rule'); return r ? win.getComputedStyle(r).borderTopLeftRadius + ' ' + win.getComputedStyle(r).marginLeft : 'none'", "0px 0px"),
            ("markup indent guides", "markup",
             "const c = doc.querySelector('#root-wrapper .children'); return c ? win.getComputedStyle(c).borderLeftWidth : 'none'", "1px"),
        ],
    },
    "terminal": {
        "modes": ("dark",),
        "ui_font": "Halo JetBrains Mono",
        "mono_font": "Halo JetBrains Mono",
        "checks": [
            ("[ bracket ] tab labels", "toolbox",
             "const l = doc.querySelector('.devtools-tab-label'); return win.getComputedStyle(l, '::before').content + win.getComputedStyle(l, '::after').content", '"[ "" ]"'),
            ("selected tab is an inverted phosphor block", "toolbox",
             "return win.getComputedStyle(doc.querySelector('.halo-tab-indicator')).backgroundColor + ' / ' + win.getComputedStyle(doc.querySelector('.devtools-tab.selected')).color", "rgb(108, 255, 160) / rgb(5, 8, 6)"),
            ("scanline overlay present and click-through", "webconsole",
             "const a = win.getComputedStyle(doc.documentElement, '::after'); return a.position + ' ' + a.pointerEvents + ' ' + a.backgroundImage.slice(0, 25)", "fixed none repeating-linear-gradient"),
            ("square corners", "toolbox",
             "return win.getComputedStyle(doc.querySelector('.devtools-tab')).borderTopLeftRadius", "0px"),
        ],
        "extra": "terminal",
    },
    "daylight": {
        "modes": ("light", "dark"),
        "ui_font": "Halo Figtree",
        "mono_font": "Halo DM Mono",
        "split_gap": 8,
        "checks": [
            ("light canvas behind floating panes", "toolbox",
             "return win.getComputedStyle(doc.querySelector('#toolbox-container')).backgroundColor", "rgb(238, 241, 246)"),
            ("cobalt pill for the selected tab", "toolbox",
             "const i = doc.querySelector('.halo-tab-indicator'); return win.getComputedStyle(i).borderTopLeftRadius + ' ' + win.getComputedStyle(i).backgroundColor", "999px rgb(47, 91, 255)"),
            ("panes sit inside 8px gutters", "toolbox",
             "const p = doc.querySelector('#toolbox-panel-webconsole[expanded], .toolbox-panel[selected]').getBoundingClientRect(); return String(Math.round(p.left)) + ',' + String(Math.round(doc.documentElement.clientWidth - p.right))", "8,8"),
            ("white panes", "webconsole",
             "return win.getComputedStyle(doc.documentElement).getPropertyValue('--theme-body-background').trim()", "light-dark(#ffffff, #161a22)"),
        ],
        "extra": "daylight",
    },
}

MEASURE_FONTS = r"""
    const m = f => {
      const s = doc.createElement('span');
      s.textContent = 'Wwmmiill Inspector 0123';
      s.style.cssText = 'position:fixed;font:20px ' + f;
      doc.documentElement.appendChild(s);
      const w = s.getBoundingClientRect().width;
      s.remove();
      return w;
    };
    return [m('serif'), m('"%UI%", serif'), m('"%MONO%", serif')];"""

INDICATOR_CHECK = r"""
    const i = doc.querySelector('.halo-tab-indicator');
    const s = doc.querySelector('.devtools-tab.selected');
    if (!i || !s) return 'missing';
    const a = i.getBoundingClientRect(), b = s.getBoundingClientRect();
    return Math.abs(a.left - b.left) < 3 && Math.abs(a.width - b.width) < 3 ? 'ok' : `${a.left},${a.width} vs ${b.left},${b.width}`;"""

COPY_CHECK = r"""
    const msg = doc.querySelector('.webconsole-output .message.error');
    if (!msg) return 'no message';
    msg.querySelector('.message-body').dispatchEvent(new win.PointerEvent('pointerover', {bubbles: true}));
    const btn = doc.querySelector('.halo-copy-btn');
    if (!btn || !btn.hasAttribute('halo-visible')) return 'button not shown';
    btn.click();
    return btn.textContent;"""

READ_CLIPBOARD = r"""
    const t = Cc['@mozilla.org/widget/transferable;1'].createInstance(Ci.nsITransferable);
    t.init(null); t.addDataFlavor('text/plain');
    Services.clipboard.getData(t, Ci.nsIClipboard.kGlobalClipboard);
    const o = {}; t.getTransferData('text/plain', o);
    return o.value.QueryInterface(Ci.nsISupportsString).data;"""

RIPPLE_CHECK = r"""
    const t = doc.querySelector('.devtools-tab:not(.selected)');
    const r = t.getBoundingClientRect();
    t.dispatchEvent(new win.PointerEvent('pointerdown', {bubbles: true, button: 0, clientX: r.left + 10, clientY: r.top + 10}));
    return !!doc.querySelector('.halo-ripple-host .halo-ripple');"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "test" / "screenshots"))
    ap.add_argument("--geckodriver", default=shutil.which("geckodriver.exe")
                    or str(Path(tempfile.gettempdir()) / "geckodriver-0.37.1.exe"))
    ap.add_argument("--firefox", default=r"C:\Program Files\Mozilla Firefox\firefox.exe")
    ap.add_argument("--preset", default=",".join(PRESET_SPECS),
                    help="comma-separated presets to test (default: all implemented)")
    ap.add_argument("--skip-shared", action="store_true", help="only run the per-preset checks")
    ap.add_argument("--keep", action="store_true", help="leave the browser open")
    ns = ap.parse_args()
    out = Path(ns.out)
    presets = [p for p in ns.preset.split(",") if p]

    tmp = Path(tempfile.mkdtemp(prefix="halo-profile-"))
    build_profile(tmp)
    page = (REPO / "test" / "fixture.html").as_uri()
    wd = WebDriver(ns.geckodriver, ns.firefox, tmp, TEST_PREFS)
    failures = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
        if not cond:
            failures.append(name)

    def css_var(match, name):
        return str(in_doc(wd, match, f"return win.getComputedStyle(doc.documentElement).getPropertyValue('{name}').trim()"))

    def hotkey(code):
        in_doc(wd, "toolbox", f"win.dispatchEvent(new win.KeyboardEvent('keydown', {{code: '{code}', ctrlKey: true, altKey: true, shiftKey: true, bubbles: true}}));")

    try:
        wd.js(LOAD_URL, page, asynchronous=True)
        loaded = wd.js("return typeof ChromeUtils.importESModule('chrome://halo/content/halo.sys.mjs').Halo")
        check("JS module booted at startup", loaded == "object", loaded)

        # ------------------------------------------------ per-preset pass
        for preset in presets:
            spec = PRESET_SPECS[preset]
            pdir = out / preset
            pdir.mkdir(parents=True, exist_ok=True)
            wd.js(SET_PREF, "halo.devtools.preset", preset)
            print(f"\n== preset: {preset}")
            for theme in ("dark", "light"):
                wd.js(SET_PREF, "devtools.theme", theme)
                for tool in TOOLS:
                    r = wd.js(OPEN_TOOL, tool, asynchronous=True)
                    check(f"[{preset}] open {tool} ({theme})", not str(r).startswith("ERR"), r)
                    if tool == "netmonitor":
                        wd.js("Services.wm.getMostRecentWindow('navigator:browser').gBrowser.selectedBrowser.reload()")
                        time.sleep(2.5)
                    wd.screenshot(pdir / f"{theme}-{tool}.png")
                if theme not in spec["modes"]:
                    scheme = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.documentElement).colorScheme")
                    check(f"[{preset}] forces its own scheme under devtools.theme={theme}",
                          spec["modes"][0] in str(scheme), scheme)

            wd.js(SET_PREF, "devtools.theme", spec["modes"][0])
            wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
            marker = css_var("toolbox", "--halo-preset")
            check(f"[{preset}] preset stylesheet active", marker == preset, marker)
            font = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily")
            check(f"[{preset}] UI font", spec["ui_font"] in str(font), font)
            mono = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.querySelector('.message-body, .jsterm-input-container') || doc.body).fontFamily")
            check(f"[{preset}] code font", spec["mono_font"] in str(mono), mono)
            widths = in_doc(wd, "toolbox", MEASURE_FONTS.replace("%UI%", spec["ui_font"]).replace("%MONO%", spec["mono_font"]))
            check(f"[{preset}] font files render",
                  isinstance(widths, list) and widths[0] not in widths[1:], widths)  # each font renders != the serif fallback
            for label, match, body, expect in spec["checks"]:
                # inspector-side checks need the inspector to be the selected tool
                tool = "inspector" if match in ("inspector/index", "markup") else "webconsole"
                wd.js(OPEN_TOOL, tool, asynchronous=True)
                got = in_doc(wd, match, body)
                check(f"[{preset}] {label}", expect in str(got), got)
            if spec.get("extra") == "daylight":
                wd.js(SET_PREF, "devtools.theme", "dark")
                wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
                canvas = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('#toolbox-container')).backgroundColor")
                check("[daylight] dark variant canvas", canvas == "rgb(13, 16, 22)", canvas)
                pill = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('.halo-tab-indicator')).backgroundColor + ' / ' + win.getComputedStyle(doc.querySelector('.devtools-tab.selected')).color")
                check("[daylight] dark variant: periwinkle pill, dark label", pill == "rgb(123, 151, 255) / rgb(11, 18, 48)", pill)
                wd.js(SET_PREF, "devtools.theme", "light")
                wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
            if spec.get("extra") == "terminal":
                wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
                wd.js(SET_PREF, "halo.devtools.terminal.scanlines", False)
                time.sleep(0.3)
                off = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.documentElement, '::after').content")
                check("[terminal] scanlines pref turns the overlay off", off in ("none", "normal"), off)
                wd.js(SET_PREF, "halo.devtools.terminal.scanlines", True)
                wd.js(SET_PREF, "halo.devtools.accent", "amber")
                time.sleep(0.3)
                tint = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.querySelector('.message:not(.warn):not(.error) .message-body')).color")
                check("[terminal] accent re-tints the phosphor text", "108, 255, 160" not in str(tint), tint)
                wd.screenshot(pdir / "amber-phosphor.png")
                wd.js(SET_PREF, "halo.devtools.accent", "")
            wd.js(SET_PREF, "halo.devtools.accent", "#22c55e")
            time.sleep(0.3)
            wd.screenshot(pdir / "accent-override.png")
            acc = css_var("webconsole", "--halo-accent")
            check(f"[{preset}] accent override applies", "22c55e" in acc.lower(), acc)
            wd.js(SET_PREF, "halo.devtools.accent", "")
            time.sleep(0.3)
            inline = in_doc(wd, "webconsole", "return doc.documentElement.style.getPropertyValue('--halo-accent')")
            check(f"[{preset}] empty accent falls back to the preset's own", inline == "", inline)

            # Compact density must still fit the preset's tab strip
            wd.js(SET_PREF, "halo.devtools.compact", True)
            time.sleep(0.4)
            fit = in_doc(wd, "toolbox", "const b = doc.querySelector('.devtools-tabbar').getBoundingClientRect(), t = doc.querySelector('.devtools-tab.selected').getBoundingClientRect(), c = doc.querySelector('.toolbox-tabs').getBoundingClientRect(); return (t.top >= b.top && t.bottom <= b.bottom && t.top >= c.top - 0.5 && t.bottom <= c.bottom + 0.5) ? 'fits ' + Math.round(b.height) : `strip ${b.top}-${b.bottom} capsule ${c.top}-${c.bottom} tab ${t.top}-${t.bottom}`")
            check(f"[{preset}] compact: tabs fit the strip", str(fit).startswith("fits"), fit)
            wd.screenshot(pdir / "compact.png")
            wd.js(SET_PREF, "halo.devtools.compact", False)

            # Inspector + split console (Esc) — a layout the per-tool pass never hits
            wd.js(OPEN_TOOL, "inspector", asynchronous=True)
            wd.js(SPLIT_CONSOLE, True, asynchronous=True)
            wd.screenshot(pdir / "split-console.png")
            gap = in_doc(wd, "toolbox", "const d = doc.querySelector('#toolbox-deck').getBoundingClientRect(), c = doc.querySelector('#toolbox-panel-webconsole').getBoundingClientRect(); return Math.round(c.top - d.bottom)")
            want = spec.get("split_gap")
            check(f"[{preset}] split console layout", isinstance(gap, (int, float)) and (want is None or gap == want), gap)
            wd.js(SPLIT_CONSOLE, False, asynchronous=True)

        if ns.skip_shared:
            raise SystemExit

        # ------------------------------------------------ shared behaviour
        print("\n== shared behaviour")
        out.mkdir(parents=True, exist_ok=True)
        wd.js(SET_PREF, "halo.devtools.preset", presets[0])
        wd.js(SET_PREF, "devtools.theme", "dark")
        wd.js(OPEN_TOOL, "webconsole", asynchronous=True)

        # live preset switching, and an unknown name falling back to the base tokens
        if len(PRESET_SPECS) > 1:
            other = next(p for p in PRESET_SPECS if p != presets[0])
            wd.js(SET_PREF, "halo.devtools.preset", other)
            time.sleep(0.4)
            got = css_var("toolbox", "--halo-preset")
            check("preset switches live", got == other, got)
        wd.js(SET_PREF, "halo.devtools.preset", "no-such-preset")
        time.sleep(0.4)
        bg = css_var("toolbox", "--halo-bg-1")
        mk = css_var("toolbox", "--halo-preset")
        check("unknown preset falls back to base tokens", mk == "" and "#0f1116" in bg, f"{mk!r} {bg}")
        wd.js(SET_PREF, "halo.devtools.preset", presets[0])
        time.sleep(0.4)

        ind = in_doc(wd, "toolbox", INDICATOR_CHECK)
        check("tab indicator tracks selected tab", ind == "ok", ind)

        # master switch: everything off -> stock look
        bg_on = css_var("toolbox", "--theme-body-background")
        wd.js(SET_PREF, "halo.devtools.enabled", False)
        time.sleep(0.6)
        bg_off = css_var("toolbox", "--theme-body-background")
        ind_off = in_doc(wd, "toolbox", "return !!doc.querySelector('.halo-tab-indicator')")
        check("master pref disables CSS live", bg_on != bg_off, f"{bg_on} -> {bg_off}")
        check("master pref removes JS mods live", ind_off is False)
        wd.screenshot(out / "disabled.png")
        wd.js(SET_PREF, "halo.devtools.enabled", True)
        time.sleep(0.6)

        # individual module toggle
        wd.js(SET_PREF, "halo.devtools.font", False)
        time.sleep(0.4)
        font_off = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily")
        check("font pref toggles live", "Halo " not in str(font_off), font_off)
        wd.js(SET_PREF, "halo.devtools.font", True)

        wd.js(SET_PREF, "halo.devtools.code-font-size", 14)
        time.sleep(0.4)
        cfs = css_var("webconsole", "--theme-code-font-size")
        check("code-font-size pref applies live", "14" in cfs, cfs)
        toast = in_doc(wd, "toolbox", "return doc.querySelector('.halo-toast')?.textContent || ''")
        check("pref change shows toast", "font" in toast.lower(), toast)
        wd.js(SET_PREF, "halo.devtools.code-font-size", 12)

        wd.js(SET_PREF, "halo.devtools.compact", True)
        time.sleep(0.4)
        wd.screenshot(out / "compact.png")
        h = in_doc(wd, "toolbox", "return doc.querySelector('.devtools-tabbar').getBoundingClientRect().height")
        check("compact mode shrinks tab bar", float(h) <= 30, h)
        wd.js(SET_PREF, "halo.devtools.compact", False)

        # --- JS mod interactions ------------------------------------------
        wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
        copy = in_doc(wd, "webconsole", COPY_CHECK)
        check("console copy button appears and copies", copy == "Copied", copy)
        clip = wd.js(READ_CLIPBOARD)
        check("clipboard holds the message text", "TypeError" in str(clip), str(clip)[:60])
        ripple = in_doc(wd, "toolbox", RIPPLE_CHECK)
        check("ripple spawns on pointerdown", ripple is True, ripple)

        before = wd.js("return Services.prefs.getBoolPref('halo.devtools.compact')")
        hotkey("KeyD")
        after = wd.js("return Services.prefs.getBoolPref('halo.devtools.compact')")
        check("hotkey Ctrl+Alt+Shift+D toggles compact", before != after, f"{before} -> {after}")
        wd.js(SET_PREF, "halo.devtools.compact", False)

        order = list(PRESET_SPECS)
        hotkey("KeyP")
        cycled = wd.js("return Services.prefs.getStringPref('halo.devtools.preset')")
        expected = order[(order.index(presets[0]) + 1) % len(order)]
        check("hotkey Ctrl+Alt+Shift+P cycles presets", cycled == expected, cycled)
        wd.js(SET_PREF, "halo.devtools.preset", presets[0])

        # JS master off: CSS stays, injected UI goes
        wd.js(SET_PREF, "halo.devtools.js.enabled", False)
        time.sleep(0.4)
        state = in_doc(wd, "toolbox", "return [!!doc.querySelector('.halo-tab-indicator'), doc.documentElement.style.getPropertyValue('--halo-code-font-size'), win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily].join('|')")
        check("js.enabled=false removes JS UI but keeps CSS", str(state).startswith("false||") and "Halo " in str(state), state)
        wd.screenshot(out / "css-only.png")
        wd.js(SET_PREF, "halo.devtools.js.enabled", True)
    except SystemExit:
        pass
    finally:
        try:
            errs = wd.js("return ChromeUtils.importESModule('chrome://halo/content/halo.sys.mjs').Halo.errors.join('\\n')")
            check("no Halo runtime errors", not errs, errs)
        except Exception as e:
            check("no Halo runtime errors", False, e)
        if not ns.keep:
            wd.quit()
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(failures)} failure(s). Screenshots in {out}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
