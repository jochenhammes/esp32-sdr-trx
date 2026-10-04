#!/usr/bin/env python3
"""RESEARCH ONLY: Phase A of docs/research/PLAN-IQ-TX.md. Drives the IQTEST firmware (firmware/protocol/iqtest.h, `make -C firmware TX=1
IQTEST=1 NARROWBAND=1`) and measures the result with a PlutoSDR over libiio (system python3, needs `import iio`).

This script TRANSMITS on the ESP32-S3. Use it only with a transmit permission that covers the frequencies, and a cabled or well attenuated
setup. Every line it can emit is LO + (tone, image, 70 MHz spurs): the script prints them before it keys.

    scripts/iqtest.py noise                         # Pluto only: noise floor, clipping check
    scripts/iqtest.py case --lo 2350 --tone 5e6     # one case: complex tone
    scripts/iqtest.py phase-a --lo 2350             # the acceptance table of the plan
    scripts/iqtest.py poke 0x60033D64 [value]       # read/write a register (chain must be begun with `case --keep` first; see source)
"""
import argparse
import math
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from espdr import board, nb, txlink  # noqa: E402

IQ_OP_BEGIN, IQ_OP_KEY, IQ_OP_GAIN, IQ_OP_ROT_COS, IQ_OP_ROT_SIN, IQ_OP_FILL, IQ_OP_MS, IQ_OP_PLAY, IQ_OP_END = 50, 51, 52, 53, 54, 55, 56, 57, 58
IQ_OP_ADDR, IQ_OP_POKE, IQ_OP_PEEK = 59, 60, 61
IQ_OP_KEY2, IQ_OP_PBUS_RD, IQ_OP_PBUS_WR, IQ_OP_KEY_RAW, IQ_OP_PWR, IQ_OP_ANA_RD, IQ_OP_ANA_WR, IQ_OP_PRE_G, IQ_OP_PRE_P = 62, 63, 64, 65, 66, 67, 68, 69, 70
IQ_OP_ROT2_COS, IQ_OP_ROT2_SIN = 71, 72
MODE_TWO = 5
MODE_ROTATOR, MODE_REAL, MODE_CONST, MODE_ZERO = 0, 1, 2, 3
WORDS = 16384
IMAGE = ROOT / "firmware" / "build-iq" / "iq-source.bin"
PLUTO = "ip:169.254.10.209"
CRYSTAL_PPM = 5.4


# ---------------------------------------------------------------------------------------------------------------- Pluto
class Pluto:
    def __init__(self, uri=PLUTO):
        import iio
        self.iio = iio
        self.ctx = iio.Context(uri)
        self.phy = self.ctx.find_device("ad9361-phy")
        self.rx = self.ctx.find_device("cf-ad9361-lpc")
        self.rxch = self.phy.find_channel("voltage0", False)
        self.lo = self.phy.find_channel("altvoltage0", True)
        self.rx.find_channel("voltage0").enabled = True
        self.rx.find_channel("voltage1").enabled = True
        # never transmit from the Pluto: maximum attenuation on its own output
        try:
            self.phy.find_channel("voltage0", True).attrs["hardwaregain"].value = "-89.75"
        except Exception:
            pass
        self.fs = None

    def tune(self, center_hz, fs, gain_db):
        if self.fs != fs:
            self.rxch.attrs["sampling_frequency"].value = str(int(fs))
            self.rxch.attrs["rf_bandwidth"].value = str(int(min(0.9 * fs, 56e6)))
            self.fs = fs
        self.rxch.attrs["gain_control_mode"].value = "manual"
        self.rxch.attrs["hardwaregain"].value = str(gain_db)
        self.lo.attrs["frequency"].value = str(int(center_hz))
        self.center = center_hz

    def capture(self, n):
        buf = self.iio.Buffer(self.rx, n, False)
        buf.refill()
        raw = np.frombuffer(buf.read(), dtype="<i2").astype(np.float32)
        del buf
        x = raw[0::2] + 1j * raw[1::2]
        return x - 0  # keep Pluto's own DC; the analysis looks at offsets away from it


