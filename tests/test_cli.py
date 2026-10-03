import pytest

from espdr import cli_rx, cli_tx


@pytest.mark.parametrize("build", [cli_rx.build_parser, cli_tx.build_parser])
def test_every_option_has_help_text(build):
    p = build()
    for a in p._actions:
        assert a.help, f"{a.option_strings} has no help text"


@pytest.mark.parametrize("build,name", [(cli_rx.build_parser, "espdr-rx"), (cli_tx.build_parser, "espdr-tx")])
def test_help_works_and_shows_examples(build, name, capsys):
    with pytest.raises(SystemExit) as e:
        build().parse_args(["-h"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert name in out and "examples:" in out


def test_tx_needs_a_frequency_and_a_source(capsys):
    with pytest.raises(SystemExit) as e:
        cli_tx.main(["-i", "x.wav"])
    assert "--freq" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli_tx.main(["-f", "2350"])
    assert "audio source" in str(e.value)


@pytest.mark.parametrize("f", ["2000", "2500", "2319.9", "2450.1"])
def test_tx_refuses_frequencies_outside_the_band(f):
    with pytest.raises(SystemExit) as e:
        cli_tx.main(["-f", f, "--test-tone", "1000", "--dry-run", "--accept-licence"])
    assert "outside" in str(e.value)


def test_tx_asks_for_the_licence_confirmation_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as e:
        cli_tx.main(["-f", "2350", "--test-tone", "1000", "--dry-run"])
    assert "--accept-licence" in str(e.value)
    assert cli_tx.main(["-f", "2350", "--test-tone", "1000", "--dry-run", "--accept-licence"]) == 0
    assert cli_tx.main(["-f", "2350", "--test-tone", "1000", "--dry-run"]) == 0       # remembered


def test_tx_dry_run_touches_no_board(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    called = []
    monkeypatch.setattr("espdr.board.ensure", lambda *a, **k: called.append(1))
    assert cli_tx.main(["-f", "2385", "-m", "usb", "--test-tone", "800", "--accept-licence", "--dry-run", "--power", "-6"]) == 0
    assert not called
    err = capsys.readouterr().err
    assert "USB voice" in err and "dry run" in err


def test_rx_selftest_runs():
    assert cli_rx.main(["--selftest"]) == 0


def test_tx_selftest_runs():
    assert cli_tx.main(["--selftest"]) == 0
