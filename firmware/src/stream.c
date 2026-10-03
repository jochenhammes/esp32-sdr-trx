/*
 * Packet queue between the two DSP lanes and the USB endpoint.
 *
 * Each capture unit becomes one packet (see narrowband.h). Packets are
 * appended to a byte FIFO in unit order; core 0 drains the FIFO into the USB
 * Serial/JTAG endpoint from its idle loops. A packet that does not fit is
 * dropped whole, so the byte stream always stays aligned; the host sees the
 * gap in the packet's sample index.
 */
#include "stream.h"

#include "control.h"
#include "narrowband.h"
#include "board.h"
#include "platform.h"
#include "soc/usb_serial_jtag_reg.h"

#define FIFO_BYTES 12288u
#define USB_PACKET 64u
#define HEADER NB_HEADER_BYTES
#define STAGING_BYTES (HEADER + DSP_MAX_OUTPUTS * 4u)
#define CPU_CYCLES_PER_SECOND 240000000u
#define DRAIN_TIMEOUT_CYCLES (240u * 100000u) /* 100 ms without progress */

dsp_config stream_cfg = {.r2 = 4, .format = NB_FORMAT_CS8, .shift = 4};

static uint8_t staging[2][STAGING_BYTES] __attribute__((aligned(16)));
static uint8_t fifo[FIFO_BYTES] __attribute__((aligned(16)));

static struct {
    /* head_* are owned by whoever holds the commit turn, tail_* by core 0. */
    volatile uint32_t head_total, tail_total;
    uint32_t head_pos, tail_pos;
    volatile uint32_t commit_seq;
    volatile uint32_t dropped_units, fifo_peak;
    uint32_t dropped_halfword; /* saturating count carried in the packet header */
} q;

volatile uint32_t stream_slips; /* unit joins accepted with a small slip, see capture.c */
volatile uint32_t stream_abort; /* set by capture.c when a run has failed */

void stream_begin(void)
{
    q.head_total = q.tail_total = 0;
    q.head_pos = q.tail_pos = 0;
    q.commit_seq = 0;
    q.dropped_units = 0;
    q.fifo_peak = 0;
    stream_abort = 0;
    stream_slips = 0;
    memory_barrier();
}

uint32_t stream_stat(unsigned index)
{
    switch (index) {
    case NB_STAT_DROPPED: return q.dropped_units;
    case NB_STAT_FIFO_PEAK: return q.fifo_peak;
    case NB_STAT_SLIPS: return stream_slips;
    default: return 0;
    }
}

unsigned stream_set(unsigned op, uint32_t value, uint32_t *effective)
{
    switch (op) {
    case NB_SET_DECIM:
        if (value < 2 || value > 4)
            return CTL_BAD_ARGUMENT;
        stream_cfg.r2 = value;
        *effective = stream_cfg.r2;
        return CTL_OK;
    case NB_SET_FORMAT:
        if (value > NB_FORMAT_CS8)
            return CTL_BAD_ARGUMENT;
        stream_cfg.format = value;
        *effective = stream_cfg.format;
        return CTL_OK;
    case NB_SET_OUTSHIFT:
        if (value > 12)
            return CTL_BAD_ARGUMENT;
        stream_cfg.shift = value;
        *effective = stream_cfg.shift;
        return CTL_OK;
    default:
        return CTL_UNKNOWN_OP;
    }
}

/* ---- USB ------------------------------------------------------------------------------ */

static inline uint32_t fifo_fill(void) { return q.head_total - q.tail_total; }

/*
 * Writes one packet if the endpoint has room. A full 64-byte packet waits for the hardware's IN-EMPTY status, which the
 * register description defines as "up to 64 bytes can be written now", and then writes all of it without polling in
 * between (the hardware flushes a full packet by itself): 4 us instead of 10 us, and 0.87 instead of 0.77 MB/s on the test host.
 * A shorter packet uses the byte-wise path and WR_DONE.
 */
static inline int usb_send_packet(const uint8_t *p, unsigned n)
{
    if (n == USB_PACKET) {
        if (!(REG(USB_SERIAL_JTAG_INT_RAW_REG) & USB_SERIAL_JTAG_SERIAL_IN_EMPTY_INT_RAW))
            return 0;
        REG(USB_SERIAL_JTAG_INT_CLR_REG) = USB_SERIAL_JTAG_SERIAL_IN_EMPTY_INT_CLR;
        for (unsigned i = 0; i < n; i++)
            REG(USB_SERIAL_JTAG_EP1_REG) = p[i];
        return 1;
    }
    if (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE))
        return 0;
    REG(USB_SERIAL_JTAG_INT_CLR_REG) = USB_SERIAL_JTAG_SERIAL_IN_EMPTY_INT_CLR; /* keep the status honest for the next packet */
    for (unsigned i = 0; i < n; i++) {
        while (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE)) {
        }
        REG(USB_SERIAL_JTAG_EP1_REG) = p[i];
    }
    REG(USB_SERIAL_JTAG_EP1_CONF_REG) = USB_SERIAL_JTAG_WR_DONE;
    return 1;
}

