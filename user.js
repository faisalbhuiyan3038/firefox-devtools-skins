// Halo DevTools — about:config switches.
// Only needed for a CSS-only install (no JS loader). With the JS module loaded,
// every halo.devtools.* pref is registered as a *default* and shows up in
// about:config automatically — then you only need the first pref below.
//
// NOTE: Firefox re-applies user.js on every startup, so values here override
// whatever you toggled in about:config. After the first run, either delete the
// halo lines from user.js or change values here instead.

// Required for userChrome/userContent.css to load at all:
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);

user_pref("halo.devtools.enabled", true);                  // Halo theme
user_pref("halo.devtools.preset", "halo");                 // Theme preset: halo | glass | ember | terminal | daylight
user_pref("halo.devtools.palette", true);                  // Colour palette
user_pref("halo.devtools.font", true);                     // Theme UI font
user_pref("halo.devtools.mono-font", true);                // Theme code font
user_pref("halo.devtools.tabs", true);                     // Pill tabs
user_pref("halo.devtools.toolbars", true);                 // Toolbars & inputs
user_pref("halo.devtools.console", true);                  // Console styling
user_pref("halo.devtools.inspector", true);                // Inspector styling
user_pref("halo.devtools.debugger", true);                 // Debugger / editor styling
user_pref("halo.devtools.netmonitor", true);               // Network styling
user_pref("halo.devtools.scrollbars", true);               // Slim scrollbars
user_pref("halo.devtools.motion", true);                   // Motion
user_pref("halo.devtools.glow", true);                     // Accent glow
user_pref("halo.devtools.compact", false);                 // Compact density
user_pref("halo.devtools.terminal.scanlines", true);      // Terminal preset: scanline overlay

user_pref("halo.devtools.js.enabled", true);               // JS mods
user_pref("halo.devtools.accent", "");                     // Accent ("" = preset's own)
user_pref("halo.devtools.ui-font-size", 12);               // UI font size
user_pref("halo.devtools.code-font-size", 12);             // Code font size
user_pref("halo.devtools.ui-font-family", "");             // UI font family
user_pref("halo.devtools.mono-font-family", "");           // Code font family
user_pref("halo.devtools.js.tab-indicator", true);         // Sliding tab indicator
user_pref("halo.devtools.js.ripple", true);                // Click ripple
user_pref("halo.devtools.js.tab-scrollbar", true);         // Slim sidebar-tab scrollbar
user_pref("halo.devtools.js.console-copy", true);          // Console copy button
user_pref("halo.devtools.js.toasts", true);                // Pref toasts
user_pref("halo.devtools.js.hotkeys", true);               // Hotkeys
