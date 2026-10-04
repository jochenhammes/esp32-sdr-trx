#!/usr/bin/env python3
"""Measure `espdr-tx -m rtty` on the air with a PlutoSDR.

THIS TRANSMITS (record and run call espdr-tx). Use it only with a transmit permission that covers the frequency, cabled or with the Pluto
a few tens of centimetres away, at a low `--power`. Needs numpy, scipy and, for recording, libiio's Python binding (`import iio`; on Debian
and Ubuntu: apt install python3-libiio, and run with that python or add /usr/lib/python3/dist-packages to PYTHONPATH).

  rtty_pluto_check.py record  DIAL_MHZ TEXT --out FILE.npz [options] [-- espdr-tx options]
  rtty_pluto_check.py analyse FILE.npz
  rtty_pluto_check.py run     DIAL_MHZ TEXT [options] [-- espdr-tx options]      (record, then analyse)
  rtty_pluto_check.py plutotx FILE.npz                                           (needs GNU Radio and a pluto-tx checkout)
  rtty_pluto_check.py chars   LOGFILE                                            (the characters in the log of pluto-cli rx --digimode rtty)

Example, calibrating --ppm first and then measuring:
  rtty_pluto_check.py run 2350 "RYRY DE <call> 73" -- --power -6 --ppm 5.4
The Pluto is tuned 150 kHz below the expected low tone, so that the tones stay away from its DC spike; the recording is 1.536 Msps.

analyse prints, for the recording: where the low tone really was against where the tool said it would be (this includes the crystal of the
Pluto), the two tone frequencies and their distance (measured over the long runs of each tone in the bit pattern that was sent), the
frequency noise inside the tones, the text that our decoder (espdr.rtty.decode_frequency) reads from it, and the spectrum around the
signal: how much of the power is within a few hundred Hz of the strongest line and the level of the pedestal beside it in dBc/Hz.

plutotx runs the RTTY receive chain of pluto-advanced-rx (freq_xlating band filter, quadrature demod, 150 Hz low-pass, slicer, UART deframer)
on the recording, with its filter centre moved off by a few offsets, to show how exactly the tones must be tuned for that decoder.
"""
import argparse
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from espdr import rtty  # noqa: E402

FS = 1_536_000
OFFSET_HZ = 150e3                 # the Pluto is tuned this far below the expected low tone
DEMOD_RATE = 12000.0              # rate of the frequency series the analysis works on


# --- recording ---------------------------------------------------------------------------------------------------------------------------

def capture(uri, center_hz, seconds, gain_db, out):
    import iio
    ctx = iio.Context(uri)
    phy = ctx.find_device("ad9361-phy")
    rx = ctx.find_device("cf-ad9361-lpc")
    ch = phy.find_channel("voltage0", False)
    ch.attrs["sampling_frequency"].value = str(FS)
    ch.attrs["rf_bandwidth"].value = "1000000"
    ch.attrs["gain_control_mode"].value = "manual"
    ch.attrs["hardwaregain"].value = str(gain_db)
    phy.find_channel("altvoltage0", True).attrs["frequency"].value = str(int(center_hz))
    try:
        phy.find_channel("voltage0", True).attrs["hardwaregain"].value = "-89.75"      # the Pluto's own transmitter stays off
    except Exception:                                                                    # noqa: BLE001
        pass
    rx.find_channel("voltage0").enabled = True
    rx.find_channel("voltage1").enabled = True
    time.sleep(0.3)
    buf = iio.Buffer(rx, 1 << 18, False)       # ONE buffer for the whole recording: a new buffer per chunk loses samples between the chunks
    chunks = []
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        buf.refill()
        chunks.append(np.frombuffer(buf.read(), dtype="<i2").copy())
    raw = np.concatenate(chunks)
    out.append((raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)).astype(np.complex64))


def record(a, tx_args):
    center = a.dial * 1e6 + a.mark_hz - OFFSET_HZ
    out = []
    th = threading.Thread(target=capture, args=(a.uri, center, a.seconds, a.pluto_gain, out))
    th.start()
    time.sleep(a.lead)
    cmd = [sys.executable, "-m", "espdr.cli_tx", "-f", f"{a.dial:.6f}", "-m", "rtty", "--text", a.text, "--mark-hz", str(a.mark_hz),
           "--shift-hz", str(a.shift_hz), "--baud-rate", str(a.baud_rate), "-q"] + (["--reverse"] if a.reverse else []) + tx_args
    print("$", " ".join(cmd), flush=True)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print((r.stderr.strip().splitlines() or [""])[0], f"\n(exit code {r.returncode})", flush=True)
    th.join()
    if r.returncode:
        print(r.stderr, file=sys.stderr)
    if a.out:
        np.savez(a.out, iq=out[0], fs=FS, center_hz=center, dial_hz=a.dial * 1e6, text=a.text, baud=a.baud_rate, shift=a.shift_hz,
                 mark=a.mark_hz, reverse=a.reverse)
        print("saved", a.out)
    return dict(iq=out[0], fs=FS, center_hz=center, dial_hz=a.dial * 1e6, text=a.text, baud=a.baud_rate, shift=a.shift_hz, mark=a.mark_hz,
                reverse=a.reverse)


