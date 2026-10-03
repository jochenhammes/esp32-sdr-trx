# espdr-rx

Receiver: loads the receiver firmware if needed and runs the rtl_tcp server. Guide: [receiver.md](receiver.md).

This page is generated from `espdr-rx -h` (`scripts/gen-reference.py`); the text below is exactly what the command prints.

```text
usage: espdr-rx [-h] [-V] [--listen HOST:PORT] [--decim {3,4}] [--ppm PPM] [--gain N]
                [--gain-min N] [--gain-max N] [--no-dc-block] [--port DEVICE]
                [--bridge-port DEVICE] [--no-load] [--reload] [--native] [--image FILE] [--flash]
                [--yes] [-v] [--selftest]

espdr-rx: use an ESP32-S3 board as a receiver for SDR++ and other rtl_tcp programs (1.84 .. 2.79 GHz, 250 or 333 ksps).

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  -v, --verbose         more detail
  --selftest            check the server against a simulated chip, no hardware, and exit

rtl_tcp server:
  --listen HOST:PORT    address of the rtl_tcp server (default 127.0.0.1:1234)
  --decim {3,4}         4: 250 ksps from the radio, +-100 kHz usable (default); 3: 333 ksps, +-133
                        kHz usable
  --ppm PPM             this board's crystal error, positive if it runs fast (a client's ppm
                        setting adds to it)
  --gain N              gain selector 0..127 until the client sets one (default 60)
  --gain-min N          selector for a client gain of 0 dB (default 30)
  --gain-max N          selector for a client gain of 49.6 dB (default 80)
  --no-dc-block         keep the radio's DC offset

board:
  --port DEVICE         the board's native USB port (default: find it)
  --bridge-port DEVICE  the board's USB-UART port, used to load the firmware (default: find it)
  --no-load             do not load firmware: fail if the receiver is not running
  --reload              load the receiver firmware even if it is running
  --native              load through the native port (board without a UART port: hold BOOT, tap
                        RESET)
  --image FILE          use this receiver image instead of the one of the package
  --flash               write the receiver to the board's flash (overwrites it), then serve
  --yes                 with --flash: do not ask for confirmation

examples:
  espdr-rx                                   load the receiver firmware if needed and start the rtl_tcp server on 127.0.0.1:1234
  espdr-rx --decim 3                         333 ksps (+-133 kHz usable) instead of 250 ksps (+-100 kHz)
  espdr-rx --listen 0.0.0.0:1234 --ppm -5.7  reachable from other computers; correct the board's crystal by -5.7 ppm
  espdr-rx --no-load                         do not touch the firmware: fail if the receiver is not running
  espdr-rx --flash                           write the receiver into the board's flash: it then starts by itself at power-up

In SDR++ choose the source "RTL-TCP", host 127.0.0.1, port 1234 and press play. The receiver firmware is loaded into the board's
RAM when it is not running (this needs the board's UART USB port as well as the native one; --no-load forbids it). Whatever runs
on the board, a reset brings back what its flash holds.
```
