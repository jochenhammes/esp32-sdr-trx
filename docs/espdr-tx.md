# espdr-tx

Transmitter: FM and SSB voice on 13 cm from a WAV file, a pipe or a sound card. **Read the [transmitter guide](transmitter.md) first (licence, safety, tuning).**

This page is generated from `espdr-tx -h` (`scripts/gen-reference.py`); the text below is exactly what the command prints.

```text
usage: espdr-tx [-h] [-V] [-f MHZ] [-m {fm,usb,lsb}] [-i SOURCE] [--test-tone HZ] [--list-devices]
                [--rate HZ] [--channels N] [--format {f32le,s16le,u8}] [--loop] [--gain DB]
                [--no-agc] [--power DB] [--ppm PPM] [--deviation HZ] [--no-preemph]
                [--carrier FRACTION] [--ssb-delay UPDATES] [--duration SECONDS] [--update-rate HZ]
                [--drift HZ] [--dry-run] [--accept-licence] [-q] [-v] [--port DEVICE]
                [--bridge-port DEVICE] [--no-load] [--reload] [--native] [--image FILE]
                [--restore] [--selftest]

espdr-tx: transmit FM or SSB voice from an ESP32-S3 board on the 13 cm band.

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  --selftest            check the signal processing without hardware and exit

what to send:
  -f, --freq MHZ        carrier frequency in MHz (2320 .. 2450); required
  -m, --mode {fm,usb,lsb}
                        fm: narrowband FM; usb or lsb: single sideband (default fm)

audio source:
  -i, --input SOURCE    a WAV file, '-' for a pipe on stdin (a WAV stream or raw samples), or
                        'soundcard' / 'soundcard:DEVICE' (DEVICE is a number or part of a name;
                        see --list-devices)
  --test-tone HZ        send a sine tone instead of an audio source
  --list-devices        list the sound card inputs and exit
  --rate HZ             sample rate of raw audio on stdin (required there; sound cards use theirs)
  --channels N          channels of raw audio on stdin, mixed to mono (default 1)
  --format {f32le,s16le,u8}
                        sample format of raw audio on stdin (default s16le)
  --loop                repeat a file until --duration or Ctrl-C
  --gain DB             raise (or lower) the audio before the automatic level control (default 0)
  --no-agc              no automatic level control: the audio is only clipped at full scale

level and tuning:
  --power DB            transmit power in dB against the strongest setting, 0 .. -17.9 (default
                        0). A weaker setting leaves SSB less amplitude range and costs quality;
                        the absolute power is not calibrated
  --ppm PPM             the board's crystal error: positive if the board transmits too high.
                        Measure it against a known signal

FM:
  --deviation HZ        peak deviation at full scale audio (default 2500)
  --no-preemph          no pre-emphasis (+6 dB per octave from 300 to 3000 Hz)

SSB:
  --carrier FRACTION    the carrier's share of the peak envelope, 0 .. 0.9: 0 suppresses it, 0.05
                        leaves a faint pilot to tune to (default)
  --ssb-delay UPDATES   delay of the gain path against the frequency path in 25 us updates
                        (default 1.0, measured on one board)

session:
  --duration SECONDS    stop after this many seconds (default: until the source ends)
  --update-rate HZ      records per second sent to the chip, 8000 .. 40000 (default 40000)
  --drift HZ            thermal frequency drift to cancel at switch-on, measured on one board
                        (default 210; 0 = off)
  --dry-run             show the settings and send nothing (the board is not touched)
  --accept-licence      confirm the notice that is printed (needed once)
  -q, --quiet           no progress line
  -v, --verbose         more detail

board:
  --port DEVICE         the board's native USB port (default: find it)
  --bridge-port DEVICE  the board's USB-UART port, used to load the firmware (default: find it)
  --no-load             do not load firmware: fail if the transmitter is not running
  --reload              load the transmitter firmware even if it is running
  --native              load through the native port (board without a UART port: hold BOOT, tap
                        RESET)
  --image FILE          use this transmitter image instead of the one of the package
  --restore             when done, reset the board so that it boots the receiver from its flash

examples:
  espdr-tx -f 2350.000 -i speech.wav                         FM voice from a WAV file
  espdr-tx -f 2350.000 -m usb -i speech.wav                  the same as single sideband (upper)
  espdr-tx -f 2350.000 -m usb -i soundcard                   SSB from the default sound card input
  espdr-tx -f 2350.000 -m fm -i soundcard:2 --power -6       FM from input device 2, 6 dB below the strongest setting
  arecord -f S16_LE -r 16000 -c 1 | espdr-tx -f 2350 -m usb -i - --rate 16000     raw samples from a pipe
  sox music.mp3 -t wav - | espdr-tx -f 2350 -i -             a WAV stream from a pipe
  espdr-tx -f 2350 --test-tone 1000 --duration 5             a 1 kHz tone for 5 seconds
  espdr-tx -f 2350 -i speech.wav --dry-run                   show what would happen, send nothing
  espdr-tx --list-devices                                    list sound card inputs

The first time, add --accept-licence (the notice you accept is printed). The transmitter firmware is loaded into the board's RAM
automatically when it is not running (this needs the board's UART USB port as well; --no-load forbids it). A reset, or
`--restore`, brings back the receiver from the flash.
```
