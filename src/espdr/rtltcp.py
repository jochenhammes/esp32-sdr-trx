#!/usr/bin/env python3
"""The rtl_tcp server for the narrowband ESP32-S3 receiver (the library behind `espdr-rx`).

SDR++ ("RTL-TCP" source), pluto-advanced-rx (RTL-SDR device, connection "127.0.0.1:1234") and anything else that speaks the rtl_tcp
protocol can use the ESP32-S3 without changes. One client at a time; the radio runs only while a client is connected.

The radio delivers 250 ksps (+-100 kHz usable) or, with decim 3, 333.3 ksps (+-133 kHz). The sample rate a client asks for is produced by
filtered interpolation, so any rate from 1 MS/s to 3.2 MS/s works; asking for exactly the radio's rate passes the data through.
Frequency and gain changes restart the ESP's run (the radio is reconfigured between runs), which leaves a short gap.
"""
import queue
import socket
import struct
import sys
import threading
import time

import numpy as np

from . import nb

HEADER = b"RTL0" + struct.pack(">II", 5, 29)  # "R820T" tuner with 29 gain steps: what clients expect to see
SRC_RATES = {3: 1e6 / 3, 4: 250_000.0}        # ESP output per --decim
LO_MIN, LO_MAX = 1_841_666_667, 2_790_000_000  # ESP tuning range, Hz (ESP_LO_MIN_HZ / ESP_LO_MAX_HZ in protocol/control.h)
# R820T gain table in tenths of a dB, for rtl_tcp's "set gain by index"
GAINS_TENTHS = (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254, 280, 297, 328, 338, 364, 372,
                386, 402, 421, 434, 439, 445, 480, 496)
CMD_FREQ, CMD_RATE, CMD_GAIN_MODE, CMD_GAIN, CMD_PPM, CMD_AGC, CMD_GAIN_INDEX = 0x01, 0x02, 0x03, 0x04, 0x05, 0x08, 0x0D
DEBOUNCE_S = 0.05      # SDR++ sends many frequency commands while dragging: restart the radio once they stop
SETTLE_S = 0.15        # after a connect, wait for the client's initial commands
FLUSH_SECONDS = 0.01   # process the stream in 10 ms blocks
DC_TAU_S = 0.5


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --- signal path ---------------------------------------------------------------------------------------
def _design_up4():
    """100-tap windowed-sinc low-pass for x4 interpolation, cut at the input's Nyquist frequency (250 k -> 1 MS/s: passband
    100 kHz, images from 150 kHz down >70 dB; 333 k -> 1.33 MS/s: the same, scaled)."""
    n = 100
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * 0.125 * t) * np.kaiser(n, 8.0)
    h *= 4 / h.sum()
    return [h[p::4].astype(np.float32) for p in range(4)]


_UP4 = _design_up4()


class Up4:
    """Streaming x4 polyphase interpolator."""

    def __init__(self):
        self.k = len(_UP4[0])
        self.tail = np.zeros(self.k - 1, np.complex64)

    def process(self, z):
        xin = np.concatenate((self.tail, z))
        self.tail = xin[len(xin) - (self.k - 1):].copy()
        out = np.empty(4 * len(z), np.complex64)
        for p in range(4):
            out[p::4] = np.convolve(xin, _UP4[p], mode="valid")
        return out


class Fractional:
    """Streaming cubic-Lagrange resampler; `step` input samples per output sample."""

    def __init__(self, step):
        self.step = float(step)
        self.tail = np.zeros(3, np.complex64)
        self.t0 = 1.0

    def process(self, z):
        x = np.concatenate((self.tail, z))
        n_out = int(np.floor((len(x) - 3 - self.t0) / self.step)) + 1
        if n_out <= 0:
            self.tail = x
            self.t0 -= len(z)
            return np.zeros(0, np.complex64)
        t = self.t0 + self.step * np.arange(n_out)
        idx = t.astype(np.int64)
        mu = (t - idx).astype(np.float32)
        y0, y1, y2, y3 = x[idx - 1], x[idx], x[idx + 1], x[idx + 2]
        c1 = -y0 / 3 - y1 / 2 + y2 - y3 / 6
        c2 = y0 / 2 - y1 + y2 / 2
        c3 = -y0 / 6 + y1 / 2 - y2 / 2 + y3 / 6
        out = ((c3 * mu + c2) * mu + c1) * mu + y1
        self.t0 += n_out * self.step - (len(x) - 3)
        self.tail = x[len(x) - 3:].copy()
        return out.astype(np.complex64)


