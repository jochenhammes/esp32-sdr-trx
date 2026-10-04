/*
 * Receiver bring-up and settings.
 *
 * The vendor PHY library performs power-up calibration. Everything after it
 * is done here directly: the RF PLL is tuned by software, the Wi-Fi AGC is
 * disabled, the receive gain is forced, and the analog gain stages are owned
 * through the PBUS interface. No transmit path is ever enabled.
 *
 * A setting change hands the gain stages back to the hardware and repeats
 * the whole receive configuration, the sequence every setting was
 * characterised with. It runs between captures only: the ROM's analog
 * register helpers keep their state in capture bank 3, which capture.c
 * restores after every run.
 */
#include "radio.h"
#include "lo_plan.h"

#include <stdbool.h>
#include <string.h>

#include "control.h"
#include "esp_phy_init.h"
#include "esp_rom_regi2c.h"
#include "hal/clk_gate_ll.h"
#include "phy_init_data.h"
#include "platform.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/sens_struct.h"
#include "soc/syscon_reg.h"
#include "soc/system_reg.h"

#define PBUS_TIMEOUT_CYCLES 24000u
#define IQ_FIELDS 0x1FFF0000u  /* I/Q correction: amplitude 20:16, phase 26:21, mode 28:27 */
#define IQ_MANUAL 0x08000000u  /* bit 27 set, bit 28 clear: the fields apply */

extern int register_chipv7_phy(const esp_phy_init_data_t *init, esp_phy_calibration_data_t *cal,
                               esp_phy_calibration_mode_t mode);
extern void phy_bbpll_en_usb(bool enable);
extern void phy_init_param_set(uint8_t param);
extern unsigned rom_pbus_rd(unsigned block, unsigned index);

/* Requested settings (control.h encodings). */
static struct {
    uint32_t lo_hz;
    unsigned rate, width, filter, gain;
    unsigned rf_gain, bb_gain;  /* or ESP_AUTO */
    unsigned dc[4];             /* or ESP_DC_AUTO */
    unsigned iq;                /* amplitude | phase << 8, or ESP_AUTO */
} settings = {
    .lo_hz = RADIO_LO_HZ,
#ifdef ESPDR_NARROWBAND
    .rate = ESP_RATE_16M, .width = 20, /* the on-chip decimator needs the 16 Msps dump */
#else
    .rate = ESP_RATE_80M, .width = 40,
#endif
    .filter = 0, .gain = RADIO_GAIN,
    .rf_gain = ESP_AUTO, .bb_gain = ESP_AUTO,
    .dc = {ESP_DC_AUTO, ESP_DC_AUTO, ESP_DC_AUTO, ESP_DC_AUTO}, .iq = ESP_AUTO,
};

/* The receiver as configured. */
static struct {
    unsigned status;                /* ESP_RADIO_* */
    uint32_t lo_hz, pll_hz;         /* nominal effective LO / normal PLL coordinate */
    uint32_t sdm_word;
    enum esp32s3_lo_mode lo_mode;
    unsigned pll_cap, pll_first, pll_length;
    bool owned;                     /* gain stages held through the PBUS */
    uint32_t pbus_ctrl, pbus_mode;  /* before taking them */
    uint32_t iq_register;           /* before a manual I/Q correction */
    unsigned rf_gain, bb_gain;      /* stage words written */
    unsigned dc[4], dc_hardware[4]; /* codes in effect, and as the hardware set them */
} receiver;

/* DC offset registers 0..3 (ESP_SET_DC) as PBUS block and index. */
static const uint8_t dc_block[4] = {3, 3, 2, 2}, dc_index[4] = {1, 2, 1, 2};

/* ---- analog register access ---------------------------------------------- */

static uint8_t analog_read(uint8_t block, uint8_t reg) { return esp_rom_regi2c_read(block, 1, reg); }

static void analog_write(uint8_t block, uint8_t reg, uint8_t value)
{
    esp_rom_regi2c_write(block, 1, reg, value);
}

static void analog_write_bits(uint8_t block, uint8_t reg, uint8_t mask, uint8_t value)
{
    uint8_t old = analog_read(block, reg);
    analog_write(block, reg, (uint8_t)((old & ~mask) | (value & mask)));
}

/* ---- PHY power-up and calibration ------------------------------------------ */

static void power_up_modem(void)
{
    REG(RTC_CNTL_DIG_PWC_REG) &= ~RTC_CNTL_WIFI_FORCE_PD;
    delay_us(10);
    periph_ll_wifi_bt_module_enable_clk();
    REG(SYSCON_WIFI_RST_EN_REG) |= MODEM_RESET_FIELD_WHEN_PU;
    REG(SYSCON_WIFI_RST_EN_REG) &= ~MODEM_RESET_FIELD_WHEN_PU;
    REG(RTC_CNTL_DIG_ISO_REG) &= ~RTC_CNTL_WIFI_FORCE_ISO;
    periph_ll_wifi_bt_module_disable_clk();

    /* The sample dump engine needs the Wi-Fi MAC clock (bit 6), which the
     * public clock-gate mask does not include. */
    periph_ll_enable_clk_clear_rst(PERIPH_WIFI_MODULE);
    REG(SYSTEM_WIFI_CLK_EN_REG) |= 1u << 6;
}

static bool calibrate_phy(void)
{
    periph_ll_wifi_bt_module_enable_clk();
    periph_ll_phy_calibration_module_enable_clk_clear_rst();
    periph_ll_enable_clk_clear_rst(PERIPH_RNG_MODULE);
    periph_ll_wifi_module_enable_clk_clear_rst();
    periph_ll_enable_clk_clear_rst(PERIPH_BT_MODULE);
    phy_init_param_set(1);
    phy_bbpll_en_usb(true);

    static esp_phy_calibration_data_t calibration;
    memset(&calibration, 0, sizeof(calibration));
    read_mac(calibration.mac);
    int result = register_chipv7_phy(&phy_init_data, &calibration, PHY_RF_CAL_FULL);

    /* The PHY and RNG clocks stay on: the dump registers stop responding
     * without them. The Bluetooth clock is not needed. */
    periph_ll_disable_clk_set_rst(PERIPH_BT_MODULE);

    /* A full calibration from an empty record reports 1 (stored data
     * invalid), which the SDK also treats as success. */
    return result == 0 || result == 1;
}

/* ---- RF PLL ------------------------------------------------------------------ */

static void set_pll_capacitor(unsigned cap)
{
    analog_write(I2C_RFPLL, 1, (uint8_t)cap);
    analog_write_bits(I2C_RFPLL, 2, 0x10, (uint8_t)((cap >> 8) << 4));
}

static void set_pll_manual_capacitor(bool manual)
{
    analog_write_bits(I2C_RFPLL, 11, 0x40, manual ? 0x40 : 0);
}

/*
 * Programs the sigma-delta word, waits for the PLL's own calibration, then
 * scans all 512 VCO capacitor codes and pins the capacitor to the middle of
 * the longest run of codes the PLL reports as locked.
 */
