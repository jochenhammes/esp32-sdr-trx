#!/usr/bin/env python3
"""Host receiver for the FPGA-free narrowband build of eSpDR.

The ESP32-S3 decimates on chip and streams complex samples over its native
USB Serial/JTAG port (protocol/narrowband.h). This tool tunes the radio,
starts a run, checks the packets and forwards the samples.

  espdr_nb.py bench --seconds 5                                      # USB throughput test
  espdr_nb.py run --freq 2400.1e6 --seconds 10 -o capture.cs8        # 250 ksps, int8 I/Q, to a file
  espdr_nb.py run --freq 2400.1e6 --convert cf32 --tcp 7373          # complex float for GNU Radio
  espdr_nb.py dspbench                                               # time the on-chip decimator
  espdr_nb.py selftest                                               # no hardware needed

The port is found by its USB id (303a:1001); give -p /dev/ttyACMx to choose one. For SDR++ and other
rtl_tcp clients use espdr_rtltcp.py instead.

Needs pyserial; numpy only for --convert cf32.
"""
import argparse
import signal
import socket
import struct
import sys
import time
import zlib

# --- protocol constants (protocol/control.h, protocol/narrowband.h) -----------------------
REQ_MAGIC, RESP_MAGIC = 0xB4, 0xB5
CTL_INFO, CTL_SAFE, CTL_STATUS = 1, 2, 3
ESP_RUN, ESP_STOP, ESP_ARG_HIGH = 17, 18, 19
ESP_SET_LO, ESP_SET_RATE, ESP_SET_WIDTH, ESP_SET_GAIN = 20, 21, 22, 24
ESP_RATE_16M = 1
NB_SET_DECIM, NB_SET_FORMAT, NB_SET_OUTSHIFT, NB_BENCH, NB_DSPBENCH = 29, 30, 31, 32, 33
NB_DSP_VERIFY, NB_DSP_PROFILE, NB_DSP_PROFILE_RESET = 0x8000, 0x4000, 0x2000
ESP_STAT_STATUS, ESP_STAT_DETAIL, ESP_STAT_FAIL_LANE = 0, 1, 2
ESP_STAT_UNITS0, ESP_STAT_UNITS1 = 3, 4
ESP_STAT_SERVICE_MAX0, ESP_STAT_SERVICE_MAX1, ESP_STAT_RADIO = 11, 12, 13
NB_STAT_DROPPED, NB_STAT_FIFO_PEAK, NB_STAT_SLIPS = 32, 33, 34
CTL_OK, CTL_UNKNOWN_OP = 0, 1
FIRMWARE_ID = 0x49515306  # CTL_ESP_FIRMWARE_ID in protocol/control.h
FAIL_NAMES = {1: "bank ownership changed", 2: "late poll", 3: "late switch", 4: "unit end not found",
              5: "unit start not found", 6: "bad unit length", 7: "bank still busy (DSP too slow)",
              8: "next bank prepared too late (DSP too slow)", 9: "radio init failed"}
NB_MAGIC = b"\xE5\x5D"
NB_HEADER = 20
FORMATS = {"cs16": 0, "cs8": 1}
BYTES_PER_SAMPLE = {0: 4, 1: 2}
SERVICE_BUDGET_CYCLES = 2 * 240_000  # two unit periods of ~1 ms at 240 MHz: what one lane may take


class ProtocolError(Exception):
    pass


class CommandError(ProtocolError):
    """The ESP answered a control request with an error status."""

    def __init__(self, op, arg, status):
        self.op, self.arg, self.status = op, arg, status
        hint = ""
        if op == ESP_SET_LO:
            hint = (" (the frequency is outside 1841.666667-2790 MHz)" if status == 2 else
                    " (the PLL did not lock there: the edges of the range, and a gap near 2210 MHz, depend on the board)")
        super().__init__(f"op {op} arg {arg}: status {status}{hint}")


def request(op, arg, seq):
    head = struct.pack("<BBHH", REQ_MAGIC, op, arg & 0xFFFF, seq & 0xFFFF)
    return head + struct.pack("<I", zlib.crc32(head))


