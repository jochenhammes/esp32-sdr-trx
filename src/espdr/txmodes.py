"""FM and SSB modulators for the ESP32-S3 transmitter.

The chip has no I/Q input. What it can do is move its carrier in frequency (the RF PLL's sigma-delta word, 457.76 Hz per step, updated
40000 times a second) and in amplitude (a gain code, 0.28 dB per step over 18 dB). A modulator turns 8 kHz audio into one 32-bit record per
update (firmware/protocol/transmit.h): the frequency offset in 1/16 word steps, the gain code, flags.

FM  sets the frequency from the audio and keeps the gain constant.
SSB is polar modulation: the analytic signal of the audio (the audio plus j times its Hilbert transform: positive frequencies only, which is
    the upper sideband) plus a carrier gives an envelope, sent as the gain code, and a phase, sent as its derivative, the frequency.
"""
import numpy as np

from . import dsp

STEP_HZ = 30e6 / 65536          # one PLL word step
Q4_HZ = STEP_HZ / 16            # unit of the frequency field of a record
MAX_STEPS = 44                  # the firmware clamps the offset to +-44 steps (TX_MAX_STEPS)
MAX_Q4 = (MAX_STEPS - 2) * 16   # the host stays two steps inside
GAIN_STRONGEST, GAIN_WEAKEST = 64, 127
AUDIO_RATE = 8000

# Amplitude of the carrier against gain code 127 in dB, measured with a PlutoSDR (docs/research/TX-RESEARCH.md, stage A1).
GAIN_CURVE = [(127, 0.0), (124, 1.32), (121, 1.94), (118, 2.79), (115, 3.82), (112, 4.74), (109, 5.22), (106, 6.05), (103, 7.03),
              (100, 8.05), (97, 8.79), (94, 9.78), (91, 10.56), (88, 11.57), (85, 12.26), (82, 13.07), (79, 13.91), (76, 14.76),
              (73, 15.51), (70, 16.33), (67, 17.12), (64, 17.90)]
_CODES = np.array([c for c, _ in GAIN_CURVE][::-1], dtype=float)     # 64 .. 127
_DB = np.array([d for _, d in GAIN_CURVE][::-1], dtype=float)        # 17.9 .. 0
RANGE_DB = float(_DB[0])


def peak_code(power_db):
    """Gain code of the strongest envelope for a power setting in dB against the strongest possible (0, -17.9 at the weakest)."""
    if power_db > 0:
        raise ValueError("the power cannot be raised above 0 dB (the strongest setting)")
    if power_db < -RANGE_DB:
        raise ValueError(f"the weakest possible power is {-RANGE_DB:.1f} dB")
    return int(round(float(np.interp(RANGE_DB + power_db, _DB[::-1], _CODES[::-1]))))


def db_of_code(code):
    return np.interp(code, _CODES, _DB)


def codes_for_levels(level, peak):
    """Gain codes for envelope values `level` (1.0 = the peak) when the peak is sent with code `peak`."""
    db = db_of_code(peak) + 20 * np.log10(np.maximum(level, 1e-6))
    codes = np.interp(db, _DB[::-1], _CODES[::-1])         # inverse of the curve; below the curve's end it stays at 127
    return np.clip(np.rint(codes), peak, GAIN_WEAKEST).astype(np.uint32)


def pack(q4, codes, end=False):
    q4 = np.clip(np.rint(q4), -MAX_Q4, MAX_Q4).astype(np.int16)
    rec = q4.view(np.uint16).astype(np.uint32) | (np.asarray(codes, dtype=np.uint32) << 16)
    if end and len(rec):
        rec[-1] |= np.uint32(1 << 24)
    return rec


def end_record(code=GAIN_WEAKEST):
    return np.array([(code << 16) | (1 << 24)], dtype=np.uint32)


class _Front:
    """Band-pass, AGC (optional) for 8 kHz audio blocks."""

    def __init__(self, band, agc, gain_db):
        self.bp = dsp.Fir(dsp.bandpass(255, band[0], band[1], AUDIO_RATE))
        self.agc = dsp.Agc(AUDIO_RATE, gain_db=gain_db) if agc else None
        self.manual = 10 ** (gain_db / 20)
        self.delay = self.bp.delay

    def process(self, x):
        y = self.bp.process(np.asarray(x, dtype=float))
        return self.agc.process(y) if self.agc else np.clip(y * self.manual, -1, 1)


