"""Settings, stored as JSON in the data folder (survives updates)."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "folders": {
        # Empty = default location under the root folder.
        "root": "",
        "cache": "",
    },
    "library": {
        "scan_on_start": True,      # look for new and removed songs every time Gekko opens
        "sort_root": "E:\\Music",   # where Tidy up sorts songs into Artist\\Album folders
    },
    "identify": {
        "auto": True,               # look songs up in the background after each scan
        "shazam": True,
        "acoustid_key": "",
        "audiotag_key": "",         # third backup: AudioTag.info, 3 hours of audio a month free
        "lastfm_key": "",           # optional: artist bios Wikipedia doesn't have
        "musicbrainz": True,
        "itunes": True,
        "lyrics": True,
        "covers": True,
        "artists": True,
    },
    "tidy": {
        "rename": True,
        "tags": True,
        "sort": True,
        "cover": True,              # put the cover art into the file's tags too
    },
    "playback": {
        "gapless": True,
        "level": True,              # play every song at a similar loudness (the files aren't changed)
        "target": -14.0,
        "resume": True,             # come back to the queue and song you left off at
        "count_after": 0.5,         # a listen counts once this much of a song has played
    },
    "ui": {
        "volume": 80,
        "geometry": "",
        "log_open": False,
        "rail_hidden": False,
        "side_open": True,
        "side_width": 320,
        "mini_on_top": True,
        "mini_pos": [],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.data_dir() / "settings.json")
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            self.data = _merge(DEFAULTS, raw)
        except FileNotFoundError:
            self.data = copy.deepcopy(DEFAULTS)
        except Exception:
            # A broken settings file must never stop the app; keep a copy for inspection.
            try:
                self.path.replace(self.path.with_suffix(".broken.json"))
            except Exception:
                pass
            self.data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
            tmp.replace(self.path)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            cur = self.data
            for part in parts[:-1]:
                if not isinstance(cur.get(part), dict):     # a value where a group is wanted: the group wins
                    cur[part] = {}
                cur = cur[part]
            cur[parts[-1]] = value
        if save:
            self.save()

    def reset(self) -> None:
        with self._lock:
            self.data = copy.deepcopy(DEFAULTS)
        self.save()

    # ---- folders -------------------------------------------------------
    def root(self) -> Path:
        r = self.get("folders.root")
        return Path(r) if r else paths.default_root()

    def folder(self, key: str) -> Path:
        custom = self.get(f"folders.{key}")
        if custom:
            p = Path(custom)
        else:
            defaults = {"cache": self.root() / "Cache"}
            p = defaults[key]
        return paths.ensure(p)

    def cache(self, sub: str) -> Path:
        return paths.ensure(self.folder("cache") / sub)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