# --- analysis ----------------------------------------------------------------------------------------------------------------------------

def _decimation(fs, target=DEMOD_RATE):
    q = max(1, int(round(fs / target)))
    stages = []
    while q > 1:
        f = next(k for k in (8, 7, 5, 4, 3, 2, q) if q % k == 0)
        stages.append(f)
        q //= f
    return stages


def active_span(iq, fs):
    """Index range of the transmission (10 ms envelope above half of the 99th percentile), trimmed by 20 ms at both ends."""
    n = int(fs * 0.01)
    env = np.convolve(np.abs(iq), np.ones(n) / n, mode="same")
    on = np.flatnonzero(env > 0.5 * np.percentile(env, 99))
    return on[0] + int(fs * 0.02), on[-1] - int(fs * 0.02)


def peak_hz(seg, fs):
    """Frequency of the strongest line of a segment in Hz from the centre (zero-padded FFT, parabolic interpolation)."""
    n = 1 << int(np.ceil(np.log2(len(seg)))) + 1
    spec = np.abs(np.fft.fft(seg * np.hanning(len(seg)), n))
    k = int(np.argmax(spec))
    a, b, c = np.log(spec[k - 1:k + 2] + 1e-30)
    k = k + 0.5 * (a - c) / (a - 2 * b + c)
    return (k if k < n / 2 else k - n) * fs / n


def frequency_series(iq, fs, f_ref):
    """Instantaneous frequency in Hz from the centre of the recording, at about DEMOD_RATE: mix the line at f_ref to zero, low-pass and
    decimate, take the phase difference."""
    from scipy import signal
    t = np.arange(len(iq)) / fs
    y = iq * np.exp(-2j * np.pi * f_ref * t).astype(np.complex64)
    rate = fs
    for q in _decimation(fs):
        y = signal.decimate(y, q, ftype="fir", zero_phase=True)
        rate /= q
    return np.angle(y[1:] * np.conj(y[:-1])) * rate / (2 * np.pi) + f_ref, rate


def tone_levels(inst, rate, text, baud, reverse):
    """Align the bit pattern that was sent with the recording and measure the two tones over the long runs (at least 2.5 bit periods, the
    middle 60 percent of each). Returns (lag in samples, median of the low tone, median of the high tone, rms spread inside the runs)."""
    from espdr import rtty as rt
    frames = rt.message_frames(text, baud)
    spb = rate / baud
    expect = np.concatenate(list(rt.OffsetStream(frames, rate, baud, 1.0, reverse, 0.0).blocks()))        # 0: low tone, 1: high tone
    lo, hi = np.percentile(inst, [5, 95])
    measured = (inst > (lo + hi) / 2).astype(float) - 0.5
    n = 1 << int(np.ceil(np.log2(len(measured) + len(expect))))
    x = np.fft.irfft(np.fft.rfft(measured, n) * np.conj(np.fft.rfft(expect - 0.5, n)), n)
    lag = int(np.argmax(x))
    lag = lag - n if lag > n // 2 else lag
    change = np.flatnonzero(np.diff(expect) != 0) + 1                                  # runs of the same tone in the bit pattern
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(expect)]])
    low, high, spread = [], [], []
    for s0, e0 in zip(starts, ends):
        if e0 - s0 < 2.5 * spb:
            continue
        margin = 0.2 * (e0 - s0)
        s1, e1 = int(s0 + margin + lag), int(e0 - margin + lag)
        if s1 < 0 or e1 > len(inst):
            continue
        w = inst[s1:e1]
        (high if expect[s0] > 0.5 else low).append(np.median(w))
        spread.append(np.std(w))
    if not low or not high:
        return lag, None, None, None
    return lag, float(np.median(low)), float(np.median(high)), float(np.sqrt(np.mean(np.square(spread))))


