#
# Run the RiverCrossing app from this checkout on Windows -- the
# PowerShell mirror of scripts/run.sh. The one override,
# RIVERCROSSING_DB_PATH, is cleared here to keep parity with an
# installed bundle.
#
# Usage: run.ps1 [arguments forwarded to the app]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"

function Write-Err {
  param([string]$Message)
  $stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"
  [Console]::Error.WriteLine("[$stamp]: $Message")
}

function Ensure-Venv {
  if (Test-Path $VenvPython) {
    return $true
  }

  if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Err "no .venv and uv is not installed. Create one with:
  python -m venv .venv && .venv/bin/pip install -e '.[dev]'"
    return $false
  }

  Write-Host "creating .venv (first run) ..."
  & uv venv .venv
  if ($LASTEXITCODE -ne 0) {
    Write-Err "uv venv failed"
    return $false
  }
  & uv pip install --python $VenvPython -e ".[dev]"
  if ($LASTEXITCODE -ne 0) {
    Write-Err "installing rivercrossing failed"
    return $false
  }

  return $true
}

Push-Location $RootDir
if (-not (Ensure-Venv)) {
  Pop-Location
  exit 1
}

# Parity with an installed bundle, which never sets this variable:
# use the per-user default database, not an inherited test override.
Remove-Item Env:\RIVERCROSSING_DB_PATH -ErrorAction SilentlyContinue

& $VenvPython -m rivercrossing @args
$code = $LASTEXITCODE
Pop-Location
exit $code
