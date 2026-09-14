import io

from rich.console import Console
from rich.progress import BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from ocr_masivo.progress import BatchProgress, last_activity


def test_long_filename_does_not_hide_timers_or_interpret_markup():
    output = io.StringIO()
    console = Console(file=output, width=90, color_system=None)
    progress = BatchProgress(
        TextColumn("{task.description}"),
        BarColumn(bar_width=20),
        TextColumn("Tiempo:"),
        TimeElapsedColumn(),
        TextColumn("Restante aprox.:"),
        TimeRemainingColumn(),
        console=console,
    )
    progress.add_task("Lote", total=538)
    progress.activity = "Archivo: " + "carpeta/" * 30 + "[red]nomina.pdf"
    for renderable in progress.get_renderables():
        console.print(renderable)
    text = output.getvalue()
    assert "Tiempo:" in text and "Restante aprox.:" in text
    assert "[red]nomina.pdf" in text
    assert text.index("Tiempo:") < text.index("Archivo:")


def test_activity_is_not_reported_as_completed_page(tmp_path):
    path = tmp_path / "ocr.log"
    path.write_text("  19 Orientación local: alternativas\n", encoding="utf-8")
    assert "página 19" in last_activity(path)
    assert "no implica página terminada" in last_activity(path)
    path.write_text("Postprocessing...\n", encoding="utf-8")
    assert "PDF final" in last_activity(path)
    assert "Preparando" in last_activity(tmp_path / "missing.log")
