"""Offline, diagnostic-only clock comparisons for JH6 recordings.

No device access and no calibrated IMU output. Sample timestamps, MCU service
timestamps and host read-completion timestamps describe different events.
Regression does not erase that distinction or certify any clock's accuracy.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import struct
import zlib

HEADER = struct.Struct("<4sBBH")
SAMPLE = struct.Struct("<IIIhhhhhhHH")
RAW_COLUMNS = ("seq", "mcu_us", "sensor_ticks", "gx_raw", "gy_raw", "gz_raw",
               "ax_raw", "ay_raw", "az_raw", "flags", "fifo_words")


def quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*p
    lo = int(index)
    hi = min(lo+1, len(ordered)-1)
    return ordered[lo] + (ordered[hi]-ordered[lo])*(index-lo)


def stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": min(values), "max": max(values),
            "mean": statistics.fmean(values), "median": statistics.median(values),
            "p01": quantile(values, .01), "p99": quantile(values, .99),
            "sd": statistics.stdev(values) if len(values)>1 else 0.0}


def parse_frames(data: bytes) -> tuple[list[dict], list[str]]:
    """Retain exact [start,end) wire offsets, including recovery after corruption."""
    result, issues = [], []
    pos = 0
    while pos < len(data):
        start = data.find(b"JH6F", pos)
        if start < 0:
            issues.append(f"{len(data)-pos} trailing bytes without frame header")
            break
        if start != pos:
            issues.append(f"{start-pos} unframed bytes at offset {pos}")
        if start+HEADER.size > len(data):
            issues.append("truncated header")
            break
        _, version, kind, size = HEADER.unpack_from(data, start)
        if size > 1200:
            issues.append(f"oversized frame at {start}")
            pos = start+4
            continue
        end = start+HEADER.size+size+4
        if end > len(data):
            issues.append(f"truncated frame at {start}")
            break
        crc = struct.unpack_from("<I", data, end-4)[0]
        if zlib.crc32(data[start:end-4]) & 0xffffffff != crc:
            issues.append(f"CRC mismatch at {start}")
            pos = start+4
            continue
        pos = end
        if version != 1:
            issues.append(f"unsupported version {version} at {start}")
            continue
        payload = data[start+8:end-4]
        try:
            if kind == 1:
                if size != SAMPLE.size:
                    raise ValueError("incorrect SAMPLE length")
                value = dict(zip(RAW_COLUMNS, SAMPLE.unpack(payload)))
            elif kind == 5:
                if size != 22:
                    raise ValueError("incorrect RAW_FIFO length")
                seq, raw_fifo = struct.unpack("<I18s", payload)
                value = {"seq": seq, "raw_hex": raw_fifo.hex()}
            elif kind in (0, 2, 3, 4):
                value = json.loads(payload)
                if not isinstance(value, dict):
                    raise ValueError("JSON frame must contain an object")
            else:
                raise ValueError(f"unknown frame type {kind}")
        except (ValueError, UnicodeError, struct.error) as exc:
            issues.append(f"invalid frame at {start}: {exc}")
            continue
        result.append({"start": start, "end": end, "type": kind, "data": value})
    return result, issues


def unwrap(values: list[int], bits: int, label: str) -> tuple[list[int], int]:
    """Reject reset/backwards/duplicates; a valid gap is below half a wrap."""
    if not values:
        return [], 0
    limit = 1 << bits
    if any(not isinstance(v, int) or not 0 <= v < limit for v in values):
        raise ValueError(f"{label} value outside unsigned {bits}-bit range")
    out, wraps = [values[0]], 0
    for previous, current in zip(values, values[1:]):
        delta = (current-previous) % limit
        if not 0 < delta < limit//2:
            raise ValueError(f"{label} duplicate, backwards value or ambiguous gap")
        wraps += current < previous
        out.append(out[-1]+delta)
    return out, wraps


def invalid_clock_intervals(values: list[int], bits: int,
                            nominal_period: float | None = None) -> list[dict]:
    """Identify invalid intervals without correcting, sorting or interpolating."""
    bad, modulus = [], 1 << bits
    for i, (previous, current) in enumerate(zip(values, values[1:]), 1):
        delta = (current-previous) % modulus
        reason = None
        if not (0 <= previous < modulus and 0 <= current < modulus):
            reason = "out_of_range"
        elif not 0 < delta < modulus//2:
            reason = "duplicate_backwards_or_ambiguous_gap"
        elif nominal_period is not None and not .75*nominal_period <= delta <= 1.25*nominal_period:
            reason = "period_outside_25_percent_nominal_guard"
        if reason:
            bad.append({"previous_index": i-1, "current_index": i,
                        "previous_raw": previous, "current_raw": current,
                        "signed_raw_delta": current-previous, "modulo_delta": delta,
                        "reason": reason})
    return bad


def read_host(path: Path, raw_size: int) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], ["host_receive.csv absent: host clock comparisons unavailable"]
    issues = []
    with path.open(newline="") as stream:
        rows = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(stream)]
    position, last_mono = 0, None
    for i, row in enumerate(rows):
        if row["byte_start"] != position or row["byte_end"] < position:
            raise ValueError(f"host read {i}: noncontiguous/backward byte offsets")
        position = row["byte_end"]
        if row["monotonic_after_ns"] < row["monotonic_before_ns"]:
            raise ValueError(f"host read {i}: reversed monotonic interval")
        if last_mono is not None and row["monotonic_before_ns"] < last_mono:
            raise ValueError(f"host read {i}: monotonic clock moved backwards")
        last_mono = row["monotonic_after_ns"]
    if position != raw_size:
        raise ValueError(f"host offsets end at {position}, raw.bin has {raw_size} bytes")
    return rows, issues


def map_host(samples: list[dict], reads: list[dict]) -> list[dict]:
    """One observation per host batch: its last fully completed SAMPLE frame.

    Earlier samples buffered into this batch share its completion time and must
    not be treated as independent clock observations. Partial frames belong to
    the read containing their final byte, not to their initial read.
    """
    if not reads:
        return []
    ends = [r["byte_end"] for r in reads]
    selected = {}
    for i, sample in enumerate(samples):
        batch = bisect.bisect_left(ends, sample["end"])
        if batch >= len(reads) or reads[batch]["byte_start"] >= sample["end"]:
            raise ValueError("completed sample has no corresponding host read")
        selected[batch] = {"sample_index": i, "batch_index": batch, **reads[batch]}
    return list(selected.values())


def linear_fit(x: list[float], y: list[float]) -> tuple[dict, list[float]]:
    if len(x) != len(y) or len(x) < 3:
        raise ValueError("a fit needs at least three matched points")
    xm, ym = statistics.fmean(x), statistics.fmean(y)
    dx = [v-xm for v in x]
    sxx = math.fsum(v*v for v in dx)
    if sxx <= 0:
        raise ValueError("fit has no time span")
    slope = math.fsum(a*(b-ym) for a, b in zip(dx, y))/sxx
    intercept = ym-slope*xm
    residuals = [b-(ym+slope*a) for a, b in zip(dx, y)]
    sse = math.fsum(v*v for v in residuals)
    lag_denom = math.fsum(v*v for v in residuals)
    lag1 = (math.fsum(a*b for a, b in zip(residuals, residuals[1:]))/lag_denom
            if lag_denom else 0.0)
    formal_se = math.sqrt(sse/(len(x)-2)/sxx)
    return {"n": len(x), "slope": slope, "intercept_s": intercept,
            "ppm_from_slope_one": (slope-1)*1e6,
            "formal_iid_slope_se_ppm": formal_se*1e6,
            "formal_iid_se_warning": "Diagnostic only: correlated/buffered observations violate iid errors.",
            "x_span_s": x[-1]-x[0], "y_span_s": y[-1]-y[0],
            "endpoint_slope": (y[-1]-y[0])/(x[-1]-x[0]),
            "residual_s": stats(residuals), "residual_lag1": lag1}, residuals


def block_bootstrap(slopes: list[float], block_length: int = 3,
                    repeats: int = 2000) -> dict:
    """Circular moving-block bootstrap of means of contiguous window slopes.

    This estimates run-local repeatability conditional on block length and
    approximate stationarity, not clock traceability or systematic latency.
    """
    if len(slopes) < max(6, 2*block_length):
        return {"available": False, "reason": "need at least six full windows and two blocks"}
    rng, means = random.Random(62840), []
    for _ in range(repeats):
        draws = []
        while len(draws) < len(slopes):
            start = rng.randrange(len(slopes))
            draws.extend(slopes[(start+j) % len(slopes)] for j in range(block_length))
        means.append(statistics.fmean(draws[:len(slopes)]))
    return {"available": True, "window_count": len(slopes), "block_length_windows": block_length,
            "repeats": repeats, "estimand": "mean of full-window slopes, not full-record OLS slope",
            "mean_slope": statistics.fmean(slopes),
            "ci95_slope": [quantile(means, .025), quantile(means, .975)],
            "ci95_ppm_from_one": [(quantile(means, p)-1)*1e6 for p in (.025, .975)],
            "assumption": "Approximate stationarity and dependence mostly shorter than resampled blocks; drift invalidates a universal CI."}


def compare(x: list[float], y: list[float], window_s: float,
            latency_bound_s: float | None = None) -> tuple[dict, list[float]]:
    fit, residuals = linear_fit(x, y)
    windows = []
    # Windows measured on the x clock; neither its nominal second nor ODR is exact.
    start = x[0]
    count = int((x[-1]-start)/window_s)
    for k in range(count):
        left = bisect.bisect_left(x, start+k*window_s)
        right = bisect.bisect_left(x, start+(k+1)*window_s)
        if right-left >= 3:
            w, _ = linear_fit(x[left:right], y[left:right])
            windows.append({"start_x_s": start+k*window_s, "n": right-left,
                            "slope": w["slope"], "residual_rms_s":
                            math.sqrt(statistics.fmean(v*v for v in residuals[left:right])),
                            "mean_full_fit_residual_s": statistics.fmean(residuals[left:right])})
    slopes = [w["slope"] for w in windows]
    fit["window_duration_x_s"] = window_s
    fit["windows"] = windows
    fit["window_slope_stats"] = stats(slopes)
    fit["moving_block_bootstrap"] = block_bootstrap(slopes)
    fit["moving_block_bootstrap_longer_blocks"] = block_bootstrap(slopes, 5)
    fit["window_slope_change_first_to_last_ppm"] = ((slopes[-1]-slopes[0])*1e6 if len(slopes)>1 else None)
    # Fitted residuals have zero global linear trend by construction. Show local
    # residual means and curvature rather than advertising that identity as a check.
    fit["residual_trend_note"] = "Full OLS residual linear trend is zero by construction; inspect window means, slope changes and residual CSV."
    empirical_spread = quantile(residuals, .99)-quantile(residuals, .01)
    fit["observed_p01_p99_residual_span_as_endpoint_ppm"] = empirical_spread/fit["x_span_s"]*1e6
    fit["observed_residual_span_warning"] = "Observed spread is not an upper bound on queued latency, drift or clock error."
    if latency_bound_s is None:
        fit["conditional_endpoint_latency_envelope"] = {"available": False,
            "reason": "No justified bound on endpoint latency difference supplied; absolute slope uncertainty remains open."}
    else:
        halfwidth = latency_bound_s/fit["x_span_s"]
        endpoint = fit["endpoint_slope"]
        fit["conditional_endpoint_latency_envelope"] = {
            "available": True, "endpoint_latency_difference_bound_s": latency_bound_s,
            "slope_interval": [endpoint-halfwidth, endpoint+halfwidth],
            "halfwidth_ppm": halfwidth*1e6,
            "assumption": "Difference in delays at the two endpoints is within +/- supplied bound; not inferred from read durations. Does not bound other clock/systematic errors."}
    return fit, residuals


def load_json(path: Path) -> dict | list | None:
    return json.loads(path.read_text()) if path.exists() else None


def ntp_reference(before_path: Path | None, after_path: Path | None) -> dict:
    """External-reference scenario; do not mistake RTT bounds for a certificate."""
    result = {"available": False, "absolute_timebase_certified": False,
              "interpretation": "NTP offsets are server-minus-host realtime. RTT/2 plus server-advertised root allowances is an unauthenticated uncertainty scenario, not a statistical confidence interval or guaranteed accuracy bound. Server agreement can share network/clock errors."}
    if before_path is None or after_path is None:
        result["reason"] = "Both before and after NTP probes are required; missing is not zero uncertainty."
        return result
    probes, selected = [], []
    for path in (before_path, after_path):
        probe = load_json(path)
        if not isinstance(probe, dict) or probe.get("schema") != "jh6.ntp_probe.v1":
            raise ValueError(f"invalid or missing NTP probe: {path}")
        best = {}
        for query in probe.get("queries", []):
            if not query.get("valid"):
                continue
            measurement = query.get("measurement", {})
            fields = ("offset_s", "delay_s", "uncertainty_s")
            if any(not isinstance(measurement.get(k), (int,float)) or not math.isfinite(measurement[k]) for k in fields):
                continue
            if measurement["delay_s"] < 0 or measurement["uncertainty_s"] < 0:
                continue
            mono_fields = ("send_monotonic_before_ns", "send_monotonic_after_ns",
                           "receive_monotonic_before_ns", "receive_monotonic_after_ns")
            if any(k not in query for k in (*mono_fields, "send_realtime_ns", "receive_realtime_ns", "server")):
                continue
            server = query["server"]
            item = {"server": server, **{k: measurement[k] for k in fields},
                    "realtime_midpoint_ns": (query["send_realtime_ns"]+query["receive_realtime_ns"])/2,
                    "monotonic_midpoint_ns": sum(query[k] for k in mono_fields)/4,
                    "realtime_midpoint_twice_ns": query["send_realtime_ns"]+query["receive_realtime_ns"],
                    "monotonic_midpoint_four_ns": sum(query[k] for k in mono_fields),
                    "host_clock_elapsed_disagreement_s": measurement.get("host_clock_elapsed_disagreement_s")}
            if all(isinstance(measurement.get(k), int) for k in ("t2_unix_ns", "t3_unix_ns")):
                item["server_midpoint_twice_ns"] = measurement["t2_unix_ns"]+measurement["t3_unix_ns"]
            if server not in best or item["delay_s"] < best[server]["delay_s"]:
                best[server] = item
        probes.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "valid_servers": best, "query_count": len(probe.get("queries", [])),
                       "failed_queries": sum(not q.get("valid") for q in probe.get("queries", []))})
        selected.append(best)
    result["probes"] = probes
    pairs = []
    for server in sorted(selected[0].keys() & selected[1].keys()):
        before, after = selected[0][server], selected[1][server]
        span = (after["realtime_midpoint_twice_ns"]-before["realtime_midpoint_twice_ns"])*.5e-9
        mono_span = (after["monotonic_midpoint_four_ns"]-before["monotonic_midpoint_four_ns"])*.25e-9
        if span <= 0 or mono_span <= 0:
            continue
        offset_change = after["offset_s"]-before["offset_s"]
        uncertainty = after["uncertainty_s"]+before["uncertainty_s"]
        correction = 1+offset_change/span
        halfwidth = uncertainty/span
        if "server_midpoint_twice_ns" in before and "server_midpoint_twice_ns" in after:
            server_span = (after["server_midpoint_twice_ns"]-before["server_midpoint_twice_ns"])*.5e-9
            server_span_method = "Direct difference of NTP (t2+t3)/2 server midpoints; independent of host realtime steps."
        else:
            server_span = span+offset_change
            server_span_method = "Equivalent realtime span + offset change; original server midpoint fields absent."
        mono_correction = server_span/mono_span
        mono_halfwidth = uncertainty/mono_span
        pairs.append({"server": server, "host_realtime_anchor_span_s": span,
                      "host_monotonic_anchor_span_s": mono_span,
                      "server_anchor_span_s": server_span, "server_anchor_span_method": server_span_method,
                      "direct_vs_offset_server_span_difference_s": server_span-(span+offset_change),
                      "before": before, "after": after,
                      "offset_change_s": offset_change, "endpoint_uncertainty_sum_s": uncertainty,
                      "external_seconds_per_host_realtime_second": correction,
                      "host_realtime_correction_ppm": (correction-1)*1e6,
                      "conditional_uncertainty_halfwidth_ppm": halfwidth*1e6,
                      "conditional_correction_interval": [correction-halfwidth, correction+halfwidth],
                      "external_seconds_per_host_monotonic_second": mono_correction,
                      "host_monotonic_correction_ppm": (mono_correction-1)*1e6,
                      "conditional_monotonic_uncertainty_halfwidth_ppm": mono_halfwidth*1e6,
                      "conditional_monotonic_correction_interval": [mono_correction-mono_halfwidth, mono_correction+mono_halfwidth]})
    result["per_server"] = pairs
    if not pairs:
        result["reason"] = "No valid same-server before/after pair; absolute reference remains unmeasured."
        return result
    lower = min(p["conditional_correction_interval"][0] for p in pairs)
    upper = max(p["conditional_correction_interval"][1] for p in pairs)
    result.update({"available": True,
        "selected_query_policy": "Minimum round-trip delay valid response per server per probe; servers are not pooled as independent statistical samples.",
        "conditional_correction_union_interval": [lower, upper],
        "conditional_monotonic_correction_union_interval": [min(p["conditional_monotonic_correction_interval"][0] for p in pairs), max(p["conditional_monotonic_correction_interval"][1] for p in pairs)],
        "server_point_correction_spread_ppm": (max(p["external_seconds_per_host_realtime_second"] for p in pairs)-min(p["external_seconds_per_host_realtime_second"] for p in pairs))*1e6,
        "server_scenario_intervals_overlap": max(p["conditional_correction_interval"][0] for p in pairs) <= min(p["conditional_correction_interval"][1] for p in pairs),
        "server_monotonic_point_correction_spread_ppm": (max(p["external_seconds_per_host_monotonic_second"] for p in pairs)-min(p["external_seconds_per_host_monotonic_second"] for p in pairs))*1e6,
        "server_monotonic_scenario_intervals_overlap": max(p["conditional_monotonic_correction_interval"][0] for p in pairs) <= min(p["conditional_monotonic_correction_interval"][1] for p in pairs),
        "transfer_assumption": "Transfer assumption: the monotonic clock rate averaged between NTP anchors also applies to the enclosed capture. Before/after queries do not establish a uniform within-capture clock rate. Realtime steps are handled by direct server-to-monotonic anchors, not by treating realtime as uniform."})
    return result


def analyze(directory: Path, window_s: float = 20,
            latency_difference_ms: float | None = None,
            ntp_before: Path | None = None, ntp_after: Path | None = None) -> tuple[dict, list[dict]]:
    if window_s <= 0 or not math.isfinite(window_s):
        raise ValueError("window seconds must be positive and finite")
    if latency_difference_ms is not None and (latency_difference_ms < 0 or not math.isfinite(latency_difference_ms)):
        raise ValueError("latency difference bound must be finite and nonnegative")
    raw = (directory/"raw.bin").read_bytes()
    frames, issues = parse_frames(raw)
    samples = [f for f in frames if f["type"] == 1]
    metas = [f["data"] for f in frames if f["type"] == 0]
    ends = [f["data"] for f in frames if f["type"] == 2]
    statuses = [f["data"] for f in frames if f["type"] == 4]
    raw_fifo_frames = [f["data"] for f in frames if f["type"] == 5]
    if len(metas) != 1 or len(ends) != 1:
        issues.append("capture must have exactly one META and one END")
    if any(f["type"] == 3 for f in frames):
        issues.append("capture contains firmware ERROR frame")
    if len(samples) < 3:
        raise ValueError("fewer than three valid sample frames")
    values = [s["data"] for s in samples]
    sequence = [s["seq"] for s in values]
    if sequence != list(range(len(values))):
        issues.append("sample sequence is not continuous from zero")
    if metas and metas[0].get("raw_fifo_frames"):
        if [f["seq"] for f in raw_fifo_frames] != sequence:
            issues.append("RAW_FIFO frames do not correspond one-to-one with SAMPLE sequence")
    if ends:
        if ends[0].get("sample_count") != len(values):
            issues.append("END sample_count disagrees with decoded sample frames")
        for key in ("dropped_frames", "i2c_errors", "fifo_overruns"):
            if ends[0].get(key) != 0:
                issues.append(f"END {key} is nonzero or absent")
        if ends[0].get("failure", "none") != "none":
            issues.append("END reports a failure")
    manifest = load_json(directory/"manifest.json") or {}
    if manifest.get("quality", {}).get("usable") is False:
        issues.append("capture manifest failed acquisition integrity")
    if (directory/"raw.csv").exists():
        with (directory/"raw.csv").open(newline="") as stream:
            csv_values = [{k: int(r[k]) for k in RAW_COLUMNS} for r in csv.DictReader(stream)]
        if csv_values != values:
            issues.append("raw.csv disagrees with binary samples")
    invalid_ticks = invalid_clock_intervals([s["sensor_ticks"] for s in values], 24, 1e6/(208*25))
    invalid_mcu = invalid_clock_intervals([s["mcu_us"] for s in values], 32)
    scope = {"raw_decoded_sample_count": len(values), "selected_sample_count": len(values),
             "selection": "all_decoded_samples", "timestamps_repaired": False}
    if invalid_ticks or invalid_mcu:
        first_bad = min(v["current_index"] for v in invalid_ticks+invalid_mcu)
        issues.append(f"invalid clock intervals: FIFO={len(invalid_ticks)}, MCU={len(invalid_mcu)}; full-run clock fit withheld")
        if first_bad < 3:
            raise ValueError("clock corruption before three valid prefix samples; no diagnostic fit possible")
        samples, values, sequence = samples[:first_bad], values[:first_bad], sequence[:first_bad]
        scope.update({"selected_sample_count": first_bad,
                      "selection": "valid_prefix_before_first_invalid_clock_interval",
                      "warning": "Full capture failed. Prefix fits are diagnostics only, not a repaired capture or repeatability acceptance. Later records remain in raw evidence and are not silently bridged."})
    ticks, tick_wraps = unwrap([s["sensor_ticks"] for s in values], 24, "FIFO ticks")
    mcu, mcu_wraps = unwrap([s["mcu_us"] for s in values], 32, "MCU service time")
    sensor_s = [(t-ticks[0])*25e-6 for t in ticks]
    mcu_s = [(t-mcu[0])*1e-6 for t in mcu]
    reads, host_issues = read_host(directory/"host_receive.csv", len(raw))
    selected = map_host(samples, reads)
    fits, residual_rows = {}, []
    bound = latency_difference_ms/1000 if latency_difference_ms is not None else None
    fits["mcu_seconds_per_nominal_fifo_second"], residual = compare(sensor_s, mcu_s, window_s)
    fits["mcu_seconds_per_nominal_fifo_second"]["implied_tick_us_in_mcu_units"] = 25*fits["mcu_seconds_per_nominal_fifo_second"]["slope"]
    for i, value in enumerate(values):
        residual_rows.append({"seq": value["seq"], "fifo_nominal_s": sensor_s[i], "mcu_nominal_s": mcu_s[i],
                              "sensor_ticks_unwrapped": ticks[i], "mcu_us_unwrapped": mcu[i],
                              "fifo_delta_ticks": ticks[i]-ticks[i-1] if i else "",
                              "mcu_delta_us": mcu[i]-mcu[i-1] if i else "",
                              "fifo_words": value["fifo_words"], "flags": value["flags"],
                              "mcu_minus_fifo_fit_residual_s": residual[i]})
    n_intervals = len(values)-1
    rates = {
        "nominal_odr_hz": 208, "nominal_tick_us": 25, "sample_count": len(values),
        "sample_intervals": n_intervals, "fifo_nominal_span_s": sensor_s[-1],
        "mcu_nominal_span_s": mcu_s[-1],
        "samples_per_fifo_nominal_second": n_intervals/sensor_s[-1],
        "samples_per_mcu_nominal_second": n_intervals/mcu_s[-1],
        "warning": "Rates are clock-relative observations, not declarations that either clock or the 208 Hz setting is exact."}
    host_summary = {"available": bool(selected), "notes": host_issues}
    if len(selected) >= 3:
        indices = [r["sample_index"] for r in selected]
        mono0, real0 = selected[0]["monotonic_after_ns"], selected[0]["realtime_after_ns"]
        mono = [(r["monotonic_after_ns"]-mono0)*1e-9 for r in selected]
        real = [(r["realtime_after_ns"]-real0)*1e-9 for r in selected]
        sx, mx = [sensor_s[i] for i in indices], [mcu_s[i] for i in indices]
        for label, x, y in (("host_monotonic_seconds_per_nominal_fifo_second", sx, mono),
                            ("host_realtime_seconds_per_nominal_fifo_second", sx, real),
                            ("host_monotonic_seconds_per_nominal_mcu_second", mx, mono),
                            ("host_realtime_seconds_per_nominal_mcu_second", mx, real)):
            fit, residual = compare(x, y, window_s, bound)
            fits[label] = fit
            for index, r in zip(indices, residual):
                residual_rows[index][label+"_residual_s"] = r
        host_intervals = sequence[indices[-1]]-sequence[indices[0]]
        rates.update({"host_selected_first_seq": sequence[indices[0]], "host_selected_last_seq": sequence[indices[-1]],
                      "host_selected_sample_intervals": host_intervals,
                      "host_monotonic_span_s": mono[-1], "host_realtime_span_s": real[-1],
                      "samples_per_host_monotonic_second": host_intervals/mono[-1],
                      "samples_per_host_realtime_second": host_intervals/real[-1] if real[-1]>0 else None})
        difference = [a-b for a, b in zip(real, mono)]
        realtime_fit, realtime_residuals = linear_fit(mono, real)
        host_summary.update({"read_count": len(reads), "sample_batches": len(selected),
                             "first_selected_realtime_ns": selected[0]["realtime_after_ns"],
                             "last_selected_realtime_ns": selected[-1]["realtime_after_ns"],
                             "empty_reads": sum(r["byte_end"] == r["byte_start"] for r in reads),
                             "read_duration_s": stats([(r["monotonic_after_ns"]-r["monotonic_before_ns"])*1e-9 for r in reads]),
                             "realtime_minus_monotonic_change_s": difference[-1],
                             "realtime_minus_monotonic_change_ppm": difference[-1]/mono[-1]*1e6,
                             "realtime_minus_monotonic_relative_s": stats(difference),
                             "backward_realtime_steps": sum(b<a for a,b in zip(real, real[1:])),
                             "realtime_vs_monotonic_fit": realtime_fit,
                             "nonuniform_realtime_relative_to_monotonic": max(abs(r) for r in realtime_residuals) > .001,
                             "nonuniform_realtime_threshold_note": "Flag when maximum absolute residual from a single realtime-versus-monotonic line exceeds 1 ms; an engineering diagnostic, not a traceable threshold.",
                             "pairing": "Last complete sample per read batch, using monotonic/realtime AFTER read; no repeated host observation for earlier buffered samples.",
                             "latency": "Host observations include sensor servicing, USB delivery, queued data and scheduling. A read bracket is not an acquisition-time bracket."})
        for record in selected:
            i = record["sample_index"]
            residual_rows[i].update({"host_batch_index": record["batch_index"],
                "host_monotonic_relative_s": (record["monotonic_after_ns"]-mono0)*1e-9,
                "host_realtime_relative_s": (record["realtime_after_ns"]-real0)*1e-9})
    deltas = [b-a for a, b in zip(ticks, ticks[1:])]
    mcu_deltas = [b-a for a, b in zip(mcu, mcu[1:])]
    fifo_words = [r["fifo_words"] for r in values]
    status_summary = {"count": len(statuses), "records": statuses,
        "temperature_c": stats([s["temp_c"] for s in statuses if isinstance(s.get("temp_c"), (float,int))]),
        "hfclkstat_values": sorted(set(s["hfclkstat"] for s in statuses if "hfclkstat" in s)),
        "timer4_prescaler_values": sorted(set(s["timer4_prescaler"] for s in statuses if "timer4_prescaler" in s)),
        "timer4_bitmode_values": sorted(set(s["timer4_bitmode"] for s in statuses if "timer4_bitmode" in s)),
        "interpretation": "HFXO selected/running is configuration evidence, not measured crystal ppm accuracy. On-chip temperature is not independently calibrated ambient temperature."}
    reference = ntp_reference(ntp_before, ntp_after)
    if reference["available"] and host_summary.get("first_selected_realtime_ns") is not None:
        for pair in reference["per_server"]:
            pair["anchors_enclose_selected_samples"] = (
                pair["before"]["realtime_midpoint_ns"] <= host_summary["first_selected_realtime_ns"]
                and pair["after"]["realtime_midpoint_ns"] >= host_summary["last_selected_realtime_ns"])
        for name, fit in fits.items():
            if name.startswith("host_realtime_"):
                if not host_summary["nonuniform_realtime_relative_to_monotonic"]:
                    fit["ntp_only_conditioned_slope_interval"] = [fit["slope"]*v for v in reference["conditional_correction_union_interval"]]
                else:
                    fit["ntp_conditioning_withheld"] = "Nonuniform host realtime observed; NTP anchor average is not applied to this realtime OLS fit. Use direct NTP server-to-monotonic anchors instead."
                fit["ntp_conditioning_warning"] = "External-reference scenario only: excludes serial/service delay drift, statistical fit variation and nonuniform clock drift; requires anchor-to-capture transfer assumption."
            elif name.startswith("host_monotonic_"):
                fit["ntp_only_conditioned_slope_interval"] = [fit["slope"]*v for v in reference["conditional_monotonic_correction_union_interval"]]
                fit["ntp_conditioning_warning"] = "Direct NTP server-to-monotonic anchor scenario: independent of realtime steps, but excludes serial/service delay drift, statistical fit variation and nonuniform monotonic clock drift. Requires rate stability between anchors and capture."
                fit["ntp_per_server_conditioned"] = [{
                    "server": pair["server"],
                    "slope": fit["slope"]*pair["external_seconds_per_host_monotonic_second"],
                    "scenario_slope_interval": [fit["slope"]*v for v in pair["conditional_monotonic_correction_interval"]],
                    "reference_factor_halfwidth_ppm_from_one": pair["conditional_monotonic_uncertainty_halfwidth_ppm"],
                    "scenario_halfwidth_ppm_of_slope": pair["conditional_monotonic_uncertainty_halfwidth_ppm"]/pair["external_seconds_per_host_monotonic_second"],
                    "anchors_enclose_selected_samples": pair["anchors_enclose_selected_samples"]}
                    for pair in reference["per_server"]]
    summary = {
        "schema": "jh6.clock_analysis.v1", "research_only": True, "timebase_calibrated": False,
        "capture": str(directory.resolve()), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "input_files": {name: {"bytes": (directory/name).stat().st_size,
                               "sha256": hashlib.sha256((directory/name).read_bytes()).hexdigest()}
                        for name in ("raw.bin", "raw.csv", "host_receive.csv", "manifest.json", "status.json", "fifo_raw.csv")
                        if (directory/name).exists()},
        "analysis_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "metadata": metas[0] if metas else None, "end": ends[0] if ends else None,
        "integrity_issues": issues, "analysis_scope": scope,
        "invalid_clock_intervals": {"fifo": invalid_ticks, "mcu": invalid_mcu},
        "rates": rates, "fits": fits, "host": host_summary,
        "interval_diagnostics": {"fifo_wraps": tick_wraps, "mcu_wraps": mcu_wraps,
            "raw_fifo_frame_count": len(raw_fifo_frames),
            "fifo_delta_ticks": stats(deltas), "mcu_delta_us": stats(mcu_deltas),
            "mcu_delta_us_per_fifo_delta_tick": stats([a/b for a,b in zip(mcu_deltas, deltas)]),
            "fifo_words": stats(fifo_words), "samples_with_more_than_nine_fifo_words": sum(w>9 for w in fifo_words),
            "fifo_interpretation": "Nine FIFO words are ONE complete six-axis-plus-timestamp record, not nine samples. fifo_words is occupancy before removing that record; >9 indicates backlog, not sample duplication."},
        "status": status_summary, "external_reference": reference,
        "gates": {"binary_integrity": not issues, "host_observations": len(selected)>=3,
                  "long_capture_at_least_600_host_seconds": rates.get("host_monotonic_span_s", 0)>=600,
                  "periodic_hfclk_temperature_status_observed": len(statuses)>=2 and all(
                      all(k in s for k in ("hfclkstat", "temp_c", "timer4_prescaler", "timer4_bitmode"))
                      for s in statuses),
                  "external_reference_observed": reference["available"],
                  "absolute_timebase_validation": False, "physical_thermal_campaign": "not established by this analysis"},
        "interpretation": [
            "No correction is applied. Slopes identify differences between clocks and event timestamps; none is assumed the true clock.",
            "Independent confirmation on another run/power cycle is required before transferring a fitted correction.",
            "N-1 adjacent sample intervals define sample rates; host rates use the selected endpoint sequence difference, not the number of read batches.",
            "Command-to-END duration is not first-to-last sample duration. Legacy 4096-byte/250 ms reads introduce endpoint phase; two short host-span ratios cannot prove which oscillator is wrong.",
            "Formal OLS standard errors exclude autocorrelation, changing USB latency, oscillator drift and host/reference systematic errors.",
            "Absolute calibration requires an external reference and defensible latency/drift bounds. NTP blocked/unavailable is a missing measurement, not zero uncertainty."]}
    return summary, residual_rows


def render_markdown(report: dict) -> str:
    rates = report["rates"]
    lines = ["# USB recorder clock comparison", "", f"Capture: `{report['capture']}`", "",
             "**Diagnostic comparison; no timebase correction applied or absolute accuracy certified.**", "",
             ("**INTEGRITY FAILED: fits below describe only decoded observations and are not a completed-run calibration.**"
              if report["integrity_issues"] else "Acquisition integrity passed; clock and height accuracy remain separate questions."), "",
             f"Raw decoded samples: {report['analysis_scope']['raw_decoded_sample_count']:,}. Analysis selection: `{report['analysis_scope']['selection']}`; {rates['sample_count']:,} samples / {rates['sample_intervals']:,} intervals.", "",
             "| Rate | Observed samples/s |", "|---|---:|",
             f"| Nominal setting | {rates['nominal_odr_hz']:.6f} |",
             f"| FIFO nominal 25 us ticks | {rates['samples_per_fifo_nominal_second']:.9f} |",
             f"| MCU nominal 1 us ticks | {rates['samples_per_mcu_nominal_second']:.9f} |"]
    for key in ("samples_per_host_monotonic_second", "samples_per_host_realtime_second"):
        if rates.get(key) is not None:
            lines.append(f"| {key} | {rates[key]:.9f} |")
    lines += ["", "These use N−1 sample intervals, or the selected host endpoint sequence difference.", "",
              "## Clock fits", "", "Slopes below are y seconds per nominal x second. Ppm is `(slope−1)×10^6`.", "",
              "| Comparison | Slope | Ppm from 1 | Residual lag 1 | Window slope range (ppm) |",
              "|---|---:|---:|---:|---:|"]
    for name, fit in report["fits"].items():
        ws = fit["window_slope_stats"]
        span = f"{(ws['min']-1)*1e6:.2f} to {(ws['max']-1)*1e6:.2f}" if ws["n"] else "not enough span"
        lines.append(f"| {name} | {fit['slope']:.12f} | {fit['ppm_from_slope_one']:.3f} | {fit['residual_lag1']:.4f} | {span} |")
    tick = report["fits"]["mcu_seconds_per_nominal_fifo_second"]["implied_tick_us_in_mcu_units"]
    lines += ["", f"Implied FIFO tick: **{tick:.9f} microseconds in MCU clock units**. This is a relative scale, not an applied calibration."]
    lines += ["", "## Uncertainty and gates", ""]
    for issue in report["integrity_issues"]:
        lines.append(f"- Integrity issue: {issue}")
    for clock, intervals in report["invalid_clock_intervals"].items():
        for interval in intervals:
            lines.append(f"- Invalid {clock} interval {interval['previous_index']}→{interval['current_index']}: {interval['previous_raw']}→{interval['current_raw']}, signed raw delta {interval['signed_raw_delta']}; {interval['reason']}. No repair applied.")
    for text in report["interpretation"]:
        lines.append(f"- {text}")
    lines += ["- Moving-block bootstrap intervals, two block lengths, local slope changes, residual distributions and conditional endpoint-latency envelopes are in the JSON.",
              "- Bootstrap describes within-run repeatability under its stationarity/block assumptions. It does not include external-reference or systematic timing error.",
              "- Observed residual spread and read durations are not hard bounds on queued serial latency.",
              "", "## FIFO and status", "", report["interval_diagnostics"]["fifo_interpretation"], "",
              f"FIFO wraps: {report['interval_diagnostics']['fifo_wraps']}; MCU wraps: {report['interval_diagnostics']['mcu_wraps']}; status frames: {report['status']['count']}.",
              "", report["status"]["interpretation"], ""]
    lines += ["## External reference", "", report["external_reference"]["interpretation"], ""]
    reference = report["external_reference"]
    if reference["available"]:
        lines += ["| NTP server | Monotonic anchor span (s) | Monotonic correction (ppm) | Conditional halfwidth (ppm) | Realtime correction (ppm) |",
                  "|---|---:|---:|---:|---:|"]
        for pair in reference["per_server"]:
            lines.append(f"| {pair['server']} | {pair['host_monotonic_anchor_span_s']:.3f} | {pair['host_monotonic_correction_ppm']:.3f} | {pair['conditional_monotonic_uncertainty_halfwidth_ppm']:.3f} | {pair['host_realtime_correction_ppm']:.3f} |")
        lines += ["", f"Server-to-monotonic point-estimate spread: {reference['server_monotonic_point_correction_spread_ppm']:.3f} ppm; scenario intervals overlap: {reference['server_monotonic_scenario_intervals_overlap']}.",
                  "", "Server midpoint differences versus monotonic midpoint differences avoid interpreting a host wall-clock adjustment as oscillator drift. These are network-reference scenarios, not certified ppm bounds.",
                  "", reference["transfer_assumption"], ""]
        conditioned = report["fits"].get("host_monotonic_seconds_per_nominal_fifo_second", {}).get("ntp_per_server_conditioned", [])
        if conditioned:
            lines += ["### FIFO tick versus named NTP references", "",
                      "Reference uncertainty only. These intervals exclude within-run drift, correlated fit variation, serial/service latency changes and transfer assumptions; they are not total accuracy bounds.", "",
                      "| NTP server | Implied FIFO tick (us) | Reference-only scenario interval (us) |",
                      "|---|---:|---:|"]
            for pair in conditioned:
                lo, hi = [25*v for v in pair["scenario_slope_interval"]]
                lines.append(f"| {pair['server']} | {25*pair['slope']:.9f} | {lo:.9f} to {hi:.9f} |")
            lines.append("")
    else:
        lines += [reference["reason"], ""]
    lines += ["## Acceptance status", ""]
    if report["host"].get("available"):
        lines.append(f"- Host realtime-minus-monotonic changed {report['host'].get('realtime_minus_monotonic_change_s', 0):.9f} s during the selected samples; nonuniform realtime flag: {report['host'].get('nonuniform_realtime_relative_to_monotonic')}.")
    for key, value in report["gates"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--window-seconds", type=float, default=20)
    parser.add_argument("--endpoint-latency-difference-ms", type=float,
                        help="Assumed bound on endpoint latency difference; never inferred from data")
    parser.add_argument("--ntp-before", type=Path, help="Before-capture ntp_probe.py probe.json")
    parser.add_argument("--ntp-after", type=Path, help="After-capture ntp_probe.py probe.json")
    parser.add_argument("--out", type=Path, help="Output directory, defaults to capture_dir/clock_analysis")
    args = parser.parse_args()
    report, residuals = analyze(args.capture_dir, args.window_seconds, args.endpoint_latency_difference_ms,
                               args.ntp_before, args.ntp_after)
    output = args.out or args.capture_dir/"clock_analysis"
    output.mkdir(parents=True, exist_ok=True)
    (output/"clock_analysis.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    (output/"CLOCK_ANALYSIS.md").write_text(render_markdown(report))
    columns = list(dict.fromkeys(k for row in residuals for k in row))
    with (output/"residuals.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader()
        writer.writerows(residuals)
    print(render_markdown(report))
    print(f"Detailed results: {output.resolve()}")
    return 0 if report["gates"]["binary_integrity"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
