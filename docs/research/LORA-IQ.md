# LoRa through the I/Q playback engine: log (PLAN-LORA-IQ.md)

Started 2026-10-04 (afternoon). Style of IQ-TX-PHASE-A.md: what was done, what was measured, what follows.

## L0a: what gr-lora_sdr can do (local, no radio)

`scripts/lora_iq/l0a_loopback.py`: `LoraTxEncoder` -> `LoraRxDecoder` of pluto-tx, 20 random 12-byte payloads per case, bit-exact comparison.

| Case | Result |
|---|---|
| SF7, BW 125 kHz, x4 | 20/20 (5/5 in the first run) |
| SF7, BW 500 kHz, x4 | 20/20 |
| SF5, BW 125 kHz, x4 | 0/20 |
| SF6, BW 125 kHz, x4 | 0/20 |
| SF5, BW 500 kHz, x4 | 0/20 |
| SF5, BW 4 MHz, x10 (40 Msps) | 0/20 |
| SF6, BW 4 MHz, x10 | 0/20 |
| SF5, BW 8 MHz, x2 (16 Msps) | 0/20 |
| SF5, BW 8 MHz, x10 (80 Msps) | 0/20 |

**Gate: fails.** The library decodes nothing at SF5 and SF6 at any bandwidth (not a question of the wide bandwidths: it does not work at 125 kHz either; the sources have no SF5/SF6 case), and SF7 is fine. So the numpy
receiver of the plan was written first.

## The numpy LoRa PHY (`scripts/lora_iq/lora_np.py`)

Transmitter and receiver for any SF 5..12 and bandwidth, ported from the sources of gr-lora_sdr (whitening LFSR, explicit header with checksum, CRC-16 with the SX127x quirk, Hamming 4/5..4/8, diagonal interleaver with SF-2 rows in the
first block, Gray, chirps with the sync word symbols and 2.25 down-chirps). Receiver: low-pass and decimation to the chip rate, preamble search by dechirping overlapping windows, timing and frequency offset from the preamble and the SFD
down-chirps, one FFT per payload symbol, Hamming decoding with single-error correction, header checksum and CRC.

* Loopback (`selftest.py`): 20/20 frames at every tested combination: SF5/SF6/SF7/SF8/SF12 at BW 62.5 kHz .. 8 MHz, oversampling 1 .. 10, CR 4/5 and 4/8, random start offset and a frequency offset of up to 12.6 kHz.
* Against gr-lora_sdr at SF7 (the only SF where it works): the numpy transmitter is decoded by `LoraRxDecoder`, 5/5 frames bit-exact; a frame of `LoraTxEncoder` is decoded by the numpy receiver (CRC ok, same payload).
  So the chain is the real LoRa chain; at SF5 and SF6 it is the same rules with SF in the formulas, **not verified against a real chip** (only an SX126x/SX128x could do that): for our own link that does not matter, and the layout is defined by this code.

## Frame times from the real symbol count (`model.py --times`)

The table of the plan was computed with the SX127x formula; the real encoder gives the same numbers for SF5 and CR 4/5: 12 B 50.25 symbols (402 us at BW 4 MHz, 201 us at 8 MHz), 16 B 60.25 (482/241 us), 20 B 70.25 (562/281 us)
-- the formula holds. SF6: 12 B 45.25 symbols (724 us at 4 MHz); SF7: 40.25 symbols (1288 us at 4 MHz). Only SF5 fits one 409.6 us buffer, at BW 4 MHz up to 12 bytes (402 us), at BW 8 MHz up to 24 bytes at 40 Msps (301 us at 24 B; oversampling 5) and only 12 bytes at 80 Msps (201 us in the 204.8 us buffer).

## L0b: offline engine model, packet error rate against SNR (SNR in the bandwidth, 100 frames per point)

Model: 10-bit I/Q at peak 200, image -33 dB (uncorrected), LO feedthrough -40 dBc, frequency offset 12.6 kHz.

