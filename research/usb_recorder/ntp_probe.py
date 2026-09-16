#!/usr/bin/env python3
"""Read-only SNTP measurements; never discipline a clock or change configuration.

Four-timestamp equations and packet fields follow RFC 5905 sections 7–8:
https://www.rfc-editor.org/rfc/rfc5905.html
This is an unauthenticated diagnostic, not an NTP daemon or certified reference.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any

NTP_EPOCH = 2208988800
SECOND_NS = 1_000_000_000
ERA_SECONDS = 1 << 32
DEFAULT_SERVERS = ("time.google.com", "time.cloudflare.com", "time.apple.com")


def encode_timestamp(unix_ns: int) -> bytes:
    seconds, nanos = divmod(unix_ns, SECOND_NS)
    return struct.pack("!II", (seconds + NTP_EPOCH) % ERA_SECONDS,
                       nanos * ERA_SECONDS // SECOND_NS)


def decode_timestamp(data: bytes, pivot_unix_ns: int) -> int:
    seconds, fraction = struct.unpack("!II", data)
    base_ns = (seconds - NTP_EPOCH) * SECOND_NS + fraction * SECOND_NS // ERA_SECONDS
    era_ns = ERA_SECONDS * SECOND_NS
    era = (pivot_unix_ns - base_ns + era_ns // 2) // era_ns
    return base_ns + era * era_ns


def make_request(t1_unix_ns: int) -> bytes:
    packet = bytearray(48)
    packet[0] = 0x23  # LI=0, VN=4, mode=3(client)
    packet[40:48] = encode_timestamp(t1_unix_ns)
    return bytes(packet)


def parse_response(response: bytes, request: bytes, t1_unix_ns: int, t4_unix_ns: int,
                   monotonic_elapsed_ns: int) -> dict[str, Any]:
    if len(response) < 48:
        raise ValueError("NTP response shorter than 48 bytes")
    li, version, mode = response[0] >> 6, (response[0] >> 3) & 7, response[0] & 7
    stratum = response[1]
    if version not in (3, 4) or mode != 4:
        raise ValueError(f"unexpected NTP version/mode {version}/{mode}")
    if stratum == 0:
        raise ValueError(f"NTP kiss-of-death: {response[12:16].decode('ascii', errors='replace')}")
    if li == 3 or not 1 <= stratum <= 15:
        raise ValueError("server reports unsynchronized time")
    if response[24:32] != request[40:48]:
        raise ValueError("NTP origin timestamp does not match request")
    if response[32:40] == bytes(8) or response[40:48] == bytes(8):
        raise ValueError("zero server receive/transmit timestamp")
    t2 = decode_timestamp(response[32:40], t1_unix_ns)
    t3 = decode_timestamp(response[40:48], t1_unix_ns)
    if t3 < t2:
        raise ValueError("server transmit precedes server receive")
    elapsed_ns = t4_unix_ns - t1_unix_ns
    if elapsed_ns < 0 or monotonic_elapsed_ns < 0:
        raise ValueError("host receive precedes send")
    clock_disagreement_ns = elapsed_ns - monotonic_elapsed_ns
    if abs(clock_disagreement_ns) > 5_000_000:
        raise ValueError("host realtime/monotonic elapsed times disagree by over 5 ms")
    delay_s = (elapsed_ns - (t3 - t2)) / SECOND_NS
    if delay_s < -1e-6:
        raise ValueError("negative network delay beyond timestamp rounding tolerance")
    root_delay = struct.unpack("!i", response[4:8])[0] / 65536
    root_dispersion = struct.unpack("!I", response[8:12])[0] / 65536
    precision_exponent = struct.unpack("!b", response[3:4])[0]
    precision_s = math.ldexp(1, precision_exponent)
    root_allowance_s = max(0, root_delay) / 2 + root_dispersion + precision_s
    offset_s = ((t2 - t1_unix_ns) + (t3 - t4_unix_ns)) / (2 * SECOND_NS)
    # With nonnegative one-way delays and a constant relative clock offset,
    # the server-minus-host offset is bracketed by [T3-T4, T2-T1].
    asymmetric_low = (t3 - t4_unix_ns) / SECOND_NS
    asymmetric_high = (t2 - t1_unix_ns) / SECOND_NS
    allowance = max(0, delay_s) / 2 + root_allowance_s
    return {
        "leap_indicator": li, "version": version, "mode": mode, "stratum": stratum,
        "reference_id_hex": response[12:16].hex(),
        "t1_unix_ns": t1_unix_ns, "t2_unix_ns": t2, "t3_unix_ns": t3, "t4_unix_ns": t4_unix_ns,
        "offset_s": offset_s, "offset_sign": "server minus host",
        "delay_s": delay_s, "server_processing_s": (t3 - t2) / SECOND_NS,
        "host_realtime_elapsed_s": elapsed_ns / SECOND_NS,
        "host_monotonic_elapsed_s": monotonic_elapsed_ns / SECOND_NS,
        "host_clock_elapsed_disagreement_s": clock_disagreement_ns / SECOND_NS,
        "root_delay_s": root_delay, "root_dispersion_s": root_dispersion,
        "server_precision_s": precision_s,
        "root_allowance_s": root_allowance_s,
        "network_asymmetry_interval_s": [asymmetric_low, asymmetric_high],
        "uncertainty_s": allowance,
        "offset_interval_s": [offset_s - allowance, offset_s + allowance],
        "uncertainty_interpretation": "Scenario allowance: half round-trip delay plus server-reported root delay/2, dispersion and precision. Not a statistical confidence interval or authenticated bound.",
        "authenticated": False,
    }


def _resolve(server: str, timeout_s: float) -> list[Any]:
    results: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            results.put((True, socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)))
        except Exception as exc:
            results.put((False, exc))

    threading.Thread(target=worker, daemon=True).start()
    try:
        okay, value = results.get(timeout=timeout_s)
    except queue.Empty as exc:
        raise TimeoutError("DNS resolution timed out") from exc
    if not okay:
        raise value
    return value


def probe(server: str, timeout_s: float = 3) -> dict[str, Any]:
    result: dict[str, Any] = {"server": server, "valid": False, "request_hex": None, "response_hex": None,
                              "started_utc": datetime.now(timezone.utc).isoformat()}
    try:
        addresses = _resolve(server, timeout_s)
        if not addresses:
            raise ValueError("DNS returned no UDP addresses")
        result["resolved_addresses"] = [list(item[4]) for item in addresses]
        family, kind, protocol, _, address = addresses[0]
        with socket.socket(family, kind, protocol) as connection:
            connection.settimeout(timeout_s)
            connection.connect(address)  # Restrict received UDP packets to this peer.
            result["peer"] = list(connection.getpeername())
            mono1_before = time.monotonic_ns()
            t1 = time.time_ns()
            mono1_after = time.monotonic_ns()
            request = make_request(t1)
            result.update({"request_hex": request.hex(), "send_realtime_ns": t1,
                           "send_monotonic_before_ns": mono1_before, "send_monotonic_after_ns": mono1_after})
            connection.send(request)
            response = connection.recv(65536)
            mono4_before = time.monotonic_ns()
            t4 = time.time_ns()
            mono4_after = time.monotonic_ns()
            result.update({"response_hex": response.hex(), "receive_realtime_ns": t4,
                           "receive_monotonic_before_ns": mono4_before, "receive_monotonic_after_ns": mono4_after})
            elapsed = ((mono4_before + mono4_after) - (mono1_before + mono1_after)) // 2
            result["measurement"] = parse_response(response, request, t1, t4, elapsed)
            result["valid"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["failed_realtime_ns"] = time.time_ns()
        result["failed_monotonic_ns"] = time.monotonic_ns()
    return result


def discipline_evidence() -> dict[str, Any]:
    """Collect explicitly read-only evidence, preserving permission failures."""
    commands = []
    if platform.system() == "Darwin":
        commands = [["/usr/sbin/systemsetup", "-getusingnetworktime"],
                    ["/usr/sbin/systemsetup", "-getnetworktimeserver"],
                    ["/bin/launchctl", "print", "system/com.apple.timed"],
                    ["/usr/sbin/sysctl", "kern.ntp_pll"]]
    records = []
    for command in commands:
        record: dict[str, Any] = {"command": command}
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            record.update({"returncode": completed.returncode, "stdout": completed.stdout,
                           "stderr": completed.stderr})
            combined = (completed.stdout + completed.stderr).lower()
            record["permission_denied"] = any(phrase in combined for phrase in
                                               ("administrator access", "permission denied", "operation not permitted"))
        except (OSError, subprocess.SubprocessError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)
    config = None
    try:
        config = Path("/etc/ntp.conf").read_text()
    except OSError as exc:
        config = f"unavailable: {exc}"
    return {"host_discipline_verified": False, "platform": platform.platform(),
            "commands": records, "ntp_conf_text": config,
            "interpretation": "Read-only configuration/process/kernel evidence. A running daemon, configured server or one agreeing query does not prove active clock discipline; permission-denied and unavailable facts remain unverified."}


def run(args: argparse.Namespace) -> int:
    if (not 1 <= args.count <= 10 or not math.isfinite(args.timeout) or not 0 < args.timeout <= 30
            or not math.isfinite(args.interval) or not 16 <= args.interval <= 3600):
        raise ValueError("count must be1..10, timeout0..30s, repeat interval at least16s")
    servers = args.server or list(DEFAULT_SERVERS)
    if len(set(servers)) != len(servers):
        raise ValueError("server list must not contain duplicates")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {
        "schema": "jh6.ntp_probe.v1", "read_only": True, "timebase_calibrated": False,
        "host_discipline_verified": False, "authenticated": False,
        "source": "https://www.rfc-editor.org/rfc/rfc5905.html",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "servers": servers, "count_per_server": args.count, "repeat_interval_s": args.interval,
        "clock_info": {name: vars(time.get_clock_info(name)) for name in ("monotonic", "time")},
        "discipline_evidence": discipline_evidence(), "queries": [],
    }
    last_query: dict[str, float] = {}
    with (args.output_dir / "transcript.jsonl").open("x", encoding="utf-8") as transcript:
        for repeat in range(args.count):
            for server in servers:
                if server in last_query:
                    wait = args.interval - (time.monotonic() - last_query[server])
                    if wait > 0:
                        time.sleep(wait)
                last_query[server] = time.monotonic()
                query = probe(server, args.timeout)
                query["repeat"] = repeat
                result["queries"].append(query)
                transcript.write(json.dumps(query, allow_nan=False) + "\n")
                transcript.flush()
                if query["valid"]:
                    measured = query["measurement"]
                    print(f"{server}: server-host offset {measured['offset_s']:+.6f}s; "
                          f"delay {measured['delay_s']:.6f}s; allowance +/-{measured['uncertainty_s']:.6f}s")
                else:
                    print(f"{server}: {query['error']}", file=sys.stderr)
    result["ended_utc"] = datetime.now(timezone.utc).isoformat()
    result["valid_query_count"] = sum(query["valid"] for query in result["queries"])
    result["failed_query_count"] = len(result["queries"]) - result["valid_query_count"]
    result["transcript_sha256"] = hashlib.sha256((args.output_dir / "transcript.jsonl").read_bytes()).hexdigest()
    with (args.output_dir / "probe.json").open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    return 0 if result["valid_query_count"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory for packet transcript and summary")
    parser.add_argument("--server", action="append", help="repeat for multiple servers; default Google, Cloudflare, Apple")
    parser.add_argument("--count", type=int, default=2, help="queries per server (default2)")
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--interval", type=float, default=16, help="minimum seconds between queries to the same server")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError) as exc:
        print(f"ntp-probe: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
