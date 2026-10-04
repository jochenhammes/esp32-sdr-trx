# Plan: raw I/Q transmit on the ESP32-S3 (reproduce, then build)

**Status (revised after h0m3us3r's report).** The question "does the S3 have a direct I/Q transmit input?" is answered by someone else's measurement: yes, a playback engine at `0x60033D64`
reads 32-bit words from capture bank 2 (`0x3FCD0000`) and feeds the TX DAC at 40 or 80 Msps ([Stage 6 in TX-RESEARCH.md](TX-RESEARCH.md#stage-6-raw-iq-transmit-confirmed-by-h0m3us3r-reported-not-yet-reproduced-here),
original: eSpDR issue #3). It matches our own finding of `dactrig` in the S3 library (Stage 5). **We have not measured it yet.** The earlier plan (register fuzzing, snapshot diffs, mux hunting, OFDM fallback) is obsolete and was removed.
Everything marked *reported* below comes from that report and must be reproduced before it is written into the user documentation as fact.

Known facts to build on (all *reported* until reproduced):

* `0x60033D64`: bits 13:0 = count - 1 (max 16383), bit 15 = rate (0: 40 Msps, 1: 80 Msps), bit 18 = done (read-only, valid while run is set), bit 19 = hold (blocks the start), bit 31 = run. No loop mode.
* Word format: I in bits 9:0, Q in bits 19:10, 10-bit two's complement. Source: bank 2, up to 16384 words (204.8 us at 80 Msps). 32-bit accesses only.
* Sequence: key the chain (`phy_txtone_start`-equivalent, receive-only PBUS state released), then `0x600C101C = 0x4` (keying clears it), write samples, write `(count-1) | 1<<15`, then set bit 31 in a second write, poll bit 18, clear run, repeat. Re-trigger from the MCU.
* Bank 3 overlaps the ROM's data (`rom_phyFuns` at `0x3FCEF3D4`); using it needs the same save/restore as our receive path.
* Seam when re-triggering: about 240 ns (poll-done) or 128 ns (computed delay). Phase noise of the PLL limits coherent high-order modulation (EVM floor 28 % per 179 us OFDM symbol); constant-envelope and non-coherent schemes are fine.
* The 5/6 LO mode (CKGEN block 0x65 register 0 bit 0x10) works on TX: about 1.84 to 2.79 GHz of coverage for about 5 dB.

Why this changes the current code: `firmware/protocol/transmit.h` uses capture banks 0..2 as the ring (`TX_RING_BYTES`), which collides with bank 2 as the playback buffer; the keyed state comes from
`start_tx_tone_step(1, 0, g, 0, 0, 0)` in `radio_tx_run()`, which the report says writes I = Q = 0 with the enable bit.

**Phase A status (2026-10-04): attempted, not reproduced; see [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md). The gate has not been passed.**

## Phase A: reproduce (smallest possible test, needs the board and a PlutoSDR or HackRF)

Use the TX research build (`make -C esp32s3 NARROWBAND=1 TXTEST=1`, loaded to RAM with `espdr_load.py`, a reset restores the flash image; see the notes in `TX-RESEARCH.md`). Add a research op, for example `radio_tx_iq_test()` next to `radio_tx_test()` in `firmware/src/radio.c`:

1. Same bring-up as Stage 2: `tune_pll()`, `txcal_debuge_mode()`, `tune_pll()` again, key with `start_tx_tone_step(1, 0, g, 0, 0, 0)` (also try the library's `phy_txtone_start`). Release the receive-only state first.
2. `0x600C101C = 0x4` (after keying), 16384 words in `0x3FCD0000` with a +5 MHz complex tone at amplitude about 400 (I = A cos, Q = A sin, 80 Msps), 32-bit stores.
3. Write `16383 | 1<<15`, then bit 31; poll bit 18; clear run; loop for 500 ms. Keep the firmware limits (2320..2400 MHz, short bursts, lowest gain).

Acceptance (each its own measurement, PlutoSDR at 50 cm as in Stage 2, 3 Msps, centre at the LO):

| Test | Expected (reported) |
|---|---|
| +5 MHz complex tone | line at LO + 5 MHz (+ about 40 kHz crystal offset), opposite sideband 44 dB or more down |
| clear bit 15 | the line moves to LO + 2.5 MHz |
| swap sign of Q | the line moves to LO - 5 MHz |
| constant word | carrier at the LO (DC offset), level proportional to the amplitude |
| bank 2 not granted (`0x600C101C` other value) | silence |
| chain not keyed | silence |

Do the Pluto check at +-2.5/5/10 MHz (inside its 3 Msps window with a wider setting, or retune), and the high offsets (+-20, +-35 MHz) with the HackRF (20 Msps) or a second tuning. Stop rule: if the +5 MHz line does not appear after the variants
above (bit 15 set/clear, with and without hold bit, banks 0 to 2 granted in turn, bank 3 never), write the log into TX-RESEARCH.md and ask h0m3us3r for the exact register sequence.

**Gate:** Phase A passes -> continue. Fails -> document, ask, do not build further.

## Phase B: characterisation (hardware, after A)

1. Level: amplitude and gain code `0x60006040[17:10]` against output power; IM3 with two tones as a function of gain (reported 10 to 13 dBc at the default gain).
2. Flatness and image: 20 MHz band-limited noise ripple, image and LO feedthrough rejection; is there a TX DC/I-Q correction register (the receive one is `0x6000607C`).
3. Seam: measure re-trigger gap with poll-done and with a computed delay; check a buffer whose end matches its start (phase-continuous tone).
4. Bits 27:20 of the control register, the other `0x60033D88..98` registers; whether `0x60033D8C` allows a second count/rate field.
5. 5/6 LO mode on TX in our PLL plan (`firmware/src/lo_plan.h`, `tune_pll()`); coverage and spurs.
6. Phase noise: carrier phase over 1, 10, 50, 179 us; optionally with an external reference at the crystal.
7. Spectral purity and out-of-band emissions for the whole 80 Msps band: what leaves the antenna at +-40 MHz around the LO, harmonics, the 5/6 mode. This decides how wide a waveform may be used inside the 13 cm amateur allocation.

## Phase C: product design (after A and B)

Design questions, with what is known:

* **No loop mode and 204.8 us per buffer.** Continuous modulation is a train of buffers with a 128 to 240 ns gap. Host streaming at 80 Msps is impossible over USB (the narrowband link carries about 0.87 MB/s, `docs/internals.md`), so useful modes are *generated on the chip*: a periodic waveform
  (tones, chirps, an OFDM symbol with cyclic prefix, preamble patterns), a table of precomputed buffers selected by a small control stream (FSK, LoRa chirp symbols, FT8/WSPR tones, POCSAG), or a lower-rate stream that the MCU upsamples into the buffer (SSB/FM at narrowband would need an NCO and an interpolator in software; check the CPU budget on the two cores).
* **Compare with the polar method** (FM/SSB, `docs/transmitter.md`): keep polar as the default for voice until a buffer-based mode beats it on unwanted sideband (61 to 63 dB) and IM3 (33 dB).
* **Memory layout:** bank 2 is the playback buffer, the host ring must move to banks 0 and 1 (`TX_RING_BYTES` becomes 2 x 64 KiB), bank 3 stays off limits unless the ROM data save/restore of the receive path is reused.
* **Protocol:** a new op set next to `TX_OP_*` in `firmware/protocol/transmit.h` (load buffer, set rate bit, start/stop burst, status) and a mode in `src/espdr/txlink.py` / `txmodes.py`; tests in `tests/test_modes.py` and the simulator `src/espdr/sim.py`.
* **Legal limits:** the firmware limit 2320..2400 MHz has to apply to every emitted line, not only to the LO: LO +- up to 40 MHz, image, LO feedthrough, and for the 5/6 mode the real output frequency. Default to a cabled, attenuated setup; keep the licence text in `docs/transmitter.md`.

## Phase D: documentation (do now for the wording, finish after A)

1. README, `docs/transmitter.md`, `docs/internals.md`: the Stage-4 wording "no I/Q transmit input was found" is now wrong; say "an I/Q playback engine exists (reported by h0m3us3r, SRAM, 80 Msps); this project's transmitter uses polar modulation, and I/Q mode status: ..." once Phase A has run.
2. `TX-RESEARCH.md`: Stage 6 is in; add Stage 7 with our own Phase A/B results and logs.
3. Credit: h0m3us3r (eSpDR) for the engine, the bank-3 warning and the measurements; the commenter on the Reddit post for the hint.
4. Reply on Reddit/eSpDR: thank, say the plan is to reproduce and extend; share the REGISTER-MAP and the `dactrig` disassembly note.

## Verification rule

A statement moves from *reported* to *reproduced* only after our own measurement shows the spectrum following the data written (change the data, the line moves or changes accordingly), on the PlutoSDR and repeated with the HackRF. Negative results need a log (register, value, measurement, noise floor).
Run `pytest` (`tests/test_docs.py` checks the docs) before every commit; firmware changes must still build with the CI target.

## Key files

`firmware/src/radio.c` (`radio_tx_test`, `radio_tx_begin`, `radio_tx_run`, `tune_pll`), `firmware/src/board.h` (`DUMP_*`, new `DAC_PLAY_*` definitions), `firmware/protocol/transmit.h`, `firmware/src/lo_plan.h`,
`src/espdr/txlink.py`, `src/espdr/txmodes.py`, `host/python/espdr_txtest.py`, `host/python/espdr_load.py`, `docs/research/TX-RESEARCH.md`, `docs/research/REGISTER-MAP.md`.
