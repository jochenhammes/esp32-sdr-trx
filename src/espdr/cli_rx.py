"""espdr-rx: use an ESP32-S3 board as a receiver for SDR++ and other rtl_tcp programs (1.84 .. 2.79 GHz, 250 or 333 ksps)."""
import argparse
import sys

from . import __version__, board, nb, rtltcp

EPILOG = """\
examples:
  espdr-rx                                   load the receiver firmware if needed and start the rtl_tcp server on 127.0.0.1:1234
  espdr-rx --decim 3                         333 ksps (+-133 kHz usable) instead of 250 ksps (+-100 kHz)
  espdr-rx --listen 0.0.0.0:1234 --ppm -5.7  reachable from other computers; correct the board's crystal by -5.7 ppm
  espdr-rx --no-load                         do not touch the firmware: fail if the receiver is not running
  espdr-rx --flash                           write the receiver into the board's flash: it then starts by itself at power-up

In SDR++ choose the source "RTL-TCP", host 127.0.0.1, port 1234 and press play. The receiver firmware is loaded into the board's
RAM when it is not running (this needs the board's UART USB port as well as the native one; --no-load forbids it). Whatever runs
on the board, a reset brings back what its flash holds.
"""


def build_parser():
    p = argparse.ArgumentParser(prog="espdr-rx", description=__doc__, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-V", "--version", action="version", version=f"espdr-rx {__version__}")
    srv = p.add_argument_group("rtl_tcp server")
    srv.add_argument("--listen", default="127.0.0.1:1234", metavar="HOST:PORT", help="address of the rtl_tcp server (default 127.0.0.1:1234)")
    srv.add_argument("--decim", type=int, choices=(3, 4), default=4,
                     help="4: 250 ksps from the radio, +-100 kHz usable (default); 3: 333 ksps, +-133 kHz usable")
    srv.add_argument("--ppm", type=float, default=0.0, metavar="PPM",
                     help="this board's crystal error, positive if it runs fast (a client's ppm setting adds to it)")
    srv.add_argument("--gain", type=int, default=60, metavar="N", help="gain selector 0..127 until the client sets one (default 60)")
    srv.add_argument("--gain-min", type=int, default=30, metavar="N", help="selector for a client gain of 0 dB (default 30)")
    srv.add_argument("--gain-max", type=int, default=80, metavar="N", help="selector for a client gain of 49.6 dB (default 80)")
    srv.add_argument("--no-dc-block", action="store_true", help="keep the radio's DC offset")
    brd = p.add_argument_group("board")
    brd.add_argument("--port", metavar="DEVICE", help="the board's native USB port (default: find it)")
    brd.add_argument("--bridge-port", metavar="DEVICE", help="the board's USB-UART port, used to load the firmware (default: find it)")
    brd.add_argument("--no-load", action="store_true", help="do not load firmware: fail if the receiver is not running")
    brd.add_argument("--reload", action="store_true", help="load the receiver firmware even if it is running")
    brd.add_argument("--native", action="store_true", help="load through the native port (board without a UART port: hold BOOT, tap RESET)")
    brd.add_argument("--image", metavar="FILE", help="use this receiver image instead of the one of the package")
    brd.add_argument("--flash", action="store_true", help="write the receiver to the board's flash (overwrites it), then serve")
    brd.add_argument("--yes", action="store_true", help="with --flash: do not ask for confirmation")
    p.add_argument("-v", "--verbose", action="store_true", help="more detail")
    p.add_argument("--selftest", action="store_true", help="check the server against a simulated chip, no hardware, and exit")
    return p


def run(args):
    if args.selftest:
        return rtltcp.selftest()
    try:
        host, port = rtltcp.parse_listen(args.listen)
    except ValueError:
        raise SystemExit("espdr-rx: --listen needs HOST:PORT, for example 127.0.0.1:1234")
    if args.flash:
        board.flash_receiver(bridge=args.bridge_port, image=args.image, yes=args.yes, log=rtltcp.log)
    native_port = board.ensure(board.RX, port=args.port, bridge=args.bridge_port,
                               load="never" if args.no_load else ("always" if args.reload and not args.flash else "auto"),
                               native=args.native, image=args.image, log=rtltcp.log)

    def open_link():
        link = nb.open_link(board.find_native(args.port) or native_port)     # the port can change if the board resets
        nb.check_firmware(link)
        return link

    bridge = rtltcp.Bridge(open_link, (host, port), ppm=args.ppm, gain=args.gain, gain_range=(args.gain_min, args.gain_max),
                           dc_block=not args.no_dc_block, decim=args.decim)
    rate = rtltcp.SRC_RATES[args.decim] / 1e3
    rtltcp.log(f"espdr-rx {__version__}: receiver on {native_port}; rtl_tcp server on {bridge.address[0]}:{bridge.address[1]}, "
               f"{rate:.0f} ksps, ppm {args.ppm:g}. In SDR++ choose RTL-TCP and press play. Ctrl-C ends.")
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass
    bridge.quit = True
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (board.BoardError, nb.ProtocolError) as e:
        print(f"espdr-rx: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
