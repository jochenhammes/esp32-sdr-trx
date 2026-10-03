import numpy as np
import pytest

from espdr import txmodes as tm


def ideal_polar(rec, rate=40000):
    """The signal an ideal transmitter makes of the records: the gain path responds one update before the frequency path."""
    q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16).astype(float)
    ph = 2 * np.pi * np.cumsum(q4 * tm.Q4_HZ) / rate
    amp = 10 ** (-(tm.RANGE_DB - tm.db_of_code(((rec >> 16) & 0xFF).astype(float))) / 20)
    return np.roll(amp, -1) * np.exp(1j * ph)


def run(mod, x):
    return np.concatenate([mod.process(x[i:i + 160]) for i in range(0, len(x), 160)])


def two_tone(f1=700, f2=1700, secs=1.0):
    t = np.arange(int(8000 * secs)) / 8000.0
    return 0.5 * np.sin(2 * np.pi * f1 * t) + 0.5 * np.sin(2 * np.pi * f2 * t)


def spectrum(s, rate=40000):
    s = s[8000:-2000]
    sp = np.abs(np.fft.fftshift(np.fft.fft(s * np.hanning(len(s)))))
    f = np.fft.fftshift(np.fft.fftfreq(len(s), 1 / rate))
    return lambda hz: sp[np.abs(f - hz) < 40].max()


@pytest.mark.parametrize("carrier", [0.55, 0.05, 0.0])
def test_usb_has_a_clean_upper_sideband(carrier):
    line = spectrum(ideal_polar(run(tm.SsbModulator(carrier=carrier, agc=False), two_tone())))
    wanted = min(line(700), line(1700))
    assert 20 * np.log10(wanted / max(line(-700), line(-1700))) > 45
    assert 20 * np.log10(wanted / max(line(-300), line(2700))) > 30      # third-order products


def test_lsb_mirrors_usb():
    line = spectrum(ideal_polar(run(tm.SsbModulator(sideband="lsb", carrier=0.05, agc=False), two_tone())))
    assert 20 * np.log10(min(line(-700), line(-1700)) / max(line(700), line(1700))) > 45


def test_records_stay_inside_the_firmware_limits():
    rec = run(tm.SsbModulator(carrier=0.0), two_tone(secs=2))
    q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16)
    assert abs(q4).max() <= tm.MAX_Q4
    codes = (rec >> 16) & 0xFF
    assert codes.min() >= tm.GAIN_STRONGEST and codes.max() <= tm.GAIN_WEAKEST
    assert not ((rec >> 24) & 0xFF).any()


def test_fm_deviation_follows_the_audio_level():
    m = tm.FmModulator(deviation=2500, agc=False, preemph=False)
    x = 0.8 * np.sin(2 * np.pi * 1000 * np.arange(8000) / 8000)
    rec = run(m, x)
    dev = abs((rec[len(rec) // 2:] & 0xFFFF).astype(np.uint16).view(np.int16)).max() * tm.Q4_HZ
    assert 1800 < dev < 2250
    assert len({int((r >> 16) & 0xFF) for r in rec}) == 1               # constant gain code in FM


def test_power_setting_maps_to_gain_codes():
    assert [tm.peak_code(p) for p in (0, -6, -12)] == [64, 87, 107]
    with pytest.raises(ValueError):
        tm.peak_code(1.0)
    with pytest.raises(ValueError):
        tm.peak_code(-20.0)


def test_weaker_power_shrinks_the_ssb_envelope_range():
    strong = tm.SsbModulator(power_db=0, agc=False)
    weak = tm.SsbModulator(power_db=-12, agc=False)
    x = two_tone()
    cs, cw = (run(strong, x) >> 16) & 0xFF, (run(weak, x) >> 16) & 0xFF
    assert cs.min() == 64 and cw.min() >= 107


def test_deviation_limit():
    with pytest.raises(ValueError):
        tm.FmModulator(deviation=40000)
