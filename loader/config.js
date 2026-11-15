// Halo DevTools — standalone autoconfig loader. (The first line of this file must be a comment.)
// Copy to the Firefox install folder, next to firefox.exe. If you already have a
// config.js (fx-autoconfig, Sine, …) don't replace it: append the try-block below.
try {
  if (!Services.appinfo.inSafeMode) {
    const manifest = Services.dirsvc.get("UChrm", Ci.nsIFile); // <profile>/chrome
    manifest.append("halo");
    manifest.append("chrome.manifest");
    if (manifest.exists()) {
      Components.manager.QueryInterface(Ci.nsIComponentRegistrar).autoRegister(manifest);
      ChromeUtils.importESModule("chrome://halo/content/halo.sys.mjs");
    }
  }
} catch (e) {
  Cu.reportError(e);
}
