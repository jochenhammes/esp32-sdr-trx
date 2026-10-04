"""espdr-tx: transmit FM or SSB voice, or RTTY, from an ESP32-S3 board on the 13 cm band."""
import argparse
import os
import queue
import re
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__, audio, board, nb, rtty, txlink, txmodes

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
  espdr-tx -f 2350 -m rtty --text "RYRY CQ CQ DE TEST"       RTTY, 45.45 baud, 170 Hz shift (true FSK)
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
    main.add_argument("-m", "--mode", choices=("fm", "usb", "lsb", "rtty"), default="fm",
                      help="fm: narrowband FM; usb or lsb: single sideband; rtty: text as two-tone FSK, the audio, FM and SSB options "
                           "and --duration do not apply (default fm)")
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
    rt = p.add_argument_group("RTTY (-m rtty)")
    rt.add_argument("--text", metavar="TEXT", help="the text to send; the typed characters \\r and \\n are sent as carriage return and line feed. "
                                                   "Put your call sign in it: the tool adds none")
    rt.add_argument("--text-file", metavar="FILE", help="send the text of this file ('-' for stdin); line ends are sent as CR LF")
    rt.add_argument("--mark-hz", type=float, default=rtty.MARK_DEFAULT, metavar="HZ",
                    help="the mark tone above -f, 300 .. 2700 (default 2125). -f is the dial frequency of an upper-sideband transmitter, "
                         "as with pluto-tx: the same -f gives the same tones")
    rt.add_argument("--shift-hz", type=float, default=rtty.SHIFT_DEFAULT, metavar="HZ",
                    help="space minus mark, 1 .. 1000 (default 170; common: 170, 425, 850)")
    rt.add_argument("--baud-rate", type=float, default=rtty.BAUD_DEFAULT, metavar="BAUD",
                    help="20 .. 200 (default 45.45; common: 45.45, 50, 75, 100)")
    rt.add_argument("--reverse", action="store_true", help="swap which tone is mark (for a station with flipped polarity)")
    rt.add_argument("--edge", type=float, default=0.2, metavar="BITS",
                    help="rise time of the frequency steps in bit periods, 0 .. 0.5 (default 0.2; 0 = abrupt steps)")
    rt.add_argument("--repeat-count", type=int, default=1, metavar="N", help="send the text N times, 1 .. 999 (default 1)")
    rt.add_argument("--repeat-interval", type=float, default=10.0, metavar="SECONDS",
                    help="pause between two transmissions, 1 .. 86400; the transmitter is off in between (default 10)")
    run = p.add_argument_group("session")
    run.add_argument("--duration", type=float, metavar="SECONDS", help="stop after this many seconds (default: until the source ends)")
    run.add_argument("--update-rate", type=int, default=40000, metavar="HZ", help="records per second sent to the chip, 8000 .. 40000 (default 40000)")
    run.add_argument("--thermal", choices=("off", "sensor", "nominal"), default="off",
                     help="cancel the carrier's thermal drift with a model: 'sensor' reads the chip's temperature first, 'nominal' assumes the idle temperature "
                          "of the chip (no sensor reading); the model is that of the board it was measured on (default off)")
    run.add_argument("--thermal-model", metavar="FILE", help="JSON file with the thermal model's numbers of this board (see espdr.thermal.DEFAULT)")
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


def _modulator(args, power_db, plan=None):
    if args.mode == "rtty":
        return txmodes.FskModulator(rate=args.update_rate, power_db=power_db, static_hz=plan["static_hz"])
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


def _rtty_text(args):
    if args.text_file:
        raw = sys.stdin.read() if args.text_file == "-" else Path(args.text_file).read_text(errors="replace")
        return re.sub(r"\r?\n", "\r\n", raw)
    return args.text.replace("\\r", "\r").replace("\\n", "\n")


