param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LauncherArguments
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$ToolsDir = Join-Path $ProjectRoot ".tools\uv"
$UvExe = Join-Path $ToolsDir "uv.exe"
$LogDir = Join-Path $ProjectRoot "workspace\logs"
$BootstrapLog = Join-Path $LogDir "bootstrap.log"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$UvVersion = "0.11.16"

New-Item -ItemType Directory -Force -Path $ToolsDir, $LogDir | Out-Null
Set-Content -Path $BootstrapLog -Value "" -Encoding UTF8

function Write-Stage([string]$Message) {
    Write-Host $Message
    Add-Content -Path $BootstrapLog -Value $Message -Encoding UTF8
}

function Stop-Launch([string]$Message) {
    Write-Stage ""
    Write-Stage "启动准备失败：$Message"
    Write-Stage "详细日志：$BootstrapLog"
    exit 1
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments *>> $BootstrapLog
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

try {
    Write-Stage "[1/4] 检查启动工具"
    if (-not (Test-Path $UvExe -PathType Leaf)) {
        Write-Stage "首次安装需要联网，通常需要数分钟，并可能下载数 GB 的科学计算依赖。"
        $Installer = Join-Path $ToolsDir "install.ps1"
        try {
            Invoke-WebRequest -UseBasicParsing `
                -Uri "https://releases.astral.sh/github/uv/releases/download/$UvVersion/uv-installer.ps1" `
                -OutFile $Installer
        }
        catch {
            Stop-Launch "首次安装需要联网。请检查网络连接后重新双击启动。"
        }
        $env:UV_UNMANAGED_INSTALL = $ToolsDir
        try {
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $Installer *>> $BootstrapLog
            if ($LASTEXITCODE -ne 0) {
                throw "uv installer failed"
            }
        }
        catch {
            Stop-Launch "启动工具安装失败。请检查网络连接后重新双击启动。"
        }
        finally {
            Remove-Item Env:\UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
        }
    }

    Write-Stage "[2/4] 准备 Python 3.11"
    try {
        Invoke-Logged $UvExe python install 3.11
    }
    catch {
        Stop-Launch "Python 3.11 准备失败。请检查网络连接后重新双击启动。"
    }

    $NeedsRebuild = $false
    if (Test-Path (Join-Path $ProjectRoot ".venv")) {
        if (-not (Test-Path $VenvPython -PathType Leaf)) {
            $NeedsRebuild = $true
        }
        else {
            & $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" *> $null
            if ($LASTEXITCODE -ne 0) {
                $NeedsRebuild = $true
            }
        }
    }

    if ($NeedsRebuild) {
        Write-Stage "检测到不完整或来自其他电脑的软件环境，正在自动修复..."
        try {
            Invoke-Logged $UvExe venv --clear --python 3.11 --managed-python (Join-Path $ProjectRoot ".venv")
        }
        catch {
            Stop-Launch "损坏的软件环境无法自动修复。请将日志发送给技术人员。"
        }
    }

    Write-Stage "[3/4] 安装或检查软件依赖"
    try {
        Invoke-Logged $UvExe sync --locked --python 3.11 --managed-python
    }
    catch {
        Stop-Launch "软件环境安装失败。无需手动处理 Python，请重新双击启动；如果仍然失败，请将日志发送给技术人员。"
    }

    & $VenvPython -c "import fastapi, uvicorn, ultralytics, PIL, numpy, scipy, matplotlib, celltrack" *>> $BootstrapLog
    if ($LASTEXITCODE -ne 0) {
        Write-Stage "依赖检查失败，正在重建软件环境..."
        try {
            Invoke-Logged $UvExe venv --clear --python 3.11 --managed-python (Join-Path $ProjectRoot ".venv")
            Invoke-Logged $UvExe sync --locked --python 3.11 --managed-python
        }
        catch {
            Stop-Launch "软件环境重建后仍无法安装依赖。请将日志发送给技术人员。"
        }
    }

    Write-Stage "[4/4] 启动 Cell Tracking Studio"
    & $VenvPython (Join-Path $ProjectRoot "scripts\launcher.py") @LauncherArguments
    exit $LASTEXITCODE
}
catch {
    Add-Content -Path $BootstrapLog -Value ($_ | Out-String) -Encoding UTF8
    Stop-Launch "发生未预期错误。请将日志发送给技术人员。"
}
