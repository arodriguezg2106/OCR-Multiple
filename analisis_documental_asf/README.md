# Análisis Documental ASF

Proyecto local separado de OCR Masivo. La fase 1 construye una relación navegable de los PDF por carpeta, mes y tipo documental. Lee la capa de texto de los PDF con OCR y nunca modifica originales ni resultados.

## Resultados

- `catalogo_documentos.csv`: una fila por PDF.
- `resumen_por_mes.csv`: documentos, páginas, cobertura y revisiones por carpeta mensual.
- `resumen_por_tipo.csv`: clasificación principal y niveles de confianza.
- `documentos_sin_clasificar.csv`: casos sin evidencia suficiente o con empate.
- `documentos_con_advertencias.csv`: OCR fallido o PDF que no se pudo leer.
- `indice_documental.sqlite3`: texto por página e índice FTS5 para búsquedas y la futura trazabilidad.
- `catalogo_documental.html`: informe local con filtros y enlaces a los PDF.

La clasificación es explicable y multietiqueta. El tipo principal resume el expediente; `tipos_contenidos` registra componentes detectados, por ejemplo póliza, cheque y CFDI dentro del mismo PDF. Los casos dudosos no se fuerzan y quedan para revisión.

La primera aplicación al lote de 2021 catalogó 538 PDF y 13,729 páginas sin advertencias. Consulte el [resultado agregado de la fase 1](docs/RESULTADOS_FASE_1.md). Los informes detallados permanecen sólo en este equipo dentro de `resultados`.

## Ejecutar

Desde PowerShell:

```powershell
cd D:\arodriguezg\Downloads\OCR_PDFS\IA-ASF\analisis_documental_asf
.\ejecutar_catalogo.ps1
```

El primer recorrido extrae texto de todos los PDF terminados. Las ejecuciones posteriores reutilizan los documentos cuyo hash no cambió. Para una instalación independiente:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

También puede indicar otras rutas:

```powershell
.\ejecutar_catalogo.ps1 -BaseOCR 'D:\Lote\logs\estado.sqlite3' -Salida 'D:\Catalogo'
```

## Límites

La clasificación y las fechas dependen del texto OCR. Un resultado con confianza alta significa que varias reglas coincidieron; no certifica el contenido ni una operación contable. Los CSV conservan rutas y advertencias para revisar el PDF. La base y los resultados contienen información del expediente y están excluidos de Git.

La fase 2 utilizará el índice por página para extraer importes, folios, cuentas, beneficiarios y referencias. La fase 3 relacionará documentos conservando evidencia y niveles de confianza.
