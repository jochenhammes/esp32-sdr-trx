import queue
import threading

import numpy as np
import pytest

from espdr import nb, sim, txlink as tl, txmodes as tm


def records(seconds=1.0, carrier=0.05):
    t = np.arange(int(8000 * seconds)) / 8000.0
    x = 0.5 * np.sin(2 * np.pi * 700 * t) + 0.5 * np.sin(2 * np.pi * 1700 * t)
    m = tm.SsbModulator(carrier=carrier)
    return [m.process(x[i:i + 160]) for i in range(0, len(x), 160)]


def start(esp, rate=40000, limit=60):
    lo, _ = tl.choose_lo(2_350_000_000)
    s = tl.Session(nb.Link(esp))
    s.configure(lo, rate, 210, limit)
    s.begin(expect_word=tl.lo_word(lo))
    return s


def test_lo_word_matches_the_firmware_formula():
    assert tl.lo_word(2_350_000_000) == 0x2E5555                          # read back from the chip in the research
    assert tl.lo_word(2_400_000_000) & 0xFFFFFF == tl.lo_word(2_400_000_000)


@pytest.mark.parametrize("hz", [2_320_000_000, 2_350_000_000, 2_385_000_000, 2_400_123_400, 2_449_999_000])
def test_choose_lo_keeps_the_low_byte_inside_the_margin(hz):
    lo, shift = tl.choose_lo(hz)
    assert tl.TX_LOW_MARGIN <= tl.lo_word(lo) & 0xFF <= 255 - tl.TX_LOW_MARGIN
    assert abs(shift) <= 23_000 and lo == hz + shift


def test_a_session_plays_every_record_in_order():
    recs = records(2.0)
    esp = sim.SimTx()
    s = start(esp)
    q = queue.Queue()
    for r in recs:
        q.put(r)
    q.put(None)
    summary = s.stream(q)
    sent = np.concatenate(recs)
    assert summary["reason"] == tl.END_REASONS and False or summary["reason"] == 0
    assert summary["underruns"] == 0
    assert np.array_equal(np.array(esp.played[:len(sent)], dtype=np.uint32), sent)
    assert (esp.played[-1] >> 24) & 1                                     # the end record


def test_the_chip_refuses_a_frequency_near_a_byte_boundary():
    esp = sim.SimTx()
    s = tl.Session(nb.Link(esp))
    bad = None
    for hz in range(2_400_000_000, 2_400_130_000, 1000):
        if not tl.TX_LOW_MARGIN <= tl.lo_word(hz) & 0xFF <= 255 - tl.TX_LOW_MARGIN:
            bad = hz
            break
    s.configure(bad, 40000)
    with pytest.raises(tl.TxError):
        s.begin()


def test_a_receiver_only_firmware_has_no_transmit_ops():
    esp = sim.SimTx(flags=1)
    status, _ = nb.Link(esp).command(tl.TX_OP_LO, 1, allow=(0, nb.CTL_UNKNOWN_OP))
    assert status == nb.CTL_UNKNOWN_OP


def test_the_stop_event_ends_the_transmission_cleanly():
    esp = sim.SimTx(speed=1.0)
    s = start(esp)
    q = queue.Queue()
    ev = threading.Event()
    threading.Timer(0.6, ev.set).start()
    q.put(np.full(30000, 100 << 16, dtype=np.uint32))                      # 0.75 s of audio, then no more
    summary = s.stream(q, stop=ev)
    assert summary["reason"] == 0
    assert (esp.played[-1] >> 24) & 1


def test_the_time_limit_ends_the_session():
    esp = sim.SimTx()
    s = start(esp, rate=8000, limit=1)
    q = queue.Queue()
    for _ in range(20):
        q.put(np.full(2000, 90 << 16, dtype=np.uint32))
    summary = s.stream(q)
    assert summary["reason"] == 2 and len(esp.played) == 8000


def test_frames_parser_resyncs_after_garbage():
    f = tl.Frames()
    status = bytearray([tl.TX_STATUS_MAGIC, 7, 0x10, 0x00, 1, 2, 1, 0])
    status[7] = (0x100 - sum(status[:7])) & 0xFF
    ev = f.feed(b"\x00\xb6\x01" + bytes(status[:4]))
    ev += f.feed(bytes(status[4:]))
    assert any(k == "status" and e["fill"] == 16 and e["underruns"] == 1 for k, e in ev)
