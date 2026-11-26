Gekko 1.2.4: mini player fixes, and a cleaner way out of full screen.

- The mini player is small again: the hidden full window was keeping it tall, which spread the title, artist and buttons apart.
- "Stay on top of other windows" works. It never did on Windows: the "on top" request reached Windows garbled (a 64-bit handle passed as a 32-bit number), so it was ignored. It's now checked on a real Windows window every build, from a normal, maximised and full-screen window, and Gekko puts it back if Windows ever drops it while the pin is on.
- Clicking the playing song's title (to open its album) no longer gives "IndexError: No item with that key".
- Leaving (and entering) full screen no longer shows stretched or unpainted edges while the window resizes: it draws once at the new size, and anything not yet drawn shows the theme's background instead of white.

Replace the App folder in E:\Pictures\Gekko with the one in Gekko.zip.
