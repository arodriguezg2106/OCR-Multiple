param(
    [string]$Relacion,
    [string]$Extraccion,
    [string]$Salida,
    [decimal]$Objetivo = 45436913.58
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Relacion) {
    $downloadsFolder = Split-Path (Split-Path (Split-Path $PSScriptRoot))
    $candidate = Get-ChildItem -LiteralPath $downloadsFolder -Filter '*pagos.xlsx' -File |
        Where-Object { $_.Name -match '^Relaci.n de pagos\.xlsx$' } |
        Select-Object -First 1
    if ($candidate) { $Relacion = $candidate.FullName }
}
if (-not $Extraccion) {
    $candidate = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'resultados_fase2') `
        -Filter 'extraccion_fase2.sqlite3' -File -Recurse |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($candidate) { $Extraccion = $candidate.FullName }
}
if (-not $Salida) { $Salida = Join-Path $PSScriptRoot 'resultados_fase3' }
if (-not (Test-Path -LiteralPath $Relacion -PathType Leaf)) { throw "No existe la relación de pagos: $Relacion" }
if (-not (Test-Path -LiteralPath $Extraccion -PathType Leaf)) { throw 'No existe una base de extracción de fase 2.' }
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    $projectPython = Join-Path (Split-Path $PSScriptRoot) 'ocr_masivo\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) { throw 'No existe un entorno Python.' }

# La copia con lectura compartida funciona incluso si Excel mantiene abierto el original.
$sourceFolder = Join-Path $Salida 'fuentes'
New-Item -ItemType Directory -Path $sourceFolder -Force | Out-Null
$snapshot = Join-Path $sourceFolder 'Relacion_de_pagos.xlsx'
$inputStream = [System.IO.File]::Open($Relacion, 'Open', 'Read', 'ReadWrite')
try {
    $outputStream = [System.IO.File]::Open($snapshot, 'Create', 'Write', 'None')
    try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
} finally { $inputStream.Dispose() }

$trace = Join-Path $Salida 'trazabilidad.sqlite3'
$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
& $projectPython -m analisis_documental_asf.payments_cli --relacion $snapshot --extraccion $Extraccion `
    --salida $Salida --objetivo $Objetivo --trazabilidad $trace
exit $LASTEXITCODE
