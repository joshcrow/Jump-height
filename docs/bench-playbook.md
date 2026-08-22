# Bench playbook — the bulletproof-pipeline doctrine

**Provenance:** distilled 2026-08-12 from three bench sessions (08-10 → 08-12)
that produced one wrong dead-hardware verdict, five diagnostic designs, ten
firmware commits, and the project's first fully wireless firmware pipeline.
This is the operational half of the doubt-list convention: FIRST_BOOT files
say what to *verify*; this file says how to *work* so verification can be
trusted. Read it before any bench session; amend it when it costs you an hour.

## 1. Board registry (keep current — identity mistakes flash the wrong board)

**Look here FIRST, before any reasoning about a board's behaviour.** On
2026-08-20 I diagnosed a "hardware fault" on a board that simply has no
battery. The fact was already written in two places — this table and
STATUS.md — and I still missed it, because the identifier the tools print
(`JumpHeight-XXXX`) had no row here. **A new identifier without a row in this
table is a rediscovery waiting to happen.**

| Board | Advertised name | BLE addr | USB serial (our fw) | **Battery?** | State (2026-08-20) |
|---|---|---|---|---|---|
| **"OG"** — original Sense (a.k.a. "the mule") | **`JumpHeight-E2C4`** | `185D88EE…` (bootloader `EB2503CC…`) | `7ACE98D972CB56F8` | **YES — pigtail SOLDERED. The only board with a cell.** | **THE product board.** Running `src=ef37e568` (older build, still lacks the `clear()` watchdog fix). Healthy: 3810 mV / 42 %, 23.8 h continuous uptime. Drop calibration was measured on this board. Water-test candidate. |
| **"The spare"** — 3rd Sense *(registry formerly titled this row "Board #3"; that ordinal now collides with "the third board" = the Puck/8673, and identity confusion has cost four wrong verdicts — so the ordinal is retired; this board is THE SPARE, full stop)* | **`JumpHeight-45ED`** | `14E6E6F1…` | `11641737F0ECA0D6` | **NO — no pigtail, USB only.** | Bench board. Running `src=54b2e904`. Healthy sensor (`accel 1.021 g / noise 0.0025 g`). **Its `vbat_mv` / `batt_pct` are a FLOATING divider and mean nothing** — seen reading 3742 mV/23 % and 4133 mV/97 % minutes apart. Never log a battery figure from it. |
| **"Puck"** — 2nd Sense (2026-08-12) | **`JumpHeight-8673`** | `B96D14EA…` | `2513620E30AE413D` | **NO** — USB only | **REASSESSED 2026-08-20: HEALTHY.** Flashed `src=15b2d468`, selftest 6/6 (accel 1.050 g, noise 0.0045 g, flash 2093056B_free). The fourth "dead board" verdict in this project to prove wrong. Role: Era-2 development board (standby/System-OFF/OTA-abort work — never the OG). |
| ~~"Mule"~~ | — | — | — | — | Retired name: the "mule" and the "OG" are the SAME board (row 1). Calling the product board sacrificial is how it nearly got treated as disposable. |

### 1a. The three rules this table exists to enforce

1. **Only the OG can run untethered.** Every drain figure, every endurance
   number, the DC/DC comparison and the three-toss desk test are meaningful
   *only* on the OG. A power measurement from any other board is noise.
2. **Pin every BLE tool to a board.** With two pucks advertising,
   `blecmd.py` / `battlog.py` answer from whichever replies first — on
   2026-08-20 two consecutive unpinned `stats` calls returned two different
   boards. Always `--name JumpHeight-XXXX` (or `--addr`). This has corrupted
   an analysis twice: once a floating 97 % landed in a death-run log, once a
   whole DC/DC board attribution.
3. **A board is identified by name AND `src=`.** `info` prints both. If the
   build hash is not the one you flashed, you are looking at a different
   board — or a stale flash.

### 1b. Keeping this table honest

When a change introduces a **new way to identify a board** — a unique
advertised name, a serial format, a manufacturer-data field — add it to this
table **in the same commit**. That is the specific failure of 2026-08-18:
unique per-board names shipped (`429a5ef`) and the registry was not updated,
so for two days every tool printed an identifier that appeared nowhere in the
documentation. Two correct documents could not prevent the error because
neither could be joined to what was on screen.

