# The transmitter: FM and SSB voice from an ESP32-S3

`espdr-tx` sends narrowband FM or single-sideband voice from an ESP32-S3 board on the 13 cm band (2320 to 2450 MHz). The chip has no I/Q
input; what it has is a Wi-Fi test tone that can be steered, and that is enough. This page is the user guide. The command line reference
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
* It is experimental and was tested on one board. Voice only: no data modes, no receive while transmitting.

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

Options: `--deviation HZ` (FM), `--no-preemph`, `--carrier FRACTION` (SSB: the carrier's share of the peak envelope; `0` suppresses it fully,
`0.5` or more makes a signal that an AM receiver also copies), `--gain DB` and `--no-agc` for the audio level. The audio is band-limited, passes
a speech AGC and is clipped at full scale, so loud input cannot overdrive the modulator.

Measured with a two-tone test (700 and 1700 Hz) and the carrier reduced step by step: the unwanted sideband stays 29 to 63 dB below the
wanted tones and third-order intermodulation 33 to 54 dB below them, from a 55 % carrier down to a completely suppressed one. That is the quality
of a plain amateur SSB transceiver. Speech was understood on a second computer with a HackRF and SDR++ in all of the modes above.

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
| Carrier | stable to ±12 Hz over 5 s and ±15 Hz over 20 s (the first seconds are cancelled in the firmware: the board's frequency falls by 194 Hz in the first 5 s as it warms up) |
| Data stream | 40 000 records per second, buffer steady at 190 ms for a file, 120 to 140 ms from a sound card; no underrun or late update in the tests (5 s to 30 s, file, pipes, sound card) |
| Frequencies | 2320.0, 2350.0, 2385.0, 2400.1234, 2449.9 MHz all came out where the tool said (plus the same +12.4 to +13.2 kHz of crystal error); near a PLL byte boundary the tool moves the carrier by up to 22 kHz and says so |
| Stop | the tool killed mid-transmission: carrier off 0.54 s later (buffer 190 ms plus the 500 ms watchdog) |
| FM voice | a 5.3 s recording sent, deviation 2073 Hz at the 99th percentile (2087 Hz in the input), understood |
| SSB voice | 20 s looped, understood; two-tone figures above |

## Limits

* One board, one host. Frequencies 2320 to 2450 MHz only. Voice only.
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