class Chain:
    """ESP samples (complex, LO-minus-RF) -> unsigned 8-bit rtl_tcp stream at `dst_rate`."""

    def __init__(self, dst_rate, shift_hz=0.0, dc_block=True, src_rate=250_000.0):
        self.src_rate = src_rate
        self.shift_w = 2 * np.pi * shift_hz / src_rate
        self.phase = 0.0
        self.dc_block = dc_block
        self.dc = None
        self.stages = []
        if abs(dst_rate - src_rate) < 1:
            pass
        elif dst_rate <= 1.2 * src_rate:
            self.stages = [Fractional(src_rate / dst_rate)]
        else:
            self.stages = [Up4()]
            if abs(dst_rate - 4 * src_rate) > 1:
                self.stages.append(Fractional(4 * src_rate / dst_rate))

    def process(self, z):
        z = np.conj(z)  # the radio delivers LO minus RF; clients expect a higher RF to be a higher frequency
        if self.dc_block:
            m = z.mean()
            a = min(1.0, len(z) / (DC_TAU_S * self.src_rate))
            self.dc = m if self.dc is None else self.dc + a * (m - self.dc)
            z = z - self.dc
        if abs(self.shift_w) > 1e-9:
            ph = self.phase + self.shift_w * np.arange(len(z))
            z = z * np.exp(1j * ph).astype(np.complex64)
            self.phase = float((self.phase + self.shift_w * len(z)) % (2 * np.pi))
        for st in self.stages:
            z = st.process(z)
        out = np.empty(2 * len(z), np.float32)
        out[0::2], out[1::2] = z.real, z.imag
        return np.clip(np.floor(out + 128.0), 0, 255).astype(np.uint8).tobytes()


# --- shared settings --------------------------------------------------------------------------------------
class Params:
    def __init__(self, freq, ppm, gain, rate):
        self.lock = threading.Lock()
        self.freq, self.ppm, self.gain, self.rate = freq, ppm, gain, rate
        self.hw_version = 0
        self.last_change = time.monotonic()

    def set(self, **kw):
        with self.lock:
            hw = False
            for k, v in kw.items():
                if getattr(self, k) != v:
                    setattr(self, k, v)
                    hw |= k in ("freq", "ppm", "gain")
            if hw:
                self.hw_version += 1
            self.last_change = time.monotonic()

    def snapshot(self):
        with self.lock:
            return self.freq, self.ppm, self.gain, self.rate, self.hw_version

    def settled(self, since):
        with self.lock:
            return time.monotonic() - self.last_change >= since


class Session:
    """One connected rtl_tcp client: a reader thread for its commands, a writer thread for the stream."""

    def __init__(self, conn, bridge):
        self.conn, self.bridge = conn, bridge
        self.closed = threading.Event()
        self.q = queue.Queue(maxsize=300)
        self.dropped = 0
        conn.settimeout(None)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.sendall(HEADER)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._write, daemon=True).start()

    def push(self, data):
        try:
            self.q.put_nowait(data)
        except queue.Full:
            self.dropped += 1

    def close(self):
        self.closed.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.conn.close()

    def _write(self):
        try:
            while not self.closed.is_set():
                try:
                    data = self.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                self.conn.sendall(data)
        except OSError:
            pass
        self.closed.set()

    def _read(self):
        buf = b""
        try:
            while not self.closed.is_set():
                chunk = self.conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 5:
                    cmd, param = struct.unpack(">BI", buf[:5])
                    buf = buf[5:]
                    self.bridge.command(cmd, param)
        except OSError:
            pass
        self.closed.set()


