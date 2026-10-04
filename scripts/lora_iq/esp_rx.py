#!/usr/bin/env python3
"""RESEARCH ONLY: can the ESP32's own receiver (espdr-rx, 250 ksps) receive narrow LoRa? The PlutoSDR TRANSMITS the frames (needs a transmit permission for the Pluto) at a very low
level on 2350 MHz, the ESP32 receives through its rtl_tcp bridge, the numpy receiver decodes (and gr-lora_sdr's, with scripts/lora_iq/grcheck.py on the saved capture).

    PYTHONPATH=/usr/lib/python3/dist-packages ~/.../.venv/bin/python scripts/lora_iq/esp_rx.py --sf 7 --bw 62500 --atten -55
"""
import argparse
import os
import socket
import struct
import subprocess
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from espdr import lora_phy as lp  # noqa: E402

URI = "ip:169.254.10.209"


def pluto_tx(iq, fs, lo_hz, atten_db):
    import iio
    ctx = iio.Context(URI)
    phy = ctx.find_device("ad9361-phy")
    tx = ctx.find_device("cf-ad9361-dds-core-lpc")
    out = phy.find_channel("voltage0", True)
    out.attrs["sampling_frequency"].value = str(int(fs))
    out.attrs["rf_bandwidth"].value = str(int(min(fs * 0.9, 56e6)))
    out.attrs["hardwaregain"].value = str(atten_db)
    phy.find_channel("altvoltage1", True).attrs["frequency"].value = str(int(lo_hz))
    phy.find_channel("altvoltage1", True).attrs["powerdown"].value = "0"
    for ch in ("voltage0", "voltage1"):
        tx.find_channel(ch, True).enabled = True
    raw = np.empty(2 * len(iq), np.int16)
    raw[0::2] = np.rint(iq.real * 8192)
    raw[1::2] = np.rint(iq.imag * 8192)
    buf = iio.Buffer(tx, len(iq), True)
    buf.write(bytearray(raw.tobytes()))
    buf.push()
    return ctx, phy, buf


