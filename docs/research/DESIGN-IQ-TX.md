# Design: using the I/Q playback engine in the product (Phase C, first version)

Status 2026-10-04. The engine works (see [IQ-TX-PHASE-A.md](IQ-TX-PHASE-A.md)). This page turns the measurements into design decisions and lists the one experiment that decides
between two designs. Nothing here is implemented in the product images yet.

## What we know (measured)

| Fact | Value |
|---|---|
| Source | bank 2 (`0x3FCD0000`), word 0 upward, 32-bit words, I in 9:0, Q in 19:10; no start offset, no loop, 1..16384 words per trigger |
| Rate | 80 Msps (rate bit 1) or 40 Msps (bit 0); 204.8 us or 409.6 us per full buffer |
| Re-trigger pause | 337 ns (27 samples of the 80 MHz clock) in the poll-done loop; stable to a fraction of a sample; counted in the phase of a tone it gives -48 dBc sidebands |
| Usable amplitude | peak <= 200 of 511 (linear, IM3 > 48 dBc); clips hard above 250 |
| Bandwidth | flat +-35 MHz with TX filter registers (block 0x67 regs 12/13) = 0 |
| Image | 33 dB raw, 62 dB with `Q' = 1.010 Q - 0.055 I` (board specific, to be calibrated per board) |
| Output level | set by PBUS (5,1)/(5,3), not by the gain code |
| Host link | 0.87 MB/s: no sample streaming at 40/80 Msps; at most about 400 ksps of 16-bit I/Q |
| Phase noise | -80 dBc/Hz at 2 kHz, -90 at 20 kHz..300 kHz, -100 at 1 MHz (upper bound) |

The playback needs bank 2, so the host ring of the polar transmitter (banks 0..2) becomes banks 0 and 1. Bank 3 is never touched.

## What the engine is good for, given the constraints

1. **Periodic and static signals** (one buffer, re-triggered): test tones, two-tone, chirps, noise, preamble patterns, carriers at an offset from the LO. Cheap, no new problem; the seam is solved by counting the pause.
2. **Narrowband I/Q from the host, up-converted on the chip** (the interesting one): the host sends complex baseband at 250 to 400 ksps (the existing 16-bit/8-bit link), the chip interpolates it to 40 Msps and shifts it by a chosen offset (an NCO), and the engine plays it. This replaces the polar trick
   for SSB/FM and makes digital modes (any modulation within a few hundred kHz, with full I/Q) possible. It needs a continuous stream of new buffers: **design B below**.
3. **Symbol-based modes** (FSK, GFSK, LoRa chirps, FT8/WSPR tones, POCSAG): each symbol a short precomputed waveform; the problem is to change the buffer content between triggers. Also needs design B, or a trick with the count.
4. **Wideband bursts** (up to 40 MHz wide, 204.8 us per burst): OFDM symbols and similar. The phase noise (28 % EVM per 179 us symbol) limits coherent high-order modulation, so only low orders and non-coherent schemes make sense. Bursts, not a product mode for now.

## Two designs for continuous output

* **Design A, no refill.** One buffer, re-triggered with the pause compensation. Everything that is periodic (modes 1, and CW/FSK by steering the PLL word as the polar transmitter does) works. The buffer cannot change during
  operation, because the pause is 337 ns and bank 2 is 64 KiB: rewriting it between triggers is impossible.
* **Design B, chase the read pointer.** The engine reads bank 2 front to back at 40 or 80 Msps. The CPU writes the *next* buffer into the part that has already been read, always staying behind the reader, and triggers again when the engine reports done.
  Budget at 40 Msps: 6 CPU cycles (240 MHz) per word. One lookup in a precomputed table, an add and a store fit; a general interpolation/NCO is borderline on one core; both cores can split the buffer (core 1 writes the second half).
  At 80 Msps there are 3 cycles per word: only copying/lookup, no arithmetic. **Whether this works at all is not known**: the writer must not overtake the reader, the engine must read what was just written without wait states or corruption, and the extra bus traffic must not slow or glitch the engine. Experiment E1.

## Experiment E1 (feasibility of design B) — RESULT: pacing works, data integrity does not (2026-10-04)

Firmware op `IQ_OP_STREAM` in the research build: the engine plays at 40 Msps; after each trigger the CPU writes the next buffer from a lookup table with a phase accumulator (a complex tone), paced so that it stays behind the reader (word n is written no earlier than `6 n + margin` cycles after the trigger); the next trigger is issued
when both the engine has finished and the writer has. Measure with the Pluto: line purity and sidebands at the buffer rate (2.44 kHz) and its multiples against the static buffer with pause compensation (-48 dBc), and the spurs produced by the writer if it ever touches the playing part.
Then change the phase increment per buffer (FM) and check that the line follows. **Pass** if the line is as clean as in design A. **Fail** (spurs, glitches, engine errors): design A only, and symbol modes by selecting between a few precomputed buffers *between bursts* (not gap-free).

## Product pieces after E1 passes

* Firmware: a TX mode `TX_MODE_IQ` next to the polar one in `firmware/protocol/transmit.h`: ops for LO, rate bit, output level (PBUS pair), filter code, offset (NCO), I/Q correction (g, p), start/stop; the record stream carries 16-bit I/Q pairs at the chosen baseband rate; status frames as now.
  The tone generator enable bit (`0x60006040` bit 18) is cleared after keying; bank 3 is masked; the watchdog (500 ms without data) and the length limit stay.
