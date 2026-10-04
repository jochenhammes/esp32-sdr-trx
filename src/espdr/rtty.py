"""RTTY (Baudot, two-tone FSK) for the transmitter.

The text is turned into a list of frames, (bit, duration in bit periods), by the same rules as the RTTY mode of pluto-tx (the US/commercial
Baudot table of dl-fldigi, letters shift and figures shift, unshift on space, 1 start bit, 5 data bits least significant first, 1.5 stop bits,
a second of idle mark before the text). `OffsetStream` turns the frames into the frequency of the two tones, relative to the lower one, at the
update rate of the transmitter: `txmodes.FskModulator` sends that to the PLL, which is true FSK with a constant amplitude. `decode_frequency`
is the other way round, for tests and for checking a recording.

The mark tone is the one of the logical mark (idle, stop bit); the space tone is `shift` Hz above it. `reverse` swaps which logical state
plays the higher tone, as the Normal/Reverse switch of a terminal unit does.
"""
import numpy as np

# US/commercial FIGS variant, index = 5-bit code. 27 and 31 are the shift codes (FIGS and LTRS), the table holds a placeholder there.
_LTRS = ["\0", "E", "\n", "A", " ", "S", "I", "U",
         "\r", "D", "R", "J", "N", "F", "C", "K",
         "T", "Z", "L", "W", "H", "Y", "P", "Q",
         "O", "B", "G", " ", "M", "X", "V", " "]
_FIGS = ["\0", "3", "\n", "-", " ", "\a", "8", "7",
         "\r", "$", "4", "'", ",", "!", ":", "(",
         "5", '"', ")", "2", "#", "6", "0", "1",
         "9", "?", "&", " ", ".", "/", ";", " "]
FIGS_SHIFT = 0b11011
LTRS_SHIFT = 0b11111

BAUD_DEFAULT = 45.45
SHIFT_DEFAULT = 170.0
MARK_DEFAULT = 2125.0
STOP_BITS = 1.5
PREAMBLE_S = 1.0
TAIL_S = 0.2


def _reverse(table):
    rev = {}
    for code, ch in enumerate(table):
        if code in (FIGS_SHIFT, LTRS_SHIFT) or ch == "\0":
            continue
        rev.setdefault(ch, code)
    return rev


_LTRS_CODE = _reverse(_LTRS)
_FIGS_CODE = _reverse(_FIGS)


def text_to_codes(text):
    """Text -> 5-bit codes with the shift codes inserted. Starts in letters shift; a space returns to letters shift (unshift on space);
    CR and LF do not. Letters are upper case, characters that are not in the table are sent as '?'."""
    codes, state = [], "LTRS"
    for ch in text:
        c = ch.upper() if ch.isalpha() else ch
        in_l, in_f = c in _LTRS_CODE, c in _FIGS_CODE
        if in_l and in_f:                                    # space, CR, LF: the same in both shifts
            codes.append(_LTRS_CODE[c])
            if c == " ":
                state = "LTRS"
        elif in_l:
            if state != "LTRS":
                codes.append(LTRS_SHIFT)
                state = "LTRS"
            codes.append(_LTRS_CODE[c])
        else:
            if state != "FIGS":
                codes.append(FIGS_SHIFT)
                state = "FIGS"
            codes.append(_FIGS_CODE[c] if in_f else _FIGS_CODE["?"])
    return codes


def codes_to_text(codes):
    """The reverse of `text_to_codes` (a receiver's view: shift codes switch the table, a space returns to letters)."""
    out, state = [], "LTRS"
    for code in codes:
        if code == FIGS_SHIFT:
            state = "FIGS"
        elif code == LTRS_SHIFT:
            state = "LTRS"
        else:
            ch = (_LTRS if state == "LTRS" else _FIGS)[code]
            if ch != "\0":
                out.append(ch)
            if ch == " ":
                state = "LTRS"
    return "".join(out)


def codes_to_frames(codes, stop_bits=STOP_BITS):
    """Codes -> (bit, duration in bit periods): start bit (space), five data bits least significant first, stop element (mark)."""
    frames = []
    for code in codes:
        frames.append((0, 1.0))
        frames.extend(((code >> i) & 1, 1.0) for i in range(5))
        frames.append((1, stop_bits))
    return frames


def preamble_frames(preamble_s, baud_rate):
    """Idle mark for about `preamble_s` seconds (whole bit periods, as in pluto-tx)."""
    return [(1, 1.0) for _ in range(max(0, round(preamble_s * baud_rate)))]


