import csv
import hashlib
import shutil
import threading
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from ocr_masivo.cli import main
from ocr_masivo.config import Config, load_config
from ocr_masivo.database import Database
from ocr_masivo.inventory import discover, inventory
from ocr_masivo.processor import command, process_one, rejection, run_batch
from ocr_masivo.reporter import COLUMNS, report
from ocr_masivo.utils import batch_lock, sha256
from ocr_masivo.validator import InvalidPDF, inspect_pdf, validate_result


def make_pdf(path, pages=1, text="Texto digital completo 123"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        for _ in range(pages):
            page = doc.new_page()
            page.insert_text((40, 50), text)
        doc.save(path)
    return path


@pytest.fixture
def env(tmp_path):
    c = Config(
        workers=2,
        generar_txt=True,
        **{k: tmp_path / k for k in ("entrada", "salida", "texto", "errores", "logs")},
    ).validate()
    c.entrada.mkdir()
    return c, Database(c.logs / "estado.sqlite3")


class FakeEngine:
    def __init__(self, code=0, message="", pages=None):
        self.calls = 0
        self.code, self.message, self.pages = code, message, pages

    def run(self, config, source, target, stdout, stderr, stop):
        self.calls += 1
        assert target.name.endswith(".partial.pdf")
        stdout.write_text("mock stdout", encoding="utf-8")
        stderr.write_text(self.message, encoding="utf-8")
        if self.pages is None:
            shutil.copyfile(source, target)
        else:
            target.unlink()
            make_pdf(target, self.pages)
        return self.code


def test_recursive_unicode_and_same_names(env):
    c, db = env
    originals = [make_pdf(c.entrada / sub / "José 漢字 espacio.PDF") for sub in ("uno", "dos/árbol")]
    (c.entrada / "ignorar.txt").write_text("x")
    hashes = [sha256(p) for p in originals]
    assert len(list(discover(c.entrada))) == 2
    rows = run_batch(c, db, engine=FakeEngine())
    assert all(r["estado"] == "completed" for r in rows)
    for r in rows:
        assert Path(r["archivo_resultado"]).relative_to(c.salida).as_posix() == r["ruta_relativa"]
        assert "Texto digital completo" in Path(r["archivo_texto"]).read_text(encoding="utf-8")
    assert hashes == [sha256(p) for p in originals]


def test_hash(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(b"abc")
    assert sha256(p) == hashlib.sha256(b"abc").hexdigest()


def test_resume_skip_and_missing_or_corrupt_results(env):
    c, db = env
    make_pdf(c.entrada / "a.pdf")
    engine = FakeEngine()
    row = inventory(c, db)[0]
    row["estado"] = "processing"
    db.save(row)
    assert run_batch(c, db, engine=engine)[0]["estado"] == "completed"
    assert run_batch(c, db, engine=engine)[0]["estado"] == "already_completed"
    assert engine.calls == 1
    (c.salida / "a.pdf").write_bytes(b"corrupt")
    assert run_batch(c, db, engine=engine)[0]["estado"] == "completed"
    (c.texto / "a.txt").unlink()
    assert run_batch(c, db, engine=engine)[0]["estado"] == "completed"
    assert engine.calls == 3


def test_changed_original(env):
    c, db = env
    source = make_pdf(c.entrada / "a.pdf")
    engine = FakeEngine()
    first = run_batch(c, db, engine=engine)[0]
    source.unlink()
    make_pdf(source, pages=2)
    second = run_batch(c, db, engine=engine)[0]
    assert second["estado"] == "completed"
    assert second["hash_original"] != first["hash_original"]
    assert second["paginas_resultado"] == 2


@pytest.mark.parametrize("password", ["", "secret"])
def test_encrypted(env, password):
    c, db = env
    original = make_pdf(c.entrada / "base.pdf")
    with pikepdf.open(original) as pdf:
        pdf.save(c.entrada / "cifrado.pdf", encryption=pikepdf.Encryption(owner="owner", user=password, R=6))
    with pytest.raises(InvalidPDF) as exc:
        inspect_pdf(c.entrada / "cifrado.pdf")
    assert exc.value.state == "encrypted"


def test_signed(env):
    c, db = env
    original = make_pdf(c.entrada / "base.pdf")
    with pikepdf.open(original) as pdf:
        sig = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Sig"), ByteRange=[0, 10, 20, 10]))
        pdf.Root.AcroForm = pikepdf.Dictionary(
            Fields=[pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name("/Sig"), T="Signature", V=sig))]
        )
        pdf.save(c.entrada / "firmado.pdf")
    rows = run_batch(c, db, engine=FakeEngine())
    assert next(r for r in rows if r["nombre"] == "firmado.pdf")["estado"] == "signed"
    assert (c.errores / "firmado.pdf.error.json").exists()
    assert not (c.salida / "firmado.pdf").exists()


@pytest.mark.parametrize(
    "code,message,state",
    [
        (8, "", "encrypted"),
        (2, "Digitally signed PDF", "signed"),
        (3, "missing dependency", "failed"),
        (7, "bad", "failed"),
    ],
)
def test_error_codes_and_cleanup(env, code, message, state):
    c, db = env
    source = make_pdf(c.entrada / "a.pdf")
    before = sha256(source)
    row = run_batch(c, db, engine=FakeEngine(code, message))[0]
    assert row["estado"] == state
    assert row["codigo_salida"] == code
    assert not list(c.salida.glob("*.partial.pdf"))
    assert sha256(source) == before
    assert (c.errores / "a.pdf.error.json").exists()
    assert rejection(code, message) == state


