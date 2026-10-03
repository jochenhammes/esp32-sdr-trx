#!/usr/bin/env python3
"""Checks src/dsp.c on the host against an independent whole-stream model.

1. Bit-exactness: the unit-by-unit firmware code must equal a plain
   stream-wide CIC + FIR in exact integer arithmetic, for any unit lengths.
2. Frequency response: a wanted tone, and an interferer that aliases into the
   passband, must come out at the amplitudes the filter design predicts.
"""
import os, subprocess, sys, tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import design_taps as dt

FS0 = 16e6


def build():
    exe = os.path.join(tempfile.gettempdir(), "dsp_selftest")
    subprocess.check_call(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", exe,
                           os.path.join(HERE, "dsp_selftest.c"), os.path.join(HERE, "..", "src", "dsp.c")])
    return exe


def make_input(n, tones, noise, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS0
    z = np.zeros(n, complex)
    for f, a in tones:
        z += a * np.exp(2j * np.pi * f * t)
    z += noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    i = np.clip(np.round(z.real), -512, 511).astype(np.int32)
    q = np.clip(np.round(z.imag), -512, 511).astype(np.int32)
    words = (i & 0x3FF) | ((q & 0x3FF) << 10)
    return words.astype(np.uint32), i, q


def reference(i, q, r2, fmt, shift, start0=0):
    """Stream-wide model of the chain in exact integer arithmetic."""
    out = []
    taps = dt.design(r2)[0].astype(np.int64)
    for x in (i, q):
        a = x.astype(np.uint32)
        for _ in range(4):
            a = np.cumsum(a, dtype=np.uint32)
        y = a[15::16].astype(np.uint32)            # integrator output at block ends
        for _ in range(4):                         # combs, zero history
            y = (y - np.concatenate(([0], y[:-1])).astype(np.uint32)).astype(np.uint32)
        s1 = (y.view(np.int32) >> 11).astype(np.int64)
        padded = np.concatenate((np.zeros(len(taps) - 1, np.int64), s1))
        # output k = FIR over s1[r2*k - (T-1) .. r2*k]
        idx = np.arange((-(start0 >> 4)) % r2, len(s1), r2)       # first block with (block number) % r2 == 0
        win = np.lib.stride_tricks.sliding_window_view(padded, len(taps))[idx]
        acc = win @ taps
        v = (acc + (1 << 14)) >> 15
        if shift:
            v = (v + (1 << (shift - 1))) >> shift
        out.append(v)
    lim = (-32768, 32767) if fmt == 0 else (-128, 127)
    return np.clip(out[0], *lim), np.clip(out[1], *lim)


def run(exe, words, r2, fmt, shift, seed, start0=0):
    with tempfile.TemporaryDirectory() as d:
        fin, fout = os.path.join(d, "in"), os.path.join(d, "out")
        words.tofile(fin)
        subprocess.check_call([exe, fin, fout, str(r2), str(fmt), str(shift), str(seed), str(start0)])
        raw = np.fromfile(fout, dtype=np.int16 if fmt == 0 else np.int8)
    return raw[0::2].astype(np.int64), raw[1::2].astype(np.int64)


def main():
    exe = build()
    ok = True
    n = 16288 * 40
    words, i, q = make_input(n, [(100e3, 200), (-60e3, 90), (1.1e6, 150)], noise=4)
    wrap = 4294967232                                  # DSP_START_WRAP in src/dsp.h
    for r2, fmt, shift, start0 in [(2, 0, 0, 0), (4, 0, 0, 0), (2, 1, 7, 0), (4, 1, 6, 0), (3, 0, 0, 0), (3, 1, 6, 0),
                                   (3, 0, 0, 16 * 7), (3, 1, 6, 16 * 1001), (3, 0, 0, wrap - 16 * 6250),
                                   (4, 0, 0, wrap - 16 * 6251), (2, 0, 0, wrap - 16 * 6251)]:
        for seed in (1, 77):
            fi, fq = run(exe, words, r2, fmt, shift, seed, start0)
            ri, rq = reference(i, q, r2, fmt, shift, start0)
            m = len(fi)
            same = np.array_equal(fi, ri[:m]) and np.array_equal(fq, rq[:m])
            print(f"bit-exact r2={r2} fmt={fmt} shift={shift} start={start0} seed={seed}: {'OK' if same else 'MISMATCH'} ({m} samples)")
            ok &= same

    # frequency response of the whole chain: a flat wanted tone, and an interferer
    # that folds into the passband at an amplitude the filter design predicts
    from scipy.signal import freqz
    for r2, fa, aa in ((2, 800e3, 200), (3, 190e3, 250), (4, 160e3, 250)):
        words, i, q = make_input(16288 * 60, [(80e3, 150), (fa, aa)], noise=0.5, seed=3)
        fi, fq = run(exe, words, r2, 0, 0, 5)
        z = (fi + 1j * fq)[200:]
        fs = FS0 / 16 / r2
        win = np.hanning(len(z))
        spec = np.fft.fftshift(np.fft.fft(z * win)) / win.sum()
        freq = np.fft.fftshift(np.fft.fftfreq(len(z), 1 / fs))
        amp_w = abs(spec[np.argmin(abs(freq - 80e3)) - 2:np.argmin(abs(freq - 80e3)) + 3]).max()
        falias = (fa + fs / 2) % fs - fs / 2
        kk = np.argmin(abs(freq - falias))
        amp_a = abs(spec[kk - 2:kk + 3]).max()
        h = dt.design(r2)[0] / 32768.0
        f1 = (fa + 5e5) % 1e6 - 5e5                      # frequency as seen at the 1 Msps stage
        _, H = freqz(h, worN=[f1], fs=1e6)
        pred = aa * 32 * dt.cic_mag(np.array([fa]))[0] * abs(H[0])
        gw = 20 * np.log10(amp_w / (150 * 32))
        ga, gp = 20 * np.log10(amp_a / (aa * 32)), 20 * np.log10(pred / (aa * 32))
        print(f"response r2={r2}: wanted tone {gw:+.2f} dB; interferer {fa/1e3:.0f} kHz folds to "
              f"{falias/1e3:.0f} kHz: measured {ga:.1f} dB, predicted {gp:.1f} dB")
        ok &= abs(gw) < 0.2 and abs(ga - gp) < 2.0
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
