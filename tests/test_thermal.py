import json

import numpy as np

from espdr import cli_tx, thermal, txmodes as tm


def test_the_error_is_zero_at_the_turning_point_and_grows_away_from_it():
    t0 = thermal.DEFAULT["t0"]
    p = dict(thermal.DEFAULT, t_inf=t0, early_hz=0.0)           # a chip that stays at T0, without the switch-on rise
    assert np.allclose(thermal.error_hz(np.array([0.0, 10.0, 100.0]), t0, p), 0.0)
    p = dict(thermal.DEFAULT)
    assert thermal.error_hz(0.0, 42.0, p) > thermal.error_hz(0.0, 45.0, p) > 0                  # colder start, larger error
    assert thermal.error_hz(150.0, 42.0, p) > 50                                                  # hot end: 56 C is far above the turning point


def test_the_error_scales_with_the_carrier_frequency():
    p = dict(thermal.DEFAULT)
    assert abs(thermal.error_hz(0.0, 42.0, p, 1175e6) / thermal.error_hz(0.0, 42.0, p, 2350e6) - 0.5) < 1e-9


def test_crystal_temperature_starts_at_the_start_temperature_and_heads_to_the_end():
    p = dict(thermal.DEFAULT)
    t = np.array([0.0, 1e5])
    c = thermal.crystal_temperature(t, 42.0, p)
    assert abs(c[0] - 42.0) < 1e-9 and abs(c[1] - p["t_inf"]) < 1e-6
    assert np.all(np.diff(thermal.crystal_temperature(np.linspace(0, 300, 50), 42.0, p)) > 0)


def test_equal_time_constants_do_not_divide_by_zero():
    p = dict(thermal.DEFAULT, tau_xtal=thermal.DEFAULT["tau_chip"])
    assert np.isfinite(thermal.crystal_temperature(np.array([0.0, 10.0]), 42.0, p)).all()


def test_apply_adds_the_opposite_of_the_error_and_keeps_gain_and_flags():
    n = 40000 * 20
    rec = tm.pack(np.full(n, 100.0), np.full(n, 90, dtype=np.uint32))
    rec[-1] |= np.uint32(1 << 24)
    c = thermal.Thermal(42.0, 40000, 2350e6)
    out = np.concatenate([c.apply(rec[i:i + 1000]) for i in range(0, n, 1000)])
    q4 = (out & 0xFFFF).astype(np.uint16).view(np.int16).astype(float)
    want = 100.0 - thermal.error_hz(np.arange(n) / 40000.0, 42.0, c.p) / tm.Q4_HZ
    assert np.max(np.abs(np.cumsum(q4 - want))) < 1.0                    # the rounding error is carried: the sum never leaves by more than one unit
    assert np.all(((out >> 16) & 0xFF) == 90) and (out[-1] >> 24) & 1 and not ((out[:-1] >> 24) & 1).any()


def test_apply_clips_at_the_record_limit():
    rec = tm.pack(np.full(100, tm.MAX_Q4), np.full(100, 90, dtype=np.uint32))
    out = thermal.Thermal(42.0, 40000, 2350e6, dict(thermal.DEFAULT, k=1e6)).apply(rec)
    assert (out & 0xFFFF).astype(np.uint16).view(np.int16).max() <= tm.MAX_Q4


def test_the_model_can_be_loaded_from_a_file(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"k": 7.0, "t0": 45.0, "bogus": 1}))
    p = thermal.load_params(str(f))
    assert p["k"] == 7.0 and p["t0"] == 45.0 and "bogus" not in p and p["tau_chip"] == thermal.DEFAULT["tau_chip"]


def test_the_chip_temperature_comes_through_the_simulated_link():
    from espdr import nb, sim, txlink
    for rng in (1, 2, 3):
        assert abs(txlink.Session(nb.Link(sim.SimTx())).chip_temperature() - 42.0) < 0.5


def test_a_simulated_session_with_the_correction_plays_and_ends(tmp_path):
    import io
    args = cli_tx.build_parser().parse_args(["-f", "2350", "-m", "fm", "--test-tone", "1000", "--duration", "1", "--thermal", "nominal", "-q", "--dry-run"])
    out = io.StringIO()
    assert cli_tx.run(args, out) == 0
