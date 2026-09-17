param(
    [string]$Extraccion,
    [string]$Salida
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $Extraccion) {
    $candidate = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'resultados_fase2') `
        -Filter 'extraccion_fase2.sqlite3' -File -Recurse |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($candidate) { $Extraccion = $candidate.FullName }
}
if (-not $Salida) { $Salida = Join-Path $PSScriptRoot 'resultados_fase3' }
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    $projectPython = Join-Path (Split-Path $PSScriptRoot) 'ocr_masivo\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw 'No existe un entorno Python. Cree .venv e instale el proyecto según el README.'
}
if (-not $Extraccion -or -not (Test-Path -LiteralPath $Extraccion -PathType Leaf)) {
    throw 'No existe una base de extracción de fase 2.'
}
$env:PYTHONPATH = Join-Path $PSScriptRoot 'src'
& $projectPython -m analisis_documental_asf.phase3_cli --extraccion $Extraccion --salida $Salida
exit $LASTEXITCODE
