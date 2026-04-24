# Bring up Postgres, apply schema, partitions, indexes-baseline, and seed metadata.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/init_db.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example"
}

Write-Host "==> docker compose up -d postgres"
docker compose up -d postgres
if (-not $?) { throw "docker compose failed" }

Write-Host "==> Waiting for Postgres health..."
$max = 40
for ($i = 0; $i -lt $max; $i++) {
    $status = docker inspect -f '{{.State.Health.Status}}' equitydb-pg 2>$null
    if ($status -eq "healthy") { break }
    Start-Sleep -Seconds 1
}
if ($status -ne "healthy") { throw "Postgres did not become healthy in time" }

function Invoke-Sql($file) {
    Write-Host "==> psql < $file"
    docker exec -i equitydb-pg psql -U equity -d equitydb -v ON_ERROR_STOP=1 -f "/sql/$file"
    if (-not $?) { throw "psql failed on $file" }
}

Invoke-Sql "00_extensions.sql"
Invoke-Sql "01_schema.sql"
Invoke-Sql "02_partitions.sql"
Invoke-Sql "99_seed_exchanges.sql"

Write-Host "==> Python: seed metadata + calendar"
if (-not (Test-Path .venv)) {
    py -3.13 -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip *> $null
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe ingest\seed_meta.py
& .\.venv\Scripts\python.exe ingest\seed_calendar.py

Write-Host "==> Done. Adminer (optional): http://localhost:8080  (server: postgres, user/pass: equity)"
