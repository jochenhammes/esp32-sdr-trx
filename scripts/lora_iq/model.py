"""L0b of PLAN-LORA-IQ.md: frame times from the real symbol count, and an offline model of the I/Q playback engine (quantisation, image, LO feedthrough,
crystal offset) in front of the numpy receiver: packet error rate against SNR, ideal against the engine."""
import argparse
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import lora_np as ln  # noqa: E402

ENGINE_PEAK = 200          # of +-511 (10-bit I/Q), measured in IQ-TX-PHASE-A
IMAGE_DB = 33.0            # raw image rejection of the transmit path, 62 dB with the correction
LO_FEEDTHROUGH_DB = -40.0  # relative to the signal, placeholder until measured per LO (IQ-TX-PHASE-A)
CFO_HZ = 12600.0           # the board's crystal error at 2350 MHz (+5.4 ppm)


def engine(iq, fs, image_db=IMAGE_DB, lo_db=LO_FEEDTHROUGH_DB, cfo_hz=CFO_HZ, bits=10):
    """What the playback engine puts on the air: the frame as 10-bit I/Q at peak 200, an image, the LO leak and a frequency offset."""
    peak = ENGINE_PEAK
    q = np.rint(iq.real * peak) + 1j * np.rint(iq.imag * peak)
    q = np.clip(q.real, -(2 ** (bits - 1)), 2 ** (bits - 1) - 1) + 1j * np.clip(q.imag, -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
    x = q / peak
    x = x + 10 ** (-image_db / 20) * np.conj(x) + 10 ** (lo_db / 20) * (0.7 + 0.7j)
    t = np.arange(len(x)) / fs
    return (x * np.exp(2j * np.pi * cfo_hz * t)).astype(np.complex64)


def per(sf, bw, os_, length, snr_db, n, use_engine, seed=3, cr=1, preamble=8):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    fs = bw * os_
    bad = 0
    for _ in range(n):
        payload = bytes(rng.randrange(32, 127) for _ in range(length))
        iq = ln.frame_iq(payload, sf, bw, os_, cr, preamble)
        sig = np.concatenate([np.zeros(rng.randrange(50, 4 * (1 << sf) * os_), np.complex64), engine(iq, fs) if use_engine else iq * np.exp(2j * np.pi * rng.random()),
                              np.zeros(6 * (1 << sf) * os_, np.complex64)])
        p_noise = 10 ** (-snr_db / 10) * (fs / bw)
        sig = sig + np.sqrt(p_noise / 2) * (nrng.standard_normal(len(sig)) + 1j * nrng.standard_normal(len(sig)))
        r = ln.demodulate(sig, fs, sf, bw, preamble=preamble)
        bad += not (r and r.get("crc_ok") and r.get("payload") == payload)
    return bad / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--times", action="store_true")
    ap.add_argument("--n", type=int, default=100)
    args = ap.parse_args()
    if args.times:
        print("frame time from the real symbol count (preamble 8, explicit header, CRC), us")
        print(f"{'payload':>8s} {'CR':>4s} {'SF':>3s} {'symbols':>8s} {'BW 4 MHz':>10s} {'BW 8 MHz':>10s} {'1.625 MHz':>10s}")
        for sf in (5, 6, 7):
            for length, cr in ((12, 1), (16, 1), (20, 1), (6, 4), (24, 1)):
                s = ln.symbols_per_frame(length, sf, cr)
                print(f"{length:>6d} B {'4/' + str(4 + cr):>4s} {sf:>3d} {s:>8.2f} {s * (1 << sf) / 4e6 * 1e6:>10.0f} {s * (1 << sf) / 8e6 * 1e6:>10.0f} {s * (1 << sf) / 1.625e6 * 1e6:>10.0f}")
        return
    for sf, bw, os_, length in ((5, 4e6, 10, 12), (5, 4e6, 10, 16), (5, 8e6, 10, 12), (6, 4e6, 10, 12)):
        print(f"SF{sf} BW {bw / 1e6:g} MHz x{os_} ({bw * os_ / 1e6:g} Msps), {length} B, {ln.symbols_per_frame(length, sf):.2f} symbols = {ln.symbols_per_frame(length, sf) * (1 << sf) / bw * 1e6:.0f} us")
        snrs = range(0 - 6 * (sf - 5), 13 - 6 * (sf - 5), 2)
        print("   SNR in the bandwidth [dB]:      " + "  ".join(f"{s:5.0f}" for s in snrs))
        for name, eng in (("ideal ", False), ("engine", True)):
            print(f"   packet error rate, {name}:    " + "  ".join(f"{per(sf, bw, os_, length, s, args.n, eng):5.2f}" for s in snrs), flush=True)


if __name__ == "__main__":
    main()
