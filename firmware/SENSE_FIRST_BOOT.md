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

## 3. `CHUNK_GAP_US` (BLE send pacing) is an untuned guess

**File:** `firmware/src/platform/nrf52/jh_link.cpp`, `CHUNK_GAP_US = 15000`.

Chosen reasoning from item 2's `hvn_qsize=1` fact: pacing slower than a
typical negotiated BLE connection interval (commonly low tens of ms) gives
the previous chunk a realistic chance to have already been ACKed before the
next send attempt, keeping `getHvnPacket()` in its fast/uncontended path
rather than its 100 ms worst case. 15 ms was picked as a plausible
mid-range value, not measured against a real negotiated interval.

**Verify:** log (or infer from throughput) the actual connection interval
Bluefy/the web app/a Garmin watch negotiate; if it's smaller than 15 ms,
`CHUNK_GAP_US` can safely shrink (faster dumps); if bigger, item 2's gap
symptom is the signal to grow it.

## 4. LSM6DS3TR-C register map — confirmed against ST's own driver source, not the datasheet PDF directly

**File:** `firmware/src/platform/nrf52/lsm6ds3_min.h`.

The datasheet PDF itself did not fetch cleanly in the authoring
environment; every register address, bit-field layout, and the ±8 g
sensitivity constant (0.244 mg/LSB) were instead confirmed against ST's own
published register-definition source
(`STMicroelectronics/lsm6ds3tr-c-pid` on GitHub — `lsm6ds3tr-c_reg.h`/`.c`),
which is as authoritative as the datasheet for these specific facts (it's
ST's own driver, not a third party's guess) — but a from-datasheet
cross-check has not happened.

**Verify:** `selftest`'s `whoami` row (see item 5) and `accel` row — a real
LSM6DS3TR-C sitting still should read close to 1.000 g with low noise,
confirming the ±8 g scale factor and axis byte order (little-endian, the
opposite of the MPU-6050's big-endian burst — see the file's own comment on
this exact risk) are both right. A wrong scale factor would show up as
"self-test WARN/FAIL, mean far from 1.0 g" immediately.

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

## 7. IMU power-rail boot-settle delay (20 ms) is an unconfirmed guess

**File:** `firmware/src/platform/nrf52/jh_imu.cpp`, `init()`'s `delay(20)`
after driving `PIN_LSM6DS3TR_C_POWER` high.

No datasheet "time to first valid I2C transaction after power-on" figure
was available to confirm against. 20 ms is a conservative, commonly-seen
value in community LSM6DS3 drivers, not a cited spec number.

**Verify:** if `selftest`'s `i2c`/`config` rows ever fail intermittently
right after a fresh power-on (but pass on `selftest` re-run moments later),
this delay is the first thing to lengthen.

## 8. QSPI deep power-down (0xB9/0xAB) — wired in, current draw and wake delay unmeasured

**File:** `firmware/src/platform/nrf52/jh_store.cpp`, `flashWake()`/`flashSleep()`.

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

**Verify:** a USB power meter (S0 milestone) with the device idle
(recording paused, no BLE commands): confirm current drops noticeably
between write bursts vs. a build with `flashWake()`/`flashSleep()`
temporarily stubbed out.

## 9. `micros64()`'s wrap-tracking assumes it's called at least every ~71 minutes

**File:** `firmware/src/platform/nrf52/jh_clock.cpp`.

The Adafruit core's `micros()` is, like the ESP32's, only 32 bits and wraps
at ~71.6 minutes. This file turns it into a 64-bit, effectively
non-wrapping counter by comparing each reading to the last and bumping a
wrap counter whenever it goes DOWN — which only correctly detects a wrap if
called at least once per wrap period, and `main.cpp`'s loop calls it every
sample-loop pass (>= `JH_SAMPLE_HZ` = 200 times/second, i.e. every few ms),
comfortably inside that bound. This reasoning is sound by construction, not
a guess — but it has never been run continuously across a real 71-minute
boundary on this chip to see it happen live (the ESP32 sibling has years of
field time doing exactly this; this port has none yet).

**Verify:** a long (>90 minute) continuous bench soak with the device
actively sampling throughout; confirm no `t` discontinuity appears in the
resulting trace around the 71-minute mark (the ESP32's own history is
already the evidence this WOULD show up obviously if wrong — a jump
mid-flight at the wrap instant, per the seam's own doc comment).

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

**Verify:** flash a build with an intentional infinite loop somewhere in
`loop()` (temporarily) and confirm the board resets within a few seconds;
then confirm a normal, long-running session never resets on its own.

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
