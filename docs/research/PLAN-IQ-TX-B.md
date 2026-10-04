# Plan B: find out ourselves why the playback engine does not radiate (trial and error allowed)

**SOLVED the same day: clear bit 18 of `0x60006040` after keying (see IQ-TX-PHASE-A.md, last section). The hypothesis table below is kept as the record.**

Status (2026-10-04): [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md) shows that the engine runs and is gated by the bank grant, that it changes the PHY's TX power detector (ratio about 0.80, independent of
the analog gain), and that no sideband appears at the antenna. We cannot wait for the original author, so we search the chip's transmit-path state ourselves.

## Ground rules

* Every trial goes into the log (what, expected, measured). The research firmware (`IQTEST=1`) already has peek/poke, PBUS read/write, the sensor (`IQ_OP_PWR`) and the engine ops.
* Measure with the **sensor first** (seconds, no RF scan). A trial counts as interesting when the engine/idle ratio leaves 0.80 or the idle reading changes in a way that is not explained by the trial itself.
  Every interesting trial is confirmed with the Pluto: narrow, high-gain search for the tone (`probe10`-style: centre at LO + tone + 0.5 MHz, 1.5 MHz filter, 55 dB) and the wide scan.
* Never grant bank 3. Only transmit inside the granted window, lowest useful gain code, a cable or 50 cm. After a hang: hard reset with esptool, then check with the Pluto that no carrier is left.
* Registers keep their value across `IQ_OP_END`/`BEGIN` (seen with `0x60006110`): start every trial from a hard reset when the state matters.

## Hypotheses, cheapest first

| # | Hypothesis | Trial | Status |
|---|---|---|---|
| 1 | The data path is scaled by a field we have not set (the gain field `0x60006040[17:10]`, the amplitude byte `[25:18]`, the Q side `0x60006044`) | sweep the whole fields with the sensor, then Pluto at the best value | open |
| 2 | A "TX on" force bit that the PHY test functions set and `phy_txtone_start` does not | replicate `force_txon_mode` (`0x60006110` bits 13:12, `0x60006000` bit 1 and the gain byte): **no effect** (ratio 0.81) | done, negative |
| 3 | The baseband's continuous-TX mode (`tx_contin_en`: `0x600310D0` bit 15 and a second field) opens the modulator/DAC path | replicate its register writes, sensor and Pluto | open |
| 4 | The CW generator register `0x60006004` that `wifiscwout` writes (values derived from 25/50/75) is the "source select" | replicate, sensor and Pluto | open |
| 5 | MAC/BB registers touched by `tx_a_frame` (`0x60033C34`, `0x60033C40`, `0x60033CA0`, `0x60033CA8`, `0x60033D84`, `0x6001D008`, `0x60035000`) set a TX-valid gate | read them in idle, write the `tx_a_frame` values one by one on top of the keyed state | open |
| 6 | A real Wi-Fi/CW transmission puts the chip into a state we lack | link `librftest` (`wifitxout`, `tx_contin_en`, `tx_a_frame`) into the IQTEST image, run them, snapshot `0x60006000..61FF`, `0x6001C000..CFFF`, `0x60033C00..3DFF`, `0x600310C0..10FF`, diff against our keyed state, apply the differences | open, larger |
| 7 | TX power control / calibration never ran (`tx_pwctrl_init`, `txiq_cal_init`, `rfcal_txiq`): the digital gain stage of the data path is uncalibrated | call them in order, repeat the sensor test | open |
| 8 | Word format: the engine wants 16-bit I/Q halves or a different bit order | write test patterns (I only, Q only, one bit set) and watch the sensor change per pattern | open |
| 9 | Other engine fields: bits 27:20 of `0x60033D64`, the registers `0x60033D88..98` | write nonzero values, sensor | open |

Order of work: 1, 3, 4, 5 (all are register trials, minutes each), then 8, 9, then 7 and 6 (they need new firmware code).

## Stop / success

Success: a line at the commanded offset, at least 20 dB over the noise, that moves when the data changes (frequency, sign of Q, rate bit), on the Pluto; then repeat with the HackRF.
Stop a branch when two independent variants of it give the sensor ratio 0.80 and the Pluto no line; write the negative result into [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md).