static bool tune_pll(uint32_t lo_hz)
{
    struct esp32s3_lo_plan plan;
    if (!esp32s3_plan_lo(lo_hz, ESP32S3_LO_AUTO, &plan))
        return false;
    uint32_t word = plan.sdm_word;
    receiver.lo_hz = plan.lo_hz;
    receiver.pll_hz = plan.pll_hz;
    receiver.sdm_word = word;
    receiver.lo_mode = plan.mode;

    /* Calibrate in normal conversion, as in the external-tone tests.
     * The final receive mode is applied after configure_receiver(). */
    analog_write_bits(ESP32S3_CKGEN_BLOCK, ESP32S3_CKGEN_REG, ESP32S3_CKGEN_5_6_BIT, 0);

    REG(RFPLL_OWNER_REG) |= 1u << 25;
    set_pll_manual_capacitor(false);
    analog_write(I2C_SDM, 0, 0x07);
    analog_write(I2C_SDM, 3, (uint8_t)(word >> 16));
    analog_write(I2C_SDM, 4, (uint8_t)(word >> 8));
    analog_write(I2C_SDM, 5, (uint8_t)word);
    analog_write(I2C_SDM, 0, 0x17);

    /* Restart calibration and wait for it to report done. */
    analog_write_bits(I2C_RFPLL, 0, 0x40, 0x40);
    analog_write_bits(I2C_RFPLL, 0, 0x20, 0x00);
    analog_write_bits(I2C_RFPLL, 0, 0x20, 0x20);
    analog_write_bits(I2C_RFPLL, 0, 0x40, 0x00);
    bool calibrated = false;
    for (unsigned poll = 0; poll < 100 && !calibrated; poll++) {
        delay_us(20);
        calibrated = analog_read(I2C_RFPLL, 7) & 2;
    }
    if (!calibrated)
        return false;
    delay_us(5);

    uint8_t saved_low = analog_read(I2C_RFPLL, 1);
    uint8_t saved_high = analog_read(I2C_RFPLL, 2);
    uint8_t saved_mode = analog_read(I2C_RFPLL, 11);
    set_pll_manual_capacitor(true);
    unsigned run_start = 0, run_length = 0, best_start = 0, best_length = 0;
    for (unsigned cap = 0; cap < 512; cap++) {
        set_pll_capacitor(cap);
        delay_us(20);
        bool locked = ((analog_read(I2C_RFPLL, 12) >> 2) & 3) == 0;
        if (!locked) {
            run_length = 0;
            continue;
        }
        if (run_length++ == 0)
            run_start = cap;
        if (run_length > best_length) {
            best_start = run_start;
            best_length = run_length;
        }
    }
    analog_write(I2C_RFPLL, 1, saved_low);
    analog_write_bits(I2C_RFPLL, 2, 0x10, saved_high);
    analog_write_bits(I2C_RFPLL, 11, 0x40, saved_mode);
    if (!best_length)
        return false;

    receiver.pll_first = best_start;
    receiver.pll_length = best_length;
    receiver.pll_cap = best_start + (best_length - 1) / 2;
    set_pll_capacitor(receiver.pll_cap);
    set_pll_manual_capacitor(true);
    return true;
}

/* ---- receive path -------------------------------------------------------------- */

/* Write one analog register through the PBUS interface. */
static bool pbus_write(unsigned block, unsigned index, unsigned value)
{
    uint32_t fields = ((value & 511u) << 6) | ((block & 15u) << 2) | ((index & 3u) << 15);
    REG(PBUS_CTRL_REG) = (REG(PBUS_CTRL_REG) & 0xFFFE0001u) | (fields & 0x1FFFCu) | 2u;
    uint32_t start = cpu_cycles();
    while (REG(PBUS_STATUS_REG) & 0x80000000u) {
        if (cpu_cycles() - start > PBUS_TIMEOUT_CYCLES) {
            REG(PBUS_CTRL_REG) &= ~2u;
            return false;
        }
    }
    REG(PBUS_CTRL_REG) &= ~2u;
    return true;
}

static void set_width(unsigned mhz)
{
    bool wide = mhz == 40;
    REG(FE_WIDTH_REG) = (REG(FE_WIDTH_REG) & ~0x003F0000u) | (wide ? 0x00120000u : 0);
    REG(BB_ENABLE_REG) = (REG(BB_ENABLE_REG) & ~0xCu) | (wide ? 0x4u : 0);
}

/* Dump engine stopped, RX forces and baseband off. */
static void park_receiver(void)
{
    REG(DUMP_CTRL_REG) &= ~DUMP_CTRL_RUN;
    REG(DUMP_BANK_SELECT_REG) &= ~15u;
    REG(AGC_RX_FORCE_REG) &= ~0xC1u;
    REG(PBUS_STATUS_REG) &= ~0xCF00u;
    REG(BB_ENABLE_REG) &= ~2u;
}

/* Parks the receiver and hands the gain stages, DC offsets and I/Q
 * correction back to the hardware as configure_receiver found them. */
static bool release_receiver(void)
{
    park_receiver();
    if (!receiver.owned)
        return true;
    bool ok = true;
    for (unsigned r = 0; r < 4; r++)
        if (receiver.dc[r] != receiver.dc_hardware[r])
            ok = pbus_write(dc_block[r], dc_index[r], receiver.dc_hardware[r]) && ok;
    ok = pbus_write(0, 1, 0) && pbus_write(1, 1, 0) && pbus_write(1, 2, 0) && ok;
    REG(PBUS_CTRL_REG) = receiver.pbus_ctrl;
    REG(PBUS_MODE_REG) = receiver.pbus_mode;
    REG(IQ_CORRECTION_REG) = receiver.iq_register;
    receiver.owned = false;
    return ok;
}

static bool configure_receiver(void)
{
    park_receiver();

    /* The width selects an analog RC bank when the baseband is enabled, so
     * it is programmed before the enable edge. */
    set_width(settings.width);

    /* Enable the baseband, disable the Wi-Fi AGC and force the RX gain. */
    REG(BB_ENABLE_REG) |= 0x10000000u;
    REG(BB_ENABLE_REG) &= ~2u;
    delay_us(1);
    REG(BB_ENABLE_REG) |= 2u;
    REG(AGC_CTRL_REG) = (REG(AGC_CTRL_REG) & 0xFF00FFFFu) | 0x007F0000u;
    REG(AGC_DISABLE_REG) |= 0x80u;
    REG(AGC_RX_FORCE_REG) |= 1u;
    REG(AGC_GAIN_FORCE_REG) =
        (REG(AGC_GAIN_FORCE_REG) & 0x007FFFFFu) | (settings.gain << 24) | 0x00800000u;
    REG(PBUS_STATUS_REG) |= 0xC000u;

    /* Baseband RC filter: registers 6/7 serve 40 MHz, 4/5 serve 20 MHz. */
    set_width(settings.width);
    unsigned filter = settings.width == 40 ? 6 : 4;
    analog_write(I2C_BB_FILTER, filter, settings.filter & 63);
    analog_write(I2C_BB_FILTER, filter + 1, (settings.filter >> 8) & 63);

    /* Let the forced gain settle, capture the gain stages it selected, then
     * take PBUS ownership and hold them there with the baseband disabled. */
    delay_us(100);
    unsigned bb = (REG(PBUS_BB_GAIN_REG) >> 9) & 511;
    unsigned rf = (REG(PBUS_RF_GAIN_REG) >> 18) & 511;
    if (settings.bb_gain != ESP_AUTO)
        bb = 0x180 | settings.bb_gain;
    if (settings.rf_gain != ESP_AUTO)
        rf = settings.rf_gain;
    receiver.pbus_ctrl = REG(PBUS_CTRL_REG);
    receiver.pbus_mode = REG(PBUS_MODE_REG);
    receiver.iq_register = REG(IQ_CORRECTION_REG);
    for (unsigned r = 0; r < 4; r++)
        receiver.dc[r] = receiver.dc_hardware[r] = 0;
    REG(PBUS_MODE_REG) &= ~0x08000000u;
    REG(PBUS_CTRL_REG) |= 1u;
    receiver.owned = true;
    REG(BB_ENABLE_REG) &= ~2u;

    /* Receive-only power configuration: both TX groups stay off. */
    bool ok = pbus_write(4, 1, 0) && pbus_write(5, 1, 0) && pbus_write(0, 1, 0x184) &&
              pbus_write(1, 1, 0x189) && pbus_write(1, 2, rf) && pbus_write(0, 1, bb);
    receiver.rf_gain = rf;
    receiver.bb_gain = bb;
    for (unsigned r = 0; r < 4 && ok; r++) {
        receiver.dc[r] = receiver.dc_hardware[r] = rom_pbus_rd(dc_block[r], dc_index[r]) & 511;
        if (settings.dc[r] != ESP_DC_AUTO && settings.dc[r] != receiver.dc[r]) {
            ok = pbus_write(dc_block[r], dc_index[r], settings.dc[r]);
            receiver.dc[r] = settings.dc[r];
        }
    }
    if (settings.iq != ESP_AUTO)
        REG(IQ_CORRECTION_REG) = (receiver.iq_register & ~IQ_FIELDS) | IQ_MANUAL |
                                 ((settings.iq & 31u) << 16) | (((settings.iq >> 8) & 63u) << 21);
    if (!ok)
        return false;

    REG(DUMP_CONFIG_REG) = DUMP_CONFIG_IQ;
    delay_us(100);
    REG(DUMP_CTRL_REG) = radio_dump_control();
    REG(DUMP_BANK_SELECT_REG) = (REG(DUMP_BANK_SELECT_REG) & ~15u) | 1u;
    return true;
}