class HackRF:
    """Second receiver for the verification rule: same interface as Pluto (tune, capture), through hackrf_transfer (8-bit I/Q)."""

    def __init__(self, lna=24, vga=20, amp=0):
        self.lna, self.vga, self.amp = lna, vga, amp
        self.fs = None

    def tune(self, center_hz, fs, gain_db):
        self.center, self.fs = center_hz, fs

    def capture(self, n):
        import subprocess, tempfile, os
        path = tempfile.mktemp(prefix="hackrf_", suffix=".bin")
        try:
            subprocess.run(["hackrf_transfer", "-r", path, "-f", str(int(self.center)), "-s", str(int(self.fs)), "-n", str(int(n)),
                            "-l", str(self.lna), "-g", str(self.vga), "-a", str(self.amp)], check=True, capture_output=True)
            raw = np.fromfile(path, dtype=np.int8).astype(np.float32)
        finally:
            if os.path.exists(path):
                os.remove(path)
        x = raw[0::2] + 1j * raw[1::2]
        return x * 16.0     # same scale as the Pluto's 12-bit samples, so that the clip figure (peak / 2048) is comparable


def spectrum(x, fs, nper=8192):
    from scipy.signal import welch
    f, p = welch(x, fs, nperseg=nper, return_onesided=False, detrend=False, window="hann")
    order = np.argsort(f)
    f, p = f[order], p[order]
    return f, 10 * np.log10(p + 1e-12)


def floor_db(p):
    return float(np.median(p))


def peaks(f, p, count=4, guard=300e3):
    out, q = [], p.copy()
    for _ in range(count):
        i = int(np.argmax(q))
        out.append((f[i], q[i]))
        q[np.abs(f - f[i]) < guard] = -300
    return out


def window_max(f, p, center, half=40e3):
    m = np.abs(f - center) <= half
    if not m.any():
        return None
    i = np.argmax(np.where(m, p, -300))
    return f[i], p[i]


