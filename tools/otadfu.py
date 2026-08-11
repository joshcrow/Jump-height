#!/usr/bin/env python3
"""otadfu — flash the puck OVER THE AIR from the laptop (no cable, no phone).

The sealed-box story (docs/sense.md §3.3): once the capsule is closed, the
ONLY firmware path is the Adafruit bootloader's Nordic *legacy* OTA DFU.
The web app can never do this — browsers blocklist the Nordic DFU service
UUID in Web Bluetooth — and macOS has no working legacy-DFU CLI (Nordic's
own nrfutil speaks BLE only through their dongle hardware; adafruit-nrfutil
is serial-only). nRF Connect on a phone works, but for the bench the phone
is one more hand you don't have.

So this speaks the legacy DFU protocol directly over CoreBluetooth via
bleak: the same stack blecmd.py already uses. ~150 lines against a
protocol frozen since nRF SDK 11.

Flow (one command):

    python3 tools/otadfu.py                 # flash the current pio build
    python3 tools/otadfu.py path/to.zip     # flash a specific package

  1. If a device named `JumpHeight` is advertising, send it `dfu\n` over
     NUS first (the app's own command, firmware >= this commit) — it
     reboots into the bootloader. If `AdaDFU` is already advertising
     (e.g. a previous transfer died), skip straight to it: this doubles
     as the RECOVERY tool, which matters because in OTA DFU mode the
     board exposes NO USB at all (verified on silicon 2026-08-11 —
     no serial port, no UF2 drive; the radio is the only way in).
  2. Connect to `AdaDFU`, run the legacy DFU procedure:
     start(app) -> sizes -> init packet (.dat) -> stream .bin in 20-byte
     writes with packet-receipt flow control -> validate -> activate.
  3. The bootloader reboots into the new app; we rescan and confirm
     `JumpHeight` is back on the air.

Single-bank caveat, unchanged from §3.3: a transfer that dies mid-way
leaves the board waiting in the bootloader. That is exactly the state this
tool starts from (step 1's AdaDFU branch), so the recovery from a failed
run of this tool is... running it again.

The .zip is adafruit-nrfutil's own package format (manifest.json + .bin +
.dat), which the normal `pio run` build already produces.

SPDX-License-Identifier: MIT
"""

import asyncio
import json
import os
import struct
import sys
import time
import zipfile

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak not installed — pip3 install bleak")

# Nordic legacy DFU service (nRF SDK 11 era; what the Adafruit bootloader runs).
DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"
DFU_CONTROL = "00001531-1212-efde-1523-785feabcd123"   # write + notify
DFU_PACKET  = "00001532-1212-efde-1523-785feabcd123"   # write-without-response

# NUS, for the app-side `dfu` trigger.
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX      = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# Control-point opcodes (legacy protocol).
OP_START_DFU          = 0x01
OP_INITIALIZE         = 0x02
OP_RECEIVE_IMAGE      = 0x03
OP_VALIDATE           = 0x04
OP_ACTIVATE_RESET     = 0x05
OP_SYS_RESET          = 0x06
OP_REPORT_RECV_SIZE   = 0x07
OP_PKT_RCPT_NOTIF_REQ = 0x08
OP_RESPONSE           = 0x10
OP_PKT_RCPT_NOTIF     = 0x11
IMAGE_APPLICATION     = 0x04
RESP_SUCCESS          = 0x01

PKT_RCPT_INTERVAL = 10       # receipts still requested, consumed opportunistically
PACKET_GAP_S      = 0.012    # fixed pace; 20 ms/pkt measured clean, 12 ms = margin'd bet
CHECKPOINT_BYTES  = 10240    # 0x07 verification cadence
CHUNK = 20               # legacy DFU streams the image in 20-byte writes

DEFAULT_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "firmware", ".pio", "build",
                           "xiaoblesense_adafruit", "firmware.zip")


def load_package(path):
    """Pull app .bin + init .dat out of an adafruit-nrfutil package."""
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
        app = manifest["manifest"]["application"]
        return z.read(app["bin_file"]), z.read(app["dat_file"])


async def find(name, seconds=10.0):
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "").lower() == name.lower(),
        timeout=seconds)