static unsigned reconfigure(bool retune)
{
    if (!release_receiver())
        return ESP_RADIO_PBUS_FAILED;
    if (retune && !tune_pll(settings.lo_hz))
        return ESP_RADIO_PLL_FAILED;
    if (!configure_receiver())
        return ESP_RADIO_PBUS_FAILED;
    /* Preserve every other CKGEN bit. On the tested PHY baseline this is
     * 0x63 -> 0x73 for 5/6, or back to 0x63 for normal conversion.
     * Reapply after every setting change, including failed-tune recovery. */
    uint8_t mode = receiver.lo_mode == ESP32S3_LO_5_6 ? ESP32S3_CKGEN_5_6_BIT : 0;
    analog_write_bits(ESP32S3_CKGEN_BLOCK, ESP32S3_CKGEN_REG, ESP32S3_CKGEN_5_6_BIT, mode);
    delay_us(3000);
    if ((analog_read(ESP32S3_CKGEN_BLOCK, ESP32S3_CKGEN_REG) & ESP32S3_CKGEN_5_6_BIT) != mode)
        return ESP_RADIO_PLL_FAILED;
    return ESP_RADIO_OK;
}

unsigned radio_init(void)
{
    power_up_modem();
    if (!calibrate_phy())
        receiver.status = ESP_RADIO_PHY_FAILED;
    else
        receiver.status = reconfigure(true);
    return receiver.status;
}

uint32_t radio_dump_control(void)
{
    return DUMP_CTRL_CIRCULAR | (settings.rate == ESP_RATE_16M ? DUMP_CTRL_16MSPS : 0);
}

unsigned radio_pairs_per_tick(void) { return settings.rate == ESP_RATE_16M ? 1 : 5; }

static bool valid_setting(unsigned op, uint32_t value)
{
    switch (op) {
    case ESP_SET_LO: return value >= ESP_LO_MIN_HZ && value <= ESP_LO_MAX_HZ;
    case ESP_SET_RATE: return value == ESP_RATE_80M || value == ESP_RATE_16M;
    case ESP_SET_WIDTH: return value == 20 || value == 40;
    case ESP_SET_FILTER: return !(value & ~0x3F3Fu);
    case ESP_SET_GAIN: return value <= 127;
    case ESP_SET_RF_GAIN: return value <= 511 || value == ESP_AUTO;
    case ESP_SET_BB_GAIN: return value <= 127 || value == ESP_AUTO;
    case ESP_SET_DC: return value >> 12 <= 3 && ((value & 0xFFF) <= 511 || (value & 0xFFF) == ESP_DC_AUTO);
    case ESP_SET_IQ: return !(value & ~0x3F1Fu) || value == ESP_AUTO;
    default: return false;
    }
}

unsigned radio_set(unsigned op, uint32_t value, uint32_t *effective)
{
    if (!valid_setting(op, value))
        return op >= ESP_SET_LO && op <= ESP_SET_IQ ? CTL_BAD_ARGUMENT : CTL_UNKNOWN_OP;
    if (receiver.status == ESP_RADIO_PHY_FAILED)
        return CTL_NOT_READY;

    typeof(settings) previous = settings;
    unsigned stat = 0;
    switch (op) {
    case ESP_SET_LO: settings.lo_hz = value; stat = ESP_STAT_LO_HZ; break;
    case ESP_SET_RATE: settings.rate = value; stat = ESP_STAT_RATE; break;
    case ESP_SET_WIDTH: settings.width = value; stat = ESP_STAT_WIDTH; break;
    case ESP_SET_FILTER: settings.filter = value; stat = ESP_STAT_FILTER; break;
    case ESP_SET_GAIN: settings.gain = value; stat = ESP_STAT_GAIN; break;
    case ESP_SET_RF_GAIN: settings.rf_gain = value; stat = ESP_STAT_RF_GAIN; break;
    case ESP_SET_BB_GAIN: settings.bb_gain = value; stat = ESP_STAT_BB_GAIN; break;
    case ESP_SET_DC: settings.dc[value >> 12] = value & 0xFFF; stat = ESP_STAT_DC0 + (value >> 12); break;
    case ESP_SET_IQ: settings.iq = value; stat = ESP_STAT_IQ; break;
    }
    /* A retune also follows a failed one, whose PLL state is unknown. */
    bool retune = settings.lo_hz != previous.lo_hz || receiver.status == ESP_RADIO_PLL_FAILED;
    receiver.status = reconfigure(retune);
    if (receiver.status != ESP_RADIO_OK) {
        settings = previous;
        receiver.status = reconfigure(true);
        return CTL_FAILED;
    }
    *effective = radio_stat(stat);
    return CTL_OK;
}

uint32_t radio_stat(unsigned index)
{
    switch (index) {
    case ESP_STAT_RADIO: return receiver.status;
    case ESP_STAT_LO_HZ: return receiver.lo_hz;
    case ESP_STAT_RATE: return settings.rate;
    case ESP_STAT_WIDTH: return settings.width;
    case ESP_STAT_FILTER: return settings.filter;
    case ESP_STAT_GAIN: return settings.gain;
    case ESP_STAT_RF_GAIN: return receiver.rf_gain;
    case ESP_STAT_BB_GAIN: return receiver.bb_gain & 127;
    case ESP_STAT_DC0:
    case ESP_STAT_DC1:
    case ESP_STAT_DC2:
    case ESP_STAT_DC3: return receiver.dc[index - ESP_STAT_DC0];
    case ESP_STAT_IQ: {
        uint32_t iq = REG(IQ_CORRECTION_REG);
        return ((iq >> 16) & 31) | (((iq >> 21) & 63) << 8);
    }
    case ESP_STAT_AUTOMATIC: {
        uint32_t automatic = (settings.rf_gain == ESP_AUTO ? 1 : 0) | (settings.bb_gain == ESP_AUTO ? 2 : 0) |
                             (settings.iq == ESP_AUTO ? 64 : 0);
        for (unsigned r = 0; r < 4; r++)
            if (settings.dc[r] == ESP_DC_AUTO)
                automatic |= 4u << r;
        return automatic;
    }
    case ESP_STAT_PLL: return receiver.pll_cap | (receiver.pll_first << 9) | (receiver.pll_length << 18);
    case ESP_STAT_LO_MODE: return receiver.lo_mode;
    case ESP_STAT_PLL_HZ: return receiver.pll_hz;
    case ESP_STAT_SDM_WORD: return receiver.sdm_word;
    default: return 0;
    }
}

/* ---- hooks required by the vendor PHY library ---------------------------------- */

uint32_t phy_enter_critical(void)
{
    uint32_t ps;
    __asm__ volatile("rsil %0, 15" : "=a"(ps)::"memory");
    return ps;
}

void phy_exit_critical(uint32_t ps) { __asm__ volatile("wsr %0, ps; rsync" ::"a"(ps) : "memory"); }

int phy_printf(const char *format, ...)
{
    (void)format;
    return 0;
}

void coex_pti_print(void) {}

int64_t esp_timer_get_time(void) { return (int64_t)(timer_ticks() / 16); }

#ifdef ESPDR_TX
/* ---- transmitter (experimental) ---------------------------------------------------------------- */
#include "transmit.h"

extern void txcal_debuge_mode(void);
extern void txcal_work_mode(void);
extern void start_tx_tone_step(int a, int i, int g, int b, int q, int h);

#define TX_FRONTEND_I_REG (*(volatile uint32_t *)0x60006040u)
#define TX_RING_BASE 0x3FCB0000u /* capture banks 0..2; bank 3 holds the ROM's working memory */
#define TX_RING_RECORDS (TX_RING_BYTES / TX_RECORD_BYTES)
#define TX_WATCHDOG_CYCLES 120000000u /* 500 ms */
#define TX_CARRIER_LEAD_US 20000u
#define TX_STATUS_PERIOD_US 5000u

static struct {
    uint32_t lo_hz, rate_hz, limit_s;
    int32_t drift_hz;
    uint32_t word;
    bool prepared;
    int32_t max_steps;
} tx = {.rate_hz = 40000, .limit_s = 600, .max_steps = TX_MAX_STEPS};
#ifdef ESPDR_IQTEST
static bool iq_skip_cal; /* research: IQ_OP_BEGIN bit 0 leaves out txcal_debuge_mode() */
#endif

/* Bits 17:10 of the frontend register hold (-g) & 0xFF: the gain code; smaller g is stronger (0.28 dB per code for g <= 127). */
static inline void tx_set_gain(unsigned g)
{
    TX_FRONTEND_I_REG = (TX_FRONTEND_I_REG & ~(0xFFu << 10)) | (((0u - g) & 0xFFu) << 10);
}

