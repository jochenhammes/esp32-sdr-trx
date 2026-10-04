#!/usr/bin/env python3
"""A/B test of the thermal drift correction (espdr-tx --thermal): SSB test tone and RTTY, in several lengths, without (A) and with (B) the correction.

A PlutoSDR (PLUTO_URI, default ip:169.254.10.209) records all the time and estimates the frequency of the strongest line of every 0.17 s buffer (for RTTY the
middle of the two tones). The chip's temperature is read before each transmission and in the pauses. TRANSMITS for 20 to 30 minutes at 2350 MHz.
usage: ab_series.py OUT.json [--lengths 15,45,120 | --runs usb:60:B,rtty:90:A:44:400] [--thermal-model FILE] [--ppm 5.4] [--power -6] [--wait-c 46] [--only usb|rtty] [--variants AB]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

from espdr import board, nb, txlink

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--lengths", default="15,45,120")
ap.add_argument("--thermal-model")
ap.add_argument("--ppm", default="5.4")
ap.add_argument("--power", default="-6")
ap.add_argument("--wait-c", type=float, default=46.0, help="wait (up to --wait-max s) until the chip is this cool before a transmission")
ap.add_argument("--wait-max", type=float, default=90.0)
ap.add_argument("--only", choices=("usb", "rtty"))
ap.add_argument("--variants", default="AB")
ap.add_argument("--freq", default="2350")
ap.add_argument("--runs", help="an own list instead of --lengths: mode:length:variant[:wait_c[:wait_max]],... (usb or rtty, A or B)")
args = ap.parse_args()

URI = os.environ.get("PLUTO_URI", "ip:169.254.10.209")
FS, N = 1_536_000, 1 << 18
CENTER = float(args.freq) * 1e6 - 100e3
NFFT = 1 << 19
BIN = FS / NFFT
samples, temps, events = [], [], []
stop = threading.Event()
pluto_temp = [None]


def capture():
    import iio
    ctx = iio.Context(URI)
    phy = ctx.find_device("ad9361-phy")
    rx = ctx.find_device("cf-ad9361-lpc")
    ch = phy.find_channel("voltage0", False)
    ch.attrs["sampling_frequency"].value = str(FS)
    ch.attrs["rf_bandwidth"].value = "1000000"
    ch.attrs["gain_control_mode"].value = "manual"
    ch.attrs["hardwaregain"].value = "10"
    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(int(CENTER))
    try:
        phy.find_channel("voltage0", True).attrs["hardwaregain"].value = "-89.75"
    except Exception:  # noqa: BLE001
        pass
    rx.find_channel("voltage0").enabled = True
    rx.find_channel("voltage1").enabled = True
    time.sleep(0.3)
    buf = iio.Buffer(rx, N, False)
    win = np.hanning(N)
    lo, hi = int(30e3 / BIN), int(170e3 / BIN)
    half = int(round(170.0 / BIN))
    n = 0
    while not stop.is_set():
        buf.refill()
        t = time.monotonic()
        raw = np.frombuffer(buf.read(), dtype="<i2").astype(np.float32)
        x = raw[0::2] + 1j * raw[1::2]
        spec = np.abs(np.fft.fft(x * win, NFFT))
        seg = spec[lo:hi]
        k = int(np.argmax(seg))
        a, b, c = np.log(seg[k - 1:k + 2] + 1e-9) if 0 < k < len(seg) - 1 else (0, 0, 0)
        kk = k + (0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0)
        f = (lo + kk) * BIN
        snr = 20 * np.log10(seg[k] / np.median(spec))
        mid = f
        if snr > 30:                                              # RTTY: the partner tone is one shift away on either side
            up = seg[min(k + half - 4, len(seg) - 1):min(k + half + 5, len(seg))]
            dn = seg[max(k - half - 4, 0):max(k - half + 5, 0)]
            if len(up) and (not len(dn) or up.max() >= dn.max()):
                j = int(np.argmax(up)) + k + half - 4
            elif len(dn):
                j = int(np.argmax(dn)) + k - half - 4
            else:
                j = k
            if seg[j] > 0.3 * seg[k]:
                mid = (f + (lo + j) * BIN) / 2
        samples.append((t, float(f), float(snr), float(mid)))
        n += 1
        if n % 20 == 0:
            try:
                pluto_temp[0] = float(phy.find_channel("temp0").attrs["input"].value) / 1000.0
            except Exception:  # noqa: BLE001
                pass


def read_temp():
    link = nb.open_link(board.find_native())
    try:
        c = txlink.Session(link).chip_temperature()
    finally:
        link.ser.close()
    temps.append((time.monotonic(), c, pluto_temp[0]))
    return c


def tx(mode, seconds, variant):
    base = ["espdr-tx", "-f", args.freq, "--power", args.power, "--ppm", args.ppm, "-q"]
    if mode == "usb":
        cmd = base + ["-m", "usb", "--test-tone", "1000", "--duration", str(seconds)]
    else:
        cmd = base + ["-m", "rtty", "--text", "RY" * max(1, int(round((seconds - 1.2) / 0.33)))]
    if variant == "B":
        cmd += ["--thermal", "sensor"] + (["--thermal-model", args.thermal_model] if args.thermal_model else [])
    start_temp = read_temp()
    t = time.monotonic()
    events.append(dict(t=t, kind="start", mode=mode, length=seconds, variant=variant, temp=start_temp, cmd=cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    events.append(dict(t=time.monotonic(), kind="end", mode=mode, length=seconds, variant=variant, code=r.returncode, err=r.stderr.strip()[-300:]))
    if r.returncode:
        print("  espdr-tx failed:", r.stderr.strip()[-300:], flush=True)


def cool(limit_s):
    t_end = time.monotonic() + limit_s
    time.sleep(0.5)
    while True:
        c = read_temp()
        if c <= args.wait_c or time.monotonic() > t_end:
            return c
        time.sleep(8.0)


def main():
    th = threading.Thread(target=capture)
    th.start()
    time.sleep(3)
    lengths = [int(x) for x in args.lengths.split(",")]
    modes = [args.only] if args.only else ["usb", "rtty"]
    order = []
    for i, L in enumerate(lengths):
        for m in modes:
            for v in (args.variants if i % 2 == 0 else args.variants[::-1]):
                order.append((m, L, v))
    if args.runs:
        order = []
        for item in args.runs.split(","):
            f = item.split(":")
            order.append((f[0], int(f[1]), f[2], float(f[3]) if len(f) > 3 else args.wait_c, float(f[4]) if len(f) > 4 else args.wait_max))
    else:
        order = [(m, L, v, args.wait_c, args.wait_max) for m, L, v in order]
    for i, (m, L, v, wc, wm) in enumerate(order):
        args.wait_c = wc
        c = cool(wm if i else 5)
        print(f"{time.strftime('%H:%M:%S')}  run {i + 1}/{len(order)}: {m} {L} s variant {v}, chip {c:.1f} C", flush=True)
        tx(m, L, v)
        time.sleep(0.7)
        json.dump(dict(samples=samples, temps=temps, events=events, args=vars(args)), open(args.out, "w"))       # a partial result survives an abort
    read_temp()
    stop.set()
    th.join()
    json.dump(dict(samples=samples, temps=temps, events=events, args=vars(args)), open(args.out, "w"))
    print("saved", args.out, len(samples), "estimates,", len(temps), "temperatures", flush=True)


if __name__ == "__main__":
    main()
