#
# Run the single "app menu quit" smoke test on Windows.
#
# Usage: run-open.ps1 [extra pytest args, e.g. -x -k something]

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$Py = "python"
if (Test-Path $VenvPython) {
  $Py = $VenvPython
}

Push-Location $RootDir
& $Py -m pytest tests/functional/test_app_menu_quit.py --no-cov @args
$code = $LASTEXITCODE
Pop-Location
exit $code
