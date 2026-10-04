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
-- the formula holds. SF6: 12 B 45.25 symbols (724 us at 4 MHz); SF7: 40.25 symbols (1288 us at 4 MHz). Only SF5 fits one 409.6 us buffer, at BW 4 MHz up to 12 bytes (402 us), at BW 8 MHz up to 24 bytes (301 us at 20 B, 80 Msps buffer 204.8 us: only 12 B).

## L0b: offline engine model, packet error rate against SNR (SNR in the bandwidth, 100 frames per point)

Model: 10-bit I/Q at peak 200, image -33 dB (uncorrected), LO feedthrough -40 dBc, frequency offset 12.6 kHz.

| Case | SNR for 50 % packet loss, ideal | with the engine model | loss |
|---|---|---|---|
| SF5, BW 4 MHz, 40 Msps, 12 B | about 1.8 dB | about 2.2 dB | about 0.4 dB |
| SF5, BW 4 MHz, 16 B | about 1.8 dB | about 2.3 dB | about 0.5 dB |
| SF5, BW 8 MHz, 80 Msps, 12 B | about 1.8 dB | about 1.8 dB | 0 dB |
| SF6, BW 4 MHz, 12 B | about -2.0 dB | about -0.5 dB | about 1.5 dB, floor of 2 to 5 % losses up to +6 dB |

**Gate for scheme S (at most about 1 dB against ideal): passed for SF5.** The SF6 floor comes from the receiver, not from the engine (to be traced: the model's -40 dBc LO line falls on a bin of the 64-point dechirp).
