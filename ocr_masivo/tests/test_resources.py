import pytest

from ocr_masivo.config import Config, load_config
from ocr_masivo.processor import command
from ocr_masivo.resources import page_jobs


@pytest.mark.parametrize(
    "free,workers,cpus,expected",
    [
        (None, 1, 12, 1),
        (1.8, 1, 12, 1),
        (2.4, 1, 12, 2),
        (2.4, 2, 12, 1),
        (8, 1, 1, 1),
        (8, 1, 12, 2),
    ],
)
def test_memory_and_cpu_bound_page_parallelism(free, workers, cpus, expected):
    assert page_jobs(2, workers, None if free is None else free * 2**30, cpus) == expected


def test_parallelism_changes_no_ocr_quality_flags():
    one = command(Config(paginas_paralelas=1), "source.pdf", "target.pdf")
    two = command(Config(paginas_paralelas=2), "source.pdf", "target.pdf")
    one[one.index("--jobs") + 1] = "2"
    assert one == two
    assert two[two.index("--optimize") + 1] == "0"
    assert two[two.index("--oversample") + 1] == "300"


def test_page_parallelism_config_roundtrip_and_validation(tmp_path):
    path = tmp_path / "test.toml"
    path.write_text("[ocr]\npaginas_paralelas = 3\n", encoding="utf-8")
    assert load_config(path).serialize()["paginas_paralelas"] == 3
    with pytest.raises(ValueError, match="paralelas"):
        load_config(path, {"paginas_paralelas": 0})