async def trigger_app_dfu():
    """Ask the running app to reboot into the bootloader — VERIFIED.

    Hard lesson (2026-08-11): a whole evening of "the trigger is flaky" was
    macOS transport flakiness — BLE writes and serial lines that silently
    never arrived, each one misread as firmware bouncing back to the app.
    Every trigger whose delivery was CONFIRMED (the `OK dfu` farewell seen)
    entered the bootloader. So: a trigger does not count as sent until
    `OK dfu` is observed on the reply stream. Up to 3 attempts, fresh
    connection each; serial fallback when a CDC port exists.
    """
    import glob
    for attempt in range(3):
        dev = await find("JumpHeight", 6.0)
        if dev is None:
            return False
        got = asyncio.Event()
        buf = bytearray()
        def on_rx(_, data):
            buf.extend(data)
            if b"OK dfu" in buf:
                got.set()
        try:
            async with BleakClient(dev, timeout=15.0) as c:
                await c.start_notify(NUS_TX, on_rx)
                await c.write_gatt_char(NUS_RX, b"dfu\n", response=True)
                try:
                    await asyncio.wait_for(got.wait(), 4.0)
                    print(f"trigger CONFIRMED over BLE (attempt {attempt+1}): OK dfu seen")
                    return True
                except asyncio.TimeoutError:
                    print(f"attempt {attempt+1}: no OK dfu observed — retrying")
        except Exception as e:
            # a dropped link right after the farewell IS the success path;
            # only trust it if the farewell was seen
            if got.is_set():
                print(f"trigger CONFIRMED over BLE (attempt {attempt+1}, link died post-farewell)")
                return True
            print(f"attempt {attempt+1}: link error before confirmation ({type(e).__name__})")
        await asyncio.sleep(3.0)

    # Serial fallback — deterministic delivery when a cable happens to be in.
    ports = glob.glob("/dev/cu.usbmodem*")
    if ports:
        try:
            import serial as pyserial
            sp = pyserial.Serial(ports[0], 115200, timeout=0.5)
            t0 = time.time()
            while time.time() - t0 < 2:
                sp.read(500)
            sp.write(b"dfu\n")
            out = b""
            t0 = time.time()
            while time.time() - t0 < 5:
                try:
                    out += sp.read(500)
                except Exception:
                    break  # port dies when the reset fires
            if b"OK dfu" in out:
                print("trigger CONFIRMED over serial: OK dfu seen")
                return True
        except Exception as e:
            print(f"serial fallback failed: {e}")
    return False


