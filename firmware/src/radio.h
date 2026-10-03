/* Receiver: PHY calibration, LO tuning, the ADC IQ path and its settings. */
#pragma once

#include <stdint.h>

/* Settings at boot. */
#define RADIO_LO_HZ 2440000000u /* LO frequency; the stream covers LO +-40 MHz */
#define RADIO_GAIN 24           /* receive gain selector (not dB) */

/*
 * Calibrates the PHY, tunes the RF PLL and routes baseband IQ to the sample
 * dump engine with the boot settings: 80 Msps, 40 MHz analog width, RC
 * filter code 0 and both gain stages from the gain selector. Returns one of
 * ESP_RADIO_* from control.h.
 */
unsigned radio_init(void);

/*
 * Changes one setting (op ESP_SET_*, value in its control.h encoding) with
 * the dump engine stopped, and reconfigures the receive path. Returns a
 * CTL_* status; *effective is the value now in effect.
 */
unsigned radio_set(unsigned op, uint32_t value, uint32_t *effective);

/* Settings in effect, indexed by ESP_STAT_LO_HZ..ESP_STAT_SDM_WORD; 0 otherwise. */
uint32_t radio_stat(unsigned index);

#ifdef ESPDR_TXTEST
/*
 * RESEARCH ONLY (docs/TX-RESEARCH.md): transmits an unmodulated carrier at lo_khz for `ms` milliseconds with the PHY's
 * test-tone setting `g` (larger = weaker; the frontend's gain field is -g), then restores the receiver. Limited to
 * TX_MIN_KHZ..TX_MAX_KHZ, g >= TX_MIN_G and ms <= 5000. *info receives the PLL's sigma-delta word as read back with the
 * carrier on. Returns a CTL_* status.
 */
#define TX_MIN_KHZ 2320000u
#define TX_MAX_KHZ 2400000u
#define TX_MIN_G 64u
unsigned radio_tx_test(uint32_t lo_khz, unsigned g, unsigned ms, uint32_t *info);

/*
 * Same, but the carrier is moved by writing I = amp*cos, Q = amp*sin of a numerically controlled oscillator at offset_hz
 * through start_tx_tone_step() at rate_hz (CPU-timed loop; amp <= TX_MAX_AMP). *info receives the number of updates that
 * could not be written on time; the number written is ms * rate_hz / 1000.
 */
#define TX_MAX_AMP 1000u

/*
 * A ladder of static states for mapping the frontend registers: each state is (a, i, b, q) as in start_tx_tone_step(a, i*4,
 * g, b, q*4, 0), held for hold_ms. states[n] = a | b << 6 | (i & 0x3FF) << 12 | (q & 0x3FF) << 22 (a, b 0..63, i, q signed
 * 10 bit). *info receives the number of states run.
 */
#define TX_MAX_STATES 16u

/*
 * Stage A of the SSB plan: the carrier's gain field alone. Bits 17:10 of the frontend register 0x60006040 hold (-g) & 0xFF (smaller g
 * = stronger); start_tx_tone_step() sets it, and here it is rewritten directly (read-modify-write of that field only). Never stronger
 * than g = TX_GAIN_MIN_G, the level of every test so far.
 *   mode 0 ladder: g runs from `a` to `b` in steps of `c` (signed), each held `d` ms.
 *   mode 1 square: g alternates between `a` and `b`, c times a second, for d ms in total.
 * *info receives the number of gain writes.
 */
#define TX_GAIN_MIN_G 64u
unsigned radio_tx_gain(uint32_t lo_khz, unsigned mode, unsigned a, unsigned b, int c, unsigned d, uint32_t *info);

/*
 * SSB stage A, step 2: other amplitude controls. radio_tx_regs() starts the carrier (start_tx_tone_step(1, 0, g, 0, 0, 0)) and copies
 * the frontend registers 0x60006000..0x60006060 (25 words) to tx_regs[] (read-only). radio_tx_backoff() does the same start and then
 * calls the PHY's target_power_backoff(b) for b = b0..b1 in steps of `step`, each held hold_ms; *info receives the CPU cycles one call took.
 */
