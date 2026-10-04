# Plan: can LoRa (MeshCore/Meshtastic framing) be transmitted through the I/Q playback engine?

Status: plan, nothing measured yet. Base: branch `research/iq-tx` ([README](README.md), [DESIGN-IQ-TX.md](DESIGN-IQ-TX.md), [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md)).
Test tools: the LoRa/MeshCore code of the (read-only) `pluto-tx` repository, available on the development machine next to the ESP32, plus a PlutoSDR as the receiver.

## 1. What the engine allows, and the one constraint that decides everything

Measured on the branch: 1..16384 words per trigger (204.8 us at 80 Msps, 409.6 us at 40 Msps), no loop, bank 2 only, re-trigger pause 337 ns, peak amplitude <= 200 of 511, usable bandwidth about +-35 MHz (20 MHz at 40 Msps),
and **bank 2 cannot be rewritten while the engine plays** (4 to 12 % wrong words from the CPU, GDMA worse). The CPU writes 16384 words in about 90 us (5.5 ns/word) when the engine is idle.

Consequence for any signal that changes from buffer to buffer: the engine must be idle while the next buffer is written. If every word is rewritten, the unavoidable dead time is the write time:
5.5 ns / (25 ns + 5.5 ns) = **18 % of the time at 40 Msps, 30 % at 80 Msps** (parallel writing by both cores might halve it; unmeasured). A signal that is the same buffer again (a preamble of identical up-chirps) has only the 337 ns pause.

## 2. Why LoRa is a good candidate anyway

* Constant envelope and **non-coherent** per symbol (dechirp, FFT, peak bin): immune to the PLL phase noise that limits OFDM here (28 % EVM per 179 us), tolerant of the seam and of the +40 kHz crystal offset (receivers estimate CFO and sampling offset).
* A symbol of value k is the base up-chirp, cyclically shifted by k chips: **symbol k = a window of one periodic base chirp** (starting at k x oversampling words). The chip needs one stored base chirp and a memcpy of a window per symbol; no arithmetic in the write loop.
* The host does **not** stream samples: it sends only the symbol values (SF bits each, a few hundred symbols for a MeshCore packet) plus the frame parameters. The 0.87 MB/s link is irrelevant.
* If part of every symbol is missing (the engine plays the first n words of the symbol period, the last words are the rewrite gap), the demodulator sees a tone that is shorter by the missing part: **about -1.7 dB at 40 Msps, -3.2 dB at 80 Msps** in the FFT peak, at an unchanged symbol clock. Hypothesis, to be measured in L0.
* LoRa's FEC (CR 4/7, 4/8) corrects one bad bit per codeword; the interleaver spreads one wrong symbol over one bit in each of the SF codewords. One lost symbol per interleaver block is therefore correctable in principle (not needed for the truncated-tail scheme, but it gives margin).

## 3. Which LoRa settings fit

Symbol time `Ts = 2^SF / BW`; words per symbol = `Ts x Fs`; one buffer holds 16384 words. "Oversampling" = Fs/BW (an integer makes the window offsets exact).

| Setting | Ts | words at 40 Msps | buffers per symbol | verdict |
|---|---|---|---|---|
| LoRa 2.4 GHz SF5, BW 1625 kHz | 19.7 us | 788 | 0.05 | fits easily, many symbols per second; oversampling 24.6 (not an integer) |
| LoRa 2.4 GHz SF7, BW 812.5 / 1625 kHz | 157.5 / 78.8 us | 6302 / 3151 | 0.38 / 0.19 | fits |
| LoRa 2.4 GHz SF9, BW 1625 kHz | 315 us | 12603 | 0.77 | fits (40 Msps only) |
| Meshtastic ShortTurbo SF7, BW 500 kHz | 256 us | 10240 (os 80) | 0.62 | fits, integer oversampling: **first hardware target** |
| Meshtastic ShortFast SF7, BW 250 kHz | 512 us | 20480 (os 160) | 1.25 | needs two buffers per symbol (**gate in the middle of a symbol), second step |
| MeshCore EU/UK Narrow SF8, BW 62.5 kHz | 4096 us | 163840 | 10 | does not fit: the base chirp ring alone is 640 KB (more than the free SRAM) and 10 buffers per symbol with a gap each |
| Meshtastic LongFast SF11, BW 250 kHz | 8192 us | 327680 | 20 | does not fit |

