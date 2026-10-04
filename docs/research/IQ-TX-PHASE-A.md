# Phase A log: reproducing the I/Q playback engine (2026-10-04, branch `research/iq-tx`)

**Result so far: NOT reproduced.** The engine at `0x60033D64` runs and behaves as reported at the register level, but nothing it plays reaches the antenna on this board.
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
