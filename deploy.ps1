Write-Host "Gate Control deploy" -ForegroundColor Cyan
Write-Host "===================" -ForegroundColor Cyan

# Go to script directory
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "Local git status:" -ForegroundColor Cyan
git status

$commitMessage = Read-Host "Commit message"

if ([string]::IsNullOrWhiteSpace($commitMessage)) {
    Write-Host "Commit message is empty. Stopping." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Adding files..." -ForegroundColor Cyan
git add .

Write-Host ""
Write-Host "Creating commit..." -ForegroundColor Cyan
git commit -m "$commitMessage"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Commit failed or there are no changes. Continuing with push and VPS update..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Pushing to GitHub..." -ForegroundColor Cyan
git push

if ($LASTEXITCODE -ne 0) {
    Write-Host "Git push failed. Stopping deploy." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Updating VPS from GitHub..." -ForegroundColor Cyan

$remoteCommand = @'
set -e

cd /opt/gate-control

echo "Current directory:"
pwd

echo ""
echo "Pulling latest code..."
git pull

echo ""
echo "Checking Docker..."

if command -v docker >/dev/null 2>&1; then
    echo "Docker found."

    if docker compose version >/dev/null 2>&1; then
        echo "Using: docker compose"
        docker compose up -d --build
        docker ps
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "Using: docker-compose"
        docker-compose up -d --build
        docker ps
    else
        echo "Docker is installed, but Docker Compose is missing."
        echo "Code was updated, but app was not rebuilt."
    fi
else
    echo "Docker is not installed on VPS."
    echo "Code was updated, but app was not rebuilt."
fi
'@

ssh gate-vps $remoteCommand

if ($LASTEXITCODE -ne 0) {
    Write-Host "VPS update failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Deploy finished." -ForegroundColor Green