def spectrum_report(iq, fs, a, b):
    """The spectrum of the transmission against the noise before or after it; returns the printed lines."""
    from scipy import signal
    n = 1 << 15
    act = iq[a:b]
    pre = max(0, a - int(0.3 * fs))
    noise = iq[:pre] if pre > int(0.3 * fs) else iq[b + int(0.3 * fs):]
    if len(noise) < 4 * n:
        return ["  (no quiet part in the recording to measure the noise: spectrum not reported)"]

    def psd(x):
        f, p = signal.welch(x, fs, nperseg=n, return_onesided=False, window="hann", detrend=False)
        o = np.argsort(f)
        return f[o], p[o]

    f, p = psd(act)
    _, pn = psd(noise)
    bw = fs / n
    f0 = f[int(np.argmax(p))]
    d = f - f0
    nf = np.median(pn)
    lines = [f"  strongest line {p.max() / nf:.0f}x ({10 * np.log10(p.max() / nf):.1f} dB) above the noise in a {bw:.0f} Hz bin"]
    sig = np.clip(p - nf, 0, None)
    order = np.argsort(np.abs(d))
    cum = np.cumsum(sig[order]) / sig.sum()
    for frac in (0.9, 0.99):
        lines.append(f"  {frac * 100:g} percent of the power within +-{np.abs(d[order])[np.searchsorted(cum, frac)]:.0f} Hz of the strongest line")
    tone = p[np.abs(d) < 400].sum() - nf * (np.abs(d) < 400).sum()
    lines.append("  pedestal beside the tones, median bin without the noise, in dBc/Hz against the power of the tones (+-400 Hz):")
    for lo_, hi_ in ((1000, 2000), (2000, 5000), (5000, 10000), (10000, 20000), (20000, 50000), (50000, 100000)):
        m = (np.abs(d) >= lo_) & (np.abs(d) < hi_)
        lvl = max(np.median(p[m]) - np.median(pn[m]), 1e-30)
        lines.append(f"    {lo_ / 1e3:5.1f} to {hi_ / 1e3:5.1f} kHz: {10 * np.log10(lvl / bw / tone):7.1f}")
    m = (np.abs(d) >= 1000) & (np.abs(d) < 100000)
    lines.append(f"  all of 1 to 100 kHz from the line, both sides: {10 * np.log10(np.clip(p - nf, 0, None)[m].sum() / tone):.1f} dB against the tones")
    return lines


def analyse_iq(rec, out=print):
    iq, fs = rec["iq"], float(rec["fs"])
    text, baud, shift, mark, reverse = str(rec["text"]), float(rec["baud"]), float(rec["shift"]), float(rec["mark"]), bool(rec["reverse"])
    center, dial = float(rec["center_hz"]), float(rec["dial_hz"])
    a, b = active_span(iq, fs)
    clip = float(np.max(np.abs(np.concatenate([iq.real, iq.imag])))) / 2048
    out(f"recording {len(iq) / fs:.1f} s at {fs / 1e6:.3f} Msps, peak {clip:.2f} of full scale; on the air {(b - a) / fs + 0.04:.2f} s "
        f"(the text takes {rtty.estimate_duration(text, baud):.2f} s)")
    f_mark = peak_hz(iq[a + int(0.2 * fs):a + int(0.8 * fs)], fs)          # the idle mark at the start
    inst, rate = frequency_series(iq[a:b], fs, f_mark)
    lag, low, high, spread = tone_levels(inst, rate, text, baud, reverse)
    expected_low = dial + mark
    result = dict(decoded=rtty.decode_frequency(inst, rate, baud, reverse), text=text, clip=clip)
    out(f"idle tone at the start: {(center + f_mark) / 1e6:.6f} MHz; the low tone should be at {expected_low / 1e6:.6f} MHz")
    if low is not None:
        err = center + low - expected_low
        result.update(low_hz=center + low, high_hz=center + high, shift_hz=high - low, error_hz=err)
        out(f"low tone {center + low:.1f} Hz ({err:+.1f} Hz from the wanted one = {err / dial * 1e6:+.3f} ppm of the sum of the crystals of the "
            f"transmitter and the Pluto), high tone {center + high:.1f} Hz, distance {high - low:.1f} Hz (asked {shift:g}); "
            f"frequency noise inside the tones {spread:.1f} Hz rms")
    ok = result["decoded"] == text
    out(f"decoded {result['decoded']!r}   {'== what was sent' if ok else 'DIFFERS from what was sent: ' + repr(text)}")
    result["ok"] = ok
    for line in spectrum_report(iq, fs, a, b):
        out(line)
    return result


# --- the receive chain of pluto-tx, offline ---------------------------------------------------------------------------------------------

