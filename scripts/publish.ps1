param(
  [switch]$TestPyPI,
  [switch]$PyPI,
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Set-EnvFromDotEnv {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  if (-not (Test-Path $Path)) {
    return
  }

  $lines = Get-Content -Path $Path
  foreach ($line in $lines) {
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
      continue
    }
    if ($trimmed.StartsWith("#")) {
      continue
    }

    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -ne 2) {
      continue
    }

    $key = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    if ($key -eq "TWINE_USERNAME" -and [string]::IsNullOrEmpty($env:TWINE_USERNAME)) {
      $env:TWINE_USERNAME = $value
    }
    if ($key -eq "TWINE_PASSWORD" -and [string]::IsNullOrEmpty($env:TWINE_PASSWORD)) {
      $env:TWINE_PASSWORD = $value
    }
  }
}

Set-EnvFromDotEnv -Path ".env"

if (($TestPyPI -or $PyPI) -and ([string]::IsNullOrEmpty($env:TWINE_USERNAME) -or [string]::IsNullOrEmpty($env:TWINE_PASSWORD))) {
  throw "Missing TWINE credentials. Set TWINE_USERNAME/TWINE_PASSWORD in environment or .env file."
}

function Invoke-Python {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Args,
    [Parameter(Mandatory = $true)]
    [string]$FailureMessage
  )

  & python @Args
  if ($LASTEXITCODE -ne 0) {
    throw $FailureMessage
  }
}

function Ensure-PublishTools {
  $missing = @()

  & python -c "import build" 2>$null
  if ($LASTEXITCODE -ne 0) {
    $missing += "build"
  }

  & python -c "import twine" 2>$null
  if ($LASTEXITCODE -ne 0) {
    $missing += "twine"
  }

  if ($missing.Count -gt 0) {
    Write-Host "Installing missing publish tools: $($missing -join ', ')"
    Invoke-Python -Args @("-m", "pip", "install", "--upgrade") + $missing -FailureMessage "Failed to install publish tools."
  }
}

Ensure-PublishTools

function Get-ProjectMetadata {
  $raw = & python -c "import pathlib, tomllib; d=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); print(d['project']['name']); print(d['project']['version'])"
  if ($LASTEXITCODE -ne 0 -or $raw.Count -lt 2) {
    throw "Failed to read project metadata from pyproject.toml."
  }

  return @{
    Name = $raw[0].Trim()
    Version = $raw[1].Trim()
  }
}

function Get-DistFilesForCurrentVersion {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PackageName,
    [Parameter(Mandatory = $true)]
    [string]$Version
  )

  $prefix = "$PackageName-$Version"
  $files = Get-ChildItem -Path "dist" -File | Where-Object {
    $_.Name -like "$prefix*.whl" -or $_.Name -like "$prefix*.tar.gz"
  } | Sort-Object Name

  if ($files.Count -eq 0) {
    throw "No distribution files found for $prefix in dist/. Run build first."
  }

  return $files | ForEach-Object { $_.FullName }
}

$meta = Get-ProjectMetadata
Write-Host "Project: $($meta.Name) $($meta.Version)"

if (-not $SkipBuild) {
  Write-Host "[1/3] Building distribution artifacts..."
  Invoke-Python -Args @("-m", "build") -FailureMessage "Build failed."
}

$distFiles = Get-DistFilesForCurrentVersion -PackageName $meta.Name -Version $meta.Version

Write-Host "[2/3] Validating artifacts with twine..."
Invoke-Python -Args (@("-m", "twine", "check") + $distFiles) -FailureMessage "Twine check failed."

if ($TestPyPI) {
  Write-Host "[3/3] Uploading to TestPyPI..."
  Invoke-Python -Args (@("-m", "twine", "upload", "--repository", "testpypi") + $distFiles) -FailureMessage "Upload to TestPyPI failed."
  return
}

if ($PyPI) {
  Write-Host "[3/3] Uploading to PyPI..."
  Invoke-Python -Args (@("-m", "twine", "upload") + $distFiles) -FailureMessage "Upload to PyPI failed."
  return
}

Write-Host "[3/3] Dry run complete."
Write-Host "Use -TestPyPI or -PyPI to publish."
