param(
    [string]$Source,
    [switch]$Check,
    [switch]$Headless,
    [switch]$Cpu,
    [switch]$Baseline,
    [switch]$TemporalOnly
)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv-pytorch\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Project Python environment is missing.' }
$taskArguments = @((Join-Path $PSScriptRoot 'main.py'))
if ($Check) { $taskArguments += '--check' }
elseif ($Source) { $taskArguments += @('--source', $Source) }
if ($Headless) { $taskArguments += '--headless' }
if ($Cpu) { $taskArguments += @('--device', 'cpu') }
if ($Baseline) { $taskArguments += '--baseline' }
if ($TemporalOnly) { $taskArguments += '--temporal-only' }
& $taskPython @taskArguments
exit $LASTEXITCODE

