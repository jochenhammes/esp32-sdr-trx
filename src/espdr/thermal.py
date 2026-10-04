"""The carrier's thermal frequency drift, predicted from the chip temperature and the time since the transmission began, and cancelled in the records.

The board's frequency follows its crystal, whose frequency has a turning point (a parabola) around a temperature T0. A transmission heats the chip within
minutes from its idle temperature (about 42 C) towards 56 C. The chip's own sensor (TX_OP_TEMP) gives the start temperature; the rest is a model fitted to
measurements on one board (docs/research/FREQUENCY-DRIFT.md on the research branch). The default numbers are those of that board: a measurement series
on another board (scripts/drift/) gives its own, which --thermal-model takes from a JSON file.
"""
import json

import numpy as np

from .txmodes import MAX_Q4, Q4_HZ

REF_HZ = 2350e6                          # the frequency the parameters were measured at; the error is in ppm of the carrier

DEFAULT = dict(
    t0=47.09,                             # turning point of the crystal, degrees C on the chip's sensor scale
    k=19.47,                              # Hz per degree^2 at 2350 MHz
    k3=0.0,                               # Hz per degree^3 at 2350 MHz (the cubic term of an AT-cut crystal)
    t_inf=58.0,                          # temperature the chip heads to while it transmits
    tau_chip=137,                      # s, heating of the chip in a transmission
    tau_xtal=0.5,                       # s, the crystal follows the chip with this lag
    idle=42.0,                           # start temperature assumed without a sensor reading
    early_hz=1118,                       # slow rise at the switch-on that does not follow the sensor (Hz at 2350 MHz): early_hz * (1 - exp(-t / early_tau))
    early_tau=67,
    early_cold_c=0.0,                    # the transient scales with (idle_ref - start temperature) / early_cold_c, 0 = fixed size
)


def load_params(path=None):
    p = dict(DEFAULT)
    if path:
        with open(path) as f:
            p.update({k: float(v) for k, v in json.load(f).items() if k in DEFAULT})
    return p


def crystal_temperature(t, start_c, p):
    """Crystal temperature after t seconds of transmission: the chip heats exponentially, the crystal follows with a first-order lag."""
    d = p["t_inf"] - start_c
    th, tx = p["tau_chip"], p["tau_xtal"]
    if abs(th - tx) < 1e-6:
        th += 1e-3
    return p["t_inf"] - d * (th * np.exp(-t / th) - tx * np.exp(-t / tx)) / (th - tx)


def error_hz(t, start_c, p, carrier_hz=REF_HZ):
    """The predicted frequency error (against the value at the turning point) at t seconds after the start, in Hz at the carrier frequency."""
    tx = crystal_temperature(t, start_c, p)
    x = tx - p["t0"]
    e = p["k"] * x ** 2 + p["k3"] * x ** 3
    if p["early_hz"]:
        scale = 1.0 if not p["early_cold_c"] else max(0.0, (p["idle"] - start_c) / p["early_cold_c"] + 1.0)
        e = e + p["early_hz"] * scale * (1.0 - np.exp(-t / p["early_tau"]))
    return e * carrier_hz / REF_HZ


class Thermal:
    """Adds the opposite of the predicted drift to the frequency field of the records, block after block (call apply() in playing order)."""

    def __init__(self, start_c, rate, carrier_hz, params=None):
        self.p = params or dict(DEFAULT)
        self.start_c, self.rate, self.carrier_hz = float(start_c), float(rate), float(carrier_hz)
        self.n = 0
        self.carry = 0.0

    def correction_hz(self, n):
        t = (self.n + np.arange(n)) / self.rate
        return -error_hz(t, self.start_c, self.p, self.carrier_hz)

    def apply(self, records, limit=MAX_Q4):
        """records: uint32 array of the transmit protocol. Returns a copy with the correction added to the frequency field (whole record units,
        the rounding error carried on so that the mean is exact)."""
        n = len(records)
        if not n:
            return records
        want = self.correction_hz(n) / Q4_HZ
        cum = np.cumsum(want) + self.carry
        whole = np.floor(cum + 0.5)
        add = np.diff(whole, prepend=0.0)
        self.carry = float(cum[-1] - whole[-1])
        self.n += n
        q4 = (records & 0xFFFF).astype(np.uint16).view(np.int16).astype(np.int32)
        q4 = np.clip(q4 + add.astype(np.int32), -limit, limit).astype(np.int16)
        return (records & np.uint32(0xFFFF0000)) | q4.view(np.uint16).astype(np.uint32)
