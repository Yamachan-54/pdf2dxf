#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\pdf2dxf"),
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PythonVersion = "3.13.15"
$PythonArchiveUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonArchiveSha256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
$PyMuPDFVersion = "1.28.2"
$PackageSource = Join-Path $PSScriptRoot "pdf2dxf"
$StageDir = "$InstallDir.installing-$PID"
$BackupDir = "$InstallDir.backup-$PID"
$DownloadDir = Join-Path ([System.IO.Path]::GetTempPath()) "pdf2dxf-install-$PID"
$Installed = $false

function Get-FileChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256
    )

    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash
    if ($Actual -ine $Sha256) {
        throw "Downloaded file hash mismatch: $Uri"
    }
}

function Add-UserPathEntry {
    param([Parameter(Mandatory = $true)][string]$Directory)

    $Current = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @()
    if (-not [string]::IsNullOrWhiteSpace($Current)) {
        $Entries = @($Current.Split(";") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    $AlreadyPresent = $Entries | Where-Object {
        $_.TrimEnd("\") -ieq $Directory.TrimEnd("\")
    }
    if (-not $AlreadyPresent) {
        $NewValue = (@($Entries) + $Directory) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $NewValue, "User")
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "This installer must be run on Windows."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Only 64-bit Windows 10/11 is supported."
}
if (-not (Test-Path -LiteralPath $PackageSource -PathType Container)) {
    throw "The pdf2dxf package folder is missing. Extract the complete project ZIP before running this installer."
}

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    New-Item -ItemType Directory -Path $DownloadDir -Force | Out-Null
    New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

    Write-Host "[1/5] Downloading the private Python runtime..."
    $PythonZip = Join-Path $DownloadDir "python-embed.zip"
    Get-FileChecked -Uri $PythonArchiveUrl -Destination $PythonZip -Sha256 $PythonArchiveSha256
    $RuntimeDir = Join-Path $StageDir "runtime"
    Expand-Archive -LiteralPath $PythonZip -DestinationPath $RuntimeDir -Force

    Write-Host "[2/5] Configuring the private runtime..."
    $PthFile = Get-ChildItem -LiteralPath $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $PthFile) {
        throw "The embedded Python path configuration was not found."
    }
    @(
        "python313.zip"
        "."
        "Lib"
        "Lib\site-packages"
        "import site"
    ) | Set-Content -LiteralPath $PthFile.FullName -Encoding ASCII
    $SitePackages = Join-Path $RuntimeDir "Lib\site-packages"
    New-Item -ItemType Directory -Path $SitePackages -Force | Out-Null

    Write-Host "[3/5] Downloading PyMuPDF..."
    $Metadata = Invoke-RestMethod -UseBasicParsing -Uri "https://pypi.org/pypi/PyMuPDF/$PyMuPDFVersion/json"
    $Wheel = $Metadata.urls | Where-Object {
        $_.packagetype -eq "bdist_wheel" -and $_.filename -match "-cp3[0-9]+-abi3-win_amd64\.whl$"
    } | Select-Object -First 1
    if (-not $Wheel) {
        throw "A compatible PyMuPDF Windows wheel was not found."
    }
    $WheelZip = Join-Path $DownloadDir "pymupdf-wheel.zip"
    Get-FileChecked -Uri $Wheel.url -Destination $WheelZip -Sha256 $Wheel.digests.sha256
    Expand-Archive -LiteralPath $WheelZip -DestinationPath $SitePackages -Force

    Write-Host "[4/5] Installing pdf2dxf..."
    Copy-Item -LiteralPath $PackageSource -Destination $SitePackages -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall-windows.ps1") -Destination $StageDir -Force
    @'
@echo off
"%~dp0runtime\python.exe" -m pdf2dxf %*
'@ | Set-Content -LiteralPath (Join-Path $StageDir "pdf2dxf.cmd") -Encoding ASCII

    $PythonExe = Join-Path $RuntimeDir "python.exe"
    $VersionOutput = & $PythonExe -m pdf2dxf --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Installed command validation failed: $VersionOutput"
    }

    Write-Host "[5/5] Activating the installation..."
    if (Test-Path -LiteralPath $BackupDir) {
        Remove-Item -LiteralPath $BackupDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $InstallDir) {
        Move-Item -LiteralPath $InstallDir -Destination $BackupDir
    }
    Move-Item -LiteralPath $StageDir -Destination $InstallDir
    $Installed = $true

    if (-not $NoPath) {
        Add-UserPathEntry -Directory $InstallDir
    }
    if (Test-Path -LiteralPath $BackupDir) {
        Remove-Item -LiteralPath $BackupDir -Recurse -Force
    }

    Write-Host ""
    Write-Host "pdf2dxf $VersionOutput was installed in:"
    Write-Host "  $InstallDir"
    if ($NoPath) {
        Write-Host "Run it with: $InstallDir\pdf2dxf.cmd input.pdf output.dxf"
    } else {
        Write-Host "Open a new Command Prompt or PowerShell window, then run:"
        Write-Host "  pdf2dxf input.pdf output.dxf"
    }
}
catch {
    if ((-not $Installed) -and (Test-Path -LiteralPath $BackupDir) -and (-not (Test-Path -LiteralPath $InstallDir))) {
        Move-Item -LiteralPath $BackupDir -Destination $InstallDir
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $StageDir) {
        Remove-Item -LiteralPath $StageDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $DownloadDir) {
        Remove-Item -LiteralPath $DownloadDir -Recurse -Force
    }
}