def _check_rtty(args):
    """Arguments of -m rtty; returns the text."""
    if (args.text is None) == (args.text_file is None):
        raise SystemExit("espdr-tx: -m rtty needs exactly one of --text and --text-file")
    if args.input or args.test_tone:
        raise SystemExit("espdr-tx: -m rtty sends text; -i and --test-tone are for the audio modes")
    if not 300 <= args.mark_hz <= 2700:
        raise SystemExit("espdr-tx: --mark-hz must be between 300 and 2700")
    if not 1 <= args.shift_hz <= 1000:
        raise SystemExit("espdr-tx: --shift-hz must be between 1 and 1000")
    if not 20 <= args.baud_rate <= 200:
        raise SystemExit("espdr-tx: --baud-rate must be between 20 and 200")
    if not 0 <= args.edge <= 0.5:
        raise SystemExit("espdr-tx: --edge must be between 0 and 0.5")
    if not 1 <= args.repeat_count <= 999:
        raise SystemExit("espdr-tx: --repeat-count must be between 1 and 999")
    if not 1 <= args.repeat_interval <= 86400:
        raise SystemExit("espdr-tx: --repeat-interval must be between 1 and 86400")
    text = _rtty_text(args)
    if rtty.estimate_duration(text, args.baud_rate) > 3500:
        raise SystemExit("espdr-tx: the text takes more than 3500 s to send; the chip ends a transmission after 3600 s. Send it in parts")
    return text


def _rtty_plan(args):
    """Where the two tones go. The lower tone is -f + mark; the board's LO is moved to a PLL word that is easy to program (txlink.choose_lo)
    and a constant in every record makes up for the move, as far as the records reach (about +-19 kHz). The LO is the one the PLL word
    really programs, and the constant and the shift are whole record units (28.6 Hz): the tones are within about 15 Hz of the wanted ones."""
    ppm = 1.0 + args.ppm * 1e-6
    low = args.freq * 1e6 + args.mark_hz
    if low < txlink.TX_MIN_HZ or low + args.shift_hz > txlink.TX_MAX_HZ:
        raise SystemExit(f"espdr-tx: the tones ({low / 1e6:.6f} and {(low + args.shift_hz) / 1e6:.6f} MHz) are outside 2320 .. 2450 MHz")
    lo_hz, _ = txlink.choose_lo(low / ppm)
    lo_real = txlink.word_hz(txlink.lo_word(lo_hz)) * ppm
    unit = txmodes.Q4_HZ * ppm                                          # one record unit on the air
    shift_q4 = max(1, int(round(args.shift_hz / unit)))
    static_q4 = int(round((low - lo_real) / unit))
    clamped = min(max(static_q4, -txmodes.MAX_Q4), txmodes.MAX_Q4 - shift_q4)
    low_real = lo_real + clamped * unit
    return dict(lo_hz=lo_hz, static_hz=clamped * txmodes.Q4_HZ, shift_nominal=shift_q4 * txmodes.Q4_HZ, low_hz=low_real,
                shift_real=shift_q4 * unit, miss_hz=low_real - low)


