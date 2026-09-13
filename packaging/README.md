# Building and installing JumpHeight Sync

## Josh: build the app

Requires the python.org universal2 build at
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`, with
py2app and rumps already installed into it (they are, on this Mac).

```
cd packaging
./build.sh
```

This vendors a universal2 `rclone` binary into `packaging/vendor/` (built
locally with `lipo -create` from rclone's two official per-arch downloads —
see `fetch_rclone.sh`'s own comment for why: rclone.org does not currently
publish a single "macOS universal" zip), runs `setup.py py2app
--arch=universal2`, then `fix_universal_libs.sh` — a couple of garth's own
dependencies (`psutil`, `pydantic_core`) only ship arch-specific wheels, and
that script patches in the missing x86_64 slice from PyPI's own published
build, verified by sha256, so the app is truly universal2 top to bottom.

Output: `packaging/dist/JumpHeight Sync.app` and `packaging/dist/JumpHeight
Sync.dmg` (the app plus an Applications shortcut, the ordinary Mac drag
install). The icon is drawn by `icon/make_icon.py` on every build.

Hand him the `.dmg`, not the bare `.app` — AirDrop/USB/a shared drive can
otherwise flatten the app bundle into a single file, and the quarantine
flag macOS attaches to a `.dmg` on download is what triggers the
right-click → Open flow below (an unquarantined bare `.app` copied over a
LAN share sometimes skips that prompt and just refuses to open silently).

Nothing under `packaging/dist`, `packaging/build`, or `packaging/vendor` is
committed (see `packaging/.gitignore`) — `./build.sh` regenerates all of it.

`./build.sh` never installs anything. Opening the built app does (see below),
so don't open it on a Mac that shouldn't be running the agent.

## Nick: install it

1. Open **JumpHeight Sync.dmg** and drag the app onto the Applications folder next to it.
2. Open Applications, **right-click JumpHeight Sync → Open → Open**. Once. (The app is not signed with an Apple Developer ID, so a plain double-click refuses the first time.)
3. A small wing appears in the menu bar and the setup window opens on its own: connect Google Drive, sign in to the watch (or skip), done.

That's it. Opening the app is the install: it registers itself to start at
login and hands off to that copy, so there is nothing else to run. macOS
shows "Background Items Added" once. From then on: plug the puck in to
charge.

Double-clicking the app again later just makes sure it is running. Quit from
the menu stops it until the next login. `packaging/uninstall.sh` removes the
login item entirely; `packaging/reset.sh` also wipes the app's data and the Google connection, to test the first run again (both Josh's tools, not Nick's).
