# Phase A log: reproducing the I/Q playback engine (2026-10-04, branch `research/iq-tx`)

**UPDATE (later the same day): REPRODUCED, see the section at the end. The text in between records the failed attempts and why.**

**Result of the first attempts: NOT reproduced.** The engine at `0x60033D64` runs and behaves as reported at the register level, but nothing it plays reaches the antenna on this board.
Everything below was measured with the research build (`make -C firmware TX=1 IQTEST=1 NARROWBAND=1`, image `build-iq`, ops in `firmware/protocol/iqtest.h`,
host script `scripts/iqtest.py`). The product images are unchanged.

Setup: ESP32-S3 dev board (QFN56, revision v0.2, 8 MB PSRAM, 40 MHz crystal, +5.4 ppm), PlutoSDR+ over Ethernet (30.72 Msps is its maximum, so wider spans
are scanned with several centres), about 50 cm, LO 2350 MHz, Pluto gain 5..55 dB. Pluto TX was kept at maximum attenuation.

## What matches the report

| Item | Measured |
|---|---|
| `0x60033D64` writable bits | `0x0FF8BFFF` before run, run (bit 31) accepted; bit 18 (done) sets while run is set |
| Re-trigger | 16384 words at rate bit 1: 1463 triggers in 300 ms (205 us each); rate bit 0: 1219 in 250 ms (twice as long); no poll timeouts |
| Hold bit 19 | blocks the start: 125 of 125 polls time out |
| Bank grant | `0x600C101C` reads 0x4 after being written post-keying (keying clears it) |
| Buffer | bank 2 (`0x3FCD0000`) keeps the 32-bit words written: a clean complex tone, 16 samples per period (checked word by word) |
| Keyed chain | carrier at LO + 11 kHz (+5.4 ppm), +40 to +70 dB over the noise depending on gain code |

## What does not

No line from the engine in any test (the criterion is a line above the noise at the commanded offset; the noise level was +0.5 dB, the carrier +40 to +70 dB):

* complex tones +5, -5, +10 MHz (amplitude 100, 300, 400, 450, 500 of 511), rate bit 1 and 0 (expected 2.5 MHz), real cosine at 5 MHz and 35 MHz;
* a narrow, high-gain search (Pluto 55 dB, 1.5 MHz filter) at the expected frequency, silent buffer as reference: identical;
* a scan of LO +-53 MHz in four steps of 30.72 Msps: only the carrier and +-0.9 MHz sidelines that are also there with a silent buffer;
* DC offset test: constant words I=+-500, Q=+-500 against zeros and an idle engine: the LO line stays within +-1 dB (measurement spread), where a DAC input would move it.

Variants tried, none changed the result:

* keying by `start_tx_tone_step(1,0,g,0,0,0)` with `txcal_debuge_mode()` (gain codes 127 and 70) and by `phy_txtone_start(2350,0,power)` with and without `txcal_debuge_mode()`
  (without it `start_tx_tone_step` gives no carrier at all, `phy_txtone_start` does);
* bank grant 0x4 (report), 0x1, 0xF, 0x0; capture engine `0x60033D5C` running during playback;
* `0x60006000` bit 26 set, `0x600061E4` bit 10 cleared; `0x60026014` clock bits 11/12 and all `0x00FB9FCF` bits set; baseband enable (`0x6002600C` bit 1) on;
* PBUS (4,1) and (5,1) written to 127 after keying;
* the receiver left configured (no release): then nothing radiates at all, as the report says.

Register snapshot in the keyed state (no PLAY): `0x60006000=0x0A82A442` (bit 26 cleared by keying), `0x60006040=0x20060400`, `0x600061E4=0x053C`, `0x60026014=0xFFFCE7FF`,
TX digital gain table `0x60006014..30` populated, `0x600060B0..BC=0`.

## Open differences to the report (candidates, none verified)

* chip revision (ours v0.2) and the board (dev kit, not a WROOM-1 module on a custom board);
* the init of the PHY before keying (ours: this project's `radio_init` and `release_receiver`; the report's firmware is not published);
* whether the report's measurements used the receiver's dump block running at the same time.

## Question for h0m3us3r

Which firmware and which PHY/clock initialisation did the measurement use (published code, or the exact register writes before `phy_txtone_start`), which chip revision, and
was the capture (`0x60033D5C`) running? Our sequence: release receiver, `tune_pll`, `phy_txtone_start`, `0x600C101C=0x4`, samples in bank 2, `0x60033D64 = (n-1)|1<<15` then `|1<<31`, poll bit 18, clear.

