#!/usr/bin/env python3
"""RESEARCH ONLY: L1 of docs/research/PLAN-LORA-IQ.md, scheme S: a whole LoRa frame in one buffer of the I/Q playback engine, repeated, received by a PlutoSDR
and decoded with the numpy receiver. TRANSMITS (inside the 13 cm band: LO + offset +- BW/2). Needs the research firmware with the load ops
(`make -C firmware TX=1 IQTEST=1 NARROWBAND=1`, image firmware/build-iq/iq-source.bin) and a transmit permission.

    python3 scripts/lora_iq/run.py render  --sf 5 --bw 4e6          # no hardware: words, duration, lines on the air
    python3 scripts/lora_iq/run.py send    --lo 2350 --sf 5 --bw 4e6  # the on-air test
    python3 scripts/lora_iq/run.py analyse capture.npz              # decode a saved capture again
"""
import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import lora_np as ln  # noqa: E402

PRE_G, PRE_P = 0.010, -0.055          # image correction of this board (IQ-TX-PHASE-A.md)
DAC_RATE = 40e6
PAYLOAD = b"DA2JH LORA-1"             # 12 bytes: the call sign belongs in the frame


def render(payload, sf, bw, f_off, amp=200, os_=None, rate=DAC_RATE, cr=1, preamble=8, g=PRE_G, p=PRE_P):
    """The frame as engine words: baseband frame at `rate`, shifted by f_off, I/Q corrected, 10 bit at peak `amp`. Returns (words uint32, I, Q, n)."""
    os_ = os_ or int(round(rate / bw))
    iq = ln.frame_iq(payload, sf, bw, os_, cr, preamble)
    t = np.arange(len(iq)) / (bw * os_)
    z = iq * np.exp(2j * np.pi * f_off * t)
    i = z.real
    q = (1 + g) * z.imag + p * z.real
    ii = np.clip(np.rint(i * amp), -512, 511).astype(np.int32)
    qq = np.clip(np.rint(q * amp), -512, 511).astype(np.int32)
    words = (ii & 0x3FF).astype(np.uint32) | ((qq & 0x3FF).astype(np.uint32) << 10)
    return words, ii, qq, len(words)


def load_words(esp, words, chunk=40):
    """Writes the words into bank 2 with IQ_OP_LDI / IQ_OP_LDQ, requests sent in bulk."""
    from espdr import nb
    import iqtest as it
    esp.c(it.IQ_OP_LDPOS, 0)
    link = esp.link
    t0 = time.time()
    for a in range(0, len(words), chunk):
        part = words[a:a + chunk]
        reqs = []
        for w in part:
            reqs.append(nb.request(it.IQ_OP_LDI, int(w & 0x3FF), link._next_seq()))
            reqs.append(nb.request(it.IQ_OP_LDQ, int((w >> 10) & 0x3FF), link._next_seq()))
        link.ser.write(b"".join(reqs))
        for _ in part:
            for op in (it.IQ_OP_LDI, it.IQ_OP_LDQ):
                st, _ = link._response(op)
                if st != nb.CTL_OK:
                    raise SystemExit(f"load failed at word {a}: status {st}")
    return time.time() - t0


def lines_on_air(lo_hz, f_off, bw):
    return (lo_hz + f_off - bw / 2, lo_hz + f_off + bw / 2, lo_hz, lo_hz - f_off)


def cmd_render(args):
    words, ii, qq, n = render(PAYLOAD, args.sf, args.bw, args.offset, args.amp)
    print(f"SF{args.sf} BW {args.bw / 1e6:g} MHz at {DAC_RATE / 1e6:g} Msps: {n} words = {n / DAC_RATE * 1e6:.1f} us (buffer {16384 / DAC_RATE * 1e6:.1f} us), peak I {abs(ii).max()}, Q {abs(qq).max()}")
    lo, hi, lo_line, image = lines_on_air(args.lo * 1e6, args.offset, args.bw)
    print(f"band edges {lo / 1e6:.3f} .. {hi / 1e6:.3f} MHz, LO line {lo_line / 1e6:.3f}, image about {image / 1e6:.3f} MHz")


def band_snr(y, fs, bw, good):
    """Power of the frames in the band against the power in the pauses between them, both low-passed to the bandwidth. y is shifted so that the frame is centred."""
    if len(good) < 3:
        return None
    from scipy import signal
    taps = signal.firwin(257, bw / 2 / (fs / 2) * 0.95)
    yb = signal.fftconvolve(y, taps, mode="same")
    pw = np.abs(yb) ** 2
    sig, noi = [], []
    for a, b in zip(good[:-1], good[1:]):
        m = 400
        sig.append(np.mean(pw[a["start"] + m:a["end"] - m]))
        noi.append(np.mean(pw[a["end"] + m + 200:b["start"] - m]))
    ps, pn = float(np.mean(sig)), float(np.mean(noi))
    return 10 * np.log10(max(ps - pn, 1e-12) / max(pn, 1e-12))