unsigned radio_tx_set(unsigned op, uint32_t arg)
{
    switch (op) {
    case TX_OP_LO:
        if (arg < TX_MIN_HZ / 100u || arg > TX_MAX_HZ / 100u)
            return CTL_BAD_ARGUMENT;
        tx.lo_hz = arg * 100u;
        return CTL_OK;
    case TX_OP_RATE:
        if (arg < 8000 || arg > 40000)
            return CTL_BAD_ARGUMENT;
        tx.rate_hz = arg;
        return CTL_OK;
    case TX_OP_DRIFT:
        tx.drift_hz = (int16_t)arg;
        return tx.drift_hz >= -2000 && tx.drift_hz <= 2000 ? CTL_OK : CTL_BAD_ARGUMENT;
    case TX_OP_LIMIT:
        if (arg < 1 || arg > 3600)
            return CTL_BAD_ARGUMENT;
        tx.limit_s = arg;
        return CTL_OK;
    case TX_OP_RANGE:
        if (arg < TX_MAX_STEPS || arg > TX_MAX_STEPS_WIDE)
            return CTL_BAD_ARGUMENT;
        tx.max_steps = (int32_t)arg;
        return CTL_OK;
    default:
        return CTL_UNKNOWN_OP;
    }
}


/* The chip's temperature sensor (part of the SAR ADC on the S3), driven as the SDK's sar_periph_ctrl does. 16 raw readings are summed. */
uint32_t radio_tx_temp(unsigned range)
{
    static const uint8_t dac[5] = {5, 7, 15, 11, 10};         /* I2C_SARADC_TSENS_DAC of the ranges (temperature_sensor_attributes) */
    if (range > 4)
        range = 2;
    REG(0x6000E044u) &= ~(1u << 18);                            /* ANA_CONFIG_REG, I2C_SAR_M: the I2C bus to the SAR block on */
    REG(0x6000E048u) |= (1u << 16);                             /* ANA_CONFIG2_REG, ANA_SAR_CFG2_M */
    REG(SYSTEM_PERIP_CLK_EN0_REG) |= SYSTEM_APB_SARADC_CLK_EN;
    REG(SYSTEM_PERIP_RST_EN0_REG) |= SYSTEM_APB_SARADC_RST;
    REG(SYSTEM_PERIP_RST_EN0_REG) &= ~SYSTEM_APB_SARADC_RST;
    SENS.sar_peri_clk_gate_conf.tsens_clk_en = 1;
    SENS.sar_peri_reset_conf.tsens_reset = 1;
    SENS.sar_peri_reset_conf.tsens_reset = 0;
    SENS.sar_tctrl.tsens_power_up_force = 1;
    SENS.sar_tctrl2.tsens_xpd_force = 1;
    SENS.sar_tctrl.tsens_power_up = 1;
    analog_write_bits(0x69, 6, 0x0F, dac[range]);               /* I2C_SAR_ADC, I2C_SARADC_TSENS_DAC */
    delay_us(400);
    uint32_t sum = 0;
    for (unsigned k = 0; k < 16; k++) {
        REG(0x60008850u) |= 1u << 24;                           /* tsens_dump_out */
        uint32_t t = cpu_cycles();
        while (!(REG(0x60008850u) & (1u << 8)) && cpu_cycles() - t < 240000u)       /* tsens_ready */
            ;
        REG(0x60008850u) &= ~(1u << 24);
        sum += REG(0x60008850u) & 0xFFu;                        /* tsens_out */
        delay_us(50);
    }
    return (range << 24) | sum;
}

unsigned radio_tx_begin(uint32_t *word)
{
    struct esp32s3_lo_plan plan;
    tx.prepared = false;
    if (tx.lo_hz < TX_MIN_HZ || tx.lo_hz > TX_MAX_HZ || !esp32s3_plan_lo(tx.lo_hz, ESP32S3_LO_NORMAL, &plan))
        return CTL_BAD_ARGUMENT;
    *word = plan.sdm_word;
    unsigned low = plan.sdm_word & 0xFF;
    if ((int)low < tx.max_steps + 4 || (int)low > 255 - (tx.max_steps + 4))
        return CTL_BAD_ARGUMENT; /* the host moves the LO by up to 22 kHz so that the offsets never carry into the next byte */
    if (receiver.status != ESP_RADIO_OK)
        return CTL_NOT_READY;
    bool ok = release_receiver() && tune_pll(tx.lo_hz);
    if (ok) {
#ifdef ESPDR_IQTEST
        if (!iq_skip_cal)
#endif
        {
            txcal_debuge_mode(); /* the PHY's own transmit test mode; it may move the PLL, so tune again */
            ok = tune_pll(tx.lo_hz);
        }
    }
    if (!ok) {
        txcal_work_mode();
        receiver.status = reconfigure(true);
        return CTL_FAILED;
    }
    tx.word = plan.sdm_word;
    tx.prepared = true;
    return CTL_OK;
}

/* Takes what the host has sent into the ring (at most one USB packet per call). */
static inline void tx_pull(volatile uint32_t *ring, uint32_t *head, uint32_t tail, uint32_t *stage, unsigned *count, uint32_t *last_rx,
                           uint32_t *overruns)
{
    int c;
    for (unsigned budget = 64; budget && (c = serial_read()) >= 0; budget--) {
        *stage |= (uint32_t)c << (8 * *count);
        *last_rx = cpu_cycles();
        if (++*count == TX_RECORD_BYTES) {
            uint32_t next = *head + 1 == TX_RING_RECORDS ? 0 : *head + 1;
            if (next == tail) {
                (*overruns)++; /* the host did not respect the fill level: drop */
            } else {
                ring[*head] = *stage;
                *head = next;
            }
            *stage = 0;
            *count = 0;
        }
    }
}

static inline uint32_t sat8(uint32_t v) { return v > 255 ? 255 : v; }