def test_page_count_and_atomic_publication(env, monkeypatch):
    import ocr_masivo.processor as processor

    c, db = env
    make_pdf(c.entrada / "a.pdf", pages=2)
    replaced = []
    real_replace = processor.os.replace

    def checked_replace(source, target):
        if str(source).endswith(".partial.pdf"):
            validate_result(source, 2)
            assert not Path(target).exists()
            replaced.append(target)
        real_replace(source, target)

    monkeypatch.setattr(processor.os, "replace", checked_replace)
    assert run_batch(c, db, engine=FakeEngine(pages=1))[0]["estado"] == "validation_failed"
    assert not replaced
    c.reintentar_fallidos = True
    assert run_batch(c, db, engine=FakeEngine())[0]["estado"] == "completed"
    assert len(replaced) == 1


def test_safe_cleanup_resume(env):
    c, db = env
    source = make_pdf(c.entrada / "original.pdf")
    row = inventory(c, db)[0]
    row.update(estado="processing", partial_pdf=str(source))
    db.save(row)
    run_batch(c, db, engine=FakeEngine())
    assert source.exists()


def test_csv(env):
    c, db = env
    make_pdf(c.entrada / "á.pdf")
    rows = run_batch(c, db, engine=FakeEngine())
    path, summary = report(c, db, rows)
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS
        assert list(reader)[0]["ruta_relativa"] == "á.pdf"
    assert summary["completados"] == 1


def test_single_failure_does_not_stop_batch_and_retry(env):
    c, db = env
    (c.entrada / "bad.pdf").write_bytes(b"not a pdf")
    make_pdf(c.entrada / "good.pdf")
    rows = run_batch(c, db, engine=FakeEngine())
    assert {r["estado"] for r in rows} == {"validation_failed", "completed"}
    engine = FakeEngine()
    run_batch(c, db, only_failed=True, engine=engine)
    assert engine.calls == 0  # invalid PDF is rejected before invoking OCR; completed PDF is untouched


def test_options_and_config(env, tmp_path):
    c, _ = env
    args = command(c, Path("original con espacio.pdf"), Path("temp.partial.pdf"))
    assert args[args.index("--jobs") + 1] == "2"
    assert args[args.index("--mode") + 1] == "skip"
    assert "--invalidate-digital-signatures" not in args
    assert "--clean-final" not in args
    assert "--remove-background" not in args
    with pytest.raises(ValueError):
        Config(entrada=tmp_path, salida=tmp_path / "nested").validate()
    toml = tmp_path / "test.toml"
    toml.write_text('[ocr]\nidioma="spa+eng"\n', encoding="utf-8")
    assert load_config(toml).idioma == "spa+eng"


def test_lock_and_cli(env):
    c, _ = env
    with batch_lock(c.logs):
        with pytest.raises(RuntimeError):
            with batch_lock(c.logs):
                pass
    assert main(["inventariar", "--entrada", str(c.entrada), "--logs", str(c.logs)]) == 0


def test_python_exception_is_recorded(env):
    c, db = env
    make_pdf(c.entrada / "a.pdf")

    class BrokenEngine:
        def run(self, *args):
            raise RuntimeError("synthetic crash")

    row = inventory(c, db)[0]
    result = process_one(c, db, row, BrokenEngine(), threading.Event())
    assert result["estado"] == "failed"
    assert "RuntimeError" in (c.errores / "a.pdf.error.json").read_text(encoding="utf-8")


def test_bounded_parallelism(env):
    c, db = env
    for i in range(6):
        make_pdf(c.entrada / f"{i}.pdf")
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = maximum = 0

    class ParallelEngine(FakeEngine):
        def run(self, *args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                barrier.wait(timeout=20)
                return super().run(*args)
            finally:
                with lock:
                    active -= 1

    rows = run_batch(c, db, engine=ParallelEngine())
    assert all(r["estado"] == "completed" for r in rows)
    assert maximum == c.workers


def test_stop_does_not_schedule_more_documents(env, monkeypatch):
    from ocr_masivo import processor

    c, db = env
    for i in range(5):
        make_pdf(c.entrada / f"{i}.pdf")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(processor, "wait", interrupt)
    engine = FakeEngine()
    rows = run_batch(c, db, engine=engine)
    assert engine.calls == 2
    assert sum(r["estado"] == "pending" for r in rows) == 3


def test_txt_publication_failure_is_recoverable(env, monkeypatch):
    from ocr_masivo import processor

    c, db = env
    make_pdf(c.entrada / "a.pdf")
    real_replace = processor.os.replace

    def fail_txt(source, target):
        if Path(target).suffix == ".txt":
            raise OSError("Synthetic disk failure")
        real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(processor.os, "replace", fail_txt)
        assert run_batch(c, db, engine=FakeEngine())[0]["estado"] == "failed"
    assert (c.salida / "a.pdf").exists()
    assert not (c.texto / "a.txt").exists()
    assert not list(c.texto.glob("*.partial.txt"))
    assert run_batch(c, db, only_failed=True, engine=FakeEngine())[0]["estado"] == "completed"


def test_unregistered_output_is_preserved(env):
    c, db = env
    make_pdf(c.entrada / "a.pdf")
    c.salida.mkdir()
    target = make_pdf(c.salida / "a.pdf", text="Unrelated existing document")
    digest = sha256(target)
    engine = FakeEngine()
    assert run_batch(c, db, engine=engine)[0]["estado"] == "validation_failed"
    assert sha256(target) == digest
    assert engine.calls == 0


def test_diagnostic_without_external_executables(env, monkeypatch):
    from ocr_masivo import cli

    c, _ = env
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: False if p.name == "tesseract.exe" else real_exists(p))

    class Result:
        returncode = 0
        stdout = "17.11.0"
        stderr = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Result())
    assert cli.diagnostic(c) == 1
