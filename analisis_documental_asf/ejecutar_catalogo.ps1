param(
    [string]$BaseOCR,
    [string]$Salida
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $BaseOCR) { $BaseOCR = Join-Path (Split-Path $PSScriptRoot) 'ocr_masivo\logs\estado.sqlite3' }
if (-not $Salida) { $Salida = Join-Path $PSScriptRoot 'resultados' }
$catalogPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $catalogPython -PathType Leaf)) {
    $catalogPython = Join-Path (Split-Path $PSScriptRoot) 'ocr_masivo\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $catalogPython -PathType Leaf)) {
    throw 'No existe un entorno Python. Cree .venv e instale el proyecto según el README.'
}
$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
& $catalogPython -m analisis_documental_asf --ocr-db $BaseOCR --salida $Salida
exit $LASTEXITCODE
