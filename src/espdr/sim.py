"""A simulated ESP32-S3 for the tests: answers the control protocol and plays the transmit stream in virtual time (no hardware, no waiting).

It follows firmware/src/radio.c: the same checks of the frequency and its low byte, the ring of 49152 records, the prefill, a status frame
every 5 ms, the watchdog of 500 ms, the end record, and the summary in the final response.
"""
import struct
import time
import zlib
from collections import deque

from . import nb, txlink as tl


class SimTx:
    def __init__(self, flags=3, speed=10.0):
        self.flags = flags                     # CTL_INFO argument 3: 1 receiver, 3 receiver + transmitter
        self.speed = speed                     # virtual seconds per real second
        self.t_real = None
        self.acc = 0.0
        self.out = bytearray()
        self.inp = bytearray()
        self.timeout = 0.002
        self.write_timeout = 1.0
        self.params = dict(lo=0, rate=40000, drift=0, limit=600)
        self.state = "idle"                    # idle, prefill, running
        self.ring = deque()
        self.played = []                       # every record that was played
        self.under = self.late = 0
        self.seq = 0
        self.vtime = 0.0                       # virtual seconds since the session began
        self.last_rx = 0.0
        self.sent_total = 0
        self.begin_request_seq = 0
        self.arg_high = 0
        self.ended_reason = None
        self.carrier_since = None
        self.ops = []

    # ---- serial-like interface
    @property
    def in_waiting(self):
        self._advance()
        return len(self.out)

    def read(self, n):
        self._advance()
        chunk = bytes(self.out[:n])
        del self.out[:n]
        return chunk

    def write(self, data):
        data = bytes(data)
        if self.state == "idle":
            self.inp += data
            self._requests()
        else:
            self.inp += data
            self._records()
        return len(data)

    def close(self):
        pass

    # ---- control requests
    def _resp(self, op, status=0, value=0, seq=0):
        head = struct.pack("<BBBBHHI", nb.RESP_MAGIC, 1, op, status, seq, 0, value)
        self.out += head + struct.pack("<I", zlib.crc32(head))

    def _requests(self):
        while len(self.inp) >= 10:
            if self.inp[0] != nb.REQ_MAGIC:
                del self.inp[0]
                continue
            raw = bytes(self.inp[:10])
            if struct.unpack("<I", raw[6:10])[0] != zlib.crc32(raw[:6]):
                del self.inp[0]
                continue
            del self.inp[:10]
            op, arg, seq = raw[1], struct.unpack("<H", raw[2:4])[0], struct.unpack("<H", raw[4:6])[0]
            if op == nb.ESP_ARG_HIGH:
                self.arg_high = arg << 16
                self._resp(op, 0, 0, seq)
                continue
            full = self.arg_high | arg
            self.arg_high = 0
            self.ops.append((op, full))
            self._execute(op, full, seq)
            if self.state != "idle":
                self._records()                 # the rest of the input is stream data
                return

    def _execute(self, op, arg, seq):
        p = self.params
        if op == nb.CTL_INFO:
            if arg == 0:
                self._resp(op, 0, nb.FIRMWARE_ID, seq)
            elif arg == 3:
                self._resp(op, 0, self.flags, seq)
            else:
                self._resp(op, 0, 0, seq)
        elif op == tl.TX_OP_LO and self.flags & 2:
            ok = tl.TX_MIN_HZ // 100 <= arg <= tl.TX_MAX_HZ // 100
            p["lo"] = arg * 100 if ok else p["lo"]
            self._resp(op, 0 if ok else nb.CTL_BAD_ARGUMENT, 0, seq)
        elif op == tl.TX_OP_RATE and self.flags & 2:
            ok = 8000 <= arg <= 40000
            p["rate"] = arg if ok else p["rate"]
            self._resp(op, 0 if ok else nb.CTL_BAD_ARGUMENT, 0, seq)
        elif op == tl.TX_OP_DRIFT and self.flags & 2:
            v = arg - 65536 if arg >= 32768 else arg
            self._resp(op, 0 if -2000 <= v <= 2000 else nb.CTL_BAD_ARGUMENT, 0, seq)
            p["drift"] = v
        elif op == tl.TX_OP_TEMP and self.flags & 2:
            self._resp(op, 0, (arg << 24) | int(16 * (42.0 + 27.88 * (arg - 2) + 20.52) / 0.4386), seq)
        elif op == tl.TX_OP_LIMIT and self.flags & 2:
            ok = 1 <= arg <= 3600
            p["limit"] = arg if ok else p["limit"]
            self._resp(op, 0 if ok else nb.CTL_BAD_ARGUMENT, 0, seq)
        elif op == tl.TX_OP_BEGIN and self.flags & 2:
            word = tl.lo_word(p["lo"]) if p["lo"] else 0
            low = word & 0xFF
            if not p["lo"] or low < tl.TX_LOW_MARGIN or low > 255 - tl.TX_LOW_MARGIN:
                self._resp(op, nb.CTL_BAD_ARGUMENT, word, seq)
                return
            self._resp(op, 0, word, seq)
            self.state, self.begin_request_seq = "prefill", seq
            self.t_real, self.acc = time.monotonic(), 0.0
            self.ended_reason = None
            self.ring.clear()
            self.played = []
            self.under = self.late = 0
            self.vtime = self.last_rx = 0.0
            self.next_status = 0.005
        else:
            self._resp(op, nb.CTL_UNKNOWN_OP, 0, seq)

    # ---- the stream
    def _records(self):
        n = len(self.inp) // 4 * 4
        for i in range(0, n, 4):
            w = struct.unpack("<I", self.inp[i:i + 4])[0]
            if len(self.ring) < tl.TX_RING_RECORDS - 1:
                self.ring.append(w)
            self.sent_total += 1
            self.last_rx = self.vtime
        del self.inp[:n]

    def _status(self, carrier):
        f = bytearray([tl.TX_STATUS_MAGIC, self.seq & 0xFF, len(self.ring) & 0xFF, (len(self.ring) >> 8) & 0xFF, min(self.under, 255),
                       min(self.late, 255), 1 if carrier else 0, 0])
        f[7] = (0x100 - sum(f[:7])) & 0xFF
        self.seq += 1
        self.out += f

    def _advance(self):
        """Virtual time runs `speed` times faster than the clock; the chip works in steps of 5 ms."""
        now = time.monotonic()
        if self.state == "idle":
            self.t_real = now
            return
        if self.t_real is None:
            self.t_real = now
        self.acc += (now - self.t_real) * self.speed
        self.t_real = now
        while self.acc >= 0.005 and self.state != "idle":
            self.acc -= 0.005
            self._step()

    def _step(self):
        dt = 0.005
        self.vtime += dt
        rate = self.params["rate"]
        if self.state == "prefill":
            have_end = any((w >> 24) & 1 for w in self.ring)
            if len(self.ring) >= tl.TX_PREFILL or have_end:
                self.state = "running"
                self.vtime = 0.0
                self.carrier_since = 0.0
                self.last_rx = 0.0
                self.record = 127 << 16
                self._status(False)
                return
            if self.vtime > 3.0:
                self._finish(1)
                return
            self._status(False)
            return
        # running: play rate * dt records
        for _ in range(int(rate * dt)):
            if self.ring:
                self.record = self.ring.popleft()
            else:
                self.under += 1
                if self.vtime - self.last_rx > 0.5:
                    self._finish(1)
                    return
            self.played.append(self.record)
            if (self.record >> 24) & 1:
                self._finish(0)
                return
            if len(self.played) >= self.params["limit"] * rate:
                self._finish(2)
                return
        self._status(True)

    def _finish(self, reason):
        self.ended_reason = reason
        self.out += b""
        self._resp(tl.TX_OP_END, 0, reason << 24 | min(self.under, 255) << 16 | min(self.late, 0xFFFF), self.begin_request_seq)
        self.state = "idle"
