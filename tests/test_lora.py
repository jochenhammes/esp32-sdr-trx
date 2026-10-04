import random

import numpy as np
import pytest

from espdr import cli_tx, lora_phy as lp, nb, sim, txlink as tl, txmodes as tm


def ideal_fm(track, rate, fs):
    """What a perfect FM transmitter makes of the per-update frequencies: zero-order hold, integrated to a complex signal at fs."""
    t = np.arange(int(len(track) / rate * fs))
    f = track[np.minimum((t / fs * rate).astype(int), len(track) - 1)]
    return np.exp(2j * np.pi * np.cumsum(f) / fs).astype(np.complex64)


def test_numpy_phy_round_trip_with_offsets():
    rng = random.Random(1)
    for sf, bw, os_ in ((5, 4e6, 4), (7, 125e3, 4), (8, 62.5e3, 8)):
        for cr in (1, 4):
            payload = bytes(rng.randrange(32, 127) for _ in range(12))
            iq = lp.frame_iq(payload, sf, bw, os_, cr)
            t = np.arange(len(iq)) / (bw * os_)
            sig = np.concatenate([np.zeros(777, np.complex64), iq * np.exp(2j * np.pi * 0.01 * bw * t), np.zeros(4000, np.complex64)])
            r = lp.demodulate(sig, bw * os_, sf, bw)
            assert r and r["crc_ok"] and r["payload"] == payload, (sf, bw, cr)


def test_known_values_of_the_chain():
    assert lp.whitening_sequence()[:8] == [0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE1, 0xC2, 0x85]
    assert lp.sync_symbols(0x34) == [24, 32]
    assert lp.header_nibbles(12, 1, True)[:3] == [0, 12, 3]
    assert lp.symbols_per_frame(12, 5) == 50.25 and lp.symbols_per_frame(16, 5) == 60.25     # the frame times of the plan


@pytest.mark.parametrize("sf,bw", [(7, 62500.0), (8, 62500.0), (9, 31250.0)])
def test_the_polar_track_decodes(sf, bw):
    rng = random.Random(sf)
    for _ in range(5):
        payload = bytes(rng.randrange(32, 127) for _ in range(10))
        track = lp.frame_track(payload, sf, bw, 40000.0)
        assert abs(track).max() <= bw / 2 * 1.001
        fs = 8 * bw
        sig = np.concatenate([np.zeros(500, np.complex64), ideal_fm(track, 40000.0, fs), np.zeros(8000, np.complex64)])
        r = lp.demodulate(sig, fs, sf, bw)
        assert r and r["crc_ok"] and r["payload"] == payload


def test_the_records_stay_inside_the_widened_range():
    payload = b"DA2JH TEST"
    track = lp.frame_track(payload, 8, 62500.0, 40000.0)
    mod = tm.FskModulator(rate=40000, power_db=0.0, static_hz=0.0, range_steps=72)
    rec = mod.process(track)
    q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16)
    assert abs(q4).max() <= 70 * 16 and abs(q4).max() > 60 * 16
    assert len({int(r >> 16) & 0xFF for r in rec}) == 1
    narrow = tm.FskModulator(rate=40000, power_db=0.0).process(track)
    assert abs((narrow & 0xFFFF).astype(np.uint16).view(np.int16)).max() == tm.MAX_Q4        # the default range clips a chirp this wide


def args_for(*extra):
    return cli_tx.build_parser().parse_args(["-f", "2350", "-m", "lora", "--text", "DA2JH", *extra])


@pytest.mark.parametrize("freq", [2350.0, 2350.05, 2381.234, 2399.99, 2420.5, 2449.9])
def test_the_lora_centre_keeps_the_sweep_inside_one_byte(freq):
    a = cli_tx.build_parser().parse_args(["-f", str(freq), "-m", "lora", "--text", "x", "--ppm", "5.4"])
    p = cli_tx._lora_plan(a)
    word = tl.lo_word(p["lo_hz"])
    margin = p["range_steps"] + 4
    assert margin <= word & 0xFF <= 255 - margin
    assert p["range_steps"] >= 72 and abs(p["moved_hz"]) < 60e3


def test_lora_argument_errors():
    for bad in (["--sf", "4"], ["--lora-bw", "125000"], ["--lora-cr", "5"], ["--lora-preamble", "2"]):
        with pytest.raises(SystemExit):
            cli_tx._check_lora(args_for(*bad))
    with pytest.raises(SystemExit):
        cli_tx._check_lora(cli_tx.build_parser().parse_args(["-f", "2350", "-m", "lora"]))


def test_the_simulated_chip_takes_the_range_and_checks_the_margin():
    esp = sim.SimTx()
    s = tl.Session(nb.Link(esp))
    a = args_for()
    p = cli_tx._lora_plan(a)
    s.configure(p["lo_hz"], 40000, 0, 60, range_steps=p["range_steps"])
    assert s.begin(expect_word=tl.lo_word(p["lo_hz"])) == tl.lo_word(p["lo_hz"])
    esp2 = sim.SimTx()
    s2 = tl.Session(nb.Link(esp2))
    s2.configure(p["lo_hz"], 40000, 0, 60)                      # the default range: this centre may be too close to the end of the byte
    low = tl.lo_word(p["lo_hz"]) & 0xFF
    if low < 48 or low > 207:
        with pytest.raises(tl.TxError):
            s2.begin()
