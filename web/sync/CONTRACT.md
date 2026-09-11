# CONTRACT.md — the wire between the puck, the rider's page, and the CLI

## Provenance — read this first

This file is **reconstructed**, on 2026-09-09, from the code that cites it.

`CONTRACT.md §0`…`§4` is cited 28+ times across `firmware/`, `tools/` and
`web/sync/` — in comments, docstrings, test names, and in `./tools/jump ingest
--help` text a user can read — and the file existed in **no commit on any
ref**. Every citation was read; every rule below is written from what the
implementing code *does*, and names the symbol and line that implements it.

Three consequences of being a reconstruction, stated plainly:

1. **Where two citations disagree about what a section says, both are
   recorded** (Appendix A). Nothing was harmonised by picking a winner —
   picking one would have invented the contract a second time.
2. **Where a citation describes a rule the code does not implement, the code
   wins and the gap is recorded** (Appendix B). This is a description of a
   wire, not a wish for one.
3. **Nothing here has been run against hardware.** No puck is on this bench
   (no `/dev/cu.usbmodem*`) and the OG is at the rider's house on
   `src=5c80a436`, which predates `traceraw` entirely. Every `traceraw` rule
   below is pinned by the host build, `tools/fake_device.py` and the test
   suites — never by silicon. See Appendix C.

> **Line numbers are pinned to commit `0d094f6`** (branch `review/e5d6160`),
> not to a working tree. Every one of the 149 citations below was checked
> against `git show 0d094f6:<path>` on 2026-09-09.
>
> This matters because the tree was moving while this was written: `tools/jump`,
> `tools/fake_device.py`, `tools/tests/test_cli.py` and `web/sync/index.html`
> carried uncommitted work from concurrent sessions, which appeared, was
> reverted, and reappeared inside an hour. **In a working copy carrying those
> edits, numbers for those four files will differ — re-find by the named
> symbol.** The symbol is the durable half of every citation (CLAUDE.md §4);
> the number is only true of a commit.

---

## §0 — The situation, and what follows from it

The rider has **a phone and an older Intel MacBook — no repo, no toolchain,
no bench** (`web/sync/index.html:8-9`). This document said "a phone, no laptop"
until 2026-09-09; DECISIONS #42 corrected that on 2026-09-07, which is what put
the cable path on the page in the first place. The puck holds the only copy of
the ride until a bundle reaches Josh. Everything below exists because of that.

**Item 1 — the puck's copy is the only copy until it is not.** No path may
erase the puck on an inference. `clear` is offered only behind §3 step 4's two
conditions (`web/sync/sync.js:752`, `web/sync/sync.js:1514`), and every failure
path says the puck was untouched.

**Item 2 — a reading that did not happen is a finding, never a pass**
(CLAUDE.md rule 3). Every check in §1 and §2 that *could not run* is itself a
named reason, and `verified` is never collapsed upward from "unknown" to
"true" (`verifyPull()`, `web/sync/sync.js:1142`; `_verify_ingest_bundle()`'s
tri-state docstring, `tools/jump:2066`).

**Item 3 — a bundle from the phone must be scored exactly the way a bench sync
is.** This is the pinned item: `_analyze_and_report()` at `tools/jump:1559`
("the analysis `sync` and `ingest` share"), `tools/tests/test_ingest.py:396`,
and `tools/tests/test_ingest.py:162`, which exists because a flat synthetic
ramp "can never tell an offline pass that found nothing apart from one that
actually ran". One function scores both paths; `ingest` does not re-implement
it.

