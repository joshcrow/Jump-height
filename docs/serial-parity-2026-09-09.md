# Serial parity: web/sync vs firmware vs tools/jump — 2026-09-09

**Status: UNMEASURED.** No hardware on the bench (`ls /dev/cu.usbmodem*` → nothing).
Every row below is read from source, not observed on a wire. This file is the
checklist to run once the spare board (`JumpHeight-45ED`, USB-only — no battery,
so its `vbat_mv`/`batt_pct` are floating noise) is plugged in.

Three implementations of one line protocol:

| | file |
|---|---|
| page | `web/sync/sync.js` (Web Serial + Web Bluetooth) |
| firmware | `firmware/src/main.cpp` (emit layer + command dispatch) |
| CLI (proven) | `tools/jump` (`SerialPort` / `Device` / `cmd_sync`) |

Nick's puck runs `src=5c80a436`, i.e. the tree **before** this branch.
`git show main:firmware/src/main.cpp | grep traceraw` → no match: **his firmware
has no `traceraw` command at all**, so every row marked "CSV path" is the row
that actually fires.

---

## 1. Protocol assumptions

| # | Assumption | Page | Firmware | tools/jump | Verdict |
|---|---|---|---|---|---|
| 1 | 115200 baud, native CDC ignores it; must not be 1200 (bootloader touch) | `sync.js:282` `await this.port.open({ baudRate: 115200 })` | `main.cpp:1422` `Serial.begin(115200);` | `tools/jump:69` `BAUD = 115200`; `:145`, `:166` | **fine** |
| 2 | Opening the port does not reset the board (native USB CDC) | `sync.js:261-265` comment | native TinyUSB CDC | `tools/jump:613-615` "the Sense is native USB CDC and keeps running across an open" | **fine** |
| 3 | Drain whatever was buffered before the first command | `sync.js:294` `await new Promise((r) => setTimeout(r, 400));` — a fixed 400 ms guess | `main.cpp:257-261` `bleGreet()` writes `READY` **to BLE only** — over USB there is no READY to wait for | `tools/jump:492-518` `drain_boot()` reads until `READY` or a quiet gap, up to 5 s | **fine, by luck** — `main.cpp:225-231` drops serial output when the host is not draining, so an unattended puck leaves little backlog. Re-check on silicon. |
| 4 | Every command ends `OK <first word>` | `sync.js:626` `if (line === 'OK ' + activeCapture.firstWord)` | `main.cpp:859,863,867,887,922,925,1234` | `tools/jump:548` `if line.strip() == f"OK {cmd.split()[0]}"` | **fine** |
| 5 | Any line starting `ERR` terminates the in-flight command | `sync.js:629` `else if (line.startsWith('ERR'))` | `main.cpp:1748` emits `ERR trace_clear — erase failed…` **from `loop()`**, not from a command | `tools/jump:550` `if line.startswith("ERR")` — identical | **will be silent** — both clients would mis-terminate on an async ERR. Unreachable in practice: `printFileFramed`/`handleCommand` run on the loop task, so nothing interleaves mid-command. |
| 6 | Old firmware answers `traceraw` with `ERR unknown_command traceraw` | `sync.js:1029` `/^ERR unknown_command\b/.test(r.err)` | `main.cpp:1402` `emitf("ERR unknown_command %s\n", cmd.c_str());` (identical at `main:1218` on Nick's build) | `tools/jump:1816` `if last == "ERR unknown_command traceraw"` (exact whole-line match) | **fine** — page's prefix test is the looser of the two; both match the real line |
| 7 | Help is printed **before** the ERR terminator | `sync.js:457-459` — the 5 `#` lines land in `deviceLog` + `classify()` | `main.cpp:1398-1402` "Help BEFORE the ERR terminator: clients stop reading at OK/ERR" | `tools/jump:1815` uses `lines[-1]`, so help never masks the ERR | **fine** |
| 8 | `FILE <name> BEGIN` … `FILE <name> END` framing | `sync.js:407-408` | `main.cpp:410,438` (`printFileFramed`), `:489,557` (`printTraceRawFramed`) | `tools/jump:1489-1492` `parse_file_sections` | **fine** |
| 9 | `#` chatter appears **inside** the frame and is not body | `sync.js:424-441` — routed to `deviceLog`, excluded from the body sink and from `textBytes` | `main.cpp:433-436` emits `# WARNING %s INCOMPLETE — %lu bytes never reached the host` before `FILE … END` | `tools/jump:1485-1496` keeps it **in** the body; `:1830` strips `#` for `trace.bin` **only** | **divergence** — `tools/jump:2002` `len(trace_csv) + 1` therefore counts the warning's own bytes on the CSV path. Only matters once INCOMPLETE has fired (check (a) already failed). |
| 10 | The puck's INCOMPLETE complaint outranks arithmetic | `sync.js:1163-1164` `l.includes('INCOMPLETE') && l.trimStart().startsWith('#')` | `main.cpp:434`, `:575`, `:583` | `tools/jump:399-400` `"INCOMPLETE" in l and l.lstrip().startswith("#")` | **fine** — identical predicate in all three |
| 11 | Inactivity timeout, reset on every line | `sync.js:57` `INACTIVITY_MS = 30000`; reset on body lines at `:462-466` | `main.cpp:206-213` `waitForSerialRoom` gives the host **2000 ms**, then drops | `tools/jump:534` default 20 s; `dump`/`traceraw` 120 s (`:1964`, `:1814`); `stats` 10 s (`:1991`) | **fine for a flowing transfer** — see row 12 for `clear` |
| 12 | `clear` finishes inside the timeout | `sync.js:1518` `await runCommand('clear')` — **no `timeoutMs`, so 30 s** | `main.cpp:909-922` `jh_store::clear()` emits **nothing** until it returns | `tools/jump:2198` `dev.command("clear", timeout=60)` — raised after "The old 10 s timeout reported failure on a clear that was merely SLOW" (`:2194-2197`, ~40 ms/sector, ~20 s for a well-used region) | **will fail** (marginal) — a full ~2 MB region is ~20 s with no margin; on a slow erase the page says "The puck didn't confirm it was emptied" for a clear that worked |
| 13 | STATS `trace_bytes` is the right denominator, sampled at the right moment | `sync.js:902` `stats` runs at **connect**; `:530` `if (S.statsBefore === null && inStatsCapture)` — captured once, never refreshed | `main.cpp:797` `stats` calls `flushTrace()`; `main.cpp:865` `trace` calls `flushTrace()` too | `tools/jump:1991` runs `stats` **after** the dump | **will fail** — the page's denominator is stale by however long Nick waits between Connect and Copy. Any sample appended in that window makes `got > devBytes` and `sync.js:1201-1202` prints a surplus as `"Only 15,918,120 of the puck's 15,917,918 bytes … came across."` |
| 14 | A byte-count match means the CSV arrived whole | `sync.js:1199` `else if (got !== devBytes)` — hard compare, no band | F-22: `trace_bytes()` over-reports once the region is FULL. Live 2026-09-07: `tracecheck fast=15917918 slow=15917153` (−765). `main.cpp:903` `emitLine(fast == slow ? "OK tracecheck" : "ERR tracecheck mismatch")` | `tools/jump:416-429` — the same hard compare. The F-22 band `F22_MAX_OVERREPORT_BYTES = 800` (`:344`, applied `:365`) is only reachable from `_trace_bytes_note`, called at `:1792` (traceraw) and `:2341` (ingest) | **will fail** on a full puck, in the page **and** in the CLI **and** in ingest (`:2229-2230` "trace.csv via a hard byte-count match") |
| 15 | `tracecheck` is the arbiter for row 14 | absent — `grep -rn "tracecheck" web/` → no match | `main.cpp:889-903` implemented | absent — `grep -n tracecheck tools/jump` → no match | **will be silent** — the one command that resolves F-22 is asked by nobody |
| 16 | The trace is checksummed | `sync.js:189-205` CRC-32/ISO-HDLC table; checked at `:1189-1193` — **`jhtrace-v2-b64` only** | `main.cpp:448-456` `crc32Update`, bitwise, same polynomial | `zlib.crc32` on the traceraw path | **fine on raw / will be silent on CSV** — the CSV path has no CRC at all, yet `sync.js:1108` still says "checked and complete" |
| 17 | jumps.csv's header row is not a jump | `sync.js:1096` `rows[0].startsWith('n,') ? rows.length - 1 : rows.length` | `platform/nrf52/jh_store.cpp:120` `JUMPS_HEADER = "n,takeoff_s,airtime_raw_s,…"` | `_parse_jumps_rows` | **fine** |
| 18 | `body = lines.join('\n') + '\n'` reproduces the stored byte count | `sync.js:1212` `joinBody` + `:1196` `byteLen(...)` (UTF-8) | CSV rows are `"%.3f,%.3f\n"` (`platform/nrf52/jh_store.cpp:559`) — pure ASCII | `tools/jump:2002` `len(trace_csv) + 1` (chars) | **fine** — identical for ASCII; the one non-ASCII line (the em dash in INCOMPLETE) is excluded by the page and included by the CLI, see row 9 |
| 19 | The reader keeps up; backpressure is real | `sync.js:297-308` one `reader.read()` loop, decode + parse **synchronously on the main thread**; two `TextEncoder.encode()` per line (`:411` and `:451`); `:455` pushes every row into `S.traceCsvLines` (~1.06 M strings for a full region at `JH_LOG_HZ 50`) | `main.cpp:225-231` — under `s_serial_must_not_drop` it waits ≤2 s for CDC room, then **counts the bytes as dropped** and the frame ends INCOMPLETE | `tools/jump:145` pyserial `timeout=0.25` with its own byte buffer; `:179-190` `read_line` | **will be slow** — and any main-thread stall over 2 s converts into dropped bytes. At least it converts *loudly*. |
| 20 | The port is released when the link ends | `sync.js:252` and `:314` define `disconnect()`; **nothing in the file calls either**. `onLinkLost` (`:931-944`) sets `transport = null` and leaves the reader lock held and the port open | n/a | `tools/jump:629` `_close_device` in a `finally:` | **will fail** — after a read error without a physical replug, `requestPort()` returns the same port and `port.open()` rejects `InvalidStateError`; the page's advice (`:830-832`) never says "reload the page" |
| 21 | 20-byte BLE writes (guaranteed MTU) | `sync.js:242` `for (let i = 0; i < bytes.length; i += 20)` | `jh_link` chunks to the negotiated MTU | n/a (serial only) | **fine** |
| 22 | The bundle carries the wall-clock anchor | `sync.js:1249-1255` `synced_at_utc`, `uptime_s`, `trace_epoch_utc = at - uptime_s`; anchored at `:530-537`, guarded to a real `stats` capture | `main.cpp:840-842` ` uptime_s=%.3f` on STATS | `tools/jump:1503-1504` `SESSION_NOTE` | **fine** — but null-and-silent if `stats` errored at connect (`sync.js:906-909` leaves `btn-pull` enabled) |
| 23 | PUCK4 in the bundle filename | `sync.js:1218-1221` — over USB the only source is `# name=` | `main.cpp:1207-1208` `if (jh_link::local_name()[0]) emitf("# name=%s\n", …)` from `info` | `tools/jump:2208-2216` `_puck4` → `"UNKN"` | **fine** — falls back to `xxxx`, which is visible, not silent |

---

## 2. Bench test order, once a board is on the port

1. `ls /dev/cu.usbmodem*` — record the name. Nothing below runs without it.
2. `./tools/jump boards` — confirm which board answered.
3. `./tools/jump status` and `stats` — record `stored_jumps`, `trace_bytes`, `uptime_s`, whether `fs=down` appears.
4. `tracecheck` — record `fast=` and `slow=`. **This is the number rows 14/15 exist for.**
5. Serve the page (`python3 -m http.server` from `web/`), open Chrome, connect with the cable.
   The page no longer shows a byte count before the pull (the "Ride data
   waiting" row was cut 2026-09-09). Compare `trace_bytes_device` in the
   bundle's `manifest.json` against step 3's `trace_bytes` instead.
6. Wait 5 minutes between Connect and Copy, then Copy. Row 13 predicts a
   `got > devBytes` message worded as a shortfall. Record the exact sentence.
7. Fill the region (`fillstore`, `main.cpp:1015`), then repeat 4-6. Row 14
   predicts `verified=false` and no step 4.
8. Unplug the cable mid-copy. Row 20 predicts that re-connecting without a
   replug fails; record whether Chrome says `InvalidStateError`.
9. Time a `clear` on a full region with a stopwatch. Row 12 needs the real number.

Nothing above is a verdict until it is a measurement.

---

## 3. Measured on the `#mock` seam, 2026-09-09

Not silicon. The mock transport (`sync.js:326-333`) exercises the page's own
line handling, verification and gating with a scripted puck; it says nothing
about the wire. Scripts:
`scratchpad/probe.py`, `scratchpad/probe2.py`.

**(A) F-22 shortfall on the CSV path** — 26,000 bytes delivered against a STATS
`trace_bytes=26,765` (the measured −765):

```
state:  verified=false  jump_rows=4  trace_format=csv
status: "That didn't come across cleanly, so it is not ready to send.
         The puck still has everything. Check the cable is pushed in properly at
         both ends (some cables only charge — use one that carries data), then
         tap "Copy the ride" again."
result: "Not complete — the puck still has everything.
         Only 26,000 of the puck's 26,765 bytes of ride data came across."
btn-clear hidden = True        (correct — step 4 stays shut)
btn-send disabled = False      (the bundle CAN still be sent)
```

The gate is right and the advice is wrong: re-seating the cable cannot change
a number the firmware over-reports, and the status line says "not ready to
send" while Send is enabled. Row 14.

**(B) Trace region auto-cleared by the firmware before the sync**
(`main.cpp:1733-1737`; jumps kept, `trace_bytes=0`):

```
connect: "Connected. The puck is holding 3 jumps and 0 bytes of ride data."
state:   verified=TRUE  jump_rows=3  reasons=[]
status:  "Got it all — 3 jumps and the whole ride, checked and complete.
          Now send it in step 3."
result:  "Everything on the puck is now on your phone. / 3 jumps / 0 bytes of ride data"
after Send: btn-clear hidden = False
send status: "…Last step: empty the puck so it has room for your next ride."
```

Zero bytes of ride data, verified true, "the whole ride", and step 4 offered.
Rows 14/15 — nothing in STATS says the region was full or was auto-cleared.

**(C) Retry after a pull that stalled inside a FILE frame.** First `trace`
opens `FILE trace.csv BEGIN`, sends three rows and goes silent; the page times
out at 30 s and tells him to tap "Copy the ride" again. `doPull`
(`sync.js:997-1022`) does **not** reset `S.fileSection` — only `freshSession`
(`:369`) and a `FILE … END` (`:422`) do — so the retry's `FILE jumps.csv BEGIN`
is swallowed as trace-body:

```
pull 1: phase=failed  advice="…tap "Copy the ride" again…"
pull 2: verified=false  jump_rows=0
        reasons=["Only 0 of the puck's 3 jumps came across.",
                 "Only 290 of the puck's 65 bytes of ride data came across."]
```

Every retry after a mid-frame stall fails this way until the page is reloaded
or the link is re-established. Note the second reason: 290 > 65, a surplus
printed as a shortfall (`sync.js:1201-1202`, and the same wording at `:1186`).

---

## Measured 2026-09-09 on real silicon — the Puck (`JumpHeight-8673`), `src=15b2d468`

First time any of this project's serial assumptions met a real port. The board
is USB-only (`./tools/jump boards` flagged its floating divider: 424 mV across
four reads) and runs a build **older than the OG's** — no `traceraw`, no
`tracecheck` — which is exactly why it is the right board for this: it takes
the **same CSV fallback path Nick's OG (`5c80a436`) will take.**

Probe: `scratchpad/portprobe.py`, pyserial 3.5, 115200, driving the page's own
command sequence.

| Assumption | Measured | Verdict |
|---|---|---|
| "Board resets when the port opens" (`tools/jump` `drain_boot`) | **Nothing arrived in 6 s.** No reset, no banner, no POST. | **FALSE on this board.** That comment is ESP32-era; the nRF52's native USB CDC does not reset on open. The page's fixed 400 ms drain is safe — there is nothing to drain. |
| The page waits 400 ms then talks (`sync.js`) | `info` answered in **55 ms**, `stats` 55 ms, `jumps` 53 ms | Fine, with ~7× margin. |
| `traceraw` falls back cleanly on old firmware | `ERR unknown_command traceraw`, and the page's `/^ERR unknown_command\b/` matches it | **CONFIRMED on silicon.** The CSV fallback fires — Nick's path works. |
| CSV throughput over USB CDC — *never measured* | **64.1 KB/s** (65,593 B/s): 204,048 wire bytes in 3.11 s | A full region's CSV (15.9 MB, the OG's 09-07 read) is **≈ 4 minutes** on the cable. Extrapolated from a 204 KB body — not measured at full size. Bluetooth's 20–30 min estimate stands unmeasured. |
| Byte accounting: page vs device | device `trace_bytes=203,999`; `byteLen(joinBody(lines))` = 203,999 **exactly** | Clean. `joinBody` (`sync.js:1398`) appends the trailing newline, which is what makes it come out even. |
| The counter drifts while connected | **0 B/s at rest** over 46 s. Earlier, while the board was being handled, it grew 203,999 → 466,154. | The growth window (`trace_bytes_after`) guards a real case, but only while the puck is moving; `idle_timeout_s=20` closes the gate. On a Mac, plugged in and still, drift is zero. |
| Chrome's port-picker entry | `ioreg`: USB Product Name **"XIAO nRF52840 Sense"**, Vendor "Seeed" | The docs told the rider to look for "JumpHeight" — the **BLE** name, which cannot appear in a USB picker. Corrected in the page and `rider-sync.md`. |
| `tracecheck` walk time vs the 300 s floor | `ERR unknown_command tracecheck` — **absent on this build** | **NOT MEASURED.** This board cannot answer it. The OG has the command (used 2026-09-07: `fast=15917918 slow=15917153`), so the floor stays unvalidated until a full region is walked on a board that has it. |

**Still not measured, and only a browser can:** `navigator.serial.requestPort()`
itself — the picker, the permission grant, and whether Chrome's stream keeps up
with a multi-megabyte body. Everything above was driven from Python over the
same port Chrome would use, so the protocol is proven and the transport is not.

## The browser, measured 2026-09-09 — Web Serial met a real port

The owner picked the port by hand (Chrome's picker is a native sheet; no
automation reaches it). Everything after that was driven and read
programmatically, on `2026-09-09c`, against `JumpHeight-8673`.

| | Result |
|---|---|
| `navigator.serial.requestPort()` → open → `info`/`stats` | **Connected.** Facts row read `JumpHeight-8673`, 455 KB of ride data, 0 jumps. |
| The old-firmware fallback, in the browser | **Fired.** "This puck has the older software, so the ride comes across the slow way." — `ERR unknown_command traceraw` → `trace`, exactly Nick's path. |
| Throughput, browser vs Python | **64.9 KB/s** in Chrome vs **64.1 KB/s** from pyserial — independent agreement on the same board. |
| The whole pull | **455 KB in 9.1 s, verified**, `verifyPull()` returned no reasons. Zero console messages, zero page errors. |
| The cancelled picker | Confirmed earlier the same evening: dismissing the sheet gives "No puck picked", button re-enabled. |

**What it caught that no fixture had.** The puck held 455 KB of ride and zero
detected jumps, and the page said **"Nothing was recorded on the puck."** That
is false, and it is the exact shape of the 2026-09-06 water session — 47
minutes on the water, a full trace, not one real jump in it — the most
valuable capture this project has. A rider told nothing was recorded has every
reason not to send it. Now three outcomes instead of two: nothing at all (a
delivered body ≤ the 6-byte header), a ride with no jumps in it, and a ride
with jumps. Pinned by
`test_a_ride_with_no_jumps_is_not_called_nothing`.

**Still unmeasured:** a multi-megabyte body through the browser (this was
455 KB, ~9 s; a full region is ~35× that), `tracecheck`'s walk on a full
region, and the Send/share-sheet step on Nick's own Mac.

## The whole chain, 2026-09-09 — puck to session folder

After the pull, the rest was driven without the owner. Send's branch was
measured rather than guessed: on this Mac's Chrome 152,
`navigator.canShare({files:[<zip File>]})` is **true**, so `doSend()` takes the
**share sheet**, and Downloads is the fallback — `docs/rider-sync.md` step 6 is
in the right order. A native share sheet is a modal that would block the
extension, so the download branch was forced (`canShare` stubbed false, then
restored) purely to get the artifact out.

    bundle   jumpheight-8673-20260909-2246.zip, 91 KB
             manifest.json / jumps.csv / trace.csv (466,154 B) / notes.txt / device.log
    ingest   ./tools/jump ingest <zip>
             ✅ trace.csv verified: 466,154 bytes = manifest trace_bytes_device
             -> data/sessions/20260909-224641-8673  (verified, forced=false)

`trace_bytes_device` == `trace_bytes_got` == `trace_bytes_after` == 466,154,
`f22_band_applied: false`, `transfer.transport: "usb"`, 9.1 s, 467,275 bytes on
the wire. **`trace_epoch_utc` is in the bundle**, so `tools/label.py` can
convert Nick's wall-clock notes on a bundle he sends from his own Mac — the
thing that makes a remote session scoreable at all.

**Unrelated finding, recorded because it is a measurement:** this Puck's
`accel_fail` ran 97,935 -> 109,415 in 65 s — ~178 dropped accelerometer reads
per second against a 200 Hz loop, ~88%, while `selftest accel` still PASSes on
a single read (and reports gravity at 1.055 g). The STATS key only appears when
the counter is non-zero, and **the OG's 2026-09-07 STATS carried no such key**,
so the OG's counter is zero and this is this board's problem, not the fleet's.
It does not touch anything above — bytes moved over the wire regardless of what
the sensor behind them was doing. Per CLAUDE.md rule 1 this is not a verdict on
the board: nothing has established its configuration, and the known cause of
exactly this shape on this project is GPIO drive strength, not a dead part.

## Multi-megabyte, measured 2026-09-10 — a FULL region over the cable

The 455 KB pull above was ~1/35th of a region, so "does it hold at scale" was
open. It is not any more. `fillstore` filled the Puck's region — 51 passes,
611 s — and the whole thing came back over USB CDC.

**`fillstore` is dispatched on `15b2d468` but absent from its `help` string.**
Confirmed on silicon, and it is the F-25 residue already noted for the current
tree: `tools/jump:2626` reconciles help against `cmd == "..."` only, so the
`startsWith`-dispatched commands (`fillstore`, `set`) are counted nowhere. The
help string is not the command list.

    region full   trace_bytes = 14,093,819   region_full=1
    pull          14,093,853 B on the wire, 939,256 body lines, 220.3 s
    throughput    62.5 KB/s sustained

Throughput is **flat across the whole transfer** — 62.9 KB/s at the 1 MB mark,
62.5 KB/s at 13 MB, every intermediate mark within 0.4 KB/s. No stall, no
backpressure cliff, no degradation. Against 64.1 KB/s on the 204 KB body that
is a 2.5 % difference over a 69× larger transfer.

**So a full puck on the cable is 3.7 minutes, measured** — not the ~4 min
extrapolated from the small body. The extrapolation was sound.

**`trace_bytes` at exhaustion is not a constant, now measured twice.** This
board reported **14,093,819** at `region_full=1`; the OG reported
**15,917,153** on 2026-09-07. Same 2,027,520 B physical region, different
content, ~13 % apart in CSV-equivalent terms. Any check that compares
`trace_bytes` against a fixed MB figure is wrong by construction.

**Audit F-22, measured on a second board.** A full region over-reports:
device `14,093,819`, delivered body `14,093,804`, **delta −15 B**. Inside the
1..800 B band, and a second independent shape for it — the OG's was **−765**.
Two boards, two deltas, both negative, both inside. The band is no longer
resting on one observation.

**Still unmeasured after this:** the same body through the BROWSER (Chrome's
stream and the page's main-thread parse at 14 MB — separate work), and
`tracecheck` on a full region, which this build does not have the command for.

## The browser at full-region scale, 2026-09-10 — the other half of the answer

The cable half above is the puck's print rate. This is the page's own cost, and
it needs no board: the body is generated **inside the browser** and handed to
`window.__mock.feed(line)` one line at a time, the same entry point a real line
takes, in 512-row chunks with a yield between them (a real `SerialTransport`
yields between CDC reads; one synchronous loop would manufacture a freeze the
link never produces). Three clocks kept apart, all inside the page, with the
generation cost measured separately and **subtracted**.

Bench: Apple M3, headless Chromium 151. **Not Nick's Intel MacBook.**
Fixture: 15,917,153 B / 1,029,542 lines — the OG's own `tracecheck slow=`
figure, in the firmware's real `"%.3f,%.3f\n"` row format.

| | Measured |
|---|---|
| The page's own line handling | **3.4–4.3 s** for 1,029,542 lines = **3.5 µs/line**, linear (3.51 µs/line at 3 MB) |
| Page-limited ingest rate | **4.39 MB/s** — against the cable's measured 62.5 KB/s, **~70× of margin**; the page's share of a 249 s transfer is ~1.5 % |
| Longest synchronous block during the body | **3.8–5.1 ms** — against the 2 s the firmware waits for CDC room before counting bytes dropped, ~400× under |
| The 30 s inactivity timer | Worst line-to-line gap **49 ms** vs `INACTIVITY_MS` 30000 (read out of `sync.js` by the test, not restated) = **~610× headroom** |
| Progress UI | **28 distinct percentages** on screen, sampled from outside the page; **59.8 fps** during the body against 60.0 fps idle on the same page |
| `verifyPull()` at that size | No reasons, `verified` true, `trace_bytes_got` exact — the byte-**exact** arm, not F-22's band |
| Peak heap | **2.76 → 106 MB** (CDP `Runtime.getHeapUsage`, 250 ms sampling, so a high-water mark, not a ceiling) |
| Console / page errors | None |

`performance.memory.usedJSHeapSize` **is not a measurement here** — it returned
`10,000,000` flat on every sample while CDP moved 2.76 → 106 MB. Both facts are
now asserted so neither can quietly stop being true.

**The one real freeze, found and fixed.** After the bar reads 100 % the page
was joining and TextEncoder-ing the whole 16 MB body **four separate times** —
`traceBytesGot()`, `okDetail()`, `verifyPull()`'s `got`, and `buildBundle()`,
uncached. Measured 116–144 ms on an M3, linear in size, and Nick's machine is
slower. Now computed once (`csvBody()`, keyed on line count and cleared in
`doPull`'s reset so a retry can never inherit the last pull's body):
**144 ms → 51.5 ms**, with `verified`, `reasons` and the bundle byte-identical.

**Also fixed, and it is rule 3's exact shape:** `unittest.main()` sat at
`test_web_sync.py:1899` with `class TestWebSyncCable` defined at `:1903`, so
running the file directly executed `unittest.main()` before that class existed
— **24 tests ran where pytest collects 32**, and the file reported OK. Eight
cable tests, including every real-port one, silently never ran. The guard now
sits last: `Ran 32 tests ... OK`.

**Still unmeasured:** Nick's actual Intel MacBook (every number here is M3; the
51 ms tail and 106 MB heap scaled to it are extrapolations), the F-22 *band*
arm at 16 MB (the exact-match arm is what passed here; the band is pinned at
fixture scale), the `traceraw`/base64 path at scale, and whether the compositor
actually painted each new bar width — rAF proves the frames were served.

