"""Compact progress display, independent of document path length."""

import re

from rich.progress import Progress
from rich.text import Text


def last_activity(path):
    """Read only the tail, never the whole growing OCR log."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 8192))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
        for line in reversed(lines):
            if "Postprocessing" in line or "optimization ratio" in line:
                return "Validando / preparando PDF final"
            match = re.match(r"\s*(\d+)\s+", line)
            if match:
                return f"Actividad en página {match[1]} (no implica página terminada)"
    except OSError:
        pass
    return "Preparando documento / esperando primera señal del motor"


class BatchProgress(Progress):
    status = "Preparando lote"
    activity = ""

    def get_renderables(self):
        # Names are literal text on their own line: brackets cannot inject Rich markup.
        yield Text(self.status)
        yield from super().get_renderables()
        if self.activity:
            yield Text(self.activity, overflow="fold")
