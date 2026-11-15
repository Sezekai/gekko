# Gekko: product

## Who it's for

One person (the owner) with a lot of music spread over several folders and drives on a Windows laptop: tidy album
folders, folders of random downloads, and songs with no names or tags at all. Everything runs on that PC; nothing
about the library is uploaded except the short audio samples and song names sent to the lookup services.

## Purpose

- Play the owner's own music well: gapless, with a queue, play orders, shuffle and repeat, a sleep timer, and even
  volume from song to song.
- Browse it every useful way: songs, albums, artists (with a profile: bio, photo, every song), genres, folders,
  playlists (own and smart), as lists or cover grids, with filters that each have an opposite.
- Find out what unknown songs are (Shazam, AcoustID, AudioTag) and fill in what's missing (album, year, genre,
  cover, lyrics), then, after review, write it into the files: names, tags and `Artist\Album` folders.
- Keep the music going whatever screen is open, with the playing song always in view (the Now playing panel, the
  full-screen player or the mini player).

## Constraints

- The song files are the owner's. Nothing changes on disk until Tidy up is reviewed and applied, the audio is never
  re-encoded, nothing is overwritten, and every rename, move or tag change can be undone (the old tags come back).
- Deleting a file always goes to the Recycle Bin; "Remove from library" never touches the disk.
- Works offline; lookups simply wait for the internet.
- The repo never holds the owner's music, file names or database; tests use generated tones.

## Never in it

- Accounts, sync, telemetry, ads, streaming services.
- Re-encoding or "improving" songs.
- Uploading whole songs anywhere.

## Decided for Gekko (differs from, or chooses within, the shared standards)

| Standard | Gekko | Why |
| --- | --- | --- |
| DESIGN.md default look (Obsidian, Lime → Teal) | Nirvash's look: "Starfield, see-through" with Teal → Violet, the colourful waveform and stars | The owner asked for Gekko to build on how Nirvash looks |
| DESIGN.md: category colours only as dots | Playlists and genres use them as tile tints and icons | They're groups the owner makes or browses by; told apart at a glance |
| STANDARDS.md: tick boxes for picking items | Not in lists (click, Shift, Ctrl, as in Nirvash). Tidy up's review has a "Do" column | Lists are for playing; the review needs a per-song yes/no |
| STANDARDS.md: one portable .exe | A zipped folder unpacking to `Gekko\App` | libmpv and FFmpeg are large and start faster unpacked |
| STANDARDS.md: ↑ ↓ volume in players | Volume is `-` and `=`, mute `0` (as in Nirvash) | ↑ ↓ move through the lists |
| STANDARDS.md: ← → previous / next | Previous / next **song** on every screen; Shift + ← → skip 10 s | The player is always on |
| Keys of its own | Q queue, N play next, V favourite, `[` `]` rating, E edit info, X shuffle, R repeat, P full-screen player, J jump to the playing album, Ctrl+M mini player, Shift+Delete Recycle Bin | Music-player actions, on letters the suite table doesn't use |
| Keys work only inside the app | No global hotkeys or system media keys | Owner's choice |
| Watching folders (App chooses) | A full check every time Gekko opens, plus Check now | Owner's choice; folders on E: may not be connected while it runs |
| Storage format (App chooses) | SQLite (`gekko.db`) | Libraries of tens of thousands of songs |
| "Remember done and skip" | Songs removed from the library and folders marked "Don't add" are never added again; Settings brings them back | Owner's rule |
| Original files never edited in place | Tidy up writes tags into the files themselves | The owner chose renaming *and* tagging; it's reviewed first, the audio isn't touched and Undo restores the old tags |
| Sorting | Into `E:\Music\Artist\Album` (changeable), songs without an album in `Artist\Singles` | Owner's choice |
| Count badges | Tidy up shows how many songs are waiting | Work waiting for review |
| Progress | Scans and Tidy up use the status bar queue; lookups and loudness measuring run in their own background worker with a title-bar pill (bar, %, counts, Pause) | Lookups are rate-limited and long; they must never hold up a Tidy up |

## Release

Every version gets a numbered release (`v1.0.0` …) built and tested on GitHub Actions. The newest is always at
https://github.com/Sezekai/gekko/releases/latest/download/Gekko.zip
