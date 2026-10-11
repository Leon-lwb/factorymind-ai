# Local equivalent of .github/workflows/ci.yml (spec §36) for Windows.
# Usage: powershell -File scripts/verify.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$engine = Join-Path $root "services/ai-engine"

$py = "python"
$venvPy = Join-Path $engine ".venv/Scripts/python.exe"
if (Test-Path $venvPy) { $py = $venvPy }

Write-Host "==> [backend] ruff"
Push-Location $engine
& $py -m ruff check app tests scripts
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }

Write-Host "==> [backend] pytest"
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

Write-Host "==> [frontend] typecheck / lint / build"
Push-Location $root
pnpm typecheck
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
pnpm lint
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
pnpm build
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
Pop-Location

Write-Host "==> All checks passed."