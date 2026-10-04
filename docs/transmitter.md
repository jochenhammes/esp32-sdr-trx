# The transmitter: FM and SSB voice and RTTY from an ESP32-S3

`espdr-tx` sends narrowband FM or single-sideband voice, or RTTY text, from an ESP32-S3 board on the 13 cm band (2320 to 2450 MHz). It uses the chip's Wi-Fi test tone, which can be steered in frequency and amplitude (polar modulation), and that is enough for voice and for RTTY.
The chip also has an I/Q playback engine (see the research notes) that this transmitter does not use. This page is the user guide. The command line reference
is [espdr-tx.md](espdr-tx.md) (and `espdr-tx -h`), how it works is in [internals.md](internals.md), and every measurement is in
[the research notes](research/TX-RESEARCH.md).

## Read this first

* **You need an amateur radio licence** that allows you to transmit on the frequency you choose, with the mode and the identification your
  licence conditions require. The tool asks you to confirm this once (`--accept-licence`) and repeats the frequency, mode and power of every
  transmission in its first line. Identify yourself as you would with any transmitter; the tool does not.
* **The signal is not that of a transceiver.** The power is not calibrated: it is some microwatts at the antenna of a plain dev board (an
  estimate from a PlutoSDR 50 cm away; the uncertainty is ±10 dB). Harmonics and spurious emissions were **not measured**. The board's
  antenna is made for Wi-Fi. Do not connect an amplifier or an antenna of gain without measuring what comes out of it.
* **The firmware only sends inside 2320 to 2450 MHz** and ends every transmission itself if the host stops sending for half a second
  (tested by killing the tool in the middle of a transmission: the carrier was gone 0.54 s later).
* It is experimental and was tested on one board. Voice and RTTY only: no other data modes, no receive while transmitting.

## What you need

