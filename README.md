# esp32-sdr-trx: a software-defined transceiver from one ESP32-S3 board

![SDR++ receiving the ESP32-S3's own single-sideband voice transmission, recorded with a HackRF](docs/images/esp32-SSB-transmit.png)

*SDR++ with a HackRF, USB demodulator, tuned to an ESP32-S3 that transmits single-sideband voice on 13 cm. The ESP32-S3 is the only
transmitter.*

Every ESP32-S3 has a 2.4 GHz Wi-Fi radio, and somewhere inside it is an I/Q stream and a test-tone generator that Espressif does not
document. This project uses both, on a plain dev board with a USB cable and no FPGA:

* **Receive** (`espdr-rx`): 1.84 to 2.79 GHz, 250 or 333 ksps of 8-bit I/Q. The chip decimates its 16 Msps ADC stream itself; a bridge makes it an
  `rtl_tcp` server, so **SDR++**, GNU Radio and anything else that speaks `rtl_tcp` can use it.
* **Transmit** (`espdr-tx`): narrowband **FM** and **SSB (USB/LSB)** voice in the 13 cm amateur band (2320 to 2450 MHz), from a WAV file, a pipe or
  a sound card. It is an *experiment* that works: the chip's carrier is steered in frequency and amplitude by software (polar modulation),
  because it has no I/Q input. Transmitting takes an amateur radio licence; read [the transmitter guide](docs/transmitter.md) first.

| | Receiver | Transmitter |
|---|---|---|
| Command | `espdr-rx` | `espdr-tx` |
| Range | 1.84 to 2.79 GHz | 2.32 to 2.45 GHz |
| Bandwidth | 250 ksps (±100 kHz) or 333 ksps (±133 kHz) | voice, 300 to 3000 Hz |
| Modes | rtl_tcp server | FM (±2.5 kHz), USB, LSB |
| Sources | the Wi-Fi front end | WAV file, `-` (pipe), sound card |
| Status | stable, one board tested | experimental, one board tested |

## Install

You need an ESP32-S3 board **with two USB ports** (the second one is a USB-UART bridge that lets the tools load the firmware without
buttons), a USB cable for each, Linux, and Python 3.9 or newer.

```sh
pipx install https://github.com/jochenhammes/esp32-sdr-trx/releases/latest/download/esp32_sdr_trx-X.Y.Z-py3-none-any.whl
# for the sound card as a transmit source:
pipx inject esp32-sdr-trx sounddevice
```

(Take the wheel's exact name from the [latest release](https://github.com/jochenhammes/esp32-sdr-trx/releases/latest). `pip install` into a virtual
environment works as well.) The package carries the firmware images; there is nothing to build.

Linux: install the udev rule once so that ModemManager leaves the board alone and you may use it without `dialout`:
`sudo cp udev/70-espdr.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger`.

## Use

```sh
espdr-rx                                      # loads the receiver firmware if needed, starts the rtl_tcp server on 127.0.0.1:1234
                                              # in SDR++: source RTL-TCP, host 127.0.0.1, port 1234, play

espdr-tx --accept-licence -f 2350 -i speech.wav                  # FM voice from a WAV file (the first time only: --accept-licence)
espdr-tx -f 2350 -m usb -i soundcard --power -6                  # SSB from the sound card, 6 dB below the strongest setting
arecord -f S16_LE -r 16000 -c 1 | espdr-tx -f 2350 -m usb -i - --rate 16000   # raw samples from a pipe
```

Both tools load the right firmware into the board's RAM by themselves and switch it when needed (a few seconds); nothing is written to the
flash unless you ask (`espdr-rx --flash`). `-h` explains every option and shows examples; the full reference is in
[docs/espdr-rx.md](docs/espdr-rx.md) and [docs/espdr-tx.md](docs/espdr-tx.md).

## Documentation

| | |
|---|---|
| [Receiver guide](docs/receiver.md) | requirements, first run, SDR++, GNU Radio, gain, frequency accuracy, limits, troubleshooting |
| [Transmitter guide](docs/transmitter.md) | **licence and safety**, modes, power, tuning your receiver, sources, measured performance, limits |
| [espdr-rx](docs/espdr-rx.md), [espdr-tx](docs/espdr-tx.md) | command line reference (also `-h`) |
| [How it works](docs/internals.md) | the time budget, the decimator, the USB stream, the transmit protocol and polar modulation |
| [Research notes](docs/research/TX-RESEARCH.md) | how the transmitter was found out, with every measurement |
| [Releasing](docs/releasing.md) | for maintainers |

## Honest limits

* Tested on **one** board (a generic ESP32-S3-WROOM-1 dev board with two USB-C ports), one Linux host, a PlutoSDR and a HackRF as test equipment.
  Windows and macOS are untested. Reports from other boards are welcome (issue template *Board / hardware report*).
* The transmitter's power is **not calibrated** (an estimate: some microwatts at the antenna of a dev board), its harmonics and spurious
  emissions are **not measured**, and its frequency is only as good as the board's crystal (up to ±24 kHz; correct it with `--ppm`).
* Receiving: no calibrated levels, a spur at 2400.000 and 2440.000 MHz (the crystal's 60th and 61st harmonic), Wi-Fi-class sensitivity.

## Credits and licence

Built on [eSpDR](https://github.com/h0m3us3r/eSpDR) by h0m3us3r, who found how to read the ESP32-S3's undocumented radio dump path and wrote the
firmware around it; see [NOTICE](NOTICE). This project's additions (the on-chip decimator, USB stream, flash boot, the `rtl_tcp` bridge, the
transmitter, the tools and the documentation) were developed by Jochen Hammes with Claude (Anthropic) as the coding assistant. Every number in the
documentation was measured. Zero-Clause BSD licence ([LICENSE](LICENSE)). Not affiliated with or endorsed by Espressif.