async def dfu(zip_path):
    image, init_packet = load_package(zip_path)
    print(f"package: {os.path.basename(zip_path)}  app image {len(image)} bytes")

    # Step 1 — get the bootloader on the air (or find it already there).
    boot = await find("AdaDFU", 5.0)
    if boot is None:
        if not await trigger_app_dfu():
            sys.exit("neither AdaDFU nor JumpHeight is advertising — is the puck awake?")
        for _ in range(6):
            boot = await find("AdaDFU", 5.0)
            if boot:
                break
    if boot is None:
        sys.exit("app accepted `dfu` but AdaDFU never appeared — tap reset and retry")
    print(f"bootloader: AdaDFU ({boot.address})")

    responses = asyncio.Queue()
    acked = 0

    def on_notify(_, data: bytearray):
        nonlocal acked
        if data and data[0] == OP_PKT_RCPT_NOTIF:
            acked = struct.unpack("<I", data[1:5])[0]
        else:
            responses.put_nowait(bytes(data))

    async def expect(op):
        r = await asyncio.wait_for(responses.get(), 30.0)
        if len(r) < 3 or r[0] != OP_RESPONSE or r[1] != op or r[2] != RESP_SUCCESS:
            raise RuntimeError(f"bootloader refused op {op:#x}: {r.hex()}")

    async def clean_reset(dev):
        # A bootloader mid-way through a dead transfer answers START_DFU with
        # INVALID_STATE (0x02). Opcode 0x06 is a plain system reset — with the
        # app image invalid it boots straight back into DFU, state cleared.
        # (If USB happens to be attached, a plain reset lands in UF2/serial
        # mode instead and AdaDFU never reappears — the error below says so.)
        print("bootloader in a stale DFU state — resetting it clean")
        try:
            async with BleakClient(dev, timeout=15.0) as rc:
                await rc.write_gatt_char(DFU_CONTROL, bytes([OP_SYS_RESET]), response=True)
        except Exception:
            pass  # reset kills the link; expected
        await asyncio.sleep(3.0)
        for _ in range(6):
            d = await find("AdaDFU", 5.0)
            if d:
                return d
        raise RuntimeError(
            "bootloader did not come back as AdaDFU — if USB is plugged in it "
            "reset into UF2/serial mode instead; recover with "
            "`pio run -d firmware -e xiaoblesense_adafruit -t upload`")

    class LinkDropped(Exception):
        pass

    async def session(dev):
        """One connection, the whole legacy-DFU procedure. Raises on refusal.

        Every failure before this version was blind on one axis: when
        receipts stopped, we could not tell a dropped LINK from a dead
        NOTIFICATION path. The disconnected_callback settles it — and the
        two need opposite responses (reconnect vs re-subscribe), so the
        attribution is not a nicety.
        """
        nonlocal acked
        acked = 0
        dropped = asyncio.Event()

        def on_disconnect(_):
            dropped.set()

        def check_link():
            if dropped.is_set():
                raise LinkDropped("link dropped mid-session")

        async with BleakClient(dev, timeout=20.0,
                               disconnected_callback=on_disconnect) as c:
            await c.start_notify(DFU_CONTROL, on_notify)

            # start(app) + image sizes (softdevice, bootloader, app)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_START_DFU, IMAGE_APPLICATION]), response=True)
            await c.write_gatt_char(DFU_PACKET, struct.pack("<III", 0, 0, len(image)), response=False)
            await expect(OP_START_DFU)
            print("bootloader accepted image size")

            # init packet (the .dat: device/app ids + CRC)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_INITIALIZE, 0x00]), response=True)
            await c.write_gatt_char(DFU_PACKET, init_packet, response=False)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_INITIALIZE, 0x01]), response=True)
            await expect(OP_INITIALIZE)
            print("init packet accepted")

            # flow control: a receipt every PKT_RCPT_INTERVAL packets
            await c.write_gatt_char(DFU_CONTROL,
                bytes([OP_PKT_RCPT_NOTIF_REQ]) + struct.pack("<H", PKT_RCPT_INTERVAL), response=True)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_RECEIVE_IMAGE]), response=True)

            # MEASURED DESIGN (bench, 2026-08-11, dfu_probe.py): packet-
            # receipt notifications are UNRELIABLE under load — the
            # bootloader's notify has a single-slot queue and silently skips
            # a receipt when it is busy. At 20 ms/packet every receipt
            # arrives; at 2 ms/packet the stream dies after the first one
            # while the link stays up (disconnect callback proved the link
            # was fine). So flow control must NOT hard-depend on receipts:
            #
            #   - fixed conservative pace (PACKET_GAP_S) does the real work
            #   - receipts are consumed opportunistically when they arrive
            #   - every CHECKPOINT_BYTES, opcode 0x07 (report received image
            #     size) — a control-point exchange, reliable in every run —
            #     verifies the bootloader actually HAS what we sent; any
            #     shortfall is detected within one checkpoint instead of at
            #     the final CRC.
            t0 = time.time()
            next_ckpt = CHECKPOINT_BYTES
            for off in range(0, len(image), CHUNK):
                check_link()
                await c.write_gatt_char(DFU_PACKET, image[off:off + CHUNK], response=False)
                await asyncio.sleep(PACKET_GAP_S)
                sent_bytes = min(off + CHUNK, len(image))  # final packet is partial
                if sent_bytes >= next_ckpt or sent_bytes >= len(image):
                    next_ckpt += CHECKPOINT_BYTES
                    await c.write_gatt_char(DFU_CONTROL, bytes([OP_REPORT_RECV_SIZE]), response=True)
                    r = await asyncio.wait_for(responses.get(), 10.0)
                    if len(r) >= 7 and r[0] == OP_RESPONSE and r[1] == OP_REPORT_RECV_SIZE:
                        have = struct.unpack("<I", r[3:7])[0]
                        if have != sent_bytes:
                            raise RuntimeError(
                                f"byte loss detected at checkpoint: sent {sent_bytes}, "
                                f"bootloader has {have}")
                        pct = 100.0 * sent_bytes / len(image)
                        rate = sent_bytes / max(time.time() - t0, 1e-9) / 1024
                        print(f"  {pct:5.1f}%  {rate:5.1f} KB/s  verified {have} bytes", flush=True)
                    else:
                        raise RuntimeError(f"bad 0x07 response: {r.hex()}")

            await expect(OP_RECEIVE_IMAGE)
            print(f"image transferred in {time.time()-t0:.0f}s")

            await c.write_gatt_char(DFU_CONTROL, bytes([OP_VALIDATE]), response=True)
            await expect(OP_VALIDATE)
            print("image validated")

            try:
                await c.write_gatt_char(DFU_CONTROL, bytes([OP_ACTIVATE_RESET]), response=True)
            except Exception:
                pass  # activation resets the link mid-write; that's the success path

    # Settle before first contact: the observed flake is a START whose
    # response notification never arrives when connecting within ~1 s of the
    # bootloader appearing; the one clean 157 KB run was against a bootloader
    # that had been advertising for minutes.
    await asyncio.sleep(6.0)
    try:
        await session(boot)
    except (RuntimeError, asyncio.TimeoutError) as e:
        # Either the bootloader refused START (stale DFU state from a dead
        # transfer) or our START landed but the response never reached us
        # (same net state: mid-DFU). Both recover identically: 0x06 reset —
        # WHICH REQUIRES USB OUT; on USB the bootloader resets into
        # UF2/serial mode instead and clean_reset explains that — then one
        # more settled attempt.
        if isinstance(e, RuntimeError) and "refused op 0x1" not in str(e) and "stalled" not in str(e):
            raise
        boot = await clean_reset(boot)
        await asyncio.sleep(6.0)
        await session(boot)

    print("activated — bootloader is flashing and rebooting")

    # Step 3 — confirm the app came back.
    for _ in range(8):
        dev = await find("JumpHeight", 5.0)
        if dev:
            print("JumpHeight is back on the air ✅  OTA DFU complete")
            return
    print("WARNING: JumpHeight not seen yet — give it a few seconds and rescan "
          "(python3 tools/blecmd.py --scan)")


if __name__ == "__main__":
    zp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ZIP
    if not os.path.exists(zp):
        sys.exit(f"no package at {zp} — run `pio run -d firmware -e xiaoblesense_adafruit` first")
    asyncio.run(dfu(zp))
