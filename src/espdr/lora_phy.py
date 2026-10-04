"""A LoRa PHY (chirp spread spectrum) in numpy (research, PLAN-LORA-IQ.md): transmitter and receiver for any SF 5..12 and any bandwidth, independent of gr-lora_sdr.

Why: gr-lora_sdr (the build of pluto-tx) decodes SF7 and up but nothing at SF5 and SF6 at any bandwidth (L0a of PLAN-LORA-IQ.md), and wide
bandwidths are untested there. The transmit chain follows gr-lora_sdr step by step (whitening, explicit header, CRC16, Hamming, diagonal interleaver,
Gray, chirp modulation; the SX127x layout) and is checked against it at SF7 (scripts/lora_iq/selftest.py). SF5 and SF6 use the same rules with the
formulas for SF (the first interleaver block has SF-2 rows and coding rate 4/8, later blocks SF rows).

Receiver: low-pass and decimate to the chip rate, find the preamble by dechirping overlapping windows, timing and frequency offset from the preamble
and the two SFD down-chirps, then one FFT per payload symbol.
"""
import numpy as np

WHITENING = None


def whitening_sequence():
    global WHITENING
    if WHITENING is None:
        s = [0xFF]
        for _ in range(254):
            c = s[-1]
            s.append(((c << 1) & 0xFF) | (((c >> 7) ^ (c >> 5) ^ (c >> 4) ^ (c >> 3)) & 1))
        WHITENING = s
    return WHITENING


def crc16(data):
    """The CRC of the LoRa payload: CRC-16 (poly 0x1021) over all but the last two bytes, xored with the last two (the SX127x quirk, as in gr-lora_sdr)."""
    crc = 0
    for b in data[:-2]:
        for i in range(8):
            if ((crc & 0x8000) >> 8) ^ (b & 0x80):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
            b = (b << 1) & 0xFF
    if len(data) >= 2:
        crc ^= data[-1] ^ (data[-2] << 8)
    return crc & 0xFFFF


def sync_symbols(sync_word):
    """A sync word byte 0xAB becomes the two symbol values (A << 3, B << 3)."""
    if isinstance(sync_word, (tuple, list)):
        return list(sync_word)
    return [((sync_word & 0xF0) >> 4) << 3, (sync_word & 0x0F) << 3]


def header_nibbles(payload_len, cr, has_crc):
    h0, h1, h2 = payload_len >> 4, payload_len & 0x0F, (cr << 1) | int(has_crc)
    b = lambda v, k: (v >> k) & 1
    c4 = b(h0, 3) ^ b(h0, 2) ^ b(h0, 1) ^ b(h0, 0)
    c3 = b(h0, 3) ^ b(h1, 3) ^ b(h1, 2) ^ b(h1, 1) ^ b(h2, 0)
    c2 = b(h0, 2) ^ b(h1, 3) ^ b(h1, 0) ^ b(h2, 3) ^ b(h2, 1)
    c1 = b(h0, 1) ^ b(h1, 2) ^ b(h1, 0) ^ b(h2, 2) ^ b(h2, 1) ^ b(h2, 0)
    c0 = b(h0, 0) ^ b(h1, 1) ^ b(h2, 3) ^ b(h2, 2) ^ b(h2, 1) ^ b(h2, 0)
    return [h0, h1, h2, c4, (c3 << 3) | (c2 << 2) | (c1 << 1) | c0]


def hamming_encode(nib, cr_app):
    b0, b1, b2, b3 = nib & 1, (nib >> 1) & 1, (nib >> 2) & 1, (nib >> 3) & 1
    if cr_app == 1:
        return (b0 << 4) | (b1 << 3) | (b2 << 2) | (b3 << 1) | (b0 ^ b1 ^ b2 ^ b3)
    p0, p1, p2, p3 = b0 ^ b1 ^ b2, b1 ^ b2 ^ b3, b0 ^ b1 ^ b3, b0 ^ b2 ^ b3
    return ((b0 << 7) | (b1 << 6) | (b2 << 5) | (b3 << 4) | (p0 << 3) | (p1 << 2) | (p2 << 1) | p3) >> (4 - cr_app)


