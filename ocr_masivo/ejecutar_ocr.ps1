param(
    [string]$Entrada,
    [string]$Salida,
    [ValidateRange(1, 128)][int]$Workers = 1,
    [ValidateRange(1, 8)][int]$PaginasParalelas = 2
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Entrada) { $Entrada = Read-Host 'Carpeta con PDF originales' }
if (-not $Salida) { $Salida = Read-Host 'Carpeta para PDF con OCR' }
if (-not (Test-Path -LiteralPath $Entrada -PathType Container)) {
    throw 'La carpeta de entrada no existe.'
}
$ocrPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ocrPython)) {
    throw 'Cree el entorno primero: py -m venv .venv; instale requirements.txt según el README.'
}
$ocrTessdata = Join-Path $PSScriptRoot 'herramientas\tessdata'
if (Test-Path -LiteralPath (Join-Path $ocrTessdata 'spa.traineddata')) {
    $env:TESSDATA_PREFIX = $ocrTessdata
}
& $ocrPython -m ocr_masivo procesar --entrada $Entrada --salida $Salida --workers $Workers --paginas-paralelas $PaginasParalelas
exit $LASTEXITCODE
