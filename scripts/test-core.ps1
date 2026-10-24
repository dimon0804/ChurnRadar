# Тесты Python-пакетов ядра под Windows PowerShell: каждый пакет отдельным запуском pytest.
#
#   scripts\test-core.ps1                 юнит-тесты всех пакетов
#   scripts\test-core.ps1 -Integration    плюс интеграционные: Postgres и NATS поднимаются в docker
#   scripts\test-core.ps1 -Lint           ruff и mypy для libs/riskcore и libs/svckit
#   scripts\test-core.ps1 ingest api      только перечисленные пакеты (имя каталога)
#
# Интерпретатор: $env:PYTHON, иначе .venv в корне репозитория, иначе python.
param(
    [switch]$Integration,
    [switch]$Lint,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Only
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Packages = @('libs/riskcore', 'libs/svckit', 'services/migrate', 'services/ingest', 'services/api',
    'services/scoring', 'services/simulator')

if ($env:PYTHON) { $Py = $env:PYTHON }
elseif (Test-Path "$Root/.venv/Scripts/python.exe") { $Py = "$Root/.venv/Scripts/python.exe" }
else { $Py = 'python' }

# libs берём из исходников, даже если в site-packages стоит старая установленная копия
$env:PYTHONPATH = "$Root/libs/riskcore;$Root/libs/svckit"
$env:PYTHONDONTWRITEBYTECODE = '1'

$Containers = @()
$Failed = @()
try {
    if ($Integration -and -not ($env:TEST_DATABASE_URL -and $env:TEST_NATS_URL)) {
        $PgPort = if ($env:TEST_PG_PORT) { $env:TEST_PG_PORT } else { '55432' }
        $NatsPort = if ($env:TEST_NATS_PORT) { $env:TEST_NATS_PORT } else { '54222' }
        $Suffix = $PID
        Write-Host ">> Postgres :$PgPort и NATS :$NatsPort в docker"
        $Containers = @("churn-test-pg-$Suffix", "churn-test-nats-$Suffix")
        docker run -d --rm --name "churn-test-pg-$Suffix" -p "127.0.0.1:${PgPort}:5432" `
            -e POSTGRES_USER=churn -e POSTGRES_PASSWORD=churn -e POSTGRES_DB=churn postgres:16-alpine | Out-Null
        docker run -d --rm --name "churn-test-nats-$Suffix" -p "127.0.0.1:${NatsPort}:4222" nats:2.10-alpine -js | Out-Null
        for ($i = 0; $i -lt 60; $i++) {
            docker exec "churn-test-pg-$Suffix" pg_isready -U churn -d churn -h 127.0.0.1 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { break }
            Start-Sleep -Seconds 1
        }
        $env:TEST_DATABASE_URL = "postgresql://churn:churn@127.0.0.1:$PgPort/churn"
        $env:TEST_NATS_URL = "nats://127.0.0.1:$NatsPort"
    }
    if (-not $Integration) {
        # без инфраструктуры интеграционные тесты пропускаются, а не ждут таймаутов подключения
        if (-not $env:TEST_DATABASE_URL) { $env:TEST_DATABASE_URL = 'postgresql://churn:churn@127.0.0.1:1/churn' }
        if (-not $env:TEST_NATS_URL) { $env:TEST_NATS_URL = 'nats://127.0.0.1:1' }
    }

    foreach ($pkg in $Packages) {
        $name = Split-Path -Leaf $pkg
        if ($Only -and -not ($Only -contains $name)) { continue }
        Write-Host "`n=== $pkg"
        Push-Location "$Root/$pkg"
        try {
            if ($Integration) { & $Py -m pytest -q -p no:cacheprovider }
            else { & $Py -m pytest -q -p no:cacheprovider -m 'not integration' }
            if ($LASTEXITCODE -ne 0) { $Failed += $pkg }
        }
        finally { Pop-Location }
    }

    if ($Lint) {
        foreach ($pkg in @('libs/riskcore', 'libs/svckit')) {
            Write-Host "`n=== lint $pkg"
            Push-Location "$Root/$pkg"
            try {
                & $Py -m ruff check .
                $ok = $LASTEXITCODE -eq 0
                & $Py -m ruff format --check .
                $ok = $ok -and $LASTEXITCODE -eq 0
                & $Py -m mypy (Split-Path -Leaf $pkg)
                $ok = $ok -and $LASTEXITCODE -eq 0
                if (-not $ok) { $Failed += "lint:$pkg" }
            }
            finally { Pop-Location }
        }
    }
}
finally {
    if ($Containers.Count -gt 0) { docker rm -f @Containers 2>$null | Out-Null }
}

if ($Failed.Count -gt 0) {
    Write-Host "`nУпали: $($Failed -join ', ')"
    exit 1
}
Write-Host "`nВсе пакеты зелёные"