def plutotx_chain(rec, errors=(0, -40, 40, -80, 80), out=print):
    from scipy import signal
    root = os.environ.get("PLUTO_TX_DIR", str(Path.home() / "Dokumente" / "plutosdr"))
    sys.path.insert(0, root)
    try:
        from gnuradio import analog, blocks, digital, gr
        from gnuradio import filter as gfilter
        from gnuradio.filter import firdes, window
        from pluto_advanced_rx.rtty_deframer import RTTYBaudotDeframer
    except ImportError as e:
        raise SystemExit(f"plutotx needs GNU Radio (run it with the python that has it, /usr/bin/python3 on Debian) and a pluto-tx checkout "
                         f"(PLUTO_TX_DIR, now {root}): {e}")
    iq, fs = rec["iq"], float(rec["fs"])
    baud, shift, mark, reverse, text = float(rec["baud"]), float(rec["shift"]), float(rec["mark"]), bool(rec["reverse"]), str(rec["text"])
    a, b = active_span(iq, fs)
    f_mark = peak_hz(iq[a + int(0.2 * fs):a + int(0.8 * fs)], fs)
    if_rate, working = 50_000.0, 5_000.0
    t = np.arange(len(iq)) / fs
    y = iq * np.exp(-2j * np.pi * (f_mark - mark) * t).astype(np.complex64)             # the idle tone now sits at the mark audio frequency
    y = signal.resample_poly(y, 50_000, int(fs)).astype(np.complex64)
    results = {}
    for err in errors:
        chars = []
        tb = gr.top_block()
        src = blocks.vector_source_c(y.tolist(), False)
        taps = firdes.low_pass(1.0, if_rate, shift / 2 + 300.0, 300.0, window.WIN_HAMMING)
        band = gfilter.freq_xlating_fir_filter_ccf(int(round(if_rate / working)), taps, mark + shift / 2 + err, if_rate)
        demod = analog.quadrature_demod_cf(working / (2 * math.pi * (shift / 2.0)))
        lp = gfilter.fir_filter_fff(1, firdes.low_pass(1.0, working, 150.0, 100.0, window.WIN_HAMMING))
        df = RTTYBaudotDeframer(lambda c: chars.append(c), working, baud, stop_bits=1.5, reverse=reverse)
        tb.connect(src, band, demod, lp, digital.binary_slicer_fb(), df)
        tb.run()
        got = "".join(chars)
        results[err] = got
        out(f"filter centre {err:+4d} Hz off: {got!r}   {'contains the text' if text in got else ''}")
    return results


# --- the log of pluto-cli rx -------------------------------------------------------------------------------------------------------------

def chars_from_log(path):
    """pluto-cli rx prints the decoded RTTY characters into the middle of its M17 debug lines ('<characters><3-character line number> state:...')."""
    out = []
    for line in open(path, errors="replace"):
        i = line.find(" state:")
        if i >= 3 and re.search(r"\d", line[i - 3:i]):
            out.append(line[:i - 3])
    return "".join(out)


# --- command line ------------------------------------------------------------------------------------------------------------------------

def load(path):
    z = np.load(path, allow_pickle=False)
    return {k: z[k][()] if z[k].ndim == 0 else z[k] for k in z.files}


def add_signal_args(p):
    p.add_argument("dial", type=float, metavar="DIAL_MHZ", help="the -f given to espdr-tx")
    p.add_argument("text")
    p.add_argument("--mark-hz", type=float, default=rtty.MARK_DEFAULT)
    p.add_argument("--shift-hz", type=float, default=rtty.SHIFT_DEFAULT)
    p.add_argument("--baud-rate", type=float, default=rtty.BAUD_DEFAULT)
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--uri", default="ip:plutoplus.local", help="libiio URI of the PlutoSDR (default ip:plutoplus.local)")
    p.add_argument("--pluto-gain", type=float, default=10.0, help="Pluto receive gain in dB (default 10; the recording must not clip)")
    p.add_argument("--seconds", type=float, default=9.0, help="length of the recording (default 9); it must cover the whole transmission")
    p.add_argument("--lead", type=float, default=1.5, help="seconds of recording before espdr-tx starts (default 1.5)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    tx_args = []
    if "--" in argv:
        i = argv.index("--")
        argv, tx_args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="send and record")
    add_signal_args(r)
    r.add_argument("--out", required=True, help="the recording (.npz)")
    ru = sub.add_parser("run", help="send, record and analyse")
    add_signal_args(ru)
    ru.add_argument("--out", help="also keep the recording (.npz)")
    an = sub.add_parser("analyse", help="analyse a recording")
    an.add_argument("file")
    pt = sub.add_parser("plutotx", help="run a recording through the RTTY receive chain of pluto-tx")
    pt.add_argument("file")
    pt.add_argument("--errors", type=int, nargs="*", default=[0, -40, 40, -80, 80], help="offsets of the filter centre in Hz")
    ch = sub.add_parser("chars", help="the characters in the log of pluto-cli rx --digimode rtty")
    ch.add_argument("file")
    a = ap.parse_args(argv)
    if a.cmd in ("record", "run"):
        rec = record(a, tx_args)
        if a.cmd == "run":
            return 0 if analyse_iq(rec)["ok"] else 1
    elif a.cmd == "analyse":
        return 0 if analyse_iq(load(a.file))["ok"] else 1
    elif a.cmd == "plutotx":
        plutotx_chain(load(a.file), a.errors)
    else:
        print(repr(chars_from_log(a.file)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