# --- bridge -----------------------------------------------------------------------------------------------
class Bridge:
    def __init__(self, open_link, listen, ppm=0.0, gain=60, gain_range=(30, 80), dc_block=True, decim=4):
        self.open_link = open_link
        self.decim, self.src_rate = decim, SRC_RATES[decim]
        self.base_ppm = ppm  # --ppm: this unit's crystal; a client's ppm command adds to it
        self.params = Params(2_400_000_000, ppm, gain, 2_400_000)
        self.gain_range = gain_range
        self.dc_block = dc_block
        self.quit = False
        self.session = None
        self.last_good = 2_400_000_000  # the last frequency the ESP accepted (2.4 GHz is where it works best)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(listen)
        self.srv.listen(1)
        self.srv.settimeout(0.2)
        self.address = self.srv.getsockname()
        self.stats = dict(restarts=0, failures=0)

    # -- rtl_tcp commands
    def _gain_sel(self, tenths_db):
        lo, hi = self.gain_range
        return int(round(lo + (hi - lo) * max(0, min(496, tenths_db)) / 496.0))

    def command(self, cmd, param):
        if cmd == CMD_FREQ:
            f = int(param)
            if not LO_MIN <= f <= LO_MAX:
                log(f"frequency {f / 1e6:.3f} MHz is outside the ESP's {LO_MIN / 1e9:.2f}-{LO_MAX / 1e9:.2f} GHz range")
                f = min(LO_MAX, max(LO_MIN, f))
            self.params.set(freq=f)
        elif cmd == CMD_RATE:
            self.params.set(rate=int(param))
        elif cmd == CMD_PPM:
            self.params.set(ppm=self.base_ppm + struct.unpack(">i", struct.pack(">I", param))[0])
        elif cmd == CMD_GAIN:
            self.params.set(gain=self._gain_sel(param))
        elif cmd == CMD_GAIN_INDEX:
            self.params.set(gain=self._gain_sel(GAINS_TENTHS[min(param, len(GAINS_TENTHS) - 1)]))
        # 0x03/0x08 (gain/AGC mode) and the rest: the ESP has manual gain only; nothing to do

    # -- accepting clients
    def serve(self):
        while not self.quit:
            try:
                conn, addr = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            log(f"client {addr[0]}:{addr[1]} connected")
            s = Session(conn, self)
            self.session = s
            s.closed.wait()
            self.session = None
            s.close()
            log(f"client left (dropped {s.dropped} blocks)")

    # -- the ESP side
    def run(self):
        threading.Thread(target=self.serve, daemon=True).start()
        link = None
        try:
            while not self.quit:
                s = self.session
                if s is None or s.closed.is_set():
                    time.sleep(0.05)
                    continue
                if link is None:
                    try:
                        link = self.open_link()
                    except Exception as e:  # noqa: BLE001 - the port may be missing for a while
                        log("cannot open the ESP:", e)
                        time.sleep(1.0)
                        continue
                try:
                    self.stream(link, s)
                except (nb.ProtocolError, OSError) as e:
                    log("ESP link error:", e)
                    link = None
                    time.sleep(1.0)
        finally:
            self.quit = True

    def stream(self, link, s):
        """Runs the ESP for as long as the client stays; restarts the run when frequency or gain change."""
        deadline = time.monotonic() + 1.0
        while not self.params.settled(SETTLE_S) and time.monotonic() < deadline:
            time.sleep(0.02)
        first = True
        while not (s.closed.is_set() or self.quit):
            freq, ppm, gain, _, version = self.params.snapshot()
            hw_freq = int(round(freq / (1 + ppm * 1e-6)))
            t0 = time.monotonic()
            try:
                lo = nb.configure_receiver(link, hw_freq, self.decim, nb.FORMATS["cs8"], 4, gain)
            except nb.CommandError as e:
                if e.op != nb.ESP_SET_LO:
                    raise
                log(f"the ESP refused {freq / 1e6:.3f} MHz ({e}); staying at {self.last_good / 1e6:.3f} MHz")
                self.params.set(freq=self.last_good)
                continue
            self.last_good = freq
            # Where the LO really is (nominal clock: lo; actual crystal: lo*(1+ppm)) against where the client asked:
            shift = lo * (1 + ppm * 1e-6) - freq
            log(f"tuned {freq / 1e6:.6f} MHz (LO {lo / 1e6:.6f} MHz, fine shift {shift:+.0f} Hz), gain {gain}"
                + ("" if first else f", retune took {time.monotonic() - t0:.2f} s"))
            first = False
            sink = Sink(self, s, shift)
            stop = lambda: (s.closed.is_set() or self.quit or
                            (self.params.hw_version != version and self.params.settled(DEBOUNCE_S)))
            status, value, stats = link.run(0, sink, stop_flag=stop)
            sink.flush()
            if value:
                self.stats["failures"] += 1
                log(f"ESP run failed: code {value} ({nb.FAIL_NAMES.get(value, '?')}); restarting")
                time.sleep(0.3)
            elif stats["lost"] or stats["dropped"]:
                log(f"{stats['lost']} samples lost in this run ({stats['dropped']} units dropped by the ESP)")
            self.stats["restarts"] += 1


