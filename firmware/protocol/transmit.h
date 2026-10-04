/*
 * Transmit protocol of the TX=1 firmware (experimental). The release receiver image does not contain this code.
 *
 * Session:  TX_OP_LO / TX_OP_RATE / TX_OP_DRIFT / TX_OP_LIMIT set the parameters, TX_OP_BEGIN prepares the transmitter (PLL
 * tuned, carrier still off) and is answered like any request. From then on the line carries a stream of 4-byte records
 * from the host and 8-byte status frames from the chip. The carrier comes on when the ring has filled to TX_PREFILL
 * records (or the host has sent the end record), the chip plays one record per update at the rate chosen, and the session
 * ends with a response frame of the op TX_OP_END whose value is the summary below. After that the receiver is set up
 * again and the chip takes ordinary requests.
 *
 * Record (little endian, one 32-bit word):
 *   bits  0..15  signed frequency offset from the programmed LO in 1/16 PLL word steps (1 step = 30 MHz / 65536 = 457.76 Hz)
 *   bits 16..23  gain code of the transmitter (64 strongest .. 127 weakest; the chip clamps to that range)
 *   bits 24..31  flags, bit 0: end of the transmission (this record is the last one)
 *
 * Status frame (every 5 ms while the session runs):
 *   byte 0 TX_STATUS_MAGIC, 1 sequence, 2..3 ring fill in records, 4 underruns (saturating), 5 late updates (saturating),
 *   6 flags (bit 0: carrier on), 7 check byte = (0x100 - sum of bytes 0..6) & 0xFF.
 */
#pragma once

#define TX_OP_LO 40    /* LO frequency in units of 100 Hz (2320 .. 2450 MHz) */
#define TX_OP_RATE 41  /* records per second, 8000 .. 40000 (default 40000) */
#define TX_OP_DRIFT 42 /* thermal drift to cancel at switch-on, signed Hz (default 0), time constant 32768 / rate seconds */
#define TX_OP_LIMIT 43 /* longest session, seconds (1 .. 3600, default 600) */
#define TX_OP_BEGIN 44 /* prepare; the value of the response is the programmed PLL word */
#define TX_OP_END 45   /* op of the final response frame; never sent as a request */
#define TX_OP_TEMP 46  /* the chip's temperature: arg = range 0..4 of its sensor (2 is the SDK default); value = range << 24 | sum of 16 raw readings.
                          Degrees C = 0.4386 * (sum / 16) - 27.88 * offset - 20.52 with the offsets -2, -1, 0, 1, 2 of the ranges. Between sessions only. */

#define TX_MIN_HZ 2320000000u
#define TX_MAX_HZ 2450000000u
#define TX_LOW_MARGIN 48 /* the low byte of the PLL word must lie in 48 .. 207 so that the offsets (clamped to +-44) never carry */
#define TX_MAX_STEPS 44
#define TX_GAIN_STRONGEST 64
#define TX_GAIN_WEAKEST 127

#define TX_RECORD_BYTES 4
#define TX_RING_BYTES (3u * 65536u) /* capture banks 0..2 */
#define TX_PREFILL 4000             /* records the ring holds before the carrier comes on (100 ms at 40 kHz) */
#define TX_FLAG_END 1u

#define TX_STATUS_MAGIC 0xB6
#define TX_STATUS_BYTES 8
#define TX_STATUS_CARRIER 1

/* Summary in the final response: bits 24..31 reason, 16..23 underruns, 0..15 late updates (both saturating). */
#define TX_END_REQUESTED 0 /* the host's end record */
#define TX_END_WATCHDOG 1  /* no data for 500 ms */
#define TX_END_LIMIT 2     /* longest session reached */
#define TX_END_FAILED 3    /* the receiver could not be set up again */
