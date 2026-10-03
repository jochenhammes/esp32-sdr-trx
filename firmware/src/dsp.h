/* Decimation chain of the narrowband build; plain 32-bit C, also built on the host. */
#pragma once

#include <stdint.h>

#define DSP_RING_PAIRS 16384u
#define DSP_R1 16u /* stage-1 decimation: input pairs per CIC block */
#define DSP_MAX_OUTPUTS 512u /* per unit, with R2 = 2 and 16288-pair units */

typedef struct {
    unsigned r2;     /* 2, 3 or 4 */
    unsigned format; /* 0: int16 I,Q   1: int8 I,Q */
    unsigned shift;  /* extra right shift of the output */
} dsp_config;

/* Block geometry shared by dsp_outputs() and dsp_unit(). */
typedef struct {
    int32_t d;       /* offset of the first block's last pair within the unit */
    uint32_t nb;     /* blocks whose last pair is in the unit */
    uint32_t ja;     /* index of the first such block, mod 2^28 */
    uint32_t skip;   /* blocks before the first stage-2 trigger */
} dsp_geometry;

/*
 * The pair index since the start of a run wraps at DSP_START_WRAP instead of 2^32: it is a multiple of 16 * 12, so the
 * block number keeps its phase modulo 2, 3 and 4 across the wrap (a power-of-two wrap would shift the R2 = 3 phase).
 */
#define DSP_START_WRAP 4294967232u /* 192 * 22369621 */

static inline __attribute__((always_inline)) uint32_t dsp_advance(uint32_t start, unsigned count)
{
    return start >= DSP_START_WRAP - count ? start - (DSP_START_WRAP - count) : start + count;
}

/* x mod 3 without a division (2^2 = 1 mod 3: sum the base-4 digits). Plain 32-bit code, safe on core 1. */
static inline __attribute__((always_inline)) unsigned dsp_mod3(uint32_t x)
{
    x = (x >> 16) + (x & 0xFFFFu);
    x = (x >> 8) + (x & 0xFFu);
    x = (x >> 4) + (x & 0xFu);
    x = (x >> 2) + (x & 3u);
    x = (x >> 2) + (x & 3u);
    x = (x >> 2) + (x & 3u);
    x = x >= 3 ? x - 3 : x;
    return x >= 3 ? x - 3 : x;
}

static inline __attribute__((always_inline)) dsp_geometry dsp_geom(uint32_t start, unsigned count, unsigned r2)
{
    dsp_geometry g;
    g.d = (int32_t)(15u - (start & 15u));
    g.nb = ((uint32_t)count - (uint32_t)g.d - 1u) / DSP_R1 + 1u;
    g.ja = start >> 4;
    if (r2 == 3) {
        unsigned m = dsp_mod3(g.ja);
        g.skip = m ? 3u - m : 0u;
    } else {
        g.skip = (0u - g.ja) & (r2 - 1u);
    }
    return g;
}

/*
 * Number of output samples dsp_unit() produces for a unit starting at pair `start`.
 * Inline, so each core runs it from its own instruction bank.
 */
static inline __attribute__((always_inline)) unsigned dsp_outputs(const dsp_config *cfg, uint32_t start,
                                                                  unsigned count)
{
    dsp_geometry g = dsp_geom(start, count, cfg->r2);
    if (g.nb <= g.skip)
        return 0;
    uint32_t n = g.nb - g.skip + cfg->r2 - 1u; /* ceil((nb - skip) / r2); n < 2^16 */
    if (cfg->r2 == 3)
        return (n * 0xAAABu) >> 17;
    return n >> (cfg->r2 == 2 ? 1 : 2);
}

/*
 * Decimates one capture unit.
 *   cur, prev   capture banks of this unit and of the unit before it
 *   first,count ring index of the unit's first pair, and its length
 *   start       index of that pair since the run began (wraps at 2^32)
 *   have_prev   0 for the first unit of a run (no earlier samples exist)
 *   out         receives dsp_outputs() samples (DSP_MAX_OUTPUTS * 4 bytes max)
 *   idle        optional hook, called every 32 input blocks (512 pairs)
 * The unit before must be contiguous in the ring (it ended at `first`).
 * Units are independent: nothing is carried over except what is re-read
 * from `prev`. Returns the number of output samples.
 */
unsigned dsp_unit(const dsp_config *cfg, const uint32_t *cur, const uint32_t *prev, unsigned first,
                  unsigned count, uint32_t start, int have_prev, uint8_t *out, void (*idle)(void));
unsigned dsp_unit_core1(const dsp_config *cfg, const uint32_t *cur, const uint32_t *prev, unsigned first,
                        unsigned count, uint32_t start, int have_prev, uint8_t *out, void (*idle)(void));