class Sink:
    """Collects ESP packets into 10 ms blocks, runs them through the chain and hands them to the client."""

    def __init__(self, bridge, session, shift_hz):
        self.bridge, self.s = bridge, session
        self.shift_hz = shift_hz
        self.rate = None
        self.chain = None
        self.parts, self.n = [], 0
        self.expected = None

    def _chain(self):
        rate = self.bridge.params.snapshot()[3]
        if rate != self.rate:
            self.rate = rate
            old = self.chain
            self.chain = Chain(rate, self.shift_hz, self.bridge.dc_block, self.bridge.src_rate)
            if old is not None:
                self.chain.dc = old.dc
        return self.chain

    def __call__(self, header, payload):
        if self.expected is not None and header["first"] != self.expected:
            lost = min((header["first"] - self.expected) & 0xFFFFFFFF, int(self.bridge.src_rate))
            self.parts.append(np.zeros(lost, np.complex64))  # keep the time base: a gap becomes silence
            self.n += lost
        self.expected = (header["first"] + header["count"]) & 0xFFFFFFFF
        if header["format"] == 1:
            a = np.frombuffer(payload, dtype=np.int8).astype(np.float32)
        else:
            a = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 256.0
        self.parts.append(a[0::2] + 1j * a[1::2])
        self.n += header["count"]
        if self.n >= self.bridge.src_rate * FLUSH_SECONDS:
            self.flush()

    def flush(self):
        if not self.parts:
            return
        z = np.concatenate(self.parts).astype(np.complex64)
        self.parts, self.n = [], 0
        data = self._chain().process(z)
        if data:
            self.s.push(data)


# --- command line -----------------------------------------------------------------------------------------
find_port = nb.find_port


def parse_listen(text):
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


