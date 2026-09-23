$ErrorActionPreference = "Stop"
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$appFile = Join-Path $appRoot "app.py"
$quotedApp = '"' + $appFile + '"'
$bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"

if (Test-Path -LiteralPath $bundledPython) {
    Start-Process -FilePath $bundledPython -ArgumentList $quotedApp -WorkingDirectory $appRoot
    exit 0
}

$pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue
if ($pyw) {
    Start-Process -FilePath $pyw.Source -ArgumentList @("-3", $quotedApp) -WorkingDirectory $appRoot
    exit 0
}

$pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($pythonw) {
    Start-Process -FilePath $pythonw.Source -ArgumentList $quotedApp -WorkingDirectory $appRoot
    exit 0
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Python 3.11 or newer was not found. See the user guide.",
    "Fire Audit AR",
    "OK",
    "Error"
) | Out-Null
exit 1
