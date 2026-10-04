"""Helpers shared by the tests of the modulators."""
import numpy as np

from espdr import txmodes as tm


def ideal_polar(rec, rate=40000):
    """The signal an ideal transmitter makes of the records: the gain path responds one update before the frequency path."""
    q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16).astype(float)
    ph = 2 * np.pi * np.cumsum(q4 * tm.Q4_HZ) / rate
    amp = 10 ** (-(tm.RANGE_DB - tm.db_of_code(((rec >> 16) & 0xFF).astype(float))) / 20)
    return np.roll(amp, -1) * np.exp(1j * ph)


def run(mod, x):
    return np.concatenate([mod.process(x[i:i + 160]) for i in range(0, len(x), 160)])


def spectrum(s, rate=40000):
    s = s[8000:-2000]
    sp = np.abs(np.fft.fftshift(np.fft.fft(s * np.hanning(len(s)))))
    f = np.fft.fftshift(np.fft.fftfreq(len(s), 1 / rate))
    return lambda hz: sp[np.abs(f - hz) < 40].max()


def instantaneous_frequency(sig, rate=40000):
    """The frequency of a complex signal in Hz, one value less than samples."""
    return np.angle(sig[1:] * np.conj(sig[:-1])) * rate / (2 * np.pi)