def message_frames(text, baud_rate=BAUD_DEFAULT, preamble_s=PREAMBLE_S, tail_s=TAIL_S, stop_bits=STOP_BITS):
    """The whole transmission: idle mark, the text, idle mark again (the transmitter stays on its mark tone, it is not keyed off)."""
    frames = preamble_frames(preamble_s, baud_rate) + codes_to_frames(text_to_codes(text if text else " "), stop_bits)
    if tail_s > 0:
        frames.append((1, tail_s * baud_rate))
    return frames


def duration_s(frames, baud_rate):
    return sum(d for _, d in frames) / baud_rate


def estimate_duration(text, baud_rate=BAUD_DEFAULT, preamble_s=PREAMBLE_S, tail_s=TAIL_S, stop_bits=STOP_BITS):
    return duration_s(message_frames(text, baud_rate, preamble_s, tail_s, stop_bits), baud_rate)


class OffsetStream:
    """The frequency of the tone against the lower tone in Hz (0 or `shift_hz`), one value per update, in blocks.

    The steps between the two tones are rounded raised-cosine ramps of `edge` bit periods centred on the bit boundary (edge 0: abrupt, as
    pluto-tx makes them). The boundaries come from the accumulated times, so the bit length does not drift at update rates that are not a
    whole multiple of the baud rate."""

    def __init__(self, frames, rate, baud_rate, shift_hz, reverse=False, edge=0.2):
        if not frames:
            raise ValueError("no frames")
        self.rate, self.baud_rate = rate, baud_rate
        spb = rate / baud_rate
        bounds = np.cumsum([d for _, d in frames]) * spb              # end of every frame in updates (float)
        high = np.array([bit == 1 for bit, _ in frames]) == bool(reverse)   # True: this frame plays the higher tone
        level = np.where(high, float(shift_hz), 0.0)
        self.total = int(round(bounds[-1]))
        self.b = bounds[:-1]                                          # the boundary between frame j and j + 1
        self.start_level = level[0]
        delta = np.diff(level)
        keep = delta != 0
        self.b, self.delta = self.b[keep], delta[keep]
        self.cum = np.concatenate([[0.0], np.cumsum(self.delta)])      # cum[m]: sum of the first m steps
        self.h = edge * spb / 2.0                                     # half the width of a ramp in updates
        self.pos = 0

    def fraction(self):
        return min(1.0, self.pos / max(1, self.total))

    def values(self, start, count):
        c = np.arange(start, start + count) + 0.5                      # centres of the updates
        if self.h <= 0:
            done = np.searchsorted(self.b, c, side="right")
            return self.start_level + self.cum[done]
        done = np.searchsorted(self.b + self.h, c, side="right")      # steps whose ramp is complete
        y = self.start_level + self.cum[done]
        j0 = np.searchsorted(self.b + self.h, c[0], side="right")
        j1 = np.searchsorted(self.b - self.h, c[-1], side="left")
        for j in range(j0, j1):                                        # the few ramps in progress inside this block
            x = c - self.b[j]
            ramp = np.where(x < self.h, 0.5 * (1 - np.cos(np.pi * np.clip((x + self.h) / (2 * self.h), 0.0, 1.0))), 0.0)
            y = y + self.delta[j] * ramp
        return y

    def blocks(self, block=None):
        block = block or max(1, int(self.rate * 0.02))
        while self.pos < self.total:
            n = min(block, self.total - self.pos)
            y = self.values(self.pos, n)
            self.pos += n
            yield y


def decode_frequency(f_hz, rate, baud_rate=BAUD_DEFAULT, reverse=False):
    """Text from a series of instantaneous frequencies (any offset): slices at the middle of the two tones, finds start bits and samples the
    middle of every data bit, like a UART."""
    f = np.asarray(f_hz, dtype=float)
    spb = rate / baud_rate
    k = max(1, int(spb / 8))
    if k > 1:
        f = np.convolve(f, np.ones(k) / k, mode="same")
    lo, hi = np.percentile(f, [3, 97])
    if hi - lo < 1e-9:
        return ""
    high = f > (lo + hi) / 2
    mark = high if reverse else ~high                                  # True: mark state
    n = len(mark)
    codes, free = [], 0
    for e in np.flatnonzero(mark[:-1] & ~mark[1:]) + 1:               # a mark -> space edge is a start bit
        if e < free:
            continue
        if e + 7 * spb >= n:
            break
        code = sum(int(mark[int(e + (1.5 + i) * spb)]) << i for i in range(5))
        if not mark[int(e + 6.25 * spb)]:                              # the stop element must be mark
            continue
        codes.append(code)
        free = int(e + 6.75 * spb)
    return codes_to_text(codes)
