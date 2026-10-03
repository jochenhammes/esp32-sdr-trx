/*
 * Narrowband decimation: 16 Msps IQ -> CIC4 /16 -> FIR /R2.
 *
 * Output sample k is a function of input pairs only (never of earlier
 * output), so each capture unit can be computed independently, by either
 * core: the unit re-reads the end of the unit before it as filter history.
 * Every output belongs to exactly one unit: the one holding the last input
 * pair that influences it.
 *
 *   stage 1 output j  = CIC over the 61 input pairs ending at pair 16j+15
 *   stage 2 output k  = FIR over stage-1 outputs R2*k-(T-1) .. R2*k
 *
 * The CIC is evaluated as the FIR it is (kernel (1+z^-1+...+z^-15)^4, see dsp_cic_taps): block j needs the 64 samples of
 * blocks j-3..j, so a unit starts three blocks before the first stage-1 output its FIR needs, with the sample rings
 * zeroed, and every later output is exact. This equals integrating and combing in 32-bit arithmetic (nothing wraps:
 * 10-bit input times a kernel sum of 2^16 is at most 2^25), bit for bit.
 * Only 32-bit arithmetic is used: core 1 must never call into the ROM
 * (64-bit helpers would), because captures overwrite the ROM's data.
 */
#include "dsp.h"

#include "dsp_taps.h"
#ifdef PROFILE
extern volatile uint32_t dsp_prof[4];
static inline uint32_t cc(void){uint32_t c; __asm__ volatile("rsr.ccount %0":"=a"(c)); return c;}
#define PT(i,e) (dsp_prof[i] += (e))
#else
#define cc() 0
#define PT(i,e) ((void)(e))
#endif

#ifdef __XTENSA__
/* Set by the on-chip test (NB_DSPBENCH) to compare the SIMD paths with the plain C ones. */
extern volatile uint32_t dsp_scalar_only;
#endif

#define R1 DSP_R1
#define CIC_EXTRA 3u /* blocks before a block that its CIC output still needs */
#define RING_MASK (DSP_RING_PAIRS - 1u)
#define HIST 128u    /* stage-1 history ring, power of two >= longest FIR */
#define CIC_SHIFT 11 /* CIC gain 2^16, 10-bit input: full scale -> +-2^14 */
#define IDLE_BLOCKS 32u

#ifndef DSP_FUNC
#define DSP_FUNC dsp_unit
#define DSP_ATTR
#endif

/* I is bits 0..9 and Q bits 10..19 of a capture word, two's complement. */
static inline int32_t sx_i(uint32_t w) { return (int32_t)(w << 22) >> 22; }
static inline int32_t sx_q(uint32_t w) { return (int32_t)(w << 12) >> 22; }

