<#
.SYNOPSIS
  Installs Halo DevTools into a Firefox profile.

.DESCRIPTION
  - copies chrome\halo into <profile>\chrome\halo
  - adds the Halo @import line to <profile>\chrome\userContent.css (once)
  - enables toolkit.legacyUserProfileCustomizations.stylesheets in user.js (once)
  It does NOT touch the Firefox install folder. For the JS mods you also need an
  autoconfig loader; see README.md ("JS mods").

.EXAMPLE
  .\install.ps1 -ProfilePath "$env:APPDATA\Mozilla\Firefox\Profiles\abcd1234.default-release"
#>
param(
  [Parameter(Mandatory = $true)][string]$ProfilePath
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path (Join-Path $ProfilePath 'prefs.js'))) {
  throw "'$ProfilePath' doesn't look like a Firefox profile (no prefs.js)."
}

$chrome = Join-Path $ProfilePath 'chrome'
New-Item -ItemType Directory -Force $chrome | Out-Null
$dest = Join-Path $chrome 'halo'
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Copy-Item -Recurse (Join-Path $PSScriptRoot 'chrome\halo') $dest
Write-Host "Copied theme to $dest"

$importLine = '@import url("halo/halo.css") (-moz-pref("halo.devtools.enabled"));'
$uc = Join-Path $chrome 'userContent.css'
$existing = if (Test-Path $uc) { Get-Content -Raw $uc } else { '' }
if ($existing -notmatch [regex]::Escape('halo/halo.css')) {
  # @import must precede every other rule, so prepend.
  Set-Content -NoNewline -Encoding utf8 $uc ("/* Halo DevTools */`n$importLine`n`n" + $existing)
  Write-Host "Added @import to $uc"
} else {
  Write-Host "userContent.css already imports Halo"
}

$userJs = Join-Path $ProfilePath 'user.js'
$pref = 'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);'
$uj = if (Test-Path $userJs) { Get-Content -Raw $userJs } else { '' }
if ($uj -notmatch 'legacyUserProfileCustomizations\.stylesheets') {
  Add-Content -Encoding utf8 $userJs "`n$pref"
  Write-Host "Enabled custom stylesheets in user.js"
}

# Seed halo.devtools.* prefs once into prefs.js (not user.js, which would
# re-apply on every start and undo about:config toggles). Needed for CSS-only
# installs; with the JS module loaded these become defaults anyway.
$prefsJs = Join-Path $ProfilePath 'prefs.js'
$lock = Join-Path $ProfilePath 'parent.lock'
$running = $false
if (Test-Path $lock) {
  try { [IO.File]::Open($lock, 'Open', 'ReadWrite', 'None').Close() } catch { $running = $true }
}
if ($running) {
  Write-Warning "Firefox is running with this profile, so halo.devtools.* prefs were not seeded. Close Firefox and re-run, or copy the halo lines from user.js once."
} else {
  $current = Get-Content -Raw $prefsJs
  $seed = Get-Content (Join-Path $PSScriptRoot 'user.js') |
    Where-Object { $_ -match '^user_pref\("(halo\.devtools\.[^"]+)"' -and $current -notmatch [regex]::Escape("`"$($Matches[1])`"") } |
    ForEach-Object { ($_ -replace '\s*//.*$', '') }
  if ($seed) {
    Add-Content -Encoding utf8 $prefsJs ($seed -join "`n")
    Write-Host "Seeded $(@($seed).Count) halo.devtools.* prefs into prefs.js"
  }
}

Write-Host "`nDone. Restart Firefox. Toggle everything under halo.devtools.* in about:config."