/* Bench modes for comparison (NB_BENCH, mode 1 and 2): the old byte-wise write with a DATA_FREE poll per byte, and one DATA_FREE check
 * followed by 64 writes. */
static inline int usb_send_polled(const uint8_t *p, unsigned n)
{
    if (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE))
        return 0;
    for (unsigned i = 0; i < n; i++) {
        while (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE)) {
        }
        REG(USB_SERIAL_JTAG_EP1_REG) = p[i];
    }
    REG(USB_SERIAL_JTAG_EP1_CONF_REG) = USB_SERIAL_JTAG_WR_DONE;
    return 1;
}

static inline int usb_send_free(const uint8_t *p, unsigned n)
{
    if (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE))
        return 0;
    for (unsigned i = 0; i < n; i++)
        REG(USB_SERIAL_JTAG_EP1_REG) = p[i];
    return 1;
}

static unsigned pump(unsigned min_bytes)
{
    uint32_t fill = fifo_fill();
    if (fill < min_bytes || fill == 0)
        return 0;
    if (!(REG(USB_SERIAL_JTAG_EP1_CONF_REG) & USB_SERIAL_JTAG_SERIAL_IN_EP_DATA_FREE))
        return 0;
    unsigned n = fill < USB_PACKET ? fill : USB_PACKET;
    uint8_t chunk[USB_PACKET];
    unsigned pos = q.tail_pos;
    for (unsigned i = 0; i < n; i++) {
        chunk[i] = fifo[pos];
        if (++pos == FIFO_BYTES)
            pos = 0;
    }
    if (!usb_send_packet(chunk, n))
        return 0;
    q.tail_pos = pos;
    memory_barrier();
    q.tail_total += n;
    return n;
}

void stream_pump(void) { pump(USB_PACKET); }

void stream_finish(void)
{
    uint32_t since = cpu_cycles();
    while (fifo_fill()) {
        if (pump(1))
            since = cpu_cycles();
        else if (cpu_cycles() - since > DRAIN_TIMEOUT_CYCLES)
            break; /* host not reading: give up on the rest */
    }
}

/* ---- packets -------------------------------------------------------------------------- */

