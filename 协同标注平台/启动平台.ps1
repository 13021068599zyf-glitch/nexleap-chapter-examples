$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$dataDir = Join-Path $PSScriptRoot 'data'
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$logPath = Join-Path $dataDir '启动日志.txt'
try {
    Start-Transcript -Path $logPath -Append | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'server.py'))) {
        throw '缺少 server.py。请解压完整文件夹后，再从“协同标注平台”目录启动。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '..\抽帧图'))) {
        throw '找不到抽帧图目录。请保留整个分享包的原始文件夹结构。'
    }
    $runner = $null
    $runnerArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $runner = 'py'
        $runnerArgs = @('-3', '-u', 'server.py')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $runner = 'python'
        $runnerArgs = @('-u', 'server.py')
    } else {
        throw '未找到 Python。请安装 Python 3.10 或更新版本，并勾选“Add Python to PATH”。'
    }
    Write-Host '正在启动图片整理平台。请保持此窗口打开。'
    Write-Host '启动完成后访问 http://127.0.0.1:8766/'
    & $runner @runnerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "平台启动失败（退出码 $LASTEXITCODE）。请将本窗口报错或 data\启动日志.txt 发给负责人。"
    }
} catch {
    Write-Host ''
    Write-Host ('启动失败：' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('启动日志：' + $logPath)
} finally {
    try { Stop-Transcript | Out-Null } catch {}
    Write-Host ''
    Write-Host '窗口会保持打开，方便查看错误。完成后可直接关闭。'
}