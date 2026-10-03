import pytest

from espdr import board


def test_kind_of_build_flags():
    assert board.kind_of(None) is None
    assert board.kind_of(1) == board.RX
    assert board.kind_of(3) == board.TX


def test_ensure_returns_the_port_when_the_right_firmware_runs(monkeypatch):
    monkeypatch.setattr(board, "native_ports", lambda: ["/dev/ttyACM1"])
    monkeypatch.setattr(board, "probe", lambda port, timeout=1.5: 3)
    monkeypatch.setattr(board, "load_ram", lambda *a, **k: pytest.fail("must not load"))
    assert board.ensure(board.TX) == "/dev/ttyACM1"


def test_ensure_loads_when_another_firmware_runs(monkeypatch):
    state = {"flags": 1, "loaded": 0}
    monkeypatch.setattr(board, "native_ports", lambda: ["/dev/ttyACM1"])
    monkeypatch.setattr(board, "probe", lambda port, timeout=1.5: state["flags"])

    def fake_load(kind, **k):
        state["flags"], state["loaded"] = (3 if kind == board.TX else 1), state["loaded"] + 1
        return "/dev/ttyACM1"
    monkeypatch.setattr(board, "load_ram", fake_load)
    assert board.ensure(board.TX, log=lambda m: None) == "/dev/ttyACM1"
    assert state["loaded"] == 1


def test_ensure_with_no_load_explains_what_is_wrong(monkeypatch):
    monkeypatch.setattr(board, "native_ports", lambda: ["/dev/ttyACM1"])
    monkeypatch.setattr(board, "probe", lambda port, timeout=1.5: 1)
    with pytest.raises(board.BoardError, match="receiver firmware is running, not the transmitter"):
        board.ensure(board.TX, load="never")
    monkeypatch.setattr(board, "native_ports", lambda: [])
    with pytest.raises(board.BoardError, match="no native USB port"):
        board.ensure(board.RX, load="never")


def test_load_without_a_bridge_says_how_to_continue(monkeypatch):
    monkeypatch.setattr(board, "bridge_ports", lambda: [])
    monkeypatch.setattr(board, "image_path", lambda kind, override=None: "x.bin")
    with pytest.raises(board.BoardError, match="UART"):
        board.load_ram(board.RX)


def test_several_bridges_need_a_choice(monkeypatch):
    monkeypatch.setattr(board, "bridge_ports", lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"])
    with pytest.raises(board.BoardError, match="--bridge-port"):
        board.find_bridge()