**Storage note (2026-08-14):** both boards were `format`ed to repair
storage, so the OG's 61-jump history is **gone for good**. It had been
unreadable (invalid superblock) since 08-12 and `format` was the only way
to get storage mounting again — but the erase is what made it permanent.

**Every board previously declared dead was healthy.** The cause was
firmware (GPIO drive strength — SENSE_FIRST_BOOT §16i,
[xiao-hardware-truth.md](xiao-hardware-truth.md)). Re-read that before
writing off any board.

Factory-fresh boards report a *different* USB serial than our firmware does
(theirs is a Seeed string, ours is the chip ID) — a board's serial *changing*
after first flash is expected, not a swap.

**Rules the registry exists for:**
- Two boards on USB ⇒ **always** `--upload-port` explicitly.
- Two boards advertising `JumpHeight` ⇒ **always** `OTADFU_ADDR=` pin.
- After any flash, re-enumerate ports by serial number, never assume.

## 1c. Flashing doctrine — measured, 2026-08-20 (n=9 in one evening)

The soak that produced these numbers: nine `jump flash` cycles against the
spare in quick succession, each scored on where it succeeded.

| fact | evidence |
|---|---|
| pio's 1200-baud touch from app state: **0/9** | every first-touch attempt failed tonight |
| serial upload against a bootloader entered via the firmware's `uf2` command: **2/2** | until the CDC staled |
| **~2 rapid flashes, then macOS's CDC goes stale** — port node exists, nothing answers, NO software recovers it | cycles 3-7 all failed identically; direct nrfutil timed out too |
| replug clears the stale CDC | proven repeatedly |
| UF2 drive (double-tap → `dd`): **2/2**, immune to the stale CDC | it is mass storage — a different USB path entirely |
| OTA (`otadfu.py`): proven 2/2 back-to-back (2026-08-12) | needs the app running + BLE |

What `jump flash` now does, because of those numbers:

1. **Probe first** — if the app answers, enter the bootloader via `uf2`
   deliberately and upload against a FRESH DFU target (the 0/9 touch never
   runs). If nothing answers, upload once as-is.
2. **One attempt, judged by the `Device programmed` marker** — never the exit
   code (PlatformIO exits 0 over failed writes) and never a bare
   "[SUCCESS]".
3. **On failure: stop.** Blind repeats are what stale the CDC — attempt N+1 is
   LESS likely to work, not more. The tool reports the board's state and the
   three recovery paths in reliability order: replug, double-tap → `dd`, OTA.
4. `selftest`'s `src=` identity check then proves the RIGHT build runs — a
   landed write of the wrong image is still caught.

The standing discipline is unchanged and now has a measured justification:
**batch changes into ONE flash.** The serial path has a budget of ~2 quick
flashes per replug; spend it once, deliberately.

## 2. The instrument doctrine (what the wrong RCA taught)

1. **A diagnostic that can false-negative is worse than none.** Three probe
   designs each convicted a healthy sensor. If a check's failure mode is
   indistinguishable from the fault it checks for, it must not ship.
2. **Never diagnose through the code under suspicion.** Every post-cold-start
   "confirmation" of the dead sensor ran through the broken probe. Archaeology
   builds (worktree at a known-good commit) are cheap — use them as the
   control arm *on the same hardware*.
3. **Positive controls before verdicts.** A probe that has never been seen to
   PASS on a known-good target proves nothing when it fails. The fresh board
   was the positive control that unraveled everything; a $15 spare is
   cheaper than one wrong conclusion.
4. **Falsifiers in writing.** The RCA was caught in hours because it named,
   in advance, the experiment that would disprove it. Verdicts without
   falsifiers are stories.
5. **Failure arcs are weak evidence.** "Fine all day, intermittent, then
   dead" fit solder fatigue perfectly — and described a software race.
6. **Liveness probes must be side-effect-free.** The "mule command-silence
   mystery" (16e) was the tester sending `selftest` — the documented
   deliberate-retry command that HANGS AND REBOOTS a ProbeGuarded board —
   as its liveness check, then recording the reboot as a wedge. Probe
   liveness with `info` or `stats`, never with a command whose designed
   failure mode is the symptom you're hunting.