> **Numbering note.** Only **item 3** is fixed by a citation
> (`tools/jump:1559`, `tools/tests/test_ingest.py:162`, `:396` all say "§0 item
> 3"). Items 1 and 2 are reconstructed from code that cites `§0` without an
> item number (`web/sync/index.html:8-9`). Their *content* is implemented; their
> *numbers* are this document's.

---

## §1 — The wire: `traceraw`'s frame, and the one fallback

### §1.1 Why it exists

`trace`/`dump` cost ~17 bytes per sample as CSV. `traceraw` sends the trace
region's own stored bytes — ~2 stored bytes per sample, +33% for base64, so
~2.7 B/sample on the wire (`firmware/src/main.cpp:460-462`). A rider syncing
over BLE waits on the wire, not on the flash.

### §1.2 The frame — fixed, and parsed strictly

```
# traceraw bytes=<N> log_hz=<hz> region_bytes=<capacity>
FILE trace.bin BEGIN
<base64, 76 characters per line (57 raw bytes), last line shorter>
FILE trace.bin END
[# WARNING trace.bin INCOMPLETE — ...   (only if something came up short)]
# traceraw crc32=<8 lowercase hex> bytes=<N>
OK traceraw
```

Implemented by `printTraceRawFramed()` (`firmware/src/main.cpp:481`), with the
framing written out as a comment block at `firmware/src/main.cpp:465-471`.
Header line: `:486`. `BEGIN`: `:489`. `END`: `:557`. crc line: `:595`.
`OK traceraw`: `:887`. The reference device reproduces it byte for byte in
`send_traceraw()` (`tools/fake_device.py:209-235`).

**What a client copies.** Everything between `FILE trace.bin BEGIN` and `FILE
trace.bin END`, and nothing else, is the file body.

- `parse_file_sections()` (`tools/jump:1485`) matches `startswith("FILE ")` +
  `endswith(" BEGIN")` / `endswith(" END")`, so **the FILE lines carry no extra
  tokens** — all metadata rides on `#` chatter
  (`firmware/src/main.cpp:472-474` states this rule).
- `web/sync/sync.js` feeds body lines straight to `atob()` (`feedB64()`,
  `web/sync/sync.js:572`), decoding a line at a time into a growing
  `Uint8Array` with CRC-32 updated as bytes land — the page never holds the
  ~2.7 MB body as a string (`web/sync/sync.js:17-20`).

**Base64 shape.** 76 characters per line, 57 raw bytes per line, last line
shorter (`firmware/src/main.cpp:468`; `tools/fake_device.py:230`). `=` padding
appears **only at the very end** (`firmware/src/main.cpp:466`; relied on by
`web/sync/sync.js:576`, and pinned by fixtures at
`tools/tests/test_web_sync.py:85-97` precisely because the ordinary fixture is
0 mod 3 and would never reach the padding branch).

**The two `bytes=` values are both `N`** — the number the header announced,
never what actually streamed (`firmware/src/main.cpp:586-594`). The reason is
in that comment and is a rule, not a detail: printing the streamed count next
to the crc32 computed over the same short body would make a truncated export
**internally consistent**, and §2's `verified` would go true on a trace missing
its tail. With `N` in both places, a short body fails the byte-count check
against either line. `tools/tests/test_hostdev.py:890-895` pins this.

**Chatter is outside the frame, never inside it.** The `# WARNING trace.bin
INCOMPLETE` lines are emitted **after** `FILE trace.bin END`
(`firmware/src/main.cpp:557`, then `:575` and `:583`). The reason
(`firmware/src/main.cpp:563-577`): inside the frame the em dash is not ASCII,
Python's `b64decode` raises and the browser's `atob` throws — a warning in the
body is a decode failure, not a note. Worse, clients keep FILE bodies out of
`device.log` (§2), so inside the frame is exactly where §2's `verified`
criterion (a) would throw away its only evidence.

> `printFileFramed()` — the CSV `jumps`/`trace`/`dump` path — does the
> opposite: its warning lands **inside** the frame, before `FILE <name> END`
> (`firmware/src/main.cpp:430-437`). Clients must therefore select `#` chatter
> **by kind, not by position** (`web/sync/sync.js:424-441`;
> `tools/jump:1830` filters in-frame `#` lines out of the base64 before
> decoding). This asymmetry is real and load-bearing:
> `tools/tests/test_web_sync.py:627-664` exists because routing that line into
> the body sink once made it count as a jump and unlocked step 4.

**`region_bytes=` is capacity, not usage.** `bytes=` is
`jh_store::trace_raw_bytes()` (`firmware/src/main.cpp:482`); `region_bytes=` is
`jh_store::trace_region_bytes()` (`:488`). A real device's `region_bytes` is
always `>=` `bytes=`. On the **host build they are the same number**
(`firmware/src/platform/host/jh_store.cpp:313-318`, and
`tools/fake_device.py:223-227` reports used bytes for both) — a client must not
read the pair as "used out of capacity" against a host build. The cap that
makes `trace_is_full()` true is `JH_TRACE_MAX_BYTES`
(`firmware/include/params.gen.h:22`, currently 2 000 000).

### §1.3 crc32

CRC-32/ISO-HDLC — reflected poly `0xEDB88320`, init `0xFFFFFFFF`, final XOR —
byte for byte what Python's `zlib.crc32()` computes, so a receiver verifies with
one stdlib call and no device-specific code (`firmware/src/main.cpp:441-457`).
Verified with `zlib.crc32` in `_verify_traceraw_download()`
(`tools/jump:1763`), with a table-driven implementation in
`web/sync/sync.js:185-207`, and with `zlib.crc32` again in
`tools/fake_device.py:234`.

The crc is computed over **what actually streamed**
(`firmware/src/main.cpp:519`, inside the read loop), while `bytes=` announces
`declared`. That pairing is deliberate — see §1.2.

### §1.4 The ERR strings, and the single fallback rule

| String | Meaning | Client behaviour |
|---|---|---|
| `ERR unknown_command traceraw` | firmware predates the command | **Fall back to the CSV `trace`/`dump` path** |
| `ERR traceraw storage_down` | `fs_ok` false — the store never mounted | Report as-is, abort. Not a fallback trigger |
| `ERR traceraw_unsupported this build has no raw trace store` | `open_read_raw()` refused | Report as-is, abort. Not a fallback trigger |

Emitted at `firmware/src/main.cpp:879`, `:884`, and (for the unknown command)
`:1402` — the help line prints **before** the `ERR` terminator
(`firmware/src/main.cpp:1398-1402`), because clients stop reading at `OK`/`ERR`
and anything after it would corrupt the next command's framing.

`storage_down` and "there is no trace" are deliberately different answers
(`firmware/src/main.cpp:869-878`): "a client that cannot tell them apart will
happily clear a puck whose session it never actually read."

**The fallback rule is: CSV on `ERR unknown_command` only. Every other `ERR` is
reported as-is.** There is no CSV path that recovers from the storage layer
being down.

- `_sync_via_traceraw()` (`tools/jump:1798`) — exact string equality at
  `tools/jump:1816` (`if last == "ERR unknown_command traceraw": return None`);
  any other `ERR` prints and raises `SystemExit(1)` at `tools/jump:1819`.
- `web/sync/sync.js:1029` — regex `/^ERR unknown_command\b/`, i.e. a **prefix**
  match. See Appendix A, disagreement A3.
- `tools/fake_device.py:310-322` reproduces all three branches; the
  `--no-traceraw` branch (`:310-317`) is the only way any test reaches the
  fallback, because nothing on the bench has `traceraw`.
- Pinned by `tools/tests/test_cli.py:486` (fallback) and `:504` (abort).

### §1.5 The round-trip guarantee

Whichever path `sync` takes, the session it writes must be
indistinguishable — same `trace.csv`, same `jumps.csv`
(`tools/tests/test_cli.py:469-484`). The raw path additionally keeps
`trace.bin` next to them (`tools/jump:1936`).

This only holds because the region is decoded at the rate it was recorded at.
`log_hz` on the header line is that rate. When chatter does not carry it, the
client falls back to `config/params.json` **and says so out loud**
(`tools/jump:1871-1878`) — a wrong rate mis-decodes every timestamp under a
green ✅, since crc32 is over raw bytes and does not know what rate they are
decoded at. `tools/fake_device.py:148-161` refuses to start at all if
`firmware.log_hz != 50`, for the same reason.

---

## §2 — The bundle: `manifest.json`, the files, and `verified`

### §2.1 The files

A bundle is a zip (or its unzipped directory) built by `buildBundle()`
(`web/sync/sync.js:1372-1392`) and read by `cmd_ingest()` (`tools/jump:2247`):

| File | Always? | Written | Read |
|---|---|---|---|
| `manifest.json` | yes | `sync.js:1376` | `tools/jump:2281` — absent ⇒ "not a jump-sync bundle" |
| `jumps.csv` | yes | `sync.js:1378` | `tools/jump:2298` — absent ⇒ refuse |
| `trace.bin` | `trace_format=jhtrace-v2-b64` only | `sync.js:1382` (sliced, exactly `N` bytes) | `tools/jump:2309` |
| `trace.csv` | `trace_format=csv` only | `sync.js:1384` | `tools/jump:2310` |
| `notes.txt` | yes | `sync.js:1386` | `tools/jump:2300` — absent ⇒ warn, write empty |
| `device.log` | yes | `sync.js:1387` | `tools/jump:2304` — absent ⇒ warn, write empty |

**`device.log` keeps the frame lines and all `#` chatter, and no FILE bodies.**
`web/sync/sync.js:351` ("every line EXCEPT FILE bodies"), the selection at
`:424-445`, and the reason at `:443-444` — the traceraw body alone is ~2.7 MB
of base64 and `device.log` is meant to be readable. Pinned by
`tools/tests/test_web_sync.py:521-523`. It is **cumulative for the whole
session**, so the INCOMPLETE scan reads only from this pull's start index
(`S.pullLogStart`, `web/sync/sync.js:1007`; reason at `:1004-1006`) — a warning
from a failed first attempt must not condemn every retry after it.

### §2.2 `manifest.json` — the keys

Every key is present in a bundle this page builds (`buildManifest()`,
`web/sync/sync.js:1237-1286`); the list is asserted key-by-key at
`tools/tests/test_web_sync.py:465-473` and mirrored as a fixture at
`tools/tests/test_ingest.py:56-88`.

| Key | Source | Line |
|---|---|---|
| `bundle_version` | literal `1` | `sync.js:1244` |
| `page_version` | baked-in `PAGE_VERSION` | `sync.js:42`, `:1245` |
| `puck_name` | `# name=` from `info` | `sync.js:502-504`, `:1246` |
| `fw`, `src` | `INFO` keys | `sync.js:1247-1248` |
| `synced_at_utc`, `synced_at_local`, `tz_offset_min` | the phone clock at `stats_before` | `sync.js:1249-1251` |
| `uptime_s` | `STATS uptime_s` | `sync.js:1241`, `:1252` |
| **`trace_epoch_utc`** | **`synced_at_utc − uptime_s`** | `sync.js:1255` |
| `info_lines`, `cal` | as received | `sync.js:1256-1257` |
| `stats_before`, `stats_after` | the two `STATS` lines | `sync.js:1258-1259` |
| `selftest_lines` | `selftest`, diagnostic only | `sync.js:1260` |
| `trace_format` | `'jhtrace-v2-b64'` \| `'csv'` \| `null` | `sync.js:1261` |
| `log_hz` | chatter, else `INFO log_hz` | `sync.js:1262` |
| `trace_bytes_device` | `STATS trace_bytes` (before) | `sync.js:1263` |
| `trace_bytes_got` | trace bytes actually received, **csv path only** (`null` on the raw path — `trace_raw_bytes` there is decoded binary, a different unit). Working tree only, see §2.5a | `sync.js` ~`:1344` |
| `f22_band_applied` | `true` when the csv byte check was forgiven inside F-22's band. Working tree only, see §2.5a | `sync.js` ~`:1345` |
| `trace_bytes_after` | `STATS trace_bytes` from the pull's **second** `stats`, read after the dump — the ceiling the csv check compares against, where `trace_bytes_device` is only the floor. `null` when that reply carried no `STATS`. Working tree only, see §2.5b | `sync.js` ~`:1448`, captured at ~`:1134` |
| `trace_raw_bytes` | bytes received, raw path only, else `null` | `sync.js:1264` |
| `trace_crc32` | computed on the phone, raw path only, else `null` | `sync.js:1265` |
| `stored_jumps_device` | `STATS stored_jumps` (before) | `sync.js:1266` |
| `jump_rows` | rows parsed, header excluded | `sync.js:1096`, `:1267` |
| `verified` | §2.4 | `sync.js:1268` |
| `cleared` | whether step 4 has since run | `sync.js:1275` |
| `transfer` | `{transport, seconds, bytes_received, mtu}` (`mtu` always `null` — Web Bluetooth does not expose it) | `sync.js:1276-1281` |
| `user_agent` | `navigator.userAgent` | `sync.js:1282` |

### §2.3 `trace_epoch_utc = synced_at_utc − uptime_s`

The puck has no RTC. Trace time is seconds since boot, so without this a
recording can never be aligned to video, to a written log, or to anything that
happened in the real world. `uptime_s` and the host clock are read **in the
same breath**, and after that `wall_clock(t) = trace_epoch_utc + t` forever.

- Firmware always emits `uptime_s` on `STATS` (`up_key`,
  `firmware/src/main.cpp:840-842` — unconditional, unlike the adder keys beside
  it), with the rationale at `:832-839`.
- The page computes it: `new Date(at.getTime() - uptime * 1000).toISOString()`,
  `null` when `uptime_s` is missing (`web/sync/sync.js:1255`).
- The CLI's own bench path computes the identical thing in
  `_write_session_info()`: `(now - datetime.timedelta(seconds=uptime))
  .isoformat()` (`tools/jump:1550`), `None` when uptime is NaN.
- **`ingest` must copy the manifest's value and must NOT substitute this
  machine's clock** — `trace_epoch_utc`, `synced_at_utc` and
  `device_uptime_s_at_sync` all come straight from the manifest
  (`tools/jump:2403-2405`, with the rule stated at `:2394-2398`).
  `web/sync/sync.js:1253-1254` states the same rule from the other side.
- The anchor is taken **only** from a `STATS` that answers this page's own
  `stats` command (`web/sync/sync.js:527-537`): over the cable a stale `STATS`
  can sit in the CDC buffer and arrive first, and a straggler taken as
  `stats_before` would silently misdate every sample in the trace.
- The same "never this machine's clock" rule governs the session directory
  name — see §4.3.

`session.json` written by `ingest` and by a bench `sync` share one schema and
one `note` constant (`SESSION_NOTE`, `tools/jump:1503`), so the two writers
cannot drift.

### §2.4 `verified`

**`verified` means: every applicable check ran and passed, and every check that
could not run is itself a reason.** An empty reason list means verified
(`web/sync/sync.js:1139-1141`, `:1099`).

The page's checks, in the order `verifyPull()` runs them
(`web/sync/sync.js:1142-1206`):

- **(0) `fs=down`** — the store never mounted, so every count below came back
  *unknown*, not zero (`:1153`). Sticky for the session (`:501`, `:522`).
  Measured on the `#mock` seam before this existed: the page verified TRUE and
  offered step 4 for a puck whose store it never read (`:1145-1152`).
- **(a) The puck's own complaint outranks any arithmetic we do** — any
  `INCOMPLETE` line in this pull's `device.log` slice (`:1163-1168`).
- **(b) Jump rows vs the puck's own `stored_jumps`**, and only **when
  `stored_jumps > 0`** (`:1172-1175`) — 0 legitimately means an empty puck.
- **(c) The ride data itself** (`:1178-1204`):
  - raw path — a base64 decode error; a missing `bytes=`; `len !== bytes=`; a
    missing `crc32=`; a crc mismatch. *Each* "could not be checked" is its own
    reason.
  - csv path — received bytes must equal `STATS trace_bytes` exactly
    (`:1194-1204`). There is no crc for CSV, so this byte count is the whole
    check (`tools/tests/test_web_sync.py:114-116`). **Working tree (§2.5a): a
    shortfall of 1..800 B is now audit F-22 and verifies; a bigger gap, or any
    surplus, still fails.** **Working tree (§2.5b): the page compares against a
    WINDOW, not a number — `trace_bytes` before − 800 ≤ got ≤ `trace_bytes`
    after — and `trace_bytes=0` with a 6-byte body is the empty puck, not a
    surplus.**
  - neither format — "No ride data arrived at all."

`ingest` **re-runs all of it independently of the manifest's own claim**,
because "the manifest was produced by a phone browser, not this repo, so it is
exactly as untrusted as any other download" (`_verify_ingest_bundle()`,
`tools/jump:2052-2069`). It also **honours a `verified: false`** it finds
(`tools/jump:2103-2113`): a page that refused to call the pull verified saw
something at pull time, and one of its reasons ("the puck never said how much
ride data to expect") leaves no other mark in the bundle.

`ok` is a true tri-state — `True` / `False` / `None` — and **never silently
collapsed to `True`** (`tools/jump:2066-2071`). `None` means nothing was
checkable; `ingest` imports but says so (`tools/jump:2360`), writes
`"verified": ok is True` into `session.json` (`tools/jump:2417`), and drops a
`VERIFICATION-FAILED.txt` beside it (`tools/jump:2422-2427`). `False` refuses
the import without `--force` (`tools/jump:2347-2353`).

**Firmware and the store both encode the same rule from their own side**:
`firmware/src/main.cpp:569-573` names criterion (a) as "no INCOMPLETE warning
line from the puck", and `firmware/src/platform/host/jh_store.cpp:319-326`
exists so that the `streamed != declared` arm — "the arm that stands between
the rider and a truncated export that self-verifies (§2's `verified`)" — can be
tested at all.

### §2.5 The `trace_bytes` cross-check, and F-22

`STATS trace_bytes` is a **live counter that over-reports once the trace region
fills** (audit F-22). The raw and CSV paths handle the resulting mismatch
differently, and the difference is real.

**Raw `traceraw` path — informational, never a refusal.**
Once crc32 and full-region-decode both hold, a `trace_bytes` mismatch is a
note, not a verdict (`_verify_traceraw_download()`, `tools/jump:1730-1739`;
the note is appended at `:1792`). The note itself is `_trace_bytes_note()`
(`tools/jump:347-376`):

- within `-F22_MAX_OVERREPORT_BYTES <= delta < 0` and no damaged patch → "likely
  audit F-22", crc32 and the decode already verified independently;
- outside that band → "an unexplained shortfall, not F-22, and not a
  reassurance";
- either way it is **printed** — a reading that goes unreported is itself a
  finding.

`F22_MAX_OVERREPORT_BYTES = 800` (`tools/jump:344`). The figure comes from
`docs/audit-2026-08-22.md:62-79`: filling the region in the harness gave
`TRACE_BYTES n=14476006` live against a remount's 14,475,206 — 800 bytes,
"exactly one 50-sample batch at 16 B/line".

**CSV path at `0d094f6` — hard, everywhere.** There is no crc32 for CSV, so the
byte count is the whole check and it stays hard:

- bench `sync --csv` / old-firmware fallback: `_verify_download()`
  (`tools/jump:378`) — a mismatch is `TRACE INCOMPLETE`, `ok=False`.
- `ingest` on a `trace_format=csv` bundle: `tools/jump:2216`
  ("TRACE.CSV SHORT" ⇒ `ok=False` ⇒ refuse without `--force`).
- the page: `web/sync/sync.js:1194-1204` (⇒ a reason ⇒ `verified=false` ⇒
  step 4 never appears).

### §2.5a The CSV F-22 band — WORKING TREE, not `0d094f6`

**Read the provenance note first: the numbers in this subsection are from an
uncommitted working tree, not from the commit the rest of this file is pinned
to.** Re-find every one of them by the named symbol. This subsection describes
work landed on 2026-09-09 that changes §2.5's "hard, everywhere" rule, and it is
written separately rather than edited into the text above so that what
`0d094f6` does stays legible beside what the tree now does.

**The change.** On the CSV path a shortfall of `1..F22_MAX_OVERREPORT_BYTES`
bytes — and only that — is now F-22 itself, and verifies instead of refusing:

- `_verify_ingest_bundle()`'s csv branch (`tools/jump`, ~`:2382`) prints
  `✅ trace.csv verified: … — audit F-22` plus the rider-language line "The
  puck is full — its counter runs N bytes ahead once it fills (a known quirk);
  the copy is complete", and imports with **no `--force`**. Outside the band it
  still refuses, in two separately-worded arms: `TRACE.CSV SHORT` past the
  band, and `TRACE.CSV LONGER THAN THE PUCK SAID` for a surplus (saying "SHORT"
  there would be the wrong sentence on the one line a human reads).
- `verifyPull()`'s csv branch (`web/sync/sync.js`, ~`:1242`) applies the same
  one-sided band and the same sentence ⇒ no reason ⇒ `verified=true`. The page
  has no live device and cannot send `tracecheck` (the re-walk takes minutes; a
  rider on a beach cannot wait), so **the band is the whole arbiter there**.
- the page records its working in the manifest — `trace_bytes_device`,
  `trace_bytes_got`, `f22_band_applied` (`buildManifest()`, `web/sync/sync.js`,
  ~`:1335-1345`) — and shows the sentence in the result list (`okDetail()`,
  ~`:1163`), so a forgiveness is visible rather than inferred from
  `verified=true`. `ingest` re-derives it from the bytes and does not trust
  those fields, exactly as for every other manifest claim.
- the bench `sync --csv` path is unchanged by this: it already had a better
  arbiter, `_query_tracecheck()` (`tools/jump`, ~`:399`), because it has a live
  device to ask.

**The band is one-sided and it is narrow, on purpose.** F-22 only ever runs the
device counter HIGH, so `got > trace_bytes` is not F-22 and is refused; and
801 B is more than the one 50-sample batch the audit measured, so it is refused
too. Both directions are pinned:
`tools/tests/test_ingest.py::TestIngestF22CsvBand` (−765 clean, −800 clean,
−801 refuses, +1 refuses, and an assertion that the test's own copy of the
constant still equals the tool's) and
`tools/tests/test_web_sync.py::TestWebSync::test_csv_f22_band_*` against the
scripted puck.

**The band is duplicated, deliberately.** `F22_MAX_OVERREPORT_BYTES = 800` in
`tools/jump` (~`:345`) and in `web/sync/sync.js` (~`:81`); a static page cannot
import a Python constant. They are the same value with the same evidence line
and must move together — `test_the_band_here_is_the_band_in_the_tool` fails if
the tool's constant moves without the test's copy.

**`tracecheck` at `0d094f6`: no client consults it.** The firmware has the
command — it re-walks the region and prints `# tracecheck fast=<live>
slow=<re-walked>`, saying in as many words that "the slow number is the correct
one" when they differ (`firmware/src/main.cpp:889-903`) — and
`docs/audit-2026-08-22.md:79-80` names "make `tracecheck` the authority" as one
of F-22's two possible fixes. At `0d094f6`, **nothing sends it**: `git grep -c
tracecheck 0d094f6 -- tools/jump web/sync/sync.js tools/fake_device.py` returns
0 for all three, so no path there has an arbiter for a disputed byte count.

**In the working tree it has one, on one path only.** `_query_tracecheck()` /
`TRACECHECK_TIMEOUT_S` are consulted from `_verify_download()` when the byte
counts disagree, letting the slow number turn a refusal into a pass on the
**bench CSV sync path**. `ingest` and the page still cannot ask — a zip has no
device behind it, and a rider cannot wait out a full-region re-walk — which is
why those two use the band above instead.

> **The reading this was built for.** The OG at the rider's house runs
> `src=5c80a436` (`docs/STATUS.md`), which predates `traceraw` — so it can only
> produce a **csv** bundle. A bench reading on the OG (2026-09-07:
> `tracecheck fast=15917918 slow=15917153`, −765 B) sits inside F-22's 800-byte
> band and outside the CSV path's zero tolerance at `0d094f6`: a COMPLETE
> download that the page refused to verify and `ingest` refused to import
> without `--force` — the same override a genuinely corrupt bundle needs. That
> reading is carried in a session brief; it is **not** recorded in any file in
> this tree, and it is the only measurement behind §2.5a. It was taken on the
> OG, the only board with a battery (CLAUDE.md §1); no puck is on this bench.

### §2.5b The accepted window, and the header-only region — WORKING TREE

**Working tree again, not `0d094f6`: re-find both by symbol.** Landed
2026-09-09 for the loan, alongside §2.5a and in the same `verifyPull()` csv
branch. Neither touches F-22's band, which still governs the short side alone.

**The window, not the number.** `STATS trace_bytes` is read ONCE, on connect,
and latched (`S.statsBefore`, `web/sync/sync.js` ~`:527-537`) — but the puck
does not stop recording because someone plugged it in, so by the time the dump
finishes that figure is a **floor**, not a total. Compared against it alone, a
puck that logged while it was handled produced *more* bytes than it "held" and
fell into the surplus arm: a hard refusal that every retry reproduced, because
every retry re-reads the same stale figure. The pull's own **second** `stats`
(`doPull()`, ~`:1132-1134`, kept as `S.statsAfterKV`) is the ceiling. The csv
check now accepts `trace_bytes`(before) − `F22_MAX_OVERREPORT_BYTES` ≤ `got` ≤
`trace_bytes`(after) — the low end is §2.5a's F-22 band, unchanged
(~`:1330-1346`); the high end is this (~`:1347-1364`), which says so in the
rider's own words ("The puck kept recording while you plugged it in — N extra
bytes; that is normal.", shown by `okDetail()` ~`:1227`) and records
`trace_bytes_after` in the manifest (~`:1448`). Above the ceiling, or with no
after-reading at all, it is still the unexplained surplus and still a refusal.
Pinned by `test_csv_growth_between_the_two_stats_reads_verifies` and
`…_past_the_after_reading_is_still_a_refusal`.

**The header-only region.** `trace_bytes=0` and a 6-byte body is an **empty
puck**, not a surplus (~`:1313-1325`). A real nrf52 puck always emits the
6-byte `"t,mag\n"` header when it dumps `trace.csv` — `read_chunk()` sends it
before it looks at whether a single stored byte is behind it
(`firmware/src/platform/nrf52/jh_store.cpp:1119-1126`) — while `trace_bytes`
only starts counting that header on the first append (`:1058-1063`). So the
one puck state that is unambiguously fine reported 0 and handed over 6, the
surplus arm called it "6 bytes against the puck's 0 … that does not add up",
and §2.4's "the puck has no jumps saved on it" outcome (`endPullOk()`
~`:1173-1178`) was unreachable on real hardware. The forgiveness is bounded at
6 bytes and at `trace_bytes === 0`: it forgives the header and nothing else.
Pinned by `test_header_only_trace_region_reads_as_an_empty_puck`.

> **`ingest` does NOT yet know either rule** — measured 2026-09-09 by calling
> `_verify_ingest_bundle()` directly on both bundle shapes. Its csv arm
> compares `got` against `trace_bytes_device` alone (`tools/jump` ~`:2368`,
> ~`:2431-2441`) and answers `❌ TRACE.CSV LONGER THAN THE PUCK SAID — got
> 4,406 bytes … (+700)` / `… got 6 bytes … (+6)`, `ok=False` ⇒ refused without
> `--force`. A bundle this page verifies under either rule above therefore
> still needs `--force` on Josh's side. That divergence is real and unfixed;
> it is not licence for a client to trust `verified` over its own arithmetic.

---

## §3 — The rider's page

### §3.1 Shape

Static files only — **no build step, no CDN, no framework**
(`web/sync/index.html:17`, `web/sync/sync.js:1-3`), which is also why the zip
writer is hand-rolled (`web/sync/sync.js:1288-1290`). **Zero external
requests**, enforced by aborting every non-localhost route in the acceptance
test (`tools/tests/test_web_sync.py:34-37`). **One theme**, light and
high-contrast, deliberately not `prefers-color-scheme`-aware
(`web/sync/sync.css:4-9`): read on a beach, in daylight, on a borrowed browser,
once. Device text never reaches `innerHTML` — text nodes only
(`web/sync/sync.js:32`, `web/sync/index.html:17-19`).

Transport is one interface — `{ sendLine, onLine, onClose, disconnect }` — over
Web Serial or Web Bluetooth (NUS UUIDs at `web/sync/sync.js:49-52`).

**Working tree, 2026-09-09 (`PAGE_VERSION` `2026-09-09c`): ONE way in per
device.** The rule used to be "show every link this browser can make", and
Chrome on a Mac reports **both** `navigator.serial` and `navigator.bluetooth`
— so the rider saw two competing Connect buttons and a hint each, with nothing
on the page saying which to press. Now `init()` (`web/sync/sync.js:1844-1850`)
shows the cable button and `#usb-hint` wherever `navigator.serial` exists and
hides `#btn-connect`, `#ble-hint` and `#ble-time-hint` entirely; Bluetooth is
offered **only** where there is no serial port at all. This is **visibility
only** — `doConnect()` stays bound and reachable, the mock seam does not go
through either button, and neither transport implementation changed. The cable
button's label is now `Connect` (it was `Connect with the cable`), and the two
sentences that named it were updated with it (`web/sync/sync.js:893-895`,
`:1022-1024`). Pinned by
`test_web_sync.py::TestWebSyncCable::test_only_one_way_in_is_ever_offered`,
which asserts both halves and states its environment assumptions.

**Step 1's facts list lost two rows in the same pass** (`web/sync/index.html`,
`#facts`): **Ride data waiting** (`#waiting`, a byte count) and **Puck
software** (`#fw`, a version plus a build hash) — diagnostics, not news to a
rider. Both figures still reach Josh in `manifest.json` (`trace_bytes_device`,
`fw`, `src`) and in `device.log`, so nothing is lost downstream. The one
*condition* those rows carried is the unmounted store, and it still reaches the
rider twice: `#stored-jumps` reads "unknown — not saving"
(`renderFacts()`, `web/sync/sync.js:748-758`) and the status line says NO REC
in his own words (`afterConnect()`, `web/sync/sync.js:1000-1003`). `verified`
and every check behind it are untouched.

### §3.2 Four phases, in order — THREE of them are presses

**Working tree, 2026-09-11c (`PAGE_VERSION` `2026-09-11c`): the four phases
below are the PAGE's, not the rider's.** They still happen, still in this
order, and every gate in them is unchanged. What changed is who performs them:
`afterConnect()` ends by calling `autoChain()` (`web/sync/sync.js`), which runs
`doPull()` and then builds the bundle, with no press in between — so connect →
copy → verify → build is one gesture.

**CORRECTED 2026-09-11c — this section said "TWO of them are presses" and
"two presses is the floor".** For one day the chain also called `doSend()`,
with no user gesture anywhere in the session. It was removed in review:
`downloadBlob()` returns `true` unconditionally — a page gets no completion
callback for `<a download>`, which is exactly why `delivered` is defined as a
hand-off below — so a Chrome that silently declined a gesture-free download
would have left the page reading "Saved to your Downloads", `delivered` true,
and **Empty the puck** live over a ride that never left the machine. Harmless
behind a human click; not behind a chain. `showSaveFilePicker()` would have
made the save confirmable, and was rejected because `typeof
window.showSaveFilePicker` is `undefined` in the test browser (Chromium
151.0.7922.34) — it would ship untested, which is how the broken share sheet
reached the rider.

**Three presses on a Mac: Connect → Save → Empty the puck.**
`navigator.serial.requestPort()` requires a user gesture, so Connect is
forced; the save is a press so that `delivered` means something; the erase is
destructive so it stays deliberate. What the chain does on its own is
everything that can be CHECKED — the copy, `verifyPull()`, and the zip — so
the press hands over a bundle that is already built (measured at 3 MB on the
test bench, two runs: 51 and 59 ms to build in the chain, 6.6 and 6.7 ms for
the press; `tools/tests/test_web_sync.py`, `bundle_build_ms` /
`save_press_ms`).

Two paths word that press differently, both deliberately:

- **A phone.** `navigator.share()` needs transient activation, and macOS
  Chrome has been **measured** rejecting it with `NotAllowedError` after the
  zip build (2026-09-10, §3.2 step 3 below). A chain that called `share()`
  itself would reject every time, so on `IS_MOBILE` the chain stops after the
  copy and offers one **Send**.
- **A note typed after the save already fired.** The zip is rebuilt and saved
  again behind one **Save it again with your note** button, which appears only
  when the note or a chip changed after delivery.

Pinned by `test_the_chain_never_saves_by_itself`: it drives a whole
connect-and-chain, asserts **no download fired**, `delivered` false and the
erase off screen, and then presses Save and asserts that it works.

**The one-button rule.** At any moment exactly one button is on screen: a
button is either the thing to do, or it is not there. **One stated exception
(2026-09-11c):** a copy that ARRIVED and did not verify shows **Try again**
and **Send** together — the ride is still on the puck to re-copy, and that
failed bundle is the only thing that can tell Josh why (until 2026-09-11c the
chain had already saved it for him). `setEnabled()`
(`web/sync/sync.js`) still computes every `.disabled` exactly as before — the
gates — and `setVisible()` is a presentation layer **that only ever hides**.
Nothing on the page is ever visible-and-greyed. The numbered step headings, the
`#facts` table, `#finish-hint` (dead markup: `showClear` was unconditionally
true) and the footer reassurance were cut in the same pass; `#status` starts
empty and hidden rather than saying "Not connected yet.". The `data-testid`
hooks in §3.3 all survive — `#puck-name`, `#battery` and `#stored-jumps` as
spans in a one-line caption.

**Step 1 — Connect.** Cable (Web Serial) or Bluetooth (Web Bluetooth), but
only one of them on screen at a time — see the one-way-in paragraph in §3.1.

**Step 2 — Copy the ride.** The command order is fixed
(`doPull()`, `web/sync/sync.js:1025-1053`): `jumps` → `traceraw` (→ `trace` on
the §1.4 fallback) → `stats` → `selftest`.
`tools/tests/test_store_host.py:1393-1397` depends on this order — the page
never opens the CSV reader at all, which is the only ordering that exercises
`open_read_raw()`'s own flush.

- **`selftest` is diagnostic, not required** — a failure or a timeout there
  costs a manifest field, never the ride (`web/sync/sync.js:1047-1053`).
- **The inactivity timer resets on every line**, body lines included
  (`INACTIVITY_MS`, `web/sync/sync.js:57`; `armCaptureTimer()` at `:613-618`;
  the body-line reset at `:462-466`) — 30 s, so a slow-but-flowing transfer
  never trips it while a genuinely stuck puck does. `selftest` gets less rope
  (`SELFTEST_MS`, 15 s, `:59`).

**Step 3 — Send.** Share sheet, else Downloads, else (iOS) a link the rider
presses himself. A built bundle is cached so a cancelled share can be retried
without pulling the whole ride off the puck again
(`web/sync/sync.js:1425-1426`). `?drop=https://…` is an optional push target —
"Josh's convenience, never a default" (`DROP_URL`, `web/sync/sync.js:68-75`).

**`delivered` means handed to the browser's downloader or accepted by the
share sheet** — a page gets no completion callback for `<a download>`, so this
is the strongest signal the platform offers, and §3 defines it that way on
purpose (`web/sync/sync.js:1402-1405`). The iOS long-press branch deliberately
does **not** count as delivered (`web/sync/sync.js:1445-1465`), because a click
that quietly does nothing, counted as delivered, would unlock step 4
(`web/sync/sync.js:82-88`).

**Step 4 — the `clear` gate.** **`clear` is reachable only after `verified` AND
`delivered`.**

- The button is hidden unless both hold (`offerClear`, `web/sync/sync.js:752`).
- `doClear()` re-checks both before writing anything to the wire
  (`web/sync/sync.js:1511-1514`) — "belt and braces on top of the hidden
  button".
- Pinned by `tools/tests/test_web_sync.py:610-617`: verified alone must not
  unlock Clear.
- After `clear`, the page re-reads `stats` and only calls it done when
  `stored_jumps == 0 && trace_bytes == 0` — "never call a wipe done on a
  reading that says otherwise" (`web/sync/sync.js:1528-1546`).
- Clearing drops the cached zip so a re-send rebuilds with `cleared: true`
  (`web/sync/sync.js:1533-1539`) — `ingest` reads that field to know whether the
  puck still holds a copy.

> The **gate** above is the contract. The **copy** around it is not: at
> `0d094f6` the heading is "Empty the puck" (`web/sync/index.html:96-101`), and
> uncommitted work in the worktree is rewording it to "Leave the puck alone —
> Josh does that" with the button still present and still gated. A client must
> key off the gate, never off the heading.

**CORRECTED 2026-09-11b — this section said "step 4 is OFF unless the URL says
otherwise", and that stopped being true on 2026-09-11a.** It described a loan
rule: the whole section hidden unless the URL carried `?allowclear=1`, the page
ending at step 3 with "You're finished. Josh empties the puck." The rule was
reversed by a session it cost — the region filled during a 1 h 54 m ride and
the firmware's own auto-clear (`main.cpp:1733`) wiped the trace the moment the
puck was **picked up to be synced**, so not emptying is not the safe option
(`OFFER_CLEAR_TO_RIDER`, `web/sync/sync.js`). The doc was not corrected with
the code; it is corrected here. What is true now:

- **The erase is part of the rider's flow**, offered on the plain URL, and
  `#finish-hint` — the line that said Josh does it — is gone from the markup.
- **`?allowclear=1` lifts `delivered`, and nothing else.** It is Josh asserting
  he already holds the ride; without the lift the admin path demanded a
  redundant multi-MB pull-and-send before it would erase a file he had in hand
  (`ALLOW_CLEAR`, folded into `offerClear`; `doClear()` re-checks it, so a
  button un-hidden by hand still writes nothing to the wire).
- **`verified` still has to hold, on both paths.** The gate above is unchanged.
- **The erase names the saved file in its own sentence** — "Once you can see
  `jumpheight-E2C4-…zip` in your Downloads:" (`setVisible()`,
  `web/sync/sync.js`). That is copy, not a gate, and it exists because
  `downloadBlob()` returns `true` whether or not Chrome wrote the file: a
  human click stands between `delivered` and the erase again (2026-09-11c),
  and a human *look* stands there as well.

Pinned by `test_step_four_is_offered_to_the_rider_once_the_ride_has_gone`
(plain URL: the erase stays off screen on a verified-but-unsaved ride, and is
offered once he has pressed Save — with the sentence naming the file),
`test_clear_is_never_sent_before_the_bundle_is_delivered` and
`test_the_owner_flag_lifts_the_delivered_requirement`. The last two are driven
as a PHONE: that was once the only shape in which "verified but not delivered"
could be observed at all, and since 2026-09-11c it is simply where the OTHER
delivery path — `navigator.share()` — lives.

### §3.3 The test seam

`#mock` installs a `MockTransport` and exposes
(`web/sync/sync.js:1585-1609`):

```js
window.__mock = { feed(line), sent: [] }
window.__sync = { state(), lastBundle() }
```

`state()` gained **`auto`** on 2026-09-11b: true while the page is still
running the chain itself. A driver cannot read `phase` alone any more —
`pulled` is where the chain RESTS on every device (it waits there for the
rider's Save press), and it is also what a chain still in flight looks like
from outside. `tools/tests/test_web_sync.py::_settled` is the reference
reading; `_save_press()` beside it is how a driver gets a DELIVERED ride.

`#mock-usb` plays a cable-shaped session instead, because transport kind
changes both the advice the page gives and what `manifest.json` records
(`web/sync/sync.js:76-80`).

**`data-testid` hooks are contract** (`web/sync/index.html:21-23`):
`btn-connect`, `btn-connect-usb`, `btn-pull`, `btn-send`, `btn-clear`,
`status`, `progress`, `puck-name`, `battery`, `result`, `note`. Rename one and
`tools/tests/test_web_sync.py` fails loudly.

### §3.4 Rider-facing language

Protocol strings are translated, not pasted (`failWord()`,
`web/sync/sync.js:960-993`): a page whose whole promise is that there is no
jargon must not print `ERR traceraw storage_down`. And the standing
reassurance — "the puck still has everything" — is **only** offered where the
puck is in a state to vouch for it (`retryCouldHelp()`,
`web/sync/sync.js:949-958`, used at `:991`): a store that never mounted cannot
be fixed by moving the phone.

---

## §4 — Names on disk

### §4.1 PUCK4

The 4 characters after `JumpHeight-` in the advertised name, e.g.
`JumpHeight-E2C4` → `E2C4`.

Two implementations, which **do not agree** — see Appendix A, A2:

- `puck4()`, `web/sync/sync.js:1218-1221` — `/JumpHeight-([0-9A-Za-z]{4})/`,
  fallback `'xxxx'`, chosen so "anything reading the name positionally still
  works and the gap is obvious rather than silent".
- `_puck4()`, `tools/jump:2041-2049` — `puck_name.rsplit("-", 1)[-1]`, fallback
  `"UNKN"`.

### §4.2 The bundle filename

`jumpheight-<PUCK4>-<YYYYMMDD>-<HHMM>.zip`, from the **phone's local clock at
`synced_at`** (`bundleName()`, `web/sync/sync.js:1223-1226`).

### §4.3 The session directory

`data/sessions/<YYYYMMDD-HHMMSS>-<PUCK4>/` (`_ingest_session_dir_name()`,
`tools/jump:2228-2245`).

The timestamp is the bundle's own **`synced_at_local`** — never this machine's
clock, the same rule `trace_epoch_utc` follows (§2.3). A missing or
unparseable `synced_at_local` **raises** (`tools/jump:2238`), and the caller
turns it into a plain `❌` line, rather than falling back to `datetime.now()`:
"a directory name silently backdated to whenever `ingest` happened to run is
exactly the kind of unlabeled guess CLAUDE.md rule 3 exists to prevent."

An existing non-empty target refuses without `--force` (`tools/jump:2366`) —
re-importing would silently overwrite `report.md` and any hand-added
`labels.csv`.

A bench `sync` names its directory from **this machine's** clock instead
(`tools/jump:1919`, `datetime.now().strftime("%Y%m%d-%H%M%S")`, no PUCK4
suffix) — there is no bundle and no phone in that path.

### §4.4 `--csv`

`./tools/jump sync --csv` forces the old `dump` path: no `traceraw` is sent at
all (`via = None`, `tools/jump:1933`), so no `trace.bin` is written and the
verdict line is the original "trace verified" wording.
`tools/tests/test_cli.py:455-466` cites this as §4; see Appendix A, A1.

---

## Appendix A — Where the citations disagree

Recorded, not resolved. Each is a real divergence between two things that both
cite this file.

**A1 — What §4 is about.**
`tools/jump:2044` (`_puck4()`'s docstring) and `tools/jump:2229`
(`_ingest_session_dir_name()`'s) cite §4 for *names* — PUCK4, the session
directory. `tools/tests/test_cli.py:455` cites §4 for the `--csv` *escape
hatch* ("--csv (CONTRACT.md §4) forces the old `dump` path"). Both readings are
written above (§4.1-§4.3 and §4.4). Nothing in the code settles which was
meant.

**A2 — Where PUCK4 lives, and what it is.**
`web/sync/sync.js:1215` attributes PUCK4 to "**§2/§4**"; `tools/jump:2044`
attributes it to §4 alone. And the two implementations differ materially:

| | `puck4()` — `web/sync/sync.js:1219` | `_puck4()` — `tools/jump:2049` |
|---|---|---|
| Accepts | exactly 4 alphanumerics after the literal `JumpHeight-` | any last `-`-separated segment, any length |
| Missing name | `'xxxx'` | `"UNKN"` |
| `"JumpHeight-E2C4"` | `E2C4` | `E2C4` |
| `"Puck-7"` | `xxxx` | `7` |

Bundle filenames and ingested directory names are therefore built by two
different rules. They agree on every real puck name (`JumpHeight-XXXX`) and
only on those.

**A3 — What triggers the CSV fallback.**
`tools/tests/test_cli.py:504` says "**only the EXACT string** `ERR
unknown_command traceraw`". `tools/jump:1816` implements exactly that (`==`).
`web/sync/sync.js:1029` implements a **prefix** match
(`/^ERR unknown_command\b/`), so the page would also fall back on
`ERR unknown_command <anything else>`. In practice both fire on the same input
(`tools/fake_device.py:317` echoes the command name back), so no test
distinguishes them.

**A4 — How §2's `verified` criteria are numbered.**
`firmware/src/main.cpp:570` and `tools/tests/test_web_sync.py:631` call the
INCOMPLETE scan "**criterion (a)**". `tools/tests/test_web_sync.py:116` calls
the csv byte count "**verified (c)**". Those two fit the lettering in §2.4.
But `tools/tests/test_ingest.py:335` calls the jump-row cross-check "§2's
**third** hard check" — and in that lettering jump rows are (b), the *second*.
The count reconciles only if the `fs=down` gate — which
`web/sync/sync.js:1145` labels "**(0)**" — is counted as the first. §2.4 above
keeps `(0)/(a)/(b)/(c)`, which is the page's own labelling, and leaves the
ordinal unclaimed.

**A5 — Whether the INCOMPLETE warning is inside or outside the FILE frame.**
Not a disagreement between citations, but a place where one rule reads like two:
`traceraw` puts it **outside** (`firmware/src/main.cpp:557`, then `:575`), the
CSV `printFileFramed()` puts it **inside** (`:430-437`). Both are current, both
are deliberate, and clients handle both by selecting on `#` rather than on
position. Stated in §1.2 so nobody "fixes" one to match the other.

---

## Appendix B — Where the reference device does not implement this contract

`tools/fake_device.py` is the only device any CLI test can talk to. Two §2
fields cannot be reached through it:

**B1 — `uptime_s` is absent from the fake's `STATS`.**
Firmware emits it unconditionally (`firmware/src/main.cpp:840-842`).
`tools/fake_device.py:295-300` emits `session_jumps`, `session_best_m`,
`session_best_airtime_s`, `stored_jumps`, `stored_best_m`, `trace_bytes` and
the battery suffix — and no `uptime_s`. Consequence: `_write_session_info()`
reads `float(kv.get("uptime_s", "nan"))` (`tools/jump:1541`), so **every
`--fake` sync writes `session.json` with `trace_epoch_utc: null`**. §2.3's
formula is therefore exercised by no CLI test — only by `web/sync/`'s own
`FakePuck` fixture (`tools/tests/test_web_sync.py:150`, whose `stats_line()`
carries `uptime_s=12345.678`).

**B2 — `# name=` is absent from the fake's `info`.**
Firmware emits it when a local name exists (`firmware/src/main.cpp:1207-1208`).
`tools/fake_device.py`'s `info` handler (`:376`) emits `INFO`, `PARAMS` and
`CAL` only. Consequence: `puck_name` — and therefore PUCK4, and therefore §4's
bundle and directory names — has no coverage on the CLI's own fake path.

Neither is a defect in this contract; both are gaps in the reference device,
recorded here so the next person does not read a green suite as coverage.

---

## Appendix C — What is unmeasured

- **No hardware has run any rule in this file.** There is no
  `/dev/cu.usbmodem*` on this bench. The OG is at the rider's house on
  `src=5c80a436` (`docs/STATUS.md`), which predates `traceraw` — so on that
  puck, today, §1's `traceraw` frame never appears and every client takes the
  §1.4 CSV fallback.
- **`docs/STATUS.md` says so itself**: "Remote diagnostics — built 2026-09-07,
  **NOT yet run on hardware**" (`docs/STATUS.md:295`).
- **What *is* pinned**: the host build (`env:host`), `tools/fake_device.py`,
  `tools/tests/test_cli.py`, `tools/tests/test_ingest.py`,
  `tools/tests/test_store_host.py`, `tools/tests/test_hostdev.py`, and
  `tools/tests/test_web_sync.py` driving the real page in a real headless
  Chromium.
- `docs/STATUS.md` wins over this file, as over every other document.
