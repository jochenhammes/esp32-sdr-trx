/*
 * Host harness for src/dsp.c: splits a 16 Msps IQ file into capture units
 * the way the firmware sees them (four 16384-pair banks, ring index running
 * on across banks, unit lengths 15360..16288) and decimates each unit.
 *
 *   dsp_selftest in.u32 out.bin r2 format shift seed [start0]
 * start0 is the pair index the first unit claims to start at (a multiple of 16), to test every phase and the wrap.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/dsp.h"

static uint32_t banks[4][DSP_RING_PAIRS];

static uint32_t lcg(uint32_t *s)
{
    *s = *s * 1664525u + 1013904223u;
    return *s >> 8;
}

int main(int argc, char **argv)
{
    if (argc != 7 && argc != 8) {
        fprintf(stderr, "usage: %s in.u32 out.bin r2 format shift seed [start0]\n", argv[0]);
        return 2;
    }
    FILE *in = fopen(argv[1], "rb"), *out = fopen(argv[2], "wb");
    if (!in || !out)
        return 2;
    dsp_config cfg = {(unsigned)atoi(argv[3]), (unsigned)atoi(argv[4]), (unsigned)atoi(argv[5])};
    uint32_t seed = (uint32_t)atoi(argv[6]);

    fseek(in, 0, SEEK_END);
    uint64_t total = (uint64_t)ftell(in) / 4;
    fseek(in, 0, SEEK_SET);

    uint64_t start = 0;
    uint32_t pair_start = argc == 8 ? (uint32_t)strtoul(argv[7], 0, 10) : 0;
    unsigned ring_index = 0;
    uint32_t expect_k = 0;
    static uint8_t buf[DSP_MAX_OUTPUTS * 4];
    uint32_t *chunk = malloc(16288 * 4);
    unsigned unit = 0, bytes = cfg.format ? 2 : 4;
    memset(banks, 0, sizeof(banks));

    while (1) {
        unsigned count = 15360 + lcg(&seed) % (16288 - 15360 + 1);
        if (start + count > total)
            break;
        if (fread(chunk, 4, count, in) != count)
            break;
        unsigned b = unit & 3;
        for (unsigned i = 0; i < count; i++)
            banks[b][(ring_index + i) & (DSP_RING_PAIRS - 1)] = chunk[i];
        unsigned n = dsp_unit(&cfg, banks[b], banks[(b + 3) & 3], ring_index, count, pair_start, unit != 0, buf, 0);
        if (n > DSP_MAX_OUTPUTS || n != dsp_outputs(&cfg, pair_start, count)) {
            fprintf(stderr, "unit %u: n=%u but dsp_outputs says %u\n", unit, n, dsp_outputs(&cfg, pair_start, count));
            return 1;
        }
        expect_k += n;
        fwrite(buf, bytes, n, out);
        start += count;
        pair_start = dsp_advance(pair_start, count);
        ring_index = (ring_index + count) & (DSP_RING_PAIRS - 1);
        unit++;
    }
    fprintf(stderr, "units=%u pairs=%llu outputs=%u\n", unit, (unsigned long long)start, expect_k);
    return 0;
}