| Case | SNR for 50 % packet loss, ideal | with the engine model | loss |
|---|---|---|---|
| SF5, BW 4 MHz, 40 Msps, 12 B | about 1.8 dB | about 2.2 dB | about 0.4 dB |
| SF5, BW 4 MHz, 16 B | about 1.8 dB | about 2.3 dB | about 0.5 dB |
| SF5, BW 8 MHz, 80 Msps, 12 B | about 1.8 dB | about 1.8 dB | 0 dB |
| SF6, BW 4 MHz, 12 B | about -2.0 dB | about -0.5 dB | about 1.5 dB, floor of 2 to 5 % losses up to +6 dB |

**Gate for scheme S (at most about 1 dB against ideal): passed for SF5.** The SF6 floor comes from the receiver, not from the engine (to be traced: the model's -40 dBc LO line falls on a bin of the 64-point dechirp).

## L1: scheme S on the air (2026-10-04, 16:00 to 16:15)

Research firmware additions (`firmware/protocol/iqtest.h`, `firmware/src/radio.c`): `IQ_OP_LDPOS` (83), `IQ_OP_LDI` (84) and `IQ_OP_LDQ` (86) load bank 2 word by word (two ops per word keep every argument under 16 bits, so they can be sent in bulk:
16 080 words in 2.0 s with 40 requests per burst; 200 per burst overflowed the chip's USB buffer, single requests take 0.07 ms), `IQ_OP_GAP` (85) adds a pause in microseconds between the triggers of `IQ_OP_PLAY`.
Host: `scripts/lora_iq/run.py` (`render`, `send`, `analyse`, `noise`). The frame (`DA2JH LORA-1`, 12 bytes, call sign inside) is rendered at 40 Msps, shifted 3 MHz above the LO so that the LO line stays outside the band,
corrected for the image with the values of this board (g = +0.010, p = -0.055), quantised to 10 bit at peak 200, loaded, and triggered every 1.4 ms (402 us frame + 1 ms pause) for 600 ms. LO 2350 MHz, so the frame occupies 2351 .. 2355 MHz and its image
2345 .. 2349 MHz; gain code 127 (the weakest), the Pluto 50 cm away, 16 Msps, the numpy receiver.

**Result: every frame decodes.** 94 of 94 frames (428 triggers in 600 ms; the 131 ms capture holds 94) with the right bytes and CRC at Pluto gain 30 dB; 93 of 93 or 94 of 94 at 22, 14, 8 and 2 dB. The only "errors" are one spurious
detection at the end of a capture. The frequency offset is +9 to +10 kHz (the board's crystal, 12.6 kHz minus the Pluto's own error), spread 1.1 to 1.8 kHz between frames.

**SNR.** The preamble-based estimator of the receiver saturates at 13.5 dB even on a noise-free synthetic frame (leakage of the 32-point dechirp), so it cannot be used for the link budget; the SNR from the frame power against the pauses
between the frames, low-passed to 4 MHz, is **57 dB at Pluto gain 30 dB and 37 dB at 2 dB**. The link is therefore far from its threshold; the threshold was measured by adding white noise to the real capture (`run.py noise`, 93 frames, the real transmitter's spurs and phase noise included):

| SNR in the bandwidth | 8 | 6 | 5 | 4 | 3 | 2.5 | 2 | 1.5 | 1 | 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| packet error rate (real capture + noise) | 0.00 | 0.00 | 0.00 | 0.06 | 0.38 | 0.65 | 0.87 | 0.94 | 0.98 | 0.99 |

50 % packet loss at about **2.9 dB**, against 1.8 dB for the ideal numpy chain and 2.2 dB for the engine model of L0b: **1.1 dB lost against ideal, 0.7 dB against the model**.
**Gate L1 (CRC-ok frames with the right bytes, reproducible, within a few dB of the model): passed** for SF5, BW 4 MHz, 40 Msps, 12 bytes. The research firmware ends the session by itself after 20 s without a command (`IQ_IDLE_MS`), so captures
are decoded after `IQ_OP_END`; a second capture in the same script run found `NOT_READY` for that reason.

### L1 variants (same day)

| Case | Result |
|---|---|
| SF5, BW 8 MHz, 80 Msps (201 us frame, the whole 204.8 us buffer), Pluto 24 Msps, frame 5 MHz above the LO | 36 of 36 and 37 of 37 frames decoded; SNR from the gaps 47 / 40 dB. Threshold (real capture + noise, 37 frames): 50 % loss at about 3.2 dB, 1.4 dB from ideal |
| SF5, BW 8 MHz, 40 Msps (oversampling 5), MeshCore raw packet `3e004441324a48` (ROUTE_DIRECT, PT_RAW_CUSTOM, "DA2JH"; 7 bytes, preamble 32, CR 4/8; 305 us as in the plan) | 33 of 33 frames decoded, the payload equals `meshcore_codec.build_packet(...)` |

(An 8 MHz signal needs an integer oversampling at the receiver: 30.72 Msps gives 3.84 and nothing decodes; 24 Msps gives 3.)

## L3: scheme P, narrow LoRa through the polar path (2026-10-04, 16:20 to 17:25)

Work items of the plan, done: (1) the offset range is one runtime value, **`TX_OP_RANGE` (47)**: 44 .. 90 PLL steps (default 44, reset after each session), the margin of the PLL word's low byte follows (`range + 4`), so voice and RTTY keep exactly
their old limits; the host (`FskModulator(range_steps=...)`, `Session.configure(range_steps=...)`, the simulator) carries it; (2) the channel centre is chosen instead of moving the LO: `cli_tx._lora_plan` picks the nearest PLL word with the low byte in `range+4 .. 251-range`
(the centre moves by at most 40 kHz and is printed); (3) **`espdr-tx -m lora`** (`--sf`, `--lora-bw`, `--lora-cr`, `--lora-preamble`, `--lora-sync`, text as for RTTY, `--repeat-*`) with `lora_phy.frame_track()`: the frequency of the frame at every 25 us update,
the mean over the update (so that the phase at every update boundary is exact, which puts the wrap of the chirp where it belongs; the first try with the value at the middle of the update gave symbol errors at SF8); (4) one temperature reader (`radio_tx_temp`).
The numpy PHY became `src/espdr/lora_phy.py`. Tests: `tests/test_lora.py` (the track decodes through an ideal FM model, range, plan, simulated session).

**On the air** (ESP at LO 2350 MHz, `--power -6`, Pluto 1 Msps 50 cm away, 6 frames of 13 bytes per run, thermal correction off):

| SF | BW | frame | decoded |
|---|---|---|---|
| 5, 6, 7 | 62.5 kHz | 41 to 82 ms | 6 / 6 each |
| 8 | 62.5 kHz | 165 ms | 8 / 8 and 3 / 3 |
| 9 | 62.5 kHz | 330 ms | 6 / 6 (3 / 6 with the first receiver, see below) |
| 10 | 62.5 kHz | 660 ms | 6 / 6 and 4 / 6 |
| 8 | 31.25 kHz | 330 ms | 6 / 6 and 3 / 3 (after the coarse centre, see below) |
| 10 | 31.25 kHz | 1.3 s | 0 / 4 |
| 7 | 80 kHz | -- | refused: the sweep needs more than 90 steps (80 kHz is 87 steps either side plus margin) |

Threshold (SF8, 62.5 kHz, real capture with white noise added, 8 frames, SNR from the gaps 27.7 dB): all 8 decode down to -4 dB, 4 of 8 at -6 dB, 1 of 8 at -8 dB. (The SF8 threshold of an ideal LoRa receiver is about -10 dB; the numpy receiver and the 8 frames are a rough figure.)

What decided it:

* **The carrier moves during a frame.** A frame of SF9 and up lasts 0.3 to 1.3 s; the board's carrier drifts by 50 to 100 Hz/s (thermal state, not corrected), a bin is 122 Hz (SF9) or 61 Hz (SF10) wide. The receiver therefore had to follow the drift:
  it fits the slope of the preamble bins, extrapolates to the payload and corrects the offset symbol by symbol from the fractional part of the decided bins. With that SF9 and SF10 at 62.5 kHz decode (they did not before: payload right, CRC wrong, because the end of the frame drifted away).
* **The thermal correction made it worse here** (`--thermal auto` with the model of this board: SF9 0 / 6, SF10 0 / 5). The model describes seconds to minutes of transmission from the chip temperature; the LoRa test sends bursts of 0.3 to 0.7 s with 1 s pauses, so every burst repeats
  the switch-on transient that the model cancels at the wrong strength (the model's fast term is 3100 Hz in 5 s for this board; the first 0.7 s of a burst after 1 s of pause are not that). For repeated short frames use `--thermal off` (or give the model the pause as an input).
* **The absolute frequency is off by 8 kHz** once the board has warmed up: `--ppm 5.4` was measured on the cold board; the carrier is 8.3 kHz (3.5 ppm) lower when it is hot. That is more than a quarter of the bandwidth at 31.25 kHz, which is where the receiver's offset estimate ends
  (it knows the offset only modulo half a bin count); `lora_phy.coarse_centre()` takes the band centre from the spectrum first. At 62.5 kHz the plan's own move of the centre (+8.4 kHz) happened to cancel it.

## L2: scheme W, a frame symbol by symbol (2026-10-04, 17:05 to 17:35)

Research firmware (`firmware/protocol/iqtest.h`, `firmware/src/radio.c`): `IQ_OP_LDTGT` (87) lets the bulk load write the ring in bank 1 instead of bank 2, `IQ_OP_SYM` (88) / `IQ_OP_SYMCLR` (89) hold the frame as a list of symbol codes,
`IQ_OP_LPAR` (91) takes the parameters (window length, words per chip, words played per symbol, symbol period in CPU cycles, repetitions, engine bits, fixed cost), `IQ_OP_LORA` (90) plays the list. Per symbol the chip copies the window of the ring that belongs to the symbol value
(the base chirp is periodic in N chips, so the window of value k starts k chips in) into bank 2 **while the engine is idle**, waits for the symbol clock (the cycle counter), triggers the engine for `lp` words and waits for it. The end of every symbol is left out: the copy
needs that time. The finished stream experiments (E1) are compiled out (`-DESPDR_IQ_STREAM`) to make room in core 0's code region. Host: `run.py wsend`, with the ring rendered from the continuous chirp formula, so the fractional oversampling (24.615 words per chip at BW 1.625 MHz) is no problem.

Numbers: SF5, BW 1.625 MHz (a real SX1280 bandwidth), 12 bytes, 51 symbols of 19.69 us = a 990 us frame (the plan's 1 ms; a 20 ms frame of the same bytes at BW 812 kHz would be 2 ms). Copying 788 words takes **1992 CPU cycles (8.3 us)**, the period is 4726 cycles, so the engine can play
**414 to 430 of 788 words (0.53 to 0.55 of every symbol, -2.8 dB)**; with 60 cycles of reserve every symbol is late, with 150 none. The quarter down-chirp of the SFD is a quarter symbol of silence (a copy of the next window did not fit into a quarter period, and the receiver does not use it).

**Result (Pluto gain 26 dB, 1000 repetitions, 216 frames in the capture): 177 frames decoded with the right bytes and CRC, 0 late triggers.** The 39 failures are mostly one symbol off by one bin (value 24 at two positions).
Findings on the way: (1) with the bare 48 % play fraction (380 words) the receiver failed on whole classes of symbol values although an offline model of the firmware decoded: below half a symbol the dechirped peak of the truncated symbol is wider than the receiver's offset estimate tolerates;
at 414 words the same model decodes 30 of 30 at every carrier offset I tried. (2) An offset calibration on the sync symbols made it worse (37 of 80 against 62 of 80), because their wrap lies inside the played part.
**Threshold** (real capture with white noise added, 177 frames): SNR from the pauses 8.8 dB as captured (it does not rise with the Pluto's gain: the gating of the symbols splatters into the band; scheme S has 37 to 57 dB), 49 % packet loss at 8 dB, 98 % at 6 dB, 100 % at 4 dB: **about 6 dB worse than the ideal chain (1.8 dB) and 4 to 5 dB worse than the plan's hypothesis (L0b)**.
**Gate L2 (CRC-ok frames within about 3 dB of the model): not passed**, but the mechanism works on the chip: a frame of more than 400 us, written symbol by symbol with a locked symbol clock, decodes.

What would change the number: a second core that copies the next window while the first plays it (not possible: bank 2 cannot be rewritten while it plays, see DESIGN-IQ-TX.md), a smaller window (play only the part of the symbol that is needed: the FFT of a truncated symbol does not need the whole chirp, but a longer play fraction helps),
and a receiver that matches the truncated symbol (a correlation against the 32 ideal truncated waveforms instead of a dechirp FFT).

## L4: comparison (all with the same payload class, Pluto as the receiver, numpy receiver)

| Scheme | Parameters | Frame | Decoded on the air | SNR for 50 % loss | Against the ideal chain | SNR of the signal itself |
|---|---|---|---|---|---|---|
| S (one buffer) | SF5, BW 4 MHz, 40 Msps, 12 B | 402 us | 93 of 93 | 2.9 dB | +1.1 dB | 37 to 57 dB |
| S | SF5, BW 8 MHz, 80 Msps, 12 B | 201 us | 37 of 37 | 3.2 dB | +1.4 dB | 40 to 47 dB |
| S | SF5, BW 8 MHz, 40 Msps, MeshCore raw 7 B, preamble 32, CR 4/8 | 305 us | 33 of 33 | -- | -- | 34 dB |
| W (symbol by symbol) | SF5, BW 1.625 MHz, 12 B | 990 us | 177 of 216 | about 8 dB | about +6 dB | 8.8 dB |
| P (polar) | SF5..SF8, BW 62.5 kHz, 13 B | 41 to 165 ms | all frames | SF8: -6 dB | about +3 dB (rough, 8 frames) | 28 dB |
| P | SF9, SF10, BW 62.5 kHz | 330 / 660 ms | 6 of 6 each (with the drift-following receiver) | -- | -- | 22 to 26 dB |
| P | SF8, BW 31.25 kHz | 330 ms | 6 of 6 | -- | -- | -- |
| P | SF10, BW 31.25 kHz | 1.3 s | 0 of 4 | -- | -- | drift of 1 to 2 bins per frame is more than the receiver follows |

**What this gives as a statement of the product** (if the numbers are confirmed on another board): the playback engine sends a complete LoRa frame of up to about 400 us (12 bytes at BW 4 MHz, 24 bytes at BW 8 MHz and 40 Msps) as a clean burst that a
numpy receiver (not a standard LoRa chip: SF5 and BW 4 or 8 MHz are not LoRa bandwidths) decodes 1 dB from the ideal; frames up to 1 ms at a real SX1280 bandwidth (1.625 MHz) can be written symbol by symbol but lose about 6 dB; and narrow LoRa (62.5 kHz, SF5 to SF10) goes through the ordinary polar transmitter with a receiver that follows the board's drift.
Not tested: any real LoRa chip as receiver, the ESP32's own receiver on narrow LoRa (needs a second transmitter at 13 cm), and other boards.

## State (17:35)

L0a, L0b, L1 (three variants), L2 and L3 are done; L4 is the table above. Open: the ESP32 receiving narrow LoRa, a real SX128x as the receiver (the only way to check the SF5 and SF6 rules and the 1.625 MHz frame against the standard),
better synchronisation on truncated symbols, and merging the research ops into `main` if wanted (`TX_OP_RANGE`, `-m lora`, `lora_phy.py` are product-side code on this branch; the `IQ_OP_*` ops are research-only).
