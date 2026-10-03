/*
 * Narrowband (FPGA-free) build: stream format and extra control operations.
 *
 * The ESP32-S3 samples at 16 Msps, decimates on chip (4th-order CIC by 16,
 * then a droop-compensating FIR by R2) and streams complex samples over its
 * native USB Serial/JTAG port, the same port as the control protocol.
 *
 * Stream packet (little-endian), sent as a plain byte stream between the
 * control request and the reply of ESP_RUN:
 *   hw0  magic 0xE5, 0x5D
 *   hw1  format (NB_FORMAT_*) | R2 << 8
 *   hw2  count: complex samples in this packet
 *   hw3  dropped: units lost to a full FIFO so far (saturating u16)
 *   hw4-5 seq: unit sequence number (u32)
 *   hw6-7 first: output sample index of the first sample (u32, wraps)
 *   hw8  output shift | 0 << 8
 *   hw9  checksum: the 16-bit sum of all ten halfwords is 0xFFFF
 *   payload: count x (I, Q), int16 LE (cs16) or int8 (cs8)
 * Output rate = 16 MHz / 16 / R2. A gap in `first` marks lost samples.
 */
#ifndef IQSTREAM_NARROWBAND_H
#define IQSTREAM_NARROWBAND_H

#define NB_MAGIC0 0xE5
#define NB_MAGIC1 0x5D
#define NB_HEADER_BYTES 20
#define NB_FORMAT_CS16 0
#define NB_FORMAT_CS8 1

#define NB_SET_DECIM 29    /* R2: 2 (500 ksps), 3 (333 ksps) or 4 (250 ksps) */
#define NB_SET_FORMAT 30   /* NB_FORMAT_* */
#define NB_SET_OUTSHIFT 31 /* extra right shift of the output, 0..12 */
#define NB_BENCH 32        /* USB throughput test: seconds (0: until a byte arrives) | write mode << 16 (0 the stream's own
                            * write path, 1 byte-wise with a poll per byte, 2 one check then 64 writes); reply: bytes sent */

/* Decimator tests without a capture. arg = R2 | format << 4 | core-1 copy << 8; reply: CPU cycles for one
 * 16000-pair unit of noise. With NB_DSP_VERIFY the reply is instead the number of bytes in which the SIMD
 * FIR differs from the plain C one (0: identical). Builds with PROFILE=1 also keep per-section cycle sums:
 * NB_DSP_PROFILE | section << 4 reads one, NB_DSP_PROFILE_RESET clears them before the run. */
#define NB_DSPBENCH 33
#define NB_DSP_VERIFY 0x8000
#define NB_DSP_PROFILE 0x4000
#define NB_DSP_PROFILE_RESET 0x2000

/* Numbered after ESP_STAT_COUNT (protocol/control.h); upstream keeps adding to that list. */
#define NB_STAT_DROPPED 32   /* units dropped (FIFO full) in the last run */
#define NB_STAT_FIFO_PEAK 33 /* peak FIFO fill in bytes */
#define NB_STAT_SLIPS 34     /* unit joins accepted although the next bank started a few pairs early */
#define NB_STAT_COUNT 35

#endif
