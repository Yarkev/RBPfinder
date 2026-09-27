<#
    RBPfinder installer (Windows).

    Installs the runtime into its OWN virtual environment. Not into the system Python:
    RBPfinder pins three dependencies, and sharing an interpreter with whatever else is
    on the machine is how one tool's upgrade silently changes another tool's results.

        .\install_windows.ps1                          online, default location
        .\install_windows.ps1 -Offline                 use the bundled wheels
        .\install_windows.ps1 -Prefix D:\tools\rbpfinder
        .\install_windows.ps1 -Skill project           also install the agent skill here
        .\install_windows.ps1 -Skill user              ... or for all your projects

    This script installs software. It downloads no sequence databases, submits nothing,
    and contacts EMBL-EBI never.
#>
[CmdletBinding()]
param(
    [string]$Prefix = "C:\rbpfinder",
    [switch]$Offline,
    [ValidateSet("none", "project", "user")]
    [string]$Skill = "none",
    [string]$SkillTarget = ".",
    [Alias("Python")]
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

function Say($msg) { Write-Host $msg }
function Fail($msg) { Write-Host "" ; Write-Host "FAILED: $msg" ; exit 1 }

Say "=== RBPfinder installer ==="
Say "  bundle   $root"
Say "  prefix   $Prefix"
Say "  mode     $(if ($Offline) { 'offline (bundled wheels)' } else { 'online (PyPI for dependencies)' })"

# --- 1. find a usable Python ------------------------------------------------------
# `python` on PATH may be the Windows Store stub, which exists and exits 9009 without
# ever running anything, so every candidate is PROBED by running it -- testing that the
# file exists proves nothing.
#
# The first version of this script gave up after `py` and `python`. On a machine with
# miniconda plus the Store stub -- an entirely ordinary combination, and the one this
# was first tested on -- that found nothing and told the user to install a Python they
# already had. The candidate list now covers the usual install locations, and -Python
# is the escape hatch for everything else.
$candidates = @()
if ($PythonExe) { $candidates += , @($PythonExe, @()) }
$candidates += , @("py", @("-3.13"))
$candidates += , @("py", @("-3"))
$candidates += , @("py", @())
$candidates += , @("python3", @())
$candidates += , @("python", @())
$searchPatterns = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
    (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
    (Join-Path $env:USERPROFILE "anaconda3\python.exe"),
    (Join-Path $env:ProgramFiles "Python3*\python.exe")
)
foreach ($pat in $searchPatterns) {
    foreach ($hit in (Get-ChildItem -Path $pat -ErrorAction SilentlyContinue |
                      Sort-Object FullName -Descending)) {
        $candidates += , @($hit.FullName, @())
    }
}

$python = $null
$tried = @()
foreach ($cand in $candidates) {
    $exe = $cand[0]
    # NOT $args -- that is a PowerShell automatic variable and assigning to it inside a
    # script is asking for a confusing failure somewhere else.
    $exeArgs = $cand[1]
    if (-not (Test-Path $exe -ErrorAction SilentlyContinue) -and
        -not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $tried += ("$exe " + ($exeArgs -join ' ')).Trim()
    try {
        $v = & $exe @exeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $parts = "$v".Trim().Split(".")
            if ($parts.Length -eq 2 -and
                ([int]$parts[0] -gt 3 -or
                 ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 9))) {
                $python = @{ exe = $exe; args = $exeArgs; version = "$v".Trim() }
                break
            }
        }
    } catch { }
}
if (-not $python) {
    Say ""
    Say "  probed, and none of these was a working Python 3.9 or newer:"
    foreach ($t in $tried) { Say "    $t" }
    Fail "no usable Python found. Install one from python.org (not the Microsoft Store build), or point this script at an existing interpreter with -Python <path to python.exe>"
}
Say "  python   $($python.exe) $($python.args -join ' ')  -> $($python.version)"

# --- 2. create the venv -----------------------------------------------------------
$venv = Join-Path $Prefix "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
$venvExe = Join-Path $venv "Scripts\rbpfinder.exe"

if (Test-Path $venvPy) {
    Say ""
    Say "  an environment already exists at $venv -- reusing it."
    Say "  (delete that directory yourself if you want a clean install; this script"
    Say "   will not remove it for you)"
} else {
    Say ""
    Say "  creating $venv"
    & $python.exe @($python.args) -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail "could not create the virtual environment." }
}

# --- 3. install -------------------------------------------------------------------
$wheelDir = Join-Path $root "runtime"
$wheel = Get-ChildItem -Path $wheelDir -Filter "rbpfinder-*.whl" -ErrorAction SilentlyContinue |
         Select-Object -First 1
if (-not $wheel) { Fail "no rbpfinder wheel found in $wheelDir" }
Say ""
Say "  installing $($wheel.Name)"

if ($Offline) {
    $wheels = Join-Path $wheelDir "wheels"
    if (-not (Test-Path $wheels)) {
        Fail "-Offline was given but $wheels does not exist. This bundle may have been built without the dependency wheels."
    }
    # Two --find-links: dependencies in wheels/, the runtime wheel one level up.
    & $venvPy -m pip install --no-index --find-links $wheels --find-links $wheelDir rbpfinder --quiet
} else {
    & $venvPy -m pip install $wheel.FullName --quiet
}
if ($LASTEXITCODE -ne 0) {
    Fail "pip install failed. If this machine has no internet access, re-run with -Offline."
}

# --- 4. install the agent skill, if asked ----------------------------------------
if ($Skill -ne "none") {
    $src = Join-Path $root "skill\rbpfinder"
    if ($Skill -eq "user") {
        $dst = Join-Path $env:USERPROFILE ".claude\skills\rbpfinder"
    } else {
        $dst = Join-Path (Resolve-Path $SkillTarget) ".claude\skills\rbpfinder"
    }
    Say ""
    Say "  installing the agent skill -> $dst"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path (Join-Path $src "*") -Destination $dst -Recurse -Force
    $agentsDst = if ($Skill -eq "user") { $null } else { Resolve-Path $SkillTarget }
    if ($agentsDst) {
        foreach ($f in @("CLAUDE.md", "AGENTS.md")) {
            $target = Join-Path $agentsDst $f
            if (Test-Path $target) {
                Say "  $f already exists here -- left alone, not overwritten"
            } else {
                Copy-Item (Join-Path $root "agents\$f") $target
                Say "  wrote $f"
            }
        }
    }
}

Say ""
Say "=== installed ==="
Say "  rbpfinder    $venvExe"
Say ""
Say "Add it to PATH for this session with:"
Say "    `$env:Path = '$venv\Scripts;' + `$env:Path"
Say ""
Say "Verify with:"
Say "    .\verify_install.ps1 -Prefix $Prefix"
Say ""
Say "Nothing has been submitted anywhere and no sequence database was downloaded."
Say "Remote Stage 2 asks for authorization the first time you use it."
