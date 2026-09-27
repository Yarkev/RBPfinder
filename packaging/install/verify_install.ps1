<#
    Verify an RBPfinder installation.

    Reports only. It runs no analysis, downloads nothing, submits nothing.

        .\verify_install.ps1
        .\verify_install.ps1 -Prefix D:\tools\rbpfinder
#>
[CmdletBinding()]
param([string]$Prefix = "C:\rbpfinder")

$ErrorActionPreference = "Continue"
$venvPy = Join-Path $Prefix "venv\Scripts\python.exe"
$venvExe = Join-Path $Prefix "venv\Scripts\rbpfinder.exe"

$script:results = @()
function Check($name, $ok, $detail) {
    $script:results += [pscustomobject]@{ Name = $name; Ok = [bool]$ok }
    $mark = if ($ok) { "OK  " } else { "FAIL" }
    Write-Host ("  {0} {1,-46} {2}" -f $mark, $name, $detail)
}

Write-Host "=== verifying RBPfinder at $Prefix ==="

Check "virtual environment exists" (Test-Path $venvPy) $venvPy
if (-not (Test-Path $venvPy)) {
    Write-Host ""
    Write-Host "VERIFY_INSTALL=FAIL  (nothing is installed here)"
    exit 1
}

Check "rbpfinder command exists" (Test-Path $venvExe) $venvExe

# Import from a DIFFERENT working directory on purpose. Importing while standing in the
# source tree proves the source tree works, not that the installation does.
$tmp = Join-Path $env:TEMP ("rbpfverify_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
Push-Location $tmp
try {
    $ver = & $venvPy -c "import rbpfinder, sys; print(getattr(rbpfinder,'__version__','?'))" 2>&1
    Check "package imports outside its source tree" ($LASTEXITCODE -eq 0) "$ver"

    # The packaged rules are the scientific source of truth. An install that resolves the
    # command but not the rules would fail later, in the middle of an analysis, looking
    # like a science problem instead of an install problem.
    $rules = & $venvPy -c @"
import importlib.resources as r
p = r.files('rbpfinder') / 'rules' / 'decision_rules.yaml'
print(p.is_file() and p.stat().st_size or 0)
"@ 2>&1
    Check "packaged decision_rules.yaml resolves" (($LASTEXITCODE -eq 0) -and ([int64]"$ver".Length -ge 0) -and ("$rules".Trim() -match '^\d+$') -and ([int64]("$rules".Trim()) -gt 10000)) "$($rules.ToString().Trim()) bytes"

    $schema = & $venvPy -c @"
import importlib.resources as r
p = r.files('rbpfinder') / 'rules' / 'evidence_schema.json'
print(p.is_file() and p.stat().st_size or 0)
"@ 2>&1
    Check "packaged evidence_schema.json resolves" (("$schema".Trim() -match '^\d+$') -and ([int64]("$schema".Trim()) -gt 100)) "$($schema.ToString().Trim()) bytes"

    foreach ($mod in @("yaml", "jsonschema", "Bio")) {
        & $venvPy -c "import $mod" 2>&1 | Out-Null
        Check "dependency importable: $mod" ($LASTEXITCODE -eq 0) ""
    }

    # doctor reports; it must not change anything. Exit 0 or 3 are both legitimate --
    # 3 means an OPTIONAL capability is missing, which is a normal remote-profile install.
    $doctor = & $venvExe doctor 2>&1
    $dcode = $LASTEXITCODE
    Check "doctor runs" ($dcode -in @(0, 3)) "exit $dcode"
    $blast = ($doctor | Select-String -Pattern "blastp" -Quiet)
    if ($blast) {
        Write-Host "       (doctor mentions blastp -- see its output for whether a local"
        Write-Host "        database is configured; the remote profile does not need one)"
    }

    $help = & $venvExe --help 2>&1
    Check "--help documents the EBI contact email" `
        (($help | Out-String) -match "results are NOT sent to it") ""
    Check "--help documents --allow-remote" `
        (($help | Out-String) -match "--allow-remote") ""
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

$failed = @($script:results | Where-Object { -not $_.Ok })
Write-Host ""
Write-Host ("VERIFY_INSTALL={0}  ({1}/{2})" -f `
    $(if ($failed.Count -eq 0) { "PASS" } else { "FAIL" }), `
    ($script:results.Count - $failed.Count), $script:results.Count)
if ($failed.Count -gt 0) { exit 1 }
