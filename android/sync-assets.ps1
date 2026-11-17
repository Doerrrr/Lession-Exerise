# Copy the shared app code into the Android Python source set.
# Run from this directory before the first Gradle build.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $PSScriptRoot 'app\src\main\python'
$assets = Join-Path $py 'android_assets'
New-Item -ItemType Directory -Force -Path (Join-Path $assets 'web'), (Join-Path $assets 'data') | Out-Null

Copy-Item (Join-Path $root 'server.py') (Join-Path $py 'server.py') -Force
if (Test-Path (Join-Path $py 'dictation')) {
    Remove-Item (Join-Path $py 'dictation') -Recurse -Force
}
Copy-Item (Join-Path $root 'dictation') (Join-Path $py 'dictation') -Recurse -Force
Get-ChildItem (Join-Path $py 'dictation') -Recurse -Directory -Filter '__pycache__' |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force }

Get-ChildItem (Join-Path $assets 'web') -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'web\*') (Join-Path $assets 'web') -Recurse -Force
Copy-Item (Join-Path $root 'data\words.json') (Join-Path $assets 'data\words.json') -Force

Write-Host 'Android Python assets synced.'