## 3. The transport doctrine (macOS is an unreliable witness)

- **Nothing counts as sent until its effect is observed.** BLE writes return
  success for commands that never execute; serial writes vanish into wedged
  CDC nodes. The `dfu` trigger requires seeing `OK dfu` back; treat every
  irreversible command the same way.
- **pio's uploader lies.** It prints `SUCCESS` over a failed
  `adafruit-nrfutil`. Trust only the literal `Device programmed.` line.
- **CoreBluetooth degrades with session length** (blind scans, dead
  notification paths, stale GATT caches that hide services). Remedies, in
  order: `blueutil -p 0 && sleep 3 && blueutil -p 1`; 12 s+ scan patience
  after; full re-pairing only if a *service* is missing from a GATT you know
  is there.
- **macOS CDC nodes wedge** after heavy replug/reset cycles (device present
  in `ioreg`, no `/dev/cu.*`). They usually self-heal; the board is rarely
  the problem. `ioreg` before blaming hardware.
- **Serial captures across reboots need reconnecting readers** — a plain
  read loop dies silently at the first re-enumeration and fakes a "hang".
  Count boot banners; one banner ≠ one boot unless the window says so.

## 4. Device power truths

- **A battery-backed board never truly resets.** Every wedge that survives
  "unplug it" survives because the battery kept the chips alive. True cold
  start = battery *and* USB removed. This applies to PERIPHERALS, not just
  the MCU: an I2C slave caught mid-transaction when the MCU dies keeps
  clamping SDA through every warm reset — only power removal (or a 9-clock
  SCL bus-clear, the standard I2C unstick, not yet implemented — see
  SENSE_FIRST_BOOT 16d/16f) releases it. Both boards' "held bus" (2026-08-12)
  match this signature: continuous power since a mid-transaction death.
- **macOS gates new USB accessories behind an Allow prompt** (Sequoia+).
  A board that shows in `ioreg` as `!registered, !matched` with no
  `/dev/cu.*` node may just be sitting at that dialog — check the screen
  before diagnosing silicon. The prompt can sit unanswered for a DAY while
  every remote diagnosis runs in circles (it did). Batteries are on JST-style pigtails
  (unclip, no soldering) — so a true cold start on the mule is a two-second
  unclip, and cells swap freely between boards once the puck gets its own
  pigtail soldered. Pigtails also make item 25c's off-current measurement
  (meter in series on the cell lead) trivial. The battery-less puck-in-bring-up is the
  easy case: replug = power cycle — for the MCU *and* the QSPI flash and IMU.
- **In OTA-DFU mode the board has no USB at all.** The radio is the only way
  in; a failed transfer strands it dark until physical reset. Never enter DFU
  without a recovery plan.
- **The 1200-baud touch is an app feature.** Bootloaders and hung apps don't
  implement it. Bootloader UF2 (MSC) mode is entered by magic `0x57` (`uf2`
  command) — the 1200-touch enters *serial-only* DFU by design.
