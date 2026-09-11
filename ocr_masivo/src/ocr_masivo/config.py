import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Config:
    entrada: Path = Path("entrada")
    salida: Path = Path("salida_pdf")
    texto: Path = Path("texto")
    errores: Path = Path("errores")
    logs: Path = Path("logs")
    idioma: str = "spa"
    modo: str = "skip"
    rotacion: bool = True
    inclinacion: bool = True
    tipo_salida: str = "pdf"
    optimizacion: int = 1
    timeout_pagina: float = 180
    workers: int = 2
    megapixeles: float | None = None
    generar_txt: bool = True
    reintentar_fallidos: bool = False

    def validate(self):
        for name in ("entrada", "salida", "texto", "errores", "logs"):
            setattr(self, name, Path(getattr(self, name)).resolve())
        paths = [self.entrada, self.salida, self.texto, self.errores, self.logs]
        for i, a in enumerate(paths):
            for b in paths[i + 1 :]:
                if a == b or a in b.parents or b in a.parents:
                    raise ValueError(
                        "Entrada, salida, texto, errores y logs deben ser carpetas separadas, sin anidarse."
                    )
        if self.modo not in {"skip", "redo", "force"} or self.tipo_salida not in {"pdf", "pdfa"}:
            raise ValueError("Modo o tipo de salida inválido.")
        if self.workers < 1 or self.timeout_pagina <= 0 or self.optimizacion not in range(4):
            raise ValueError("Workers y timeout deben ser positivos; optimización debe estar entre 0 y 3.")
        if self.megapixeles is not None and self.megapixeles <= 0:
            raise ValueError("Megapíxeles debe ser positivo.")
        if self.modo == "redo" and self.inclinacion:
            raise ValueError("El modo redo requiere --no-inclinacion por incompatibilidad de OCRmyPDF.")
        return self

    def serialize(self):
        return {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}


def load_config(path=None, overrides=None):
    values = {}
    if path:
        path = Path(path).resolve()
        with path.open("rb") as f:
            values = tomllib.load(f).get("ocr", {})
        for name in ("entrada", "salida", "texto", "errores", "logs"):
            if name in values:
                values[name] = path.parent / values[name]
    values.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return Config(**values).validate()