class FmModulator:
    """Narrowband FM: `deviation` Hz at full scale, with the usual voice pre-emphasis (+6 dB per octave from 300 to 3000 Hz)."""

    name = "FM"

    def __init__(self, rate=40000, deviation=2500.0, power_db=0.0, preemph=True, agc=True, gain_db=0.0, band=(300.0, 3000.0)):
        if deviation / Q4_HZ > MAX_Q4:
            raise ValueError(f"the deviation can be at most {MAX_Q4 * Q4_HZ:.0f} Hz")
        self.rate, self.deviation = rate, deviation
        self.code = peak_code(power_db)
        self.front = _Front(band, agc, gain_db)
        self.pre = dsp.preemphasis(AUDIO_RATE) if preemph else None
        self.up = dsp.Resampler(AUDIO_RATE, rate)
        self.latency_s = (self.front.delay + self.up.latency / (rate / AUDIO_RATE)) / AUDIO_RATE

    def process(self, audio):
        x = self.front.process(audio)
        if self.pre:
            x = np.clip(self.pre.process(x), -1, 1)
        y = self.up.process(x)
        return pack(y * self.deviation / Q4_HZ, np.full(len(y), self.code, dtype=np.uint32))

    def trim(self, ppm):
        self.up.trim = ppm


class SsbModulator:
    """Single sideband by polar modulation. `carrier` is the carrier's share of the peak envelope: 0 suppresses it completely (the
    envelope then runs through zero, which the 18 dB range of the gain code cannot follow exactly and the frequency cannot follow at
    the nulls; measured quality: docs/research), 0.05 leaves a faint pilot to tune to, 0.5 or more is a robust AM-compatible signal.
    `delay` (in updates) delays the gain path against the frequency path: the transmitter's amplitude responds 25 us earlier."""

    def __init__(self, rate=40000, sideband="usb", carrier=0.05, power_db=0.0, delay=1.0, agc=True, gain_db=0.0, band=(300.0, 2700.0)):
        if sideband not in ("usb", "lsb"):
            raise ValueError("sideband must be usb or lsb")
        if not 0.0 <= carrier < 1.0:
            raise ValueError("the carrier share must be 0 .. 1")
        self.name = sideband.upper()
        self.rate, self.lsb, self.c, self.delay = rate, sideband == "lsb", carrier, delay
        self.peak = peak_code(power_db)
        self.front = _Front(band, agc, gain_db)
        self.analytic = dsp.Analytic(255)
        self.up = dsp.Resampler(AUDIO_RATE, rate)
        self.z_prev = carrier + 0j
        self.a_tail = np.full(4, carrier)
        self.latency_s = (self.front.delay + self.analytic.delay) / AUDIO_RATE + self.up.latency / rate

    def process(self, audio):
        x = self.front.process(audio)
        xa = self.analytic.process(x)
        if self.lsb:
            xa = np.conj(xa)
        xa = dsp.clip_magnitude(xa, 1.0)
        z = self.c + (1.0 - self.c) * self.up.process(xa)
        if len(z) == 0:
            return np.zeros(0, dtype=np.uint32)
        env = np.minimum(np.abs(z), 1.0)
        prev = np.concatenate([[self.z_prev], z[:-1]])
        f = np.angle(z * np.conj(prev)) * self.rate / (2 * np.pi)       # instantaneous frequency in Hz
        self.z_prev = z[-1]
        # the gain path later by `delay` updates (linear interpolation for a fraction)
        n = int(np.floor(self.delay))
        frac = self.delay - n
        hist = np.concatenate([self.a_tail, env])
        base = len(self.a_tail)
        a = (1 - frac) * hist[base - n: base - n + len(env)] + frac * hist[base - n - 1: base - n - 1 + len(env)]
        self.a_tail = hist[len(hist) - len(self.a_tail):]
        return pack(f / Q4_HZ, codes_for_levels(a, self.peak))

    def trim(self, ppm):
        self.up.trim = ppm


def idle_record(code=GAIN_WEAKEST):
    return np.array([code << 16], dtype=np.uint32)