static inline __attribute__((always_inline)) void put16(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static inline __attribute__((always_inline)) void put32(uint8_t *p, uint32_t v)
{
    put16(p, v);
    put16(p + 2, v >> 16);
}

/*
 * Body of stream_unit() and stream_unit_core1(), inlined into both so core 1
 * runs its copy from its own instruction bank and never fetches from core 0's.
 * `core1` is a constant at each call site.
 */
static inline __attribute__((always_inline)) void unit_body(const unsigned core1, uint32_t sequence,
                                                            const uint32_t *cur, const uint32_t *prev,
                                                            unsigned first, unsigned count, uint32_t start,
                                                            uint32_t out_start)
{
    uint8_t *buf = staging[core1];
    void (*idle)(void) = core1 ? 0 : stream_pump;
    unsigned n = (core1 ? dsp_unit_core1 : dsp_unit)(&stream_cfg, cur, prev, first, count, start,
                                                     sequence != 0, buf + HEADER, idle);
    unsigned bytes_per = stream_cfg.format == NB_FORMAT_CS16 ? 4 : 2;
    unsigned payload = n * bytes_per;
    unsigned total = HEADER + payload;

    /* Wait for our turn: packets must enter the FIFO in sequence order. */
    while (q.commit_seq != sequence && !stream_abort) {
        if (!core1)
            stream_pump();
    }
    if (stream_abort)
        return;

    uint32_t dropped = q.dropped_units;
    buf[0] = NB_MAGIC0;
    buf[1] = NB_MAGIC1;
    put16(buf + 2, stream_cfg.format | (stream_cfg.r2 << 8));
    put16(buf + 4, n);
    put16(buf + 6, dropped > 0xFFFF ? 0xFFFF : dropped);
    put32(buf + 8, sequence);
    put32(buf + 12, out_start);
    put16(buf + 16, stream_cfg.shift);
    uint32_t sum = 0;
    for (unsigned i = 0; i < HEADER - 2; i += 2)
        sum += buf[i] | (buf[i + 1] << 8);
    put16(buf + HEADER - 2, 0xFFFFu - (sum & 0xFFFFu));

    uint32_t fill = fifo_fill();
    if (FIFO_BYTES - fill >= total) {
        unsigned pos = q.head_pos;
        unsigned part = FIFO_BYTES - pos < total ? FIFO_BYTES - pos : total;
        for (unsigned i = 0; i < part; i++)
            fifo[pos + i] = buf[i];
        for (unsigned i = part; i < total; i++)
            fifo[i - part] = buf[i];
        pos += total;
        if (pos >= FIFO_BYTES)
            pos -= FIFO_BYTES;
        q.head_pos = pos;
        memory_barrier();
        q.head_total += total;
        if (fill + total > q.fifo_peak)
            q.fifo_peak = fill + total;
    } else {
        q.dropped_units++;
    }
    memory_barrier();
    q.commit_seq = sequence + 1;
}

void stream_unit(uint32_t sequence, const uint32_t *cur, const uint32_t *prev, unsigned first, unsigned count,
                 uint32_t start, uint32_t out_start)
{
    unit_body(0, sequence, cur, prev, first, count, start, out_start);
}

void CORE1_CODE stream_unit_core1(uint32_t sequence, const uint32_t *cur, const uint32_t *prev, unsigned first,
                                  unsigned count, uint32_t start, uint32_t out_start)
{
    unit_body(1, sequence, cur, prev, first, count, start, out_start);
}


#ifdef PROFILE
volatile uint32_t dsp_prof[4]; /* cycles per section of dsp_unit(), see dsp.c */
#endif
volatile uint32_t dsp_scalar_only;
/* ---- decimator timing ------------------------------------------------------------------ */

/* Runs outside a capture, on banks 0 and 1 (bank 3 holds the ROM's working memory). */
uint32_t stream_dsp_bench(uint32_t arg)
{
    if (arg & NB_DSP_VERIFY) { /* bytes that differ between the plain C and the SIMD FIR */
        uint32_t *c = (uint32_t *)CAPTURE_BANK_BASE, *p = (uint32_t *)(CAPTURE_BANK_BASE + CAPTURE_BANK_BYTES);
        uint32_t sd = 777;
        for (unsigned i = 0; i < DSP_RING_PAIRS; i++) {
            sd = sd * 1664525u + 1013904223u; c[i] = (sd >> 8) & 0xFFFFFu;
            sd = sd * 1664525u + 1013904223u; p[i] = (sd >> 8) & 0xFFFFFu;
        }
        uint8_t *ref = (uint8_t *)(CAPTURE_BANK_BASE + 2 * CAPTURE_BANK_BYTES);
        dsp_config vc = {.r2 = arg & 15, .format = (arg >> 4) & 1, .shift = 4};
        dsp_scalar_only = 1;
        unsigned n0 = (arg & 0x100 ? dsp_unit_core1 : dsp_unit)(&vc, c, p, 0, 16000, 0, 1, ref, 0);
        dsp_scalar_only = 0;
        unsigned n1 = (arg & 0x100 ? dsp_unit_core1 : dsp_unit)(&vc, c, p, 0, 16000, 0, 1, staging[0] + HEADER, 0);
        uint32_t bad = n0 != n1;
        for (unsigned i = 0; i < n0 * (vc.format ? 2 : 4) && i < DSP_MAX_OUTPUTS * 4; i++)
            bad += ref[i] != staging[0][HEADER + i];
        return bad;
    }
#ifdef PROFILE
    if (arg & NB_DSP_PROFILE_RESET)
        dsp_prof[0] = dsp_prof[1] = dsp_prof[2] = 0;
    if ((arg & 0xF000) == NB_DSP_PROFILE)
        return dsp_prof[(arg >> 4) & 3];
#endif
    uint32_t *cur = (uint32_t *)CAPTURE_BANK_BASE, *prev = (uint32_t *)(CAPTURE_BANK_BASE + CAPTURE_BANK_BYTES);
    uint32_t seed = 12345;
    for (unsigned i = 0; i < DSP_RING_PAIRS; i++) {
        seed = seed * 1664525u + 1013904223u;
        cur[i] = (seed >> 8) & 0xFFFFFu;
        seed = seed * 1664525u + 1013904223u;
        prev[i] = (seed >> 8) & 0xFFFFFu;
    }
    memory_barrier();
    dsp_config c = {.r2 = arg & 15, .format = (arg >> 4) & 15, .shift = 4};
    uint32_t t0 = cpu_cycles();
    (arg & 0x100 ? dsp_unit_core1 : dsp_unit)(&c, cur, prev, 0, 16000, 0, 1, staging[0] + HEADER, 0);
    return cpu_cycles() - t0;
}

/* ---- throughput test ------------------------------------------------------------------ */

/* Streams 64-byte blocks (0xBE, 0xEF, 16-bit counter, ...) as fast as the host takes them. */
uint32_t stream_bench(unsigned seconds, unsigned mode)
{
    const uint32_t second = CPU_CYCLES_PER_SECOND;
    uint32_t sent = 0, counter = 0;
    uint32_t second_start = cpu_cycles(), last_progress = second_start;
    unsigned elapsed = 0;
    uint8_t block[USB_PACKET];
    while (!serial_rx_pending()) {
        uint32_t now = cpu_cycles();
        if (now - second_start >= second) {
            second_start += second;
            if (++elapsed == seconds)
                break;
        }
        if (now - last_progress > DRAIN_TIMEOUT_CYCLES * 5)
            break;
        block[0] = 0xBE;
        block[1] = 0xEF;
        put16(block + 2, counter);
        for (unsigned i = 4; i < USB_PACKET; i++)
            block[i] = (uint8_t)(i * 3 + counter);
        int sent_one = mode == 0 ? usb_send_packet(block, USB_PACKET)
                       : mode == 1 ? usb_send_polled(block, USB_PACKET)
                                   : usb_send_free(block, USB_PACKET);
        if (sent_one) {
            counter++;
            sent += USB_PACKET;
            last_progress = cpu_cycles();
        }
    }
    return sent;
}
