import importlib.util
import os
import pathlib

import numpy as np
import pytest

from espdr import cli_tx, rtty, txmodes as tm
from helpers import ideal_polar, instantaneous_frequency

RATE = 40000


def records(text, baud=45.45, shift=170.0, reverse=False, edge=0.2, static_hz=-1234.0, power_db=0.0):
    stream = rtty.OffsetStream(rtty.message_frames(text, baud), RATE, baud, shift, reverse, edge)
    mod = tm.FskModulator(rate=RATE, power_db=power_db, static_hz=static_hz)
    return np.concatenate([mod.process(b) for b in stream.blocks()])


# --- Baudot ------------------------------------------------------------------------------------------------------------------------------

def test_known_codes():
    assert rtty.text_to_codes("RYRY") == [10, 21, 10, 21]
    assert rtty.text_to_codes("A1 B") == [3, rtty.FIGS_SHIFT, 23, 4, 25]       # letters, figures shift, 1, space, B: no letters shift needed after the space
    assert rtty.text_to_codes("\r\n") == [8, 2]
    assert rtty.text_to_codes("cq") == rtty.text_to_codes("CQ")


def test_figures_and_letters_shifts():
    assert rtty.text_to_codes("1A") == [rtty.FIGS_SHIFT, 23, rtty.LTRS_SHIFT, 3]
    assert rtty.text_to_codes("1 2") == [rtty.FIGS_SHIFT, 23, 4, rtty.FIGS_SHIFT, 19]    # a space returns to letters (unshift on space)
    assert rtty.text_to_codes("1\r2") == [rtty.FIGS_SHIFT, 23, 8, 19]                      # CR and LF do not


def test_unknown_characters_become_a_question_mark():
    assert rtty.text_to_codes("~") == rtty.text_to_codes("?") == [rtty.FIGS_SHIFT, 25]


def test_text_survives_the_codes():
    text = "THE QUICK BROWN FOX 0123456789 -$'!:()#&./;,\"? 73\r\n"
    assert rtty.codes_to_text(rtty.text_to_codes(text)) == text


def test_frames_and_durations():
    frames = rtty.message_frames("E", 45.45)
    pre = round(45.45)
    assert frames[:pre] == [(1, 1.0)] * pre
    assert frames[pre:pre + 7] == [(0, 1.0), (1, 1.0), (0, 1.0), (0, 1.0), (0, 1.0), (0, 1.0), (1, 1.5)]      # start, E = 00001 (LSB first), stop
    assert frames[-1][0] == 1 and abs(frames[-1][1] - 0.2 * 45.45) < 1e-9
    assert abs(rtty.estimate_duration("E", 45.45) - (pre + 7.5 + 0.2 * 45.45) / 45.45) < 1e-9
    assert rtty.message_frames("") == rtty.message_frames(" ")


# --- the tone sequence ------------------------------------------------------------------------------------------------------------------

def test_stream_has_the_length_of_the_frames_and_no_drift():
    frames = rtty.message_frames("CQ CQ DE TEST", 45.45)
    s = rtty.OffsetStream(frames, RATE, 45.45, 170.0, False, 0.0)
    y = np.concatenate(list(s.blocks()))
    assert len(y) == round(sum(d for _, d in frames) * RATE / 45.45)
    assert set(np.unique(y)) <= {0.0, 170.0}


def test_mark_and_space_levels_and_reverse():
    frames = [(1, 4.0), (0, 4.0), (1, 4.0)]
    for reverse, mark_level in ((False, 0.0), (True, 170.0)):
        y = np.concatenate(list(rtty.OffsetStream(frames, 4000, 100.0, 170.0, reverse, 0.0).blocks()))
        assert y[80] == mark_level and y[240] == 170.0 - mark_level and y[400] == mark_level


def test_edges_are_smooth_and_centred_on_the_boundary():
    frames = [(1, 6.0), (0, 6.0)]
    spb = 400
    y = np.concatenate(list(rtty.OffsetStream(frames, 40000, 100.0, 170.0, False, 0.2).blocks()))
    boundary = 6 * spb
    assert abs(y[boundary] - 85.0) < 3                       # half way at the boundary
    assert y[boundary - 40] < 1.0 and y[boundary + 40] > 169.0   # the ramp is 0.2 bit = 80 updates wide
    ramp = y[boundary - 40:boundary + 40]
    assert np.all(np.diff(ramp) >= -1e-9)


def test_blocks_do_not_matter():
    s1 = rtty.OffsetStream(rtty.message_frames("RYRY"), RATE, 45.45, 170.0, False, 0.2)
    a = np.concatenate(list(s1.blocks(800)))
    s2 = rtty.OffsetStream(rtty.message_frames("RYRY"), RATE, 45.45, 170.0, False, 0.2)
    b = np.concatenate(list(s2.blocks(137)))
    assert np.allclose(a, b)
    assert s2.fraction() == 1.0


# --- the modulator and the whole chain ---------------------------------------------------------------------------------------------------

def test_records_stay_inside_the_firmware_limits_and_keep_the_gain_constant():
    rec = records("CQ CQ DE TEST 73", shift=850.0, static_hz=-17000.0)
    q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16)
    assert abs(q4).max() <= tm.MAX_Q4
    codes = (rec >> 16) & 0xFF
    assert len(set(codes.tolist())) == 1 and codes[0] == tm.peak_code(0.0)
    assert not ((rec >> 24) & 0xFF).any()


def test_power_setting_sets_the_gain_code():
    rec = records("E", power_db=-6.0)
    assert set(((rec >> 16) & 0xFF).tolist()) == {tm.peak_code(-6.0)}


