"""The host side of the transmit protocol (firmware/protocol/transmit.h): set up a session, stream records, read the chip's status."""
import queue
import struct
import time
import zlib

from . import nb

TX_OP_LO, TX_OP_RATE, TX_OP_DRIFT, TX_OP_LIMIT, TX_OP_BEGIN, TX_OP_END, TX_OP_TEMP = 40, 41, 42, 43, 44, 45, 46
TX_MIN_HZ, TX_MAX_HZ = 2_320_000_000, 2_450_000_000
TX_LOW_MARGIN = 48
TX_STATUS_MAGIC, TX_STATUS_BYTES = 0xB6, 8
TX_RING_RECORDS = 3 * 65536 // 4
TX_PREFILL = 4000
END_REASONS = {0: "finished", 1: "the chip stopped because no data arrived for 500 ms", 2: "the longest session was reached",
               3: "the receiver could not be set up again"}
PLL_STEP_HZ = 30e6 / 65536


class TxError(Exception):
    pass


def lo_word(hz):
    """The PLL word the firmware programs for an LO of `hz` (esp32s3_plan_lo, normal conversion): LO = 30 MHz * (32 + W / 65536)."""
    scaled = (hz * 65536 + 15_000_000) // 30_000_000
    return scaled - 32 * 65536


def word_hz(word):
    """The LO a PLL word really programs (the inverse of lo_word): 30 MHz * (32 + W / 65536); it can differ from the requested frequency by up to 229 Hz."""
    return 30e6 * (32 + word / 65536)


def choose_lo(hz, max_offset_steps=44):
    """Moves the requested LO by the smallest amount that puts the low byte of the PLL word into 48 .. 207, where the offsets of a
    modulated signal cannot carry into the next byte. Returns (lo_hz in steps of 100 Hz, shift in Hz)."""
    hz = int(round(hz / 100.0)) * 100
    for d in range(0, 400):
        for sign in (1, -1):
            cand = hz + sign * d * 100
            low = lo_word(cand) & 0xFF
            if TX_LOW_MARGIN <= low <= 255 - TX_LOW_MARGIN:
                return cand, cand - hz
    raise TxError("no usable LO frequency near the request")


class Frames:
    """Splits the chip's output during a session into status frames and the final response."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        events = []
        i = 0
        b = self.buf
        while i < len(b):
            if b[i] == TX_STATUS_MAGIC:
                if i + TX_STATUS_BYTES > len(b):
                    break
                f = b[i:i + TX_STATUS_BYTES]
                if sum(f) & 0xFF == 0:
                    events.append(("status", dict(seq=f[1], fill=f[2] | f[3] << 8, underruns=f[4], late=f[5], carrier=bool(f[6] & 1))))
                    i += TX_STATUS_BYTES
                    continue
            elif b[i] == nb.RESP_MAGIC:
                if i + 16 > len(b):
                    break
                raw = bytes(b[i:i + 16])
                if struct.unpack("<I", raw[12:16])[0] == zlib.crc32(raw[:12]):
                    _, _, op, status, _seq, _, value = struct.unpack("<BBBBHHI", raw[:12])
                    events.append(("response", dict(op=op, status=status, value=value)))
                    i += 16
                    continue
            i += 1
        del b[:i]
        return events


class Session:
    """One transmission: configure, begin, stream, end."""

    def __init__(self, link):
        self.link = link
        self.ser = link.ser
        self.status = None          # last status frame
        self.summary = None

    def configure(self, lo_hz, rate, drift_hz=0, limit_s=600):
        c = self.link.command
        c(TX_OP_LO, int(round(lo_hz / 100.0)), allow=(nb.CTL_OK,))
        c(TX_OP_RATE, int(rate))
        c(TX_OP_DRIFT, int(drift_hz) & 0xFFFF)
        c(TX_OP_LIMIT, int(limit_s))

    def chip_temperature(self):
        """The chip's temperature in degrees C from its sensor (between sessions only). Range 2 is the SDK's default, range 1 takes over when it saturates."""
        for rng in (2, 1):
            _, v = self.link.command(TX_OP_TEMP, rng)
            idx, raw = v >> 24, (v & 0xFFFFFF) / 16.0
            c = 0.4386 * raw - 27.88 * (idx - 2) - 20.52
            if c < 70:
                return c
        return c

    def begin(self, expect_word=None):
        try:
            status, word = self.link.command(TX_OP_BEGIN, 0, allow=(nb.CTL_OK,))
        except nb.CommandError as e:
            raise TxError({nb.CTL_BAD_ARGUMENT: "the chip refused the frequency or the settings",
                           nb.CTL_NOT_READY: "the receiver of the chip is not ready",
                           nb.CTL_FAILED: "the PLL did not lock at this frequency (try another)"}.get(e.status, f"the chip answered status {e.status}"))
        if expect_word is not None and word != expect_word:
            raise TxError(f"the chip programmed PLL word 0x{word:06X}, expected 0x{expect_word:06X}")
        return word

    def stream(self, records, target_fill=8000, on_status=None, stop=None, idle_s=0.002):
        """records: a queue.Queue of numpy uint32 arrays; None ends the transmission. stop: a threading.Event that ends it too.
        Returns the chip's summary dict."""
        import serial
        ser = self.ser
        ser.timeout = idle_s
        ser.write_timeout = 1.0
        frames = Frames()
        pending = None
        sent_since = 0
        fill = 0
        ending = False
        end_sent = False
        t_last = time.monotonic()
        end_by = None
        while True:
            data = ser.read(ser.in_waiting or 1)
            if data:
                for kind, ev in frames.feed(data):
                    if kind == "status":
                        self.status = ev
                        fill, sent_since = ev["fill"], 0
                        if on_status:
                            on_status(ev)
                    elif ev["op"] == TX_OP_END:
                        v = ev["value"]
                        self.summary = dict(reason=v >> 24, underruns=(v >> 16) & 0xFF, late=v & 0xFFFF, status=ev["status"])
                        return self.summary
            now = time.monotonic()
            if not ending and stop is not None and stop.is_set():
                ending = True
            if not end_sent:
                room = target_fill - fill - sent_since
                if room >= 256 or (ending and pending is None):
                    if pending is None and not ending:
                        try:
                            item = records.get_nowait()
                        except queue.Empty:
                            item = False
                        if item is None:
                            ending = True
                        elif item is not False:
                            pending = item
                    if pending is not None and len(pending):
                        n = min(room, len(pending), 4096)
                        try:
                            ser.write(pending[:n].astype("<u4").tobytes())
                        except serial.SerialTimeoutException:
                            raise TxError("the chip does not take data any more (it stopped the transmission?)")
                        sent_since += n
                        pending = pending[n:] if n < len(pending) else None
                        t_last = now
                    elif ending and pending is None:
                        import numpy as np
                        code = 127
                        ser.write(np.array([(code << 16) | (1 << 24)], dtype="<u4").tobytes())
                        end_sent = True
                        end_by = now + 5.0
            if end_by and now > end_by:
                raise TxError("the chip did not confirm the end of the transmission")
            if now - t_last > 5.0 and not end_sent and fill == 0 and self.status is None:
                raise TxError("the chip sends no status; is the transmitter firmware running?")
