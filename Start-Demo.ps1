$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$demoPython = Get-Command python -ErrorAction SilentlyContinue
if ($demoPython) {
    & $demoPython.Source -m mosquito demo
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython -m mosquito demo
    } else {
        throw 'Install Python 3.10 or newer, then run: python -m mosquito demo'
    }
}