def hamming_decode(cw, cr_app):
    """The nibble of a codeword (no error correction beyond the single-error syndrome of 4/7 and 4/8)."""
    n = 4 + cr_app
    bits = [(cw >> (n - 1 - k)) & 1 for k in range(n)]
    d = bits[:4]
    if cr_app >= 3:
        p = bits[4:]
        # syndrome decoding of the (7,4)/(8,4) code: try flipping each data bit and keep the one that matches the parities
        def parities(d):
            b0, b1, b2, b3 = d
            return [b0 ^ b1 ^ b2, b1 ^ b2 ^ b3, b0 ^ b1 ^ b3, b0 ^ b2 ^ b3][:len(p)]
        if parities(d) != p:
            for k in range(4):
                e = list(d)
                e[k] ^= 1
                if parities(e) == p:
                    d = e
                    break
    return d[0] | (d[1] << 1) | (d[2] << 2) | (d[3] << 3)


def frame_symbols(payload, sf, cr=1, has_crc=True, ldro=False):
    """The symbol values (0..2^SF-1) of a frame with explicit header, in transmission order."""
    seq = whitening_sequence()
    nib = header_nibbles(len(payload), cr, has_crc)
    for i, b in enumerate(payload):
        w = b ^ seq[i]
        nib += [w & 0x0F, w >> 4]
    if has_crc:
        crc = crc16(payload)
        nib += [crc & 0xF, (crc >> 4) & 0xF, (crc >> 8) & 0xF, (crc >> 12) & 0xF]
    syms = []
    pos = 0
    first = True
    while pos < len(nib):
        sf_app = sf - 2 if (first or ldro) else sf
        cw_len = 8 if first else 4 + cr
        block = nib[pos:pos + sf_app]
        cws = [hamming_encode(n, 4 if first else cr) for n in block] + [0] * (sf_app - len(block))
        # codeword bits, MSB first
        cb = [[(cw >> (cw_len - 1 - k)) & 1 for k in range(cw_len)] for cw in cws]
        for i in range(cw_len):
            bits = [cb[(i - j - 1) % sf_app][i] for j in range(sf_app)]
            bits += [0] * (sf - sf_app)
            if first or ldro:
                bits[sf_app] = sum(bits[:sf_app]) % 2
            v = 0
            for bt in bits:
                v = (v << 1) | bt
            g = v
            for j in range(1, sf):
                g ^= v >> j
            syms.append((g + 1) % (1 << sf))
        pos += sf_app
        first = False
    return syms


def upchirp(sym, sf, os_=1):
    N = 1 << sf
    n = np.arange(N * os_)
    nf = N * os_ - sym * os_
    ph = n * n / (2.0 * N) / os_ ** 2 + np.where(n < nf, sym / N - 0.5, sym / N - 1.5) * n / os_
    return np.exp(2j * np.pi * ph)


