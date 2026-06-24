Write-Host "=== Dev Certificate Setup ===" -ForegroundColor Cyan

# ---- Ensure Admin حقوق ----
if (-not ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(`
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {

    Write-Host "Restarting as Administrator..." -ForegroundColor Yellow
    Start-Process powershell "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

# ---- Paths ----
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootCAPath = Join-Path $scriptDir "rootCA.pem"

if (!(Test-Path $rootCAPath)) {
    Write-Host "❌ rootCA.pem not found in script folder!" -ForegroundColor Red
    exit 1
}

# =========================================================
# 1️⃣ Install Chocolatey (OFFICIAL SCRIPT)
# =========================================================
if (!(Get-Command choco -ErrorAction SilentlyContinue)) {

    Write-Host "Installing Chocolatey (official script)..." -ForegroundColor Yellow

    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = `
        [System.Net.ServicePointManager]::SecurityProtocol -bor 3072

    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString(
        'https://community.chocolatey.org/install.ps1'
    ))

    # Refresh PATH for current session
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine")

} else {
    Write-Host "Chocolatey already installed" -ForegroundColor Green
}

# =========================================================
# 2️⃣ Install mkcert
# =========================================================
if (!(Get-Command mkcert -ErrorAction SilentlyContinue)) {

    Write-Host "Installing mkcert..." -ForegroundColor Yellow
    choco install mkcert -y

    # Refresh PATH again
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine")

} else {
    Write-Host "mkcert already installed" -ForegroundColor Green
}

# =========================================================
# 3️⃣ Run mkcert -install (creates local CA)
# =========================================================
Write-Host "Running mkcert -install..." -ForegroundColor Yellow
mkcert -install

# =========================================================
# 4️⃣ Overwrite/Import SERVER rootCA.pem
# =========================================================

Write-Host "Installing TEAM root CA (overrides trust)..." -ForegroundColor Yellow

# Convert/copy to temp
$tempCert = Join-Path $env:TEMP "team-rootCA.crt"
Copy-Item $rootCAPath $tempCert -Force

# Import into system trust store
Import-Certificate `
    -FilePath $tempCert `
    -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

# Also install per user
Import-Certificate `
    -FilePath $tempCert `
    -CertStoreLocation Cert:\CurrentUser\Root | Out-Null

Remove-Item $tempCert -Force

Write-Host "✅ Team root CA installed successfully!" -ForegroundColor Green

# =========================================================
# ✅ DONE
# =========================================================
Write-Host ""
Write-Host "=== DONE ===" -ForegroundColor Cyan
Write-Host "👉 Restart Chrome/Edge completely!"
``