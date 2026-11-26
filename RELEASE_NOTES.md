Gekko 1.2.2: fixes the lyrics error that kept popping up.

- "IndexError: list index out of range" from the lyrics kept appearing when a song with synced lyrics followed one with fewer lines. The lyrics now work out their layout for the new song before following along. A test reproduces it.
- When MusicBrainz is busy (503 "Service Temporarily Unavailable"), artist and song lookups now wait and ask again instead of giving up straight away.

Replace the App folder in E:\Pictures\Gekko with the one in Gekko.zip.
