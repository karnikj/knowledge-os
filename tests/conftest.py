import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.config import Vault  # noqa: E402


@pytest.fixture
def vault(tmp_path) -> Vault:
    v = Vault(tmp_path)
    for d in v.required_dirs():
        d.mkdir(parents=True, exist_ok=True)
    # skutečný prompt z repozitáře (testy legacy analyzéru ho čtou)
    src = ROOT / "99_System" / "prompts" / "transcription_prompt.txt"
    if src.exists():
        (v.prompts / "transcription_prompt.txt").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return v


GOLDEN_DIR = ROOT / "tests" / "golden"
