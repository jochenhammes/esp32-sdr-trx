"""espdr-tx: transmit FM or SSB voice from an ESP32-S3 board on the 13 cm band."""
import argparse
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__, audio, board, nb, txlink, txmodes

LICENCE_NOTICE = """\
Transmitting takes an amateur radio licence, and you are responsible for what you send. This transmitter is a Wi-Fi chip's
test tone, steered in frequency and amplitude; it was measured on one board only. Its power is not calibrated (some microwatts
at the antenna of a plain dev board, an estimate), its harmonics and spurious emissions were not measured, and its frequency
is only as good as the board's crystal (up to +-24 kHz; correct it with --ppm). It sends only inside 2320..2450 MHz.
"""

EPILOG = """\
examples:
  espdr-tx -f 2350.000 -i speech.wav                         FM voice from a WAV file
  espdr-tx -f 2350.000 -m usb -i speech.wav                  the same as single sideband (upper)
  espdr-tx -f 2350.000 -m usb -i soundcard                   SSB from the default sound card input
  espdr-tx -f 2350.000 -m fm -i soundcard:2 --power -6       FM from input device 2, 6 dB below the strongest setting
  arecord -f S16_LE -r 16000 -c 1 | espdr-tx -f 2350 -m usb -i - --rate 16000     raw samples from a pipe
  sox music.mp3 -t wav - | espdr-tx -f 2350 -i -             a WAV stream from a pipe
  espdr-tx -f 2350 --test-tone 1000 --duration 5             a 1 kHz tone for 5 seconds
  espdr-tx -f 2350 -i speech.wav --dry-run                   show what would happen, send nothing
  espdr-tx --list-devices                                    list sound card inputs

The first time, add --accept-licence (the notice you accept is printed). The transmitter firmware is loaded into the board's RAM
automatically when it is not running (this needs the board's UART USB port as well; --no-load forbids it). A reset, or
`--restore`, brings back the receiver from the flash.
"""


def _config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "espdr"


def _licence_accepted():
    return (_config_dir() / "licence-accepted").exists()


def _accept_licence():
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "licence-accepted").write_text(f"accepted with espdr-tx {__version__}\n")


