"""Signal processing for the transmitter, numpy only: streaming filters, a resampler with a controllable ratio, a speech AGC.

Everything works on blocks of any size and keeps its state between calls, so a microphone (or a pipe) can be processed as it arrives.
"""
import numpy as np


# ---- filter design (windowed sinc) -----------------------------------------------------------------------------------------

def _kaiser(n, beta):
    return np.kaiser(n, beta)


def lowpass(taps, cutoff, fs, beta=7.0):
    """Linear-phase low-pass, `taps` odd, `cutoff` in Hz."""
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff / fs * np.sinc(2 * cutoff / fs * n) * _kaiser(taps, beta)
    return h / h.sum()


def bandpass(taps, lo, hi, fs, beta=7.0):
    """Linear-phase band-pass with unity gain in the middle of the band."""
    n = np.arange(taps) - (taps - 1) / 2
    w = _kaiser(taps, beta)
    h = (2 * hi / fs * np.sinc(2 * hi / fs * n) - 2 * lo / fs * np.sinc(2 * lo / fs * n)) * w
    fc = 0.5 * (lo + hi)
    gain = abs(np.sum(h * np.exp(-2j * np.pi * fc / fs * n)))
    return h / gain


def hilbert(taps, beta=6.0):
    """Hilbert-transformer FIR (odd length, type III): positive frequencies get -90 degrees... applied as x + j*h(x) it gives the
    analytic signal of x delayed by (taps-1)/2 samples."""
    assert taps % 2 == 1
    m = (taps - 1) // 2
    n = np.arange(-m, m + 1)
    h = np.zeros(taps)
    odd = n % 2 != 0
    h[odd] = 2.0 / (np.pi * n[odd])
    return h * _kaiser(taps, beta)


# ---- streaming blocks --------------------------------------------------------------------------------------------------------

class Fir:
    """Streaming FIR filter: y[n] = sum h[k] x[n-k]; the output has the length of the input (the first taps-1 outputs see zeros)."""

    def __init__(self, h):
        self.h = np.asarray(h, dtype=float)
        self.tail = np.zeros(len(self.h) - 1)

    @property
    def delay(self):
        return (len(self.h) - 1) / 2

    def process(self, x):
        x = np.asarray(x)
        buf = np.concatenate([self.tail.astype(x.dtype, copy=False), x])
        y = np.convolve(buf, self.h, mode="valid")
        self.tail = buf[len(buf) - (len(self.h) - 1):]
        return y


class Delay:
    def __init__(self, n):
        self.tail = np.zeros(n)
        self.n = n

    def process(self, x):
        buf = np.concatenate([self.tail, x])
        self.tail = buf[len(buf) - self.n:] if self.n else buf[:0]
        return buf[:len(x)]


