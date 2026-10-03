**esp32-sdr-trx** turns one ESP32-S3 dev board into a software-defined transceiver for 13 cm: an `rtl_tcp` receiver for SDR++ (1.84 to 2.79 GHz, 250 or 333 ksps, no FPGA) and an
experimental FM/SSB voice transmitter (2320 to 2450 MHz). Built on [eSpDR](https://github.com/h0m3us3r/eSpDR) by h0m3us3r.

## Install (Linux, Python 3.10+)

Download the wheel and `install-linux.sh` below, then:

```sh
chmod +x install-linux.sh
./install-linux.sh esp32_sdr_trx-*.whl --audio       # --audio: a sound card as a transmit source
espdr-rx                                              # receiver: rtl_tcp server on 127.0.0.1:1234 for SDR++
espdr-tx --accept-licence -f 2350 -m usb -i soundcard # transmitter: read docs/transmitter.md first
```

The wheel contains the firmware images and the tools load the right one into the board's RAM by themselves. Step by step, including the virtual environment and the
udev rule: [docs/install.md](https://github.com/jochenhammes/esp32-sdr-trx/blob/main/docs/install.md). `esp32-sdr-trx-rx.bin` and `esp32-sdr-trx-tx.bin` are the bare images,
`SHA256SUMS` lists everything.

## Tested

One board (a generic ESP32-S3-WROOM-1 dev board with two USB-C ports), one Linux host, a PlutoSDR and a HackRF as test equipment. Receiver: 20 to 120 s runs without lost samples at
250 and 333 ksps, SDR++ over the bridge. Transmitter: FM and SSB voice from a WAV file, a pipe and a sound card (20 to 30 s without an underrun), five frequencies over the band,
the carrier stops 0.54 s after the host disappears, crystal correction with `--ppm`. Measured with the carrier suppressed: unwanted sideband 61 to 63 dB and third-order
intermodulation 33 dB below the wanted tones. All numbers: `docs/research/TX-RESEARCH.md`.

## Read before you transmit

Transmitting takes an **amateur radio licence**. The firmware only sends in 2320 to 2450 MHz. The power is **not calibrated** (an estimate: microwatts at the antenna of a plain dev board), harmonics and
spurious emissions were **not measured**, and the frequency is only as good as the board's crystal (use `--ppm`). Windows and macOS are untested. Much of the code and the documentation was written
with Claude (Anthropic) as the coding assistant; every number in the documentation was measured.