def build_parser():
    p = argparse.ArgumentParser(prog="espdr-tx", description=__doc__, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-V", "--version", action="version", version=f"espdr-tx {__version__}")
    main = p.add_argument_group("what to send")
    main.add_argument("-f", "--freq", type=float, metavar="MHZ", help="carrier frequency in MHz (2320 .. 2450); required")
    main.add_argument("-m", "--mode", choices=("fm", "usb", "lsb"), default="fm", help="fm: narrowband FM; usb or lsb: single sideband (default fm)")
    src = p.add_argument_group("audio source")
    src.add_argument("-i", "--input", metavar="SOURCE",
                     help="a WAV file, '-' for a pipe on stdin (a WAV stream or raw samples), or 'soundcard' / 'soundcard:DEVICE' "
                          "(DEVICE is a number or part of a name; see --list-devices)")
    src.add_argument("--test-tone", type=float, metavar="HZ", help="send a sine tone instead of an audio source")
    src.add_argument("--list-devices", action="store_true", help="list the sound card inputs and exit")
    src.add_argument("--rate", type=int, metavar="HZ", help="sample rate of raw audio on stdin (required there; sound cards use theirs)")
    src.add_argument("--channels", type=int, default=1, metavar="N", help="channels of raw audio on stdin, mixed to mono (default 1)")
    src.add_argument("--format", default="s16le", choices=sorted(audio.RAW_FORMATS), help="sample format of raw audio on stdin (default s16le)")
    src.add_argument("--loop", action="store_true", help="repeat a file until --duration or Ctrl-C")
    src.add_argument("--gain", type=float, default=0.0, metavar="DB", help="raise (or lower) the audio before the automatic level control (default 0)")
    src.add_argument("--no-agc", action="store_true", help="no automatic level control: the audio is only clipped at full scale")
    lvl = p.add_argument_group("level and tuning")
    lvl.add_argument("--power", type=float, default=0.0, metavar="DB",
                     help="transmit power in dB against the strongest setting, 0 .. -17.9 (default 0). A weaker setting leaves SSB "
                          "less amplitude range and costs quality; the absolute power is not calibrated")
    lvl.add_argument("--ppm", type=float, default=0.0, metavar="PPM",
                     help="the board's crystal error: positive if the board transmits too high. Measure it against a known signal")
    fmg = p.add_argument_group("FM")
    fmg.add_argument("--deviation", type=float, default=2500.0, metavar="HZ", help="peak deviation at full scale audio (default 2500)")
    fmg.add_argument("--no-preemph", action="store_true", help="no pre-emphasis (+6 dB per octave from 300 to 3000 Hz)")
    ssb = p.add_argument_group("SSB")
    ssb.add_argument("--carrier", type=float, default=0.05, metavar="FRACTION",
                     help="the carrier's share of the peak envelope, 0 .. 0.9: 0 suppresses it, 0.05 leaves a faint pilot to tune to (default)")
    ssb.add_argument("--ssb-delay", type=float, default=1.0, metavar="UPDATES",
                     help="delay of the gain path against the frequency path in 25 us updates (default 1.0, measured on one board)")
    run = p.add_argument_group("session")
    run.add_argument("--duration", type=float, metavar="SECONDS", help="stop after this many seconds (default: until the source ends)")
    run.add_argument("--update-rate", type=int, default=40000, metavar="HZ", help="records per second sent to the chip, 8000 .. 40000 (default 40000)")
    run.add_argument("--drift", type=int, default=210, metavar="HZ",
                     help="thermal frequency drift to cancel at switch-on, measured on one board (default 210; 0 = off)")
    run.add_argument("--dry-run", action="store_true", help="show the settings and send nothing (the board is not touched)")
    run.add_argument("--accept-licence", action="store_true", help="confirm the notice that is printed (needed once)")
    run.add_argument("-q", "--quiet", action="store_true", help="no progress line")
    run.add_argument("-v", "--verbose", action="store_true", help="more detail")
    brd = p.add_argument_group("board")
    brd.add_argument("--port", metavar="DEVICE", help="the board's native USB port (default: find it)")
    brd.add_argument("--bridge-port", metavar="DEVICE", help="the board's USB-UART port, used to load the firmware (default: find it)")
    brd.add_argument("--no-load", action="store_true", help="do not load firmware: fail if the transmitter is not running")
    brd.add_argument("--reload", action="store_true", help="load the transmitter firmware even if it is running")
    brd.add_argument("--native", action="store_true", help="load through the native port (board without a UART port: hold BOOT, tap RESET)")
    brd.add_argument("--image", metavar="FILE", help="use this transmitter image instead of the one of the package")
    brd.add_argument("--restore", action="store_true", help="when done, reset the board so that it boots the receiver from its flash")
    p.add_argument("--selftest", action="store_true", help="check the signal processing without hardware and exit")
    return p


def _modulator(args, power_db):
    if args.mode == "fm":
        return txmodes.FmModulator(rate=args.update_rate, deviation=args.deviation, power_db=power_db, preemph=not args.no_preemph,
                                   agc=not args.no_agc, gain_db=args.gain)
    return txmodes.SsbModulator(rate=args.update_rate, sideband=args.mode, carrier=args.carrier, power_db=power_db,
                                delay=args.ssb_delay, agc=not args.no_agc, gain_db=args.gain)


def _blocks(args, stop):
    """(8 kHz audio blocks as a generator, the sound card source or None, a description)."""
    if args.test_tone:
        return audio.test_tone(args.test_tone, 0.5), None, f"test tone {args.test_tone:g} Hz"
    src = audio.open_source(args.input, rate=args.rate, channels=args.channels, fmt=args.format)
    live = isinstance(src, audio.SoundCardSource)
    desc = f"sound card {src.name!r} at {src.rate} Hz" if live else f"{args.input if args.input != '-' else 'stdin'} ({src.rate} Hz)"
    mono = audio.Mono8k(src)
    can_loop = args.loop and not live and args.input != "-"

    def gen():
        while not stop.is_set():
            for b in mono.blocks():
                yield b
                if stop.is_set():
                    return
            if not can_loop:
                return
            mono.source.close()
            mono.source = audio.open_source(args.input, rate=args.rate, channels=args.channels, fmt=args.format)
    return gen(), (mono if live else None), desc


def run(args, out=None):
    import numpy as np
    out = out or sys.stderr
    if args.selftest:
        return selftest()
    if args.list_devices:
        for line in audio.list_devices() or ["  (no sound card input found)"]:
            print(line)
        return 0
    if args.freq is None:
        raise SystemExit("espdr-tx: --freq is required (for example: espdr-tx -f 2350 -i speech.wav); see espdr-tx -h")
    if not args.input and not args.test_tone:
        raise SystemExit("espdr-tx: give an audio source with -i (a WAV file, '-' for stdin, or 'soundcard') or use --test-tone")
    if not txlink.TX_MIN_HZ / 1e6 <= args.freq <= txlink.TX_MAX_HZ / 1e6:
        raise SystemExit(f"espdr-tx: {args.freq:g} MHz is outside 2320 .. 2450 MHz, the only range this transmitter sends in")
    if not 8000 <= args.update_rate <= 40000 or args.update_rate % 8000:
        raise SystemExit("espdr-tx: --update-rate must be 8000, 16000, 24000, 32000 or 40000")
    if not 0 <= args.carrier <= 0.9:
        raise SystemExit("espdr-tx: --carrier must be between 0 and 0.9")
    try:
        mod = _modulator(args, args.power)
    except ValueError as e:
        raise SystemExit(f"espdr-tx: {e}")

    # the carrier the board has to produce: the request corrected for the crystal, moved a little if its PLL word is near a byte boundary
    want_hz = args.freq * 1e6 / (1.0 + args.ppm * 1e-6)
    lo_hz, shift = txlink.choose_lo(want_hz)
    actual = lo_hz * (1.0 + args.ppm * 1e-6) / 1e6
    print(f"espdr-tx {__version__}: {mod.name} voice at {actual:.4f} MHz"
          + (f" (you asked for {args.freq:.4f}; the PLL word allows only this near it)" if abs(actual - args.freq) > 0.00005 else "")
          + f", power {args.power:g} dB, gain codes {txmodes.peak_code(args.power)}..{txmodes.GAIN_WEAKEST}"
          + (f", carrier {args.carrier:g}" if args.mode != "fm" else f", deviation {args.deviation:g} Hz")
          + f", {args.update_rate} updates/s", file=out)
    if args.power < -9 and args.mode != "fm":
        print(f"note: at {args.power:g} dB the SSB amplitude range shrinks to {txmodes.RANGE_DB + args.power:.1f} dB; expect more distortion", file=out)
    if not (_licence_accepted() or args.accept_licence):
        print(LICENCE_NOTICE, file=out)
        raise SystemExit("espdr-tx: read the notice above; run once with --accept-licence to confirm it")
    if args.accept_licence and not _licence_accepted():
        _accept_licence()
    if args.dry_run:
        stop = threading.Event()
        _, _, desc = _blocks(args, stop)
        print(f"dry run: would send {desc}; nothing is sent and the board is not touched", file=out)
        return 0

    stop = threading.Event()
    blocks, live, desc = _blocks(args, stop)
    print(f"source: {desc}", file=out)
    port = board.ensure(board.TX, port=args.port, bridge=args.bridge_port, load="never" if args.no_load else ("always" if args.reload else "auto"),
                        native=args.native, image=args.image, log=lambda m: print(m, file=out))
    link = nb.open_link(port)
    session = txlink.Session(link)
    session.configure(lo_hz, args.update_rate, drift_hz=args.drift, limit_s=3600)
    session.begin(expect_word=txlink.lo_word(lo_hz))

    q = queue.Queue(maxsize=64)
    err = []

    def producer():
        try:
            for b in blocks:
                q.put(mod.process(b))
            for _ in range(10):                                  # flush the filters with silence
                q.put(mod.process(np.zeros(160)))
        except Exception as e:                                   # noqa: BLE001 - reported by the main thread
            err.append(e)
        finally:
            q.put(None)

    th = threading.Thread(target=producer, daemon=True)
    th.start()
    started = time.monotonic()
    last_print = [0.0]
    trim = [0.0]

    def on_status(ev):
        if live is not None:
            e = (ev["fill"] - 8000) / 8000.0
            trim[0] = max(-1000.0, min(1000.0, trim[0] + 5.0 * e))     # follow the sound card's clock
            live.trim(trim[0])
        now = time.monotonic()
        if not args.quiet and now - last_print[0] > 0.5:
            last_print[0] = now
            print(f"\r  on air {now - started:6.1f} s   buffer {ev['fill'] * 1000 // args.update_rate:4d} ms   underruns {ev['underruns']}   late {ev['late']}   ",
                  end="", file=out, flush=True)
        if args.duration and now - started >= args.duration:
            stop.set()

    old = signal.signal(signal.SIGINT, lambda *a: stop.set())
    try:
        summary = session.stream(q, target_fill=8000, on_status=on_status, stop=stop)
    finally:
        signal.signal(signal.SIGINT, old)
        stop.set()
    print("", file=out)
    if err:
        raise SystemExit(f"espdr-tx: {err[0]}")
    print(f"done: {txlink.END_REASONS.get(summary['reason'], summary['reason'])}; underruns {summary['underruns']}, late updates {summary['late']}", file=out)
    if args.restore:
        print("resetting the board: it boots what its flash holds", file=out)
        if not board.reset(args.bridge_port):
            print("no UART port found; unplug and replug the board (or press RESET) to get the receiver back", file=out)
    return 0 if summary["reason"] in (0, 2) or stop.is_set() else 1


def selftest():
    """No hardware: modulators against an ideal polar transmitter, the session against a simulated chip."""
    import numpy as np
    from . import sim
    ok = True

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{name}: {'OK' if good else 'FAIL'} {detail}")

    def ideal(rec, rate=40000):
        q4 = (rec & 0xFFFF).astype(np.uint16).view(np.int16).astype(float)
        ph = 2 * np.pi * np.cumsum(q4 * txmodes.Q4_HZ) / rate
        amp = 10 ** (-(txmodes.RANGE_DB - txmodes.db_of_code(((rec >> 16) & 0xFF).astype(float))) / 20)
        return np.roll(amp, -1) * np.exp(1j * ph)

    t = np.arange(8000) / 8000.0
    x = 0.5 * np.sin(2 * np.pi * 700 * t) + 0.5 * np.sin(2 * np.pi * 1700 * t)
    for carrier in (0.55, 0.05, 0.0):
        m = txmodes.SsbModulator(carrier=carrier, agc=False)
        rec = np.concatenate([m.process(x[i:i + 160]) for i in range(0, len(x), 160)])
        s = ideal(rec)[8000:-2000]
        sp = np.abs(np.fft.fftshift(np.fft.fft(s * np.hanning(len(s)))))
        f = np.fft.fftshift(np.fft.fftfreq(len(s), 1 / 40000))
        line = lambda hz: sp[np.abs(f - hz) < 40].max()
        r = 20 * np.log10(min(line(700), line(1700)) / max(line(-700), line(-1700)))
        check(f"SSB two-tone, carrier {carrier:g}: unwanted sideband", r > 40, f"{r:.1f} dB below the wanted tones")
    m = txmodes.FmModulator(deviation=2500, agc=False, preemph=False)
    rec = np.concatenate([m.process(0.8 * np.sin(2 * np.pi * 1000 * t)[i:i + 160]) for i in range(0, 8000, 160)])
    dev = np.abs((rec[len(rec) // 2:] & 0xFFFF).astype(np.uint16).view(np.int16)).max() * txmodes.Q4_HZ
    check("FM deviation", abs(dev - 2000) < 200, f"{dev:.0f} Hz for 0.8 of 2500 Hz (the band-pass is not flat at 1 kHz)")
    check("power setting", [txmodes.peak_code(p) for p in (0, -6, -12)] == [64, 87, 107], "codes for 0, -6, -12 dB")

    lo, _ = txlink.choose_lo(2_350_000_000)
    esp = sim.SimTx()
    s = txlink.Session(nb.Link(esp))
    s.configure(lo, 40000, 210, 60)
    s.begin(expect_word=txlink.lo_word(lo))
    m = txmodes.SsbModulator(carrier=0.05)
    q = queue.Queue()
    recs = [m.process(x[i:i + 160]) for i in range(0, len(x), 160)]
    for r in recs:
        q.put(r)
    q.put(None)
    summary = s.stream(q)
    sent = np.concatenate(recs)
    same = np.array_equal(np.array(esp.played[:len(sent)], dtype=np.uint32), sent)
    check("session against the simulated chip", summary["reason"] == 0 and summary["underruns"] == 0 and same,
          f"{len(sent)} records, end: {txlink.END_REASONS[summary['reason']]}")
    for hz in (2_320_000_000, 2_385_000_000, 2_449_999_000):
        lo2, shift = txlink.choose_lo(hz)
        check(f"LO choice near {hz / 1e6:.3f} MHz", txlink.TX_LOW_MARGIN <= txlink.lo_word(lo2) & 0xFF <= 255 - txlink.TX_LOW_MARGIN and abs(shift) <= 22_000,
              f"moved by {shift / 1e3:+.1f} kHz")
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    board.VERBOSE = bool(args.verbose)
    try:
        return run(args)
    except (board.BoardError, txlink.TxError, audio.AudioError, nb.ProtocolError) as e:
        print(f"espdr-tx: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
