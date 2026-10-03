import io
import struct

import numpy as np
import pytest

from espdr import audio


def wav_bytes(samples, rate=16000, channels=1, size=None):
    data = np.asarray(samples, dtype="<i2").tobytes()
    n = len(data) if size is None else size
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16)
    return b"RIFF" + struct.pack("<I", (36 + n) & 0xFFFFFFFF) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", n) + data


def collect(src):
    return np.concatenate(list(src.blocks()))


def test_wav_stream_is_read_to_the_end():
    x = (3000 * np.sin(2 * np.pi * 500 * np.arange(8000) / 16000)).astype(int)
    src = audio.StreamSource(io.BytesIO(wav_bytes(x)))
    y = collect(src)
    assert src.rate == 16000 and len(y) == 8000
    assert np.allclose(y, x / 32768.0, atol=1e-4)


def test_streaming_wav_with_an_open_size_reads_until_eof():
    x = np.arange(1000) % 200
    for open_size in (0, 0xFFFFFFFF):
        src = audio.StreamSource(io.BytesIO(wav_bytes(x, size=open_size)))
        assert len(collect(src)) == 1000


def test_stereo_is_mixed_to_mono():
    left = np.full(100, 8000)
    right = np.full(100, -8000)
    st = np.column_stack([left, right]).ravel()
    y = collect(audio.StreamSource(io.BytesIO(wav_bytes(st, channels=2))))
    assert len(y) == 100 and np.abs(y).max() < 1e-6


def test_raw_audio_needs_a_rate():
    with pytest.raises(audio.AudioError):
        audio.StreamSource(io.BytesIO(b"\x01\x02\x03\x04" * 100))
    src = audio.StreamSource(io.BytesIO((np.arange(400) % 50).astype("<i2").tobytes()), rate=8000)
    assert len(collect(src)) == 400


def test_raw_float_samples():
    x = np.linspace(-1, 1, 500).astype("<f4")
    y = collect(audio.StreamSource(io.BytesIO(x.tobytes()), rate=8000, fmt="f32le"))
    assert np.allclose(y, x)


def test_unsupported_wav_is_explained():
    bad = bytearray(wav_bytes(np.zeros(10)))
    bad[34:36] = struct.pack("<H", 24)                                    # 24 bits
    with pytest.raises(audio.AudioError, match="not supported"):
        audio.StreamSource(io.BytesIO(bytes(bad)))


def test_mono8k_resamples_to_8khz():
    x = (3000 * np.sin(2 * np.pi * 440 * np.arange(44100) / 44100)).astype(int)
    src = audio.StreamSource(io.BytesIO(wav_bytes(x, rate=44100)))
    y = np.concatenate(list(audio.Mono8k(src).blocks()))
    assert 7900 < len(y) <= 8000


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(audio.AudioError, match="No such file|no such file|not exist|Errno"):
        audio.open_source(str(tmp_path / "nope.wav"))