class Analytic:
    """x -> x + j*H{x} (positive frequencies only: the upper sideband when mixed up), delayed by `delay` samples."""

    def __init__(self, taps=255):
        self.h = Fir(hilbert(taps))
        self.d = Delay((taps - 1) // 2)
        self.delay = (taps - 1) // 2

    def process(self, x):
        return self.d.process(x) + 1j * self.h.process(x)


class Iir1:
    """First-order section y[n] = b0 x[n] + b1 x[n-1] - a1 y[n-1]."""

    def __init__(self, b0, b1, a1):
        self.b0, self.b1, self.a1 = b0, b1, a1
        self.x1 = self.y1 = 0.0

    def process(self, x):
        y = np.empty(len(x))
        x1, y1, b0, b1, a1 = self.x1, self.y1, self.b0, self.b1, self.a1
        for i, v in enumerate(x):
            y1 = b0 * v + b1 * x1 - a1 * y1
            x1 = v
            y[i] = y1
        self.x1, self.y1 = x1, y1
        return y


def preemphasis(fs, f_lo=300.0, f_hi=3000.0):
    """+6 dB per octave between f_lo and f_hi (the FM voice pre-emphasis), bilinear transform of (1 + s/w1) / (1 + s/w2)."""
    w1, w2 = 2 * np.pi * f_lo, 2 * np.pi * f_hi
    k = 2 * fs
    b0, b1 = (1 + k / w1), (1 - k / w1)
    a0, a1 = (1 + k / w2), (1 - k / w2)
    # normalise to unity gain at f_lo-ish (DC gain 1)
    return Iir1(b0 / a0, b1 / a0, a1 / a0)


class Resampler:
    """Streaming resampler for any ratio, real or complex input. Polyphase kernel table with linear interpolation between its entries
    (better than 70 dB image rejection); `trim_ppm` changes the ratio slightly (to follow a sound card's clock). The delay is `latency`
    input samples."""

    def __init__(self, fs_in, fs_out, taps=24, phases=128, beta=8.0, cutoff=0.45):
        self.step0 = fs_in / fs_out                      # input samples per output sample
        self.trim = 0.0
        scale = max(1.0, self.step0)                     # a downsampler needs a wider kernel in input samples
        self.k = int(np.ceil(taps * scale / 2)) * 2      # kernel length in input samples (even)
        self.phases = phases
        fc = cutoff / scale                              # cutoff in cycles per input sample
        j = np.arange(self.k * phases + 2)
        d = (j - self.k * phases / 2) / phases           # position in input samples, -k/2 .. k/2
        win = np.kaiser(self.k * phases + 2, beta)
        win[np.abs(d) > self.k / 2] = 0
        self.table = 2 * fc * np.sinc(2 * fc * d) * win
        self.buf = np.zeros(self.k)                      # history, so that the first outputs see silence
        self.base = -self.k                              # absolute index of buf[0]
        self.n_in = 0
        self.t = 0.0                                     # absolute input position of the next output
        self.latency = self.k / 2

    @property
    def step(self):
        return self.step0 * (1.0 + self.trim * 1e-6)

    def process(self, x):
        x = np.asarray(x)
        if len(x):
            self.buf = np.concatenate([self.buf.astype(np.result_type(self.buf, x)), x])
            self.n_in += len(x)
        half = self.k // 2
        out = []
        # outputs are possible while floor(t) + half is available
        last_ok = self.n_in - 1
        n_out = int(np.floor((last_ok - half - self.t) / self.step)) + 1 if last_ok - half >= self.t else 0
        if n_out > 0:
            t = self.t + self.step * np.arange(n_out)
            i0 = np.floor(t).astype(np.int64)
            frac = t - i0
            m = np.arange(-half + 1, half + 1)                   # k taps around floor(t)
            idx = i0[:, None] + m[None, :] - self.base           # position in buf
            jf = (frac[:, None] - m[None, :] + half) * self.phases
            j0 = np.floor(jf).astype(np.int64)
            w = jf - j0
            kern = (1 - w) * self.table[j0] + w * self.table[j0 + 1]
            out = np.sum(self.buf[idx] * kern, axis=1)
            self.t += self.step * n_out
        # keep only what later outputs can still touch
        keep_from = int(np.floor(self.t)) - half + 1
        drop = keep_from - self.base
        if drop > 0:
            self.buf = self.buf[drop:]
            self.base += drop
        return np.asarray(out) if n_out > 0 else np.zeros(0, dtype=self.buf.dtype)


class Agc:
    """Speech AGC and limiter for blocks: raises quiet speech up to `target` (a fraction of full scale) with a limited gain, follows
    peaks quickly and lets go slowly, and clips at +-1. Gain changes are ramped across the block."""

    def __init__(self, fs, target=0.5, max_gain_db=30.0, release_s=0.6, gain_db=0.0):
        self.fs, self.target = fs, target
        self.max_gain = 10 ** (max_gain_db / 20)
        self.release = release_s
        self.manual = 10 ** (gain_db / 20)
        self.env = 1e-3
        self.gain = 1.0

    def process(self, x):
        n = len(x)
        if n == 0:
            return x
        peak = float(np.max(np.abs(x))) * self.manual
        self.env = max(peak, self.env * np.exp(-n / self.fs / self.release))
        want = min(self.max_gain, self.target / max(self.env, 1e-6))
        new = want if want < self.gain else self.gain + (want - self.gain) * min(1.0, n / self.fs / 0.3)   # fast down, slow up
        g = np.linspace(self.gain, new, n, endpoint=False) * self.manual
        self.gain = new
        return np.clip(x * g, -1.0, 1.0)


def clip_magnitude(z, limit=1.0):
    """Limits the magnitude of a complex signal and keeps its phase."""
    mag = np.abs(z)
    scale = np.where(mag > limit, limit / np.maximum(mag, 1e-12), 1.0)
    return z * scale