uint32_t radio_tx_run(void)
{
    volatile uint32_t *ring = (volatile uint32_t *)TX_RING_BASE;
    uint32_t head = 0, tail = 0, stage = 0, overruns = 0, underruns = 0, late = 0, last_rx = cpu_cycles();
    unsigned staged = 0, reason = TX_END_REQUESTED, seq = 0;
    bool ended = false, carrier = false;
    const uint32_t period = 240000000u / tx.rate_hz;
    const uint32_t base = tx.word & 0xFF;
    uint32_t status_due = cpu_cycles();
    uint32_t end_flag_seen = 0;

    /* wait for the prefill (or a short clip that already carries the end record), reporting the fill level meanwhile */
    uint32_t started = cpu_cycles();
    for (;;) {
        tx_pull(ring, &head, tail, &stage, &staged, &last_rx, &overruns);
        uint32_t fill = head >= tail ? head - tail : head + TX_RING_RECORDS - tail;
        if (fill >= TX_PREFILL)
            break;
        if (fill && ((ring[(head ? head : TX_RING_RECORDS) - 1] >> 24) & TX_FLAG_END))
            break;
        if (cpu_cycles() - started > 3u * 240000000u) {
            reason = TX_END_WATCHDOG;
            ended = true;
            break;
        }
        if ((int32_t)(cpu_cycles() - status_due) >= 0) {
            uint8_t f[TX_STATUS_BYTES] = {TX_STATUS_MAGIC, (uint8_t)seq++, (uint8_t)fill, (uint8_t)(fill >> 8), 0, 0, 0, 0};
            f[7] = (uint8_t)(0x100 - (f[0] + f[1] + f[2] + f[3] + f[4] + f[5] + f[6]));
            serial_try_write(f, sizeof(f));
            status_due += TX_STATUS_PERIOD_US * 240u;
        }
    }

    uint32_t played = 0;
    if (!ended) {
        /* key the carrier at the weakest end of the smooth gain branch, with 20 ms of plain carrier before the first record */
        start_tx_tone_step(1, 0, TX_GAIN_WEAKEST, 0, 0, 0);
        carrier = true;
        int32_t c = (int32_t)(-((int64_t)tx.drift_hz << 24) * 65536 / 30000000), cacc = 0; /* drift correction in word steps, Q24 */
        uint32_t lead_end = cpu_cycles() + TX_CARRIER_LEAD_US * 240u;
        while ((int32_t)(cpu_cycles() - lead_end) < 0)
            tx_pull(ring, &head, tail, &stage, &staged, &last_rx, &overruns);
        for (uint32_t i = 0; i < tx.rate_hz / 50u; i++)
            c -= c >> 15;
        int32_t e1 = 0, e2 = 0; /* quantisation errors of the last two updates, Q16 */
        uint32_t record = TX_GAIN_WEAKEST << 16;
        const uint32_t total = tx.limit_s * tx.rate_hz;
        uint32_t next = cpu_cycles() + period;
        uint32_t status_every = tx.rate_hz / 200u, status_count = 0;
        while (!ended) {
            if ((int32_t)(cpu_cycles() - next) > 0)
                late++;
            while ((int32_t)(cpu_cycles() - next) < 0)
                ;
            next += period;
            tx_pull(ring, &head, tail, &stage, &staged, &last_rx, &overruns);
            if (tail != head) {
                record = ring[tail];
                tail = tail + 1 == TX_RING_RECORDS ? 0 : tail + 1;
                if ((record >> 24) & TX_FLAG_END)
                    end_flag_seen = 1;
            } else {
                underruns++;
                if (cpu_cycles() - last_rx > TX_WATCHDOG_CYCLES) {
                    reason = TX_END_WATCHDOG;
                    break;
                }
            }
            unsigned g = (record >> 16) & 0xFF;
            g = g < TX_GAIN_STRONGEST ? TX_GAIN_STRONGEST : g > TX_GAIN_WEAKEST ? TX_GAIN_WEAKEST : g;
            int32_t v = (int32_t)(int16_t)(record & 0xFFFF) * 4096; /* 1/16 step in Q16 */
            cacc += c;
            int32_t wc = (cacc + (1 << 23)) >> 24;
            cacc -= wc << 24;
            c -= c >> 15;
            int32_t u = v - 2 * e1 + e2; /* second-order error feedback: noise transfer function (1 - z^-1)^2 */
            int32_t w = (u + 0x8000) >> 16;
            e2 = e1;
            e1 = w * 65536 - u;
            int32_t dw = w + wc;
            dw = dw > tx.max_steps ? tx.max_steps : dw < -tx.max_steps ? -tx.max_steps : dw;
            tx_set_gain(g);
            analog_write(I2C_SDM, 5, (uint8_t)((int32_t)base + dw));
            played++;
            if (end_flag_seen) {
                ended = true;
                break;
            }
            if (played >= total) {
                reason = TX_END_LIMIT;
                break;
            }
            if (++status_count >= status_every) {
                status_count = 0;
                uint32_t fill = head >= tail ? head - tail : head + TX_RING_RECORDS - tail;
                uint8_t f[TX_STATUS_BYTES] = {TX_STATUS_MAGIC, (uint8_t)seq++, (uint8_t)fill, (uint8_t)(fill >> 8),
                                              (uint8_t)sat8(underruns), (uint8_t)sat8(late), TX_STATUS_CARRIER, 0};
                f[7] = (uint8_t)(0x100 - (f[0] + f[1] + f[2] + f[3] + f[4] + f[5] + f[6]));
                serial_try_write(f, sizeof(f));
            }
        }
    }

    if (carrier) {
        analog_write(I2C_SDM, 5, (uint8_t)base);
        start_tx_tone_step(0, 0, 0, 0, 0, 0);
    }
    txcal_work_mode();
    receiver.status = reconfigure(true);
    tx.prepared = false;
    tx.max_steps = TX_MAX_STEPS;
    if (receiver.status != ESP_RADIO_OK)
        reason = TX_END_FAILED;
    (void)overruns;
    return (uint32_t)reason << 24 | sat8(underruns) << 16 | (late > 0xFFFF ? 0xFFFF : late);
}

#ifdef ESPDR_IQTEST
/* ---- research: DAC playback engine tests (protocol/iqtest.h) ---------------------------------- */
#include "iqtest.h"
#include "hal/dma_types.h"
#include "hal/gdma_ll.h"

extern void phy_txtone_start(int mhz, int offset, int power);
extern int txtone_linear_pwr(void);

static struct {
    bool begun, keyed;
    int32_t rot_cos, rot_sin, rot2_cos, rot2_sin, pre_g, pre_p;
    uint32_t st_g, st_inc, st_inc2;
    uint32_t addr, ms, last_op, play_bits, ld_pos, gap_us, ld_i, ld_tgt;
} iq = {.ms = 100, .play_bits = 16383u | (1u << 15)};

static void iq_end(void)
{
    if (iq.keyed) {
        REG(IQ_DAC_REG) = 0;
        start_tx_tone_step(0, 0, 0, 0, 0, 0);
    }
    if (iq.begun) {
        txcal_work_mode(); /* harmless if the debug mode was never entered (to be confirmed) */
        receiver.status = reconfigure(true);
    }
    iq.begun = iq.keyed = false;
    tx.prepared = false;
}

void radio_iq_watchdog(void)
{
    if ((iq.begun || iq.keyed) && cpu_cycles() - iq.last_op > IQ_IDLE_MS * 240000u)
        iq_end();
}

static uint32_t iq_fill(unsigned amp, unsigned mode)
{
    volatile uint32_t *bank2 = (volatile uint32_t *)IQ_BANK2;
    int64_t zr = (int64_t)amp << 20, zi = 0; /* Q20 */
    int64_t ur = (int64_t)(amp / 2) << 20, ui = 0, vr = (int64_t)(amp / 2) << 20, vi = 0; /* IQ_MODE_TWO */
    for (unsigned n = 0; n < IQ_WORDS; n++) {
        int32_t i = (int32_t)((zr + (1 << 19)) >> 20), q = (int32_t)((zi + (1 << 19)) >> 20);
        if (mode == IQ_MODE_TWO) {
            i = (int32_t)((ur + vr + (1 << 19)) >> 20);
            q = (int32_t)((ui + vi + (1 << 19)) >> 20);
        }
        switch (mode) {
        case IQ_MODE_REAL:
            q = 0;
            break;
        case IQ_MODE_CONST:
            i = (int32_t)amp;
            q = 0;
            break;
        case IQ_MODE_ZERO:
            i = q = 0;
            break;
        case IQ_MODE_RAW:
            bank2[n] = (uint32_t)iq.rot_cos;
            continue;
        default:
            break;
        }
        if (mode == IQ_MODE_TWO) {
            int64_t nu = (ur * iq.rot_cos - ui * iq.rot_sin) >> 30, nv = (ui * iq.rot_cos + ur * iq.rot_sin) >> 30;
            int64_t mu = (vr * iq.rot2_cos - vi * iq.rot2_sin) >> 30, mv = (vi * iq.rot2_cos + vr * iq.rot2_sin) >> 30;
            ur = nu; ui = nv; vr = mu; vi = mv;
        }
        if (mode == IQ_MODE_ROTATOR || mode == IQ_MODE_REAL || mode == IQ_MODE_TWO) {
            int64_t q2 = (((int64_t)q * (65536 + iq.pre_g)) >> 16) + (((int64_t)i * iq.pre_p) >> 16);
            q = q2 > 511 ? 511 : q2 < -512 ? -512 : (int32_t)q2;
        }
        bank2[n] = ((uint32_t)i & 0x3FFu) | (((uint32_t)q & 0x3FFu) << 10);
        int64_t nr = (zr * iq.rot_cos - zi * iq.rot_sin) >> 30, ni = (zr * iq.rot_sin + zi * iq.rot_cos) >> 30;
        zr = nr;
        zi = ni;
    }
    return IQ_WORDS;
}


#ifdef ESPDR_IQ_STREAM /* experiment E1, finished (docs/research/DESIGN-IQ-TX.md); built only with -DESPDR_IQ_STREAM */
/* ---- E1: stream new buffers into bank 2 while the engine plays it (40 Msps, 6 CPU cycles per word) ---- */
static uint32_t *const iq_lut = (uint32_t *)0x3FCB0000u; /* 1024 words in capture bank 0 (free in the research build; the work RAM is full) */

static void iq_build_lut(unsigned amp)
{
    int64_t zr = (int64_t)amp << 20, zi = 0;
    for (unsigned k = 0; k < 1024; k++) {
        int32_t i = (int32_t)((zr + (1 << 19)) >> 20), q = (int32_t)((zi + (1 << 19)) >> 20);
        int64_t q2 = (((int64_t)q * (65536 + iq.pre_g)) >> 16) + (((int64_t)i * iq.pre_p) >> 16);
        q = q2 > 511 ? 511 : q2 < -512 ? -512 : (int32_t)q2;
        iq_lut[k] = ((uint32_t)i & 0x3FFu) | (((uint32_t)q & 0x3FFu) << 10);
        int64_t nr = (zr * 1073721611 - zi * 6588356) >> 30, ni = (zr * 6588356 + zi * 1073721611) >> 30;
        zr = nr;
        zi = ni;
    }
}

