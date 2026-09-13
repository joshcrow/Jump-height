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

Output: `packaging/dist/JumpHeight Sync.app`. Build the `.dmg` to hand to
Nick with:

```
hdiutil create -volname "JumpHeight Sync" -srcfolder "dist/JumpHeight Sync.app" \
    -ov -format UDZO "dist/JumpHeight Sync.dmg"
```

Hand him the `.dmg`, not the bare `.app` — AirDrop/USB/a shared drive can
otherwise flatten the app bundle into a single file, and the quarantine
flag macOS attaches to a `.dmg` on download is what triggers the
right-click → Open flow below (an unquarantined bare `.app` copied over a
LAN share sometimes skips that prompt and just refuses to open silently).

Nothing under `packaging/dist`, `packaging/build`, or `packaging/vendor` is
committed (see `packaging/.gitignore`) — `./build.sh` regenerates all of it.

`./build.sh` never installs anything. Run `packaging/install.sh` yourself,
on the machine it should actually run on — not as part of a build.

## Nick: install and run it

1. Copy `JumpHeight Sync.app` to `/Applications`.
2. **First launch only:** right-click the app → **Open** → **Open** again in
   the dialog. (It's not signed with an Apple Developer ID, so a plain
   double-click refuses to open it the first time — right-click → Open is
   the one-time workaround. After this, it opens normally.)
3. macOS will show a **"Background Items Added"** notification the first
   time it runs — that's normal; it means the app registered to keep
   running in the menu bar. Nothing to do.
4. A `JH` icon appears in the menu bar. Click it → **Set up…** and follow
   the four screens once (Google Drive, then the watch).
5. From then on: plug the puck in to charge. That's the whole job.

To have it start automatically at login, run `packaging/install.sh` on
Nick's Mac (copies the app to `/Applications` and installs the LaunchAgent
at `~/Library/LaunchAgents/com.jumpheight.puckd.plist`). `packaging/
uninstall.sh` reverses it.
