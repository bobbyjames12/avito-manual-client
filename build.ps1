$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --name AvitoManualClient --collect-data botocore --add-data "avito_client/catalog.json;avito_client" main.py
if ($LASTEXITCODE -ne 0) { throw 'Сборка EXE не удалась' }
# Qt uses Windows' unversioned ICU API. PyInstaller can collect an incompatible
# icuuc.dll from another PATH entry (ICU 78 exports suffixed symbols).
# Leave Windows to resolve its own system ICU, as it does for the source app.
$bundledIcu = Join-Path $PSScriptRoot 'dist\AvitoManualClient\_internal\icuuc.dll'
if (Test-Path -LiteralPath $bundledIcu) { Remove-Item -LiteralPath $bundledIcu }
Copy-Item -LiteralPath 'README.md' -Destination 'dist\AvitoManualClient\README.md' -Force
Compress-Archive -Path '.\dist\AvitoManualClient' -DestinationPath '.\dist\AvitoManualClient-Windows.zip' -Force
