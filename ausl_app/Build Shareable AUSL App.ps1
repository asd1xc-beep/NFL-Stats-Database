$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path (Split-Path $PSScriptRoot -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "The project's virtual environment was not found. Run the NFL/AUSL setup first."
}

Write-Host "Installing the AUSL packaging requirements and current SSL certificates..."
& $python -m pip install --upgrade pandas openpyxl pyinstaller certifi

Write-Host "Building the Windows AUSL application..."
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "AUSL Broadcast Stats" `
    --paths src `
    --collect-all pandas `
    --collect-all certifi `
    --hidden-import openpyxl `
    --hidden-import certifi `
    src\ausl_stats_app.py

$package = Join-Path $PSScriptRoot "dist\AUSL Broadcast Stats"
$exports = Join-Path $package "data\exports"
New-Item -ItemType Directory -Force -Path $exports | Out-Null
Copy-Item "data\exports\ausl_rosters.xlsx" $exports -Force
Copy-Item "data\exports\ausl_season_stats.xlsx" $exports -Force
Copy-Item "data\exports\ausl_career_stats.xlsx" $exports -Force
foreach ($optionalExport in @(
    "data\exports\ausl_batting_splits.xlsx",
    "data\exports\ausl_pitching_splits.xlsx",
    "data\exports\ausl_fielding_splits.xlsx",
    "data\exports\ausl_team_context.xlsx",
    "data\exports\ausl_storyline_sources.xlsx"
)) {
    if (Test-Path $optionalExport) {
        Copy-Item $optionalExport $exports -Force
    }
}
Copy-Item "data\exports\update_manifest.json" $exports -Force
$manual = Join-Path $package "data\manual"
New-Item -ItemType Directory -Force -Path $manual | Out-Null
if (Test-Path "data\manual\player_notes.csv") {
    Copy-Item "data\manual\player_notes.csv" $manual -Force
}
Copy-Item "README.txt" $package -Force

$zip = Join-Path $PSScriptRoot "dist\AUSL-Broadcast-Stats-Windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "Compressing the shareable app..."
Compress-Archive -Path "$package\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Finished. Share this ZIP file:"
Write-Host $zip
Read-Host "Press Enter to close"
