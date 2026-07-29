$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtual environment not found. Create .venv and install requirements first."
}

Write-Host "Installing the packaging tool..."
& $python -m pip install -r requirements-app.txt
& $python -m pip install -r requirements-build.txt

Write-Host "Building the Windows application..."
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "NFL Stats Lookup" `
    --paths src `
    --collect-all nflreadpy `
    --collect-all polars `
    --collect-all pandas `
    --hidden-import openpyxl `
    --hidden-import xlsxwriter `
    src\nfl_stats_app.py

$package = Join-Path $PSScriptRoot "dist\NFL Stats Lookup"
$exports = Join-Path $package "data\exports"
New-Item -ItemType Directory -Force -Path $exports | Out-Null
Copy-Item "data\exports\nfl_career_database.xlsx" $exports -Force
Copy-Item "data\exports\nfl_latest_season_database.xlsx" $exports -Force
Copy-Item "data\exports\nfl_current_active_rosters.xlsx" $exports -Force
Copy-Item "data\exports\nfl_current_preseason_database.xlsx" $exports -Force
Copy-Item "SHAREABLE_APP_README.txt" $package -Force

$zip = Join-Path $PSScriptRoot "dist\NFL-Stats-Lookup-Windows.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "Compressing the app (this can take a minute or two; keep this window open)..."
Compress-Archive -Path "$package\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Finished! Share this file:"
Write-Host $zip
Read-Host "Press Enter to close"
