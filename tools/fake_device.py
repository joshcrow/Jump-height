#!/usr/bin/env python3
"""Fake Jump Height device: emulates the firmware's serial protocol on a pty.

Lets you rehearse and integration-test the entire CLI flow with zero hardware.
Use it through the CLI's --fake flag, which spawns it and drives it:

    ./tools/jump desktest --fake
    ./tools/jump drop --fake

Running it by hand (`python3 tools/fake_device.py --scenario desktest` prints
"PTY /dev/pts/N" to connect a monitor to) is for protocol debugging only: the
scripted shake/toss/drop events only advance when the connected client sends
`_sim next` — the CLI does that automatically in --fake mode, so a manual
`--port /dev/pts/N` desktest would wait forever at the shake step unless you
type `_sim next` yourself.

Scenarios:
    ok         boots clean, responds to commands, no events
    badwiring  self-test fails (no I2C device) with the real hints
    desktest   emits STATE + 3 toss JUMPs on a timeline
    drop       emits N drop JUMPs with a known injected timing bias (+15 ms)
    session    pre-loaded with the demo session (4 jumps + full trace)

Keep this file's protocol output in lockstep with firmware/src/main.cpp.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import select
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(REPO / "tools"))

from detector import Detector, load_params  # noqa: E402
from generate import DEMO_JUMPS, synth_session  # noqa: E402
import gen_params  # noqa: E402

FW_VERSION = "0.4.3"
# A fake device is genuinely NOT running the compiled firmware, so it reports
# an identity that can never collide with a real source hash (which is 8 hex
# chars). The mismatch the CLI then reports is correct, not a bug.
BUILD_SRC = "fakedev0"
INJECTED_BIAS_S = 0.015  # pretend detection latency, for the drop scenario


class FakeDevice:
    def __init__(self, args):
        self.args = args
        self.params = load_params()
        self.master, self.slave = os.openpty()
        os.set_blocking(self.master, False)
        # Raw mode: without this the pty echoes our own output back to us as
        # input (default line discipline), creating a feedback loop.
        import tty

        tty.setraw(self.slave)
        print(f"PTY {os.ttyname(self.slave)}", flush=True)

        self.session_jumps = 0
        self.session_best = 0.0
        self.jumps_rows: list[str] = []
        self.trace_rows: list[str] = []
        self.rng = random.Random(args.seed)
        self.buf = b""
        self.cal_from_nvs = False  # mirrors the firmware's `set` persistence flag

        # Scripted "physical" events. The CLI (in --fake mode) advances them
        # deterministically by sending `_sim next` at each point where a human
        # would act — no wall-clock races.
        self.events: list[str] = []

        if args.scenario == "session":
            self._preload_session()
        elif args.scenario == "desktest":
            self.events = ["recording", "jump:0.45", "jump:0.62", "jump:0.38"]
        elif args.scenario == "drop":
            h = args.height_cm / 100.0
            true_t = math.sqrt(2.0 * h / self.params.g)
            self.events = ["recording"]
            for _ in range(args.drops):
                raw = true_t + INJECTED_BIAS_S + self.rng.gauss(0.0, 0.004)
                self.events.append(f"jump:{raw:.4f}")

    # ------------------------------------------------------------ helpers
    def battery_suffix(self) -> str:
        """The battery keys appended to STATS/INFO, or '' when emulating a
        no-telemetry (v1/ESP32) device — mirroring main.cpp's adder rule
        exactly: keys present iff the platform can measure. Percent uses
        the host seam's linear 3300→0..4160→100 curve (top anchor is the
        nrf52 curve's rested-full anchor, SENSE_FIRST_BOOT.md item 24, not
        the 4200 mV charging voltage; calibration realism is not what
        rehearsals assert, key plumbing is)."""
        if self.args.no_battery:
            return ""
        mv = self.args.vbat_mv
        pct = max(0, min(100, (mv - 3300) * 100 // 860))
        chg = 1 if self.args.charging else 0
        return f" vbat_mv={mv} batt_pct={pct} chg={chg}"

    def _preload_session(self):
        """Populate storage exactly as a real logged session would."""
        times, mag = synth_session(DEMO_JUMPS, fs_hz=50.0, seed=3)
        det = Detector(self.params)
        n = 0
        for t, m in zip(times, mag):
            self.trace_rows.append(f"{t:.3f},{m:.3f}")
            ev = det.update(t, m)
            if ev:
                n += 1
                self.jumps_rows.append(
                    f"{n},{ev.takeoff_time_s:.3f},{ev.airtime_raw_s:.3f},"
                    f"{ev.airtime_s:.3f},{ev.height_m:.3f}")

    def send(self, line: str):
        """Write a whole line to the pty, RELIABLY.

        The master fd is non-blocking and macOS pty buffers are tiny (~1 KB,
        vs ~64 KB on Linux), so a full `dump` overruns them instantly. A real
        UART would drop bytes; a deterministic test double must not — dropped
        framing (the 'OK dump' terminator) left the CLI waiting forever, but
        only on Macs, where the smaller buffer actually overflowed. So: write
        it all, pausing briefly for the reader to drain, and give up only
        after a stalled 5 s (reader is gone) so nothing can hang either.
        """
        data = (line + "\n").encode()
        deadline = time.monotonic() + 5.0
        while data:
            try:
                n = os.write(self.master, data)
                data = data[n:]
            except BlockingIOError:
                if time.monotonic() > deadline:
                    return  # no one is draining the pipe; drop rather than hang
                select.select([], [self.master], [], 0.05)
            except OSError:
                return  # reader closed the pty

    def emit_jump(self, raw: float):
        p = self.params
        cal = max(0.0, raw + p.airtime_offset_s)
        h = p.height_scale * p.g * cal * cal / 8.0
        self.session_jumps += 1
        self.session_best = max(self.session_best, h)
        n = len(self.jumps_rows) + 1
        self.jumps_rows.append(f"{n},{10.0 + 5 * n:.3f},{raw:.3f},{cal:.3f},{h:.3f}")
        self.send(f"JUMP n={self.session_jumps} airtime_raw_s={raw:.3f} "
                  f"airtime_s={cal:.3f} height_m={h:.3f} height_ft={h * 3.28084:.1f} "
                  f"best_m={self.session_best:.3f}")

    # ----------------------------------------------------------- protocol
    def send_help(self):
        self.send("# commands: help | stats | jumps | trace | dump | clear | selftest | info")
        self.send("#           set <airtime_offset_s|height_scale> <value|default>")

    def send_selftest(self):
        self.send("SELFTEST BEGIN")
        if self.args.scenario == "badwiring":
            self.send("SELFTEST i2c FAIL detail=no_device")
            self.send("# hint: no sensor found. Check the 4 wires: sensor VCC->3V3 (NOT the")
            self.send("# hint: pin marked VCC — it carries ~4.7V), GND->GND, SDA->SDA,")
            self.send("# hint: SCL->SCL. Swapped SDA/SCL is the #1 cause; loose jumper is #2.")
            self.send("SELFTEST accel SKIP detail=no_sensor")
            self.send("SELFTEST noise SKIP detail=no_sensor")
            self.send("SELFTEST ble PASS detail=advertising")
            self.send("SELFTEST flash PASS detail=1441792B_free")
            self.send("SELFTEST END result=FAIL")
        else:
            self.send("SELFTEST i2c PASS detail=0x68")
            self.send("SELFTEST whoami PASS detail=0x68")
            self.send("SELFTEST accel PASS detail=1.002g")
            self.send("SELFTEST noise PASS detail=0.0061g")
            self.send("SELFTEST ble PASS detail=advertising")
            self.send("SELFTEST flash PASS detail=1441792B_free")
            self.send("SELFTEST END result=PASS")

    def send_boot(self):
        self.send(f"# JumpHeight fw v{FW_VERSION}")
        if self.cal_from_nvs:
            # Mirrors firmware's loadCalibration(), called at boot before
            # the self-test (main.cpp's setup()).
            self.send(f"# calibration from device memory: "
                      f"airtime_offset_s={self.params.airtime_offset_s:.4f} "
                      f"height_scale={self.params.height_scale:.3f}")
        self.send_selftest()
        if self.jumps_rows:
            best = max(float(r.split(",")[-1]) for r in self.jumps_rows)
            self.send(f"# stored history: {len(self.jumps_rows)} jumps, best "
                      f"{best:.2f} m — `dump` to export, `clear` to reset")
        self.send_help()
        self.send("READY")

    def send_file(self, name: str, header: str, rows: list[str]):
        self.send(f"FILE {name} BEGIN")
        if rows:
            self.send(header)
            for r in rows:
                self.send(r)
        self.send(f"FILE {name} END")

    def handle(self, cmd: str):
        if cmd == "help":
            self.send_help()
            self.send("OK help")
        elif cmd == "stats":
            stored_best = max((float(r.split(",")[-1]) for r in self.jumps_rows),
                              default=0.0)
            # Bytes trace.csv would take in a dump — lets a client size a
            # download before starting it (mirrors the firmware's counter).
            if self.trace_rows:
                trace_bytes = len("t,mag\n") + sum(len(r) + 1 for r in self.trace_rows)
            else:
                trace_bytes = 0
            self.send(f"STATS session_jumps={self.session_jumps} "
                      f"session_best_m={self.session_best:.3f} "
                      f"session_best_airtime_s={getattr(self, 'session_best_airtime', 0.0):.3f} "
                      f"stored_jumps={len(self.jumps_rows)} "
                      f"stored_best_m={stored_best:.3f} "
                      f"trace_bytes={trace_bytes}{self.battery_suffix()}")
            self.send("OK stats")
        elif cmd == "jumps":
            self.send_file("jumps.csv", "n,takeoff_s,airtime_raw_s,airtime_s,height_m",
                           self.jumps_rows)
            self.send("OK jumps")
        elif cmd == "trace":
            self.send_file("trace.csv", "t,mag", self.trace_rows)
            self.send("OK trace")
        elif cmd == "dump":
            self.send_file("jumps.csv", "n,takeoff_s,airtime_raw_s,airtime_s,height_m",
                           self.jumps_rows)
            self.send_file("trace.csv", "t,mag", self.trace_rows)
            self.send("OK dump")
        elif cmd == "clear":
            self.jumps_rows = []
            self.trace_rows = []
            self.send("# cleared stored data")
            self.send("OK clear")
        elif cmd == "selftest":
            self.send_selftest()
            self.send("OK selftest")
        elif cmd.startswith("set "):
            # Mirrors firmware EXACTLY (main.cpp): key = cmd[4:sp], value =
            # cmd[sp+1:].strip(), where sp is the first space at-or-after
            # index 4 (indexOf-style, not a whitespace-collapsing split()) —
            # so a double space right after "set" yields an EMPTY key (sp
            # lands immediately, before any key text), not a leniently-
            # skipped-past one. `cmd.split()` would silently tolerate that;
            # the real firmware does not, and neither must this.
            sp = cmd.find(" ", 4)
            key = cmd[4:sp].strip() if sp > 0 else ""
            val = cmd[sp + 1:].strip() if sp > 0 else ""
            if key not in ("airtime_offset_s", "height_scale"):
                self.send("ERR set_unknown_key (airtime_offset_s | height_scale)")
            elif val == "default":
                defaults = load_params()
                setattr(self.params, key, getattr(defaults, key))
                self.cal_from_nvs = False
                self.send("# reverted to the compiled default")
                self.send("OK set")
            else:
                try:
                    f = float(val)
                except ValueError:
                    f = 0.0
                sane = (-0.5 <= f <= 0.5) if key == "airtime_offset_s" \
                    else (0.5 <= f <= 2.0)
                if not sane:
                    self.send(f"ERR set_out_of_range {key}={val}")
                else:
                    setattr(self.params, key, f)
                    self.cal_from_nvs = True
                    self.send("# saved to device memory — survives reboot and reflash")
                    self.send("OK set")
        elif cmd.startswith("_sim"):
            # Test hook: emit the next scripted "physical" event (the CLI in
            # --fake mode sends this where a human would shake/toss/drop).
            if self.events:
                self.fire(self.events.pop(0))
        elif cmd == "info":
            cfg = gen_params.load_config(
                Path(os.environ.get("JH_CONFIG") or gen_params.CONFIG_PATH))
            summary = " ".join(
                f"{k}={gen_params.fmt_summary(v)}"
                for k, v in sorted(cfg["detector"].items()) if not k.startswith("_"))
            # Rates come from the config, exactly like the firmware's JH_ macros
            # do — hardcoding them here would drift the moment config changes.
            fw_cfg = cfg["firmware"]
            self.send(f"INFO src={BUILD_SRC} fw={FW_VERSION} sample_hz={fw_cfg['sample_hz']} "
                      f"log_hz={fw_cfg['log_hz']} "
                      f"motion_thresh_g={fw_cfg['motion_thresh_g']:.2f} "
                      f"idle_timeout_s={fw_cfg['idle_timeout_s']} ble=1"
                      f"{self.battery_suffix()}")
            self.send("PARAMS " + summary)
            self.send(f"CAL airtime_offset_s={self.params.airtime_offset_s:.4f} "
                      f"height_scale={self.params.height_scale:.3f} "
                      f"source={'device' if self.cal_from_nvs else 'defaults'}")
            self.send("OK info")
        else:
            # Help BEFORE the ERR terminator, exactly like the firmware —
            # clients stop reading at OK/ERR, so trailing lines would corrupt
            # the framing of the next command's response.
            self.send_help()
            self.send(f"ERR unknown_command {cmd}")

    def fire(self, action: str):
        if action == "recording":
            self.send("STATE recording")
        elif action == "idle":
            self.send("STATE idle")
        elif action.startswith("jump:"):
            self.emit_jump(float(action.split(":", 1)[1]))

    # --------------------------------------------------------------- loop
    def run(self):
        self.send_boot()
        while True:
            r, _, _ = select.select([self.master], [], [], 0.25)
            if not r:
                continue
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                chunk = b""
            if chunk:
                self.buf += chunk
                while b"\n" in self.buf or b"\r" in self.buf:
                    for sep in (b"\n", b"\r"):
                        if sep in self.buf:
                            line, self.buf = self.buf.split(sep, 1)
                            cmd = line.decode(errors="replace").strip()
                            if cmd:
                                self.handle(cmd)
                            break


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default="ok",
                    choices=["ok", "badwiring", "desktest", "drop", "session"])
    ap.add_argument("--fast", action="store_true", help="compressed timeline (tests)")
    ap.add_argument("--height-cm", type=float, default=100.0)
    ap.add_argument("--drops", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    # Battery telemetry (jh_power seam, docs/sense.md §3.4): the fake
    # emulates a Sense-class device, so the keys are PRESENT by default —
    # --no-battery rehearses the other half of the adder rule (v1/ESP32
    # devices, where the keys are byte-absent). Values are fixed, not
    # drifting: tests and rehearsals want determinism, not realism.
    ap.add_argument("--vbat-mv", type=int, default=3920,
                    help="reported battery voltage (default 3920 ≈ 72%%)")
    ap.add_argument("--charging", action="store_true", help="report chg=1")
    ap.add_argument("--no-battery", action="store_true",
                    help="emulate a no-battery-telemetry (v1/ESP32) device")
    args = ap.parse_args()
    try:
        FakeDevice(args).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