/* 256 words from the lookup table: 8 rounds of 32 fully unrolled stores, two interleaved phase accumulators so that the load-use delay
 * of the table lookup overlaps (a call costs about 38 cycles, so the call is per 256 words). */
__attribute__((noinline)) static uint32_t iq_write_256(volatile uint32_t *vdst, uint32_t phase, uint32_t inc)
{
    uint32_t *dst = (uint32_t *)vdst; /* not volatile: a volatile store gets a memw barrier in front of it, one more cycle per word */
    uint32_t pa = phase, pb = phase + inc, inc2 = inc * 2u, a, b;
    for (unsigned r = 0; r < 8; r++, dst += 32) {
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[0] = a; dst[1] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[2] = a; dst[3] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[4] = a; dst[5] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[6] = a; dst[7] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[8] = a; dst[9] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[10] = a; dst[11] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[12] = a; dst[13] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[14] = a; dst[15] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[16] = a; dst[17] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[18] = a; dst[19] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[20] = a; dst[21] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[22] = a; dst[23] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[24] = a; dst[25] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[26] = a; dst[27] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[28] = a; dst[29] = b; pa += inc2; pb += inc2;
        a = iq_lut[pa >> 22]; b = iq_lut[pb >> 22]; dst[30] = a; dst[31] = b; pa += inc2; pb += inc2;
    }
    __asm__ volatile("memw" ::: "memory");
    return pa;
}

#define STREAM_BLOCK 256u
#define STREAM_TAIL 512u
#define STREAM_MARGIN 64u /* words the writer stays behind the reader */
#define STREAM_WORD_CYC 6u

static uint32_t iq_stream(unsigned amp)
{
    volatile uint32_t *bank2 = (volatile uint32_t *)IQ_BANK2;
    const uint32_t base = 16383u; /* rate bit 0: 40 Msps */
    uint32_t phase = 0, buffers, late = 0;
    iq_build_lut(amp);
    const uint32_t inc1 = iq.st_inc, inc2 = iq.st_inc2 ? iq.st_inc2 : iq.st_inc;
    for (unsigned w = 0; w < IQ_WORDS; w += 256) /* buffer 0 before the first trigger */
        phase = iq_write_256(bank2 + w, phase, inc1);

    REG(IQ_DAC_REG) = base;
    REG(IQ_DAC_REG) = base | (1u << 31);
    uint32_t t0 = cpu_cycles();
    const uint32_t end = t0 + iq.ms * 240000u;
    buffers = 1;
    for (uint32_t b = 1;; b++) { /* b: buffer being written */
        const uint32_t inc = (b & 1u) ? inc2 : inc1;
        phase += (uint32_t)(((uint64_t)inc * iq.st_g) >> 8); /* the pause between the buffers */
        /* The last STREAM_TAIL words are written after the next trigger, so that nothing delays the trigger: this part has
         * time (the reader reaches it only a whole buffer later), the trigger has none. */
        for (unsigned w = 0; w < IQ_WORDS - STREAM_TAIL; w += STREAM_BLOCK) {
            const uint32_t target = t0 + STREAM_WORD_CYC * (w + STREAM_BLOCK - 1 + STREAM_MARGIN);
            while ((int32_t)(cpu_cycles() - target) < 0)
                ;
            if ((int32_t)(cpu_cycles() - target) > 400)
                late++;
            phase = iq_write_256(bank2 + w, phase, inc);
        }
        while (!(REG(IQ_DAC_REG) & (1u << 18))) /* the engine finishes the previous buffer: trigger at once */
            ;
        REG(IQ_DAC_REG) = base;
        REG(IQ_DAC_REG) = base | (1u << 31);
        t0 = cpu_cycles();
        buffers++;
        for (unsigned w = IQ_WORDS - STREAM_TAIL; w < IQ_WORDS; w += STREAM_BLOCK)
            phase = iq_write_256(bank2 + w, phase, inc);
        if ((int32_t)(cpu_cycles() - end) >= 0)
            break; /* bank 2 holds a complete buffer (the one the engine is playing now) */
    }
    {
        uint32_t t = cpu_cycles();
        while (!(REG(IQ_DAC_REG) & (1u << 18)) && cpu_cycles() - t < 3u * 240000u)
            ;
    }
    REG(IQ_DAC_REG) = 0;
    return (late > 0xFFFF ? 0xFFFFu : late) << 16 | (buffers > 0xFFFF ? 0xFFFFu : buffers);
}

static uint32_t iq_stream_check(void)
{
    volatile uint32_t *bank2 = (volatile uint32_t *)IQ_BANK2;
    uint32_t best_bad = 0xFFFFFFFFu, best_first = 0xFFFF;
    for (unsigned k = 0; k < 1024; k++) { /* every table entry equal to word 0 is a candidate for the start phase */
        if (iq_lut[k] != bank2[0])
            continue;
        uint32_t phase = (uint32_t)k << 22, bad = 0, first = 0xFFFF;
        for (unsigned w = 0; w < IQ_WORDS && bad < best_bad; w++) {
            if (bank2[w] != iq_lut[phase >> 22]) {
                if (!bad)
                    first = w;
                bad++;
            }
            phase += iq.st_inc;
        }
        if (bad < best_bad) {
            best_bad = bad;
            best_first = first;
        }
    }
    if (best_bad == 0xFFFFFFFFu)
        return 0xFFFFFFFFu;
    return (best_first << 16) | (best_bad > 0xFFFF ? 0xFFFFu : best_bad);
}


static uint32_t iq_stream_check(void);
static uint32_t iq_wcheck(unsigned playing)
{
    volatile uint32_t *bank2 = (volatile uint32_t *)IQ_BANK2;
    uint32_t phase = 0;
    iq_build_lut(150);
    if (playing) {
        REG(IQ_DAC_REG) = 16383u;
        REG(IQ_DAC_REG) = 16383u | (1u << 31);
    }
    if (playing == 2) { /* volatile stores, each with a memw barrier in front (7 cycles per word) */
        for (unsigned w = 0; w < IQ_WORDS; w++) {
            bank2[w] = iq_lut[phase >> 22];
            phase += iq.st_inc;
        }
    } else if (playing == 3) { /* the same, but every word is stored twice */
        for (unsigned w = 0; w < IQ_WORDS; w++) {
            uint32_t v = iq_lut[phase >> 22];
            bank2[w] = v;
            bank2[w] = v;
            phase += iq.st_inc;
        }
    } else {
        for (unsigned w = 0; w < IQ_WORDS; w += 256)
            phase = iq_write_256(bank2 + w, phase, iq.st_inc);
    }
    if (playing) {
        while (!(REG(IQ_DAC_REG) & (1u << 18)))
            ;
        REG(IQ_DAC_REG) = 0;
    }
    return iq_stream_check();
}


#endif /* ESPDR_IQ_STREAM */

#ifdef ESPDR_IQ_GDMA /* experiment E2, finished (docs/research/DESIGN-IQ-TX.md); built only with -DESPDR_IQ_GDMA */
/* ---- E2: refill bank 2 with the GDMA memory-to-memory channel while the engine reads it ---- */
#define IQ_BANK1 0x3FCC0000u
#define IQ_DESC_BASE 0x3FCB1000u /* descriptors: bank 0, behind the 4 KiB lookup table */
#define IQ_DESC_BYTES 2048u
static uint32_t iq_gdma_cycles;

