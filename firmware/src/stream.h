/* Narrowband build: decimated sample stream over USB Serial/JTAG. */
#pragma once

#include <stdint.h>

#include "dsp.h"

extern volatile uint32_t stream_abort; /* set when a run has failed: lanes stop waiting */
extern volatile uint32_t stream_slips; /* unit joins that were a few pairs off and were accepted */

/* Resets the stream state for a run: FIFO, counters, first sequence number. */
void stream_begin(void);

/*
 * Decimates one capture unit and queues it as a packet. Called by the lane
 * that owns the unit; both lanes may call concurrently, packets are queued in
 * sequence order. Core 0 calls stream_unit(), core 1 its copy in core 1's own
 * instruction bank, stream_unit_core1().
 */
void stream_unit(uint32_t sequence, const uint32_t *cur, const uint32_t *prev, unsigned first, unsigned count,
                 uint32_t start, uint32_t out_start);
void stream_unit_core1(uint32_t sequence, const uint32_t *cur, const uint32_t *prev, unsigned first,
                       unsigned count, uint32_t start, uint32_t out_start);

/* Moves queued bytes to the USB endpoint; cheap and non-blocking. Core 0 only. */
void stream_pump(void);

/* Sends everything still queued, then any partial USB packet. Core 0 only. */
void stream_finish(void);

/* Configuration (NB_SET_*); returns a CTL_* status. */
unsigned stream_set(unsigned op, uint32_t value, uint32_t *effective);
extern dsp_config stream_cfg;
/* Inline: lane 1 reads it from its own code bank. */
static inline const dsp_config *stream_config(void) { return &stream_cfg; }
uint32_t stream_stat(unsigned index);

/* Decimator timing test (NB_DSPBENCH): CPU cycles for one 16000-pair unit of noise. */
uint32_t stream_dsp_bench(uint32_t arg);

/* USB throughput test (NB_BENCH): returns the bytes sent. */
uint32_t stream_bench(unsigned seconds, unsigned mode);