def analyse(x, fs, f_off, args, triggers=None):
    t = np.arange(len(x)) / fs
    y = (x - np.mean(x)) * np.exp(-2j * np.pi * f_off * t)
    frames = ln.demodulate_all(y.astype(np.complex64), fs, args.sf, args.bw)
    good = [r for r in frames if r.get("crc_ok") and r.get("payload") == PAYLOAD]
    snrs = [r["snr_db"] for r in good]
    real = band_snr(y, fs, args.bw, good)
    print(f"decoded: {len(good)} frames with the right bytes and CRC, {len(frames) - len(good)} with errors, of {len(frames)} found"
          + (f"; the chip triggered {triggers} times" if triggers else ""))
    if real is not None:
        print(f"  SNR in the bandwidth from frame power against the gaps: {real:.1f} dB (the preamble estimator saturates at about 13.5 dB)")
    if good:
        print(f"  preamble estimate {np.mean(snrs):.1f} dB (min {np.min(snrs):.1f}, max {np.max(snrs):.1f}), frequency offset {np.mean([r['cfo_hz'] for r in good]) / 1e3:+.2f} kHz "
              f"(spread {np.std([r['cfo_hz'] for r in good]):.0f} Hz)")
    bad = [r for r in frames if r not in good]
    for r in bad[:3]:
        print("  bad frame:", {k: r.get(k) for k in ("payload", "header_ok", "crc_ok", "snr_db", "cfo_hz")})
    return good, frames


def cmd_send(args):
    import iqtest as it
    lo_req = args.lo * 1e6
    words, ii, qq, n = render(PAYLOAD, args.sf, args.bw, args.offset, args.amp)
    print(f"frame: {n} words = {n / DAC_RATE * 1e6:.1f} us at {DAC_RATE / 1e6:g} Msps, SF{args.sf}, BW {args.bw / 1e6:g} MHz, shifted by {args.offset / 1e6:+.2f} MHz")
    lo_e, hi_e, lo_line, image = lines_on_air(lo_req, args.offset, args.bw)
    print(f"on the air: {lo_e / 1e6:.3f} .. {hi_e / 1e6:.3f} MHz, LO line {lo_line / 1e6:.3f}, image about {image / 1e6:.3f} MHz; gain code {args.gain}")
    if not (2320e6 <= lo_e and hi_e <= 2450e6):
        raise SystemExit("outside the 13 cm band")
    esp = it.Esp(args.load)
    pluto = it.Pluto(args.uri)
    lo, word = esp.begin(lo_req)
    print(f"LO {lo / 1e6:.4f} MHz, PLL word 0x{word:06X}")
    try:
        esp.key(args.gain, 4)
        esp.tx_filter(0)
        dt = load_words(esp, words)
        print(f"loaded {n} words in {dt:.1f} s")
        esp.c(it.IQ_OP_GAP, args.gap_us)
        caps = []
        for pg in [float(v) for v in str(args.pluto_gain).split(",")]:
            pluto.tune(lo + args.center_off, args.fs, pg)
            time.sleep(0.2)
            esp.play_start(args.ms, count=n, rate80=False)
            time.sleep(0.12)
            x = pluto.capture(args.n)
            st, done, to = esp.play_result()
            shift = args.offset - args.center_off
            clip = float(np.max(np.abs(np.concatenate([x.real, x.imag])))) / 2048
            print(f"Pluto gain {pg:g} dB: status {st}, {done} triggers, {to} poll timeouts, clip {clip:.2f}", flush=True)
            caps.append((pg, x, done, shift))
    finally:
        esp.end()
    results = []                       # decoded after the chip ended its session: the research firmware stops by itself after 20 s without a command
    for pg, x, done, shift in caps:
        if args.save:
            np.savez_compressed(args.save.replace(".npz", f"_pg{pg:g}.npz"), x=x.astype(np.complex64), fs=args.fs, shift=shift)
        print(f"--- Pluto gain {pg:g} dB")
        good, frames = analyse(x, args.fs, shift, args, done)
        period = (n / DAC_RATE + args.gap_us * 1e-6 + 0.34e-6) * args.fs
        slots = (round((good[-1]["start"] - good[0]["start"]) / period) + 1) if len(good) > 1 else max(len(good), 1)
        results.append((pg, np.mean([r["snr_db"] for r in good]) if good else float("nan"), len(good), slots))
    print("Pluto gain [dB] | SNR in the bandwidth [dB] | frames decoded / slots | packet error rate")
    for pg, snr, g, sl in results:
        print(f"  {pg:6g}          {snr:8.1f}                  {g:4d} / {sl:4d}              {1 - g / sl:5.2f}")


