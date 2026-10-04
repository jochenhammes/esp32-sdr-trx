# Plan: LoRa through the I/Q playback engine, entirely in the 13 cm amateur band

Status: plan (second version), nothing measured yet. Base: branch `research/iq-tx` ([README](README.md), [DESIGN-IQ-TX.md](DESIGN-IQ-TX.md), [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md)).
Test tools: the LoRa/MeshCore code of the (read-only) `pluto-tx` repository, available on the development machine next to the ESP32, plus a PlutoSDR as the receiver.

**Premise.** Transmitting and receiving happen only at 13 cm. The classic LoRa frequencies (433/868/915 MHz) and with them compatibility with MeshCore/Meshtastic nodes do not matter. SF, bandwidth, preamble, coding rate and frame layout are free, so we choose them to suit the engine.
Only 2.4 GHz SX1280 hardware could ever interoperate (bandwidths 203/406/812.5/1625 kHz), and that is an optional later check.

## 1. What the engine allows, and the constraint that decides everything

Measured on the branch: 1..16384 words per trigger (**204.8 us at 80 Msps, 409.6 us at 40 Msps**), no loop, bank 2 only, re-trigger pause 337 ns, peak amplitude <= 200 of 511, bandwidth about +-35 MHz with the TX filter registers opened (20 MHz at 40 Msps), and
**bank 2 cannot be rewritten while the engine plays** (4 to 12 % wrong words from the CPU, GDMA worse). With the engine idle the CPU writes 16384 words in about 90 us (5.5 ns/word).

Two consequences:

* A signal that fits **one buffer** needs no rewrite and no timing control: one trigger, a true burst.
* A signal that changes from buffer to buffer has dead time while the next buffer is written: at least 18 % at 40 Msps and 30 % at 80 Msps if every word is rewritten (both cores writing might halve it; unmeasured). Identical buffers cost only the 337 ns pause.

## 2. The frame time rule

A LoRa frame lasts (symbols) x Ts with `Ts = 2^SF / BW`. The protocol on top (LoRaWAN, MeshCore, our own) only changes the **byte count and the preamble/CR defaults**, never the time per symbol. What decides whether a frame fits one buffer is the PHY: **low SF and wide BW**.
Symbols per frame (SX127x formula: explicit header, CRC on; payload symbols = 8 + ceil((8 PL - 4 SF + 28 + 16 CRC) / (4 SF)) x (CR + 4)); preamble + 4.25 sync/SFD symbols included:

| Frame | symbols | SF5 BW 8 MHz | SF5 BW 4 MHz | SF5 BW 1625 kHz | SF5 BW 812.5 kHz | SF7 BW 125 kHz |
|---|---|---|---|---|---|---|
| LoRaWAN layout, 12 B (no FRMPayload), preamble 8, CR 4/5 | 50.25 | **201 us** | **402 us** | 990 us | 1.98 ms | 41.2 ms |
| own frame, 16 B, preamble 8, CR 4/5 | 60.25 | **241 us** | 482 us | 1.19 ms | 2.37 ms | 51.5 ms |
| LoRaWAN layout, 20 B | 70.25 | **281 us** | 562 us | 1.38 ms | 2.77 ms | 56.6 ms |
| MeshCore raw, 6 B, preamble 32, CR 4/8 | 76.25 | **305 us** | 610 us | 1.50 ms | 3.00 ms | 69.9 ms |

Bold = fits one 409.6 us buffer at 40 Msps (the 201 us case also fits 204.8 us at 80 Msps). The ISM2400 LoRaWAN parameters (BW 812.5 kHz) and everything narrower are far too long for one buffer.

## 3. Three ways to transmit, and what each can do

* **Scheme S, the whole frame in one buffer (first target).** SF5, BW 4 MHz at 40 Msps (oversampling 10) or BW 8 MHz at 80 Msps (oversampling 10), frame <= about 12 to 24 bytes (see the table). The host renders the complete frame (preamble, sync, SFD, header, payload) at the engine rate with gr-lora_sdr's `modulate`
  (`samp_rate_mult = 10`, so no resampling), quantises to 10-bit I/Q at peak 200, uploads 64 KiB, the chip triggers once (and repeats it every few ms for statistics). No rewrite, no timing control, no holes. The frame is LoRa modulation but not a standard LoRa bandwidth, so only our own receiver decodes it.