## Addendum: the on-chip power sensor sees the engine (2026-10-04, later)

The PHY's own `txtone_linear_pwr()` (what `txiq_get_mis_pwr` uses during calibration; the research op `IQ_OP_PWR` sums 64 readings, with or without the engine triggered
before each reading) is an internal sensor that does not depend on the Pluto. It reacts to the engine, so the engine data does reach the chip's analog transmit chain:

| Test (tone-gen gain code 70 unless noted; reading = sum of 64) | Result |
|---|---|
| unkeyed / keyed g=127 / keyed g=70, engine idle | about 270 000 / 0 (clamped) / 36 000 |
| engine triggered with zeros | same as idle (ratio 1.00) |
| engine triggered, I=Q=a constant, a = 0, 100, 200, 300, 400, 500 | playing/idle = 1.01, 0.96, 0.87, 0.83, 0.81, 0.81 (saturates) |
| constant I=+500, I=-500, Q=+500, Q=-500 | all the same (about 0.81..0.85): sign does not matter, so it is not an additive DC |
| bank grant 0x0 / 0x4 / 0x1 / 0x2 / 0x7 | 1.01 / 0.80 / 0.95 / 0.95 / 0.92 (banks 0/1 hold old data); no grant: no effect, as reported |
| complex tone 1, 5, 10, 20 MHz (rate bit 1) | 0.82, 0.82, 0.84, **1.005**: a low-pass between 10 and 20 MHz, like a baseband filter |
| gain code 64, 80, 100, 120 (idle reading 50 700, 19 500, 4 900, 170) | ratio 0.83, 0.73, 0.34, 0.0: the relative effect grows as the carrier shrinks; the absolute drop is 9 000, 5 500, 3 100, 150 |
| `phy_txtone_start` power 0..48 | ratio 0.87 for all |

Reading: with the grant on bank 2 and non-zero data, something multiplies the sensed carrier by about 0.8 (-1 dB) for data below roughly 15 MHz; the same -1 dB shows in the
Pluto carrier reading (constant words, +-1 dB spread). So the engine data enters the analog chain through a low-pass, but only as a gain-like effect: no sideband from it
is visible at the antenna (limit about -50 dBc from the narrow high-gain search). Candidates: the data is added with a very small gain, enters at a node that is not
the mixer input, or the received power is limited by something that this board does not provide (see the open differences above).

Mistake recorded: granting bank 3 (`0x600C101C` bit 3) crashed the chip, as the report warns (it hung with the chain keyed until a hard reset). The IQTEST firmware now masks bit 3 of every bank grant.

## Addendum 2: what the sensor is, and the analog chain does not matter (2026-10-04)

`txtone_linear_pwr()` loops twice over `get_tone_sar_dout()` and `get_sar_sig_ref()` and returns the sum of (tone reading / reference reading) << 10: it is the TX power
detector's SAR ADC, so the sensor looks at the analog transmit output (relative to a reference), not at a digital register.

Clean single-register sweep (fresh PLL, keying and buffer for every value; I = Q = 500 constant buffer; reading = idle vs engine triggered):

| Change | Idle reading | Ratio playing/idle |
|---|---|---|
| baseline (g=70) | 36 800 | 0.803 |
| PBUS (4,1) = 255 / 64 | 36 500 / 0 | 0.802 / carrier off |
| PBUS (4,2) = 0 / 1 / 64 / 127 | 36 700 / 36 300 / 36 500 / 230 | 0.802 / 0.803 / 0.812 / carrier off |
| PBUS (4,3) = 0 / 1 / 64 / 127 | 0 / 0 / 0 / 260 | carrier off |
| PBUS (5,1) = 1 / 64 / 127 / 255 | 205 / 750 / **178 500** / 176 300 | 0.93 / 0.87 / 0.813 / 0.810 |
| PBUS (5,2) = 1 .. 255 | 35 700 .. 35 900 | 0.795 .. 0.805 |
| PBUS (5,3) = 1 / 64 / 127 / 255 | 196 / 804 / 175 500 / 174 100 | 1.06 / 0.83 / 0.809 / 0.808 |
| `tx_state_set(0..3)` register writes (`0x600060B0..BC`) | 36 100 .. 36 900 | 0.803 .. 0.812 |

The analog settings move the carrier by a factor of five (and switch it off), but the engine's effect stays at x0.80 whenever there is a carrier. A change that is the same
for every analog gain happens before the analog stages: most likely in the digital stage where the tone generator and the engine data meet (saturation or a shared multiplier),
not in a signal path to the mixer. PBUS (4,1), (4,2), (4,3), (5,1), (5,3) are the carrier's on/off and level controls; (4,0), (5,0) refuse writes.

