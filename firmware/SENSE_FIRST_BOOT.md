# First-boot checklist — Seeed XIAO nRF52840 Sense port

This platform layer (`src/platform/nrf52/`) was written and proven to
**compile clean** — three PlatformIO environments build, the host-side
C++/Python parity suite passes, `./tools/jump simtest` passes, and
`tools/uf2conv.py` converts the built `.hex` into a valid `.uf2` — entirely
without the physical board, which had not yet arrived when this work was
done (docs/sense.md's own status line: "Board... ordered, arriving within
days"). Every claim about how the chip, the Bluefruit stack, and the LSM6DS3
actually behave was checked as carefully as possible against the INSTALLED
core's own source and, where needed, the vendor's own reference driver on
GitHub — but "checked against source" is not "checked against silicon."

This file lists every place a wrong guess is plausible, in rough priority
order (most likely to bite first), each with exactly what was assumed,
where it lives in code, and how to check it on the real board in one step —
in the style of `garmin/FIRST_COMPILE.md`. Read this before the first power-on,
work top to bottom as `selftest`/behavior surprises show up, and edit this
file (like `docs/sense.md` itself says: "Items marked VERIFY get answered on
the bench and this doc gets edited") once each item is confirmed.

---

## 1. Board id / platform source: `xiaoblesense_adafruit` via a community fork, not the official platform

**File:** `firmware/platformio.ini`'s `[env:xiaoblesense_adafruit]` comment block.

`pio boards nordicnrf52` against the officially-registered
`platformio/nordicnrf52` platform (checked at version 10.12.0) does **not**
list `xiaoblesense_adafruit` — Seeed's XIAO BLE Sense isn't in PlatformIO's
own boards database. Per the port's design decisions, this falls back to
`maxgerhardt/platform-nordicnrf52` (a community superset fork, MIT-licensed
like upstream), pinned to the exact commit
(`cac6fcf943a41accd2aeb4f3659ae297a73f422e`) verified in the authoring
environment: the platform, the `xiaoblesense_adafruit` board definition, and
its matching core fork (`framework-arduinoadafruitnrf52-seeed`, providing
the `Seeed_XIAO_nRF52840_Sense` variant) all install and link cleanly, and a
probe sketch exercising Bluefruit BLEUart, `Adafruit_SPIFlashBase`/
`Adafruit_FlashTransport_QSPI`, and `Wire1` builds successfully.

**What's unverified:** that this pinned fork commit's board/variant
definition matches the REAL board in hand — i.e. that Seeed hasn't shipped
a hardware revision with different pin mappings since this fork's variant
file was written, and that `adafruit-nrfutil`/the bootloader this platform
expects actually matches what ships on the board.

**Verify:** `pio run -d firmware -e xiaoblesense_adafruit -t upload` (or the
UF2 drag-drop path, item 16) against the real board; a boot banner over USB
serial at 115200 baud is the first sign everything lines up.

**CONFIRMED 2026-07-31 (first power-on):** the real board's bootloader
reports `Board-ID: Seeed_XIAO_nRF52840_Sense`, `UF2 Bootloader 0.6.1`,
`SoftDevice: S140 version 7.3.0` (INFO_UF2.TXT) — exactly the variant and
SoftDevice this pinned fork targets. Both flash paths worked: the UF2
drag-drop (first flash) and `pio run ... -t upload` serial DFU via
adafruit-nrfutil (second flash — "Device programmed"). Firmware boots,
enumerates as `/dev/cu.usbmodem101`, prints the self-test and `READY` at
115200. Everything about this item lines up on real silicon.

## 2. BLEUart/BLECharacteristic can genuinely block up to ~100 ms per notify

**File:** `firmware/src/platform/nrf52/jh_link.cpp`'s file-level comment
(section "THE CARDINAL RULE"), `sendOneChunk()`.

Traced through the installed Bluefruit52Lib source (cited by file+line in
the code comment): `BLECharacteristic::notify()` takes a per-connection
FreeRTOS counting semaphore (`getHvnPacket()`) with a **real** blocking
wait, capped at `BLE_GENERIC_TIMEOUT = 100` ms, and the semaphore's count
defaults to `BLE_GATTS_HVN_TX_QUEUE_SIZE_DEFAULT = 1` — only one
notification per connection may be in flight at a time, replenished only
when the SoftDevice confirms the previous one actually left the radio. This
is a materially different (and worse-case-slower) semantic than the ESP32's
NimBLE `notify()`, which returns `void` and never blocks the calling task.

The queue+pump architecture (mirroring the ESP32 side) confines this
possible stall to `pump()`'s own call, never to `write()`'s callers — but
`pump()` itself is called from `loop()`, so a genuinely stalled link (two
centrals connected, one out of range) could still measurably slow sampling
for as long as ~100–200 ms in the worst case, which is longer than "at most
one paced chunk... in microseconds" the seam's contract describes for the
idle case.

**Verify:** with two centrals connected (a phone + a Garmin, or two phones),
run a `dump` and inspect the resulting `trace.csv` for any gap between
consecutive rows bigger than a couple of sample periods (`1/JH_SAMPLE_HZ`).
If gaps show up, first try loosening `CHUNK_GAP_US` in `jh_link.cpp` (currently
15000, a guess — see item 3), then consider whether one link should be
allowed to lag the other rather than both being serviced from one shared
queue.

## 3. `CHUNK_GAP_US` (BLE send pacing) — derived, not yet measured

**File:** `firmware/src/platform/nrf52/jh_link.cpp`, `CHUNK_GAP_US = 15000`.

Reasoning from item 2's `hvn_qsize=1` fact: pacing slower than the
negotiated BLE connection interval gives the previous chunk a realistic
chance to have already been ACKed before the next send attempt, keeping
`getHvnPacket()` in its fast/uncontended path rather than its 100 ms worst
case. *(Upgraded 2026-07-28 from "plausible guess" to "derived": Apple's
Accessory Design Guidelines require peripheral connection intervals in
multiples of 15 ms, floor 15 ms for non-HID accessories — so against an
iPhone the interval is ≥15 ms and one chunk per interval is the physical
ceiling regardless of pacing.)* Still to measure on the bench: the interval
Bluefy and the Garmin watch actually negotiate, and whether dump throughput
warrants requesting a shorter interval or a bigger `hvn_qsize`.

**Verify:** log (or infer from throughput) the actual connection interval
Bluefy/the web app/a Garmin watch negotiate; if it's smaller than 15 ms,
`CHUNK_GAP_US` can safely shrink (faster dumps); if bigger, item 2's gap
symptom is the signal to grow it.

## 4. LSM6DS3TR-C register map — confirmed against ST's own driver source, not the datasheet PDF directly

**File:** `firmware/src/platform/nrf52/lsm6ds3_min.h`.

The datasheet PDF itself did not fetch cleanly in the authoring
environment; every register address and bit-field layout were instead
confirmed against ST's own published register-definition source
(`STMicroelectronics/lsm6ds3tr-c-pid` on GitHub — `lsm6ds3tr-c_reg.h`/`.c`),
which is as authoritative as the datasheet for these specific facts (it's
ST's own driver, not a third party's guess) — but a from-datasheet
cross-check has not happened.

*(Corrected 2026-07-29: this item previously stated the sensitivity
constant as "±8 g / 0.244 mg/LSB" — stale, and self-contradictory with item
20 below, which documents this port's DELIBERATE choice of the ±16 g range
(`CTRL1_XL = 0x54`) at 0.488 mg/LSB. The register-map confirmation this item
is actually about was always against the real, shipped ±16 g/0.488
configuration; only the sensitivity NUMBER quoted here was wrong, copied
from a generic ±8 g reference point rather than this file's own constants.)*

**Verify:** `selftest`'s `whoami` row (see item 5) and `accel` row — a real
LSM6DS3TR-C sitting still should read close to 1.000 g with low noise,
confirming the ±16 g scale factor (0.488 mg/LSB — item 20) and axis byte
order (little-endian, the opposite of the MPU-6050's big-endian burst — see
the file's own comment on this exact risk) are both right. A wrong scale
factor would show up as "self-test WARN/FAIL, mean far from 1.0 g"
immediately.

**CONFIRMED 2026-07-31 (first power-on):** `accel PASS` at 0.979–1.021 g
across four boots (runtime gravity normalization absorbs the ~2% unit
offset, exactly as designed), `noise` 0.0016–0.0018 g at true rest —
register map, ±16 g scale factor, and little-endian byte order all correct.
(Early `noise` readings of 0.14 g / 0.057 g were the board being handled /
desk vibration, not the sensor: same boot, board left alone, 0.0018 g.)

## 5. WHO_AM_I reads 0x6A, not 0x68 — an EXPECTED self-test WARN, not a bug

**File:** `firmware/src/platform/nrf52/lsm6ds3_min.h`'s `whoAmI()` comment;
`firmware/src/main.cpp`'s self-test (unchanged, shared with the ESP32 —
compares against the literal `0x68`).

The LSM6DS3TR-C's real WHO_AM_I value (0x6A) is passed through honestly
(never remapped to fake a 0x68 match). Since `main.cpp` is shared,
unchanged code written for the MPU-6050, its `whoami` self-test row will
print `WARN detail=0x6A` on every single Sense board, every time, forever —
along with the ESP32-specific hint text "likely a clone MPU-6050", which
doesn't literally apply here. This does **not** fail the aggregate
self-test (WARN, not FAIL — see `main.cpp`'s own "WARN-not-FAIL" comment);
it's cosmetic, but worth knowing before the first `selftest` run reads as
alarming.

**Verify:** nothing to verify here — confirm the WARN reads `0x6A` (not
some other, genuinely-wrong value) on first boot, and move on.

**CONFIRMED 2026-07-31:** first boot read `whoami WARN detail=0x6A`, with
the (inapplicable, as predicted) clone-MPU-6050 hint text. Cosmetic, as
documented. Moved on.

## 6. Dual-probe self-test address mapping: `i2c PASS detail=0x68` is a placeholder, not the real address

**File:** `firmware/src/platform/nrf52/jh_imu.cpp`'s file comment.

`jh_imu.h`'s shared self-test loop only ever probes the two MPU-6050
AD0-strapping candidates (0x68/0x69); the LSM6DS3TR-C's real, fixed address
(0x6A) is neither. `probe(ADDR_PRIMARY)` performs the real bus transaction
against 0x6A internally and reports that result under the 0x68 placeholder
slot; `probe(ADDR_SECONDARY)` always misses by design. The visible effect:
`SELFTEST i2c PASS detail=0x68` on every boot, never `0x6A`. Purely
cosmetic (main.cpp never uses the probed address value for anything but
that log line and passing it back into `begin()`, which ignores it too).

**Verify:** nothing behavioral to check; just don't be surprised the log
says 0x68.

**CONFIRMED 2026-07-31:** `i2c PASS detail=0x68` on every boot, exactly as
described. The real 0x6A transaction underneath works (the accel row's
1 g reading is the proof).

## 7. IMU power-rail boot-settle delay — RESOLVED-BY-DATASHEET (35 ms found; margin now 40 ms)

**File:** `firmware/src/platform/nrf52/jh_imu.cpp`, `init()`'s `delay(40)`
after driving `PIN_LSM6DS3TR_C_POWER` high.

**RESOLVED 2026-07-29:** this item previously read "20 ms, unconfirmed
guess — no datasheet figure was available." The real LSM6DS3TR-C
datasheet's electrical characteristics table gives **Ton (turn-on time,
power-up to first valid output) = 35 ms** — extracted directly from the
datasheet for this fix (the earlier authoring environment couldn't fetch
the PDF cleanly; a later pass could). The old 20 ms sat BELOW that figure —
masked so far only by incidental boot ordering (other setup work already
burning enough wall-clock time before the bus gets touched), not by the
delay itself being adequate. The delay is now **40 ms**, a deliberate
margin above the cited 35 ms rather than a bare guess.

**Verify:** nothing left to guess at — this is now a cited spec number with
margin, not an open question. If `selftest`'s `i2c`/`config` rows ever fail
intermittently right after a fresh power-on regardless, that would point at
something OTHER than this delay (wiring, the rail itself, a different
board revision).

## 8. QSPI deep power-down (0xB9/0xAB) — BIT ON FIRST POWER-ON; wake-before-begin was a no-op (fixed); current draw still unmeasured

**File:** `firmware/src/platform/nrf52/jh_store.cpp`, `flashWake()`/`flashSleep()`,
and `init()`'s mount-retry.

`Adafruit_FlashTransport_QSPI::runCommand()` was confirmed to compile and
to issue a genuine single-byte custom QSPI instruction (traced into the
installed `Adafruit_FlashTransport_QSPI_NRF.cpp`), so the deep-power-down
enter (0xB9) / release (0xAB) commands are real, not stubbed — but:
(a) the 100 µs post-wake recovery delay is a guess, not a P25Q16H datasheet
figure; (b) whether 0xAB is safe to issue when the chip was never asleep
(e.g. right after `flash.begin()`, which itself issues a RESET command)
hasn't been confirmed on real silicon, though it's standard, generally-safe
SPI-NOR practice; (c) the actual µA delta this buys (docs/sense.md §3.6's
whole reason for existing) is completely unmeasured.

**THIS ITEM BIT, FIRST — the port's first real silicon bug (2026-07-31):**
first power-on self-test read `flash FAIL detail=mount_failed`, every boot,
warm or otherwise — because the chip was ALREADY in deep power-down (left
there by Seeed's factory firmware; USB power had never been removed since
unboxing), and `init()`'s original `flashWake()`-before-`begin()` was a
**silent no-op every boot**: `runCommand()` calls `nrfx_qspi_cinstr_xfer()`
directly, which errors out unless the QSPI peripheral has been initialized —
and only `begin()`'s transport setup does that. So the release never
reached the chip, the JEDEC probe read garbage, and the mount failed. A
cold power cycle (unplug, 5 s, replug — DPD does not survive power removal)
confirmed the diagnosis: mount + first-boot format immediately succeeded.
That made the latent half obvious: our OWN `flashSleep()` at the end of
every successful `init()` re-arms the same trap for every subsequent warm
reset (reset tap, watchdog, DFU cycle) — storage would only ever have
worked on cold boots. **Fix (same day):** `init()` now attempts the mount
first, and on failure — the failed `begin()` having configured the QSPI
peripheral, a JEDEC miss not tearing it down (confirmed in the installed
`Adafruit_SPIFlashBase.cpp`: `_trans->begin()` runs before the probe) —
issues the wake and retries once. **Verified on silicon:** after a serial-DFU
reflash (a warm reset; the bootloader never touches QSPI, so the chip was
provably still in DPD), `flash PASS 2085376B_free` — and since the
factory-DPD boots proved `begin()` cannot succeed against a sleeping chip,
that pass MUST have come through the wake-retry path. Stored data survived
the cycle. Also answers (b) for the only case that matters now: the retry
path's 0xAB goes to a chip that genuinely is asleep; the bracketing
`flashWake()` calls elsewhere run against an initialized peripheral and
remain standard-practice safe.

**Verify (remaining):** (a) the 100 µs post-wake delay vs the P25Q16H
datasheet's tRDP, and (c) a USB power meter with the device idle
(recording paused, no BLE commands): confirm current drops noticeably
between write bursts vs. a build with `flashWake()`/`flashSleep()`
temporarily stubbed out.

## 9. `micros64()`'s wrap-tracking assumes it's called at least every ~71 minutes

**File:** `firmware/src/platform/nrf52/jh_clock.cpp`.

The Adafruit core's `micros()` is, like the ESP32's, only 32 bits and wraps
at ~71.6 minutes. This file turns it into a 64-bit, effectively
non-wrapping counter by comparing each reading to the last and bumping a
wrap counter whenever it goes DOWN — which only correctly detects a wrap if
called at least once per wrap period. This reasoning is sound by
construction, not a guess — but it has never been run continuously across
a real 71-minute boundary on this chip to see it happen live (the ESP32
sibling has years of field time doing exactly this; this port has none
yet).

**Wrap-hole FOUND, then FIXED (2026-07-29):** the assumption above ("called
at least once per wrap period") turned out to have a real hole.
`main.cpp`'s `loop()` calls `jh_clock::micros64()` itself every
sample-loop pass (>= `JH_SAMPLE_HZ` = 200/s) — but only AFTER an
`if (!sensor_ok) { delay(10); return; }` early return. If the IMU is ever
down (a wiring fault, later recovered by re-running `selftest`) for
**longer than the 71.6-minute wrap period**, that early return starves
`micros64()` entirely for the whole outage: no call means no wrap gets
detected, so when the sensor recovers and sampling resumes, every
subsequent timestamp this boot is off by ~71.6 minutes (a discontinuity,
not a crash — the same failure mode this item's own "Verify" paragraph
below describes, just triggered by a sensor outage instead of a genuine
71-minute soak). **Fix:** `firmware/src/platform/nrf52/jh_link.cpp`'s `pump()` — which
`loop()` calls unconditionally, every pass, BEFORE the `!sensor_ok` early
return — now also calls `jh_clock::micros64()` itself (discarding the
result; the call's only purpose is its wrap-tracking side effect), keeping
the tracker alive across any length of sensor outage. Platform-local fix
(this port's `jh_link.cpp` only) since the underlying wrap-tracking scheme
itself is chip-specific.

**Verify:** a long (>90 minute) continuous bench soak with the device
actively sampling throughout; confirm no `t` discontinuity appears in the
resulting trace around the 71-minute mark (the ESP32's own history is
already the evidence this WOULD show up obviously if wrong — a jump
mid-flight at the wrap instant, per the seam's own doc comment). ADDITIONALLY,
now that the sensor-down hole above is understood: a bench soak that
deliberately disconnects the IMU for >71.6 minutes, then reconnects and
runs `selftest` to recover it, confirming no discontinuity appears in the
trace resumed afterward either.

## 10. Serial port naming — already handled by the existing CLI, confirmed by reading it (not a new risk)

**File:** not a firmware file — noting this here because docs/sense.md
§3.11/§7 item 9 flags it as a thing to check. `tools/jump`'s own
`KNOWN_USB_GLOBS` (unread/untouched by this port, per the DO-NOT-TOUCH list)
already includes `/dev/cu.usbmodem*` alongside the WCH/CH34x patterns the
ESP32 boards need — native USB boards like this one enumerate as
`usbmodem…` on macOS, not `wchusbserial…`, and the CLI already widens for
it. No code change was needed or made.

**Verify:** confirm `./tools/jump wizard`/`selftest` (no `--fake`) actually
finds the board's port automatically on macOS; if `pyserial` is installed
its own `list_ports` path is used instead and should need nothing extra
either.

**CONFIRMED 2026-07-31:** the board enumerates as `/dev/cu.usbmodem101`
and `./tools/jump selftest` found it automatically, first try, no flags.
One stale-text note: the CLI's "the board resets when the port opens —
normal" banner is ESP32-era — opening this board's CDC port does NOT reset
it (four selftest connects in a row left the same boot running). Harmless,
just inaccurate here.

## 11. Boot-time trace scan cost scales with how full the trace region is

**File:** `firmware/src/platform/nrf52/jh_store.cpp`, `findTraceAppendPoint()`'s
comment.

Reconstructing `trace_bytes()`'s CSV-equivalent estimate across a reboot
requires decoding every stored block once (not just finding the append
offset) — a genuinely full ~1.93 MB trace region means walking on the order
of tens of thousands of blocks at boot, all before `READY`. No hardware
timing exists to say whether this is milliseconds or multiple seconds.

**Verify:** fill the trace region close to capacity (a long bench soak with
`--fast`/repeated `drop`/`desktest` cycles, or just let it run), power-cycle,
and time how long boot takes to reach `READY` vs. an empty board. If it's
too slow, the fix (not built here, to keep the on-disk format simple for
this pass) is a periodic checkpoint of append-offset + running CSV byte
count, so a reboot only replays the tail since the last checkpoint.

Logic host-verified (tools/tests/test_store_host.py); bench now verifies timing/the real chip only.

## 12. `trace_is_full()` means "the physical region is full", not "`JH_TRACE_MAX_BYTES` CSV-equivalent bytes" — a deliberate platform difference, not a bug

**File:** `firmware/src/platform/nrf52/jh_store.cpp`'s file-level comment
("DEVIATION" paragraph).

`config/params.json`'s `JH_TRACE_MAX_BYTES` (2,000,000) was sized for the
ESP32's own CSV-on-flash format, where 1 CSV byte ≈ 1 stored byte. Binary
trace v2 stores the same session in roughly 1/7th the bytes — gating
fullness on the same small constant would silently discard most of the
capacity gain docs/sense.md §3.2 promises (~45 min → ~5 h). This platform's
`trace_is_full()` instead means "the trace region's actual QSPI bytes are
exhausted." Net effect: a Sense board will keep recording (and
`trace_bytes()`'s CSV estimate will climb) far past 2,000,000 before
`STATE`/`# trace log full` ever fires — expected, not a leak.

**Verify:** confirm a long bench soak actually reaches multiple hours of
moving-time trace before `trace_is_full()` trips, roughly matching
docs/ota.md §4.5's ~3.6–5 h math for this flash size.

Logic host-verified (tools/tests/test_store_host.py); bench now verifies timing/the real chip only.

## 13. `clear()` can block the device for a while on a nearly-full trace region

**File:** `firmware/src/platform/nrf52/jh_store.cpp`, `clear()`.

Unlike the ESP32's near-instant `LittleFS.remove()`, this platform's
`clear()` erases every sector that's ever been written (proportional to how
full the trace region was, not a fixed cost) before rewriting the
superblock — QSPI sector erase timing (tens to a few hundred ms each,
typical for this flash class) times up to ~495 sectors in the
worst case. `jh_store.h`'s `clear()` has no announce/progress callback, so
there's no way to warn the user mid-operation; the device (sampling, BLE,
serial commands) is simply unresponsive for the duration.

**Verify:** time a `clear` command after a long session vs. right after
boot; if the worst case is user-hostile in practice, the fix is a
courser-grained erase (e.g. `eraseChip()` traded against always taking the
same, fixed, longer time) or accepting the trade-off explicitly in
BUILD.md-equivalent user docs — not decided here.

Logic host-verified (tools/tests/test_store_host.py); bench now verifies timing/the real chip only.

## 14. Bluefruit two-central mechanics — re-derived from source, never run against real centrals

**File:** `firmware/src/platform/nrf52/jh_link.cpp`'s file comment (full
section on advertising restart + the two-central TX design).

`Bluefruit.begin(2, 0)`, the advertising-restart gap on both connect AND
disconnect (confirmed by reading the library source AND by noting even
Adafruit's own official `bleuart_multi.ino` example has the same gap on
disconnect), and the per-connection MTU/notify fan-out were all worked out
by reading Bluefruit52Lib source and one official example — never
exercised against a real phone + a real Garmin watch simultaneously.

**Verify (docs/sense.md §7 item 1):** connect a phone (Bluefy) AND a second
central (another phone, or the Garmin field once it exists) at the same
time; confirm both see `JUMP`/`STATE` lines live, both can send commands,
and disconnecting either one leaves the puck still advertising and still
serving the other.

**HALF-CONFIRMED 2026-07-31:** the single-central path is fully proven —
Bluefy connected, ran a self-test over BLE (commands in, rows out), showed
the live INFO readout, and BLE + serial were served simultaneously all
morning. The genuinely-two-BLE-centrals test (and advertising restart
after the first connect) remains open — needs a second phone/iPad or the
Garmin field.

## 15. NUS UUID + name are in the advertisement/scan response — never scanned-for by a real central

**File:** `firmware/src/platform/nrf52/jh_link.cpp`, `begin()`'s advertising
setup (`Advertising.addService(s_bleuart)` + `ScanResponse.addName()`).

Mirrors the exact sequence Adafruit's own official multi-connection example
uses, which strongly suggests it's correct — but was never checked with an
actual BLE scanner (nRF Connect, a Garmin Connect IQ scan) confirming the
128-bit NUS service UUID is visible in the advertisement and `JumpHeight`
shows up as the name.

**Verify (docs/sense.md §7 item 2):** scan for the device with nRF Connect
or similar; confirm the NUS UUID is listed and the name reads `JumpHeight`.

**CONFIRMED 2026-07-31 (S0 day, first central ever):** Bluefy on iPhone
found `JumpHeight` in its scan sheet (Bluefy filters on the NUS service
UUID, so its appearance alone proves the UUID is in the advertisement and
the name in the scan response), connected, and the web app rendered the
full `INFO` readout — firmware version, 200 Hz, params, calibration —
without knowing the chip underneath had changed. Advertising → connect →
notify round-trip all working against a real central.

## 16. UF2 flashing procedure — never actually dragged a file onto a real Sense board

**File:** `.github/workflows/build.yml`'s new `.uf2` conversion step;
`tools/uf2conv.py`.

The conversion itself is proven: `uf2conv.py` run locally against this
port's actual `firmware.hex` build produces a valid `.uf2` (family id
`0xADA52840`, correct block count/magic — see this port's commit message
for the exact byte count). The DEVICE side of the procedure — double-tap
reset → a `XIAO-SENSE` USB mass-storage drive appears → drag the `.uf2`
file onto it → the board reboots into the new firmware — is standard
Adafruit nRF52 bootloader behavior (matches docs/sense.md §1's own
citation) but was never performed against the real board.

**Verify (docs/sense.md §7 item 9):** the actual drag-and-drop flash cycle,
end to end, ideally right after first unboxing (S0 milestone).

**CONFIRMED 2026-07-31, with field notes:** the first flash went exactly as
described — 1200-baud touch (a plain `stty -f <port> 1200` sufficed) →
`XIAO-SENSE` drive → `cp` the `.uf2` → board reboots into the app. The
SECOND bootloader entry was flakier: the stty touch didn't take (a
pyserial open-at-1200 + DTR toggle did), and that time the bootloader's
mass-storage side never mounted on macOS (device present in `ioreg` as the
bootloader, VID 0x2886 / PID 0x0045, but no disk ever appeared). No fight
needed: `pio run -e xiaoblesense_adafruit -t upload` (adafruit-nrfutil
serial DFU over the bootloader's CDC port) programmed it cleanly in ~23 s.
Practical takeaway: UF2 drag-drop is real but macOS's automount is not to
be trusted twice in a row; serial DFU is the reliable scripted path.

## 17. PDM microphone rail — never measured

**File:** not touched by this port at all (deliberately: no PDM code
exists anywhere in `src/platform/nrf52/`).

docs/sense.md §7 item 10 asks to confirm the PDM mic stays unpowered by
default. This port never enables `PIN_PDM_PWR` (P1.10, per the variant),
and nothing in the Arduino core's own startup path was seen to touch it
either — but this was not exhaustively traced, and no current measurement
exists either way.

**Verify:** an idle-current measurement (item 8's power meter) with and
without a deliberate `digitalWrite(PIN_PDM_PWR, LOW)` added temporarily; if
the numbers match, it's already off.

## 18. Watchdog reload behavior — set up, never watched actually catch a hang

**File:** `firmware/src/platform/nrf52/jh_link.cpp`'s `wdtInit()`/`wdtFeed()`.

Register names/offsets and the reload magic value (`0x6E524635`) were
confirmed against the installed core's own Nordic device header (cited by
file+line in the code), matching the nRF52840 Product Specification's WDT
chapter — this is a high-confidence citation, but the watchdog has never
actually been watched to (a) NOT fire during normal operation and (b) DOES
fire (resetting the board) when something is deliberately hung.

**WDT SCOPE — read this before assuming the watchdog protects everything
(2026-07-29):** `wdtInit()` runs inside `jh_link::begin()`, which
`main.cpp`'s `setup()` calls AFTER `jh_store::init()` (mounting/formatting
the QSPI flash). That ordering is deliberate (a boot-time format that later
tripped the watchdog mid-format would be worse — a reset loop instead of a
clean, if slow, first boot), but it means **no watchdog protection exists
at all** for anything `jh_store::init()` does, including the underlying
Adafruit SPIFlash library's own `waitUntilReady()`-style busy-poll (waiting
on the flash chip's WIP/busy bit) — which has **no software timeout of its
own** in the library. **Diagnostic: if a fresh/blank board hangs before
ever printing `READY` (no crash, no reset, just silence forever), the most
likely cause is the QSPI flash chip itself stuck busy** — a soldering/joint
problem on the QSPI bus, not a firmware logic bug. There is nothing
firmware-side that will time this out or recover from it; the fix is
physical (cable/reseat/inspect the QSPI joints on the board), not a
`selftest` or reflash. Once past `jh_link::begin()` — which now feeds the
watchdog once at its own end, right before returning control to `setup()`
(see this port's fix for review-nrf52.md finding #4) — every remaining
first-boot step (`jh_persist::init()`, `loadCalibration()`, `runSelfTest()`
— its accel check alone loops 100x `delay(5)` — `scanStoredJumps()`) runs
against a comfortably fresh ~3.5s budget instead of whatever was left over
from `wdtInit()`'s own reload.

**Verify:** flash a build with an intentional infinite loop somewhere in
`loop()` (temporarily) and confirm the board resets within a few seconds;
then confirm a normal, long-running session never resets on its own. If a
fresh board genuinely hangs pre-`READY`, don't expect a watchdog reset to
rescue it — go straight to inspecting the QSPI flash chip's connections
per the diagnostic above.

## 19. Jump/trace region sizing — never stress-tested against a marathon session

**File:** `firmware/src/platform/nrf52/jh_store.cpp`'s geometry constants
(`JUMPS_REGION_BYTES = 65536`, 2048 jump-record slots).

2048 stored jumps is a lot for any single session before a `clear`, and the
trace region's capacity is covered by item 12 — but neither has been
exercised against a genuinely marathon (many-hour, many-jump) real session.
`jumps_append()` silently stops accepting new jumps once its region is full
(matching the trace region's own "silently stop" stance) rather than
wrapping or erroring loudly.

**Verify:** a long bench/water-adjacent soak test with unusually frequent
jumps; confirm `stored_jumps` in `STATS` keeps climbing sanely and doesn't
silently plateau well under a real session's jump count.

Logic host-verified (tools/tests/test_store_host.py); bench now verifies timing/the real chip only.

## 20. Accel range is ±16 g on this platform (ESP32 build stays ±8 g)

**File:** `firmware/src/platform/nrf52/lsm6ds3_min.h` (`CTRL1_XL = 0x54`,
`g_per_lsb = 0.000488`).

Deliberate divergence, research-backed (docs/research.md §2/§6): real
landings peak 4.2–5.5+ g and marine impact literature runs 7–10 g+, so
±8 g clips landing peaks; detection thresholds (0.35 g / 2.5 g) are
unaffected and the binary trace's u16 milli-g format has ±16 g headroom
by design. **Bench check:** the boot self-test's resting-gravity reading
is itself the scale-factor verification — if the range/sensitivity pair
were mismatched (e.g. 0x54 with the ±8 g LSB), rest would read ~0.5 g or
~2 g and the self-test would WARN/FAIL immediately. Confirm rest reads
≈1.0 g and a hard table slap exceeds 8 g in the trace.

**HALF-CONFIRMED 2026-07-31:** rest reads 0.979–1.021 g across boots
(item 4) — the range/sensitivity pair is right. The >8 g table-slap trace
check hasn't been run yet.

## 21. Interrupted ERASE during first-boot format / `clear()` — an unmodeled corruption shape (bench awareness note)

**File:** `firmware/src/platform/nrf52/jh_store.cpp`, `init()`'s
first-boot format path and `clear()`.

Both paths erase one or more sectors and then rewrite the superblock as a
multi-step sequence: `init()` prints `"# first boot: formatting storage —
takes up to a minute, hang tight..."`, calls `eraseChip()`, then
`writeSuperblock()`; `clear()` erases the superblock sector + every
jumps/trace sector ever written, then rewrites the superblock. A power cut
landing mid-erase (as opposed to mid-WRITE, which the existing torn-write
recovery — `skipPastTornWrite()`/`isErasedBytes()` — was specifically
designed for and is well-tested against, including the exact-last-slot
case) is a genuinely different corruption shape: an ERASE that's only
partway done can leave a sector in some chip-specific intermediate state
that is neither "cleanly erased" (all-0xFF) nor "whatever was there
before" — real NOR flash sector-erase operations are not required to be
interruptible-and-resumable the way a byte/page write is, and this
possibility was not part of what the recovery scheme (jumps/trace
append-point scanning, the superblock magic/version/CRC check) was designed
to detect or recover from. This is a real, user-reachable scenario: anyone
power-cycling the board while the "formatting storage" message is on
screen (first boot) or immediately after typing `clear` hits exactly this
window.

**Practical consequence, honestly stated:** this is NOT fixed here (harder
than the return-value/no-resurrection fix `jh_store.cpp`'s `clear()` DOES
now have for a same-process, non-fatal erase FAILURE — see this file's
`clear()` and `tools/tests/test_store_host.py`'s
`test_failed_clear_leaves_not_ok_and_no_resurrection` — that's a different,
narrower shape: the chip reports failure but keeps running; THIS item is
about a genuine power cut, mid-erase, across a reboot, which the mock's
fault injection deliberately does not model either — see
`firmware/test/store_host/mock_flash.h`'s own note on interrupted-erase
being out of scope for that harness). Worst case on real silicon: a
reboot's `superblockValid()` check fails (an in-progress erase would very
plausibly also corrupt the superblock sector itself, sector 0, which both
paths erase first or early) and the device re-formats from scratch,
losing everything — the SAME outcome as any other unreadable superblock,
just via a corruption path the design didn't specifically reason through.
No SILENT wrong-data case is currently known, but it hasn't been
specifically hunted for either.

**Verify (bench-only, not code):** if practical, a deliberate power-cut
test mid-format (first boot) and mid-`clear()` (after a session with
data), confirming the device always comes up EITHER fully formatted/empty
OR with its pre-clear data intact — never a state that reads back garbage,
hangs, or reports success while actually corrupted. Not built into the
host test harness (`mock_flash.h` only interrupts WRITEs, by design — see
that file's own scope note) — a future harness mode that can also tear
down mid-`eraseSector()`/`eraseChip()` call would be the natural way to
cover this without needing real hardware.

## 22. QSPI flash addresses MUST be word-aligned — found on silicon, fixed, now modeled in the host harness *(added 2026-07-31)*

**File:** `firmware/src/platform/nrf52/jh_store.cpp` (`align4()` and every
trace-region offset), `firmware/test/store_host/mock_flash.cpp`
(`roundDownUnaligned()`), `sim/trace_codec.py` (`decode_region()`).

Not one of the original 21 items — real silicon added it to the list. The
nRF52840 QSPI peripheral requires WORD-ALIGNED flash addresses for bulk
READ/WRITE, and nothing in the stack compensates: `nrfx_qspi_write()`/
`read()` validate only the RAM-side pointer, then load the flash address
into `WRITE.DST`/`READ.SRC`, where the hardware silently drops the low
2 bits — a transfer aimed at offset 54 lands at 52, clobbering its
neighbor's tail. The byte-packed trace append log hit this on nearly every
block: the S0 bench symptom was a 10-minute session reading back as **274
of ~14,000 samples** (two surviving islands, everything between skipped as
torn), while `trace_bytes()`'s CSV estimate grew at the full rate. Jump
records were immune by accident (32-byte records ⇒ always aligned — now a
`static_assert`ed invariant).

**Fix, verified on silicon same day:** every trace-region offset is
quantized with `align4()` on the write path AND every scan/read path
(writer and readers step identically; the 0–3 pad bytes between blocks
stay erased 0xFF, compatible with the existing torn-write recovery); the
two `off + HEADER_BYTES` payload reads became single whole-block reads
from the aligned block start. The Python mirror gained `decode_region()`
(same align4 stepping — `decode()` keeps byte-packed CODEC semantics, the
distinction is now explicit), and the host mock now MODELS the hardware's
silent round-down with a stderr note, so any future byte-packed access
pattern corrupts in CI the same way it corrupts on the bench. After the
fix: a 14,402-sample session read back complete at a metronomic 20.0 ms
cadence, zero loss.

## 23. The motion gate is honest — a bare board on a USB cable is a seismometer *(bench note, 2026-07-31)*

**File:** none — no code change; this is a bench-environment finding.

S0's longest-lived false alarm: the motion gate appeared "stuck recording"
on a still board for most of the morning (trace estimate growing ~continuously
while the board sat untouched). Full-rate (200 Hz log) captures settled it:
the trips are genuine, multi-sample ~20 Hz mechanical oscillations
occasionally cresting the 0.12 g threshold — the 2.8 g board dangling on
its USB-C cable rings like a mass on a spring at every footstep, phone
set-down, or fan ramp on the same desk, and ONE crest legally buys
`JH_IDLE_TIMEOUT_S` = 20 s of recording. A genuinely quiet minute reads
`trace_bytes=0`; gate enter/exit mechanics verified exact (idle at trip
+20.0 s). BLE was exonerated (a connected phone link contributed nothing).
No firmware change: on the water the puck is strapped to a board and this
sensitivity is what you want. Bench expectation: a tethered bare board on
a live desk WILL record intermittently — that's the gate working.

---

## Explicitly out of scope for this pass (not bugs, not forgotten)

Matching docs/sense.md's own "S1 — the port" milestone (platform seams +
detector/protocol/self-test + binary trace storage) — everything below is a
LATER milestone (S2 battery/sleep, S3 update path, S4 the puck, S5 the
metrics ladder) and was deliberately not touched here, so nothing about it
appears above as a VERIFY item:

- Battery telemetry, charge-current selection (P0.13), low-voltage System
  OFF (docs/sense.md §3.4) — firmware reports nothing about the battery yet.
- Sleep/wake, System OFF, the IMU hardware motion interrupt
  (`PIN_LSM6DS3TR_C_INT1`, unused by this poll-loop port) (docs/sense.md §3.5).
- The RGB status LED language (docs/sense.md §3.10) — no LED code exists in
  this port.
- Nordic DFU / `BLEDfu` (docs/sense.md §3.3) — this port's CI publishes a
  `.uf2` for cable/drag-drop recovery and first-flash only; wireless update
  is unbuilt.
- Antenna keep-out / range testing (docs/sense.md §3.11) — a housing/RF
  concern, not firmware.
- Drop calibration re-run on the Sense build (docs/sense.md §3.7) — a bench
  activity, not something firmware can pre-verify.
