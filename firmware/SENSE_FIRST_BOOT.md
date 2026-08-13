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

**TWO CENTRALS RAN FOR REAL, 2026-08-11 — and this item is now the prime
suspect in an open bug.** The Garmin field (Epix Gen 2) and
`tools/blecmd.py --watch` (Mac, one persistent connection) were subscribed
simultaneously for over an hour.

*What passed:* both centrals were served concurrently. The puck kept
advertising while connected, the Mac's `stats` round-tripped correctly the
whole time, and killing the Mac's central left the watch still connected.
That is the core of this item's acceptance criterion, finally exercised
against two real centrals rather than re-derived from library source.

*What FAILED:* the watch's numbers were corrupt while both were subscribed
— it displayed a jump count of 64 and a best of 0.3 ft at a moment the puck
itself reported `session_jumps=1 session_best_m=0.164`. Fields went missing
mid-line (`airtime_s`, `best_m`) while their neighbours (`n`, `height_m`)
arrived fine. Full evidence table and the two ruled-out causes are in
`garmin/FIRST_COMPILE.md` under "OPEN BUG — corrupted values on the watch".

*Explicitly NOT the cause:* the `s_mtu` adopt-from-any-reporter bug in
`platform/esp32/jh_link.cpp`. That is the FireBeetle's NimBLE path. This
board runs `platform/nrf52/jh_link.cpp`, whose `sendOneChunk()` takes the
MINIMUM MTU across subscribed connections, queries it fresh per chunk, and
writes per-connection rather than broadcasting one buffer. Verified by
reading it. (The ESP32 bug is still real and still worth fixing on that
platform — it is just not this.)

*Leading hypothesis:* Connect IQ silently dropping notifications, with two
subscribers doubling per-chunk work in `sendOneChunk()` and the second
central adding traffic. **Untested.** Settle it by rendering the raw
received line on the watch for one sideload before changing any firmware —
two confident wrong diagnoses were already produced by reasoning from
rendered numbers instead of received bytes.

*Still untested even now:* whether the corruption survives with the watch
as the ONLY central. Until that single-central run happens, "two centrals"
is a correlation, not the cause.

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

## 16b. OTA DFU — the sealed box's only firmware path, and it is NOT yet trustworthy

**Files:** `firmware/src/platform/nrf52/jh_link.cpp` (`BLEDfu` service +
`reboot_to_dfu()` / the `dfu` command), `tools/otadfu.py` (legacy DFU over
CoreBluetooth from the Mac).

**Why this outranks nearly everything else:** measured on silicon
2026-08-11 — in OTA-DFU mode the board exposes **no USB at all**. No CDC
port, no UF2 drive. Once the capsule is sealed, this path is not a
convenience; it is the only rescue there is.

**What is PROVEN on silicon (2026-08-11, first bench night):**
- The app-side `dfu` command reboots into the bootloader and `AdaDFU`
  appears on the air. (First attempt used GPREGRET=0xB1 and bounced
  straight back into the app — 0xB1 is `DFU_MAGIC_OTA_APPJUM`, the
  "app jumped here with the SoftDevice live" handshake, false through a
  full reset. The reset path wants 0xA8 = `DFU_MAGIC_OTA_RESET`; the core's
  own `enterOTADfu()` does exactly this and is what ships.)
- The legacy DFU protocol implementation is essentially right: one run
  streamed the full 157 KB image to 98%.
- Stale-state recovery (control opcode 0x06 = system reset) works — **but
  only with USB out.** With USB attached the plain reset lands in
  UF2/serial mode and OTA never reappears.
- Two tool bugs found and fixed: packet-receipt notifications report
  BYTES (comparing `acked * CHUNK` made the flow-control window look ~20×
  emptier than it was — 157 KB firehosed into a bootloader that
  flash-writes per packet, error 0x06 at 98%); and a retry wrapper that
  double-sent START across two connections.

**What FAILED, and must gate the tape:** a transfer died mid-stream
(receipts stalled at 4,380 bytes; macOS CoreBluetooth notification
delivery is the suspected flake) and the bootloader came back **DARK** —
20 s of active scanning found no `AdaDFU`, no DFU service UUID, no NUS,
nothing connectable. A sealed box in that state is a paperweight until the
battery dies. Radio-only recovery failed; the cable was the way out.

