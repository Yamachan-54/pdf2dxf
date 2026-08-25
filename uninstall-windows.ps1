#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\pdf2dxf")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "This uninstaller must be run on Windows."
}

$Current = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not [string]::IsNullOrWhiteSpace($Current)) {
    $Entries = @($Current.Split(";") | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and
        $_.TrimEnd("\") -ine $InstallDir.TrimEnd("\")
    })
    [Environment]::SetEnvironmentVariable("Path", ($Entries -join ";"), "User")
}

if (Test-Path -LiteralPath $InstallDir) {
    Set-Location ([System.IO.Path]::GetTempPath())
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Write-Host "pdf2dxf was removed from: $InstallDir"
} else {
    Write-Host "pdf2dxf is not installed in: $InstallDir"
}
Write-Host "Open a new terminal window to apply the PATH change."

