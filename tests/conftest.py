import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
_TMP = Path(tempfile.mkdtemp(prefix="gekko-tests-"))
os.environ.setdefault("GEKKO_ROOT", str(_TMP / "root"))
os.environ.setdefault("GEKKO_DATA", str(_TMP / "data"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GEKKO_NO_PLAYBACK"] = "1"
os.environ["GEKKO_NO_SAFE_MODE"] = "1"
os.environ["GEKKO_AUTO_DIALOGS"] = "accept"

import pytest  # noqa: E402


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """A new, empty data folder and database for one test."""
    monkeypatch.setenv("GEKKO_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("GEKKO_ROOT", str(tmp_path / "root"))
    from gekko.core import db, paths, settings
    paths.reset_data_dir_cache()
    db.reset_db_cache()
    settings._settings = None
    yield tmp_path
    db.reset_db_cache()
    settings._settings = None


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gekko.ui import theme
    theme.apply(app)
    return app