**VERIFY before sealing (docs/sense.md §3.3's own AC, sharpened):** the
complete loop — app → `dfu` → transfer → validate → activate → app back on
the air — **twice consecutively, USB out, via `tools/otadfu.py`**, plus
once via nRF Connect on a phone (the known-good client, and the beach
fallback). Additionally worth knowing: what state the bootloader is in
after a mid-transfer disconnect (dark? timeout? does it recover on its
own after N minutes?) — that answer decides whether a failed beach OTA is
"retry" or "go home".

Until every box above is checked, **do not seal the capsule.**

**BENCH SESSION 2 (2026-08-11 evening) — transfer SOLVED, trigger now the
open item.** The full loop passed once, end to end, wireless: 158,064 bytes
in 112 s, every checkpoint byte-verified, validated, activated, app back on
the air with calibration intact. What it took, and what remains:

- **Packet-receipt notifications are UNRELIABLE under load — measured.**
  At 20 ms/packet every receipt arrives; at 2 ms/packet the receipt stream
  dies after ONE notification while the link stays up (disconnect callback
  proved the link; control-point 0x07 probe proved the bootloader had
  received every byte). The bootloader's notify has a single-slot queue and
  silently skips receipts it cannot send. **Any DFU client that hard-blocks
  on receipts will stall.** tools/otadfu.py therefore paces at a fixed
  12 ms/packet, treats receipts as opportunistic, and verifies progress
  every 10 KB via opcode 0x07 (report received image size) — a control
  exchange, reliable in every run. Byte loss is detected within one
  checkpoint instead of at the final CRC.
- **The `dfu` trigger is INTERMITTENT, cause not yet isolated.** Same
  binary: three entries into AdaDFU, then repeated bounces straight back
  into the app. Both GPREGRET write forms behave identically (raw
  NRF_POWER->GPREGRET via the core's enterOTADfu(), and SD-aware
  sd_power_gpregret_set) — so the API-choice hypothesis is DEAD; the
  correlate is something else (charging state? time-since-boot? SD radio
  activity at reset?). The current build prints a GPREGRET readback line
  (`# gpregret rc=../../.. val=0x..`) before resetting — capture it on the
  next bench session; it decides write-failed vs bootloader-ignored.
- Late in the session USB dropped entirely (no CDC, no drive; app fine on
  battery) and macOS CoreBluetooth grew flaky after ~50 connect cycles —
  scans intermittently blind to an advertising device. Bench sessions this
  connect-heavy should expect that and re-scan patiently.
- Iteration was STOPPED deliberately at that point: with no USB safety
  net, one failed DFU entry = dark puck until physically reset. Next
  session starts by re-seating the cable.

**GATE PASSED 2026-08-12 ~15:00**: two complete OTA loops back-to-back on
the (sensor-dead but radio-healthy) original board — 113 s + 114 s, every
10 KB checkpoint verified against the bootloader's own byte count,
validate + activate clean, app back with calibration intact after both.
Address-pinned via OTADFU_ADDR (two boards now share the JumpHeight name).
Trigger reliability: resolved — every "flaky trigger" was macOS transport
(unverified sends) or wrong-firmware archaeology; the verified trigger has
not missed since. Still open, ceremony only: one USB-out run (human
unplug) and one nRF Connect phone run. Dark-state timeout
characterization remains open.

## 16c. BOOT HANG at the first I2C probe — RESOLVED 2026-08-12: hardware, not firmware

**RESOLUTION:** the LSM6DS3TR-C (or its power path) failed in hardware.
Full RCA with the exoneration experiment, evidence table, firmware audit and
prevention actions: **[../docs/rca-sense-imu-2026-08-11.md](../docs/rca-sense-imu-2026-08-11.md)**.
The decisive fact: `a6e477d` — the exact firmware that recorded 61 jumps
that afternoon — hangs identically on the cold-started board. The bounded
probe below (originally the mitigation) is now permanent policy.

Original investigation notes follow, kept for the evidence trail:

**Status 2026-08-11 ~23:30: the app boot-loops, dying inside the selftest's
first I2C transaction** (`jh_imu::probe` — Arduino `Wire` has no timeout, so
a held bus hangs forever; captured twice over serial as a banner that stops
at `SELFTEST BEGIN`). Wedged boots were intermittent through the evening
(healthy serial-flash boots at 19:52 and a healthy OTA boot at 20:33) and
became persistent around 22:20.

**Dead hypotheses, with the evidence that killed each:**
1. *loop() early-return on sensor failure* — wrong: loop() polls commands
   BEFORE the `!sensor_ok` return (main.cpp:711 vs :714).
2. *IMU rail power-cycle fixes it* — a LOW pulse on P1.08 at init did not
   change the outcome.
3. *Back-powering through the bus pull-ups defeats the rail cycle* — driving
   SDA/SCL low during a 150 ms rail-off window did not change the outcome
   either. (Both changes are KEPT — they are correct hardening — they just
   were not the cure.)

**Standing facts:** the BLE task stays fully alive through the hang
(advertising, connectable, and the BLEDfu control point works — used all
night as a remote reboot). The battery means NO reset ever removes power
from the sensor: every "power cycle" tonight was CPU-only. The gyro config
writes (CTRL4_C/CTRL6_C/CTRL7_G, commit d504c54) are the newest code that
touches this sensor and shipped the same day the wedge first appeared.

**Morning plan, in order:**
1. **Battery disconnect** (the box is open): a true cold start is the one
   discriminator software cannot fake. Healthy after it ⇒ the sensor was
   hardware-latched and the hunt moves to what latched it (prime suspect:
   the new gyro config sequence). Still wedged ⇒ the fault is not
   sensor-state and the map redraws.
2. **Bounded probe, regardless of cause:** the boot path must never hang on
   a dead bus. Give the first I2C touch a timeout (TWIM-level; Arduino
   Wire won't do it) so a wedged sensor produces `SELFTEST i2c FAIL` and a
   live, commandable device instead of a boot loop. This is the real
   robustness fix and is independent of the root cause.
3. Then re-run the OTA gate pair (transfer machinery is proven — three
   complete flashes including the bootloader's own 0.6.1→0.11.0 update).

## 16d. Gyro-crash-hunt results (2026-08-12) — five fixed, one deferred, core exonerated

An 18-agent adversarial hunt (sanitizers over 2.16M-sample streams,
line review, independent repro harnesses) for the new board's dark-out.
**The C++ gyro core was exonerated for crash/hang/deadlock** — every loop
provably bounded, 12 h EMA and 40 h float-time soaks clean. The dark-out
itself was the platform chain (CDC spin -> WDT -> pre-watchdog boot window),
fixed in afba536. The hunt's OTHER confirmed findings, all repro'd before
fixing and pinned as harness tests (gyro_bias_harness.cpp):

- **Railed gyro + armed lever = detector livelock + calibration corruption**
  -> three guards: gyro_bias rail guard (>=1900 dps never enters the EMA),
  lever_arm kGyroClipGuardDps, correct_for_spin passes raw through when
  rot_g exceeds the accel's own full scale (never manufacture free-fall).
- **Lever commit skipped on gyro-absent landings** -> transition tracking
  hoisted; commit on validated jumps only; discard() (pending-only, the
  converged value survives) on every other AIRBORNE exit. This also kills
  the "rejected flights still commit" corruption path.
- **Bias EMA seeded from a moving first sample** -> seed only from samples
  plausibly a bias (<20 dps; ZRL spec is +-10).
- **`gyro` bench command starves the WDT** (2.05 s of delay(100)) -> feeds
  per iteration.

**DEFERRED, on the record:** the 200 Hz sample path still reads the IMU
through Wire, whose event waits are unbounded — a mid-session bus wedge is
a WDT reset (now survivable: watchdog-first boot + sticky probe guard turn
it into one reset + sensor-off-until-selftest, not a boot loop). The clean
fix is a vendored bounded-TWIM mini-driver (twim_min.h, per the DECISIONS
#13 minimal-driver precedent) with a micros() deadline on every wait.
Do this before the water day; it is the last unbounded wait on the boot or
sample path.

## 16e. Mule QSPI mount-hang (FIXED: store guard) + a command-silence that was self-inflicted (RESOLVED)

**Evening 2026-08-12.** The mule went dark after an afternoon of heavy
flashing. Neuter-and-bisect (the sensor probe's method, reapplied):
persist bypass — still dark; **store bypass — back on the air**. The
mule's QSPI chip now wedges `jh_store::init` exactly like the sensor bus
wedged the probe: an unbounded external-bus first-contact in the boot
path, the storage twin of 16c's lesson. Somewhere in the same window the
mule's stored history (61 jumps + drop-cal trace) was wiped by a
reformat whose trigger is unestablished — assume bench data on the mule
is volatile until the chip's state is understood.

**Fix shipped:** persist moves FIRST in boot (internal flash, nothing to
wedge); `jh_store::init` is bracketed by a sticky StoreGuard (guard byte
bit 1 — same jh_persist byte as the probe guard, now a bitmask). A mount
hang costs one watchdog reset, then boots skip storage and run live-only
with an honest `flash FAIL` row. `format` is the deliberate retry, itself
guarded. Verified on the mule: the guarded build boots and advertises
stably (60 s continuous adv, no self-reboot).

**RESOLVED 2026-08-12, late evening — the silence was self-inflicted.**
Every "liveness" test of the guarded build had used `selftest` as its
probe — the ONE command that, on a ProbeGuarded board, is the *designed
deliberate retry* of the held sensor bus: it hangs, the watchdog fires,
the board reboots mid-session. The tester was rebooting the device under
test and recording the reboot as a wedge. `info` and `stats` answer
instantly and completely (verified same evening, same Mac, same stack —
after a `blueutil` reset had already failed to change anything, which is
what forced the re-look). Neither the Mac nor the mule was ever guilty;
the Bluefy discriminator is unnecessary. Doctrine written into
docs/bench-playbook.md: **liveness probes must be side-effect-free**
(`info`, never `selftest`) — a probe that reboots the target fabricates
the exact wedge it hunts.

**Also corrected:** "the 61-jump history was wiped by a reformat" (above)
was never established and is now *known unknown* — with StoreGuard
skipping the mount, STATS counts are unknown-not-zero. A non-destructive
`mount` command shipped for exactly this (see below): `try_mount()` never
formats (an invalid superblock is reported, not rebuilt — torture-tested
in tools/tests/test_store_host.py including a byte-for-byte
nothing-touched assertion). First live attempt: the mount still hangs →
one WDT reset → guard re-latches, board back commandable. The chip's
wedged mode survives every warm reset because the battery never lets it
power down (bench-playbook §4). **Next action: two-second battery unclip
+ USB out, then `mount` again** — if the wedge was interrupted-format
mode residue, the history is still sitting in the jumps region.

## 16f. The held-bus signature REPRODUCED on the Puck — clamp theory, predictions on record

**Late evening 2026-08-12.** The Puck (new board), first contact after its
day behind macOS's unanswered USB-accessory Allow prompt: hardened build
flashed clean over the 1200-touch (ISRs were alive under the hung app),
`info`/`stats` answer, `flash PASS 2093056B_free` (playbook prediction:
QSPI mounts after true power-cycle — confirmed). But: `i2c FAIL no_device`
at boot, and `selftest` (the deliberate retry) hangs → WDT reset at
~3.5 s, **deterministically, 5/5 trials** — the mule's exact disease, on
silicon that read 0.970 g two days ago.

**The unifying theory (playbook §4, peripheral corollary):** an I2C slave
caught mid-transaction when its master dies holds SDA low until it loses
power. Both boards died mid-something with the bus active (Puck: yesterday's
CDC-blocked hang; mule: the 08-11 evening wedge era) and have been powered
continuously since (Puck: USB never unplugged; mule: battery). Warm resets,
reflashes, and DFU cycles never release the slave.

**Falsifiable predictions written BEFORE the experiment** — playbook §7
items 1–3: Puck sensor returns after a ~10 s USB-out; mule `mount` (and
possibly its sensor too) returns after battery-unclip + USB-out. If the
Puck still FAILs truly cold, the theory dies and the current firmware's
sensor first-contact goes under the lamp as a reproducible sensor-killer.

**If the theory holds, the software remedy** (folds into 16d's bounded-TWIM
work): the standard 9-clock SCL bus-clear + STOP before `Wire.begin()` —
write-only (immune to the GPIO-read false-negative disease of the dead
probes), harmless on a healthy bus. Honesty note: cc4662c's overnight
probe DID include a 9-pulse bus-clear and the mule stayed un-ACKing — but
that ran inside the same apparatus later proven unreliable at pin control
(PIN_CNF mismatch the leading suspect), so it neither confirms nor refutes.
Implement only AFTER the power-cycle experiment confirms the mechanism,
and verify the pulses with a scope or a second board, not with the code
under test.

### 16f VERDICT, same night: THE FALSIFIERS FIRED. Clamp theory dead.

The experiment ran within the hour (owner replugged both boards):

- **Puck, true cold start (no battery — replug IS power removal):
  `selftest` still hangs → WDT, 5/5.** Prediction 1 FAILED.
- **Archaeology control (the doctrine's control arm): `a6e477d` — the
  binary that read this same sensor at 0.970 g two days ago — BOOT-LOOPS
  on the Puck** (port re-enumerates every ~4 s, the boot-selftest-hang
  watchdog cycle; commands never answered; flashed back off it after).
  The current firmware is EXONERATED for the hang itself; the bus is
  genuinely held, cold, under any firmware. The Puck now presents
  IDENTICALLY to the mule.
- **Mule `mount` still hangs after the power-out** — but this arm is
  UNSCORED until the owner confirms the battery pigtail itself was
  unclipped (a USB-only unplug is a no-op on a battery-backed board,
  playbook §4). If confirmed unclipped: the wedged-mode theory is dead
  too and the mule's QSPI joins the genuinely-sick list.

**What stands after the fire:** two boards, days apart, each losing its
IMU bus permanently (and surviving cold starts) with radio/MCU/flash
healthy. That resurrects the original RCA's environmental candidates —
now with a COMMON-MODE question attached (same bench, same hub, same
kapton-and-tape workflow, same firmware lineage). One noted correlation,
mechanism unknown, kept honest: the Puck ran the probe-era builds
(bit-bang + rail power-cycling — the code that "browned out healthy
sensors") during the 08-12 falsifier session, and was dead within ~a day;
but the MULE's death PREDATES that code entirely, so it cannot be the
whole story. **Next instrument is a meter, not code** (playbook §7):
sensor rail voltage + SDA/SCL levels on both boards, powered. Software
has nothing left to say about this bus.

## 16g. Web findings (2026-08-13, overnight): the back-feed mechanism is DOCUMENTED for this module; off-path fix shipped; meter protocol

Owner's directive: "reach out to the web for anyone else suffering this."
Findings, most important first:

**1. Nordic DevZone (Zephyr LSM6DS3 bring-up on this exact board):**
P1.08 does not feed the sensor directly — it ENABLES a small fixed
regulator whose output (6D_PWR) powers BOTH the sensor's VDD AND the
on-module I2C pull-up resistors (Zephyr devicetree models it as a
regulator with `startup-delay-us = 3000`). The documented hazard, quoted
in substance: the pull-up source voltage **must be 0 V before P1.08
rises**; SDA/SCL energized while the 6D rail is down **back-drive the
sensor through its pins and corrupt its internal power-up sequencing.**
A sensor in that state does not ACK — indistinguishable from dead
silicon from the bus side.

**2. Our firmware commits exactly that hazard, sustained, in `off`:**
`system_off()` cut the rail with Wire1's TWIM still owning SDA/SCL — and
Wire_nRF52.cpp's begin() enables the nRF's own **internal pull-ups**
(GPIO_PIN_CNF_PULL_Pullup, core source lines 56/62), powered from the
MCU domain, alive regardless of the 6D rail. System OFF **retains** pin
state. Net effect: every soft-off left the sensor unpowered with both
bus lines held at 3.3 V for the entire sleep — hours of back-feed
through the die's protection structures. `off` was proven "both
directions" on the mule (54fa232, web Power-off button a3e4889) the day
before its sensor stopped ACKing. The Puck's exposure was different but
same family: the (since-removed) hard rail LOW→HIGH power-cycle code ran
on it during the 08-12 falsifier session — discharging the rail into an
energized bus is the same back-feed, transient edition.

**FIX SHIPPED (unflashed — bench frozen until the meter):** system_off()
now does `Wire1.end()` → SDA/SCL/INT1 to input-no-pull → settle → rail
LOW → System OFF. Builds clean; full sim suite passes.

**3. The archaeology control was CONTAMINATED (16f verdict amended):**
tonight's cold start was followed by the CURRENT build's first contact;
`a6e477d` was flashed afterward over a warm reset only. If any current
boot re-corrupts the power-up, every later observation is poisoned. The
clean version of that experiment (cold start DIRECTLY into a6e477d) has
not been run. 16f's "held under any firmware" claim is therefore
weakened from proven to probable, pending the meter.

### Morning meter protocol (2 minutes/board; black probe on a GND pad)

| # | Measure | Reading | Meaning |
|---|---|---|---|
| 0 | 3V3 castellated pad | ~3.3 V | sanity; on the MULE, if LOW → one answer explains BOTH its dead buses (QSPI + IMU) — board-level power fault |
| 1 | Across the small capacitor next to the LSM6DS3TR-C (the small ~3×2.5 mm chip, NOT under the big shield) = the 6D rail | ~3.3 V | power path fine → the die itself is stressed/damaged (back-feed history above). Module replacement path — but ONLY after the fixed firmware is what a new module ever meets. |
| | | ~0 V | **sensor probably healthy** — the rail path is the casualty (P1.08 driver or regulator). Recoverable: bodge 3V3→rail, or repoint firmware at a spare GPIO wired to the regulator EN. |
| 2 | (if #1 read 0 V) P1.08 / regulator EN side, board powered, app booted | ~3.3 V | pin drives, regulator dead → bodge or module swap |
| | | ~0 V | pin driver blown or firmware not driving — I'll add a `railtest` command to toggle it on demand before concluding |

If the chip locations aren't obvious in the morning: photo → I'll mark
the probe points on it.

## 16h. The software meter (railcheck) — mule verdict IN: the EN line itself is dead. 2026-08-13 morning.

Owner's directive: no multimeter session; a third, final board plugged
in; "take care of this problem once and for all." The meter question
("is the 6D rail coming up?") got answered in software instead.

**The instrument** — `railcheck` (mule-only branch `mule-railcheck`):
detach the bus (audited `bus_release()`), put weak internal PULL-DOWNS
on SDA/SCL, then step EN (P1.08) high/low/high. The module's own 4.7 k
I2C pull-ups hang off the SAME switched rail as the sensor, so against
a pull-down, a line reading HIGH can only mean "rail up". Pull-DOWNS
sink toward ground — they cannot back-feed an unpowered die (the 16g
hazard is pull-UPS into a dead rail), so the instrument is safe by
construction. v2 added EN READBACK: the pin's own input buffer is
connected while driving, so the row reports the actual level ON the
pin, not the level we asked for. v3 added a method control: the same
readback on P0.14 (battery-divider EN, proven working by every vbat
read).

**Mule results** (after the owner's full battery+USB unplug, so this is
a true cold state):
- selftest: still `i2c FAIL no_device` — full power removal did NOT
  recover it. The volatile-latch theory is dead.
- `revive` (clean 600 ms rail cycle): still no_device.
- railcheck v1: `en=1 sda=0 scl=0` ×3 — rail never rises.
- railcheck v2: `en=1 pin=0` ×3 — **the MCU drives P1.08 HIGH and the
  pin itself stays LOW.** The EN net is stuck at ground.

**Verdict:** the mule's fault is a hard board-level electrical fault on
the sensor-power path — P1.08 pin driver damaged, or the net it drives
shorted low (a die latched/fused into a short across its own supply
would do exactly this, and jh_imu.cpp's own RCA notes say P1.08 sources
the rail from a standard-drive GPIO). No firmware, reflash, power
cycle, or ritual can bring this sensor back; recovery is physical
(bodge wire from 3V3 with a series resistor, or module replacement).
Every "held bus" observation on the mule is explained: an UNPOWERED die
clamps SDA/SCL through its ESD structures. The die itself may even be
healthy — it has simply never been powered since the fault.

Consistency check: this is the meter table's "rail ~0 V" outcome, the
one the decision tree called the good-news branch for the die and the
bad-news branch for the board.

**Second opinion scored (owner forwarded a Gemini answer):** its fix #1
(drive PIN_LSM6DS3TR_C_POWER HIGH + settle before bus traffic) has been
in our firmware from the start (jh_imu::init, 40 ms) — and the mule's
pin=0 readback refutes its claim that this failure class is always a
temporary software-state lockup: EN cannot rise regardless of software.
Its #2 (library/BSP mismatch) is n/a — our register-level driver read
0.970 g on healthy silicon. Its #3 (polling faster than ODR "crashes
the bus") is not a real electrical mechanism; bounded transactions
(16d) make any bus stall a 2 ms timeout regardless. Useful frame
retained: most community "dead IMU" reports ARE the software power-pin
miss, which is why the optimistic consensus exists. Ours measured past
it.

**Pre-flash audit before board #3 (10-agent adversarial workflow):** no
damage-capable defect found in HEAD; bootloader confirmed hands-off
P1.08 (Adafruit_nRF52_Bootloader board config); no permanent-damage
reports found in the wild for this failure (datasheet only says
exceeding I2C-pin voltage vs VDD "may cause permanent damage" — a
stress rating, no recovery path documented). Three real blockers found
and fixed before anything was flashed:
1. Working-tree contamination — railcheck (experimental electrical
   code) would have shipped to the one-shot board. Board #3's build is
   clean main (`dca2985`); railcheck stays on the mule branch.
2. railcheck left the bus detached with `sensor_ok` stale-true —
   silent sample loss until the next selftest. Fixed: railcheck now
   ends with a full selftest, like revive.
3. **Cold-boot selftest read the accel before its first conversion**
   (output regs still 0x0000 under BDU) — ONE zero triple in the
   N=100 stats = sd 0.0995 = guaranteed `noise FAIL` on a healthy
   cold-booted sensor. The archive's own odd boot rows (0.960 g /
   0.0966 vs on-command 0.970 g / 0.0010) are exactly this artifact —
   the model reproduces them to three decimals. Board #3 would have
   looked dead on arrival. Fixed on main (`dca2985`): bounded
   discard-until-nonzero before the stats loop.

Advisories logged for follow-up (not damage-capable, audit 2026-08-13):
INT1 input buffer left connected through sleep; no WDT feed inside
runSelfTest/revive; ProbeGuard writes flash twice per healthy probe
despite its "writes nothing" comment; TwimBounded begin() writes PSEL
without forcing ENABLE off first; Lsm6ds3Min::begin() issues no
SW_RESET (warm reboots inherit stale register state); pinMode(OUTPUT)
momentarily drives EN low before digitalWrite(HIGH) on a cold boot.

**Board #3 (USB SN 8C7E817A759445ED):** factory app ignores the
pyserial 1200-touch; `stty -f <port> 1200` is what actually reset it to
bootloader. It then vanished from USB: ioreg shows the device present
but `!registered, !matched` — the macOS accessory-approval gate
(playbook §4) blocking the bootloader's NEW identity. Waiting on a
human Allow click; the bootloader idles with the sensor rail untouched,
which the audit confirmed is safe indefinitely.

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

## 24. Battery telemetry ADC accuracy — built 2026-08-04, never checked against a meter

**File:** `firmware/src/platform/nrf52/jh_power.cpp` (the whole file — the
jh_power seam's real implementation).

Written without the board on the desk (the S0 pattern, again): pins read
out of the installed variant (D14/P0.14 divider enable, D32/P0.31 VBAT,
D23/P0.17 ~CHG), the 1 MΩ/510 kΩ divider ratio and the
AR_INTERNAL_2_4/12-bit recipe from Seeed's own published battery example.
The known soft spot, called out in the file's own header: the divider's
~340 kΩ Thevenin source impedance is high for the SAADC's default
acquisition time, which the Adafruit core doesn't expose per-read — the
mitigations (1 ms settle, one throwaway read, 4-read average) are
reasoned, not measured.

**Verify:** with the battery attached and USB unplugged, compare
`vbat_mv` from a `stats` (over BLE) against a multimeter across the cell.
Within ~2% → done, edit this item. Reading consistently LOW by more →
the SAADC acquisition-time theory is confirmed; the fix is core-level
TACQ configuration, not divider-constant tweaks. Also confirm `chg`
flips to 1 on USB attach and back to 0 on detach, and that `batt_pct`
roughly tracks the charge state over a full charge cycle.

**FIX (1) APPLIED AND VERIFIED ON SILICON 2026-08-11.** `vbat_mv()` now
drives the SAADC through raw registers at 15 µs instead of `analogRead()`.
Same cell, `chg=0`, before → after: **4035–4044 → 4079–4082 mV**,
`batt_pct` **86–88 → 91**. Residual against the 4160 mV meter reading is
**78 mV / 1.88%** — the sweep predicted 75 mV / 1.80%, so the split is
confirmed to better than 5 mV. Fix (2), the per-unit gain error, is
untouched and still belongs in the calibration record, not here.

**A HANG I INTRODUCED AND FIXED THE SAME HOUR — read this before writing
any more register-level code here.** The first cut of `vbat_mv_tacq()`
used bare `while (!NRF_SAADC->EVENTS_x) {}` spin-waits. Harmless while it
only ran on an explicit `vbatscan`; a whole-device hang the moment
`vbat_mv()` was routed through it, because that sits on the `stats`/`info`
path. The symptom is deceptive: **BLE still accepts connections** (the
SoftDevice runs beneath `loop()`), so the puck looks alive and answers
nothing. Every wait is now bounded at 5 ms and a timeout returns -1
("unsupported"), which every caller already handles — never a partial
average. Stale `EVENTS_*` are also cleared before the FIRST conversion,
not just between them, or the first wait falls straight through.

**RESOLVED 2026-08-11 — BOTH causes are real, in a ~40/60 split.** Two
meter points plus an acquisition-time sweep on silicon (`vbatscan`
command, `jh_power::vbat_mv_tacq()`):

| meter | ADC | low by |
|---|---|---|
| 3490 mV (charging, 08-10) | 3390 | 100 mV / 2.87% |
| 4160 mV (rested full, 08-11) | 4050 | 110 mV / 2.64% |

The two points could NOT separate the candidate causes — the competing
models predict 4041 vs 4060 and the meter's own resolution is ±5 mV plus
~0.5%, so the sweep was built instead. On a rested cell (`chg=0`):

| TACQ | mV | vs 3 µs |
|---|---|---|
| 3 µs | 4044 | — |
| 5 µs | 4056 | +12 |
| 10 µs | 4077 | +33 |
| 15 µs | 4082 | +38 |
| 20 µs | 4082 | +38 |
| 40 µs | 4085 | +41 |

**The acquisition-time theory is CONFIRMED — and insufficient.** The
reading genuinely climbs with TACQ and plateaus at ~15 µs, which is the
SAADC failing to charge through the divider's ~340 kΩ source exactly as
the header predicted. But it accounts for only **50 of the 125 mV** gap
(default `analogRead` reads 4035): at the 40 µs plateau the reading is
still **75 mV / 1.80% low**, and no amount of acquisition time closes it.

So there are two independent errors stacked:
1. **~50 mV — acquisition time.** A firmware fix, correct for EVERY unit:
   configure TACQ ≥15 µs. Free; there is no downside to the longer sample.
2. **~75 mV / 1.8% — divider or reference tolerance.** A gain error, and a
   **PER-UNIT** one. A 1.043 MΩ top leg instead of 1 MΩ would do it, well
   inside 5% part tolerance; so would the internal reference's own spread.
   Baking this into firmware would be **wrong for other units** — it
   belongs in the per-unit calibration record
   (docs/data-pipeline.md), alongside `airtime_offset_s`/`height_scale`.

**Consequence for the gauge, worth fixing:** `batt_pct` read **88%** on a
cell the charger had just declared full. Under-reporting is the safe
direction, but "88% when full" reads as broken. Fixing (1) and recording
(2) fixes the gauge — no separate curve work needed.

**Still open:** whether a second unit shows the same 1.8% residual. If it
does, the "per-unit" reading is wrong and it is systematic after all
(reference spread, or the divider's nominal values being off) — one more
board settles it.

**FIRST METER POINT, 2026-08-10 — reads LOW by 2.9%, one point only.**
Meter across the cell 3490 mV; `vbat_mv` 3387–3393 over the same minute
(charging, `chg=1`, USB in). Delta ≈ 100 mV low — past the ~2% line and
in the direction the header predicted, so the acquisition-time theory
stands.

Deliberately taken WITH the cable in, against this item's own
instruction, because the cell was at 1% and the unplugged/resting
version was neither safe nor meaningful there — and it costs nothing:
the meter and the divider tap the SAME node, so the ADC-vs-truth
comparison is valid at any voltage. The `batt_pct`-tracks-the-curve
clause genuinely does need the resting cell and is still open.

**Do not fix on this point alone — it cannot tell the two errors apart.**
A proportional error (sample cap not charging through the ~340 kΩ source
— the TACQ theory) and a fixed offset (reference or divider constant)
are indistinguishable at a single voltage. They diverge near 4.0 V:
proportional predicts ~116 mV low, fixed predicts ~100 mV. Second meter
point at full charge decides which, and only then which fix.

Tooling built the same day for this: `tools/blecmd.py` (Nordic UART from
the Mac via bleak — no phone, no Bluefy, the unplugged reads are
scriptable now) and `tools/chargelog.py` (serial, hours-long, CSV).

**Unplanned finding, same session — idle drain is real.** A cell charged
to 4053 mV on 08-04 was at 3372 mV/1% on 08-10, six days later. Not
self-discharge: the puck was left advertising. 250 mAh at ~2 mA idle ≈ 5
days, which lands on the observed date. Makes item 25's `off` ritual a
shipping requirement, not a convenience, and makes off-current (25c) the
highest-value remaining measurement.

## 25. Soft power-off (`off` → System OFF) — entry PROVEN on-cable; wake paths and off-current pending *(added 2026-08-04)*

**File:** `firmware/src/platform/nrf52/jh_power.cpp` `system_off()`,
`firmware/src/main.cpp`'s `off` command.

Built the same bench day as the battery telemetry (the S2 sleep design's
smallest useful slice — a battery-powered board with no off switch runs
until the cell is flat): `off` flushes the open trace block, farewells
(`OK off` BEFORE the silence, so clients never hang into a timeout), cuts
the LSM6DS3's power rail (P1.08 low — GPIO states are retained through
System OFF, so the rail stays cut), then `sd_power_system_off()`.

**PROVEN on silicon, same day:** `off` over USB serial → farewell → `OK
off` → the CDC port died mid-read → no usbmodem device at all 3 s later.
Notably this answers the "entry with VBUS present" question: System OFF
engages fine with the cable in (VBUS wake did not immediately re-fire).

**Reset-tap wake CONFIRMED (same day):** tap → normal boot (blue
advertising blink returns after the usual boot seconds — don't panic
early) → stats healthy, stored jumps intact. Every System OFF wake is
also a live re-test of item 8's DPD mount-retry: power never dropped, so
the QSPI chip is asleep at every wake.

**Verify (remaining):** (b) the beach path — `off` over BLE from the
phone, USB unplugged, then wake on a LATER USB attach (VBUS rising edge —
untestable while the cable is already in); (c) actual off-current with a
meter (spec says < 5 µA System OFF + the IMU rail cut; the charger's own
quiescent draw on the cell is whatever the BQ25101 datasheet says, not
firmware's doing); (d) that charging genuinely proceeds while off
(hardware says yes — BQ25101 needs no CPU — but watch the red LED once
for the record).

**(c) DO IT AS AN OVERNIGHT VOLTAGE DELTA, not a meter in series.**
Meter-in-series needs the cell's positive line broken between a JST plug
(battery) and a JST socket (board) — pigtails of BOTH genders, or solder.
It also invites the classic own-goal: starting on the µA range (the awake
board draws mA and pegs it or blows the fuse), or switching ranges
mid-test, which on most meters moves a lead, breaks the circuit and
reboots the board.

None of that is needed to answer the question that matters, which is
"microamps or milliamps?" — a 100× gap. On the 250 mAh cell in its upper
range the curve in `jh_power.cpp` gives ≈**3.2 mV of resting voltage per
mAh** (4060→3980 mV spans 90→80%, i.e. 80 mV per 25 mAh). Over 12 h off:

| off-current | expected drop | verdict |
|---|---|---|
| ~100 µA | ~4 mV (invisible) | months — ship it |
| ~500 µA | ~19 mV | weeks — worth chasing |
| ~2 mA | ~77 mV | `off` is NOT working; a real bug |

Procedure: read `vbat` → `python3 tools/blecmd.py off` → **unplug USB**
(else the cable holds it up and you measure nothing) → leave overnight →
USB attach (which is also the wake, so it doubles as the (b) test) → read
`vbat`.

**No meter required, and the ADC's known ~1.8% residual does not matter
here**: it is a GAIN error, so it cancels in a before/after difference —
a 77 mV drop reads as 77 mV ±1.4 mV. The very error item 24 chased is
irrelevant to a delta measurement.

Only if this comes back showing milliamps is the meter-in-series version
worth its fiddliness — at that point you need to know *what* is still
awake, not just that something is.

## 26. Gyro spin correction + self-calibrating lever arm — built 2026-08-10, ZERO silicon time *(added 2026-08-10)*

**Files:** `firmware/include/jump_detector.h` (`correct_for_spin`),
`firmware/include/gyro_bias.h`, `firmware/include/lever_arm.h`,
`firmware/src/platform/nrf52/lsm6ds3_min.h` (gyro now ON: `CTRL2_G=0x5C`,
LPF via `CTRL4_C=0x02`/`CTRL6_C=0x00`/`CTRL7_G=0x00`).

Implements DECISIONS.md #29. Everything here is validated **against the
simulator only** — the sim's own sensor model, its own assumed spin
profile. No gyro sample from real silicon has ever been through it. The
whole feature is inert until a lever arm is estimated (`spin_lever_m`
defaults to 0 = exact identity), so the risk of shipping it dark is low,
but nothing below is a silicon fact.

**The precision constraint, which is the surprise and the thing to
re-check first.** A mis-set lever arm leaves a free-fall residual of
`rot_g·√(1−k²)` where `k = r_assumed/r_true`. The square root amplifies
small errors brutally: `k=0.99` leaves **14%** of `rot_g`. At r=0.5 m and
600 dps, `rot_g` is 5.6 g, so that residual is 0.79 g — more than twice
the 0.35 g free-fall gate. Takeoff gets re-pinned mid-flight and height
reads ≈−95% low. Required precision, computed:

| mount | spin | rot_g | k must exceed | tolerance |
|---|---|---|---|---|
| 0.2 m | 300 dps | 0.56 g | 0.780 | 22% |
| 0.3 m | 600 dps | 3.35 g | 0.995 | 0.55% |
| 0.5 m | 600 dps | 5.59 g | 0.998 | 0.20% |
| 0.8 m | 600 dps | 8.95 g | 0.999 | 0.08% |

**0.08% at the far end.** No tape measure reaches that, which is precisely
why the lever arm is measured per-jump from flight data rather than
entered by hand.

**A rule this file's sibling comment got BACKWARDS, now fixed.** g4's
landing-erasure probe said an over-estimated r erases the touchdown, so
`jump_detector.h` originally advised "err SHORT." Measured: a deliberate
5% short-shave broke **5 of 8** lever×spin cases; removing it fixed all 8
at +0.0% error. The two errors push in *opposite* directions — under
breaks the free-fall gate, over erases the landing — so there is no safe
side and the target is UNBIASED. `kSafetyFactor` is 1.0 and lowering it is
not a free safety margin.

**Accelerometer SATURATION is a real failure mode here, not a footnote**
*(experiment g5, `sim/experiments/g5_lever_tolerance.py`)*. `rot_g` grows
as `ω²r`, and it passes the ±16 g range faster than intuition suggests:
r=0.8 m at 900 dps is **20.1 g**. Past the rail a sample's magnitude is a
floor, not a measurement, so the lever-arm estimate reads LOW — the
dangerous direction. Unguarded, that case returned k=0.849, the only one
of twelve outside the usable band; `lever_arm.h`'s `kClipGuardG` (15.5 g)
discards railed samples and it returns k=1.001. **On silicon, check
whether real tricks actually rail the accel** — if they routinely do,
the range choice itself (item 20's deliberate ±16 g) needs revisiting.

**The usable band is strongly ASYMMETRIC** — measured, not assumed. Across
all twelve lever×spin cases the band runs from ≈1.00 up to at least 1.20
(the sweep ceiling): over-estimating r by 20% was survivable everywhere
tested, while under-estimating by 1-2% was fatal at high `rot_g`. Note
this does NOT reproduce g4's landing-erasure at +20%, because g4 used a
LATE spin burst (peak at 98% of airtime, still spinning at touchdown) and
g5 uses a mid-flight burst. **Both are assumed profiles; the real
distribution is a silicon question.** Treat "over is safe" as true for
mid-flight spins and unproven for spins that persist through landing.

**STEP 1 DONE 2026-08-11 — the gyro is real, and so is the bias.** New
`gyro` command (20 samples @ 10 Hz, raw + bias-corrected + the running
baseline). At rest: `x=0.8 y=-2.9 z=1.0`, **|ω| = 3.1 dps** — a genuine
zero-rate offset, comfortably inside the ±10 dps spec that motivated
`gyro_bias.h`. The estimator converged on silicon to `(1.2, -2.5, 1.1)`
and pulled 3.1 dps down to **0.5 dps**. Noise ±0.1 dps.

Then rotated by hand: **peak 257.8 dps**, returning to rest after. That
second half is not optional — a healthy gyro at rest and one STUCK at a
constant offset produce identical readings, so the stationary test alone
proves nothing.

**Why this had to happen before any water session:** `lever_arm.h`
SELF-ARMS. After one spinning jump it sets `spin_lever_m` above zero and
the correction goes live, so "ships inert" was only ever true until the
first spun jump — after which everything rested on a sensor nobody had
looked at.

**Gyro SCALE error does not threaten the height path** (worked out while
checking this, worth not re-deriving): `r` is estimated from the same
mis-scaled ω that later applies the correction, so a constant scale factor
`s` cancels exactly — `r_est = r/s²`, then `rot_g = (sω)²·r_est/g = ω²r/g`.
That leaves only bias (validated here) and range clipping (guarded by
`kClipGuardG`). Scale still matters for rotation counting / trick metrics,
which are S5 and unbuilt.

**Verify on silicon, in this order:**
1. ~~**Gyro reads at all**~~ **DONE, above.** — `selftest`, or add a raw gyro row. Stationary
   should read ≈0 dps per axis after `gyro_bias` settles; rotating the
   board by hand should track sensibly. Byte-order errors here look like a
   plausible-but-wrong rate, not an obvious failure (same little-endian
   trap as the accel burst — see `lsm6ds3_min.h`).
2. **Real `rot_g` magnitudes.** The table above assumes the sim's spin
   profile. Spin the board on a string/turntable at a known radius and
   check the measured `|a|` against `ω²r/g`. This is also the first honest
   check of whether the ±16 g accel range clips: r=0.8 m at 600 dps is
   ~9 g, and real tricks reportedly reach 900+ dps.
3. **Lever-arm estimate on a real jump.** Does it converge, and to
   something physically sensible for where the puck actually sits?
4. **Power.** Gyro is now always-on at ~0.9 mA against a ~2 mA idle
   baseline (item 25's drain finding) — measure the real delta before
   deciding whether duty-cycling is worth the settle-time risk.

**Deliberately NOT done:** duty-cycling the gyro (the free-fall gate
confirms in 80 ms and the LSM6DS3's gyro settle is the same order —
measure before attempting), and any use of the gyro for rotation counting
or trick metrics (that is the S5 metrics ladder, still deferred).

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
