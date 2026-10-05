# Plan: M17 (and every other constant-envelope IQ mode of pluto-tx) through the ESP32-S3 transmitter

Branch: `feature/m17`, from `main` (the polar transmitter with RTTY and thermal correction is in the product; the I/Q engine and `-m lora` stay on `research/iq-tx`).
Status: plan, nothing implemented. Written 2026-10-05 after reading `pluto-tx` (read-only repository, available on the development machine) and this repository.

## 0. Goal, scope, non-goals

**Goal.** Send M17 digital voice (4FSK, 4800 symbol/s, 9 kHz channel) from the ESP32-S3 at 13 cm, using the existing M17 chain of `pluto-tx` (gr-m17 coder, codec2, RRC, FM modulator), and make the ESP32 a **full TX device of `pluto-tx`** next to Pluto and HackRF, so that every mode that works with a constant envelope becomes available on it without mode-specific code on the ESP32 side.

**Scope.** Host-side encoding; the chip only plays frequency/gain records, as it does for FM, SSB and RTTY. 13 cm only (2320..2450 MHz). Receiving is done with a PlutoSDR and the M17 receiver of `pluto-tx` (the ESP32 receiver is an optional later check).

**Non-goals.** No M17 coder on the chip (it would be a port of codec2 and the FEC chain to bare-metal firmware for no gain). No I/Q engine (M17 is narrowband and constant envelope: the engine runs 640 times faster than the signal needs). No SSB-type modes in the first version (FT8, JS8, PSK31, Digitext, FreeDV, RADE, SSB: non-constant envelope, see section 6). No change of the firmware, unless a test shows it is needed.

## 1. What was found

