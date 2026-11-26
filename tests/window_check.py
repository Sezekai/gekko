"""Checks on a real Windows window (QT_QPA_PLATFORM=windows on CI): the mini player is small and stays on top,
also when it opens from a maximised or full-screen window, and full screen comes back cleanly.

    python tests/window_check.py"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="gekko-win-"))
os.environ["GEKKO_ROOT"] = str(TMP / "root")
os.environ["GEKKO_DATA"] = str(TMP / "data")
os.environ["GEKKO_NO_PLAYBACK"] = "1"
os.environ["GEKKO_NO_SAFE_MODE"] = "1"


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gekko.ui import theme
    theme.apply(app)
    from gekko.ui.main_window import MainWindow, is_topmost
    MainWindow._start_scan = lambda self: None
    w = MainWindow()
    w.show()

    def wait(s):
        end = time.time() + s
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    wait(1)
    bad = []
    for start in ("normal", "maximised", "full screen"):
        if start == "maximised":
            w.showMaximized()
        elif start == "full screen":
            w.toggle_full_screen()
        wait(1)
        w.toggle_mini()
        wait(1.5)
        top = is_topmost(w)
        print(f"mini from {start}: {w.width()}x{w.height()}, on top: {top}")
        if not top:
            bad.append(f"not on top (from {start})")
        if w.height() > 140:
            bad.append(f"too tall: {w.height()} (from {start})")
        w.mini.pin.setChecked(False)
        wait(0.3)
        if is_topmost(w):
            bad.append("still on top after unpinning")
        w.mini.pin.setChecked(True)
        wait(0.3)
        w.toggle_mini()
        wait(1)
        if is_topmost(w):
            bad.append(f"full window left on top (from {start})")
        if w.isFullScreen():
            w.toggle_full_screen()
            wait(1)
        w.showNormal()
        wait(0.5)
    w.toggle_full_screen()
    wait(1)
    w.toggle_full_screen()
    wait(1)
    print(f"after full screen: {w.width()}x{w.height()}, updates on: {w.updatesEnabled()}, "
          f"title bar: {w.title_bar.isVisible()}")
    if not w.updatesEnabled() or not w.title_bar.isVisible() or w.isFullScreen():
        bad.append("full screen didn't come back cleanly")
    print("problems:", bad or "none")
    os._exit(1 if bad else 0)


if __name__ == "__main__":
    main()
