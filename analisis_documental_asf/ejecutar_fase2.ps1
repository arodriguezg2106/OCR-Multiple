param(
    [string]$Catalogo,
    [string]$Salida
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Catalogo) { $Catalogo = Join-Path $PSScriptRoot 'resultados\indice_documental.sqlite3' }
if (-not $Salida) { $Salida = Join-Path $PSScriptRoot 'resultados_fase2' }
$catalogPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $catalogPython -PathType Leaf)) {
    $catalogPython = Join-Path (Split-Path $PSScriptRoot) 'ocr_masivo\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $catalogPython -PathType Leaf)) {
    throw 'No existe un entorno Python. Cree .venv e instale el proyecto según el README.'
}
if (-not (Test-Path -LiteralPath $Catalogo -PathType Leaf)) {
    throw "No existe el catálogo de fase 1: $Catalogo"
}
$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
& $catalogPython -m analisis_documental_asf.phase2_cli --catalogo $Catalogo --salida $Salida
exit $LASTEXITCODE
