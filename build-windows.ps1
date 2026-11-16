param(
    [string]$PythonExe = "$PSScriptRoot/work/package-venv/Scripts/python.exe"
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Create the isolated build environment first (see README.md).'
}
& $PythonExe "$PSScriptRoot/packaging/build_windows.py"
if ($LASTEXITCODE -ne 0) { throw 'Windows portable package build or smoke check failed' }
