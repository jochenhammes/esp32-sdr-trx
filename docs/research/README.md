# Research branch `research/iq-tx`: raw I/Q transmit on the ESP32-S3

This branch collects the experiments with the chip's **I/Q playback engine**: a register block at `0x60033D64` that reads 32-bit words from capture bank 2 (`0x3FCD0000`) and feeds
the transmit DAC at 40 or 80 Msps. It was found and first measured by h0m3us3r (eSpDR [issue 3](https://github.com/h0m3us3r/eSpDR/issues/3)); everything here is our own
reproduction and extension. **The product transmitter on `main` does not use the engine** and nothing on this branch is part of a release. All test results are written down in the files
below; a result that is not in one of them was not measured.

Status 2026-10-04: the engine is reproduced on two receivers (PlutoSDR, HackRF One) and characterised; refilling its buffer while it plays does not work on this chip; the product design
is therefore limited to fixed buffers changed between bursts.

## Where the results are

| File | Content |
|---|---|
| [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md) | the log: failed attempts, **the solution** (clear bit 18 of `0x60006040` after keying), reproduction table, HackRF check, TX filter registers, image correction, level and gain, IM3, seam, phase noise |
| [DESIGN-IQ-TX.md](DESIGN-IQ-TX.md) | what the engine can be used for, the streaming experiments E1 (CPU refill) and E2 (GDMA refill) with their negative results, the decision |
| [PLAN-IQ-TX.md](PLAN-IQ-TX.md), [PLAN-IQ-TX-B.md](PLAN-IQ-TX-B.md) | the plans (Phase A to D; plan B: the trial-and-error search for the missing step) |
| [TX-RESEARCH.md](TX-RESEARCH.md) | the older research notes of the polar transmitter, with Stages 4 to 6 on the engine (static analysis, report of the original author) |
| [FREQUENCY-DRIFT.md](FREQUENCY-DRIFT.md) | the carrier's drift against the chip temperature (on-chip sensor, crystal model, what it means for RTTY); scripts in `scripts/drift/` |
| [REGISTER-MAP.md](REGISTER-MAP.md) | register map from the PHY libraries (static, inferred) |

## Results in short (all measured, board: ESP32-S3 dev kit QFN56 rev v0.2, LO 2350 MHz unless noted)

| Topic | Result |
|---|---|
| Does the engine radiate? | yes, **after keying the chain and clearing bit 18 of `0x60006040`**; tone at the commanded frequency, +69.6 dB over the noise (PlutoSDR), +61.6 dB (HackRF); sign of rotation, rate bit (halves every frequency), constant word (carrier), bank grant all as reported |
| Bandwidth | the default TX filter drops 28 dB at 20 MHz and 56 dB at 35 MHz; **analog block `0x67` registers 12/13 = 0** make it flat within +-3 dB to +-35 MHz |
| Image (opposite sideband) | 33 dB raw; **62 dB** after pre-correcting the samples with `Q' = 1.010 Q - 0.055 I` (board specific); `0x6000607C` has no effect |
| Level | the gain code `0x60006040[17:10]` does nothing for the engine; analog gain is in PBUS (5,1)/(5,3); linear up to a peak amplitude of about 200 of 511, clips above 250 |
| Intermodulation | IM3 better than 48 dBc below peak 200 (noise-limited), 26 dBc at 260 |
| Seam between re-triggers | pause 337 ns; counted as 27 extra samples in the tone phase it gives sidebands of -48 dBc; sharp and reproducible |
| Phase noise | about -80 dBc/Hz at 2 kHz, -90 dBc/Hz from 20 to 300 kHz, -100 dBc/Hz at 1 MHz (Pluto and ESP together, upper bound) |
| Refill while playing | **not usable**: CPU writes into the bank the engine reads are wrong in 4 to 12 % of the words (0 % with the engine idle), GDMA copies are wrong in 95 % and take 1 ms per buffer |
| Crashes | granting bank 3 (`0x600C101C` bit 3) hangs the chip; the research firmware masks it |

## Reproducing

* Firmware: `make -C firmware TX=1 IQTEST=1 NARROWBAND=1 IDF_PATH=... ESPTOOL="python -m esptool"` builds `firmware/build-iq/iq-source.bin` (research ops in `firmware/protocol/iqtest.h`, code in `firmware/src/radio.c` under `ESPDR_IQTEST`).
* Host: `scripts/iqtest.py` (run with the venv python and `PYTHONPATH=/usr/lib/python3/dist-packages` for `iio`); a PlutoSDR over Ethernet is the reference receiver (30.72 Msps at most), a HackRF the second.
* **This transmits.** Use it only with a transmit permission that covers the frequencies, cabled or well attenuated; the lines it can emit are LO +- tone, the image, the third harmonic, and (for wide filters) anything out to LO +- 35 MHz.

## Open

Harmonics and out-of-band emissions at the real operating level; absolute power; a burst mode for the product (design A in DESIGN-IQ-TX.md); whether the playback bank can be refilled in some other way.
