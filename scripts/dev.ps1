param(
    [Parameter(Position = 0)]
    [string]$Action = "help"
)

$ErrorActionPreference = "Stop"
$ComposeFile = "docker-compose.dev.yml"

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    & docker compose -f $ComposeFile @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-App {
    param([string[]]$AppArgs)
    $Args = @("exec", "-T", "app") + $AppArgs
    Invoke-Compose $Args
}

function Show-Help {
    Write-Host "Usage: ./scripts/dev.ps1 <action>"
    Write-Host ""
    Write-Host "Actions:"
    Write-Host "  dev-up | up"
    Write-Host "  dev-up-no-wait | up-no-wait"
    Write-Host "  dev-down | down"
    Write-Host "  dev-clean | clean"
    Write-Host "  dev-logs | logs"
    Write-Host "  dev-app-logs | app-logs"
    Write-Host "  dev-db-logs | db-logs"
    Write-Host "  dev-migrate | migrate"
    Write-Host "  dev-seed | seed"
    Write-Host "  dev-test | test"
    Write-Host "  request-health"
    Write-Host "  request-ready"
    Write-Host "  request-followup-first"
    Write-Host "  request-followup-second"
    Write-Host "  request-multitask"
    Write-Host "  request-safety-toxic"
    Write-Host "  request-idempotency"
    Write-Host "  request-profile-memory"
    Write-Host "  request-memory-read"
    Write-Host "  request-all"
    Write-Host "  request-curl"
}

switch ($Action) {
    { $_ -in @("dev-up", "up") } {
        Invoke-Compose @("up", "-d", "--build", "--wait")
        break
    }
    { $_ -in @("dev-up-no-wait", "up-no-wait") } {
        Invoke-Compose @("up", "-d", "--build")
        break
    }
    { $_ -in @("dev-down", "down") } {
        Invoke-Compose @("down", "--remove-orphans")
        break
    }
    { $_ -in @("dev-clean", "clean") } {
        Invoke-Compose @("down", "-v", "--remove-orphans")
        break
    }
    { $_ -in @("dev-logs", "logs") } {
        Invoke-Compose @("logs", "-f")
        break
    }
    { $_ -in @("dev-app-logs", "app-logs") } {
        Invoke-Compose @("logs", "-f", "app")
        break
    }
    { $_ -in @("dev-db-logs", "db-logs") } {
        Invoke-Compose @("logs", "-f", "postgres")
        break
    }
    { $_ -in @("dev-migrate", "migrate") } {
        Invoke-App @("alembic", "upgrade", "head")
        break
    }
    { $_ -in @("dev-seed", "seed") } {
        Invoke-App @("python", "scripts/seed_database.py")
        break
    }
    { $_ -in @("dev-test", "test") } {
        Invoke-App @("pytest", "-q")
        break
    }
    { $_ -eq "request-curl" } {
        Invoke-App @("python", "scripts/dev_request.py", "print-curl")
        break
    }
    { $_.StartsWith("request-") } {
        $Scenario = $_.Substring("request-".Length)
        Invoke-App @("python", "scripts/dev_request.py", $Scenario)
        break
    }
    default {
        Show-Help
    }
}