# ------------------------------------------------------------------------------------------------------------------ ESP
class Esp:
    def __init__(self, load="auto", image=IMAGE):
        port = board.find_native()
        self.link = None
        if port and load != "always":
            try:
                self.link = nb.open_link(port)
                st, _ = self.link.command(IQ_OP_MS, 100, allow=(nb.CTL_OK, nb.CTL_UNKNOWN_OP, nb.CTL_BAD_ARGUMENT))
                if st == nb.CTL_UNKNOWN_OP:
                    self.link.ser.close()
                    self.link = None
            except Exception:
                self.link = None
        if self.link is None:
            board.load_ram(board.TX, image=str(image))
            time.sleep(0.5)
            self.link = nb.open_link(board.find_native())
            st, _ = self.link.command(IQ_OP_MS, 100, allow=(nb.CTL_OK, nb.CTL_UNKNOWN_OP))
            if st != nb.CTL_OK:
                raise SystemExit("the loaded firmware does not know the IQTEST ops")

    def c(self, op, arg=0, allow=(nb.CTL_OK,)):
        return self.link.command(op, arg & 0xFFFFFFFF, allow=allow)

    def begin(self, lo_hz, skip_cal=False):
        lo, shift = txlink.choose_lo(lo_hz)
        self.c(txlink.TX_OP_LO, int(round(lo / 100.0)))
        _, word = self.c(IQ_OP_BEGIN, int(skip_cal))
        self.lo_hz = lo
        return lo, word

    TONE_REG = 0x60006040   # bit 18 = enable of the PHY's tone generator (a=1 in start_tx_tone_step)

    def key(self, gain=127, bank=4, tone_off=True):
        """Keys the chain (carrier on). With tone_off the tone generator's enable bit 18 of 0x60006040 is cleared afterwards: the chain stays keyed, the
        LO-feedthrough 'carrier' disappears and the playback engine's samples reach the antenna. FOUND 2026-10-04: without this the engine is invisible."""
        _, rb = self.c(IQ_OP_KEY, gain | (bank << 8))
        if tone_off:
            self.c(IQ_OP_ADDR, self.TONE_REG)
            v = self.c(IQ_OP_PEEK)[1]
            self.c(IQ_OP_POKE, v & ~(1 << 18) & 0xFFFFFFFF)
        return rb

    def fill(self, tone_hz, rate_msps, amp, mode):
        fs_dac = rate_msps * 1e6
        k = round(tone_hz / (fs_dac / WORDS))     # whole cycles per buffer: the seam is continuous
        actual = k * fs_dac / WORDS
        ph = 2 * math.pi * k / WORDS
        self.c(IQ_OP_ROT_COS, round(math.cos(ph) * (1 << 30)))
        self.c(IQ_OP_ROT_SIN, round(math.sin(ph) * (1 << 30)))
        self.c(IQ_OP_FILL, amp | (mode << 16))
        return actual

    def play_start(self, ms, count=WORDS, rate80=True, hold=False, extra=0):
        self.c(IQ_OP_MS, ms)
        bits = (count - 1) | (int(rate80) << 15) | (int(hold) << 19) | extra
        self.link.send(IQ_OP_PLAY, bits)

    def play_result(self, timeout=10.0):
        st, v = self.link._response(IQ_OP_PLAY, timeout=timeout)
        return st, v & 0xFFFF, v >> 16

    def end(self):
        self.c(IQ_OP_END)

    def fill_gap(self, tone_hz, amp, gap_samples, rate_msps=80, mode=MODE_ROTATOR):
        """A tone whose phase continues across the pause between two triggers of the engine: the pause counts as gap_samples extra samples of the DAC clock.
        Buffer start phase is always 0, so the phase advance per sample is 2*pi*k/(16384+G) with an integer k."""
        n = WORDS + float(gap_samples)
        k = round(tone_hz * n / (rate_msps * 1e6)); ph = 2 * math.pi * k / n
        self.c(IQ_OP_ROT_COS, round(math.cos(ph) * (1 << 30))); self.c(IQ_OP_ROT_SIN, round(math.sin(ph) * (1 << 30)))
        self.c(IQ_OP_FILL, amp | (mode << 16))
        return k * rate_msps * 1e6 / n

    def fill_two(self, f1, f2, amp, rate_msps=80):
        """Two complex tones of amplitude amp/2 each (a two-tone test for intermodulation)."""
        out = []
        for f, (cop, sop) in ((f1, (IQ_OP_ROT_COS, IQ_OP_ROT_SIN)), (f2, (IQ_OP_ROT2_COS, IQ_OP_ROT2_SIN))):
            k = round(f / (rate_msps * 1e6 / WORDS)); ph = 2 * math.pi * k / WORDS
            self.c(cop, round(math.cos(ph) * (1 << 30))); self.c(sop, round(math.sin(ph) * (1 << 30)))
            out.append(k * rate_msps * 1e6 / WORDS)
        self.c(IQ_OP_FILL, amp | (MODE_TWO << 16))
        return out

    TX_FILTER_BLOCK, TX_FILTER_REGS = 0x67, (12, 13)   # analog block of the baseband filter; regs 12 and 13 are the TX filter capacitor codes (default 0x23 = 20 MHz channel)

    def tx_filter(self, code):
        """0 = widest (flat to +-35 MHz); 0x23 = default after reset (about 20 MHz, -28 dB at 20 MHz and -55 dB at 35 MHz)."""
        for r in self.TX_FILTER_REGS:
            self.c(IQ_OP_ANA_WR, code << 16 | self.TX_FILTER_BLOCK << 8 | r)

    def predistort(self, g=0.0, p=0.0):
        """Q' = (1+g) Q + p I applied by IQ_OP_FILL to complex and real tones (corrects the analog I/Q imbalance in software)."""
        self.c(IQ_OP_PRE_G, int(round(g * 65536)))
        self.c(IQ_OP_PRE_P, int(round(p * 65536)))

    def peek(self, addr):
        self.c(IQ_OP_ADDR, addr)
        return self.c(IQ_OP_PEEK)[1]

    def poke(self, addr, value):
        self.c(IQ_OP_ADDR, addr)
        self.c(IQ_OP_POKE, value)