# --- selftest (simulated ESP, no hardware) -----------------------------------------------------------------
class SimEsp:
    """Plays the ESP for the bridge: a transmitter at `rf_hz`, seen through the LO it is set to, in the radio's
    LO-minus-RF convention, with a DC offset and noise; paced `speed` times the real rate (250 ksps, or what NB_SET_DECIM selects)."""
    LO_STEP = 381.4697265625

    def __init__(self, rf_hz, speed=1.0):
        self.rf_hz, self.speed = rf_hz, speed
        self.ops = []                  # (op, arg) in arrival order
        self.out = bytearray()
        self.lo = 0.0
        self.running = False
        self.stopping = False
        self.t_last = 0.0
        self.sample = 0
        self.seq = 0
        self.arg_high = 0
        self.rng = np.random.default_rng(1)
        self.r2 = 4

    @property
    def in_waiting(self):
        self._pump()
        return len(self.out)

    def _resp(self, op, status=0, value=0):
        head = struct.pack("<BBBBHHI", nb.RESP_MAGIC, 1, op, status, 0, 0, value)
        return head + struct.pack("<I", nb.zlib.crc32(head))

    def write(self, data):
        data = bytes(data)
        op, arg = data[1], struct.unpack("<H", data[2:4])[0]
        if op == nb.ESP_ARG_HIGH:
            self.arg_high = arg << 16
            self.out += self._resp(op)
            return
        full = self.arg_high | arg
        self.arg_high = 0
        self.ops.append((op, full))
        if op == nb.CTL_INFO:
            self.out += self._resp(op, 0, nb.FIRMWARE_ID)
        elif op == nb.ESP_SET_LO and full < 1_848_000_000:  # like the test board: no PLL lock down there
            self.out += self._resp(op, 6, 0)
        elif op == nb.ESP_SET_LO:
            self.lo = round(full / self.LO_STEP) * self.LO_STEP
            self.out += self._resp(op, 0, int(round(self.lo)))
        elif op == nb.NB_SET_DECIM:
            self.r2 = full
            self.out += self._resp(op, 0, full)
        elif op == nb.ESP_RUN:
            self.running, self.stopping, self.t_last = True, False, time.monotonic()
        elif op == nb.ESP_STOP:
            self.stopping = True
            self.out += self._resp(op)
        else:
            self.out += self._resp(op)

    def read(self, n):
        self._pump()
        if not self.out:
            time.sleep(0.002)
        chunk = bytes(self.out[:n])
        del self.out[:n]
        return chunk

    def _pump(self):
        if self.running:
            now = time.monotonic()
            rate = 1e6 / self.r2
            count = int((now - self.t_last) * rate * self.speed)
            if count >= 240:
                count = min(count, 480)
                self.t_last = now
                k = np.arange(self.sample, self.sample + count)
                f_base = -(self.rf_hz - self.lo)            # LO minus RF
                z = 40 * np.exp(2j * np.pi * f_base / rate * k) + (-6 + 4j)
                z += self.rng.normal(0, 2, count) + 1j * self.rng.normal(0, 2, count)
                iq = np.empty(2 * count, np.int8)
                iq[0::2], iq[1::2] = np.clip(np.round(z.real), -128, 127), np.clip(np.round(z.imag), -128, 127)
                words = [0xE5 | 0x5D << 8, 1 | self.r2 << 8, count, 0, self.seq & 0xFFFF, self.seq >> 16,
                         self.sample & 0xFFFF, self.sample >> 16, 4, 0]
                words[9] = 0xFFFF - (sum(words[:9]) & 0xFFFF)
                self.out += struct.pack("<10H", *words) + iq.tobytes()
                self.sample += count
                self.seq += 1
            if self.stopping:
                self.out += self._resp(nb.ESP_RUN)
                self.running = False


