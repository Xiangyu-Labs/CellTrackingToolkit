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
    Write-Stage "Startup preparation failed: $Message"
    Write-Stage "Detailed log: $BootstrapLog"
    exit 1
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )

    # Windows PowerShell can promote a native program's stderr output to a
    # terminating error when ErrorActionPreference is Stop. Tools such as uv
    # write normal download progress to stderr, so let the process finish and
    # use its exit code to determine whether it failed.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments *>> $BootstrapLog
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }

    if ($ExitCode -ne 0) {
        throw "Command failed with exit code $ExitCode"
    }
}

try {
    Write-Stage "[1/6] Checking startup tools"
    if (-not (Test-Path $UvExe -PathType Leaf)) {
        Write-Stage "An internet connection is required for the first installation. The process usually takes several minutes and may download several GB of scientific computing dependencies."
        $Installer = Join-Path $ToolsDir "install.ps1"
        try {
            Invoke-WebRequest -UseBasicParsing `
                -Uri "https://releases.astral.sh/github/uv/releases/download/$UvVersion/uv-installer.ps1" `
                -OutFile $Installer
        }
        catch {
            Stop-Launch "An internet connection is required for the first installation. Check your connection and start the application again."
        }
        $env:UV_UNMANAGED_INSTALL = $ToolsDir
        try {
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $Installer *>> $BootstrapLog
            if ($LASTEXITCODE -ne 0) {
                throw "uv installer failed"
            }
        }
        catch {
            Stop-Launch "The startup tool could not be installed. Check your connection and start the application again."
        }
        finally {
            Remove-Item Env:\UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
        }
    }

    Write-Stage "[2/6] Preparing Python 3.11"
    try {
        Invoke-Logged $UvExe --verbose python install 3.11
    }
    catch {
        Stop-Launch "Python 3.11 could not be prepared. Check your connection and start the application again."
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
        Write-Stage "An incomplete or transferred application environment was detected. Repairing it automatically..."
        try {
            Invoke-Logged $UvExe venv --clear --python 3.11 --managed-python (Join-Path $ProjectRoot ".venv")
        }
        catch {
            Stop-Launch "The damaged application environment could not be repaired automatically. Send the log to technical support."
        }
    }

    if ($env:CELLTRACK_UPDATE_RESTARTED -ne "1") {
        Write-Stage "[3/6] Checking for application updates"
        & $UvExe run --python 3.11 --no-project `
            (Join-Path $ProjectRoot "scripts\updater.py") --app-only *>> $BootstrapLog
        $UpdateStatus = $LASTEXITCODE
        if ($UpdateStatus -eq 10) {
            Write-Stage "Restarting with the updated application..."
            $env:CELLTRACK_UPDATE_RESTARTED = "1"
            & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
                -File (Join-Path $ProjectRoot "scripts\bootstrap.ps1") @LauncherArguments
            exit $LASTEXITCODE
        }
        elseif ($UpdateStatus -ne 0) {
            Write-Stage "Automatic update check failed; continuing with this version."
        }
    }
    else {
        Write-Stage "[3/6] Application update applied"
    }

    Write-Stage "[4/6] Installing or checking application dependencies"
    try {
        Invoke-Logged $UvExe sync --locked --python 3.11 --managed-python
    }
    catch {
        Stop-Launch "The application environment could not be installed. Do not modify Python manually; start the application again, and send the log to technical support if it still fails."
    }

    & $VenvPython -c "import fastapi, uvicorn, ultralytics, PIL, numpy, scipy, matplotlib, celltrack" *>> $BootstrapLog
    if ($LASTEXITCODE -ne 0) {
        Write-Stage "The dependency check failed. Rebuilding the application environment..."
        try {
            Invoke-Logged $UvExe venv --clear --python 3.11 --managed-python (Join-Path $ProjectRoot ".venv")
            Invoke-Logged $UvExe sync --locked --python 3.11 --managed-python
        }
        catch {
            Stop-Launch "Dependencies still could not be installed after rebuilding the application environment. Send the log to technical support."
        }
    }

    Write-Stage "[5/6] Checking the segmentation model"
    try {
        Invoke-Logged $VenvPython (Join-Path $ProjectRoot "scripts\updater.py") --model-only
    }
    catch {
        Stop-Launch "The segmentation model could not be downloaded. Check the internet connection and start the application again."
    }

    Write-Stage "[6/6] Starting Cell Tracking Studio"
    & $VenvPython (Join-Path $ProjectRoot "scripts\launcher.py") @LauncherArguments
    exit $LASTEXITCODE
}
catch {
    Add-Content -Path $BootstrapLog -Value ($_ | Out-String) -Encoding UTF8
    Stop-Launch "An unexpected error occurred. Send the log to technical support."
}
