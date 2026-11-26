"""Draws assets/gekko.png and assets/gekko.ico from the same note as the in-app logo.
Run: QT_QPA_PLATFORM=offscreen python tools/make_icon.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter  # noqa: E402

from gekko.ui.theme import paint_logo  # noqa: E402

app = QGuiApplication([])
N = 1024
img = QImage(N, N, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
g = QLinearGradient(0, 0, N, N)
g.setColorAt(0, QColor("#38d3b8"))
g.setColorAt(1, QColor("#9b8cfa"))
p.setBrush(g)
p.setPen(Qt.NoPen)
r = QRectF(40, 40, N - 80, N - 80)
p.drawRoundedRect(r, 230, 230)
paint_logo(p, r, "#0c0d12", 0.54)
p.end()
png = ROOT / "assets" / "gekko.png"
img.save(str(png))
Image.open(png).save(ROOT / "assets" / "gekko.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
print("made", png)
