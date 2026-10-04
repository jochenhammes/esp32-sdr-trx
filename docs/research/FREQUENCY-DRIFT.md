# Frequency drift of the transmitter against the chip temperature (2026-10-04)

Question: how far does the carrier move while the board warms up and cools, can it be predicted, and can the chip's own temperature sensor be used to correct it?
Measured with the production transmit path (`espdr-tx -m fm --deviation 0 --ppm 5.4 --power -6`, the plain carrier of the FM mode, including the firmware's
built-in cancellation of the first seconds, `--drift 210`) and a PlutoSDR that estimated the carrier frequency of every 0.17 s buffer. The Pluto's own crystal
drifts too and could not be separated; its recording started 3 s before the first transmission. Raw data: `drift-series-2026-10-04.json`; scripts: `scripts/drift/`.

## The temperature sensor

The ESP32-S3 has one (SENS peripheral, part of the SAR ADC; `IQ_OP_TEMP` in the research firmware, see `firmware/src/radio.c`). It needs the SAR block's I2C access
and APB clock switched on, then the register `SENS_SAR_TSENS_CTRL` (0x60008850) is read directly (reading through the struct bit fields gave zeros). Degrees C =
0.4386 * raw - 27.88 * offset - 20.52 with the offset -2, -1, 0, 1, 2 of the five ranges; ranges 0 to 3 agreed within 0.5 C, range 4 saturates. The sum of 16 reads
resolves 0.03 C. The absolute accuracy was not checked (the sensor is meant for a few degrees). It can only be read **between** transmissions with the current
firmware: during a transmission the chip only plays records.

## What the temperature does

* Idle (receiver image on the board): 41.8 to 42.2 C. A **transmission heats the chip fast**: +2 C in 4 s, 42 -> 57 C in 150 s (time constant about 40 s, to about 56 C).
  After a long transmission the sensor falls 57 -> 47 C in about 5 minutes; every 5 s burst warms it by 1 to 2 C.
* The carrier frequency follows it like a crystal around its turnover point. Over one session the carrier moved over **1470 Hz** (0.6 ppm at 2.35 GHz):

| What | Frequency |
|---|---|
| first 30 s of a transmission starting cold (42 C) | falls by about 290 Hz |
| then, while the chip heats from 48 to 57 C | rises by about 900 Hz in 120 s (+14 Hz/s at the end) |
| a transmission starting at 47 C | rises monotonic, +1330 Hz in 100 s |
| 5 s bursts every 30 s after a long transmission (chip 54 -> 47 C) | -875, -1160, -1300, -1380, -1431, -1452, -1457, -1459, -1460 Hz (settles within 4 minutes) |
| slope against the sensor | about 140 Hz/C at 52 to 54 C, 90 at 51, 57 at 50, 17 at 48.5 C: curved, not linear |

Model: `f = c + k * (Tx - T0)^2` with `T0` = 47.7 C (the sensor's scale), `k` = 19 Hz/C^2, the crystal's temperature `Tx` following the chip's (first order, 6 s) and
the heating during a transmission `T(t) = 56.0 C - (56.0 - T_start) * exp(-t/37 s)`. Fitted to the 18 bursts and the two long transmissions together:

| | spread of the frequency | after the model |
|---|---|---|
| 18 bursts | 430 Hz rms | **35 Hz rms** (worst 122 Hz) |
| long transmission 2 (starts at 47 C) | 355 Hz rms | 63 Hz rms |
| long transmission 1 (starts cold at 42 C) | 312 Hz rms | 198 Hz rms (not explained: the cold start, perhaps also the Pluto warming up in its first minutes) |
| everything | 407 Hz rms | 146 Hz rms |

A linear fit against the temperature leaves 165 Hz for the bursts, a plain parabola 72 Hz, with the crystal lag 35 Hz. The turnover point and the curvature are those of
**this board**; another board has other numbers.

## What it means for RTTY

RTTY at 170 Hz shift tolerates about +-85 Hz of error at the decoder. The carrier moves by hundreds of Hz within the first minutes of use (and by about +-40 Hz per minute
while it warms up), so a decoder without automatic tuning loses lock, and the RTTY receiver of pluto-tx needs several seconds to track it.

* At a chip temperature near the turnover (about 47 to 49 C on the sensor) the frequency barely depends on the temperature (19 Hz/C^2: +-2 C give +-80 Hz at most).
  Starting a transmission when the chip is in that band, and keeping transmissions short, keeps the carrier within a few tens of Hz.
* A correction is possible if the chip temperature is known during the transmission: `df = k * ((Tx-T0)^2 - (Tx_start-T0)^2)` as a slowly changing offset in every record.
  Needed: (1) the firmware reads the sensor before each transmission (a small `TX_OP`; during the transmission the temperature can be modelled, `tau` 37 s, or read
  every few 100 ms inside the loop, which costs a few microseconds of the 25 microsecond budget and has not been tried); (2) a per-board calibration (c, k, T0)
  from one measurement series like this one; (3) the host applies the offset. Expected: the bursts' 430 Hz spread becomes about 35 Hz, a cold start still about 200 Hz.
* Not done: a model that also explains the cold start, a series with the board at other ambient temperatures, a series that reads the sensor inside a transmission,
  and a check of the model on a second board.
