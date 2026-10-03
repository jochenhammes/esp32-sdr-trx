"""Audio sources for the transmitter: a WAV file, a pipe on stdin (WAV or raw samples), or a sound card.

Every source delivers mono float blocks at its own sample rate; `Mono8k` turns that into the 8 kHz stream the modulators take.
"""
import queue
import struct
import sys
import time

import numpy as np

from . import dsp

RAW_FORMATS = {"s16le": (np.dtype("<i2"), 32768.0), "f32le": (np.dtype("<f4"), 1.0), "u8": (np.dtype("u1"), None)}


class AudioError(Exception):
    pass


def _to_float(raw, dtype, scale, channels):
    a = np.frombuffer(raw, dtype=dtype)
    if scale is None:                                   # unsigned 8 bit
        a = (a.astype(np.float32) - 128.0) / 128.0
    else:
        a = a.astype(np.float32) / scale
    if channels > 1:
        a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
    return a


class StreamSource:
    """Raw or WAV audio read from a binary file object (a file, or stdin as a pipe)."""

    def __init__(self, fh, rate=None, channels=1, fmt="s16le", block_s=0.02, name="stdin"):
        self.fh, self.name, self.block_s = fh, name, block_s
        head = fh.read(4)
        if head == b"RIFF":
            self._read_wav_header()
        else:
            if rate is None:
                raise AudioError(f"{name}: raw audio needs --rate (and --channels, --format if they are not mono s16le)")
            if fmt not in RAW_FORMATS:
                raise AudioError(f"unknown format {fmt!r}; use one of {', '.join(RAW_FORMATS)}")
            self.rate, self.channels = int(rate), int(channels)
            self.dtype, self.scale = RAW_FORMATS[fmt]
            self.pending = head
            self.remaining = None

    def _read_wav_header(self):
        fh = self.fh
        fh.read(4)                                                        # RIFF size
        if fh.read(4) != b"WAVE":
            raise AudioError(f"{self.name}: not a WAV file")
        fmt = None
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                raise AudioError(f"{self.name}: no audio data in the WAV file")
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"fmt ":
                body = fh.read(size + (size & 1))
                tag, ch, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
                if tag == 0xFFFE and len(body) >= 26:                      # WAVE_FORMAT_EXTENSIBLE: the real tag is in the sub format
                    tag = struct.unpack("<H", body[24:26])[0]
                fmt = (tag, ch, rate, bits)
            elif cid == b"data":
                if fmt is None:
                    raise AudioError(f"{self.name}: the WAV data came before its format")
                tag, ch, rate, bits = fmt
                self.channels, self.rate = ch, rate
                if tag == 1 and bits == 16:
                    self.dtype, self.scale = np.dtype("<i2"), 32768.0
                elif tag == 1 and bits == 8:
                    self.dtype, self.scale = np.dtype("u1"), None
                elif tag == 3 and bits == 32:
                    self.dtype, self.scale = np.dtype("<f4"), 1.0
                else:
                    raise AudioError(f"{self.name}: WAV format tag {tag} with {bits} bits is not supported (use 16-bit PCM)")
                self.pending = b""
                self.remaining = None if size in (0, 0xFFFFFFFF) else size      # streaming writers leave the size open
                return
            else:
                fh.read(size + (size & 1))

    def blocks(self):
        width = self.dtype.itemsize * self.channels
        n = max(1, int(self.rate * self.block_s)) * width
        pending = self.pending
        while True:
            want = n - len(pending)
            if self.remaining is not None:
                want = min(want, self.remaining)
            chunk = self.fh.read(want) if want > 0 else b""
            if self.remaining is not None:
                self.remaining -= len(chunk)
            pending += chunk
            whole = len(pending) // width * width
            if whole >= n or (not chunk and whole):
                yield _to_float(pending[:whole], self.dtype, self.scale, self.channels)
                pending = pending[whole:]
            if not chunk:
                return

    def close(self):
        if self.fh not in (sys.stdin.buffer,):
            self.fh.close()


class SoundCardSource:
    """A sound card input through PortAudio (the optional `sounddevice` package)."""

    def __init__(self, device=None, rate=None, block_s=0.02):
        try:
            import sounddevice as sd
        except ImportError:
            raise AudioError("the sound card needs the sounddevice package: pip install 'esp32-sdr-trx[audio]' "
                             "(or: pipx inject esp32-sdr-trx sounddevice); on Linux also PortAudio: sudo apt install libportaudio2")
        self.sd = sd
        if device is not None and str(device).lstrip("-").isdigit():
            device = int(device)
        try:
            info = sd.query_devices(device, "input")
        except Exception as e:
            raise AudioError(f"no such sound card input {device!r} ({e}); `espdr-tx --list-devices` shows what there is")
        self.name = info["name"]
        self.rate = int(rate or info["default_samplerate"])
        self.q = queue.Queue(maxsize=200)
        self.overflows = 0
        self.stream = sd.InputStream(device=device, channels=1, samplerate=self.rate, dtype="float32",
                                     blocksize=max(1, int(self.rate * block_s)), callback=self._cb)

    def _cb(self, indata, frames, time_info, status):
        if status:
            self.overflows += 1
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.overflows += 1

    def blocks(self):
        self.stream.start()
        while True:
            try:
                yield self.q.get(timeout=1.0)
            except queue.Empty:
                raise AudioError(f"the sound card {self.name!r} delivers no audio")

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


def list_devices():
    try:
        import sounddevice as sd
    except ImportError:
        raise AudioError("the sound card needs the sounddevice package: pip install 'esp32-sdr-trx[audio]'")
    lines = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            lines.append(f"  {i:3d}  {d['name']}  ({d['max_input_channels']} in, {int(d['default_samplerate'])} Hz)")
    return lines


def open_source(spec, rate=None, channels=1, fmt="s16le", device=None):
    """spec: a file name, '-' for stdin, or 'soundcard' / 'soundcard:DEVICE' (number or name)."""
    if spec == "-":
        return StreamSource(sys.stdin.buffer, rate, channels, fmt, name="stdin")
    if spec == "soundcard" or spec.startswith("soundcard:"):
        dev = device if device is not None else (spec.split(":", 1)[1] if ":" in spec else None)
        return SoundCardSource(dev, rate)
    try:
        fh = open(spec, "rb")
    except OSError as e:
        raise AudioError(f"{spec}: {e.strerror or e}")
    return StreamSource(fh, rate, channels, fmt, name=spec)


class Mono8k:
    """Blocks of mono audio at the source's rate -> blocks of 8 kHz audio of about 20 ms."""

    def __init__(self, source, out_rate=8000):
        self.source = source
        self.rs = dsp.Resampler(source.rate, out_rate) if source.rate != out_rate else None

    def blocks(self):
        for b in self.source.blocks():
            y = self.rs.process(b) if self.rs else b
            if len(y):
                yield y.astype(float)

    def trim(self, ppm):
        if self.rs:
            self.rs.trim = ppm


def test_tone(freq=1000.0, level=0.5, seconds=None, block_s=0.02, rate=8000):
    """A sine as an 8 kHz source (for --test-tone); endless when seconds is None."""
    n = int(rate * block_s)
    i = 0
    total = None if seconds is None else int(seconds * rate)
    while total is None or i < total:
        k = n if total is None else min(n, total - i)
        t = (np.arange(i, i + k)) / rate
        yield level * np.sin(2 * np.pi * freq * t)
        i += k
