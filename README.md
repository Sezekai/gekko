# Gekko

A music player for the songs on your own drives, built on Nirvash's look.

## What it does

**Your library**

- **Music folders** anywhere, on any drive, as many as you like (Folders > Add folder). Album folders and folders of
  random songs both work. Every time Gekko opens it checks them: new songs are added, changed ones read again, and
  ones that disappeared are marked "can't be found" (songs on a drive that isn't connected just wait).
- **Never added again:** songs you take out of the library (Delete) and subfolders you mark "Don't add this folder"
  (right-click in Folders) are skipped by every later check. Settings > Music folders brings them back.
- **Files moved outside Gekko** are recognised by size and length, so they keep their ratings and plays.
- **Browse** by Songs, Albums, Artists, Genres, Folders and Playlists, as a list or a cover-art grid (tile size:
  slider or Ctrl + mouse wheel). Every column sorts. Narrow windows drop the less important columns.
- **Filters** that combine, each with its opposite: favourite, rating (half stars), played, lyrics, identified,
  genre (any of / none of), length, release year, date added. Active filters show as pills; × clears one.
- **Artist profiles:** photo, who they are (Wikipedia, found through MusicBrainz; Last.fm with a free key fills the
  gaps), and every song and album they're on, including as a guest ("feat.").
- **Ratings** from ½ to 5 stars (click a star's left half for a half star, or `[` `]`) and a **favourite** heart (V).
- **Playlists:** your own (drag songs onto one, drag rows to reorder) and **smart playlists** built from filters
  ("4★ and up J-Pop under 4 minutes, most played first, at most 50"). Built in: Favourites, Top rated, Recently
  added, Most played, Played lately, Never played.

**Playing**

- **Music keeps playing on every screen.** The **Now playing panel** on the right shows the cover, the song's
  details, lyrics that follow along, a bit about the artist and the queue. Resize it, fold its sections, or hide it.
- **The player bar** along the bottom: the song (click the title for its album, the artist for their profile),
  favourite, shuffle, previous, play, next, repeat, the **sound wave** to click or drag through (Nirvash's look),
  sleep timer, the panel, the full-screen player, the mini player and volume.
- **Full-screen player** (P): big cover, details, controls, lyrics in the middle, and the queue, the artist and the
  song's full details beside them.
- **Mini player** (Ctrl+M): the window shrinks to the cover and the controls; drag it anywhere, keep it on top.
- **The queue:** Play next (N), Add to queue (Q), drag to reorder, drop songs onto it from any list, Delete to take
  songs out. It carries on after an album ends and is remembered when Gekko closes.
- **Play orders** for any album, artist, playlist, folder or list: in order, shuffled, reversed, shortest or
  longest first, newest or oldest released first, highest rated, most or least played, newest added.
- **Gapless** playback, **even volume** (each song's loudness is measured once; the files aren't changed), repeat
  the queue or one song, sleep timer, and picking up where you left off.
- **Lyrics:** timed lyrics light up line by line (click a line to jump there); plain lyrics scroll.

**Finding out what songs are, and tidying them**

- Songs with no title or artist are **identified from their sound** in the background (Shazam; AcoustID and
  AudioTag as optional backups; "Try harder" also catches sped-up or slowed-down edits). Songs that nothing
  recognises still get a name from their file name when it looks like "Artist - Title".
- Every song gets what it's missing: **album, year, genre, cover art and lyrics** (iTunes, MusicBrainz, LRCLIB).
  The song that's playing is looked up first. Progress is in the title bar; it can be paused.
- **Edit info** (E) for one song or many at once.
- **Tidy up** reviews what would change before anything does: rename to "Artist - Title", write the info into the
  tags (adding the cover to files without one), and sort into `E:\Music\Artist\Album` (`Artist\Singles` for songs
  with no album). Leave out single songs, then Apply. The audio is never touched, nothing is overwritten, and every
  change is in Log > File moves with Undo, which puts the file back *and* its old tags (Ctrl+Z undoes the last
  batch). The song that's playing waits for the next time.

**Around the app:** one window only, a log (L) that opens on errors, Settings > Storage (sizes, move, clear the
cache), Settings > Reset one part at a time, crash logs and safe mode, the Look screen with themes shared across
the suite (now with outline and highlight colours).

## Keys

| Key | Does |
| --- | --- |
| K | Keyboard shortcuts (a popup over any screen) |
| / or Ctrl+F | Search this screen's list |
| F or F11 | Full screen |
| H | Hide the panels (H or Esc brings them back) |
| L / M / S | Log / show or hide the rail / Settings |
| Ctrl+Z | Undo the last Tidy up |
| Esc | Close what's open, innermost first |
| Space | Play / pause |
| ← → PgUp PgDn | Previous / next song |
| Shift + ← → | Back / on 10 seconds |
| - / = , 0 | Volume down / up, mute |
| X / R | Shuffle / repeat (off, queue, song) |
| P / Ctrl+M | Full-screen player / mini player |
| J | Jump to the playing song's album |
| Enter | Play the chosen song |
| Q / N | Add to queue / play next |
| V | Favourite |
| [ ] | Rating down / up half a star |
| E | Edit info |
| Delete / Shift+Delete | Remove from library / send to the Recycle Bin (both ask first) |
| Home End | First / last in the list |

The song keys act on the songs chosen in the list, or on the song that's playing when none are chosen.

## Install

1. Download `Gekko.zip` from the newest numbered release:
   https://github.com/Sezekai/gekko/releases/latest/download/Gekko.zip (older versions stay under **Releases**).
2. Extract it into `E:\Pictures` so the program sits at `E:\Pictures\Gekko\App\Gekko.exe`.
3. Run `Gekko.exe` and add your music folders.

Updating = replace the `App` folder. Nothing else is touched.

## Where things live

| Folder | Holds |
| --- | --- |
| `E:\Pictures\Gekko\App` | the program (replaced by updates) |
| `E:\Pictures\Gekko\Cache` | covers copied out of songs, downloaded covers and artist photos, sound waves (safe to clear) |
| `%LOCALAPPDATA%\Gekko` | settings, the library database (ratings, plays, playlists, the move log), themes, added fonts, logs (can be moved in Settings > Storage) |
| your music folders | your songs, wherever they are; `E:\Music` is where Tidy up sorts them (changeable) |

## Build from source

```
pip install -r requirements.txt pyinstaller
python build/fetch_tools.py        # FFmpeg, libmpv, fpcalc -> bin/
pyinstaller build/gekko.spec
```

Tests: `python -m pytest tests` (generated songs only), `python tests/mpv_check.py` (real gapless playback through
libmpv), `python tests/screenshots.py <folder>` for screenshots of every screen at 1100, 1440 and 1920 wide.

Docs: PRODUCT.md (purpose and what differs from the shared standards), DESIGN.md and STANDARDS.md (Gekko's copies).

Bundled tools: FFmpeg (BtbN GPL build), libmpv (shinchiro), fpcalc (Chromaprint). Lookups: Shazam (shazamio),
AcoustID, AudioTag.info, iTunes Search, MusicBrainz, LRCLIB, Wikipedia/Wikidata, Deezer, Last.fm.
