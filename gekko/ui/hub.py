"""App-wide signals shared by the screens."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Hub(QObject):
    songChanged = Signal(int)        # one song changed (info, rating, plays…)
    songsReset = Signal()            # many songs changed: reload everything
    artistChanged = Signal(str)      # an artist's profile was looked up
    playlistsChanged = Signal()
    foldersChanged = Signal()
    identifyStatus = Signal(dict)    # pending, current, done, started, busy, paused
    logLine = Signal(str, str)
    toast = Signal(str, str)         # message, kind (info|ok|warn|error)
    movesChanged = Signal()          # the move log changed
    themeChanged = Signal()          # the look changed (Appearance)
    goto = Signal(str, object)       # open a screen: ("artist", name), ("album", (album, artist)), ("genre", g)…
    tidyCount = Signal(int)          # songs waiting in Tidy up


hub = Hub()
