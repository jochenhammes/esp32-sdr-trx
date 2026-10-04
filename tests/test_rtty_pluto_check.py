"""scripts/rtty_pluto_check.py on a synthetic recording (an ideal FSK signal with noise): the tools that measured the transmitter must measure right."""
import importlib.util
import pathlib

import numpy as np
import pytest

from espdr import rtty, txmodes as tm

pytest.importorskip("scipy")
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rtty_pluto_check", ROOT / "scripts" / "rtty_pluto_check.py")
chk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chk)

FS = chk.FS


def synthetic(text, reverse=False, baud=45.45, shift=170.0, error_hz=13_000.0, noise=0.01):
    """A recording: 0.6 s of noise, the transmission as the transmitter makes it (records of 40 kHz, steps of 28.6 Hz), 0.6 s of noise."""
    stream = rtty.OffsetStream(rtty.message_frames(text, baud), 40000, baud, shift, reverse, 0.2)
    mod = tm.FskModulator(static_hz=0.0)
    q4 = np.concatenate([(mod.process(b) & 0xFFFF).astype(np.uint16).view(np.int16) for b in stream.blocks()]).astype(float)
    on = q4 * tm.Q4_HZ                                                  # the frequency of the tone against the low tone, as the chip makes it
    pad = int(0.6 * FS)
    n = int(len(on) * FS / 40000)
    f = np.interp(np.arange(n) * 40000.0 / FS, np.arange(len(on)), on, left=on[0], right=on[-1])
    f += chk.OFFSET_HZ + error_hz                                       # where the Pluto sees the low tone
    phase = 2 * np.pi * np.cumsum(f) / FS
    rng = np.random.default_rng(3)
    sig = np.exp(1j * phase).astype(np.complex64)
    quiet = lambda k: (noise * (rng.standard_normal(k) + 1j * rng.standard_normal(k)) / np.sqrt(2)).astype(np.complex64)
    iq = np.concatenate([quiet(pad), sig + quiet(len(sig)), quiet(pad)])
    dial = 2350e6
    return dict(iq=iq * 1000, fs=FS, center_hz=dial + 2125.0 - chk.OFFSET_HZ, dial_hz=dial, text=text, baud=baud, shift=shift, mark=2125.0, reverse=reverse), error_hz


@pytest.mark.parametrize("reverse", [False, True])
def test_the_analysis_finds_the_tones_and_the_text(reverse):
    rec, error = synthetic("RY DE TEST", reverse=reverse)
    lines = []
    r = chk.analyse_iq(rec, out=lines.append)
    assert r["ok"] and r["decoded"] == "RY DE TEST"
    assert abs(r["error_hz"] - error) < 8                                # the low tone is where the synthetic signal put it
    assert abs(r["shift_hz"] - 171.6) < 4                                 # whole record units: 6 x 28.6 Hz
    assert any("dBc/Hz" in x for x in lines)


def test_the_analysis_notices_wrong_text():
    rec, _ = synthetic("RY DE TEST")
    rec["text"] = "RY DE BEST"
    assert chk.analyse_iq(rec, out=lambda *_: None)["ok"] is False


def test_characters_come_out_of_the_log_of_pluto_cli(tmp_path):
    log = tmp_path / "rx.log"
    log.write_text("rade_open: x\nR  1 state:     search valid\n Y  2 state: candidate\n  3 state: search\n2195 state: x\nD  4 state:\nunrelated line\n")
    assert chk.chars_from_log(log) == "R Y2D"