| Topic | Finding |
|---|---|
| M17 chain in `pluto_tx/flowgraph.py` | audio 48 k -> 8 k -> codec2 -> `m17_coder` (symbols +-1, +-3 at 4800/s) -> `m17_rrc` (RRC alpha 0.5, 81 taps, 10 sps = 48 kHz) -> `m17_fm_mod` (`frequency_modulator_fc`, 800 Hz per symbol unit) -> `m17_tx_resampler` (rational, to the device's `quad_rate`) -> `tx_gain` -> device sink. Constants in `pluto_tx/config.py` (`M17_*`). |
| What the sink gets | **complex baseband at the device rate, constant envelope**; frequency excursion +-2.4 kHz nominal, peaks about +-3.05 kHz after the RRC filter (my numpy model). |
| Device interface | `pluto_tx/devices/base.py`: `TxDevice` with `build_sink()` (a GNU Radio block that consumes complex samples), `set_frequency`, `set_power` (power stages), `pre_key`, `post_unkey`, `force_safe_state`, `read_hw_state`, `probe_with_timeout`, `scan_devices_with_timeout`, `prepare_for_start`; registry in `devices/__init__.py` (the AIOC device is registered with a guarded import: the template for an optional device). |
| ESP32 polar path | `espdr.txmodes.FskModulator` already turns "frequency in Hz relative to the LO, one value per update at 40 000/s" into records (28.6 Hz units, constant gain code); the firmware adds second-order error feedback and writes the PLL word. RTTY (true FSK) and FM (+-2.5 kHz, SINAD 31.8 dB) work through it. |
| Quantisation (my numpy model: random 4FSK symbols, RRC, 48 -> 40 kHz, 28.6 Hz units, the firmware's second-order error feedback) | EVM about -50 dB (0.33 %), symbol errors 0 of 3600. M17 needs far less. |
| Data rate | 40 000 records/s x 4 bytes = 160 kB/s of the 0.87 MB/s link. |
| Licences | `pluto-tx` is GPL-3.0, `esp32-sdr-trx` is 0BSD. See section 2 for where the adapter lives. |
| Gaps in `pluto-tx` | `config.DE_AMATEUR_BANDS_HZ` has no 13 cm band; no way to say that a device supports only some modes; the GUI/CLI know no "ESP32" connection. |

## 2. Architecture: two layers, and where the code lives

```
pluto-tx flowgraph (unchanged)            espdr package (pure numpy + pyserial, no GNU Radio)
  M17 / FM / ... -> IQ at device rate ->  [adapter in pluto-tx: EspdrDevice + EspdrPolarSink]
                                              |  complex64 blocks
                                              v
                                          espdr.polar.IqToPolar   (IQ -> offsets in Hz, amplitude)
                                              |  FskModulator.process -> 32-bit records
                                              v
                                          espdr.txlink.Streamer   (push API, flow control, session)
                                              v
                                          USB -> ESP32-S3 (firmware unchanged) -> PLL word + gain at 40 kHz
```

* **Core in `espdr` (0BSD):** all signal processing and the link (`polar.py`, `Streamer` in `txlink.py`), testable without GNU Radio and with the existing chip simulator `espdr/sim.py`. `espdr-tx -m iq` (a CLI mode that reads complex IQ from a file or pipe) uses the same core and gives an early hardware test without touching `pluto-tx`.
* **Adapter in `pluto-tx` (GPL-3):** `pluto_tx/devices/espdr.py` (about 250 lines): the `TxDevice` subclass and a small `gr.sync_block` sink. It imports `espdr` as an optional dependency with a guarded import, exactly like `devices/aioc.py`. This keeps `espdr` free of GNU Radio and of the GPL, and puts the knowledge of the `TxDevice` contract where the contract lives. Because `pluto-tx` is read-only for this session, **the adapter is developed as a patch set in this repository (`integration/pluto-tx/`: adapter, registry patch, tests) and applied on the development machine** (work package 6).

## 3. `TxDevice` mapping (the contract the adapter has to fulfil)

| `TxDevice` item | ESP32-S3 value / behaviour |
|---|---|
| `device_type`, `display_name`, `connection_kind` | `"espdr"`, "ESP32-S3 (espdr)", `"serial"`; connection string `""` (find the board), or `PORT` or `PORT,BRIDGE` |
| `frequency_range_hz` | (2.32e9, 2.45e9); `config.DE_AMATEUR_BANDS_HZ` gets `("13cm", 2_320_000_000, 2_450_000_000)` |
| `sample_rate_range_hz`, default | 40 kHz .. 160 kHz, default **80 kHz** (the sink derives frequency from phase differences; 80 kHz gives headroom beyond +-20 kHz and an integer ratio 5/3 to 48 kHz; 40 000 records/s are produced by averaging pairs) |
| `default_bandwidth_hz` | `None` |
| `power_stages` | one primary stage "gain" in dB relative to the strongest code, range -17.9 .. 0 (codes 127 .. 64, 0.28 dB per code), `off_value` = weakest. "Off" is **ending the session**, not the gain: the chip radiates a carrier whenever a session runs. Default ceiling conservative (-12 dB). |
| `supports_persistent_sink` | `True` (the sink object stays; it discards samples while unkeyed) |
| `supports_frequency_correction` | `True`: `_hw_frequency()` feeds the LO plan; the ESP32's crystal is about +5 ppm off (12.5 kHz at 2.35 GHz), the thermal model of `espdr.thermal` handles the drift on top |
| `tx_end_loss_s` | `0`: `post_unkey()` drains the ring (end record, wait for the summary), nothing is swallowed |
| `prepare_for_start()` | `board.ensure(board.TX, ...)` loads the transmitter firmware into RAM if it is not running (needs the UART port too), checks that the licence notice was accepted (`espdr-tx --accept-licence`, `cli_tx._licence_accepted`), opens the session object |
| `build_sink()` | returns `EspdrPolarSink` (consumes complex64 at the device rate, no outputs) |
| `set_frequency(hz)` | the LO is a PLL word chosen so that the low byte leaves room (`txlink.choose_lo`) and cannot change while a session runs: stored and applied at the next `pre_key()`; changing it while keyed is refused with a clear message (Pluto retunes live, this chip cannot) |
| `set_power("gain", v)` | changes the gain code in the records immediately (the gain is per record) |
| `pre_key()` | `choose_lo`, `Session.configure` (rate, drift from `thermal` if a model file exists, limit), read the chip temperature first (`TX_OP_TEMP` works only between sessions), `begin()`, start feeding; the carrier appears when the ring has filled (`TX_PREFILL`, about 100 ms), preceded by 20 ms of plain carrier |
| `post_unkey()` | end record, wait for the end summary (underruns, late updates), close the session; always bounded in time |
| `force_safe_state()` | idempotent, never raises: end record, and if the link is dead `board.reset()`; the chip's own watchdog (no data for 500 ms) and length limit are the backstop |
| `read_hw_state()` | `{keyed, lo_mhz, fill, underruns, late, temperature_c (None while keyed), firmware_loaded}` |
| `probe_with_timeout`, `scan_devices_with_timeout` | `board.bridge_ports()`/`native_ports()` and `board.probe()`; label with the serial number of the bridge |

## 4. Signal path details (work package 1 and 2 decide these with tests)

* **IQ -> polar.** `f[n] = fs/(2 pi) * arg(x[n] * conj(x[n-1]))`, averaged over `fs / 40 000` samples to one frequency per record; the state (last sample) survives block boundaries. The result goes into `FskModulator.process(offsets_hz)` (static offset from `choose_lo`/ppm, clamp via `limit_q4`). Amplitude: **constant gain code from the power stage**; `|x|` is ignored in the default (constant-envelope) mode. While unkeyed the sink discards the samples.
* **Range.** M17 peaks about +-3.05 kHz plus the static shift that `choose_lo` may add (up to 22 kHz as written today); records carry about +-19 kHz. The LO choice must therefore reserve the deviation (`choose_lo(max_offset_steps=...)`); a test covers every channel centre of the band.
* **Time base.** The chip plays 40 000 records/s from its crystal; the flowgraph source is the audio clock or a file. The sink paces to the wall clock when unkeyed, and to the chip's ring fill when keyed (status frames every 5 ms), with a slip control that inserts or drops one record when the fill drifts (16 ppm drift of the clocks = one record every 1.5 s at worst). Ring target fill goes down from 8000 records to about 2000 (50 ms) to keep the latency small.
* **PTT semantics.** `key_ptt()` -> `pre_key()`; `unkey_ptt()` -> (M17: the existing EOT hold of 0.4 s in the flowgraph) -> `post_unkey()`. The extra latency of the ring (about 100 to 200 ms in total) is added to the unkey time, never cut off.
* **Frequency accuracy.** The PLL step (30 MHz / 65536 = 457.76 Hz) is exact; the crystal scale error (16 ppm) is irrelevant for the deviation. The absolute offset (+12.5 kHz) is removed with `set_frequency_correction_ppm` (Pluto side) or the receiver's DC averaging (the M17 decoder in `pluto_advanced_rx` averages over 0.1 s).

## 5. Work packages

Each package ends with a commit on `feature/m17` and its tests; estimates are for one person with the hardware at hand.

**WP0, baseline and decisions (0.5 day, development machine).** gr-m17 built (`install-m17.sh`); `pluto-tx` M17 TX -> Pluto RX -> `M17FieldsDeframer` works with a Pluto as the transmitter (the reference). Record a golden IQ file of a short M17 voice transmission with the `fake` device (as in `tests/test_meshcore_flowgraph.py`, device rate 80 kHz instead of 2.5 MHz) and the source/destination callsigns it carries. *Decision needed:* device rate 80 kHz (default proposed) or 40 kHz.

**WP1, `espdr.polar` (1 day).** `IqToPolar(rate_in, update_rate=40000, static_hz, range)`: block processing, state across blocks, decimation by averaging, wrap handling, unit tests: a tone gives a constant frequency; an FM signal gives its modulating audio back (compare with `txmodes.FmModulator`); the golden M17 IQ file gives records whose frequency track reproduces the symbols (EVM below -40 dB through the host pipeline plus a model of the firmware's quantiser, which goes into `espdr/sim.py` if it is not there yet; M17 symbols decoded without errors by a numpy 4FSK slicer).
*Files:* `src/espdr/polar.py`, `tests/test_polar.py`, `src/espdr/sim.py`.

**WP2, `Streamer` push API (1 day).** `txlink.Session.stream(records, ...)` takes an iterable and blocks; the sink needs the opposite (`push(records)` with back-pressure, `finish()`, `abort()`). Refactor `stream()` into a `Streamer` class that `stream()` wraps (CLI behaviour unchanged, tests unchanged), with the fill-level slip control and a bounded `close()`. Tests against `SimTx`: no underrun at 40 000/s for 60 s of simulated time, abort within 100 ms, watchdog end if the host stops.
*Files:* `src/espdr/txlink.py`, `tests/test_modes.py`/new `tests/test_streamer.py`.

**WP3, `espdr-tx -m iq` (0.5 day), the first hardware test.** A CLI mode that reads complex64 IQ (file or pipe, `--iq-rate`) through `IqToPolar` and `Streamer`, using the existing plan/LO/thermal code of `cli_tx` (like `-m rtty`). `scripts/m17_iq_make.py` creates the IQ file with the `pluto-tx` modules (fake device, file source: a WAV with speech or a test tone, callsign argument), so **M17 on air needs nothing from `pluto-tx` beyond importing it**. Docs: README and `docs/transmitter.md` get an "IQ input" section.
*Hardware test H1 here:* the golden IQ file on air, PlutoSDR + `pluto-tx` M17 receive chain: LSF callsigns decoded, voice frames decoded.

**WP4, the `pluto-tx` adapter as a patch set (2 days).** `integration/pluto-tx/devices/espdr.py` (`EspdrDevice`, `EspdrPolarSink` per section 3 and 4), `integration/pluto-tx/patches/*.patch` (registry entry with guarded import; 13 cm band; section 6 items), `integration/pluto-tx/tests/` (a `FakeEspLink` in the style of `tests/fakes.py`: the sink writes to a `SimTx`). Tests without hardware: device contract (every abstract method, `force_safe_state` never raises, `probe` and `scan` with no board), pre_key/post_unkey sequence, power stage clamp, frequency refusal while keyed, and **a loop test**: `PlutoTxFlowgraph(device_type="espdr", ...)` with `SimTx` -> records -> reconstructed FM IQ -> `m17_decoder` -> callsigns equal (skipped when gr-m17 is missing).

**WP5, integration into the development copy of `pluto-tx` (1 day, on the machine with the board).** Apply the patch set; run `pluto-tx` with `--device espdr` (CLI) and the GUI device list; the mode matrix (section 6) is generated by running every mode against the simulator link and then on the air. *Hardware tests H2 to H5.*

**WP6, hardware test programme (2 days).** With a PlutoSDR as receiver, 50 cm or cabled with attenuation, transmit permission for 13 cm:

| Test | Measure | Target (to be confirmed after H3) |
|---|---|---|
| H1 golden file, 10 s voice | frames decoded, callsigns, codec2 audio vs the reference run | all LSFs, at least 99 % of voice frames at 50 cm |
| H2 level sweep (attenuation steps) | frame loss against received level; compare to the Pluto as transmitter at the same level | within a few dB of the Pluto's curve |
| H3 spectrum | occupied bandwidth (99 %), lines at +-40 kHz and multiples (the update rate), second and third harmonic | the 40 kHz lines are the open question: `docs/transmitter.md` says spurious emissions were not measured; set the limit after the measurement, target at most -40 dBc, better -50 |
| H4 deviation accuracy | the four symbol frequencies (+-800, +-2400 Hz) from a long recording | within 3 % |
| H5 long run, 120 s and 600 s | frame loss over time, drift (thermal model on and off), ring underruns | no underrun; drift below the M17 receiver's tolerance |
| H6 PTT cycling, 50 cycles | session start/stop robust, time from key to carrier, from unkey to silence, no stuck carrier after abort | carrier gone within 300 ms of unkey |
| H7 live microphone through the GUI | latency key-to-air, audio quality, long over (clock drift between sound card and chip) | no overrun in 10 minutes |
| H8 (optional) | the ESP32's own receiver (`espdr-rx`, 250 ksps) decoding M17 | informational |

## 6. Patch list for `pluto-tx` (applied locally, section 2)

1. `devices/__init__.py`: guarded import of `EspdrDevice`, `DEVICE_REGISTRY["espdr"]`, flag `ESPDR_AVAILABLE` (as AIOC).
2. `config.py`: add `("13cm", 2_320_000_000, 2_450_000_000)` to `DE_AMATEUR_BANDS_HZ` (affects the "outside amateur band" warnings only).
3. **Mode gating:** a class attribute on `TxDevice`, `supported_modes = None` (all) and for the ESP32 a set of the constant-envelope modes; used by the CLI (`pluto_cli/tx.py`: refuse with a message) and the GUI (`_sync_mode_combo_availability`, grey out with a tooltip). To be filled from the mode matrix: expected **yes** = FM, M17, POCSAG, RTTY if it is FSK in `pluto-tx` (check `rtty.py`), Baseband FSK; expected **no** (non-constant envelope) = SSB/LSB, FreeDV, RADE, PSK31, Digitext, FT8, JS8, File Broadcast. LoRa/Meshtastic/MeshCore presets are at 868/433 MHz, outside the frequency range, and stay unavailable.
4. `pluto_cli`/GUI: the "serial" connection widget with an auto-find button for the ESP32; `scan_devices_with_timeout`.
5. TX waterfall: it is computed from the device rate (`WATERFALL_ZOOM_BANDWIDTH_HZ` against `quad_rate`); check it at 80 kHz and disable it for the ESP32 if it breaks.
6. `install-espdr.sh` (or a line in `install.sh`): `pip install` of the `espdr` package (or `PYTHONPATH`), `udev` rules for the board (`udev/70-espdr.rules` exists), a note about the licence acceptance.
7. A rate audit: build the flowgraph with `FakeEspTx` at 80 kHz for every mode and look for assertions and resampler ratios (the resamplers are built relative to `quad_rate` all over `flowgraph.py`).

## 7. Risks and open questions

* **Spurious emissions of the 40 kHz staircase** (lines at +-40 kHz): unmeasured, decides whether the mode may run unattended; H3 before any long transmission.
* **Latency and PTT timing:** the ring adds 100 to 200 ms before the carrier and after the unkey; the flowgraph's PTT logic assumes an instant device (Pluto). Check `finish_unkey_m17` and the one-shot digimode timers (`*_TAIL_S`, auto-unkey watchdogs) against it.
* **Clock drift in live use** (sound card vs chip): the slip control must be tested for 10 minutes and more (H7).
* **Frequency changes while keyed** are not possible; the GUI offers a live frequency control.
* **Licence of the adapter:** it lives in `pluto-tx` (GPL-3); `espdr` stays 0BSD and does not import GNU Radio. Check before copying code in the other direction.
* **gr-m17 availability on the CI/cloud side:** the loop tests are skipped there; they run on the development machine.
* **Open decision:** device rate 80 kHz (more headroom, 5/3 to 48 kHz) or 40 kHz (no averaging, ratio 5/6). Proposed: 80 kHz.
* **Later, not now:** SSB-type modes through polar SSB (`txmodes.SsbModulator` needs the analytic signal plus a carrier share, not just `arg()`), an amplitude path from `|x|`, M17 reception on the ESP32 receiver, LoRa presets at 2.4 GHz as custom presets (the research branch has a numpy PHY).

## 8. Legal and safety

* Amateur radio licence required; 13 cm only; the callsign is in the M17 LSF (source field), **no encryption** (the `m17_coder` parameters for scrambler/AES stay empty as in `pluto-tx`).
* Tests cabled with attenuation or at 50 cm with the Pluto's gain low; the chip's output is microwatts, which does not change who is responsible.
* The firmware limits stay (2320..2450 MHz, session length `TX_OP_LIMIT`, watchdog 500 ms). The adapter adds: no key without an accepted licence notice, E-STOP ends the session in under 100 ms (abort path of the `Streamer`), `force_safe_state` on every exit path.

## 9. Branch layout and definition of done

Commits: WP1, WP2, WP3 (each with tests and docs) -> hardware test H1 -> WP4 -> WP5/WP6. `feature/m17` merges into `main` after H1, H3, H5 and H6 pass and `pytest` and the docs tests are green; the adapter patch set is submitted to `pluto-tx` on the development machine.
Done when: (1) `espdr-tx -m iq` plays an M17 IQ file and a PlutoSDR decodes it; (2) `pluto-tx --device espdr` in M17 mode transmits a voice file and live microphone audio, decoded by the M17 receiver of `pluto-tx`; (3) FM works through the same device; (4) the mode matrix is documented and the unsupported modes are greyed out with a reason; (5) the spectrum (H3) and the PTT behaviour (H6) are written down in `docs/transmitter.md`.
