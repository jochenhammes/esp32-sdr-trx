# Plan: does the ESP32-S3 have a direct I/Q transmit input?

Working plan for the hardware sessions. Status: Phase 1 (disassembly) is done in part, Phase 2 and 3 need the board and a PlutoSDR/HackRF.
Background: [TX-RESEARCH.md](TX-RESEARCH.md) (Stages 3 to 5), register list: [REGISTER-MAP.md](REGISTER-MAP.md).

## Where we are

* Stage 3: the test-tone registers `0x60006040/44` are not an I/Q input (negative).
* Stage 4: the PHY libraries have no SRAM transmit path in the *named* test functions; C5/C6 `fedump_wr_txmem` are empty stubs.
* **Stage 5: `mac_common:dactrig` in the S3 library programs `0x60033D64`, an engine that plays SRAM (`0x3FCD0000`) to the DAC.** Bits, as inferred: 13:0 length,
  15 mode (0 when arg3 == 1), 19 option, 27:20 second field, 31 start, 18 done. Unknown: base address register, sample format, rate, routing to the RF transmit chain.
  A Reddit commenter says other ESP32 chips have arbitrary I/Q TX with samples read from SRAM; `dactrig` exists on S2, S3, C3, C6, C5, C2, H2.

## Hypotheses

* **H1 (main)** `0x60033D64` is the DAC playback engine; it needs the TX chain keyed (as in Stage 2) and an SRAM buffer.
* **H2** The TX baseband source mux (bit 26 of `0x60006000`, bit 10 of `0x600061E4`, `txcal_debuge_mode`/`force_txon_mode`) decides whether the DAC input is the modem or the playback engine.
* **H3** The ADC dump engine (`0x60033D5C/60/90`, `0x600C101C`) has a TX tap or shares the bank base with the playback engine.
* **H4** The C6 `set_dump_mode` registers (`0x600A0958`, `0x600A70B8`) have a counterpart on the S3.

## Phase 1 remainder (no board needed)

1. Read every place that touches `0x60033D5C..0x60033D90` and `0x600C101C` in the libraries (`mac_common`, `bb_common:tx_a_frame` which uses `0x60033D84`, `wifi`, `rf_test`).
   Goal: find the base-address register and the sample format.
2. Disassemble `txiq_set_reg`, `txiq_cover`, `rfcal_txiq`, `txdc_cal_*`, `force_txon_mode`, `tx_cont_cfg`, `tx_contin_en`, `wifitxout_func`, `WifiTxStart` for the TX source mux.
3. Compare `dactrig`/`adctrig` and the callers on C3 and S2 (same family) for the register layout; the C6 build lacks the register part.
4. Dump the S3 ROM (`esptool dump_mem`) and look for the console command that calls `dactrig`; the ROM/test console shows the arguments (`a2..a5`).
5. Toolchain: ESP crosstool-NG `xtensa-esp-elf` (objdump), `esp-phy-lib` for the `.a` files (see Stage 4 notes: `ar x`, then `objdump -dr`).

## Phase 2: tools on the board

Firmware is bare-metal (`firmware/src/startup.S`, `main.c`, `radio.c`), loaded into RAM (`espdr_load.py`), a reset restores the old state.

1. **Peek/poke op** in the USB control protocol (`firmware/protocol/control.h`, `main.c`) plus `host/python/espdr_reg.py`: read/write one register, read a block, with a deny list (flash, RTC, reset).
2. **Measurement script** for the PlutoSDR (3 Msps, centre 2349.5 MHz as in Stage 2): carrier level, offset, spectrum, burst length; one command per experiment; second look with the HackRF.
3. **Register snapshots** of `0x60005000..0x60008FFF`, `0x6000D000..0x6000EFFF`, `0x6001C000..0x6001FFFF`, `0x60026000..0x60027FFF`, `0x60033000..0x60036FFF` in these states:
   reset, receiver running, test tone on, `dactrig` sequence run, normal Wi-Fi burst. Diff them. Protect against bus hangs (read in small blocks, watchdog).
4. **SRAM-pointer search:** with an engine running, scan the registers for values in `0x3FC8_0000..0x3FCF_FFFF`; these are DMA address registers.

## Phase 3: experiments (stop rule: about one day per hypothesis)

* **E1 (H1)** Reproduce Stage 2 (carrier from `radio_tx_test`). With the carrier on, run the `dactrig` register sequence from our firmware with a constant buffer at `0x3FCD0000`:
  all zero, then a constant, then the library's ramp. Does level, phase or offset of the carrier change? Run both values of bit 15 and bit 19.
* **E2** Buffer with a sine of known frequency, as I/Q word pairs and as one real value; look for a line that moves when the pattern frequency changes. Vary bits 27:20 (rate).
* **E3 (H2)** If E1 and E2 show nothing, change the source mux (bit 26 of `0x60006000`, bit 10 of `0x600061E4`, `txcal_debuge_mode`, `force_txon_mode`) and repeat.
* **E4 (H3)** Vary `DUMP_CONFIG_REG` and `0x600C101C` bits (source, bank) with a pattern in the bank.
* **E5 (H4)** Probe the C6 counterpart registers one or two bits at a time, measuring after each step.
* **E6** Write down what was found: format, rate, buffer length, latency, start/stop behaviour.

Safety: stay in 2320..2400 MHz (the firmware refuses anything else), keep every transmission short (5 s as before), lowest gain, receiver with attenuation, no experiments on flash, reset or RTC registers.
A licence is required; read [the transmitter guide](../transmitter.md).

## Phase 4: decision

* **Positive:** document the path (registers, bits, format, rate); minimal `radio_tx_iq()` with a ring buffer in SRAM; protocol and host tool in `firmware/protocol/transmit.h` and `src/espdr/txlink.py`;
  compare with polar modulation (unwanted sideband 61 to 63 dB, third-order intermodulation 33 dB); then update README/docs and answer the Reddit comment.
* **Negative:** record every tested register and bit with its measurement in `TX-RESEARCH.md` (Stage 6); change the README wording to "investigated systematically, no I/Q input found".
* **Fallback:** Wi-Fi OFDM as a waveform generator (invert scrambler, FEC, interleaver and QAM mapping to put chosen subcarrier values in a normal packet; a known technique for emulating ZigBee/BLE with Wi-Fi). Not a free I/Q input, but wideband waveforms the polar method cannot make.

## Verification rule

A hypothesis counts as confirmed only when the measured spectrum depends on the data written (change the data, the signal changes accordingly), seen on the PlutoSDR and repeated with the HackRF.
A negative result counts only with a written measurement log (register, value, measurement, noise floor).
Run `pytest` (including `tests/test_docs.py`) before committing.

## Key files

`firmware/src/board.h` (add register definitions), `firmware/src/radio.c` (`radio_tx_test`, `tune_pll`: template for test routines), `firmware/src/main.c`, `firmware/protocol/control.h` (peek/poke),
`host/python/espdr_txtest.py`, `espdr_load.py` (runs, RAM load), new `espdr_reg.py`, `docs/research/TX-RESEARCH.md`, `REGISTER-MAP.md`.
