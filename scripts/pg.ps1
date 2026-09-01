<#
Local PostgreSQL control for the AI Revenue Recovery project.

Docker needs WSL2, which isn't available on this machine, so we run a
self-contained PostgreSQL 17 (the zonky embedded-postgres binaries, pulled
from Maven Central) under %LOCALAPPDATA%\revrec-pg. No admin, no service.

Usage (from repo root):
    powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 install   # one-time: download + initdb + create DBs
    powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start
    powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 stop
    powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 status

Connection (matches backend/.env.example):
    postgresql+psycopg://revrec:revrec@localhost:5432/revrec        (app)
    postgresql+psycopg://revrec:revrec@localhost:5432/revrec_test   (tests)
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'start', 'stop', 'status', 'restart')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$PgVersion = '17.11.0'
$Root      = Join-Path $env:LOCALAPPDATA 'revrec-pg'
$Bin       = Join-Path $Root 'dist\bin'
$Data      = Join-Path $Root 'data'
$LogFile   = Join-Path $Root 'pg.log'
$Port      = 5432
$SuperUser = 'revrec'
$MavenUrl  = "https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/$PgVersion/embedded-postgres-binaries-windows-amd64-$PgVersion.jar"

function Install-Postgres {
    if (Test-Path (Join-Path $Bin 'postgres.exe')) {
        Write-Host "binaries already present at $Bin"
    }
    else {
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        $jar = Join-Path $Root 'pg.jar'
        Write-Host "downloading PostgreSQL $PgVersion binaries ..."
        Invoke-WebRequest -Uri $MavenUrl -OutFile $jar
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $extract = Join-Path $Root 'jar'
        Remove-Item -Recurse -Force $extract -ErrorAction SilentlyContinue
        [System.IO.Compression.ZipFile]::ExtractToDirectory($jar, $extract)
        $txz = Join-Path $extract 'postgres-windows-x86_64.txz'
        $dist = Join-Path $Root 'dist'
        New-Item -ItemType Directory -Force -Path $dist | Out-Null
        # tar.exe (bsdtar) ships with Windows 10+ and handles .xz;
        # the archive contains bin/ lib/ share/ at its root
        & tar.exe -xf $txz -C $dist
        Write-Host "extracted to $Bin"
    }

    if (-not (Test-Path (Join-Path $Data 'PG_VERSION'))) {
        Write-Host "initdb ..."
        & (Join-Path $Bin 'initdb.exe') --pgdata=$Data --username=$SuperUser --auth=trust --encoding=UTF8 --no-locale | Out-Null
    }

    Start-Postgres
    Write-Host "creating databases revrec + revrec_test (if missing) ..."
    $py = @"
import psycopg
c = psycopg.connect('host=localhost port=$Port user=$SuperUser dbname=postgres')
c.autocommit = True
for db in ('revrec', 'revrec_test'):
    if not c.execute('select 1 from pg_database where datname=%s', (db,)).fetchone():
        c.execute(f'CREATE DATABASE {db} OWNER $SuperUser'); print('created', db)
    else:
        print(db, 'exists')
c.close()
"@
    Push-Location (Join-Path $PSScriptRoot '..\backend')
    try { $py | uv run python - } finally { Pop-Location }
}

function Start-Postgres {
    if (Test-Path (Join-Path $Data 'postmaster.pid')) {
        $running = & (Join-Path $Bin 'pg_ctl.exe') -D $Data status 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "already running"; return }
    }
    & (Join-Path $Bin 'pg_ctl.exe') -D $Data -l $LogFile -o "-p $Port" -w start
}

function Stop-Postgres {
    & (Join-Path $Bin 'pg_ctl.exe') -D $Data -m fast -w stop
}

function Get-Status {
    if (Test-Path (Join-Path $Bin 'pg_ctl.exe')) {
        & (Join-Path $Bin 'pg_ctl.exe') -D $Data status
    }
    else {
        Write-Host "not installed - run: scripts\pg.ps1 install"
    }
}

switch ($Command) {
    'install' { Install-Postgres }
    'start'   { Start-Postgres }
    'stop'    { Stop-Postgres }
    'restart' { Stop-Postgres; Start-Postgres }
    'status'  { Get-Status }
}