def pluto_rx(seconds, fs, centre, gain, out, started):
    import iio
    ctx = iio.Context(URI)
    phy = ctx.find_device("ad9361-phy")
    rx = ctx.find_device("cf-ad9361-lpc")
    ch = phy.find_channel("voltage0", False)
    ch.attrs["gain_control_mode"].value = "manual"
    ch.attrs["hardwaregain"].value = str(gain)
    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(int(centre))
    rx.find_channel("voltage0").enabled = True
    rx.find_channel("voltage1").enabled = True
    time.sleep(0.2)
    buf = iio.Buffer(rx, 1 << 18, False)
    started.set()
    chunks = []
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        buf.refill()
        chunks.append(np.frombuffer(buf.read(), dtype="<i2").copy())
    raw = np.concatenate(chunks).astype(np.float32)
    out.append(raw[0::2] + 1j * raw[1::2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sf", type=int, default=7)
    ap.add_argument("--bw", type=float, default=62500.0)
    ap.add_argument("--cr", type=int, default=1)
    ap.add_argument("--text", default="DA2JH LORA-E1")
    ap.add_argument("--freq", type=float, default=2350.0)
    ap.add_argument("--atten", type=float, default=-55.0)
    ap.add_argument("--gap", type=float, default=0.3, help="seconds of silence after each frame")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--gain", type=float, default=30.0, help="rtl_tcp gain of the ESP32 receiver, dB")
    ap.add_argument("--save")
    ap.add_argument("--pluto-rx", type=float, default=None, metavar="GAIN", help="record with the Pluto as well (its receiver gain in dB) and decode that too, for a comparison")
    args = ap.parse_args()
    fs_tx = 2.5e6
    n_per = int(round(fs_tx / args.bw))
    frame = lp.frame_iq(args.text.encode(), args.sf, args.bw, 40, args.cr)             # 2.5 Msps for 62.5 kHz
    period = int(round((len(frame) / fs_tx + args.gap) * 2)) / 2                      # whole half seconds: the shift below is continuous at the wrap
    n = int(period * fs_tx)
    buf_iq = np.zeros(n, np.complex64)
    buf_iq[:len(frame)] = frame
    shift = 60e3
    buf_iq *= np.exp(2j * np.pi * shift * np.arange(n) / fs_tx).astype(np.complex64) * 0.7
    print(f"frame {len(frame) / fs_tx * 1e3:.0f} ms, repeated every {period:.1f} s; Pluto TX at {args.freq} MHz, attenuation {args.atten} dB", flush=True)
    rx = subprocess.Popen([sys.executable, "-m", "espdr.cli_rx", "--listen", "127.0.0.1:1234", "--ppm", "0"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src") + os.pathsep + os.environ.get("PYTHONPATH", "")))
    try:
        s = None
        for _ in range(60):
            try:
                s = socket.create_connection(("127.0.0.1", 1234), timeout=2)
                break
            except OSError:
                time.sleep(1.0)
        if s is None:
            raise SystemExit("the espdr-rx server did not come up")
        s.settimeout(5)
        s.recv(12)
        for cmd, val in ((0x02, 250000), (0x01, int(args.freq * 1e6)), (0x03, 1), (0x04, int(args.gain * 10))):
            s.sendall(struct.pack(">BI", cmd, val))
        time.sleep(0.5)
        ctx, phy, buf = pluto_tx(buf_iq, fs_tx, args.freq * 1e6, args.atten)
        prx, pstarted = [], threading.Event()
        if args.pluto_rx is not None:
            th = threading.Thread(target=pluto_rx, args=(args.seconds, fs_tx, args.freq * 1e6, args.pluto_rx, prx, pstarted))
            th.start()
            pstarted.wait()
        time.sleep(0.5)
        t_end = time.monotonic() + args.seconds
        data = bytearray()
        while time.monotonic() < t_end:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            data += chunk
        out = phy.find_channel("voltage0", True)
        out.attrs["hardwaregain"].value = "-89.75"                 # off
        del buf
    finally:
        rx.terminate()
        try:
            rx.wait(timeout=5)
        except Exception:  # noqa: BLE001
            rx.kill()
    if args.pluto_rx is not None:
        th.join()
        px = prx[0].astype(np.complex64)
        pt = np.arange(len(px)) / fs_tx
        py = ((px - np.mean(px)) * np.exp(-2j * np.pi * 0.0 * pt)).astype(np.complex64)
        pfr, pc, pgood = lp.search_centre(py, fs_tx, args.bw, args.sf, args.text.encode(), span=100e3)
        print(f"Pluto receiver  (gain {args.pluto_rx:g} dB, peak {np.abs(px).max() / 2048:.2f} of full scale): signal at {pc / 1e3:+.1f} kHz; numpy receiver: {pgood} frames with the right bytes and CRC of {len(pfr)} found")
    raw = np.frombuffer(bytes(data[: len(data) // 2 * 2]), np.uint8).astype(np.float32) - 127.5
    x = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)
    print(f"received {len(x)} samples = {len(x) / 250e3:.1f} s from the ESP32 receiver")
    if args.save:
        np.savez_compressed(args.save, x=x, fs=250e3, shift=60e3)
    t = np.arange(len(x)) / 250e3
    y = ((x - np.mean(x)) * np.exp(-2j * np.pi * 60e3 * t)).astype(np.complex64)
    frames, c, good = lp.search_centre(y, 250e3, args.bw, args.sf, args.text.encode(), span=60e3)
    rms = float(np.sqrt(np.mean(np.abs(x - np.mean(x)) ** 2)))
    print(f"ESP32 receiver (gain {args.gain:g} dB, rms {rms:.1f} counts, peak {np.abs(x).max():.0f}): signal at {60 + c / 1e3:.1f} kHz; numpy receiver: {good} frames with the right bytes and CRC "
          f"of {len(frames)} found (about {int(args.seconds / period)} sent), Pluto TX attenuation {args.atten:g} dB, SF{args.sf}, BW {args.bw / 1e3:g} kHz")


if __name__ == "__main__":
    main()
