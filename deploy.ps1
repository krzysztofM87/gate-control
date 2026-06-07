Write-Host "Git status..." -ForegroundColor Cyan

git status

$commitMessage = Read-Host "Commit message"

git add .

git commit -m "$commitMessage"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Git commit failed or there were no changes. Continuing with push/deploy..." -ForegroundColor Yellow
}

git push

if ($LASTEXITCODE -ne 0) {
    Write-Host "Git push failed. Stopping deploy." -ForegroundColor Red
    exit 1
}

Write-Host "Updating VPS..." -ForegroundColor Cyan

ssh gate-vps "cd /opt/gate-control && git pull && docker compose up -d --build && docker ps"

if ($LASTEXITCODE -ne 0) {
    Write-Host "VPS deploy failed." -ForegroundColor Red
    exit 1
}

Write-Host "Deploy finished." -ForegroundColor Green