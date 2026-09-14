# Заливка кода на сервер с этой Windows-машины.
#
# Первый раз (полная настройка сервера — пакеты, БД, systemd, nginx, HTTPS):
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Server user@1.2.3.4 -Bootstrap -Email you@example.com
#
# Дальше, после любой правки кода:
#   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Server user@1.2.3.4
#
# Логин не root (Google Cloud, обычный VPS с sudo) — команды сами обернутся в sudo.
#
# .env на сервере НЕ трогается: локальный .env содержит прокси и адрес туннеля,
# на сервере от них всё ломается. Серверный .env живёт в /opt/bratstvo/.env.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [int]$Port = 22,
    [switch]$Bootstrap,
    [string]$Email = "",
    [string]$Domain = "",
    [string]$AppDir = "/opt/bratstvo"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if ($Bootstrap -and [string]::IsNullOrWhiteSpace($Email)) {
    throw "-Bootstrap требует -Email: на этот адрес Let's Encrypt пришлёт предупреждение о непродлённом сертификате."
}
if ($Server -notmatch '^([A-Za-z0-9][A-Za-z0-9._-]*@)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' -or $Server.Contains('..')) {
    throw "Некорректный -Server"
}
if ($Port -lt 1 -or $Port -gt 65535) { throw "Некорректный -Port" }
if ($AppDir -notmatch '^/[A-Za-z0-9._/-]+$' -or $AppDir -match '/\.\.?(/|$)') {
    throw "Некорректный -AppDir"
}
if ($Email -and $Email -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$') {
    throw "Некорректный -Email"
}
if ($Domain -and $Domain -notmatch '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$') {
    throw "Некорректный -Domain"
}

# На Google Cloud под root не заходят — там обычный пользователь с passwordless sudo.
$remotePrefix = "sudo "
if ($Server -like "root@*") { $remotePrefix = "" }

function Invoke-Remote([string]$command) {
    ssh -p $Port $Server "$remotePrefix bash -c '$command'"
    if ($LASTEXITCODE -ne 0) { throw "Команда на сервере вернула $LASTEXITCODE : $command" }
}

$nonce = [guid]::NewGuid().ToString("N")
$archive = Join-Path $env:TEMP "bratstvo_deploy_$nonce.tar.gz"
$remoteArchive = "/tmp/bratstvo_deploy_$nonce.tar.gz"
if (Test-Path $archive) { Remove-Item $archive -Force }

Write-Host "==> Упаковка кода" -ForegroundColor Cyan
# Исключаем всё, что на сервере своё: окружение, .env, базу, загруженные документы.
tar --exclude=./.venv --exclude=./.env --exclude=./.git --exclude=./.claude `
    --exclude=./storage --exclude=./.pytest_cache --exclude=./__pycache__ `
    --exclude=*.pyc --exclude=*.db --exclude=./deploy/*.tar.gz `
    -czf $archive -C $projectRoot .
if ($LASTEXITCODE -ne 0) { throw "tar завершился с кодом $LASTEXITCODE" }
$sizeMb = [math]::Round((Get-Item $archive).Length / 1MB, 2)
Write-Host "    архив: $sizeMb МБ"

Write-Host "==> Загрузка на сервер" -ForegroundColor Cyan
scp -P $Port $archive "${Server}:$remoteArchive"
if ($LASTEXITCODE -ne 0) { throw "scp завершился с кодом $LASTEXITCODE" }

Write-Host "==> Распаковка" -ForegroundColor Cyan
Invoke-Remote "mkdir -p $AppDir; test -f $remoteArchive; tar xzf $remoteArchive -C $AppDir; rm -f $remoteArchive; chmod +x $AppDir/deploy/*.sh"

if ($Bootstrap) {
    Write-Host "==> Первичная настройка сервера (пакеты, PostgreSQL, systemd, nginx, HTTPS)" -ForegroundColor Cyan
    $setupArgs = "--email $Email"
    if (-not [string]::IsNullOrWhiteSpace($Domain)) { $setupArgs = "$setupArgs --domain $Domain" }
    Invoke-Remote "bash $AppDir/deploy/setup_server.sh $setupArgs"
}
else {
    Write-Host "==> Обновление зависимостей и перезапуск" -ForegroundColor Cyan
    Invoke-Remote "chown -R bratstvo:bratstvo $AppDir; test -f $AppDir/.env; chown root:bratstvo $AppDir/.env; chmod 640 $AppDir/.env; test -f $AppDir/requirements.lock; sudo -u bratstvo $AppDir/.venv/bin/pip install --quiet --require-hashes -r $AppDir/requirements.lock; cd $AppDir; sudo -u bratstvo $AppDir/.venv/bin/python -m scripts.migrate_security_constraints; systemctl restart bratstvo-api bratstvo-bot; sleep 3; systemctl is-active bratstvo-api bratstvo-bot"
}

Remove-Item $archive -Force
Write-Host "Готово." -ForegroundColor Green
