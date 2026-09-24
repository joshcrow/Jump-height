"""packaging/src/launcher.py — the py2app entry point for "JumpHeight
Sync.app". This is the ONLY new code in the whole bundle: everything the
daemon actually does still lives in tools/puckd/*.py, untouched (the build
brief: do not edit tools/ or web/ or docs/).

Two things this file does, and why each one has to happen HERE rather than
in tools/puckd itself:

  1. Point sys.path at the same repo-relative layout tools/puckd's own
     modules already assume. daemon.py, serial_job.py and flash.py each
     compute their own "REPO" as

         Path(__file__).resolve().parent.parent.parent

     i.e. three directories above wherever the .py file physically sits —
     then read tools/jump (a script with NO .py extension, loaded with
     importlib.machinery.SourceFileLoader, which needs a real path on disk,
     never a zip member) from REPO/tools/jump, and tools/puckd's siblings
     from REPO/tools/puckd/*.

     packaging/setup.py's data_files copy tools/jump and the whole
     tools/puckd/ tree VERBATIM — real, unzipped files, not compiled into
     site-packages.zip — to Contents/Resources/tools/... . So once this
     launcher inserts Contents/Resources/tools onto sys.path before ever
     importing `puckd`, every one of those REPO computations lands on
     Contents/Resources — the exact directory this file just inserted, with
     the path computed once here and re-derived identically, independently,
     by each of those modules. Nothing here duplicates tools/puckd's own
     sys.path lines (daemon.py inserts the same two paths again once it
     loads); that's a harmless redundant insert, not a second source of
     truth — the layout is the one thing this file and daemon.py both read
     off the filesystem, not off each other.

  2. Set PUCKD_RCLONE to the universal2 rclone binary this same build
     bundled at Contents/Resources/rclone (packaging/fetch_rclone.sh).
     tools/puckd/upload.py reads PUCKD_RCLONE, falling back to PATH only if
     it's unset — and Nick's PATH has no rclone on it at all (brew's
     /opt/homebrew/bin/rclone, where it exists, is arm64-only and is not
     bundled). Using setdefault(), not a blind set(), so a developer who
     already exported PUCKD_RCLONE for a bench rehearsal is never overridden
     by the bundled copy.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _resource_path() -> Path:
    # py2app's native launcher stub sets RESOURCEPATH before Python ever
    # starts (py2app/apptemplate/src/main.c) — this is the one env var every
    # py2app app can rely on to find its own Contents/Resources. The
    # fallback (this file's own grandparent-of-grandparent at dev time) only
    # matters for running `python3 packaging/src/launcher.py` directly,
    # unbundled, which the build script never does but a developer might.
    env = os.environ.get("RESOURCEPATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def main() -> None:
    # launchd starts us with no locale: Python's stdio and default file
    # encoding come up ASCII, and the first non-ASCII character printed or
    # logged raises. Never let text encoding be a way to crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
    resources = _resource_path()
    tools_dir = resources / "tools"

    sys.path.insert(0, str(tools_dir))            # `from puckd import daemon`
    sys.path.insert(0, str(tools_dir / "puckd"))   # bare `import garmin`, `import flash`

    rclone_bin = resources / "rclone"
    if rclone_bin.is_file():
        os.environ.setdefault("PUCKD_RCLONE", str(rclone_bin))

    if _install_and_hand_off(resources):
        return                                     # launchd's copy takes over
    from puckd import daemon  # noqa: E402  (path must be set up first)
    daemon.main()


# ------------------------------------------------------- self-install
#
# Nick installs a Mac app by dragging it to Applications and opening it.
# So opening it IS the install: every Finder launch writes
# ~/Library/LaunchAgents/com.jumpheight.puckd.plist pointing at this very
# bundle, loads it (launchd starts the real, long-running copy with
# --launchd on its argv), and this Finder-started copy exits. Opened from
# inside the .dmg, the bundle is copied to /Applications first so the
# agent never points at a volume that gets ejected.
#
# KeepAlive is SuccessfulExit=false: a crash restarts it, "Quit" in the
# menu (exit 0) does not, and RunAtLoad brings it back at the next login.

LABEL = "com.jumpheight.puckd"
LAUNCHD_FLAG = "--launchd"
# Where a bundle opened from a mounted .dmg installs itself to. A module
# constant, not an inline literal, so the install can be exercised against a
# scratch directory in tools/tests/test_puckd_launcher.py -- the copy/rename
# ordering below is the part that decides whether a second open can destroy a
# working install, and it is not something to find out on Nick's Mac.
APPLICATIONS_DIR = Path("/Applications")
# The mounted-.dmg prefix that means "this bundle is not installed yet".
# Named for the same reason APPLICATIONS_DIR is: so the install path can be
# rehearsed off a scratch directory instead of only on a rider's Mac.
DMG_PREFIX = "/Volumes/"


def _bundle_path(resources: Path) -> Path:
    return resources.parent.parent                 # .../JumpHeight Sync.app


def _copy_to_applications(bundle: Path) -> Path:
    """Copy the .dmg's bundle to /Applications, NEVER by deleting the copy
    that is already there first.

    The old order was rmtree(dest) then copytree(). Two ways that bites, and
    both are ordinary rider behaviour, not edge cases:

      * the launchd copy is RUNNING out of /Applications — very likely, since
        installing is what put it there — and rmtree pulls the .app out from
        under a live process that may be mid-job. py2app's interpreter reads
        from the bundle lazily (site-packages.zip, the bundled rclone), so
        the running agent does not fail cleanly; it fails whenever it next
        touches a file.
      * copytree dies part-way (a full disk, an ejected volume) and there is
        now NO app in /Applications at all, while the LaunchAgent plist still
        points at the executable inside it. Nothing syncs, and the menu bar
        is not there to say so.

    So: stage the new copy beside the destination, then swap it into place
    with one rename, and only then delete the old one. The window in which
    /Applications has no app is a single rename long, and a failed copy
    leaves the working install untouched.
    """
    import shutil
    dest = APPLICATIONS_DIR / bundle.name
    staged = dest.with_name(dest.name + ".new")
    old = dest.with_name(dest.name + ".old")
    for leftover in (staged, old):
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(bundle, staged, symlinks=True)
    if dest.exists():
        os.rename(dest, old)                       # same volume: atomic
    os.rename(staged, dest)
    shutil.rmtree(old, ignore_errors=True)
    return dest


OPEN_SETUP_FLAG = "open-setup"   # same name as daemon.OPEN_SETUP_FLAG


def _puckd_home() -> Path:
    override = os.environ.get("PUCKD_HOME")
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "JumpHeight"


def _agent_is_running(run, domain: str) -> bool:
    """launchctl print shows a `pid = N` line only for a running service."""
    proc = run("print", f"{domain}/{LABEL}")
    out = (getattr(proc, "stdout", b"") or b"")
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return proc.returncode == 0 and "pid = " in out


def _launchagent_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _registered_program(plist_path: Path) -> "str | None":
    """The executable the installed LaunchAgent actually points at, or None
    if there is no readable plist naming one."""
    import plistlib
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
    except Exception:  # noqa: BLE001 -- missing, unreadable, not a plist
        return None
    args = data.get("ProgramArguments")
    if isinstance(args, list) and args and isinstance(args[0], str):
        return args[0]
    return None


def _plist_bytes(exe: Path) -> bytes:
    """The LaunchAgent, as bytes, from one place -- so a reinstall can tell
    whether the one on disk is stale."""
    import plistlib
    logs = Path.home() / "Library" / "Logs"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(exe), LAUNCHD_FLAG],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "EnvironmentVariables": {"PYTHONUTF8": "1", "LANG": "en_US.UTF-8"},
        # Where a crash before daemon.py's own logging goes. Josh reads this
        # over the phone from 300 miles away; without it an agent that dies
        # at import time leaves no trace anywhere on the machine.
        "StandardOutPath": str(logs / f"{LABEL}.out.log"),
        "StandardErrorPath": str(logs / f"{LABEL}.err.log"),
    })


def _install_and_hand_off(resources: Path) -> bool:
    """Write the LaunchAgent and hand the daemon over to launchd. Returns
    True only when launchd has actually been told to run it -- a False means
    THIS process runs the daemon itself, so a failed handoff is never a
    silently dead agent behind a menu bar that never appears."""
    import plistlib
    import subprocess

    if LAUNCHD_FLAG in sys.argv[1:]:
        return False
    bundle = _bundle_path(resources)
    exe = bundle / "Contents" / "MacOS" / "JumpHeight Sync"
    if not exe.is_file() and not str(bundle).startswith(DMG_PREFIX):
        return False                               # not a bundle; a dev run
    domain = f"gui/{os.getuid()}"

    def run(*args):
        try:
            return subprocess.run(["launchctl", *args], capture_output=True)
        except OSError:
            return subprocess.CompletedProcess(args, 1, b"", b"launchctl missing")

    # Already installed and running from THIS bundle? Then a click on the
    # icon means "show me the window", not "reinstall". Measured 2026-09-14:
    # the rider clicked the Dock icon during his first sync, and the old
    # behaviour booted the running agent out mid-job. Leave a flag the
    # agent's loop consumes (daemon.OPEN_SETUP_FLAG) and exit.
    #
    # "FROM THIS BUNDLE" is checked, not assumed. The test used to be only
    # "an agent is running, and I am not on a dmg" -- which is also true of
    # a NEWER copy opened from ~/Downloads while the old one runs out of
    # /Applications. That click installed nothing, wrote a flag, and exited:
    # the rider saw the setup window open and had every reason to believe he
    # had upgraded, while the old build kept running. The LaunchAgent plist
    # on disk names the executable launchd was given, so compare against it.
    same_bundle = _registered_program(_launchagent_plist()) == str(exe)
    if (_agent_is_running(run, domain) and same_bundle
            and not str(bundle).startswith(DMG_PREFIX)):
        # A newer build dropped over the old one in Finder reaches this path
        # too, so refresh the LaunchAgent plist if its content is stale (the
        # UTF-8 environment was added after the rider's first install and
        # never reached his plist this way -- measured 2026-09-14). It takes
        # effect at the next start, which the self-updater's kickstart is.
        try:
            wanted = _plist_bytes(exe)
            current = _launchagent_plist()
            if not current.is_file() or current.read_bytes() != wanted:
                current.parent.mkdir(parents=True, exist_ok=True)
                current.write_bytes(wanted)
        except OSError:
            pass
        try:
            flag = _puckd_home() / OPEN_SETUP_FLAG
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("open")
        except OSError:
            pass
        return True

    # Stop the running copy BEFORE touching the bundle on disk. Opening the
    # app from the dmg while the launchd copy is mid-job used to rmtree the
    # very bundle that copy is executing from; booting it out first makes the
    # replace clean. A job killed here is exactly G3's case: nothing wrote
    # the bootloader, the spool keeps the bundle, and the next plug-in retries.
    run("bootout", f"{domain}/{LABEL}")

    if str(bundle).startswith(DMG_PREFIX):
        try:
            bundle = _copy_to_applications(bundle)
        except OSError:
            pass                                   # run from where it is
        exe = bundle / "Contents" / "MacOS" / "JumpHeight Sync"
    if not exe.is_file():
        return False

    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / f"{LABEL}.plist"
    logs = Path.home() / "Library" / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(_plist_bytes(exe))

    # `bootstrap gui/<uid>` is the modern spelling and the right DOMAIN for a
    # GUI agent (a menu bar needs an Aqua session; user/<uid> has none). Two
    # things can still make it fail: the older `launchctl load -w` is all
    # some systems accept, and a service left registered by a bootout that
    # did not complete makes bootstrap return EALREADY. kickstart covers the
    # second. If NONE of the three worked, say so by returning False and run
    # the daemon in this process -- an install that quietly started nothing
    # is the failure mode the rider cannot see and cannot report.
    #
    # A return code of 0 is NOT proof. MEASURED on macOS 26.5.1 (2026-09-24,
    # update review): with the job already registered, `bootstrap` returns 5
    # and `load -w` then prints "Load failed" and returns 0. Trusting that 0
    # made this function report a handoff that started nothing; this Finder
    # copy then exited and NOTHING ran the agent. So each spelling counts
    # only once `launchctl print` shows the job with a pid.
    tried = []
    for name, args in (("bootstrap", (domain, str(plist))),
                       ("load", ("-w", str(plist))),
                       ("kickstart", ("-k", f"{domain}/{LABEL}"))):
        proc = run(name, *args)
        if proc.returncode == 0 and _wait_until_running(run, domain):
            return True
        tried.append((name, proc))
    _record_handoff_failure(tried)
    return False


# How long a spelling that returned 0 gets to show a running job. launchd
# spawns a RunAtLoad job immediately; the pid appears in `launchctl print`
# before our Python has even started, so this is generous.
HANDOFF_CONFIRM_S = 10.0


def _wait_until_running(run, domain: str) -> bool:
    deadline = time.monotonic() + HANDOFF_CONFIRM_S
    while True:
        if _agent_is_running(run, domain):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def _record_handoff_failure(tried) -> None:
    """Write WHY launchd would not take the agent into the daemon's own log,
    which is the one file the owner can read from the rider's Mac (the
    daemon uploads it). Never raises.

    Without this line the fallback above is invisible. MEASURED on the
    rider's Mac 2026-09-15..17: every self-update restart failed with
    "kickstart did not report success" -- the signature of an agent running
    in-process, where kickstart has nothing to restart -- and nothing on his
    Mac or in his log said the handoff had ever failed, let alone why."""
    parts = []
    for name, proc in tried:
        err = getattr(proc, "stderr", b"") or b""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        parts.append(f"{name} rc={proc.returncode} {err.strip()[:160]!r}")
    try:
        from datetime import datetime
        home = _puckd_home()
        home.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(home / "daemon.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} launcher: launchd would not take the agent "
                    f"({'; '.join(parts)}); running it inside the app -- an "
                    f"app update reopens the app to take effect\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