static uint32_t iq_gdma(unsigned arg)
{
    const bool playing = arg & 1, burst = arg & 2;
    volatile uint32_t *bank1 = (volatile uint32_t *)IQ_BANK1, *bank2 = (volatile uint32_t *)IQ_BANK2;
    gdma_dev_t *dev = GDMA_LL_GET_HW(0);
    uint32_t phase = 0;
    iq_build_lut(150);
    for (unsigned w = 0; w < IQ_WORDS; w += 256) /* the source */
        phase = iq_write_256(bank1 + w, phase, iq.st_inc);
    for (unsigned w = 0; w < IQ_WORDS; w++) /* the destination: zeros, so that a missing copy shows */
        bank2[w] = 0;
    const unsigned n = IQ_WORDS * 4 / IQ_DESC_BYTES;
    dma_descriptor_t *tx = (dma_descriptor_t *)IQ_DESC_BASE, *rx = tx + n;
    for (unsigned i = 0; i < n; i++) {
        tx[i].dw0.size = IQ_DESC_BYTES;
        tx[i].dw0.length = IQ_DESC_BYTES;
        tx[i].dw0.err_eof = 0;
        tx[i].dw0.suc_eof = i == n - 1;
        tx[i].dw0.owner = 1;
        tx[i].buffer = (void *)(IQ_BANK1 + i * IQ_DESC_BYTES);
        tx[i].next = i == n - 1 ? NULL : &tx[i + 1];
        rx[i].dw0.size = IQ_DESC_BYTES;
        rx[i].dw0.length = 0;
        rx[i].dw0.err_eof = 0;
        rx[i].dw0.suc_eof = 0;
        rx[i].dw0.owner = 1;
        rx[i].buffer = (void *)(IQ_BANK2 + i * IQ_DESC_BYTES);
        rx[i].next = i == n - 1 ? NULL : &rx[i + 1];
    }
    periph_ll_enable_clk_clear_rst(PERIPH_GDMA_MODULE);
    gdma_ll_force_enable_reg_clock(dev, true);
    gdma_ll_tx_reset_channel(dev, 0);
    gdma_ll_rx_reset_channel(dev, 0);
    gdma_ll_tx_connect_to_periph(dev, 0, GDMA_TRIG_PERIPH_M2M, 0);
    gdma_ll_rx_connect_to_periph(dev, 0, GDMA_TRIG_PERIPH_M2M, 0);
    gdma_ll_tx_enable_owner_check(dev, 0, false);
    gdma_ll_rx_enable_owner_check(dev, 0, false);
    gdma_ll_tx_enable_data_burst(dev, 0, burst);
    gdma_ll_rx_enable_data_burst(dev, 0, burst);
    if (burst) {
        gdma_ll_tx_set_burst_size(dev, 0, 32);
        gdma_ll_rx_set_burst_size(dev, 0, 32);
    }
    gdma_ll_rx_clear_interrupt_status(dev, 0, GDMA_LL_RX_EVENT_MASK);
    gdma_ll_tx_clear_interrupt_status(dev, 0, GDMA_LL_TX_EVENT_MASK);
    gdma_ll_rx_set_desc_addr(dev, 0, (uint32_t)rx);
    gdma_ll_tx_set_desc_addr(dev, 0, (uint32_t)tx);
    gdma_ll_rx_start(dev, 0);
    if (playing) {
        REG(IQ_DAC_REG) = 16383u;
        REG(IQ_DAC_REG) = 16383u | (1u << 31);
    }
    uint32_t t = cpu_cycles();
    gdma_ll_tx_start(dev, 0);
    while (!(gdma_ll_rx_get_interrupt_status(dev, 0, true) & GDMA_LL_EVENT_RX_SUC_EOF) && cpu_cycles() - t < 20u * 240000u)
        ;
    iq_gdma_cycles = cpu_cycles() - t;
    if (playing) {
        while (!(REG(IQ_DAC_REG) & (1u << 18)))
            ;
        REG(IQ_DAC_REG) = 0;
    }
    uint32_t bad = 0, first = 0xFFFF;
    for (unsigned w = 0; w < IQ_WORDS; w++)
        if (bank2[w] != bank1[w]) {
            if (!bad)
                first = w;
            bad++;
        }
    return (first << 16) | (bad > 0xFFFF ? 0xFFFFu : bad);
}


#endif /* ESPDR_IQ_GDMA */

/* ---- chip temperature (the SoC's temperature sensor, as the SDK's temperature_sensor_ll.h and sar_periph_ctrl_common.c drive it) ---- */
#include "soc/sens_struct.h"

static uint32_t iq_play(uint32_t bits)
{
    const uint32_t base = bits & 0x0FF8BFFFu; /* the implemented bits except run (31) and done (18) */
    uint32_t done = 0, timeouts = 0;
    const uint32_t end = cpu_cycles() + iq.ms * 240000u;
    while ((int32_t)(cpu_cycles() - end) < 0) {
        REG(IQ_DAC_REG) = base;
        REG(IQ_DAC_REG) = base | (1u << 31);
        uint32_t t = cpu_cycles();
        while (!(REG(IQ_DAC_REG) & (1u << 18)))
            if (cpu_cycles() - t > 2u * 240000u) {
                timeouts++;
                break;
            }
        REG(IQ_DAC_REG) = base;
        done++;
        if (iq.gap_us) {
            uint32_t g = cpu_cycles() + iq.gap_us * 240u;
            while ((int32_t)(cpu_cycles() - g) < 0)
                ;
        }
    }
    REG(IQ_DAC_REG) = 0;
    return (timeouts > 0xFFFF ? 0xFFFFu : timeouts) << 16 | (done > 0xFFFF ? 0xFFFFu : done);
}

/* ---- L2 of docs/research/PLAN-LORA-IQ.md: a LoRa frame symbol by symbol. The engine plays a short window per symbol; between two triggers (engine idle) the CPU copies the next window
 * from a ring of the base chirp in bank 1 into bank 2. The symbol clock is the cycle counter; the end of every symbol is left out (the copy takes that time). ---- */
static struct {
    uint32_t nsym, w, os_q8, lp, tc, reps, pbits, ov;
} lw = {.w = 788, .os_q8 = 6300, .lp = 600, .tc = 4727, .reps = 1, .ov = 400};

__attribute__((noinline)) static uint32_t iq_lora(uint32_t report)
{
    const uint32_t *ring = (const uint32_t *)0x3FCC0000u;
    uint32_t *dst = (uint32_t *)IQ_BANK2;
    const uint16_t *syms = (const uint16_t *)0x3FCB1000u; /* capture bank 0 behind the lookup table */
    const uint32_t base0 = lw.pbits & 0x0FF8BFFFu & ~0x3FFFu;
    uint32_t late = 0, copy_cycles = 0, first_late = 0xFFFFu;
    for (uint32_t rep = 0; rep < lw.reps; rep++) {
        uint32_t next = cpu_cycles() + 2400u;
        for (uint32_t i = 0; i < lw.nsym; i++) {
            uint32_t code = syms[i], len = lw.w, play = lw.lp, period = lw.tc;
            const uint32_t *src;
            if (code & 0x8000u) {
                src = ring + 2u * lw.w;
            } else if (code & 0x4000u) {
                next += lw.tc / 4u; /* the quarter down-chirp of the SFD is left out: silence for its time, and the next window is copied meanwhile */
                continue;
            } else {
                src = ring + (((code & 0xFFFu) * lw.os_q8) >> 8);
            }
            uint32_t t = cpu_cycles();
            for (uint32_t k = 0; k + 4u <= len; k += 4u) {
                uint32_t a = src[k], b = src[k + 1], c = src[k + 2], d = src[k + 3];
                dst[k] = a;
                dst[k + 1] = b;
                dst[k + 2] = c;
                dst[k + 3] = d;
            }
            __asm__ volatile("memw" ::: "memory");
            copy_cycles = cpu_cycles() - t;
            if ((int32_t)(cpu_cycles() - next) > 0) {
                late++;
                if (first_late == 0xFFFFu)
                    first_late = i;
            } else {
                while ((int32_t)(cpu_cycles() - next) < 0)
                    ;
            }
            const uint32_t base = base0 | ((play - 1u) & 0x3FFFu);
            REG(IQ_DAC_REG) = base;
            REG(IQ_DAC_REG) = base | (1u << 31);
            next += period;
            uint32_t t1 = cpu_cycles();
            while (!(REG(IQ_DAC_REG) & (1u << 18)))
                if (cpu_cycles() - t1 > 240000u)
                    break;
            REG(IQ_DAC_REG) = base;
        }
        if (iq.gap_us) {
            uint32_t g = cpu_cycles() + iq.gap_us * 240u;
            while ((int32_t)(cpu_cycles() - g) < 0)
                ;
        }
    }
    REG(IQ_DAC_REG) = 0;
    return (late > 0xFFFFu ? 0xFFFFu : late) << 16 | (report ? first_late : (copy_cycles > 0xFFFFu ? 0xFFFFu : copy_cycles));
}