class Link:
    """Control protocol plus stream parser over a pyserial-like object."""

    def __init__(self, ser):
        self.ser = ser
        self.buf = bytearray()
        self.seq = 0

    # -- raw input
    def _fill(self, want, timeout):
        end = time.monotonic() + timeout
        while len(self.buf) < want:
            chunk = self.ser.read(max(1, min(65536, getattr(self.ser, "in_waiting", 0) or 1)))
            if chunk:
                self.buf += chunk
            elif time.monotonic() > end:
                return False
        return True

    def _take(self, n):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    # -- control
    def send(self, op, arg=0):
        if arg > 0xFFFF:
            self.ser.write(request(ESP_ARG_HIGH, arg >> 16, self._next_seq()))
            self._response(ESP_ARG_HIGH)
        self.ser.write(request(op, arg, self._next_seq()))

    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFFFF
        return self.seq

    def _response(self, op, timeout=5.0):
        """Waits for the response to `op`, skipping anything else."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self._fill(1, 0.2):
                continue
            if self.buf[0] != RESP_MAGIC:
                del self.buf[0]
                continue
            if not self._fill(16, 0.5):
                continue
            raw = bytes(self.buf[:16])
            if struct.unpack("<I", raw[12:16])[0] != zlib.crc32(raw[:12]):
                del self.buf[0]
                continue
            del self.buf[:16]
            _, _, rop, status, _seq, _, value = struct.unpack("<BBBBHHI", raw[:12])
            if rop == op:
                return status, value
        raise ProtocolError(f"no response to op {op}")

    def command(self, op, arg=0, allow=(CTL_OK,)):
        self.send(op, arg)
        status, value = self._response(op)
        if status not in allow:
            raise CommandError(op, arg, status)
        return status, value

    def stat(self, index):
        return self.command(CTL_STATUS, index)[1]

    # -- run: stream parser
    def run(self, seconds, sink, stop_flag=lambda: False, on_idle=lambda: None):
        """Starts ESP_RUN and feeds every valid packet to sink(header, payload).
        Returns (status, value, stats)."""
        self.send(ESP_RUN, seconds)
        stats = dict(packets=0, samples=0, lost=0, skipped=0, bad=0, dropped=0)
        expected = None
        stopped = False
        t_last = time.monotonic()
        while True:
            if stop_flag() and not stopped:
                self.ser.write(request(ESP_STOP, 0, self._next_seq()))
                stopped = True
            if not self._fill(1, 0.05):
                on_idle()
                if time.monotonic() - t_last > 10:
                    raise ProtocolError("stream stalled for 10 s")
                continue
            t_last = time.monotonic()
            b0 = self.buf[0]
            if b0 == RESP_MAGIC:
                if not self._fill(16, 1.0):
                    continue
                raw = bytes(self.buf[:16])
                if struct.unpack("<I", raw[12:16])[0] == zlib.crc32(raw[:12]):
                    del self.buf[:16]
                    _, _, rop, status, _s, _, value = struct.unpack("<BBBBHHI", raw[:12])
                    if rop == ESP_RUN:
                        return status, value, stats
                    continue
            if b0 == NB_MAGIC[0]:
                if not self._fill(NB_HEADER, 1.0):
                    continue
                hdr = bytes(self.buf[:NB_HEADER])
                words = struct.unpack("<10H", hdr)
                if hdr[1] == NB_MAGIC[1] and sum(words) & 0xFFFF == 0xFFFF:
                    fmt_r2, count, dropped, seq_lo, seq_hi, first_lo, first_hi, shift, _ = words[1:]
                    fmt, r2 = fmt_r2 & 0xFF, fmt_r2 >> 8
                    size = NB_HEADER + count * BYTES_PER_SAMPLE.get(fmt, 4)
                    if not self._fill(size, 1.0):
                        continue
                    pkt = self._take(size)
                    first = first_lo | first_hi << 16
                    h = dict(format=fmt, r2=r2, count=count, dropped=dropped,
                             seq=seq_lo | seq_hi << 16, first=first, shift=shift)
                    if expected is not None and first != expected:
                        stats["lost"] += (first - expected) & 0xFFFFFFFF
                    expected = (first + count) & 0xFFFFFFFF
                    stats["packets"] += 1
                    stats["samples"] += count
                    stats["dropped"] = dropped
                    sink(h, pkt[NB_HEADER:])
                    continue
                stats["bad"] += 1
            del self.buf[0]
            stats["skipped"] += 1

    # -- throughput test
    def bench(self, seconds, mode=0):
        self.send(NB_BENCH, seconds | mode << 16)
        got = blocks = bad = 0
        counter = None
        t0 = None
        while True:
            if not self._fill(1, 2.0):
                raise ProtocolError("bench stalled")
            if self.buf[0] == RESP_MAGIC:
                if not self._fill(16, 1.0):
                    continue
                raw = bytes(self.buf[:16])
                if struct.unpack("<I", raw[12:16])[0] == zlib.crc32(raw[:12]):
                    del self.buf[:16]
                    _, _, rop, status, _s, _, value = struct.unpack("<BBBBHHI", raw[:12])
                    if rop == NB_BENCH:
                        dt = (time.monotonic() - t0) if t0 else 0.0
                        return dict(host_bytes=got, esp_bytes=value, seconds=dt, blocks=blocks, bad=bad)
                    continue
            if not self._fill(64, 1.0):
                continue
            blk = self._take(64)
            if blk[0] != 0xBE or blk[1] != 0xEF:
                bad += 1
                continue
            if t0 is None:
                t0 = time.monotonic()
            c = blk[2] | blk[3] << 8
            if counter is not None and c != (counter + 1) & 0xFFFF:
                bad += 1
            counter = c
            got += 64
            blocks += 1


# --- sinks ------------------------------------------------------------------------------
class Output:
    def __init__(self, args):
        self.convert = args.convert == "cf32"
        self.flip = not args.native_iq
        self.files = []
        self.server = self.conn = self.udp = None
        if args.out:
            self.files.append(sys.stdout.buffer if args.out == "-" else open(args.out, "wb"))
        if args.udp:
            host, port = args.udp.rsplit(":", 1)
            self.udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (host, int(port)))
        if args.tcp:
            self.server = socket.socket()
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(("127.0.0.1", args.tcp))
            self.server.listen(1)
            self.server.setblocking(False)
            print(f"waiting for a TCP client on 127.0.0.1:{args.tcp}", file=sys.stderr)
        if self.convert:
            import numpy as np
            self.np = np

    def _convert(self, header, payload):
        np = self.np
        if header["format"] == 0:
            a = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
        else:
            a = np.frombuffer(payload, dtype=np.int8).astype(np.float32) / 128.0
        if self.flip:
            a[1::2] *= -1.0  # the radio delivers LO minus RF; conjugate so that a higher RF is a higher frequency
        return a.astype("<f4").tobytes()

    def __call__(self, header, payload):
        data = self._convert(header, payload) if self.convert else payload
        for f in self.files:
            f.write(data)
        if self.udp:
            sock, addr = self.udp
            for i in range(0, len(data), 1400):
                sock.sendto(data[i:i + 1400], addr)
        if self.server:
            if self.conn is None:
                try:
                    self.conn, _ = self.server.accept()
                except BlockingIOError:
                    return
            try:
                self.conn.sendall(data)
            except OSError:
                self.conn = None

    def close(self):
        for f in self.files:
            if f is not sys.stdout.buffer:
                f.close()


# --- commands ---------------------------------------------------------------------------
def find_port():
    """The ESP32-S3's native USB Serial/JTAG port (USB id 303a:1001) while the narrowband image runs."""
    from serial.tools import list_ports
    found = [p.device for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
    if not found:
        raise ProtocolError("no ESP32-S3 (USB id 303a:1001) found; load the narrowband image first (espdr_load.py)")
    if len(found) > 1:
        raise ProtocolError(f"several ESP32-S3 found ({', '.join(found)}); choose one with -p")
    return found[0]


def open_link(port):
    import serial
    if port == "auto":
        port = find_port()
    # Leave DTR/RTS alone. On the native USB Serial/JTAG port, DTR=0 with RTS=1 resets the chip
    # into the ROM loader, so clearing the lines one after the other (DTR first) kills the
    # firmware. The kernel and pyserial both open with both asserted, which is harmless; closing
    # drops both at once.
    return Link(serial.Serial(port, 115200, timeout=0.05))


def check_firmware(link):
    _, fid = link.command(CTL_INFO, 0)
    if fid != FIRMWARE_ID:
        raise ProtocolError(f"unexpected firmware id 0x{fid:08X}")
    status, _ = link.command(NB_SET_FORMAT, 1, allow=(CTL_OK, CTL_UNKNOWN_OP))
    if status == CTL_UNKNOWN_OP:
        raise ProtocolError("this is not the narrowband firmware (build with NARROWBAND=1)")
    if link.stat(ESP_STAT_RADIO) != 0:
        raise ProtocolError("radio initialisation failed on the ESP32-S3")


def configure_receiver(link, freq, decim, fmt, shift, gain):
    """Programs the radio and the decimator; returns the LO the ESP actually settled on, in Hz."""
    link.command(ESP_SET_RATE, ESP_RATE_16M)
    link.command(ESP_SET_WIDTH, 20)
    _, lo = link.command(ESP_SET_LO, int(round(freq)))
    link.command(ESP_SET_GAIN, gain)
    link.command(NB_SET_DECIM, decim)
    link.command(NB_SET_FORMAT, fmt)
    link.command(NB_SET_OUTSHIFT, shift)
    return lo


def cmd_run(args):
    link = open_link(args.port)
    check_firmware(link)
    fmt = FORMATS[args.format]
    shift = args.shift if args.shift is not None else (4 if fmt == 1 else 0)
    lo = configure_receiver(link, args.freq, args.decim, fmt, shift, args.gain)
    rate = 1e6 / args.decim
    need = rate * BYTES_PER_SAMPLE[fmt] / 1e6
    print(f"LO {lo / 1e6:.6f} MHz, {rate / 1e3:.0f} ksps {args.format}, usable +-{rate * 0.4 / 1e3:.0f} kHz, "
          f"{need:.2f} MB/s over USB", file=sys.stderr)
    out = Output(args)
    stop = {"flag": False}
    # Ctrl-C asks the ESP to finish the current unit pair; the run's reply follows.
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    t0 = time.monotonic()
    status, value, stats = link.run(args.seconds, out, stop_flag=lambda: stop["flag"])
    out.close()
    return report(link, status, value, stats, time.monotonic() - t0)


def report(link, status, value, stats, elapsed):
    if stats is None:
        return 1
    print(f"{stats['samples']} samples in {stats['packets']} packets, {elapsed:.1f} s", file=sys.stderr)
    if stats["lost"] or stats["dropped"] or stats["skipped"]:
        print(f"WARNING: {stats['lost']} samples lost, {stats['dropped']} units dropped by the ESP "
              f"(USB too slow), {stats['skipped']} stray bytes", file=sys.stderr)
    try:
        s0, s1 = link.stat(ESP_STAT_SERVICE_MAX0), link.stat(ESP_STAT_SERVICE_MAX1)
        peak = link.stat(NB_STAT_FIFO_PEAK)
        slips = link.stat(NB_STAT_SLIPS)
        print(f"worst unit service time: core0 {s0} / core1 {s1} cycles "
              f"({100 * max(s0, s1) / SERVICE_BUDGET_CYCLES:.0f}% of the budget); FIFO peak {peak} bytes",
              file=sys.stderr)
        if slips > 8:  # one per run is normal: the join of two banks that is a few pairs off, see capture.c
            print(f"WARNING: {slips} unit joins were a few pairs off (normally 1 per run)", file=sys.stderr)
    except ProtocolError:
        pass
    if value:
        why = FAIL_NAMES.get(value, "unknown")
        detail = link.stat(ESP_STAT_DETAIL)
        print(f"RUN FAILED: code {value} ({why}), detail {detail}", file=sys.stderr)
        return 2
    return 0


def cmd_bench(args):
    link = open_link(args.port)
    check_firmware(link)
    r = link.bench(args.seconds, args.mode)
    mbs = r["host_bytes"] / r["seconds"] / 1e6 if r["seconds"] else 0
    print(f"{r['host_bytes']} bytes in {r['seconds']:.2f} s = {mbs:.3f} MB/s; "
          f"ESP sent {r['esp_bytes']} bytes; {r['bad']} bad blocks")
    print(f"=> sustainable: 8-bit IQ up to {mbs * 1e6 / 2 / 1e3:.0f} ksps, 16-bit IQ up to {mbs * 1e6 / 4 / 1e3:.0f} ksps")
    return 0


def cmd_dspbench(args):
    """Times the on-chip decimator on one 16000-pair unit of noise (no radio, no capture), and checks
    that the SIMD FIR gives the same bytes as the plain C one."""
    link = open_link(args.port)
    check_firmware(link)
    print(f"budget per unit and lane: {SERVICE_BUDGET_CYCLES} cycles")
    bad = 0
    for r2 in (4, 3, 2):
        for fmt, name in ((1, "cs8"), (0, "cs16")):
            for core1 in (0, 1):
                what = r2 | fmt << 4 | core1 << 8
                _, diff = link.command(NB_DSPBENCH, what | NB_DSP_VERIFY)
                _, cycles = link.command(NB_DSPBENCH, what | NB_DSP_PROFILE_RESET)
                bad += diff
                print(f"R2={r2} {name} core{core1} copy: {cycles} cycles ({cycles / 16000:.1f} per pair, "
                      f"{100 * cycles / SERVICE_BUDGET_CYCLES:.0f}% of budget); SIMD vs C: {diff} bytes differ")
                if args.profile:
                    parts = [link.command(NB_DSPBENCH, what | NB_DSP_PROFILE | i << 4)[1] for i in range(3)]
                    print(f"    cic {parts[0]}, comb+history {parts[1]}, FIR+output {parts[2]}, other {cycles - sum(parts)}")
    return 1 if bad else 0


# --- selftest with a fake serial port ---------------------------------------------------
class FakeSerial:
    """Plays the ESP: answers requests, then streams packets and a final response."""

    def __init__(self, drop_every=0, garbage=False):
        self.out = bytearray()
        self.stopped = False
        self.packets_sent = 0
        self.drop_every = drop_every
        self.garbage = garbage
        self.sent_samples = 0
        self.pending_run = False

    in_waiting = 0

    def _response(self, op, status=0, value=0):
        head = struct.pack("<BBBBHHI", RESP_MAGIC, 1, op, status, 0, 0, value)
        return head + struct.pack("<I", zlib.crc32(head))

    def write(self, data):
        data = bytes(data)
        assert data[0] == REQ_MAGIC and struct.unpack("<I", data[6:10])[0] == zlib.crc32(data[:6])
        op, arg = data[1], struct.unpack("<H", data[2:4])[0]
        if op == ESP_STOP:
            self.stopped = True
        elif op == ESP_RUN:
            self.pending_run = True
        elif op == CTL_INFO:
            self.out += self._response(op, 0, FIRMWARE_ID)
        elif op == CTL_STATUS:
            self.out += self._response(op, 0, 12345 if arg in (11, 12) else 0)
        elif op in (NB_BENCH,):
            for i in range(50):
                self.out += bytes([0xBE, 0xEF, i & 0xFF, i >> 8]) + bytes(60)
            self.out += self._response(op, 0, 50 * 64)
        else:
            self.out += self._response(op, 0, 0)

    def _packet(self, seq, first, count, dropped):
        payload = bytes((first + i) & 0xFF for i in range(count * 2))
        words = [0xE5 | 0x5D << 8, 1 | 4 << 8, count, dropped, seq & 0xFFFF, seq >> 16, first & 0xFFFF,
                 first >> 16, 4, 0]
        words[9] = 0xFFFF - (sum(words[:9]) & 0xFFFF)
        return struct.pack("<10H", *words) + payload

    def read(self, n):
        if self.pending_run and not self.out:
            seq = first = 0
            for _ in range(200):
                if self.stopped:
                    break
                if self.drop_every and seq % self.drop_every == self.drop_every - 1:
                    first += 240  # a dropped unit leaves a gap in the sample index
                    seq += 1
                self.out += self._packet(seq, first, 240, 1 if self.drop_every else 0)
                if self.garbage and seq == 50:
                    self.out += b"\x00\xE5\x00junk"
                first += 240
                seq += 1
                self.sent_samples += 240
            self.out += self._response(ESP_RUN, 0, 0)
            self.pending_run = False
        chunk = bytes(self.out[:n])
        del self.out[:n]
        return chunk


def selftest():
    ok = True
    for kw, expect_lost in ((dict(), 0), (dict(drop_every=50), 4 * 240), (dict(garbage=True), 0)):
        link = Link(FakeSerial(**kw))
        got = []
        status, value, stats = link.run(0, lambda h, p: got.append((h, p)))
        good = status == 0 and value == 0 and stats["lost"] == expect_lost and stats["packets"] > 0
        good &= all(len(p) == h["count"] * 2 for h, p in got)
        if kw.get("garbage"):
            good &= stats["skipped"] >= 1
        print(f"stream {kw or 'clean'}: packets={stats['packets']} lost={stats['lost']} "
              f"skipped={stats['skipped']} -> {'OK' if good else 'FAIL'}")
        ok &= good
    r = Link(FakeSerial()).bench(1)
    good = r["bad"] == 0 and r["host_bytes"] == 50 * 64
    print(f"bench: {r['host_bytes']} bytes, {r['bad']} bad -> {'OK' if good else 'FAIL'}")
    ok &= good
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", default="auto",
                    help="serial port of the ESP32-S3's native USB (e.g. /dev/ttyACM1); default: find it by its USB id")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="tune, run and stream")
    r.add_argument("--freq", type=float, required=True, help="LO frequency in Hz (e.g. 2400.1e6)")
    r.add_argument("--decim", type=int, choices=(2, 3, 4), default=4,
                   help="4: 250 ksps (+-100 kHz usable, default); 3: 333 ksps (+-133 kHz); 2: 500 ksps, which needs 1.0 MB/s - "
                        "more than this USB link delivers, so samples are lost")
    r.add_argument("--format", choices=sorted(FORMATS), default="cs8")
    r.add_argument("--shift", type=int, help="output right shift (default 4 for cs8, 0 for cs16)")
    r.add_argument("--gain", type=int, default=60,
                   help="receive gain selector 0..127 (an AGC table index, not dB; 24 leaves the noise near 2 counts, 60 near 20)")
    r.add_argument("--seconds", type=int, default=0, help="0: until Ctrl-C")
    r.add_argument("-o", "--out", help="file, or - for stdout")
    r.add_argument("--tcp", type=int, help="serve the samples to one TCP client on 127.0.0.1:PORT")
    r.add_argument("--udp", help="send the samples as UDP datagrams to HOST:PORT")
    r.add_argument("--convert", choices=("raw", "cf32"), default="raw", help="cf32: float32 I/Q, needs numpy")
    r.add_argument("--native-iq", action="store_true",
                   help="cf32 only: keep the radio's convention (I+jQ = LO minus RF, spectrum mirrored) "
                        "instead of conjugating it to the usual RF-up = frequency-up")
    b = sub.add_parser("bench", help="measure the USB throughput")
    b.add_argument("--seconds", type=int, default=5)
    b.add_argument("--mode", type=int, choices=(0, 1, 2), default=0,
                   help="how the firmware writes the USB packets: 0 as the stream does (default), 1 byte-wise with a poll "
                        "per byte (the old way), 2 one check then 64 writes")
    d = sub.add_parser("dspbench", help="time the on-chip decimator without a capture")
    d.add_argument("--profile", action="store_true", help="per-section cycles (firmware built with PROFILE=1)")
    sub.add_parser("selftest", help="check the parser against a simulated ESP")
    args = ap.parse_args()
    try:
        if args.cmd == "selftest":
            return selftest()
        return {"run": cmd_run, "bench": cmd_bench, "dspbench": cmd_dspbench}[args.cmd](args)
    except ProtocolError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