* **Scheme W, symbol by symbol with rewrite.** For frames that do not fit one buffer: BW 812.5/1625 kHz (the real SX1280 bandwidths) or 500 kHz, SF5..9, Ts <= one buffer. The chip keeps a periodic base chirp ring and copies a window (symbol k = start at k x oversampling) into bank 2 for every symbol,
  locks the symbol period to Ts with the cycle counter and drops the last part of every symbol (the rewrite gap). Expected cost: play fraction 0.82 at 40 Msps, 0.695 at 80 Msps = about -1.7 / -3.2 dB in the FFT peak (hypothesis, L0). Preamble symbols are identical and need no copy. The host sends only symbol values and the frame parameters.
* **Scheme P, polar (the existing transmitter) for narrow LoRa.** BW 62.5 to 125 kHz, long symbols: the PLL word at 40 kHz and 457 Hz steps with error feedback follows a chirp (62.5 kHz over 4096 us = 164 updates of 0.38 kHz). It needs the offset range widened from +-44 to about +-70 steps, see the work item below.
  Since `main` has the RTTY and thermal-drift commits (merged 2026-10-04), P reuses what is there: `txmodes.FskModulator` already sends one frequency per update at constant gain (a LoRa chirp is a frequency track, so only a track generator and a `-m lora` mode in `cli_tx.py` are new), and `--thermal` cancels the carrier drift for long frames (irrelevant for S and W, whose frames last under 4 ms).
  The engine does not help here: it runs 640 times faster than the signal and every 409.6 us slice would need new content (the base chirp ring for SF8/62.5 kHz alone is 640 KB).

**Receiving at 13 cm.** The ESP32 receiver (`espdr-rx`) delivers 250 or 333 ksps, enough only for LoRa up to about 125 to 250 kHz bandwidth. Therefore two worlds:

| | wide LoRa (BW >= 0.8 MHz, SF5..) | narrow LoRa (BW 62.5..125 kHz) |
|---|---|---|
| TX | I/Q engine, scheme S or W | polar, scheme P |
| RX | PlutoSDR (16 Msps for BW 4 MHz; 32 Msps for 8 MHz is beyond the 30.72 Msps of the Ethernet setup, so use oversampling 2 there) or HackRF | ESP32 `espdr-rx` through rtl_tcp, or the Pluto |
| ESP32-only link | no (the ESP32 cannot receive wide) | yes |

## 4. Frame layout (our own network, free choice)

* **Own minimal frame, plaintext (default):** short header with callsign and counter, payload, 16-bit CRC from the LoRa PHY. Shortest, no cryptography, clean for the amateur band.
* **MeshCore raw** (`meshcore_codec.build_packet(ROUTE_DIRECT, PT_RAW_CUSTOM, payload)`): 2 header bytes plus payload, unencrypted; useful because the existing MeshCore tools parse it (`parse_packet`, `summarize_packet`). Adverts (`build_advert`) are public and unencrypted too (signature only), 100+ bytes: not for scheme S.
* **LoRaWAN layout (optional):** MHDR, DevAddr, FCtrl, FCnt, [FPort, FRMPayload], MIC = 12 bytes without payload. Preamble 8, sync word 0x34 (symbols 24, 32), CR 4/5, uplink without inverted IQ. There is no LoRaWAN code in `pluto-tx`; a builder with the MIC (AES-CMAC, `cryptography`) is about 40 lines.
  LoRaWAN always encrypts FRMPayload (AES) and authenticates with the MIC. **On the amateur band keep FRMPayload empty or use a published test key**, and check the rules; the MIC alone only authenticates. It buys test vectors and a well-defined short frame, nothing for the timing.
* Legal in all cases: inside the amateur band, no encryption that obscures the content, callsign in the frame, cabled or attenuated for tests, transmit permission required. Every emitted line must be in the band: LO +- (offset + BW/2) (4 to 8 MHz wide: check the band plan segment), image (33 dB raw, 62 dB corrected), harmonics, LO feedthrough at the LO.

## 5. Test chain built from the `pluto-tx` functions (read-only repository, used as a library)

New scripts live in this repository (`scripts/lora_iq/`), importing from `pluto-tx` via `PYTHONPATH` (gr-lora_sdr needs the `LD_LIBRARY_PATH` that `tests/test_meshcore_flowgraph.py` uses).

