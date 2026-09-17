param(
    [ValidateRange(1, 8)][int]$PaginasParalelas = 1
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$ocrPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ocrPython -PathType Leaf)) {
    throw 'No existe el entorno .venv. Instale requirements.txt según el README.'
}
$ocrTessdata = Join-Path $PSScriptRoot 'herramientas\tessdata'
if (Test-Path -LiteralPath (Join-Path $ocrTessdata 'spa.traineddata')) {
    $env:TESSDATA_PREFIX = $ocrTessdata
}
$ocrLogs = Join-Path $PSScriptRoot 'logs'
& $ocrPython -m ocr_masivo reanudar --logs $ocrLogs --paginas-paralelas $PaginasParalelas
exit $LASTEXITCODE