def _tone(u8, rate):
    """(frequency Hz, level dB over the median bin, strongest spur far from the tone in dB below it)."""
    z = (u8[0::2].astype(np.float32) - 127.5) + 1j * (u8[1::2].astype(np.float32) - 127.5)
    z = z[len(z) // 4:]
    w = np.hanning(len(z))
    spec = np.abs(np.fft.fftshift(np.fft.fft(z * w))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(len(z), 1 / rate))
    i = int(np.argmax(spec))
    far = (np.abs(f - f[i]) > 20e3) & (np.abs(f) > 3e3)  # the DC bin is judged on its own
    return f[i], 10 * np.log10(spec[i] / np.median(spec)), 10 * np.log10(spec[i] / spec[far].max())


def _collect(sock, seconds_of_samples, rate):
    need = int(seconds_of_samples * rate) * 2
    got = bytearray()
    sock.settimeout(5.0)
    while len(got) < need:
        chunk = sock.recv(1 << 18)
        if not chunk:
            break
        got += chunk
    return np.frombuffer(bytes(got[:need]), np.uint8)


def _drain(sock, seconds):
    """Discards what arrives for a while: the stream never ends, so wait for a time, not for silence."""
    end = time.monotonic() + seconds
    sock.settimeout(0.2)
    while time.monotonic() < end:
        try:
            if not sock.recv(1 << 20):
                return
        except socket.timeout:
            pass


def selftest():
    ok = _selftest(4) & _selftest(3)
    print("SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def _selftest(decim):
    ok = True
    src = int(round(SRC_RATES[decim]))
    print(f"--- radio at {src / 1e3:.0f} ksps (--decim {decim})")

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"{name}: {'OK' if good else 'FAIL'} {detail}")

    sim = SimEsp(rf_hz=2_400_050_000)
    bridge = Bridge(lambda: nb.Link(sim), ("127.0.0.1", 0), ppm=0.0, decim=decim)
    threading.Thread(target=bridge.run, daemon=True).start()

    def connect():
        s = socket.create_connection(bridge.address[:2], timeout=5)
        hdr = b""
        while len(hdr) < 12:
            hdr += s.recv(12 - len(hdr))
        return s, hdr

    def cmd(s, c, p):
        s.sendall(struct.pack(">BI", c, p & 0xFFFFFFFF))

    s, hdr = connect()
    check("header", hdr == b"RTL0" + struct.pack(">II", 5, 29), repr(hdr))
    for rate in (src, 1_024_000, 2_400_000):
        cmd(s, CMD_RATE, rate)
        cmd(s, CMD_FREQ, 2_400_000_000)
        _drain(s, 0.6)                         # let the new rate take effect, drop the old one
        f, level, spur = _tone(_collect(s, 0.4, rate), rate)
        # transmitter 50 kHz above the LO; chain fine-shifts by the LO rounding, so the tone must be at +50 kHz
        check(f"tone at {rate / 1e6:g} MS/s", abs(f - 50_000) < 400 and level > 25 and spur > 45,
              f"{f:+.0f} Hz, {level:.0f} dB over noise, spurs {spur:.0f} dB down")
    u8 = _collect(s, 0.3, 2_400_000)
    check("DC removed", abs(u8[0::2].mean() - 127.5) < 1.5 and abs(u8[1::2].mean() - 127.5) < 1.5,
          f"mean I {u8[0::2].mean():.1f} Q {u8[1::2].mean():.1f}")

    n_lo = sum(1 for op, _ in sim.ops if op == nb.ESP_SET_LO)
    cmd(s, CMD_FREQ, 2_400_020_000)
    cmd(s, CMD_GAIN, 300)
    _drain(s, 1.2)
    lo_cmds = [a for op, a in sim.ops if op == nb.ESP_SET_LO]
    gain_cmds = [a for op, a in sim.ops if op == nb.ESP_SET_GAIN]
    check("retune restarts the run", len(lo_cmds) == n_lo + 1 and lo_cmds[-1] == 2_400_020_000
          and gain_cmds[-1] == bridge._gain_sel(300), f"LO commands {lo_cmds[-2:]}, gain {gain_cmds[-1]}")
    f, level, _ = _tone(_collect(s, 0.4, 2_400_000), 2_400_000)
    check("tone after retune", abs(f - 30_000) < 400, f"{f:+.0f} Hz (transmitter 30 kHz above the new centre)")
    cmd(s, CMD_FREQ, 1_700_000_000)            # below the ESP's range: clamped to its lower limit, not refused
    _drain(s, 1.0)
    lo_cmds = [a for op, a in sim.ops if op == nb.ESP_SET_LO]
    check("frequency below the range is clamped", LO_MIN in lo_cmds and LO_MIN == 1_841_666_667, f"LO commands {lo_cmds[-3:]}")
    # the simulated ESP refuses that (no PLL lock, as on the test board): the bridge goes back to the last good frequency
    # and keeps streaming
    f, level, _ = _tone(_collect(s, 0.4, src), src)
    check("refused frequency falls back and keeps streaming", lo_cmds[-1] == 2_400_020_000 and level > 25,
          f"last LO command {lo_cmds[-1]}, {level:.0f} dB")
    cmd(s, CMD_FREQ, 2_400_000_000)
    s.close()
    time.sleep(0.5)
    s, hdr = connect()
    cmd(s, CMD_RATE, src)
    f, level, _ = _tone(_collect(s, 0.4, src), src)
    check("reconnect", hdr[:4] == b"RTL0" and level > 25, f"{f:+.0f} Hz")
    s.close()
    bridge.quit = True
    time.sleep(0.3)
    return ok
