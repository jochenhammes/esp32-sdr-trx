#!/usr/bin/env python3
"""RESEARCH ONLY: L3 of docs/research/PLAN-LORA-IQ.md, scheme P. `espdr-tx -m lora` sends narrow LoRa frames through the polar path (PLL word at 40 000 updates/s),
a PlutoSDR records and the numpy receiver decodes. TRANSMITS inside the 13 cm band, needs a transmit permission. Run with the repository's Python (esptool) and
PYTHONPATH=/usr/lib/python3/dist-packages (for iio):

    scripts/lora_iq/polar.py --freq 2350 --sf 8 --bw 62500 --count 6 --power -6
"""
import argparse
import os
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


def capture(center, fs, seconds, gain, out, started):
    import iio
    ctx = iio.Context(URI)
    phy = ctx.find_device("ad9361-phy")
    rx = ctx.find_device("cf-ad9361-lpc")
    ch = phy.find_channel("voltage0", False)
    ch.attrs["sampling_frequency"].value = str(int(fs))
    ch.attrs["rf_bandwidth"].value = str(int(min(fs * 0.9, 56e6)))
    ch.attrs["gain_control_mode"].value = "manual"
    ch.attrs["hardwaregain"].value = str(gain)
    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(int(center))
    try:
        phy.find_channel("voltage0", True).attrs["hardwaregain"].value = "-89.75"
    except Exception:  # noqa: BLE001
        pass
    rx.find_channel("voltage0").enabled = True
    rx.find_channel("voltage1").enabled = True
    time.sleep(0.3)
    n = 1 << 18
    buf = iio.Buffer(rx, n, False)
    chunks = []
    started.set()
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        buf.refill()
        chunks.append(np.frombuffer(buf.read(), dtype="<i2").copy())
    raw = np.concatenate(chunks).astype(np.float32)
    out.append(raw[0::2] + 1j * raw[1::2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", type=float, default=2350.0)
    ap.add_argument("--sf", type=int, default=8)
    ap.add_argument("--bw", type=float, default=62500.0)
    ap.add_argument("--cr", type=int, default=1)
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--interval", type=float, default=1.5)
    ap.add_argument("--power", default="-6")
    ap.add_argument("--ppm", default="5.4")
    ap.add_argument("--text", default="DA2JH LORA-P1")
    ap.add_argument("--fs", type=float, default=1e6)
    ap.add_argument("--pluto-gain", type=float, default=10.0)
    ap.add_argument("--offset", type=float, default=150e3, help="Pluto centre below the channel centre, Hz")
    ap.add_argument("--image", default=os.path.join(ROOT, "firmware", "build-tx", "iq-source.bin"))
    ap.add_argument("--thermal", default="off")
    ap.add_argument("--save")
    ap.add_argument("--reload", action="store_true", help="load the image first (the board keeps it until it is reset)")
    args = ap.parse_args()
    center = args.freq * 1e6 - args.offset
    seconds = 2.0 + args.count * (args.interval + 0.4) + 1.5 + (10.0 if args.reload else 0.0)
    out, started = [], threading.Event()
    th = threading.Thread(target=capture, args=(center, args.fs, seconds, args.pluto_gain, out, started))
    th.start()
    started.wait()
    time.sleep(1.0)
    cmd = [sys.executable, "-m", "espdr.cli_tx", "-f", str(args.freq), "-m", "lora", "--sf", str(args.sf), "--lora-bw", str(args.bw), "--lora-cr", str(args.cr),
           "--text", args.text, "--ppm", args.ppm, "--power", args.power, "--repeat-count", str(args.count), "--repeat-interval", str(args.interval),
           "--thermal", args.thermal, "--accept-licence", "-q", "--image", args.image] + (["--reload"] if args.reload else [])
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    print("$", " ".join(cmd[2:]), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print(r.stderr.strip()[-600:], f"\n(exit code {r.returncode})", flush=True)
    th.join()
    x = out[0]
    if args.save:
        np.savez_compressed(args.save, x=x.astype(np.complex64), fs=args.fs, shift=args.offset)
    analyse(x, args)


def analyse(x, args):
    t = np.arange(len(x)) / args.fs
    y = ((x - np.mean(x)) * np.exp(-2j * np.pi * args.offset * t)).astype(np.complex64)
    c = lp.coarse_centre(y, args.fs, args.bw)                          # the carrier may sit several kHz from the plan (thermal drift of the crystal)
    t = np.arange(len(y)) / args.fs
    y = (y * np.exp(-2j * np.pi * c * t)).astype(np.complex64)
    print(f"coarse centre {c / 1e3:+.1f} kHz from the expected channel centre")
    frames = lp.demodulate_all(y, args.fs, args.sf, args.bw)
    payload = args.text.encode("latin-1", "replace")
    good = [f for f in frames if f.get("crc_ok") and f.get("payload") == payload]
    print(f"decoded {len(good)} frames with the right bytes and CRC of {args.count} sent ({len(frames)} candidates found)")
    for f in frames[:args.count + 2]:
        print(f"  start {f['start'] / args.fs:6.2f} s  crc_ok {f.get('crc_ok')}  payload {f.get('payload')}  cfo {f['cfo_hz'] / 1e3:+.2f} kHz  preamble SNR est {f['snr_db']:.1f} dB")
    return good


if __name__ == "__main__":
    main()