Narrow, long-symbol LoRa (what MeshCore and Meshtastic actually use on 868 MHz) is **not** a job for the I/Q engine: the engine runs 640 times faster than the signal needs, and every 409.6 us slice would need new content. The polar transmitter
(PLL word at 40 kHz, 457 Hz steps with error feedback) is the natural baseline there: a 62.5 kHz chirp over 4096 us is about 164 updates with 0.38 kHz per step, which is finer than one PLL step. It needs the offset range widened from +-44 to about +-70 steps
(`TX_MAX_STEPS`, `TX_LOW_MARGIN` in `firmware/protocol/transmit.h`). This is **baseline P** in the plan; it answers "is the engine the right tool" instead of assuming it.

## 4. Frequency and compatibility: what can and cannot be tested

* The LO reaches 2320..2450 MHz (1.84..2.79 GHz with the reported 5/6 mode). **868/869 MHz is out of reach**, so no real MeshCore/Meshtastic node (SX126x/SX127x at 868 MHz) can receive these tests.
  What is tested is the **waveform and the framing**: same SF, BW, CR, preamble, sync symbols, header, CRC and payload, at 2.4 GHz, decoded by `pluto-tx`'s own receiver and parsed by `meshcore_codec`.
* A real 2.4 GHz LoRa node (SX1280) would be a later, optional check; its bandwidths are 203/406/812.5/1625 kHz.
* Legal: only inside the amateur band (2320..2400 MHz, our firmware also allows up to 2450), cabled and attenuated, transmit permission required. No encrypted payloads on the air: use a MeshCore advert (public key, name, signature; not encrypted) or a plain test string
  (`meshcore_codec.build_advert`), not group or direct messages (AES). Every emitted line must be in the band: LO +- (offset + BW/2), image (33 dB raw, 62 dB corrected), harmonics, LO feedthrough at the LO.

## 5. Test chain built from the `pluto-tx` functions (read-only repository, used as a library)

New scripts live in this repository (`scripts/lora_iq/`), importing from `pluto-tx` via `PYTHONPATH` (gr-lora_sdr needs the `LD_LIBRARY_PATH` that `tests/test_meshcore_flowgraph.py` uses).

| Step | Existing function | Use |
|---|---|---|
| packet | `pluto_tx.meshcore_codec.build_advert` / `build_packet`, `Identity.generate()` | the payload bytes |
| reference baseband | `PlutoTxFlowgraph(device_type="fake", mode=MODE_MESHCORE, ...)`, IQ from `fg.device.sink.data()` (as `send()` in `tests/test_meshcore_flowgraph.py`, 2.5 Msps) | whole-chain reference for the offline model (L0), including preamble/sync/SFD from `config.LoraPreset` |
| symbol values | rebuild the chain of `pluto_tx.lora.LoraTxEncoder` in a script (whitening, header, add_crc, hamming_enc, interleaver, gray_demap) and put a vector sink on the `gray_demap` output (= the input of `modulate`) | the symbol sequence that the ESP32 will play; `modulate` itself is not needed in the loop |
| framing | `config.LoraPreset` (`spreading_factor`, `bandwidth_hz`, `coding_rate`, `preamble_len`, `sync_word` = 0x12 -> symbols 8, 16 for MeshCore; `meshtastic_sync_symbols(sf)` for Meshtastic) | preamble length, sync symbols, 2.25 SFD down-chirps |
| receive | Pluto at LO +- offset, `pluto_advanced_rx.lora_rx.LoraRxDecoder(sf, bw, cr, center_freq_hz != 0, preamb_len, sync_word)` or the full RX flowgraph; then `meshcore_codec.parse_packet` / `summarize_packet` | verdict: CRC ok, bytes identical, packet parses |

Reference bytes are compared at three levels: symbol values (RX demod output), payload bytes (CRC ok), parsed MeshCore packet. A numpy dechirp/FFT receiver is added as a second, independent check (it runs without GNU Radio, also in cloud sessions).

## 6. Phases with **gates

**L0, offline model (no hardware, local machine; the numpy part also runs anywhere).** Take the reference IQ of a MeshCore advert at the chosen BW, resample to 40 or 80 Msps, apply the engine model, decode, measure packet error rate against SNR:
10-bit quantisation at peak <= 200, the truncated tail (play fraction f = 0.82 / 0.695), the 337 ns pause, image rejection 33/62 dB, LO feedthrough, crystal offset +40 kHz, the polar staircase for baseline P (update 40 kHz, 457 Hz, +-70 steps, error feedback).
*Gate:* decoding with f <= 0.82 loses at most about 3 dB against the ideal signal; else drop scheme W.

**L1, one fixed buffer on the air (hardware; existing ops only: IQ_OP_FILL / PLAY).** BW 500 kHz, SF7: a buffer with one up-chirp (Ts = 256 us = 10240 words at 40 Msps, minus the pause), re-triggered back to back = a preamble of identical up-chirps (design A, gap 337 ns, no rewrite). The chirp comes from a new fill mode that renders the base chirp.
Pluto + dechirp: peak bin position stable, symbol clock, SNR; `frame_sync` of gr-lora_sdr detects the preamble. Compare the line quality with the ideal chirp from L0.
*Gate:* detected preamble with a stable peak; otherwise fix the chirp rendering (phase wrap, oversampling) before going on.