* An ESP32-S3 board with two USB ports and a cable for each ([hardware notes](receiver.md#requirements)). Both tools use the second port (the
  USB-UART bridge) to load the firmware; the board's own USB port carries the audio data.
* `esp32-sdr-trx` installed ([README](../README.md#install)). For a sound card as a source also `sounddevice` and, on Linux, PortAudio
  (`sudo apt install libportaudio2`).
* A receiver to listen with (SDR++ with an SDR, or any 13 cm receiver), and for your first tests a dummy load or a distance of some metres.

## First transmission

```sh
espdr-tx --accept-licence -f 2350 -m fm -i speech.wav
```

The first time the tool loads the transmitter firmware into the board's RAM (about 8 seconds, the transmitter image is replaced by the receiver
the next time you run `espdr-rx`), prints what it will do, and sends. Press Ctrl-C to stop; the ring buffer of about 200 ms is played out first.
`--dry-run` shows the settings and touches nothing; `--test-tone 1000 --duration 5` sends a tone instead of speech.

## Tuning your receiver

The board's 40 MHz crystal is typically off by ±10 ppm, which is ±24 kHz at 2.4 GHz. On the tested board the carrier came out **12.6 kHz (+5.4 ppm)
above** the frequency asked for. For FM that is a small offset you can tune out. For **SSB it matters**: the audio is only as good as the carrier
frequency you tune to. Measure the error once against a known signal (a receiver with a stable reference, or a second SDR) and correct it:

```sh
espdr-tx -f 2350 -m usb -i speech.wav --ppm 5.4      # positive if the board transmits too high
```

With `--ppm 5.4` the carrier landed 160 Hz from the frequency asked for. The receiver's own error adds to the picture; calibrate against a
source you trust. For SSB tune the USB demodulator's dial to the **carrier** frequency (a faint pilot, 5 % by default, helps to find it): the
voice sits 300 to 2700 Hz above it (LSB: below).

## Modes

| Mode | What it is | When |
|---|---|---|
| `-m fm` | narrowband FM, ±2.5 kHz at full-scale audio, with the usual voice pre-emphasis (+6 dB per octave from 300 to 3000 Hz) | to be heard by any NBFM receiver; most forgiving |
| `-m usb` | upper sideband, polar modulation, 5 % pilot carrier by default | weak signal work above 10 MHz is USB by convention |
| `-m lsb` | lower sideband | |
| `-m rtty` | text as two-tone FSK (Baudot, 45.45 baud, 170 Hz shift by default): true FSK, the amplitude stays constant | RTTY; the tones are those of the RTTY mode of pluto-tx |

Options: `--deviation HZ` (FM), `--no-preemph`, `--carrier FRACTION` (SSB: the carrier's share of the peak envelope; `0` suppresses it fully,
`0.5` or more makes a signal that an AM receiver also copies), `--gain DB` and `--no-agc` for the audio level. The audio is band-limited, passes
a speech AGC and is clipped at full scale, so loud input cannot overdrive the modulator.

Measured with a two-tone test (700 and 1700 Hz) and the carrier reduced step by step: the unwanted sideband stays 29 to 63 dB below the
wanted tones and third-order intermodulation 33 to 54 dB below them, from a 55 % carrier down to a completely suppressed one. That is the quality
of a plain amateur SSB transceiver. Speech was understood on a second computer with a HackRF and SDR++ in all of the modes above.

## RTTY

```sh
espdr-tx -f 2350 -m rtty --text "RYRY CQ CQ DE <your call sign> K"
espdr-tx -f 2350 -m rtty --text-file message.txt --baud-rate 50 --repeat-count 3 --repeat-interval 30
```

`-f` is the **dial frequency of an upper-sideband transmitter**, as with the RTTY mode of pluto-tx: the mark tone sits `--mark-hz` (2125 Hz) above it and
the space tone `--shift-hz` (170 Hz) above the mark. The same `-f` therefore gives the same signal on the air, and an RTTY receiver in upper sideband, or the RTTY decoder of
pluto-tx, copies it on the same dial setting. `--reverse` swaps which tone is mark. The first line of the output prints the two tone frequencies, which are what to tune to if the
receiver has no dial in this sense. The tones come out within about 15 Hz of the frequencies asked for (the records count in steps of 28.6 Hz); `--ppm` matters as for the other modes.
On the few percent of frequencies where the PLL word is far from the one asked for, the move of the LO is larger than the records can make up for, and the tool warns and
says how far off the tones are; change `-f` by a few kHz then.

The transmitter is not keyed off between the characters: it sends the mark tone for a second before the text and 0.2 s after it, and the carrier is switched off at the end. The text goes out
as US Baudot with letters and figures shifts (a space returns to letters), 1 start bit, 5 data bits, 1.5 stop bits; letters are sent as capitals and what the table lacks as `?`.
In `--text` the typed characters `\r` and `\n` are sent as carriage return and line feed; `--text-file` (`-` for stdin) turns line ends into CR LF. The steps between the tones are
rounded over 0.2 bit (`--edge`; `0` makes abrupt steps; the measurements below show no difference in the spectrum). Start the text with a few `RY` so that a decoder with automatic tuning has the two tones to lock on: the first seconds of a transmission can be lost. **The tool adds no call sign or identification: put yours in the text.** The audio options, `--deviation`,
`--carrier` and `--duration` do not apply to RTTY.

## Thermal drift (`--thermal`)

The board warms up while it transmits: the chip's own temperature sensor goes from 42 C (idle) to 57 C within 150 s, and the frequency of the crystal changes with it
by more than a RTTY decoder can follow (half the shift, 85 Hz) and more than an SSB receiver tolerates. Measured with a PlutoSDR at 2350 MHz, the carrier moved
**+230 Hz in 15 s, +730 to +860 Hz in 45 s, +1800 to +2050 Hz in 120 s and +2080 Hz in 150 s** when the transmission started at 47 to 50 C; by up to 20 to 38 Hz per second.

```sh
espdr-tx -f 2350 -m usb -i speech.wav --ppm 5.4 --thermal sensor
espdr-tx -f 2350 -m rtty --text "RYRY CQ CQ DE <your call sign> K" --thermal sensor
```

`--thermal sensor` reads the chip's temperature before the transmission (`TX_OP_TEMP`), predicts the frequency error from that and the time since the start with a model of the
crystal (a parabola around its turning point at 47 C, the chip heating towards 58 C with a time constant of 137 s, and a slower rise of about 1100 Hz that does not follow
the sensor) and adds the opposite to the frequency field of every record. It works for all modes. `--thermal nominal` does the same without reading the sensor and assumes
the idle temperature of 42 C (for a transmission after the board has rested for a few minutes; an older firmware image without the sensor falls back to it with a warning).
`--thermal-model FILE` takes the numbers of another board (a JSON file with the names of `espdr.thermal.DEFAULT`; `scripts/drift/` makes one, see below).

| Transmission (start temperature) | change of the frequency over the transmission without / with `--thermal sensor` |
|---|---|
| SSB, 15 s (47 C) | +229 / +197 Hz |
| SSB, 45 s (46.6 / 48.3 C) | +729 / +188 Hz |
| RTTY, 45 s (49 / 50 C) | +862 / +252 Hz |
| SSB, 120 s (50 C) | +1812 / +1041 Hz (first model) |
| RTTY, 120 s (53 C) | +2048 / +1317 Hz (first model) |
| SSB, 150 s (47 C) | +2077 / **+162 Hz** |
| SSB, 120 s (51 C) | / **+191 Hz** |
| RTTY, 120 s (47 C) / 90 s (44 C) / 60 s (52 C) | / **+202 Hz** / **+172 Hz** / **+165 Hz** |
| SSB, 30 s (57 C) | / **-40 Hz** |

The rows in bold are with the model that is built in now or the one before its last refit (the same numbers within a few percent), on transmissions that were not used to fit it.

The first seconds are the weak point: after the correction a rise of 50 to 100 Hz in the first 10 s remains (5 to 10 Hz/s, from 20 to 30 Hz/s), and over the whole transmission the frequency stays within about 250 Hz of its value at the start. The tables are for one board
(the tested one) and one PlutoSDR as referee: the Pluto's own drift is in all of the numbers. The frequency to which the corrected carrier holds depends on the start temperature by
about +-200 Hz, and sits 0.3 to 0.5 ppm lower than the setting `--ppm 5.4` gave on the cold board: set `--ppm` again with `--thermal` in use. Transmissions longer than 150 s were not measured (the model
flattens out). The model must be refitted for another board: `scripts/drift/ab_series.py` transmits for about half an hour and records the carrier with a PlutoSDR,
`scripts/drift/ab_analyse.py` tabulates it, and `scripts/drift/fit_thermal.py OUT.json recording.json[@MODEL_USED.json] ...` fits the numbers for `--thermal-model`.
The research notes (`docs/research/FREQUENCY-DRIFT.md` on the research branch) hold the raw series.

## Power

`--power DB` is relative to the strongest setting: `0` (default) to `-17.9`. The amplitude control of the chip covers 18 dB in steps of 0.28 dB, and
SSB uses that range for its envelope. A weaker setting moves the whole range, so **it leaves SSB less amplitude range** (at `-12 dB`
only 6 dB remain) and costs quality; the tool tells you. FM has constant amplitude and loses nothing. Lower the output with an attenuator, not
with `--power`, if you need much less. The level varies by a few dB from run to run and drifts by about 3 dB in 20 s as the transmitter warms up
(carrier and voice scale together, which does not hurt SSB).

## Audio sources

| Source | Command |
|---|---|
| WAV file | `-i speech.wav` (16-bit PCM or 32-bit float; any rate, mono or stereo; `--loop` repeats it) |
| WAV stream on a pipe | `ffmpeg -i music.mp3 -f wav - \| espdr-tx -f 2350 -i -` |
| raw samples on a pipe | `arecord -f S16_LE -r 16000 -c 1 \| espdr-tx -f 2350 -i - --rate 16000` (`--channels`, `--format s16le\|f32le\|u8`) |
| sound card | `-i soundcard` (default input) or `-i soundcard:3` / `-i soundcard:name`; `--list-devices` shows the inputs |
| test tone | `--test-tone 1000` |

While transmitting, a progress line shows the time on air, the **audio input level**, the buffer in the chip (it should stay near 200 ms for a
file and 100 to 150 ms live), and counters for underruns and late updates. A warning appears if almost no audio arrives, which usually means a
muted microphone or the wrong input. A sound card's clock differs slightly from the chip's; the tool follows it by resampling a few ppm.

## Measured

| | |
|---|---|
| Carrier | stable to ±12 Hz over 5 s and ±15 Hz over 20 s (the first seconds are cancelled in the firmware: the board's frequency falls by 194 Hz in the first 5 s as it warms up); over minutes it drifts by up to 2 kHz, see "Thermal drift" above |
| Data stream | 40 000 records per second, buffer steady at 190 ms for a file, 120 to 140 ms from a sound card; no underrun or late update in the tests (5 s to 30 s, file, pipes, sound card) |
| Frequencies | 2320.0, 2350.0, 2385.0, 2400.1234, 2449.9 MHz all came out where the tool said (plus the same +12.4 to +13.2 kHz of crystal error); near a PLL byte boundary the tool moves the carrier by up to 22 kHz and says so |
| Stop | the tool killed mid-transmission: carrier off 0.54 s later (buffer 190 ms plus the 500 ms watchdog) |
| FM voice | a 5.3 s recording sent, deviation 2073 Hz at the 99th percentile (2087 Hz in the input), understood |
| SSB voice | 20 s looped, understood; two-tone figures above |
| RTTY spectrum | 45.45 baud, 170 Hz shift, PlutoSDR 50 cm away: 99 % of the power within ±234 Hz of the strongest tone, the line 67 dB above the receiver noise. The quantisation of the PLL steps leaves a pedestal around the tones: -69 dBc/Hz 1 to 2 kHz from the line, -74 at 2 to 5, -79 at 5 to 10, -83 at 10 to 20, -91 at 20 to 50, -93 at 50 to 100 kHz (all 1 to 100 kHz together: -29 dB against the tones; -22 dB at 100 baud with 850 Hz shift). `--edge 0` and `--edge 0.2` gave the same spectrum |
| RTTY decoded | Our own decoder read the PlutoSDR recordings without an error: 45.45 baud / 170 Hz, 50 baud / 170 Hz reversed, 75 baud / 425 Hz, 100 baud / 850 Hz, with letters, figures and punctuation. The RTTY receiver of pluto-tx (`pluto-cli rx ssb --digimode rtty`, live, PlutoSDR) decoded the transmissions too: the whole text at 75 baud / 425 Hz, and the text after the first two to three seconds at 45.45 baud / 170 Hz, also reversed (`--reverse` here, `--rtty-reverse` there). Its AFC needs those seconds to lock; the recordings fed through its receive chain with the filter centred on the signal decoded without errors, and the chain fails when the centre is 80 Hz off (half the shift) |
| RTTY measuring | the tools that made these measurements are in the repository: `scripts/rtty_pluto_check.py` records a transmission with a PlutoSDR and reports the tones (the distance of the two was 170.4 Hz for 171.6 Hz in the records, the frequency noise inside the tones 56 Hz rms), the decoded text and the spectrum; it can also run the recording through the receive chain of pluto-tx. It transmits |
| RTTY frequency | after `--ppm` was set the tones were within a few tens of Hz of the wanted ones; the board's frequency kept sinking by 30 to 40 Hz per minute during the first ten minutes of repeated transmissions (it warms up), so set `--ppm` after a warm-up |

## Limits

* One board, one host. Frequencies 2320 to 2450 MHz only. Voice and RTTY only.
* Not calibrated, harmonics not measured, no output filter: do not use it near sensitive services, and keep it short and low.
* The PLL steps are 458 Hz, so FM and SSB are quantised in frequency; error feedback pushes the error out of the voice band (SINAD about 19 to 31 dB
  in the voice band, which is why it sounds like a plain NBFM or SSB radio and not like a broadcast).
* The transmitter image and the receiver image are separate; the tools switch them in a few seconds.
* With a very weak `--power` SSB loses amplitude range; with `--carrier 0` the envelope passes through zero and the 18 dB range cannot follow it
  exactly (the intermodulation figures above include that).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| *no USB-UART bridge found, so the firmware cannot be loaded* | Only the native port is connected. Plug in the second USB port as well, or load by hand (hold BOOT, tap RESET) with `--native`. |
| *several USB-UART bridges found* | Name the board's with `--bridge-port /dev/ttyUSB0`. |
| SSB sounds like noise | The USB dial is not on the carrier (see *Tuning*), or the demodulator is LSB, or the audio level is too low: watch the input level in the progress line. |
| *almost no audio arrives* | Wrong or muted input. `espdr-tx --list-devices`, then `-i soundcard:NUMBER`. A laptop microphone is quiet (the progress line showed -36 to -58 dBFS while speaking); the speech AGC raises that, `--gain 10` raises it further. |
| *the chip does not take data any more* | The chip ended the transmission (watchdog, limit) or the cable was disturbed. Run again. |
| *the PLL did not lock at this frequency* | Rare inside 2320 to 2450 MHz on the tested board. Try 1 kHz away, or replug the board. |
| underruns | The computer could not keep up (a slow sound card driver or a busy USB hub). Use `-q` and a direct USB port. |