Status after this round: the engine runs, is gated by the bank grant, affects the transmit chain at a digital level, and produces no sideband at the antenna. Not tried: the BB TX state
registers beyond `tx_state_set`, `0x60033D84` (touched by `tx_a_frame`), anything that needs h0m3us3r's initialisation.

## SOLVED (2026-10-04): clear bit 18 of `0x60006040` after keying

The sensor reads in the earlier tests had a side effect that nobody had looked for: `txtone_linear_pwr()` and the functions it calls clear **bit 18 of `0x60006040`**
(`0x2006E800` -> `0x2002E800`), the enable bit of the PHY's tone generator. With that bit cleared the keyed chain stays on, the LO-feedthrough "carrier" falls away
and the playback engine's samples reach the antenna. Order of an experiment that showed it (2 x 2 test, fresh PLL each time):

| continuous-TX bit `0x600310D0` | sensor reads before the Pluto measurement | carrier | tone at +5 MHz |
|---|---|---|---|
| off | no | +68.6 dB | none |
| off | yes (clears bit 18) | +13.9 dB | **+68.4 dB** |
| on | no | +68.7 dB | none |
| on | yes (clears bit 18) | +13.9 dB | **+68.3 dB** |

(The continuous-TX bit is irrelevant. The 0.80 ratio of the earlier sensor tests was the engine data summed with the tone generator's constant.)
`scripts/iqtest.py` now does it in `Esp.key()`: `start_tx_tone_step(1, 0, g, ...)`, grant bank 2 (`0x600C101C = 4`), then `0x60006040 &= ~(1 << 18)`.

Phase A acceptance table (LO 2350 MHz, gain code 70, amplitude 450/511, rate bit 1 = 80 Msps, Pluto at 30.72 Msps, about 50 cm):

| Test | Expected (reported) | Measured |
|---|---|---|
| complex tone +5 MHz | line at LO+5 MHz (+13 kHz crystal offset) | **+5.0137 MHz, +69.6 dB**; opposite sideband -4.9875 MHz at +34.4 dB (35 dB down) |
| complex tone -5 MHz (sign of the rotation) | line at LO-5 MHz | -4.9875 MHz, +68.5 dB; image at +5.0137 MHz +34.4 dB |
| complex tone +10 MHz | line at LO+10 MHz | +10.0125 MHz, +69.7 dB; image +39.6 dB (30 dB down) |
| rate bit 0 (40 Msps), same buffer as for 5 MHz at 80 Msps | line at 2.5 MHz ("clearing bit 15 halves every frequency") | **+2.513 MHz, +76 dB** (image -2.486 MHz +44 dB); half as many triggers (1220 per 250 ms) |
| real cosine 5 MHz in I only | both +5 and -5 MHz | +66.8 dB and +65.7 dB |
| constant word I = A | carrier at the LO from the DC offset | +72.5 dB at LO+11 kHz |
| bank 0 granted instead of bank 2 | no line at the commanded frequency | none (broadband junk from the old content of bank 0 instead) |
| harmonics | | -15 MHz at +38.7 dB for the +5 MHz tone (third harmonic, 31 dB below the line) |

Reported by the original author and now reproduced on a second, different board (dev kit, chip revision v0.2): tone level +57..71 dB over the noise, opposite sideband 44 to 54 dB down
(ours 30 to 35 dB: image rejection is worse here, not yet investigated), the 5 MHz-per-80 Msps scaling, the sign behaviour, the bank grant. Not yet repeated with a second receiver (HackRF).

### Larger offsets (same session, LO 2350 MHz, Pluto re-centred each time)

| Tone (complex, 80 Msps, amplitude 450) | Line over the noise |
|---|---|
| +5 MHz | +69 dB |
| +20 MHz / -20 MHz | +44 dB / +40 dB |
| +35 MHz / -35 MHz | +14 dB / +10 dB |
| real cosine 35 MHz | +8 dB at +35 MHz, +5 dB at -35 MHz |

So the output is not flat to +-35 MHz on this board: about -25 dB at 20 MHz and -55 dB at 35 MHz against 5 MHz (the original report has +57 to +71 dB at all of
2.5, 5, 10, 20 and 35 MHz). The channel-width setting (`0x60006100` bits 21:16 and `0x6002600C` bits 3:2 set to the 40 MHz values) does not change it. Open for Phase B: which
filter limits it (digital or analog TX baseband filter), whether it can be widened, and whether the original board had a different setting.
