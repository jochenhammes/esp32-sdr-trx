#!/usr/bin/env python3
"""Needs the research firmware with IQ_OP_TEMP (make -C firmware TX=1 IQTEST=1 NARROWBAND=1, loaded into RAM) and a PlutoSDR (PLUTO_URI); TRANSMITS for about 15 minutes.
usage: drift_series.py OUT.json    then    drift_analyse.py OUT.json    and    drift_lag.py OUT.json

Frequency drift of the ESP32-S3 transmitter against the chip temperature.

The Pluto records all the time and estimates the carrier frequency of every 0.17 s buffer. The ESP sends plain carriers (espdr-tx -m fm --deviation 0).
Phases: idle (temperature only), a long transmission (heating), then short bursts with temperature reads in between (cooling), twice.
"""
import json
import subprocess
import sys
import threading
import time

import numpy as np

import os
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from espdr import board, nb  # noqa: E402

URI = os.environ.get("PLUTO_URI", "ip:plutoplus.local")
FS = 1_536_000
N = 1 << 18
DIAL_HZ = 2350e6
CENTER = DIAL_HZ - 100e3
OFFS = {0: -2, 1: -1, 2: 0, 3: 1, 4: 2}
TX = [shutil.which("espdr-tx") or "espdr-tx", "-f", "2350", "-m", "fm", "--test-tone", "1000", "--deviation", "0",
      "--power", "-6", "--ppm", "5.4", "-q"]
OUT = sys.argv[1]
samples, temps, events = [], [], []
stop = threading.Event()


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
    nfft = 1 << 19
    lo, hi = int(30e3 / FS * nfft), int(170e3 / FS * nfft)
    while not stop.is_set():
        buf.refill()
        t = time.monotonic()
        raw = np.frombuffer(buf.read(), dtype="<i2").astype(np.float32)
        x = raw[0::2] + 1j * raw[1::2]
        spec = np.abs(np.fft.fft(x * win, nfft))
        seg = spec[lo:hi]
        k = int(np.argmax(seg))
        a, b, c = np.log(seg[max(k - 1, 0):k + 2] + 1e-9) if 0 < k < len(seg) - 1 else (0, 0, 0)
        kk = k + (0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0)
        f = (lo + kk) * FS / nfft
        snr = 20 * np.log10(seg[k] / np.median(spec))
        samples.append((t, float(f), float(snr)))


def degrees(v):
    idx, s = v >> 24, v & 0xFFFFFF
    return 0.4386 * (s / 16.0) - 27.88 * OFFS[idx] - 20.52


def read_temp():
    link = nb.open_link(board.find_native())
    try:
        st, v = link.command(82, 2)
        c = degrees(v)
        if c > 70:                                           # out of the middle range
            c = degrees(link.command(82, 1)[1])
        temps.append((time.monotonic(), c))
        return c
    finally:
        link.ser.close()


def tx(seconds, label):
    t = time.monotonic()
    events.append((t, label, seconds))
    subprocess.run(TX + ["--duration", str(seconds)], capture_output=True, text=True)
    events.append((time.monotonic(), label + " end", 0))


def idle(seconds, every=5.0):
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        read_temp()
        time.sleep(every)


def bursts(seconds, tick=10.0, burst_every=3, burst_s=5):
    t_end = time.monotonic() + seconds
    i = 0
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        read_temp()
        if i % burst_every == 0:
            tx(burst_s, "burst")
            time.sleep(0.5)
            read_temp()
        i += 1
        time.sleep(max(0.0, tick - (time.monotonic() - t0)))


def main():
    th = threading.Thread(target=capture)
    th.start()
    time.sleep(3)
    print("phase 0: idle", flush=True)
    events.append((time.monotonic(), "phase idle", 0))
    idle(40)
    print("phase 1: long transmission", flush=True)
    tx(150, "long1")
    time.sleep(0.5)
    read_temp()
    print("phase 2: cooling with bursts", flush=True)
    events.append((time.monotonic(), "phase cooling1", 0))
    bursts(300)
    print("phase 3: second long transmission", flush=True)
    tx(100, "long2")
    time.sleep(0.5)
    read_temp()
    print("phase 4: cooling with bursts", flush=True)
    events.append((time.monotonic(), "phase cooling2", 0))
    bursts(240)
    stop.set()
    th.join()
    json.dump(dict(samples=samples, temps=temps, events=events), open(OUT, "w"))
    print("saved", OUT, len(samples), "carrier estimates,", len(temps), "temperatures")


if __name__ == "__main__":
    main()