def _transmit(args, out, mod, session, lo_hz, stop, interrupted, blocks, live, stream):
    """One transmission: configure and begin the chip, feed it, report. Returns (the chip's summary, ended by the user)."""
    import numpy as np
    correct = None
    if args.thermal != "off":
        from . import thermal
        params = thermal.load_params(args.thermal_model)
        start_c = params["idle"]
        if args.thermal == "sensor":
            try:
                start_c = session.chip_temperature()
            except (nb.CommandError, OSError) as e:
                print(f"warning: the chip's temperature could not be read ({e}); assuming {start_c:g} C", file=out)
        correct = thermal.Thermal(start_c, args.update_rate, args.freq * 1e6, params)
        peak = max(abs(correct.correction_hz(int(args.update_rate * 150))))
        print(f"thermal drift correction: chip {start_c:.1f} C {'(read)' if args.thermal == 'sensor' else '(assumed)'}, "
              f"up to {peak:.0f} Hz within 150 s", file=out)
    session.configure(lo_hz, args.update_rate, drift_hz=args.drift, limit_s=3600)
    session.begin(expect_word=txlink.lo_word(lo_hz))

    q = queue.Queue(maxsize=64)
    err = []
    level = [-120.0]                                     # input level in dBFS of the last audio block, shown in the progress line

    def producer():
        try:
            for b in blocks:
                if stream is None:
                    level[0] = 20 * np.log10(max(float(np.sqrt(np.mean(np.square(b)))), 1e-6))
                r = mod.process(b)
                q.put(correct.apply(r) if correct else r)
            if stream is None:
                for _ in range(10):                          # flush the filters with silence
                    r = mod.process(np.zeros(160))
                    q.put(correct.apply(r) if correct else r)
        except Exception as e:                                   # noqa: BLE001 - reported by the main thread
            err.append(e)
        finally:
            q.put(None)

    th = threading.Thread(target=producer, daemon=True)
    th.start()
    started = time.monotonic()
    last_print = [0.0]
    warned = [False]
    trim = [0.0]
    by_user = [False]

    def on_status(ev):
        if live is not None:
            e = (ev["fill"] - 8000) / 8000.0
            trim[0] = max(-1000.0, min(1000.0, trim[0] + 5.0 * e))     # follow the sound card's clock
            live.trim(trim[0])
        now = time.monotonic()
        if not args.quiet and now - last_print[0] > 0.5:
            last_print[0] = now
            what = f"sent {100 * stream.fraction():3.0f} %" if stream is not None else f"audio {level[0]:6.1f} dBFS"
            print(f"\r  on air {now - started:6.1f} s   {what}   buffer {ev['fill'] * 1000 // args.update_rate:4d} ms   underruns {ev['underruns']}   late {ev['late']}   ",
                  end="", file=out, flush=True)
            if stream is None and level[0] < -70 and now - started > 3 and not warned[0]:
                warned[0] = True
                print("\nwarning: almost no audio arrives (input level below -70 dBFS). Is the microphone muted or the wrong input chosen? "
                      "See --list-devices and -i soundcard:NUMBER.", file=out)
        if stream is None and args.duration and now - started >= args.duration:
            by_user[0] = True
            stop.set()

    def on_sigint(*_):
        interrupted.set()
        by_user[0] = True
        stop.set()

    old = signal.signal(signal.SIGINT, on_sigint)
    try:
        summary = session.stream(q, target_fill=8000, on_status=on_status, stop=stop)
    finally:
        signal.signal(signal.SIGINT, old)
        stop.set()
    print("", file=out)
    if err:
        raise SystemExit(f"espdr-tx: {err[0]}")
    print(f"done: {txlink.END_REASONS.get(summary['reason'], summary['reason'])}; underruns {summary['underruns']}, late updates {summary['late']}", file=out)
    return summary, by_user[0]