- **The touch that actually works from a shell is `stty -f /dev/cu.usbmodemX
  1200`** (2026-08-13, board #3's factory app): pyserial open-at-1200 +
  DTR-toggle variants did nothing, stty reset the board instantly. And after
  ANY reset into the bootloader, expect the Allow gate above if the Mac has
  never seen that board's bootloader identity — the app and bootloader count
  as different accessories.
- **USB port numbers are not stable across sessions** — the boards SWAPPED
  `/dev/cu.usbmodem` numbers on 2026-08-13. Before any flash or destructive
  command, map port→board via `ioreg` USB serial numbers, never by habit.
- **uhubctl doesn't work on the current hub.** A PPPS-capable hub (~$30)
  would make bench power fully software-controlled and retire half of this
  file's "ask a human to replug" cases.

- **Before ANY "dead hardware" verdict, read
  [xiao-hardware-truth.md](xiao-hardware-truth.md).** It lists the
  measurements that are INVALID on this board — they return the same
  answer for healthy and broken silicon — and the two that work
  (`pincensus`, pin readback while driving). Two wrong dead-hardware
  verdicts came from tests on the first list.
- **`pincensus` is the FIRST diagnostic, not the last.** One command,
  every GPIO, control pins included in the same pass.

## 5. Recovery ladder (try in order; each rung proven on silicon)

| Symptom | Remedy |
|---|---|
| App alive, commands work | `dfu` / `uf2` / `off` / `format` as needed |
| App advertising but command-dead | **BLEDfu control point** (subscribe, write `0x01`) — runs on the BLE task, survives a dead `loop()`; then `0x06` from the bootloader reboots clean |
| Bootloader stale mid-DFU | `otadfu.py` auto-recovers (0x07 probe → `0x06`) — with USB *out*; with USB in, `0x06` lands in UF2/serial mode: recover by serial flash |
| Boot loops at `SELFTEST BEGIN` | Already handled: the jh_persist sticky guard turns the second boot into an honest `i2c FAIL` + READY. `selftest` command = deliberate retry |
| Storage unmountable | `mount` first — non-destructive retry (`try_mount()` NEVER formats; an unreadable superblock is reported, not rebuilt). Then `format` (works with fs down, DESTROYS data). If the chip won't even probe: physical power-cycle (battery-less board: replug) |
| Dark on every interface | Physical: reset tap or power cycle. This is the case to engineer away, not accept |

## 6. Firmware invariants (hold these in review)

- **Boot must complete no matter what a peripheral does.** No unbounded
  first-contact on any bus; crash-guards must live in storage that survives
  the *whole* reset path — measured: `.noinit` doesn't exist in this core's
  linker scripts, GPREGRET2 is sanitized through the bootloader,
  **jh_persist (internal LittleFS) survives everything**.
- **Untrusted-input boundaries hold the line**: radio bytes (bare `catch` in
  the watch's ingest; corruption gate in its Model), flash state (mount
  failures degrade, never block), sensor presence (FAIL row, not a hang).
- **Every recovery path must be reachable over the air** — the sealed box has
  no other hands: `dfu` (reflash), `format` (storage), `selftest` (sensor
  retry), BLEDfu (wedged-app rescue). Anything new that can fail needs its
  radio-reachable recovery named in review.
- **Migrations never discard.** jh_persist v1→v2→v3 pattern: a shorter valid
  record is old, not corrupt.

## 6b. Electrical safety rules (2026-08-13 — how we never cook hardware again)

> The full standing plan — mechanism, shipped code defenses, the meter
> decision tree, and the unseal gate for the sealed spare board — lives in
> [hardware-protection.md](hardware-protection.md). This section is the
> five rules; that document is the law they come from.

1. **No MCU line into a peripheral's power domain may be energized while
   that domain is down.** Power-up: float bus → rail up → settle (regulator
   3 ms + device Ton 35 ms) → attach. Power-down: detach (Wire1.end()) →
   float lines → rail down. Both sequences exist in code now (jh_imu::revive,
   jh_power::system_off) — new code copies them, never improvises.
2. **Any code that touches a power/rail/bus pin gets a datasheet-or-web
   sequencing check BEFORE silicon.** The off-path back-feed was documented
   on Nordic DevZone the whole time; ten minutes of searching would have
   prevented it.
3. **New-board quarantine: a fresh board meets only the current audited
   build.** Never experimental electrical code. The spare Sense stays
   SEALED until the damage mechanism is confirmed fixed and a sacrificial
   board has survived the off/sleep/wake cycle repeatedly.
4. **Electrical experiments run on the designated sacrificial board only**
   (currently: the mule).
5. **One flash per session where possible; batch changes.** Seven-flash
   evenings are where sequencing mistakes compound.

## 7. Standing predictions (check on next bench contact)

Settled 2026-08-12 evening:
- ~~The new board's QSPI mounts fine after its replug~~ **CONFIRMED** —
  first boot of the hardened build: `flash PASS 2093056B_free`, no guard
  trip, no format needed.
- ~~The dark-out recurs under the gyro hot path~~ **RESOLVED OTHERWISE** —
  the "dark-out" was macOS's unanswered USB-accessory Allow prompt (§4)
  stacked on the pre-hardening build's unbounded CDC emit; with the prompt
  answered and the hardened build flashed, the board is up and commandable.

Scored the same night (2026-08-12, late) — **the falsifiers fired**:
1. ~~Puck sensor returns after USB-out cold start~~ **FAILED** — still
   hangs→WDT 5/5 truly cold, AND the archaeology control (`a6e477d`, which
   read this sensor at 0.970 g two days prior) boot-loops on it. Bus
   genuinely held under any firmware. Clamp theory dead. (SENSE_FIRST_BOOT
   16f verdict has the full table.)
2. ~~Mule `mount` succeeds after battery-unclip~~ **FAILED but UNSCORED**
   until the owner confirms the pigtail itself was unclipped — a USB-only
   unplug is a no-op on a battery-backed board (§4).
3. Mule sensor return — moot with #1 dead; superseded below.

Open:
1. **Meter, not code** — software has nothing left to say about either
   IMU bus. Full 2-minute protocol + interpretation table:
   SENSE_FIRST_BOOT **16g** (written after the overnight web findings:
   the 6D rail powers the sensor AND its pull-ups via a P1.08-enabled
   regulator; bus-energized-while-rail-down back-feed is a documented
   sensor-corrupter on this module, and our `off` path did it for hours
   per sleep — fixed in code, unflashed pending the meter).
2. **Common-mode question** on record: two IMU buses dead days apart,
   same bench, same hub, same kapton workflow, same firmware lineage.
   Noted correlation, mechanism unknown, not the whole story (the mule
   died BEFORE the probe-era rail-cycling code existed): the Puck ran
   probe-era builds during the 08-12 falsifier session and was dead
   within a day.
3. Mule battery-unclip experiment still worth one clean run (if it wasn't
   truly unclipped): `mount` verdict on the 61-jump history stands or
   falls there.

## 1d. Committing while agents are working (lesson, 2026-08-21)

**Never `git add -A` while a background agent is editing the tree.**

On 2026-08-21 three agents worked in parallel while I committed my own
foreground work. Every `git add -A` swept up whatever the agents had
half-written at that instant. The result:

- `e89beca` — message says "Instinct night prep: the MTP sender…" and it
  also contains the **entire float→double timebase fix** (jump_detector.h,
  trace_codec.h, main.cpp, jh_store.cpp, host_test.cpp) plus the rider brief
  and the store draft.
- `6ea9ec1` — message says "Two riders, two pucks…" and it also contains the
  **timebase regression test** (tools/tests/test_timebase_falsifier.py).

The code is correct and the tree is green — but the *history* now lies about
which change is which, and the timebase fix (an Era-2 blocker with its own
verification story) has no commit of its own to point at. That is the same
class of failure as prose drifting from reality: the artifact says one thing
and the truth is another.

**The rule:** while agents are running, stage explicit paths —
`git add docs/foo.md tools/bar.py` — never `-A`, never `.`. Check
`git status --short` before every commit and confirm every listed file is
one *you* touched. If an unexpected file appears, it belongs to an agent:
leave it, and let that work land in its own commit with its own message.

The agent caught this and reported it; I had not noticed. Worth saying
plainly, because the cost is invisible until someone tries to read the
history six months from now — which is exactly the horizon this project is
building for.

## 1e. Screenshots return BLACK when the Mac is locked — check before diagnosing

`screencapture` on a locked Mac with the display asleep exits **0** and writes
a **valid PNG that is 100 % black** (measured 2026-08-21: 5,621,280 of
5,621,280 pixels, all channels zero). No error, no warning.

I read that as "Screen Recording permission is missing" and said so. The owner
knew better — he was driving this session by remote control with the machine
locked. One API call settles it:

```python
import Quartz
d = Quartz.CGSessionCopyCurrentDictionary()
print(d.get("CGSSessionScreenIsLocked"))                  # True == locked
print(Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID())) # True == no framebuffer
```

**Rule: before any capture, assert the screen is unlocked and awake, and
refuse with that reason rather than producing a black image.** Same rule as
everywhere else in this repo — a tool that cannot do its job must say so
instead of returning something that looks like output.

Note the failure ordering that made this expensive: the capture "worked"
(exit 0, valid PNG, plausible file size), so the natural next step was to
debug crop geometry — which produced more black images and more plausible
explanations. The artifact was never inspected until several steps in.
**Inspect the artifact first; the exit code proves nothing.**

Practical consequence: simulator screenshots ARE available, just not while the
machine is locked. Capture them when someone is physically at the Mac.