unsigned radio_iq_op(unsigned op, uint32_t arg, uint32_t *value)
{
    iq.last_op = cpu_cycles();
    if (op != IQ_OP_BEGIN && op != IQ_OP_END && op != IQ_OP_KEY_RAW && op < IQ_OP_ADDR && !iq.begun && op != IQ_OP_ROT_COS && op != IQ_OP_ROT_SIN && op != IQ_OP_MS)
        return CTL_NOT_READY;
    switch (op) {
    case IQ_OP_BEGIN: {
        iq_end();
        iq_skip_cal = arg & 1;
        unsigned st = radio_tx_begin(value);
        iq.begun = st == CTL_OK;
        return st;
    }
    case IQ_OP_KEY:
        if (iq.keyed)
            return CTL_BUSY;
        start_tx_tone_step(1, 0, (int)(arg & 0xFF), 0, 0, 0);
        iq.keyed = true;
        REG(IQ_BANK_SELECT_REG) = (arg >> 8) & 7u; /* keying clears it; grant the bank after keying (never bank 3) */
        *value = REG(IQ_BANK_SELECT_REG);
        return CTL_OK;
    case IQ_OP_KEY2:
        if (iq.keyed)
            return CTL_BUSY;
        phy_txtone_start((int)(arg & 0xFFFF), 0, (int)((arg >> 16) & 0xFF));
        iq.keyed = true;
        REG(IQ_BANK_SELECT_REG) = (arg >> 24) & 7u;
        *value = REG(IQ_BANK_SELECT_REG);
        return CTL_OK;
    case IQ_OP_KEY_RAW:
        if (iq.keyed || iq.begun)
            return CTL_BUSY;
        phy_txtone_start((int)(arg & 0xFFFF), 0, (int)((arg >> 16) & 0xFF));
        iq.keyed = iq.begun = true;
        REG(IQ_BANK_SELECT_REG) = (arg >> 24) & 7u;
        *value = REG(IQ_BANK_SELECT_REG);
        return CTL_OK;
    case IQ_OP_PWR: {
        int32_t sum = 0;
        const uint32_t base = iq.play_bits & 0x0FF8BFFFu;
        for (unsigned k = 0; k < 64; k++) {
            if (arg & 1) {
                REG(IQ_DAC_REG) = base;
                REG(IQ_DAC_REG) = base | (1u << 31);
            }
            sum += (int16_t)txtone_linear_pwr();
            if (arg & 1) {
                uint32_t t = cpu_cycles();
                while (!(REG(IQ_DAC_REG) & (1u << 18)) && cpu_cycles() - t < 2u * 240000u)
                    ;
                REG(IQ_DAC_REG) = base;
            }
        }
        *value = (uint32_t)sum;
        return CTL_OK;
    }
    case IQ_OP_ROT2_COS:
        iq.rot2_cos = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_ROT2_SIN:
        iq.rot2_sin = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_STREAM_G:
        iq.st_g = arg;
        return CTL_OK;
    case IQ_OP_STREAM_INC:
        iq.st_inc = arg;
        return CTL_OK;
    case IQ_OP_STREAM_INC2:
        iq.st_inc2 = arg;
        return CTL_OK;
#ifdef ESPDR_IQ_GDMA
    case IQ_OP_GDMA:
        *value = iq_gdma(arg & 3);
        return CTL_OK;
    case IQ_OP_GDMA_TIME:
        *value = iq_gdma_cycles;
        return CTL_OK;
#endif
    case IQ_OP_LDPOS:
        if (arg >= IQ_WORDS)
            return CTL_BAD_ARGUMENT;
        iq.ld_pos = arg;
        return CTL_OK;
    case IQ_OP_LDI:
        iq.ld_i = arg & 0x3FFu;
        return CTL_OK;
    case IQ_OP_LDQ:
        if (iq.ld_pos >= IQ_WORDS)
            return CTL_BAD_ARGUMENT;
        ((volatile uint32_t *)(iq.ld_tgt ? 0x3FCC0000u : IQ_BANK2))[iq.ld_pos++] = iq.ld_i | ((arg & 0x3FFu) << 10);
        return CTL_OK;
    case IQ_OP_LDTGT:
        iq.ld_tgt = arg & 1u;
        return CTL_OK;
    case IQ_OP_SYM:
        if (lw.nsym >= 1500u)
            return CTL_BAD_ARGUMENT;
        ((volatile uint16_t *)0x3FCB1000u)[lw.nsym++] = (uint16_t)arg;
        return CTL_OK;
    case IQ_OP_SYMCLR:
        lw.nsym = 0;
        return CTL_OK;
    case IQ_OP_LPAR: {
        uint32_t v = arg & 0xFFFFFFu;
        switch (arg >> 24) {
        case 0: lw.w = v; break;
        case 1: lw.os_q8 = v; break;
        case 2: lw.lp = v; break;
        case 3: lw.tc = v; break;
        case 4: lw.reps = v; break;
        case 5: lw.pbits = v; break;
        case 6: lw.ov = v; break;
        default: return CTL_BAD_ARGUMENT;
        }
        return CTL_OK;
    }
    case IQ_OP_LORA:
        if (!lw.nsym || lw.w < 8u || lw.w > 8000u || lw.lp < 8u || lw.lp > lw.w)
            return CTL_BAD_ARGUMENT;
        *value = iq_lora(arg);
        iq.last_op = cpu_cycles();
        return CTL_OK;
    case IQ_OP_GAP:
        iq.gap_us = arg > 1000000u ? 1000000u : arg;
        return CTL_OK;
    case IQ_OP_TEMP:
        *value = radio_tx_temp(arg);
        return CTL_OK;
#ifdef ESPDR_IQ_STREAM
    case IQ_OP_WCHECK:
        *value = iq_wcheck(arg & 3);
        return CTL_OK;
    case IQ_OP_STREAM_CHECK:
        *value = iq_stream_check();
        return CTL_OK;
    case IQ_OP_STREAM:
        if (arg < 1 || arg > 255)
            return CTL_BAD_ARGUMENT;
        *value = iq_stream(arg);
        iq.last_op = cpu_cycles();
        return CTL_OK;
#endif
    case IQ_OP_PRE_G:
        iq.pre_g = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_PRE_P:
        iq.pre_p = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_ANA_RD:
        *value = esp_rom_regi2c_read(((arg >> 8) & 0xFF), ((arg >> 24) & 15) ? ((arg >> 24) & 15) : 1, arg & 0xFF);
        return CTL_OK;
    case IQ_OP_ANA_WR:
        esp_rom_regi2c_write(((arg >> 8) & 0xFF), ((arg >> 24) & 15) ? ((arg >> 24) & 15) : 1, arg & 0xFF, (arg >> 16) & 0xFF);
        return CTL_OK;
    case IQ_OP_PBUS_RD:
        *value = rom_pbus_rd((arg >> 4) & 15, arg & 15) & 511;
        return CTL_OK;
    case IQ_OP_PBUS_WR:
        return pbus_write((arg >> 4) & 15, arg & 15, (arg >> 8) & 511) ? CTL_OK : CTL_FAILED;
    case IQ_OP_GAIN:
        tx_set_gain(arg & 0xFF);
        return CTL_OK;
    case IQ_OP_ROT_COS:
        iq.rot_cos = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_ROT_SIN:
        iq.rot_sin = (int32_t)arg;
        return CTL_OK;
    case IQ_OP_FILL:
        if ((arg & 0x3FF) > 511 || ((arg >> 16) & 15) > IQ_MODE_TWO)
            return CTL_BAD_ARGUMENT;
        *value = iq_fill(arg & 0x3FF, (arg >> 16) & 15);
        return CTL_OK;
    case IQ_OP_MS:
        if (arg < 1 || arg > 3000)
            return CTL_BAD_ARGUMENT;
        iq.ms = arg;
        return CTL_OK;
    case IQ_OP_PLAY:
        iq.play_bits = arg;
        *value = iq_play(arg);
        iq.last_op = cpu_cycles();
        return CTL_OK;
    case IQ_OP_END:
        iq_end();
        return CTL_OK;
    case IQ_OP_ADDR:
        if ((arg & 3) || !((arg >= 0x60000000u && arg <= 0x600FFFFCu) || (arg >= 0x3FCB0000u && arg <= 0x3FCDFFFCu)))
            return CTL_BAD_ARGUMENT;
        iq.addr = arg;
        return CTL_OK;
    case IQ_OP_POKE:
        if (!iq.addr)
            return CTL_NOT_READY;
        if (iq.addr == IQ_BANK_SELECT_REG)
            arg &= 7u; /* bank 3 overlaps the ROM's working data: never grant it */
        REG(iq.addr) = arg;
        return CTL_OK;
    case IQ_OP_PEEK:
        if (!iq.addr)
            return CTL_NOT_READY;
        *value = REG(iq.addr);
        return CTL_OK;
    default:
        return CTL_UNKNOWN_OP;
    }
}
#endif /* ESPDR_IQTEST */
#endif