def test_tone_distance_does_not_depend_on_the_constant():
    # the constant and the shift are rounded separately: the distance is the same whole number of record units wherever the constant falls
    for static in (-1234.0, -1234.0 + 7.0, 5.0, 14.0):
        rec = records("E", edge=0.0, static_hz=static)
        q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16)
        assert len(np.unique(q4)) == 2 and np.ptp(q4) == round(170.0 / tm.Q4_HZ)


@pytest.mark.parametrize("baud,shift", [(45.45, 170.0), (50.0, 170.0), (75.0, 425.0), (100.0, 850.0)])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("edge", [0.0, 0.2])
def test_text_comes_back_through_the_ideal_transmitter(baud, shift, reverse, edge):
    text = "CQ CQ DE TEST 73 (1.5-9)"
    f = instantaneous_frequency(ideal_polar(records(text, baud, shift, reverse, edge)), RATE)
    assert rtty.decode_frequency(f, RATE, baud, reverse) == text


def test_decoder_copes_with_noise():
    text = "RYRY DE TEST 123"
    f = instantaneous_frequency(ideal_polar(records(text)), RATE)
    f = f + np.random.default_rng(1).normal(0, 20, len(f))
    assert rtty.decode_frequency(f, RATE) == text


def test_wrong_polarity_does_not_decode():
    f = instantaneous_frequency(ideal_polar(records("RYRY DE TEST")), RATE)
    assert rtty.decode_frequency(f, RATE, reverse=True) != "RYRY DE TEST"


# --- where the tones go ------------------------------------------------------------------------------------------------------------------

def plan(freq, ppm=0.0, mark=2125.0, shift=170.0):
    args = cli_tx.build_parser().parse_args(["-f", str(freq), "-m", "rtty", "--text", "E", "--mark-hz", str(mark), "--shift-hz", str(shift),
                                             "--ppm", str(ppm)])
    return cli_tx._rtty_plan(args), args


def test_the_lower_tone_lands_on_the_wanted_frequency():
    off = []
    for k in range(200):
        p, a = plan(2350.0 + k * 0.0001)
        off.append(p["miss_hz"])
    off = np.array(off)
    assert np.mean(np.abs(off) <= 15.0) > 0.9                 # whole record units and the PLL word grid: within about 15 Hz
    assert np.mean(np.abs(off) > 15.0) < 0.1                  # the rest are the frequencies where the move of the LO is larger than the records reach


def test_the_constant_makes_up_for_the_moved_lo():
    p, a = plan(2385.0)                                      # the LO moves by 21.8 kHz here (see the selftest): beyond the reach of the records
    assert abs(p["miss_hz"]) > 100
    moved = [plan(2350.0 + k * 0.0005)[0] for k in range(400)]
    moved = [p for p in moved if abs(p["static_hz"]) > 1000]   # frequencies where choose_lo moved the LO by more than 1 kHz
    assert len(moved) > 20
    assert all(abs(p["miss_hz"]) <= 15.0 for p in moved if abs(p["static_hz"]) < 18000)      # compensated by the constant in every record


def test_the_crystal_error_is_corrected():
    p0, _ = plan(2350.0, ppm=0.0)
    p1, _ = plan(2350.0, ppm=5.4)
    # with --ppm the board is asked for lower frequencies so that it ends up on the wanted one: the programmed tone is 5.4 ppm lower,
    # the one on the air stays within the same 15 Hz of the wanted frequency
    assert abs(p1["miss_hz"]) <= 15.0
    assert (p0["lo_hz"] - p1["lo_hz"]) > 10_000


# --- the same rules as pluto-tx ----------------------------------------------------------------------------------------------------------

def _pluto_rtty():
    root = pathlib.Path(os.environ.get("PLUTO_TX_DIR", pathlib.Path.home() / "Dokumente" / "plutosdr"))
    path = root / "pluto_tx" / "rtty.py"
    if not path.exists():
        pytest.skip(f"pluto-tx not found at {path} (set PLUTO_TX_DIR)")
    spec = importlib.util.spec_from_file_location("pluto_tx_rtty_for_comparison", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TEXTS = ["RYRY", "CQ CQ CQ DE DA2JH DA2JH K", "A1 B", "1 2 3 4 5", "the quick brown fox", "Hello, World! 73 (OK)?", "WX: 12.5 C; 1013 hPa/QNH",
         "x\r\ny\r\n", "~@^_{}|", "Ärger 123 ÄÖÜ", "'quote' \"double\" #1 $5 &6", "A  B   C", "9" * 20 + "A" * 20, "\a bell"]


def test_tables_and_codes_equal_pluto_tx():
    p = _pluto_rtty()
    assert rtty._LTRS == p._LTRS_TABLE and rtty._FIGS == p._FIGS_TABLE
    assert (rtty.FIGS_SHIFT, rtty.LTRS_SHIFT) == (p.FIGS_SHIFT, p.LTRS_SHIFT)
    for t in TEXTS:
        assert rtty.text_to_codes(t) == p._text_to_baudot_codes(t), t


@pytest.mark.parametrize("baud", [45.45, 50.0, 75.0, 100.0])
def test_frames_equal_pluto_tx(baud):
    p = _pluto_rtty()
    for t in TEXTS:
        mine = rtty.message_frames(t, baud, preamble_s=1.0, tail_s=0.0, stop_bits=1.5)
        theirs = p._preamble_frames(1.0, baud) + p._codes_to_frames(p._text_to_baudot_codes(t), 1.5)
        assert mine == theirs, t
