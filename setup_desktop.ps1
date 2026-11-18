param(
    [string]$PythonPath = 'python',
    [ValidateSet('reuse', 'cpu', 'cu128')][string]$TorchMode = 'reuse',
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskEnvironment = Join-Path $taskRoot '.venv'
$taskPython = Join-Path $taskEnvironment 'Scripts\python.exe'
$taskBase = (Get-Command $PythonPath -ErrorAction Stop).Source
$taskProbe = 'import json,sys,struct; print(json.dumps({"version":list(sys.version_info[:2]),"bits":struct.calcsize("P")*8,"prefix":sys.prefix,"base":sys.base_prefix}))'
$taskInfo = $taskProbe | & $taskBase -
if ($LASTEXITCODE -ne 0) { throw 'Selected Python cannot start.' }
$taskInfo = $taskInfo | ConvertFrom-Json
if ($taskInfo.version[0] -ne 3 -or $taskInfo.version[1] -lt 9 -or $taskInfo.version[1] -gt 11 -or $taskInfo.bits -ne 64) {
    throw 'Use 64-bit Python 3.9, 3.10 or 3.11. Other versions have not been checked.'
}
if ($TorchMode -eq 'reuse') {
    $taskTorchProbe = 'import torch,torchvision; assert torch.__version__.split("+")[0]=="2.8.0" and torchvision.__version__.split("+")[0]=="0.23.0", "Expected torch 2.8.0 and torchvision 0.23.0"'
    $taskTorchProbe | & $taskBase -
    if ($LASTEXITCODE -ne 0) { throw 'Reuse requires a working PyTorch 2.8.0 / torchvision 0.23.0 base. Select that python.exe or choose cpu/cu128 for a separate project environment.' }
}
$taskCommands = @()
if (-not (Test-Path -LiteralPath $taskPython)) {
    if (Test-Path -LiteralPath $taskEnvironment) { throw 'Existing .venv is incomplete. Rename it before retrying; this script will not delete it.' }
    $taskVenvArgs = @('-m', 'venv')
    if ($TorchMode -eq 'reuse') { $taskVenvArgs += '--system-site-packages' }
    $taskVenvArgs += $taskEnvironment
    $taskCommands += @{ Python = $taskBase; Arguments = $taskVenvArgs }
} elseif ($TorchMode -eq 'reuse') {
    $taskVenvConfig = Get-Content -LiteralPath (Join-Path $taskEnvironment 'pyvenv.cfg') -Raw
    if ($taskVenvConfig -notmatch '(?im)^include-system-site-packages\s*=\s*true\s*$') {
        throw 'Existing .venv does not inherit packages. Rename it before selecting reuse; no environment was changed.'
    }
    $taskTorchProbe | & $taskPython -
    if ($LASTEXITCODE -ne 0) { throw 'Existing project environment cannot reuse the expected PyTorch versions. No installation was performed.' }
}
if ($TorchMode -ne 'reuse') {
    $taskCommands += @{ Python = $taskPython; Arguments = @('-m', 'pip', 'install', "torch==2.8.0+$TorchMode", "torchvision==0.23.0+$TorchMode", '--index-url', "https://download.pytorch.org/whl/$TorchMode") }
}
$taskCommands += @{ Python = $taskPython; Arguments = @('-m', 'pip', 'install', '-r', (Join-Path $taskRoot 'requirements.txt')) }
$taskCommands += @{ Python = $taskPython; Arguments = @('-X', 'utf8', (Join-Path $taskRoot 'check_desktop.py')) }
foreach ($taskCommand in $taskCommands) {
    if ($PlanOnly) {
        [pscustomobject]@{ executable = $taskCommand.Python; arguments = $taskCommand.Arguments } | ConvertTo-Json -Compress
    } else {
        $taskStepPython = $taskCommand.Python
        $taskStepArguments = $taskCommand.Arguments
        & $taskStepPython @taskStepArguments
        if ($LASTEXITCODE -ne 0) { throw 'A setup step failed. Read its output, fix the cause, and rerun. Existing project files were not deleted.' }
    }
}
if ($PlanOnly) { Write-Output 'Plan only: no environment creation or installation performed.' }
else { Write-Output 'Setup complete. Run start_desktop.cmd.' }