# ----------------------------------------------------------------------------------------------------------------- cases
def run_case(esp, pluto, lo_hz, tone_hz, args, mode=MODE_ROTATOR, rate80=True, bank=4, amp=None, hold=False, keyed=True, label="", extra=0,
             center_off=None, fill_rate=None):
    """Plays one buffer pattern and measures it. Returns a dict."""
    amp = args.amp if amp is None else amp
    actual = esp.fill(tone_hz, fill_rate or (80 if rate80 else 40), amp, mode) if mode in (MODE_ROTATOR, MODE_REAL) else (esp.fill(0, 80, amp, mode) and 0)
    off = args.center_off if center_off is None else center_off
    center = lo_hz + off
    pluto.tune(center, args.fs, args.pluto_gain)
    time.sleep(0.15)
    esp.play_start(args.ms, rate80=rate80, hold=hold, extra=extra)
    time.sleep(0.12)
    x = pluto.capture(args.n)
    st, done, to = esp.play_result()
    f, p = spectrum(x, args.fs)
    fl = floor_db(p)
    clip = float(np.max(np.abs(np.concatenate([x.real, x.imag])))) / 2048.0
    shift = lo_hz * CRYSTAL_PPM * 1e-6          # the ESP's crystal error moves every line by this much
    rel = lambda d: None if d is None else (d[0] + off, d[1] - fl)   # offset from the ESP LO (nominal), dB over the floor; Pluto centre = LO + off
    res = dict(label=label, tone=actual, status=st, triggers=done, timeouts=to, clip=clip, floor=fl,
               peaks=[(a + off, b - fl) for a, b in peaks(f, p)],
               at_tone=rel(window_max(f, p, actual + shift - off, 60e3)) if mode != MODE_CONST else None,
               at_image=rel(window_max(f, p, -actual + shift - off, 60e3)) if mode == MODE_ROTATOR else None,
               at_lo=rel(window_max(f, p, shift - off, 60e3)))
    return res, (f, p, off)


def show(res):
    def fmt(v):
        return "-" if v is None else f"{v[0] / 1e6:+9.4f} MHz {v[1]:+6.1f} dB"
    print(f"--- {res['label']}: tone {res['tone'] / 1e6:+.4f} MHz, status {res['status']}, triggers {res['triggers']}, poll timeouts {res['timeouts']}, "
          f"clip {res['clip']:.2f}, floor {res['floor']:.1f} dB/Hz")
    print(f"    at expected tone : {fmt(res['at_tone'])}   at image : {fmt(res['at_image'])}   at LO : {fmt(res['at_lo'])}")
    print("    strongest peaks  : " + "; ".join(f"{a / 1e6:+.4f} MHz {b:+.1f}" for a, b in res["peaks"]))


def announce(lo_hz, tones, rate80, args):
    lines = sorted({lo_hz + s * t for t in tones for s in (-1, 0, 1)})
    print("Lines this test can emit (MHz): " + ", ".join(f"{v / 1e6:.3f}" for v in lines))
    print(f"ESP: LO {lo_hz / 1e6:.4f} MHz, gain code {args.gain}, amplitude {args.amp}/511, rate {'80' if rate80 else '40'} Msps; Pluto RX {PLUTO}, "
          f"{args.fs / 1e6:.2f} Msps, centre LO{args.center_off / 1e6:+.1f} MHz")


# ---------------------------------------------------------------------------------------------------------------- commands
def cmd_noise(args):
    p = Pluto(args.uri)
    p.tune(args.lo * 1e6 + args.center_off, args.fs, args.pluto_gain)
    time.sleep(0.2)
    x = p.capture(args.n)
    f, ps = spectrum(x, args.fs)
    print(f"floor {floor_db(ps):.1f} dB/Hz, clip {np.max(np.abs(np.concatenate([x.real, x.imag]))) / 2048:.2f}, peaks:",
          "; ".join(f"{a / 1e6:+.3f} MHz {b - floor_db(ps):+.1f} dB" for a, b in peaks(f, ps)))


def cmd_case(args):
    lo_req = args.lo * 1e6
    esp = Esp(args.load)
    pluto = Pluto(args.uri)
    announce(lo_req, [args.tone], not args.rate40, args)
    lo, word = esp.begin(lo_req)
    print(f"LO {lo / 1e6:.4f} MHz, PLL word 0x{word:06X}")
    try:
        print("bank select readback after keying: 0x%X" % esp.key(args.gain, args.bank))
        mode = {"complex": MODE_ROTATOR, "real": MODE_REAL, "const": MODE_CONST, "zero": MODE_ZERO}[args.mode]
        res, _ = run_case(esp, pluto, lo, args.tone, args, mode=mode, rate80=not args.rate40, bank=args.bank, hold=args.hold, label=args.mode)
        show(res)
    finally:
        esp.end()


