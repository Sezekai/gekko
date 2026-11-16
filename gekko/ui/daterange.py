"""Picking a span of days: quick choices (today, this week, this month, this year, last 7/30 days) or any two dates
with a calendar. Dates are kept as "YYYY-MM-DD"; times() turns them into the start and end of those days."""
from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QWidget

from .widgets import label

PRESETS = [("all", "All time"), ("today", "Today"), ("yesterday", "Yesterday"), ("week", "This week"),
           ("last7", "Last 7 days"), ("month", "This month"), ("last30", "Last 30 days"), ("year", "This year"),
           ("custom", "Between dates…")]


def preset_dates(key: str, today: dt.date | None = None) -> tuple[str | None, str | None]:
    t = today or dt.date.today()
    if key == "today":
        a = b = t
    elif key == "yesterday":
        a = b = t - dt.timedelta(days=1)
    elif key == "week":
        a, b = t - dt.timedelta(days=t.weekday()), t
    elif key == "last7":
        a, b = t - dt.timedelta(days=6), t
    elif key == "month":
        a, b = t.replace(day=1), t
    elif key == "last30":
        a, b = t - dt.timedelta(days=29), t
    elif key == "year":
        a, b = t.replace(month=1, day=1), t
    else:
        return None, None
    return a.isoformat(), b.isoformat()


def times(start: str | None, end: str | None) -> tuple[float | None, float | None]:
    """Seconds since 1970 for the start of the first day and the end of the last (local time)."""
    def ts(d: dt.date) -> float:
        return time.mktime(d.timetuple())
    a = ts(dt.date.fromisoformat(start)) if start else None
    b = ts(dt.date.fromisoformat(end) + dt.timedelta(days=1)) if end else None
    return a, b


def describe(start: str | None, end: str | None) -> str:
    def f(s: str) -> str:
        d = dt.date.fromisoformat(s)
        return f"{d.day} {d.strftime('%b %Y')}"
    if start and end:
        return f(start) if start == end else f"{f(start)} – {f(end)}"
    if start:
        return f"since {f(start)}"
    if end:
        return f"until {f(end)}"
    return "all time"


def date_edit() -> QDateEdit:
    d = QDateEdit()
    d.setCalendarPopup(True)
    d.setDisplayFormat("d MMM yyyy")
    d.setDate(QDate.currentDate())
    d.setMaximumDate(QDate.currentDate().addYears(1))
    d.setFixedWidth(136)
    d.calendarWidget().setFirstDayOfWeek(Qt.Monday)
    d.calendarWidget().setGridVisible(False)
    d.setToolTip("Click the arrow for a calendar")
    return d


class DateRange(QWidget):
    """[preset ▾]  from [date]  to [date] - the dates show for "Between dates…" and follow the preset otherwise."""

    changed = Signal()

    def __init__(self, preset: str = "all", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.preset = QComboBox()
        for k, t in PRESETS:
            self.preset.addItem(t, k)
        self.preset.setMinimumWidth(150)
        lay.addWidget(self.preset)
        self.from_lbl = label("from", "faint")
        self.a = date_edit()
        self.to_lbl = label("to", "faint")
        self.b = date_edit()
        for w in (self.from_lbl, self.a, self.to_lbl, self.b):
            lay.addWidget(w)
        self._quiet = False
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.a.dateChanged.connect(self._dates_changed)
        self.b.dateChanged.connect(self._dates_changed)
        self.set_preset(preset)

    def set_preset(self, key: str) -> None:
        self.preset.setCurrentIndex(max(0, self.preset.findData(key)))
        self._preset_changed()

    def set_dates(self, start: str | None, end: str | None) -> None:
        self._quiet = True
        try:
            self.preset.setCurrentIndex(self.preset.findData("custom" if (start or end) else "all"))
            if start:
                self.a.setDate(QDate.fromString(start, "yyyy-MM-dd"))
            if end:
                self.b.setDate(QDate.fromString(end, "yyyy-MM-dd"))
            self._show_dates()
        finally:
            self._quiet = False

    def _show_dates(self) -> None:
        custom = self.preset.currentData() == "custom"
        for w in (self.from_lbl, self.a, self.to_lbl, self.b):
            w.setVisible(custom)

    def _preset_changed(self, *_):
        k = self.preset.currentData()
        a, b = preset_dates(k)
        self._quiet = True
        if a:
            self.a.setDate(QDate.fromString(a, "yyyy-MM-dd"))
            self.b.setDate(QDate.fromString(b, "yyyy-MM-dd"))
        self._quiet = False
        self._show_dates()
        self.changed.emit()

    def _dates_changed(self, *_):
        if self._quiet:
            return
        if self.a.date() > self.b.date():
            other = self.b if self.sender() is self.a else self.a
            self._quiet = True
            other.setDate(self.sender().date())
            self._quiet = False
        self.changed.emit()

    def dates(self) -> tuple[str | None, str | None]:
        k = self.preset.currentData()
        if k == "all":
            return None, None
        if k == "custom":
            return self.a.date().toString("yyyy-MM-dd"), self.b.date().toString("yyyy-MM-dd")
        return preset_dates(k)

    def key(self) -> str:
        return self.preset.currentData()