def run(args, out=None):
    out = out or sys.stderr
    if args.selftest:
        return selftest()
    if args.list_devices:
        for line in audio.list_devices() or ["  (no sound card input found)"]:
            print(line)
        return 0
    is_rtty = args.mode == "rtty"
    if args.freq is None:
        raise SystemExit("espdr-tx: --freq is required (for example: espdr-tx -f 2350 -i speech.wav); see espdr-tx -h")
    if is_rtty:
        text = _check_rtty(args)
    else:
        if args.text is not None or args.text_file is not None or args.repeat_count != 1:
            raise SystemExit("espdr-tx: --text, --text-file and --repeat-count belong to -m rtty")
        if not args.input and not args.test_tone:
            raise SystemExit("espdr-tx: give an audio source with -i (a WAV file, '-' for stdin, or 'soundcard') or use --test-tone")
    if not txlink.TX_MIN_HZ / 1e6 <= args.freq <= txlink.TX_MAX_HZ / 1e6:
        raise SystemExit(f"espdr-tx: {args.freq:g} MHz is outside 2320 .. 2450 MHz, the only range this transmitter sends in")
    if not 8000 <= args.update_rate <= 40000 or args.update_rate % 8000:
        raise SystemExit("espdr-tx: --update-rate must be 8000, 16000, 24000, 32000 or 40000")
    if not 0 <= args.carrier <= 0.9:
        raise SystemExit("espdr-tx: --carrier must be between 0 and 0.9")
    plan = _rtty_plan(args) if is_rtty else None
    try:
        mod = _modulator(args, args.power, plan)
    except ValueError as e:
        raise SystemExit(f"espdr-tx: {e}")

    if is_rtty:
        lo_hz = plan["lo_hz"]
        frames = rtty.message_frames(text, args.baud_rate)
        duration = rtty.duration_s(frames, args.baud_rate)
        low_hz = plan["low_hz"]
        mark_is = "high" if args.reverse else "low"
        print(f"espdr-tx {__version__}: RTTY (FSK) low tone {low_hz / 1e6:.6f} MHz, high tone {(low_hz + plan['shift_real']) / 1e6:.6f} MHz "
              f"(shift {plan['shift_real']:.1f} Hz, mark is the {mark_is} tone), {args.baud_rate:g} baud, {len(text)} characters, about {duration:.1f} s, "
              f"power {args.power:g} dB, gain code {txmodes.peak_code(args.power)}, edge {args.edge:g} bit, {args.update_rate} updates/s", file=out)
        if abs(plan["miss_hz"]) > 20:
            print(f"warning: the PLL word cannot reach the wanted frequency here; the tones are {plan['miss_hz']:+.0f} Hz off. "
                  f"Tune your receiver to the tones above, or change -f by a few kHz", file=out)
    else:
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

    def sources():
        stop = threading.Event()
        if is_rtty:
            stream = rtty.OffsetStream(frames, args.update_rate, args.baud_rate, plan["shift_nominal"], args.reverse, args.edge)
            return stop, stream.blocks(), None, f"RTTY text, {len(text)} characters", stream
        blocks, live, desc = _blocks(args, stop)
        return stop, blocks, live, desc, None

    stop, blocks, live, desc, stream = sources()
    if args.dry_run:
        print(f"dry run: would send {desc}; nothing is sent and the board is not touched", file=out)
        return 0
    print(f"source: {desc}", file=out)
    port = board.ensure(board.TX, port=args.port, bridge=args.bridge_port, load="never" if args.no_load else ("always" if args.reload else "auto"),
                        native=args.native, image=args.image, log=lambda m: print(m, file=out))
    link = nb.open_link(port)
    session = txlink.Session(link)
    interrupted = threading.Event()
    summary, by_user = None, False
    for n in range(args.repeat_count):
        if n:
            print(f"pause of {args.repeat_interval:g} s; the transmitter is off", file=out)
            if interrupted.wait(args.repeat_interval):
                break
            stop, blocks, live, desc, stream = sources()
            print(f"source: {desc} (transmission {n + 1} of {args.repeat_count})", file=out)
        summary, by_user = _transmit(args, out, mod, session, lo_hz, stop, interrupted, blocks, live, stream)
        if interrupted.is_set() or summary["reason"] not in (0, 2):
            break
    if args.restore:
        print("resetting the board: it boots what its flash holds", file=out)
        if not board.reset(args.bridge_port):
            print("no UART port found; unplug and replug the board (or press RESET) to get the receiver back", file=out)
    return 0 if summary is None or summary["reason"] in (0, 2) or by_user else 1


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

    text = "RYRY DE TEST 123. CQ (OK)?"
    stream = rtty.OffsetStream(rtty.message_frames(text), 40000, rtty.BAUD_DEFAULT, 6 * txmodes.Q4_HZ)
    m = txmodes.FskModulator(static_hz=-3000.0)
    rec = np.concatenate([m.process(b) for b in stream.blocks()])
    sig = ideal(rec)
    inst = np.angle(sig[1:] * np.conj(sig[:-1])) * 40000 / (2 * np.pi)
    check("RTTY text through the polar model", rtty.decode_frequency(inst, 40000) == text and len({int(r >> 16) for r in rec}) == 1,
          f"{len(text)} characters")

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
