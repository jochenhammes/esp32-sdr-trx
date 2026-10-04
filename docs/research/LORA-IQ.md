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
