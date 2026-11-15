"""Real-browser test harness for Halo DevTools.

Builds a throwaway Firefox profile from this repo, launches Firefox through
geckodriver, switches WebDriver into chrome context, opens DevTools panels and
takes screenshots / evaluates privileged JS against the live devtools DOM.

Stdlib only.  Usage:
    python -I test/harness.py [--out DIR] [--geckodriver PATH] [--firefox PATH]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "test" / "screenshots"))
    ap.add_argument("--geckodriver", default=shutil.which("geckodriver.exe")
                    or str(Path(tempfile.gettempdir()) / "geckodriver-0.37.1.exe"))
    ap.add_argument("--firefox", default=r"C:\Program Files\Mozilla Firefox\firefox.exe")
    ap.add_argument("--keep", action="store_true", help="leave the browser open")
    ns = ap.parse_args()
    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="halo-profile-"))
    build_profile(tmp)
    page = (REPO / "test" / "fixture.html").as_uri()
    wd = WebDriver(ns.geckodriver, ns.firefox, tmp, TEST_PREFS)
    failures = []

    def check(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
        if not cond:
            failures.append(name)

    try:
        wd.js(LOAD_URL, page, asynchronous=True)
        loaded = wd.js("return typeof ChromeUtils.importESModule('chrome://halo/content/halo.sys.mjs').Halo")
        check("JS module booted at startup", loaded == "object", loaded)

        for theme in ("dark", "light"):
            wd.js(SET_PREF, "devtools.theme", theme)
            for tool in ("webconsole", "inspector", "jsdebugger", "netmonitor", "styleeditor"):
                r = wd.js(OPEN_TOOL, tool, asynchronous=True)
                check(f"open {tool} ({theme})", not str(r).startswith("ERR"), r)
                if tool == "netmonitor":
                    wd.js("Services.wm.getMostRecentWindow('navigator:browser').gBrowser.selectedBrowser.reload()")
                    time.sleep(2.5)
                wd.screenshot(out / f"{theme}-{tool}.png")

        # --- assertions against the live DOM -------------------------------
        wd.js(SET_PREF, "devtools.theme", "dark")
        wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
        font = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily")
        check("UI font is Geist", "Geist" in str(font), font)
        mono = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.querySelector('.message-body, .jsterm-input-container') || doc.body).fontFamily")
        check("code font is Geist Mono", "Geist Mono" in str(mono), mono)
        # user-sheet @font-face rules are not exposed via document.fonts, so
        # compare rendered widths against a fallback that "Halo Geist" can't be.
        widths = in_doc(wd, "toolbox", r"""
            const m = f => {
              const s = doc.createElement('span');
              s.textContent = 'Wwmmiill Inspector 0123';
              s.style.cssText = 'position:fixed;font:20px ' + f;
              doc.documentElement.appendChild(s);
              const w = s.getBoundingClientRect().width;
              s.remove();
              return w;
            };
            return [m('serif'), m('"Halo Geist", serif'), m('"Halo Geist Mono", serif')];""")
        check("font files actually load", isinstance(widths, list) and widths[0] not in widths[1:] and widths[1] != widths[2], widths)
        ind = in_doc(wd, "toolbox", "const i = doc.querySelector('.halo-tab-indicator'); const s = doc.querySelector('.devtools-tab.selected'); if (!i || !s) return 'missing'; const a = i.getBoundingClientRect(), b = s.getBoundingClientRect(); return Math.abs(a.left - b.left) < 3 && Math.abs(a.width - b.width) < 3 ? 'ok' : `${a.left},${a.width} vs ${b.left},${b.width}`")
        check("tab indicator tracks selected tab", ind == "ok", ind)

        # master switch: everything off -> stock look
        bg_on = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.documentElement).getPropertyValue('--theme-body-background')")
        wd.js(SET_PREF, "halo.devtools.enabled", False)
        time.sleep(0.6)
        bg_off = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.documentElement).getPropertyValue('--theme-body-background')")
        ind_off = in_doc(wd, "toolbox", "return !!doc.querySelector('.halo-tab-indicator')")
        check("master pref disables CSS live", bg_on != bg_off, f"{bg_on} -> {bg_off}")
        check("master pref removes JS mods live", ind_off is False)
        wd.screenshot(out / "dark-disabled.png")
        wd.js(SET_PREF, "halo.devtools.enabled", True)
        time.sleep(0.6)

        # individual module toggle
        wd.js(SET_PREF, "halo.devtools.font", False)
        time.sleep(0.4)
        font_off = in_doc(wd, "toolbox", "return win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily")
        check("font pref toggles live", "Geist" not in str(font_off).replace("Geist Mono", ""), font_off)
        wd.js(SET_PREF, "halo.devtools.font", True)

        # JS-driven accent + sizes
        wd.js(SET_PREF, "halo.devtools.accent", "#22c55e")
        wd.js(SET_PREF, "halo.devtools.code-font-size", 14)
        time.sleep(0.4)
        acc = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.documentElement).getPropertyValue('--halo-accent')")
        check("accent pref applies live", "22c55e" in str(acc).lower() or "34, 197, 94" in str(acc), acc)
        cfs = in_doc(wd, "webconsole", "return win.getComputedStyle(doc.documentElement).getPropertyValue('--theme-code-font-size')")
        check("code-font-size pref applies live", "14" in str(cfs), cfs)
        toast = in_doc(wd, "toolbox", "return doc.querySelector('.halo-toast')?.textContent || ''")
        check("pref change shows toast", "font" in toast.lower() or "accent" in toast.lower(), toast)
        time.sleep(0.5)
        wd.screenshot(out / "dark-accent-green.png")

        wd.js(SET_PREF, "halo.devtools.compact", True)
        time.sleep(0.4)
        wd.screenshot(out / "dark-compact.png")
        h = in_doc(wd, "toolbox", "return doc.querySelector('.devtools-tabbar').getBoundingClientRect().height")
        check("compact mode shrinks tab bar", float(h) <= 30, h)

        wd.js(SET_PREF, "halo.devtools.compact", False)

        # --- JS mod interactions ------------------------------------------
        wd.js(OPEN_TOOL, "webconsole", asynchronous=True)
        copy = in_doc(wd, "webconsole", """
            const msg = doc.querySelector('.webconsole-output .message.error');
            if (!msg) return 'no message';
            msg.querySelector('.message-body').dispatchEvent(new win.PointerEvent('pointerover', {bubbles: true}));
            const btn = doc.querySelector('.halo-copy-btn');
            if (!btn || !btn.hasAttribute('halo-visible')) return 'button not shown';
            btn.click();
            return btn.textContent;""")
        check("console copy button appears and copies", copy == "Copied", copy)
        clip = wd.js("""
            const t = Cc['@mozilla.org/widget/transferable;1'].createInstance(Ci.nsITransferable);
            t.init(null); t.addDataFlavor('text/plain');
            Services.clipboard.getData(t, Ci.nsIClipboard.kGlobalClipboard);
            const o = {}; t.getTransferData('text/plain', o);
            return o.value.QueryInterface(Ci.nsISupportsString).data;""")
        check("clipboard holds the message text", "TypeError" in str(clip), str(clip)[:60])

        ripple = in_doc(wd, "toolbox", """
            const t = doc.querySelector('.devtools-tab:not(.selected)');
            const r = t.getBoundingClientRect();
            t.dispatchEvent(new win.PointerEvent('pointerdown', {bubbles: true, button: 0, clientX: r.left + 10, clientY: r.top + 10}));
            return !!doc.querySelector('.halo-ripple-host .halo-ripple');""")
        check("ripple spawns on pointerdown", ripple is True, ripple)

        before = wd.js("return Services.prefs.getBoolPref('halo.devtools.compact')")
        in_doc(wd, "toolbox", "win.dispatchEvent(new win.KeyboardEvent('keydown', {code: 'KeyD', key: 'D', ctrlKey: true, altKey: true, shiftKey: true, bubbles: true}));")
        after = wd.js("return Services.prefs.getBoolPref('halo.devtools.compact')")
        check("hotkey Ctrl+Alt+Shift+D toggles compact", before != after, f"{before} -> {after}")
        wd.js(SET_PREF, "halo.devtools.compact", False)

        # JS master off: CSS stays, injected UI goes, CSS fallbacks take over
        wd.js(SET_PREF, "halo.devtools.js.enabled", False)
        time.sleep(0.4)
        state = in_doc(wd, "toolbox", "return [!!doc.querySelector('.halo-tab-indicator'), doc.documentElement.style.getPropertyValue('--halo-accent'), win.getComputedStyle(doc.querySelector('.devtools-tab')).fontFamily].join('|')")
        check("js.enabled=false removes JS UI but keeps CSS", str(state).startswith("false||") and "Geist" in str(state), state)
        wd.screenshot(out / "dark-css-only.png")
        wd.js(SET_PREF, "halo.devtools.js.enabled", True)

        errs = wd.js("return ChromeUtils.importESModule('chrome://halo/content/halo.sys.mjs').Halo.errors.join('\\n')")
        check("no Halo runtime errors", not errs, errs)
    finally:
        if not ns.keep:
            wd.quit()
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(failures)} failure(s). Screenshots in {out}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
