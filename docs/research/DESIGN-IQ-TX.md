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

## Experiment E1 (feasibility of design B) — runs next

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
