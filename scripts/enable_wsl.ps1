$ErrorActionPreference = "Stop"

Write-Host "Enabling Windows Subsystem for Linux..."
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
if ($LASTEXITCODE -notin 0, 3010) {
    throw "Failed to enable Windows Subsystem for Linux (exit $LASTEXITCODE)."
}

Write-Host "Enabling Virtual Machine Platform..."
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
if ($LASTEXITCODE -notin 0, 3010) {
    throw "Failed to enable Virtual Machine Platform (exit $LASTEXITCODE)."
}

Write-Host "WSL prerequisites enabled. Restart Windows before continuing."