static inline int32_t clamp(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

/*
 * Stage 1 for one block: unpacks its 16 pairs into the sample rings (each block twice, so that the four newest blocks are
 * always one contiguous run) and returns the CIC output, ci[0] for I and ci[1] for Q, before the final shift.
 * `slot` is the block number mod 4; the window of block j is the slots after it, in time order.
 */
static void DSP_ATTR __attribute__((noinline)) cic_block_c(const uint32_t *p, int16_t *ri, int16_t *rq, unsigned slot,
                                                          int32_t *ci)
{
    for (unsigned n = 0; n < R1; n++) {
        const uint32_t w = p[n];
        ri[R1 * slot + n] = ri[R1 * (slot + 4) + n] = (int16_t)sx_i(w);
        rq[R1 * slot + n] = rq[R1 * (slot + 4) + n] = (int16_t)sx_q(w);
    }
    const int16_t *wi = ri + R1 * ((slot + 1) & 3), *wq = rq + R1 * ((slot + 1) & 3);
    int32_t ai = 0, aq = 0;
    for (unsigned n = 0; n < 4 * R1; n++) {
        ai += (int32_t)dsp_cic_taps[n] * wi[n];
        aq += (int32_t)dsp_cic_taps[n] * wq[n];
    }
    ci[0] = ai;
    ci[1] = aq;
}

#ifdef __XTENSA__
/*
 * Stage 1 for one block on the S3's SIMD unit. Same job and same result as cic_block_c().
 *
 * Unpacking: the 16 words of the block (at any word alignment: ee.ld.128.usar.ip and ee.src.q realign them) become
 * four vectors of four words. I is the sign-extended low 10 bits: shift left 22, arithmetic shift right 22; Q is
 * bits 10..19: shift left 12, then right 22 (ssr sets the shift amount for ee.vsl.32 and ee.vsr.32). ee.vunzip.16
 * packs the 32-bit lanes of two vectors into 16-bit lanes, in order. The samples go into the rings twice, as in the
 * C version. The 64-tap FIR is eight ee.vmulas.s16.accx per channel (8 multiply-adds each, into the 40-bit ACCX).
 */
#define CIC_STEP(w, t) \
    "ee.vld.128.ip q1, %[" #w "], 16\n ee.vld.128.ip q2, %[" #t "], 16\n ee.vmulas.s16.accx q1, q2\n"
#define CIC_DOT(w, t) \
    "ee.zero.accx\n" CIC_STEP(w, t) CIC_STEP(w, t) CIC_STEP(w, t) CIC_STEP(w, t) \
    CIC_STEP(w, t) CIC_STEP(w, t) CIC_STEP(w, t) CIC_STEP(w, t)
#define CIC_STORE(d, d2) \
    "ee.vst.128.ip q1, %[" #d "], 16\n ee.vst.128.ip q3, %[" #d "], 0\n" \
    "ee.vst.128.ip q1, %[" #d2 "], 16\n ee.vst.128.ip q3, %[" #d2 "], 0\n"

static void DSP_ATTR __attribute__((noinline)) cic_block_pie(const uint32_t *p, int16_t *ri, int16_t *rq, unsigned slot,
                                                            int32_t *ci)
{
    int16_t *di = ri + R1 * slot, *di2 = ri + R1 * (slot + 4);
    int16_t *dq = rq + R1 * slot, *dq2 = rq + R1 * (slot + 4);
    const int16_t *wi = ri + R1 * ((slot + 1) & 3), *wq = rq + R1 * ((slot + 1) & 3);
    const int16_t *t1 = dsp_cic_taps, *t2 = dsp_cic_taps;
    int32_t acc_i, acc_q, k;
    __asm__ volatile(
        "ee.ld.128.usar.ip q0, %[src], 16\n ee.vld.128.ip q1, %[src], 16\n ee.vld.128.ip q2, %[src], 16\n"
        "ee.vld.128.ip q3, %[src], 16\n ee.vld.128.ip q4, %[src], 0\n"
        "ee.src.q q5, q0, q1\n ee.src.q q6, q1, q2\n ee.src.q q7, q2, q3\n ee.src.q q0, q3, q4\n"
        /* I: sign-extend bits 0..9 */
        "movi %[k], 22\n ssr %[k]\n"
        "ee.vsl.32 q1, q5\n ee.vsl.32 q2, q6\n ee.vsl.32 q3, q7\n ee.vsl.32 q4, q0\n"
        "ee.vsr.32 q1, q1\n ee.vsr.32 q2, q2\n ee.vsr.32 q3, q3\n ee.vsr.32 q4, q4\n"
        "ee.vunzip.16 q1, q2\n ee.vunzip.16 q3, q4\n"
        CIC_STORE(di, di2)
        /* Q: sign-extend bits 10..19 */
        "movi %[k], 12\n ssr %[k]\n"
        "ee.vsl.32 q1, q5\n ee.vsl.32 q2, q6\n ee.vsl.32 q3, q7\n ee.vsl.32 q4, q0\n"
        "movi %[k], 22\n ssr %[k]\n"
        "ee.vsr.32 q1, q1\n ee.vsr.32 q2, q2\n ee.vsr.32 q3, q3\n ee.vsr.32 q4, q4\n"
        "ee.vunzip.16 q1, q2\n ee.vunzip.16 q3, q4\n"
        CIC_STORE(dq, dq2)
        /* the two 64-tap dot products */
        CIC_DOT(wi, t1) "rur.accx_0 %[ai]\n"
        CIC_DOT(wq, t2) "rur.accx_0 %[aq]\n"
        : [src] "+a"(p), [di] "+a"(di), [di2] "+a"(di2), [dq] "+a"(dq), [dq2] "+a"(dq2), [wi] "+a"(wi), [wq] "+a"(wq),
          [t1] "+a"(t1), [t2] "+a"(t2), [k] "=&a"(k), [ai] "=a"(acc_i), [aq] "=a"(acc_q)
        :
        : "memory");
    ci[0] = acc_i;
    ci[1] = acc_q;
}
#undef CIC_STEP
#undef CIC_DOT
#undef CIC_STORE
#endif

/*
 * Symmetric FIR over both channels at once: the taps are symmetric (odd length), so the two samples that
 * share a tap are added first. Own function so the pointers and accumulators stay in registers.
 */
static void DSP_ATTR __attribute__((noinline)) fir_sym(const int16_t *h, const int16_t *wi, const int16_t *wq,
                                                       unsigned taps, int32_t *out)
{
    const unsigned mid = taps / 2;
    const int16_t *ia = wi, *ib = wi + taps - 1, *qa = wq, *qb = wq + taps - 1;
    int32_t accI = (int32_t)h[mid] * wi[mid], accQ = (int32_t)h[mid] * wq[mid];
    for (unsigned n = 0; n < mid; n++) {
        const int32_t c = h[n];
        accI += c * ((int32_t)*ia++ + *ib--);
        accQ += c * ((int32_t)*qa++ + *qb--);
    }
    out[0] = accI;
    out[1] = accQ;
}

#ifdef __XTENSA__

/*
 * One FIR output from the ESP32-S3's SIMD unit: 8 multiply-accumulates per instruction into ACCX.
 * `w` is the 16-byte aligned start of the vectors covering the window, `t` the tap row shifted to match
 * (dsp_taps_pie_*), nvec vectors each. Taps outside the window are zero, so the extra samples are harmless.
 */
static int32_t DSP_ATTR __attribute__((noinline)) fir_pie(const int16_t *w, const int16_t *t, unsigned nvec)
{
    int32_t r;
    __asm__ volatile("ee.zero.accx\n"
                     "loopnez %3, 1f\n"
                     "ee.vld.128.ip q0, %0, 16\n"
                     "ee.vld.128.ip q1, %1, 16\n"
                     "ee.vmulas.s16.accx q0, q1\n"
                     "1:\n"
                     "rur.accx_0 %2\n"
                     : "+a"(w), "+a"(t), "=a"(r)
                     : "a"(nvec)
                     : "memory");
    return r;
}
#endif

unsigned DSP_ATTR DSP_FUNC(const dsp_config *cfg, const uint32_t *cur, const uint32_t *prev, unsigned first,
                           unsigned count, uint32_t start, int have_prev, uint8_t *out, void (*idle)(void))
{
    const unsigned r2 = cfg->r2;
    const unsigned taps = r2 == 2 ? DSP_TAPS_R2_2 : r2 == 3 ? DSP_TAPS_R2_3 : DSP_TAPS_R2_4;
    const int16_t *h = r2 == 2 ? dsp_taps_r2_2 : r2 == 3 ? dsp_taps_r2_3 : dsp_taps_r2_4;
#ifdef __XTENSA__
    const int16_t *pie_taps = r2 == 2   ? &dsp_taps_pie_r2_2[0][0]
                              : r2 == 3 ? &dsp_taps_pie_r2_3[0][0]
                                        : &dsp_taps_pie_r2_4[0][0];
    const unsigned pie_vecs = r2 == 2 ? DSP_PIE_VECS_R2_2 : r2 == 3 ? DSP_PIE_VECS_R2_3 : DSP_PIE_VECS_R2_4;
#endif
    const dsp_geometry g = dsp_geom(start, count, r2);

    /* Blocks are numbered relative to the unit's first complete block (rel 0). */
    int32_t rel = (int32_t)g.skip - (int32_t)(taps - 1u) - (int32_t)CIC_EXTRA;
    if (!have_prev && rel < 0)
        rel = 0;
    const int32_t rel_end = (int32_t)g.nb;

    /* The four newest blocks of samples, each stored twice (see cic_block_c). Static for the same reason as below. */
    static int16_t ri[8 * R1] __attribute__((aligned(16))), rq[8 * R1] __attribute__((aligned(16)));
    for (unsigned n = 0; n < 8 * R1; n++)
        ri[n] = rq[n] = 0;
    /* Static, not on the stack: at stack offsets beyond 510 bytes every access costs three instructions.
     * Each build of this file (core 0's and core 1's copy) has its own, and each runs on one core only. */
    static int16_t hi[2 * HIST] __attribute__((aligned(16))), hq[2 * HIST] __attribute__((aligned(16)));
    for (unsigned n = 0; n < 2 * HIST; n++)
        hi[n] = hq[n] = 0;
    unsigned pos = 0, produced = 0, tick = 0, blk = 0;
    int32_t trigger = (int32_t)g.skip; /* next block that triggers stage 2 */
    uint32_t tmp[R1];
    const unsigned shift = cfg->shift;

    uint32_t cs = cc(); (void)cs;
    for (; rel < rel_end; rel++) {
        /* The block's first pair, relative to the unit's first pair. */
        const int32_t off = g.d - (int32_t)(R1 - 1u) + rel * (int32_t)R1;
        const uint32_t *p;
        if (off >= 0) {
            unsigned idx = (first + (unsigned)off) & RING_MASK;
            if (idx + R1 <= DSP_RING_PAIRS) {
                p = cur + idx;
                goto have_block;
            }
        } else if (off + (int32_t)R1 <= 0) {
            unsigned idx = (first + (unsigned)off) & RING_MASK;
            if (idx + R1 <= DSP_RING_PAIRS) {
                p = prev + idx;
                goto have_block;
            }
        }
        for (unsigned n = 0; n < R1; n++) {
            int32_t o = off + (int32_t)n;
            unsigned idx = (first + (unsigned)o) & RING_MASK;
            tmp[n] = o >= 0 ? cur[idx] : prev[idx];
        }
        p = tmp;
    have_block:;

        /* Stage 1 */
        uint32_t c0 = cc();
        int32_t ci[2];
#ifdef __XTENSA__
        if (!dsp_scalar_only)
            cic_block_pie(p, ri, rq, blk & 3u, ci);
        else
#endif
            cic_block_c(p, ri, rq, blk & 3u, ci);
        blk++;
        uint32_t c1 = cc(); PT(0, c1 - c0);
        hi[pos] = hi[pos + HIST] = (int16_t)(ci[0] >> CIC_SHIFT);
        hq[pos] = hq[pos + HIST] = (int16_t)(ci[1] >> CIC_SHIFT);
        pos = (pos + 1) & (HIST - 1);

        uint32_t c2 = cc(); PT(1, c2 - c1);
        if (idle && ++tick == IDLE_BLOCKS) {
            tick = 0;
            idle();
        }

        /* Stage 2: every R2-th stage-1 output from the first trigger on. */
        if (rel != trigger)
            continue;
        trigger += (int32_t)r2;
        const int16_t *wi = hi + ((pos - taps) & (HIST - 1));
        const int16_t *wq = hq + ((pos - taps) & (HIST - 1));
        uint32_t c3 = cc();
        int32_t acc[2];
#ifdef __XTENSA__
        if (!dsp_scalar_only) {
            const unsigned s = (pos - taps) & (HIST - 1), a = s & 7;
            acc[0] = fir_pie(hi + (s - a), pie_taps + a * (pie_vecs * 8), pie_vecs);
            acc[1] = fir_pie(hq + (s - a), pie_taps + a * (pie_vecs * 8), pie_vecs);
        } else
#endif
        fir_sym(h, wi, wq, taps, acc);
        const int32_t accI = acc[0], accQ = acc[1];
        int32_t yi = (accI + (1 << 14)) >> 15;
        int32_t yq = (accQ + (1 << 14)) >> 15;
        if (shift) {
            yi = (yi + (1 << (shift - 1))) >> shift;
            yq = (yq + (1 << (shift - 1))) >> shift;
        }
        if (cfg->format == 0) {
            yi = clamp(yi, -32768, 32767);
            yq = clamp(yq, -32768, 32767);
            uint8_t *o = out + produced * 4;
            o[0] = (uint8_t)yi;
            o[1] = (uint8_t)(yi >> 8);
            o[2] = (uint8_t)yq;
            o[3] = (uint8_t)(yq >> 8);
        } else {
            uint8_t *o = out + produced * 2;
            o[0] = (uint8_t)(int8_t)clamp(yi, -128, 127);
            o[1] = (uint8_t)(int8_t)clamp(yq, -128, 127);
        }
        produced++;
        PT(2, cc() - c3);
    }
    return produced;
}
