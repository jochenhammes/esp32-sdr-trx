import numpy as np

from espdr import dsp


def test_resampler_down_keeps_a_tone_clean_in_odd_blocks():
    fs = 48000
    x = np.sin(2 * np.pi * 1000 * np.arange(fs) / fs)
    r = dsp.Resampler(fs, 8000)
    rng = np.random.default_rng(1)
    out, i = [], 0
    while i < len(x):
        n = int(rng.integers(100, 2000))
        out.append(r.process(x[i:i + n]))
        i += n
    y = np.concatenate(out)
    assert abs(np.abs(y[200:]).max() - 1.0) < 0.01
    spec = np.abs(np.fft.rfft(y[500:7500] * np.hanning(7000)))
    assert 20 * np.log10(np.sort(spec)[-20] / spec.max()) < -80


def test_resampler_up_complex_has_no_images():
    x = np.exp(2j * np.pi * 1500 * np.arange(8000) / 8000)
    r = dsp.Resampler(8000, 40000)
    y = np.concatenate([r.process(x[i:i + 160]) for i in range(0, 8000, 160)])
    assert abs(np.abs(y[2000:-200]).mean() - 1.0) < 0.01
    spec = np.abs(np.fft.fft(y[2000:2000 + 32768] * np.hanning(32768)))
    f = np.fft.fftfreq(32768, 1 / 40000)
    far = np.abs(f - 1500) > 4000
    assert 20 * np.log10(spec[far].max() / spec.max()) < -70


def test_resampler_trim_changes_the_ratio():
    x = np.ones(8000, dtype=complex)
    a, b = dsp.Resampler(8000, 40000), dsp.Resampler(8000, 40000)
    b.trim = 1000.0                                   # 1000 ppm: a thousandth fewer outputs
    na = sum(len(a.process(x[i:i + 160])) for i in range(0, 8000, 160))
    nb_ = sum(len(b.process(x[i:i + 160])) for i in range(0, 8000, 160))
    assert 30 <= na - nb_ <= 50


def test_analytic_signal_has_one_sideband():
    for f in (300, 1000, 2700):
        z = dsp.Analytic(255).process(np.sin(2 * np.pi * f * np.arange(16000) / 8000))
        spec = np.abs(np.fft.fft(z[300:300 + 4096] * np.hanning(4096)))
        fr = np.fft.fftfreq(4096, 1 / 8000)
        wanted, mirror = spec[np.argmin(abs(fr - f))], spec[np.argmin(abs(fr + f))]
        assert 20 * np.log10(wanted / mirror) > 60


def test_bandpass_passes_the_voice_band_and_stops_hum():
    h = dsp.bandpass(255, 300, 2700, 8000)
    w = np.fft.rfftfreq(4096, 1 / 8000)
    resp = np.abs(np.fft.rfft(h, 4096))
    assert abs(20 * np.log10(resp[np.argmin(abs(w - 1000))])) < 1.0
    assert 20 * np.log10(resp[np.argmin(abs(w - 50))]) < -40


def test_agc_raises_quiet_speech_and_never_clips_above_one():
    agc = dsp.Agc(8000)
    quiet = 0.02 * np.sin(2 * np.pi * 500 * np.arange(8000) / 8000)
    out = np.concatenate([agc.process(quiet[i:i + 160]) for i in range(0, 8000, 160)])
    assert np.abs(out[-800:]).max() > 0.2
    loud = 5 * np.sin(2 * np.pi * 500 * np.arange(1600) / 8000)
    assert np.abs(agc.process(loud)).max() <= 1.0


def test_preemphasis_rises_6db_per_octave():
    p = dsp.preemphasis(8000)
    def gain(f):
        x = np.sin(2 * np.pi * f * np.arange(8000) / 8000)
        return np.abs(p.process(x)[4000:]).max()
    g = [gain(f) for f in (300, 600, 1200, 2400)]
    for lo, hi in zip(g, g[1:]):
        assert 1.5 < hi / lo < 2.2