def frame_iq(payload, sf, bw, os_=1, cr=1, preamble=8, sync_word=0x34, has_crc=True, ldro=False):
    """The complete frame at fs = bw * os_ (no padding): preamble, two sync symbols, 2.25 down-chirps, payload symbols. Unit amplitude."""
    N = 1 << sf
    up0 = upchirp(0, sf, os_)
    down = np.conj(up0)
    sw = sync_symbols(sync_word)
    parts = [up0] * preamble + [upchirp(sw[0], sf, os_), upchirp(sw[1], sf, os_), down, down, down[:N * os_ // 4]]
    parts += [upchirp(s, sf, os_) for s in frame_symbols(payload, sf, cr, has_crc, ldro)]
    return np.concatenate(parts).astype(np.complex64)


def frame_samples(payload_len, sf, os_, cr=1, preamble=8, has_crc=True, ldro=False):
    n = len(frame_symbols(bytes(payload_len), sf, cr, has_crc, ldro))
    return int(round((preamble + 4.25 + n) * (1 << sf) * os_))


def symbols_per_frame(payload_len, sf, cr=1, preamble=8, has_crc=True, ldro=False):
    return preamble + 4.25 + len(frame_symbols(bytes(payload_len), sf, cr, has_crc, ldro))


def frame_track(payload, sf, bw, rate=40000.0, cr=1, preamble=8, sync_word=0x34, has_crc=True, ldro=False):
    """The frequency (Hz, against the channel centre) of the frame at every update of a polar transmitter: one value per 1 / rate, taken at the middle of the update.
    The same frame as frame_iq(); the chirp is followed with the frequency only (constant amplitude)."""
    N = 1 << sf
    sw = sync_symbols(sync_word)
    syms = [("up", 0)] * preamble + [("up", sw[0]), ("up", sw[1]), ("down", 0), ("down", 0), ("downq", 0)]
    syms += [("up", v) for v in frame_symbols(payload, sf, cr, has_crc, ldro)]
    dur = np.array([N * 0.25 if t == "downq" else N for t, _ in syms], dtype=float)
    starts = np.concatenate([[0.0], np.cumsum(dur)])
    n = int(np.ceil(starts[-1] / bw * rate))
    fine = 16                                                    # the frequency of an update is the mean over its interval: the phase at every update boundary is exact,
    chips = (np.arange(n * fine) + 0.5) / (rate * fine) * bw     # including the update that holds the wrap of the chirp (time in chips)
    k = np.minimum(np.searchsorted(starts, chips, side="right") - 1, len(syms) - 1)
    c = chips - starts[k]
    kinds = np.array([0 if t == "up" else 1 for t, _ in syms])
    ids = np.array([v for _, v in syms], dtype=float)
    up = ((c + ids[k]) % N) / N - 0.5
    down = 0.5 - c / N
    f = np.where(kinds[k] == 0, up, down) * bw
    return f.reshape(n, fine).mean(axis=1)


# --- receiver ----------------------------------------------------------------------------------------------------------------------------

def _parabolic(mag, k):
    """Interpolated peak position (bins) around index k of a magnitude array (circular)."""
    N = len(mag)
    a, b, c = np.log(mag[(k - 1) % N] + 1e-12), np.log(mag[k] + 1e-12), np.log(mag[(k + 1) % N] + 1e-12)
    d = a - 2 * b + c
    return k + (0.5 * (a - c) / d if d != 0 else 0.0)


def _peak_bin(w, ref, zp=4):
    """Position (in bins of the N-point FFT) of the dechirped tone: zero-padded FFT, parabolic interpolation. Robust for offsets of half a bin."""
    N = len(w)
    s = np.abs(np.fft.fft(w * ref, zp * N))
    k = int(np.argmax(s))
    return (_parabolic(s, k) / zp) % N


def to_chip_rate(iq, os_, phase=0):
    """Anti-alias filter and decimate to one sample per chip (the bandwidth)."""
    if os_ == 1:
        return np.asarray(iq, dtype=np.complex64)
    from scipy import signal
    taps = signal.firwin(8 * os_ + 1, 0.9 / os_)
    y = signal.fftconvolve(iq, taps, mode="same")
    return y[phase::os_].astype(np.complex64)


def _windows(x, N, start, count):
    return x[start:start + count * N].reshape(count, N)


def _peaks(win, ref):
    spec = np.abs(np.fft.fft(win * ref, axis=1))
    k = np.argmax(spec, axis=1)
    return k, spec[np.arange(len(k)), k], spec


def detect(x, sf, preamble=8, threshold=6.0):
    """Coarse search for the preamble in a chip-rate stream x: returns (apparent start t_s' in chips, mean bin offset e, quality) or None."""
    N = 1 << sf
    stride = max(1, N // 4)
    count = (len(x) - N) // stride
    if count < 4 * preamble:
        return None
    down = np.conj(upchirp(0, sf, 1))
    idx = np.arange(count)[:, None] * stride + np.arange(N)[None, :]
    spec = np.abs(np.fft.fft(x[idx] * down[None, :], axis=1))
    k = np.argmax(spec, axis=1)
    pk = spec[np.arange(count), k]
    ratio = pk / (np.median(spec, axis=1) + 1e-12)
    d = (k - np.arange(count) * stride) % N
    need = 4 * (preamble - 2)
    good = ratio > threshold
    best = None
    i = 0
    while i < count:
        if not good[i]:
            i += 1
            continue
        j = i
        while j + 1 < count and good[j + 1] and abs(((d[j + 1] - d[i] + N // 2) % N) - N // 2) <= 2:
            j += 1
        if j - i + 1 >= need:                          # the first frame in the stream, not the best one: decode_all walks through all of them
            best = (i, j)
            break
        i = j + 1
    if best is None:
        return None
    i, j = best
    # the chain of windows is inside the preamble (up-chirps); the apparent start of the preamble chirp train is t - b modulo N (see the module doc)
    m = (i + j) // 2
    t_apparent = (m * stride - k[m]) % N                        # a chirp start (mod N chips)
    return t_apparent, i * stride, j * stride + N, float(np.mean(ratio[i:j + 1]))


def demodulate(iq, fs, sf, bw, cr_expected=None, preamble=8, sync_word=0x34, max_payload=64):
    """Decode one frame from a complex recording. Returns a dict (payload, crc_ok, header_ok, timing, cfo_hz, snr) or None if no preamble is found."""
    os_ = int(round(fs / bw))
    N = 1 << sf
    up0 = upchirp(0, sf, 1)
    down = np.conj(up0)
    best = None
    for phase in range(os_):
        x = to_chip_rate(iq, os_, phase)
        det = detect(x, sf, preamble)
        if det and (best is None or det[3] > best[1][3]):
            best = (phase, det, x)
    if best is None:
        return None
    phase, (t_app, lo, hi, q), x = best
    # a preamble chirp start inside the detected stretch (apparent)
    t0 = t_app
    while t0 < lo:
        t0 += N
    # fine bin offset on the preamble windows at t0
    n_pre = (hi - t0) // N
    n_pre = max(1, min(n_pre, preamble - 1))
    pk, mag, spec = _peaks(_windows(x, N, t0, n_pre), down[None, :])
    wins = _windows(x, N, t0, n_pre)
    e1 = float(np.mean([((_peak_bin(wins[r], down) + N / 2 - pk[0]) % N) - N / 2 + pk[0] for r in range(n_pre)]))
    e1 = ((e1 + N / 2) % N) - N / 2
    # where does the preamble start? count back up-chirps with the same bin
    start = t0
    while start - N >= 0:
        w = x[start - N:start] * down
        s = np.abs(np.fft.fft(w))
        k = int(np.argmax(s))
        if s[k] / (np.median(s) + 1e-12) > 6 and abs(((k - pk[0] + N / 2) % N) - N / 2) <= 1:
            start -= N
        else:
            break
    # the chirp train starts at "start" (apparent); the two sync symbols follow after `preamble` chirps; find where the up-chirps end
    k_up = 0
    pos = start
    while pos + N <= len(x):
        s = np.abs(np.fft.fft(x[pos:pos + N] * down))
        k = int(np.argmax(s))
        if s[k] / (np.median(s) + 1e-12) > 6 and abs(((k - e1 + N / 2) % N) - N / 2) <= 1.5:
            k_up += 1
            pos += N
        else:
            break
    # k_up up-chirps found (the first sync symbol may count when its bin is within 1.5 of the preamble's: sync words 0 are rare)
    pre_start = start
    sfd_nominal = pre_start + (preamble + 2) * N
    # timing/frequency from the SFD down-chirps: dechirp with the up-chirp, the peak is at 2*eps
    if sfd_nominal + 2 * N > len(x):
        return None
    b2 = _peak_bin(x[sfd_nominal:sfd_nominal + N], up0)
    b2 = ((b2 + N / 2) % N) - N / 2
    eps = b2 / 2.0
    # apparent shift of the up-chirps is e1 = tau_off + cfo, of the down-chirps b2 = -(tau_off) + cfo + e1 ... solve with both measured against the same window grid
    # e1 = cfo - dt (dt: how far the true start is behind the grid), b2 = cfo + dt  => cfo = (e1 + b2) / 2, dt = (b2 - e1) / 2
    cfo = (e1 + b2) / 2.0
    dt = (b2 - e1) / 2.0
    true_start = pre_start + dt
    # align at the sample phase nearest to the true start (1 / os of a chip) instead of carrying a fraction of a chip into the payload windows
    S = int(round(dt * os_))
    tot = pre_start * os_ + phase + S
    if os_ > 1 and tot % os_ != phase:
        x = to_chip_rate(iq, os_, tot % os_)
    pre_start = tot // os_
    res = float(dt - S / os_)
    shift = 0
    pay0 = pre_start + int(round((preamble + 4.25) * N))
    # bins of the payload: the shift by cfo (bins) and the fractional timing remainder (res chips is a shift of -res bins on up-chirps)
    off = cfo + (-res) * 1.0
    pw = np.abs(np.fft.fft(_windows(x, N, pre_start, max(1, min(k_up, preamble - 1))) * down[None, :], axis=1)) ** 2
    pk_pow = pw.max(axis=1)
    noise_bin = np.median(pw, axis=1) / np.log(2)
    snr = float(np.mean(10 * np.log10(np.maximum(pk_pow - noise_bin, 1e-9) / (N * noise_bin + 1e-12))))
    out = dict(timing_chips=float(true_start), cfo_bins=float(cfo), cfo_hz=float(cfo * bw / N), phase=phase, quality=q, e1=e1, b2=b2, snr_db=snr,
               start=int(round(true_start * os_ + phase)))

    def sym_at(i):
        w = x[pay0 + i * N: pay0 + (i + 1) * N]
        if len(w) < N:
            return None
        return int(round(_peak_bin(w, down) - off)) % N

    # first block: SF-2 rows, 8 columns (CR 4/8)
    def block_nibbles(i0, sf_app, cw_len, cr_app, count):
        v = [sym_at(i0 + i) for i in range(cw_len)]
        if any(s is None for s in v):
            return None
        cws = [[0] * cw_len for _ in range(sf_app)]
        for i, s in enumerate(v):
            x_ = (s - 1) % N
            g = x_ ^ (x_ >> 1)
            bits = [(g >> (sf - 1 - j)) & 1 for j in range(sf)]
            for j in range(sf_app):
                cws[(i - j - 1) % sf_app][i] = bits[j]
        nib = []
        for r in range(sf_app):
            c = 0
            for bt in cws[r]:
                c = (c << 1) | bt
            nib.append(hamming_decode(c, cr_app))
        return nib

    nib = block_nibbles(0, sf - 2, 8, 4, 0)
    if nib is None:
        return dict(out, payload=None, header_ok=False, crc_ok=False)
    plen = (nib[0] << 4) | nib[1]
    cr = nib[2] >> 1
    has_crc = bool(nib[2] & 1)
    out.update(payload_len=plen, cr=cr, has_crc=has_crc)
    if not (1 <= cr <= 4) or plen == 0 or plen > max_payload:
        return dict(out, payload=None, header_ok=False, crc_ok=False)
    need = 5 + 2 * plen + (4 if has_crc else 0)
    sym_i = 8
    while len(nib) < need:
        more = block_nibbles(sym_i, sf, 4 + cr, cr, 0)
        if more is None:
            return dict(out, payload=None, header_ok=False, crc_ok=False)
        nib += more
        sym_i += 4 + cr
    hdr = header_nibbles(plen, cr, has_crc)
    header_ok = nib[3:5] == hdr[3:5]
    seq = whitening_sequence()
    body = nib[5:5 + 2 * plen]
    payload = bytes(((body[2 * i] | (body[2 * i + 1] << 4)) ^ seq[i]) for i in range(plen))
    crc_ok = True
    if has_crc:
        crcn = nib[5 + 2 * plen:5 + 2 * plen + 4]
        rx_crc = crcn[0] | (crcn[1] << 4) | (crcn[2] << 8) | (crcn[3] << 12)
        crc_ok = rx_crc == crc16(payload)
    return dict(out, payload=payload, header_ok=header_ok, crc_ok=crc_ok, end=out["start"] + frame_samples(plen, sf, os_, cr, preamble, has_crc))


def demodulate_all(iq, fs, sf, bw, preamble=8, sync_word=0x34, max_frames=1000):
    """All frames of a recording, in order: a list of the dicts of demodulate() with start/end as sample indices into iq."""
    out = []
    base = 0
    os_ = int(round(fs / bw))
    N = 1 << sf
    seg = iq
    while len(out) < max_frames and len(seg) > 12 * N * os_:
        r = demodulate(seg, fs, sf, bw, preamble=preamble, sync_word=sync_word)
        if r is None:
            break
        end = r.get("end") or (r["start"] + 8 * N * os_)
        r = dict(r, start=r["start"] + base, end=end + base)
        out.append(r)
        seg = seg[max(end, 1):]
        base += max(end, 1)
    return out