**L2, symbols with rewrite, scheme W (hardware, new research op `IQ_OP_LORA`).** The host sends the parameters (SF, BW code, oversampling, preamble length, sync symbols) and the symbol values. The firmware renders the base chirp ring once (10240 words at BW 500 kHz, SF7, in banks 0/1; bank 2 is the playback bank, bank 3 stays masked),
then per symbol: wait for done, copy the window (up to two segments) into bank 2, trigger, with the symbol period locked to `Ts` by the cycle counter (play count = `Ts x Fs / (1 + 5.5 ns x Fs)`, i.e. the tail is dropped). Identical symbols (preamble) skip the copy. SFD down-chirps: second ring, conju**gate. Pause compensation as in design A.
Receiver: Pluto, `LoraRxDecoder`. Start with a frame of 16 preamble + sync + SFD + header + a short payload (as few bytes as possible), then a MeshCore advert (about 100 bytes, roughly 100 to 200 symbols at SF7).
*Metrics:* packet error rate against attenuation (cable + attenuator or distance), against the play fraction f (vary the count deliberately, to compare with the L0 prediction), measured symbol timing jitter, spectrum and out-of-band lines.
*Gate:* CRC-ok packets with the right bytes, reproducible; loss against L0 prediction within a few dB.

**L3, two buffers per symbol (BW 250 kHz, SF7, Ts = 512 us).** The symbol is cut into two halves with a gap in the middle. Models in L0 first (periodic 2 kHz-order gating gives ghost peaks at about -14 dBc; check the decoder). Only if L2 passed and L0 predicts success.

**L4, baseline P: polar LoRa at BW 62.5 kHz, SF8 (MeshCore EU/UK Narrow parameters).** Extend the polar transmitter's offset range, host converts symbol values to frequency offsets (the chirp is the frequency track: 164 updates per symbol, error-feedback quantised), decode with the same receiver chain.
*Gate:* answers whether narrow, long-symbol LoRa works at all on this chip; it is the only path to MeshCore/Meshtastic's real parameters (waveform only, 2.4 GHz). Needs no engine, so it can run in parallel to L2.

**L5, comparison and decision.** Table: scheme, parameters, packet error rate against level, loss in dB against ideal, spectrum. Result written as Stage 7 in `TX-RESEARCH.md`; the `research/iq-tx` README gets a line. If W works at BW >= 500 kHz and P works at 62.5 kHz, the product statement is: wide LoRa through the engine (waveform-compatible with SX1280 at 2.4 GHz after retuning BW to 812.5/1625 kHz), narrow LoRa through the polar path.

## 7. Risks and stop rules

* **Symbol clock accuracy:** the symbol period comes from the CPU cycle counter (240 MHz, derived from the same crystal as the LO). Measure the period over a whole frame; error budget about 1 chip over a frame. Stop W if the period jitters by more than about 1/4 chip.
* **Gate/hole spurs:** if the decoder's FFT sees ghost peaks, shorten the hole (parallel write by both cores; the second core must not disturb the engine, to be measured as in `IQ_OP_BENCH`), or use 40 Msps instead of 80.
* **Oversampling not an integer** (812.5/1625 kHz at 40 Msps): round the window offset to the nearest word (error below half a word of 25 ns, far below one chip) or use the test BW 500 kHz/1 MHz first.
* **gr-lora_sdr quirks** (documented in `pluto_tx/lora.py`: needs `center_freq != 0`, big buffers, preamble/sync symbols of the preset, LD_LIBRARY_PATH): the numpy receiver is the cross-check.
* **Memory:** the ring for BW 250 kHz, SF7 is 20480 words (80 KB) and fits banks 0/1; BW 62.5 kHz does not (see section 3), which is why it is baseline P.
* **Everything radiates:** short bursts (the research firmware limits the length), lowest gain code, cable and attenuator. No encrypted MeshCore messages on the amateur band.

## 8. Deliverables

`scripts/lora_iq/model.py` (offline model, L0), `scripts/lora_iq/symbols.py` (symbol values from the pluto-tx chain), `scripts/lora_iq/run.py` (L1 to L4 runs, receiver, metrics), `firmware/protocol/iqtest.h` + `firmware/src/radio.c` (`IQ_OP_LORA`, chirp ring, window copy, paced triggering),
`docs/research/LORA-IQ.md` (log in the style of `IQ-TX-PHASE-A.md`), a Stage 7 entry in `TX-RESEARCH.md`.
