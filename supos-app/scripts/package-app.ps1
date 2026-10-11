# Assemble and zip the FactoryMind AI open supOS app package.
#
#   powershell -File scripts/package-app.ps1
#   powershell -File scripts/package-app.ps1 -BuildImages   # also docker build
#
# Produces `quality-copilot-app.zip` at the package root, ready for
#   POST /inter-api/supos/app/uploadApp  (form field `file`, `autoInstall`)
#   POST /inter-api/supos/app/install    (body `name` = quality-copilot)
#
# The committed `bin/` holds only the Dockerfiles; source is staged here from the
# repository at package time so there is a single source of truth.

param(
  [switch]$BuildImages
)

$ErrorActionPreference = 'Stop'

$appRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $appRoot
$engine = Join-Path $repoRoot 'services/ai-engine'
$stage = Join-Path $appRoot '.package'
$zipPath = Join-Path $appRoot 'quality-copilot-app.zip'

function Copy-Tree {
  param([string]$From, [string]$To, [string[]]$ExcludeDirs = @())
  $robocopyArgs = @($From, $To, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
  if ($ExcludeDirs.Count -gt 0) { $robocopyArgs += '/XD'; $robocopyArgs += $ExcludeDirs }
  robocopy @robocopyArgs | Out-Null
  # robocopy exit codes 0-7 are success; >=8 is a real failure.
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $From -> $To" }
}

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null

# 1. Manifests and metadata.
foreach ($name in @('app.yaml','build.yaml','compose.yaml','vendor.yaml','dependency.yaml',
                    'dependencies.yaml','requirement.yaml','open_api.yaml','config.ini','README.md')) {
  Copy-Item -LiteralPath (Join-Path $appRoot $name) -Destination $stage
}
Copy-Tree (Join-Path $appRoot 'data') (Join-Path $stage 'data')
Copy-Tree (Join-Path $appRoot 'statics') (Join-Path $stage 'statics')

# 2. bin/backend — engine source (no venv, no tests, no caches).
$backend = Join-Path $stage 'bin/backend'
New-Item -ItemType Directory -Force -Path $backend | Out-Null
Copy-Item (Join-Path $appRoot 'bin/backend/Dockerfile'),(Join-Path $appRoot 'bin/backend/.dockerignore') -Destination $backend
Copy-Tree (Join-Path $engine 'app') (Join-Path $backend 'app') @('__pycache__')
Copy-Tree (Join-Path $engine 'config') (Join-Path $backend 'config') @('__pycache__')
Copy-Item (Join-Path $engine 'requirements.txt') -Destination $backend

# 3. bin/front — the pnpm workspace subset the SPA build needs.
$front = Join-Path $stage 'bin/front'
New-Item -ItemType Directory -Force -Path $front | Out-Null
Copy-Item (Join-Path $appRoot 'bin/front/Dockerfile'),(Join-Path $appRoot 'bin/front/nginx.conf'),(Join-Path $appRoot 'bin/front/.dockerignore') -Destination $front
foreach ($name in @('pnpm-workspace.yaml','package.json','pnpm-lock.yaml')) {
  Copy-Item (Join-Path $repoRoot $name) -Destination $front
}
Copy-Tree (Join-Path $repoRoot 'packages/shared-types') (Join-Path $front 'packages/shared-types') @('node_modules')
Copy-Tree (Join-Path $repoRoot 'apps/web') (Join-Path $front 'apps/web') @('node_modules','dist')

# 4. Zip. Entries are written by hand with forward slashes: Windows PowerShell's
#    Compress-Archive emits backslashes, which violates the ZIP spec and can break
#    extraction on the Linux host that installs the package.
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stageFull = (Resolve-Path -LiteralPath $stage).ProviderPath
$zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
  Get-ChildItem -LiteralPath $stageFull -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($stageFull.Length + 1).Replace('\', '/')
    $entry = $zip.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
    $entryStream = $entry.Open()
    try {
      $fileStream = [System.IO.File]::OpenRead($_.FullName)
      try { $fileStream.CopyTo($entryStream) } finally { $fileStream.Dispose() }
    } finally { $entryStream.Dispose() }
  }
} finally {
  $zip.Dispose()
}
Write-Host "Package written: $zipPath"

# 5. Optional: build the two images from the staged package.
if ($BuildImages) {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is required for -BuildImages.' }
  docker build -t factorymind-ai-backend:1.0.0 (Join-Path $stage 'bin/backend')
  if ($LASTEXITCODE -ne 0) { throw "docker build factorymind-ai-backend failed ($LASTEXITCODE)." }
  docker build -t factorymind-ai-front:1.0.0 (Join-Path $stage 'bin/front')
  if ($LASTEXITCODE -ne 0) { throw "docker build factorymind-ai-front failed ($LASTEXITCODE)." }
  Write-Host 'Images built: factorymind-ai-front:1.0.0, factorymind-ai-backend:1.0.0'
}