| Step | Existing function | Use |
|---|---|---|
| payload | `pluto_tx.meshcore_codec.build_packet` / `build_advert`, `Identity.generate()`; own frame builder | the frame bytes |
| render | `pluto_tx.lora.LoraTxEncoder` (`sf`, `bw`, `cr`, `preamb_len`, `sync_word`, `samp_rate_mult`), IQ from a vector sink at the end instead of the SDR sink; for scheme S with `samp_rate_mult=10` at the engine rate | the single-buffer frame |
| reference | `PlutoTxFlowgraph(device_type="fake", mode=MODE_MESHCORE)` and its `device.sink` (as `send()` in `tests/test_meshcore_flowgraph.py`) | whole-chain reference for the offline model |
| symbol values (scheme W) | rebuild the chain of `LoraTxEncoder` (whitening, header, add_crc, hamming_enc, interleaver, gray_demap) with a vector sink on the `gray_demap` output (= input of `modulate`) | symbols the ESP32 plays |
| framing | `config.LoraPreset`-style values (`spreading_factor`, `bandwidth_hz`, `coding_rate`, `preamble_len`, `sync_word`) | our own parameter set, not the 868 MHz presets |
| receive | `pluto_advanced_rx.lora_rx.LoraRxDecoder(sf, bw, cr, center_freq_hz != 0, preamb_len, sync_word)` with the Pluto, or a numpy dechirp/FFT receiver as a second, independent check | CRC ok, bytes identical, `parse_packet` for MeshCore raw |

Open point that decides the receiver: **`pluto-tx` tested SF7..12 at 62.5..500 kHz only.** SF5/SF6 and BW 4 to 8 MHz are unverified in gr-lora_sdr (oversampling, buffer sizes, LoRa header and Hamming at SF5); this is step L0a below, and the numpy receiver is the fallback.

## 6. Phases with gates

**L0a, what gr-lora_sdr can do (local machine, no radio).** Loopback `LoraTxEncoder` -> `LoraRxDecoder` at SF5 and SF6 with BW 4 and 8 MHz (oversampling 10 and 2), then the same at SF7 with 125/500 kHz as a control. Bit-exact payload round trip over 20 random payloads.
*Gate:* works -> use gr-lora_sdr as the receiver; fails -> write the numpy receiver first (dechirp, FFT, Gray, deinterleave, Hamming, CRC) and use it as the reference.

**L0b, offline engine model.** Reference IQ -> engine model -> decode, packet error rate against SNR. Model: 10-bit quantisation at peak 200, image 33/62 dB, LO feedthrough, +40 kHz crystal offset, the 337 ns pause; for scheme W the truncated tail (play fraction 0.82/0.695); for scheme P the polar staircase (40 kHz, 457 Hz, +-70 steps, error feedback).
*Gate:* scheme S loses at most about 1 dB against ideal; scheme W at most about 3 dB; otherwise drop that scheme.

**L1, scheme S on the air (hardware; one new op for bulk upload).** BW 4 MHz, SF5 at 40 Msps, 12 to 16 byte frame. A new op `IQ_OP_LOAD` uploads 16384 words into bank 2 (the existing ops only fill from a generator or poke single words); then `IQ_OP_PLAY` triggers once, repeated every few ms. First the preamble alone (identical up-chirps: compare peak bin stability and SNR with the model), then the whole frame.
Receiver: Pluto at 16 Msps, `LoraRxDecoder`. Metrics: packet error rate against attenuation, spectrum, out-of-band lines, LO feedthrough, image.
*Gate:* CRC-ok frames with the right bytes, reproducible, loss against the model within a few dB. Then repeat at BW 8 MHz and 80 Msps (the 201 us frame) and with a MeshCore raw frame (305 us, 40 Msps only).

**L2, scheme W (hardware, new research op `IQ_OP_LORA`).** BW 812.5 kHz or 1625 kHz (or 500 kHz), SF5..7: the chirp ring is rendered once (a few thousand words), the symbols arrive from the host, the chip copies windows and triggers with the symbol period locked to Ts. Frames of 12 to 20 bytes (2 to 3 ms).
Vary the play fraction on purpose and compare with L0b. *Gate:* CRC-ok frames within about 3 dB of the model. Only if L1 passed.