* Safety: every emitted line must be inside the allowed band, not just the LO. With offset `f_off` and baseband half-width `B`: LO +- (|f_off| + B), the image at -f_off, harmonics, and the LO feedthrough at the LO. The firmware refuses combinations that leave 2320..2450 MHz and the host warns about harmonics.
  The amplitude is limited to the linear range (peak 200), the default output power is the lowest PBUS setting that gives a usable signal, with the same cabled/attenuated default as the polar mode.
* Host: `espdr-tx --mode iq` with a baseband source (a file of complex samples, a pipe, or a modulator from `txmodes.py`), calibration command (`espdr-tx --calibrate-iq`, uses an SDR receiver the way the research script does, stores g and p per board), simulator support in `sim.py`, tests in `tests/test_modes.py`.
* Keep the polar FM/SSB as the default for voice until a buffer-based voice mode beats it on unwanted sideband (61 to 63 dB) and IM3 (33 dB); the numbers above already beat the IM3 (better than 48 dBc) and the image can reach 62 dB at the right offset.

## Open questions

* The harmonics at the real operating level (the 3rd harmonic was only measured in the clipped regime) and what an amateur band transmitter needs in out-of-band emission.
* The rate bit 0 at 40 Msps gives 20 MHz of bandwidth: is that enough for every planned mode? (Probably, narrowband needs far less.)
* The pause compensation G = 27 depends on the CPU loop; with design B the pause is set by the writer, so G must be measured again.
* 5/6 LO mode for TX (reported by the original author): not measured here.

## E1 result

Research build ops `IQ_OP_STREAM*`, `IQ_OP_WCHECK`, `IQ_OP_STREAM_CHECK`, `IQ_OP_BENCH` (firmware/protocol/iqtest.h), PlutoSDR and a word-by-word readback of bank 2.

**What works.**

* The engine accepts a re-trigger every 409.6 us at 40 Msps for as long as the CPU keeps writing: 731 of 732 expected buffers in 300 ms, no late block, the line at the right frequency (+65 dB).
* The CPU can write fast enough: a lookup-table writer costs 5.2 cycles per word (budget at 40 Msps: 6). Three things were needed: no `volatile` on the destination (the compiler puts a `memw` barrier before every volatile store, about one cycle more per word), an unrolled
  loop with two interleaved phase accumulators, and one call per 256 words (a function call costs about 38 cycles). A plain loop costs 7.0, a `volatile` unrolled one 6.7.
* Writing does not slow the engine and the engine does not slow the CPU (timing identical with the engine idle, at 40 and at 80 Msps).
* Trigger without delay: write the last 512 words of the next buffer *after* the trigger (the reader reaches them a whole buffer later) and poll the done bit in a tight loop before it.

**What does not work: the written words are wrong while the engine reads the bank.** `IQ_OP_WCHECK` writes the 16384 words of a tone and compares them with the ideal tone afterwards:

| Writer | Engine | Wrong words of 16384 (tone 0.625 MHz) |
|---|---|---|
| block writer | idle | **0** |
| block writer | playing at 40 Msps | 1900 (615 for a 5 MHz tone) |
| plain volatile stores, `memw` before each | playing | 2906 |
| every word stored twice | playing | 3876 |

The errors are identical from run to run (deterministic), spread evenly over the whole buffer in a pattern with a period of 512 words (about 45 per 1024 words in the paced streaming run, where the writer stays 30 to 64 words behind the reader, so it is not
a collision of neighbouring words), and the wrong value is often a value from a few words earlier (words 326 and 327 contained the data of words 322 and 323). Stores are not simply dropped (a dropped store would leave the identical old tone in the buffer,
the stored tone in these tests has a different phase from the previous one). Looks like a hardware problem when the CPU writes into the bank the engine reads (store data from an earlier cycle on a stalled access). The sidebands at the buffer rate in the streaming runs (-18 to -26 dBc at 5 MHz,
nearly independent of the pause compensation) come from these wrong words, not from the pause.

**Consequences.**

* Design B with CPU stores into the playing bank is **not usable** as it is. Buffers can only be changed when the engine is not reading, which means a gap of at least the copy time (about 90 us for 16384 words), so no gap-free stream.
* Design A (one fixed buffer, re-triggered, pause compensated, G = 27 samples at 80 Msps for the poll-done loop) stays the base of every product mode. With it periodic signals are clean (first sidebands -48 dBc), and everything that is changed *between* bursts works.
* Not tried yet, in the order of effort: (1) copy with the GDMA memory-to-memory channel instead of CPU stores, in case the DMA master is not hit by the problem; (2) write only the part of the bank the engine reads *later* with a larger distance than 64 words (perhaps the errors are tied to a read-ahead of the engine; the evenly spread errors argue against it); (3) a readback-and-repair pass (reads may be reliable);
  (4) the other three banks cannot be used as the playback source (the engine reads bank 2 only).
* What Design A already gives for the product: carriers and tones at any offset from the LO, two-tone and chirp test signals, modulated bursts of at most 204.8 us (one buffer), and the polar FM/SSB transmitter unchanged. A voice mode that beats the polar transmitter is not in reach with Design A alone.