extern uint32_t tx_regs[25];
unsigned radio_tx_regs(uint32_t lo_khz, unsigned g, uint32_t *info);
unsigned radio_tx_backoff(uint32_t lo_khz, unsigned g, int b0, int b1, int step, unsigned hold_ms, uint32_t *info);

/*
 * SSB by polar modulation: n updates of two bytes each at TX_AUDIO_BASE, played at rate_hz: a signed word delta (in 458.8 Hz steps, the
 * host has done the noise shaping) and a gain code g (clamped to 64..127, the smooth weak branch of the gain field). The carrier
 * starts at g = 127 with 20 ms of plain carrier.
 * (see also loops and drift_hz below) *info receives the number of updates that were late.
 */
unsigned radio_tx_ssb(uint32_t lo_khz, uint32_t rate_hz, const uint8_t *buf, uint32_t n, unsigned loops, int drift_hz,
                      uint32_t *info);
/* loops: the buffer is played that many times in a row (<= 30 s in all). drift_hz: a thermal frequency drift to cancel, decaying with the
 * time constant 32768 / rate_hz seconds from switch-on (0.82 s at 40 kHz); the correction is added to the word deltas here, once, so that
 * looped passes do not repeat it. */

/*
 * Frequency modulation by recorded audio: the signed 8-bit samples at buf[0..n) (full scale 127 = dev_hz deviation) are played once at
 * rate_hz through the same PLL-word update as radio_tx_fm(). The audio sits in the unused capture banks 0..2 (TX_AUDIO_BASE), the
 * host fills it first. The samples are `up` (1 or 2) times fewer than the updates: with 2 each one is linearly interpolated.
 * Second-order error feedback. n * up / rate_hz <= 12 s. *info receives the number of samples played.
 */
#define TX_AUDIO_BASE 0x3FCB0000u
#define TX_AUDIO_MAX (3u * 65536u)
unsigned radio_tx_audio(uint32_t lo_khz, unsigned g, uint32_t dev_hz, uint32_t rate_hz, unsigned up, const int8_t *buf,
                        uint32_t n, uint32_t *info);

/*
 * Frequency modulation by a sine tone: the PLL word follows dev_hz * sin(2 pi tone_hz t), updated rate_hz times per second
 * (<= 40000) with first-order error feedback (the word is 457.8 Hz per step, so plain rounding would be coarse). Only the low
 * byte of the word is written, so the swing must stay inside it; dev_hz <= 20000. *info receives the number of updates.
 */
unsigned radio_tx_fm(uint32_t lo_khz, unsigned g, unsigned ms, uint32_t tone_hz, uint32_t dev_hz, uint32_t rate_hz,
                     uint32_t *info);

/*
 * Frequency-shift keying through the PLL's sigma-delta word: the carrier of radio_tx_test() jumps between lo_khz and lo_khz +
 * dev_hz (<= 20 kHz) toggle_hz times per second (<= 5000), for ms. The word is 30 MHz / 65536 = 457.8 Hz per step. *info
 * receives the number of word updates; status as above. fast: write only the low byte of the word (no bracket).
 */
unsigned radio_tx_fsk(uint32_t lo_khz, unsigned g, unsigned ms, uint32_t dev_hz, uint32_t toggle_hz, unsigned fast,
                      uint32_t *info);
unsigned radio_tx_ladder(uint32_t lo_khz, unsigned g, const uint32_t *states, unsigned count, unsigned hold_ms,
                         uint32_t *info);
unsigned radio_tx_nco(uint32_t lo_khz, unsigned g, unsigned ms, uint32_t offset_hz, uint32_t rate_hz, unsigned amp,
                      uint32_t *info);
#endif

/* The dump engine's control word for the selected sample rate (not running),
 * and the pairs it writes per 16 MHz system timer tick. */
uint32_t radio_dump_control(void);
unsigned radio_pairs_per_tick(void);