**L3, scheme P at BW 62.5 kHz, SF8 (parallel to L1/L2, needs no engine).**
Work items, in this order:
1. *Widen the offset clamp as one coordinated change* (a +-62.5 kHz sweep of 62.5 kHz bandwidth needs about +-68 steps; the clamp is wired into several places): `TX_MAX_STEPS` and `TX_LOW_MARGIN` in `firmware/protocol/transmit.h`, the clamp in `firmware/src/radio.c` (`radio_tx_run`, around line 706), `MAX_STEPS` in `src/espdr/txmodes.py`, `TX_LOW_MARGIN` in `src/espdr/txlink.py` and `src/espdr/sim.py`, the LO check in `src/espdr/cli_tx.py` (around line 457), and the tests (`tests/test_cli.py`, `tests/test_modes.py`, `tests/test_rtty.py`).
2. *Choose the channel centre, not an LO shift.* Only the low byte of the PLL word is written, so the sweep (about 137 steps for 62.5 kHz) must stay inside one low-byte window (no carry into the next byte). The LoRa centre frequency is free at 13 cm, so the host picks a centre whose sweep fits (low byte of the centre word in about 70..185) instead of using `txlink.choose_lo` and `static_hz` to move the LO.
3. *Generator and mode:* a LoRa frequency-track generator (symbol values -> frequency offset per update) feeding `FskModulator`, and `-m lora` in `cli_tx.py`.
4. *Unify the two temperature readers before merging the branches:* `radio_tx_temp()` / `TX_OP_TEMP` (46) on `main` and `iq_temp()` / `IQ_OP_TEMP` (82) on `research/iq-tx` drive the same sensor; keep one implementation. `git merge-tree` reports a clean merge of `research/iq-tx` into `main` (checked 2026-10-04, not built).
Then: host converts symbol values into frequency offsets, decode with the Pluto and then with the ESP32 receiver (`espdr-rx` through rtl_tcp, 250 ksps) for an ESP32-only link.
*Gate:* answers whether narrow LoRa works at all on this chip and whether the ESP32 can receive it.

**L4, comparison and decision.** Table: scheme, parameters, frame time, packet error rate against level, loss in dB against ideal, spectrum. Result as Stage 7 in `TX-RESEARCH.md` and a line in the branch README. Expected product statement if it works: wide LoRa bursts through the engine (RX at the Pluto), narrow LoRa through the polar path (ESP32-only link).

## 7. Risks and stop rules

* **gr-lora_sdr limits at SF5 / wide BW:** see L0a; the numpy receiver is the fallback and doubles as an independent check.
* **The frame must fit one buffer exactly:** at BW 4 MHz the LoRaWAN-layout frame takes 402 of 409.6 us. A longer payload or preamble breaks scheme S (go to BW 8 MHz at 80 Msps with <= 12 bytes, or to scheme W). Compute the symbol count from the real encoder output, not from the formula.
* **Amplitude and spectrum of a 4 to 8 MHz wide signal:** the TX filter is flat only with block 0x67 registers 12/13 = 0; peak <= 200 of 511; check the Pluto's own filter and the sampling rate.
* **Symbol clock accuracy (scheme W):** the symbol period comes from the CPU cycle counter (240 MHz, same crystal as the LO); measure over a whole frame; stop if the period jitters by more than about 1/4 chip.
* **Spurs from the rewrite gaps (scheme W):** if the decoder sees ghost peaks, shorten the gap (second core writing in parallel, to be measured as with `IQ_OP_BENCH`) or use 40 Msps.
* **Oversampling not an integer** (812.5/1625 kHz at 40 Msps: 49.2/24.6): round the window offset to the nearest word (error below half a word, far below one chip), or start with BW 500 kHz / 1 MHz.
* **Everything radiates:** short bursts (the research firmware limits the length), lowest useful gain code, cable and attenuator, transmit permission, no encrypted content.

## 8. Deliverables

`scripts/lora_iq/model.py` (offline model), `scripts/lora_iq/render.py` (frame -> 10-bit words, symbol values), `scripts/lora_iq/run.py` (L1 to L3 runs, receiver, metrics), a numpy LoRa receiver if L0a fails,
`firmware/protocol/iqtest.h` + `firmware/src/radio.c` (`IQ_OP_LOAD`, later `IQ_OP_LORA`), `docs/research/LORA-IQ.md` (log in the style of `IQ-TX-PHASE-A.md`), a Stage 7 entry in `TX-RESEARCH.md`.