def cmd_phase_a(args):
    lo_req = args.lo * 1e6
    esp = Esp(args.load)
    pluto = Pluto(args.uri)
    tones = [5e6, 10e6]
    announce(lo_req, tones + [2.5e6], True, args)
    lo, word = esp.begin(lo_req)
    print(f"LO {lo / 1e6:.4f} MHz, PLL word 0x{word:06X}")
    log = []
    try:
        print("bank select readback after keying: 0x%X" % esp.key(args.gain, 4))
        # reference: keyed chain, engine idle (LO feedthrough only)
        esp.fill(5e6, 80, args.amp, MODE_ZERO)
        res, _ = run_case(esp, pluto, lo, 0, args, mode=MODE_ZERO, label="engine playing zeros (LO feedthrough reference)")
        show(res); log.append(res)
        for tone, sign in ((5e6, 1), (-5e6, 1), (10e6, 1)):
            res, _ = run_case(esp, pluto, lo, tone, args, label=f"complex tone {tone / 1e6:+.1f} MHz @80 Msps")
            show(res); log.append(res)
        res, _ = run_case(esp, pluto, lo, 5e6, args, rate80=False, label="complex 5 MHz tone, rate bit 0 (40 Msps): expect 2.5 MHz")
        show(res); log.append(res)
        res, _ = run_case(esp, pluto, lo, 5e6, args, mode=MODE_REAL, label="real cosine 5 MHz in I only: expect +-5 MHz")
        show(res); log.append(res)
        res, _ = run_case(esp, pluto, lo, 0, args, mode=MODE_CONST, amp=min(args.amp, 400), label="constant word I=A: carrier at the LO")
        show(res); log.append(res)
        # bank not granted: the tone must vanish
        esp.c(IQ_OP_ADDR, 0x600C101C); esp.c(IQ_OP_POKE, 0x1)
        res, _ = run_case(esp, pluto, lo, 5e6, args, label="complex 5 MHz tone but bank 0 granted (not bank 2): expect no line")
        show(res); log.append(res)
        esp.c(IQ_OP_POKE, 0x4)
    finally:
        esp.end()


def cmd_poke(args):
    esp = Esp(args.load)
    addr = int(args.addr, 0)
    if args.value is None:
        print(f"0x{addr:08X} = 0x{esp.peek(addr):08X}")
    else:
        esp.poke(addr, int(args.value, 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=PLUTO)
    ap.add_argument("--load", default="auto", choices=("auto", "always"))
    ap.add_argument("--lo", type=float, default=2350.0, help="ESP LO in MHz")
    ap.add_argument("--fs", type=float, default=30.72e6, help="Pluto sample rate")
    ap.add_argument("--n", type=int, default=1 << 20, help="Pluto samples per capture")
    ap.add_argument("--center-off", type=float, default=-3e6, help="Pluto centre minus ESP LO, Hz")
    ap.add_argument("--pluto-gain", type=float, default=30.0)
    ap.add_argument("--gain", type=int, default=127, help="ESP tone gain code (127 weakest)")
    ap.add_argument("--amp", type=int, default=100, help="sample amplitude, 0..511")
    ap.add_argument("--ms", type=int, default=500)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("noise")
    c = sub.add_parser("case")
    c.add_argument("--tone", type=float, default=5e6)
    c.add_argument("--mode", choices=("complex", "real", "const", "zero"), default="complex")
    c.add_argument("--rate40", action="store_true")
    c.add_argument("--hold", action="store_true")
    c.add_argument("--bank", type=int, default=4)
    sub.add_parser("phase-a")
    k = sub.add_parser("poke")
    k.add_argument("addr")
    k.add_argument("value", nargs="?")
    args = ap.parse_args()
    {"noise": cmd_noise, "case": cmd_case, "phase-a": cmd_phase_a, "poke": cmd_poke}[args.cmd](args)


if __name__ == "__main__":
    main()
