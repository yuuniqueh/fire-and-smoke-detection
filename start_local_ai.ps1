$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$portableRoot = if ((Split-Path -Leaf $taskRoot) -eq '_internal') { Split-Path -Parent $taskRoot } else { $taskRoot }
$portableAi = Join-Path $portableRoot 'ai-component'
$portablePython = Join-Path $portableAi 'runtime\python.exe'
$isPortableAi = Test-Path -LiteralPath $portablePython
$qwenReceiptPath = Join-Path $taskRoot '.cache\qwen-local-runtime.json'
if ($isPortableAi) { $qwenReceiptPath = Join-Path $portableAi 'config.json' }
if (-not (Test-Path -LiteralPath $qwenReceiptPath)) {
    throw '此电脑尚未配置本地视觉模型。基础录像检测仍可使用。'
}
$qwen = Get-Content -LiteralPath $qwenReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
$expectedPrecision = if ($qwen.language_precision) { $qwen.language_precision } else { 'nf4' }
$allowedRoot = [IO.Path]::GetFullPath($(if ($isPortableAi) { $portableAi } else { Join-Path $taskRoot '.cache' })) + [IO.Path]::DirectorySeparatorChar
$qwenPython = [IO.Path]::GetFullPath($(if ($isPortableAi) { $portablePython } else { $qwen.python }))
if (-not $qwenPython.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $qwenPython)) {
    throw '本地AI运行路径无效或文件缺失。'
}
function Test-QwenReady {
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 1
        return $version.version -eq 'fireguard-qwen-local-v5' -and $version.ready -eq $true -and $version.language_precision -eq $expectedPrecision
    } catch { return $false }
}
if (Test-QwenReady) {
    Write-Output 'Qwen本机场景复核已就绪，画面不离开此电脑。'
    return
}
$localLog = if ($isPortableAi) { Join-Path $portableRoot 'data\outputs\local-ai' } else { Join-Path $taskRoot 'outputs\local-ai' }
New-Item -ItemType Directory -Path $localLog -Force | Out-Null
$serviceReceipt = Join-Path $localLog 'service.json'
if (Test-Path -LiteralPath $serviceReceipt) {
    $previous = Get-Content -LiteralPath $serviceReceipt -Raw -Encoding UTF8 | ConvertFrom-Json
    $existing = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $previous.executable -and $existing.Path.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $existing.Id /T /F | Out-Null
    }
}
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$qwenArguments = if ($isPortableAi) { @('-I','-B','-X','utf8','-u','-m','core.local_scene_server','--config',('"' + $qwenReceiptPath + '"')) } else { @('-X','utf8','-X','faulthandler','-m','core.local_scene_server') }
$helper = Start-Process -FilePath $qwenPython -ArgumentList $qwenArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $localLog 'qwen-output.log') -RedirectStandardError (Join-Path $localLog 'qwen-error.log')
@{pid=$helper.Id;executable=$qwenPython;endpoint='http://127.0.0.1:11435'} | ConvertTo-Json | Set-Content -LiteralPath $serviceReceipt -Encoding UTF8
$qwenReady = $false
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-QwenReady) { $qwenReady = $true; break }
    if ($helper.HasExited) { break }
}
if (-not $qwenReady) { throw '本地视觉模型尚未就绪，请查看outputs/local-ai中的运行记录。' }
Write-Output 'Qwen本机场景复核已就绪，画面不离开此电脑。'