def cmd_noise(args):
    """Adds white noise to a saved capture so that the SNR in the bandwidth takes the wanted values, and counts the frames that still decode.
    The real transmitter (its spurs, phase noise, image) stays in the signal; only the receiver noise is synthetic."""
    d = np.load(args.file)
    x, fs, shift = d["x"], float(d["fs"]), float(d["shift"])
    t = np.arange(len(x)) / fs
    y = ((x - np.mean(x)) * np.exp(-2j * np.pi * shift * t)).astype(np.complex64)
    frames = ln.demodulate_all(y, fs, args.sf, args.bw)
    good = [r for r in frames if r.get("crc_ok") and r.get("payload") == PAYLOAD]
    base = band_snr(y, fs, args.bw, good)
    slots = len(good)
    first, last = good[0]["start"], good[-1]["end"]
    ps = None
    from scipy import signal
    taps = signal.firwin(257, args.bw / 2 / (fs / 2) * 0.95)
    pw = np.abs(signal.fftconvolve(y, taps, mode="same")) ** 2
    ps = float(np.mean([np.mean(pw[g["start"] + 400:g["end"] - 400]) for g in good]))
    print(f"clean capture: {len(good)} frames decoded, SNR from the gaps {base:.1f} dB, frame power {ps:.3g}")
    rng = np.random.default_rng(7)
    print("SNR set [dB] | effective SNR [dB] | decoded / frames | packet error rate")
    for snr in [float(v) for v in args.snrs.split(",")]:
        pn_total = ps / 10 ** (snr / 10)
        pn_have = ps / 10 ** (base / 10)
        add = max(pn_total - pn_have, 0.0)
        sigma2 = add * fs / args.bw                                   # white over fs, in-band share bw / fs
        segment = y[first - 2000:last + 2000]
        n = (rng.standard_normal(len(segment)) + 1j * rng.standard_normal(len(segment))) * np.sqrt(sigma2 / 2)
        fr = ln.demodulate_all((segment + n).astype(np.complex64), fs, args.sf, args.bw)
        ok = sum(1 for r in fr if r.get("crc_ok") and r.get("payload") == PAYLOAD)
        print(f"  {snr:6.1f}        {10 * np.log10(ps / (pn_have + add)):6.1f}            {ok:4d} / {slots:4d}        {1 - ok / slots:5.2f}", flush=True)


def cmd_analyse(args):
    d = np.load(args.file)
    analyse(d["x"], float(d["fs"]), float(d["shift"]), args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("render", "send", "analyse", "noise"))
    ap.add_argument("file", nargs="?")
    ap.add_argument("--lo", type=float, default=2350.0, help="ESP LO in MHz")
    ap.add_argument("--sf", type=int, default=5)
    ap.add_argument("--bw", type=float, default=4e6)
    ap.add_argument("--offset", type=float, default=3e6, help="the frame's centre above the LO, Hz (away from the LO line)")
    ap.add_argument("--amp", type=int, default=200)
    ap.add_argument("--gain", type=int, default=127, help="ESP gain code (127 weakest .. 64 strongest)")
    ap.add_argument("--gap-us", type=int, default=1000)
    ap.add_argument("--ms", type=int, default=600)
    ap.add_argument("--uri", default="ip:169.254.10.209")
    ap.add_argument("--load", default="auto", choices=("auto", "always"))
    ap.add_argument("--fs", type=float, default=16e6, help="Pluto sample rate")
    ap.add_argument("--n", type=int, default=1 << 21)
    ap.add_argument("--center-off", type=float, default=0.0, help="Pluto centre minus ESP LO, Hz")
    ap.add_argument("--pluto-gain", default="30", help="Pluto gain in dB; several separated by commas make a sweep")
    ap.add_argument("--save")
    ap.add_argument("--snrs", default="12,9,7,5,4,3,2,1,0,-1")
    args = ap.parse_args()
    if args.cmd == "render":
        cmd_render(args)
    elif args.cmd == "send":
        cmd_send(args)
    elif args.cmd == "noise":
        cmd_noise(args)
    else:
        cmd_analyse(args)


if __name__ == "__main__":
    main()
