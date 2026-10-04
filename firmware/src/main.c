/*
 * ESP32-S3 IQ source firmware: command loop on core 0.
 *
 * Runs from RAM (loaded by the ROM bootloader), with no RTOS, heap or
 * interrupts. Both cores are polling loops.
 */
#include <stdbool.h>
#include <stdint.h>

#include "board.h"
#include "capture.h"
#include "control.h"
#include "hal/dedic_gpio_cpu_ll.h"
#include "hal/clk_gate_ll.h"
#include "hal/i2s_ll.h"
#include "platform.h"
#include "radio.h"
#ifdef ESPDR_NARROWBAND
#include "narrowband.h"
#include "stream.h"
#ifdef ESPDR_TX
#include "transmit.h"
#ifdef ESPDR_IQTEST
#include "iqtest.h"
#endif
#endif
#endif
#include "soc/gpio_reg.h"
#include "soc/gpio_sig_map.h"
#include "soc/io_mux_reg.h"
#include "soc/system_reg.h"

#define CPU_HZ 240000000u
#define IDLE_RELEASE_CYCLES CPU_HZ /* release the link after 1 s without commands */
#define PARTIAL_REQUEST_CYCLES (CPU_HZ / 10)

static const unsigned link_gpio[LINK_LINES] = {LINK_GPIOS};

/* Dedicated-GPIO output signals: bits 0..7 of each core's port. */
static const unsigned link_signal[LINK_LINES] = {
    PRO_ALONEGPIO_OUT0_IDX, PRO_ALONEGPIO_OUT1_IDX, PRO_ALONEGPIO_OUT2_IDX, PRO_ALONEGPIO_OUT3_IDX,
    PRO_ALONEGPIO_OUT4_IDX, PRO_ALONEGPIO_OUT5_IDX, PRO_ALONEGPIO_OUT6_IDX, PRO_ALONEGPIO_OUT7_IDX,
    CORE1_GPIO_OUT0_IDX,    CORE1_GPIO_OUT1_IDX,    CORE1_GPIO_OUT2_IDX,    CORE1_GPIO_OUT3_IDX,
    CORE1_GPIO_OUT4_IDX,    CORE1_GPIO_OUT5_IDX,    CORE1_GPIO_OUT6_IDX,    CORE1_GPIO_OUT7_IDX,
};

static uint8_t mac[6];
static bool outputs_enabled;

/* The on-board WS2812 retains its last colour through a CPU/RAM reload.
 * Send 24 zero bits on GPIO48; a static low alone does not clear it. */
static void rgb_led_off(void)
{
    REG(IO_MUX_GPIO48_REG) = (PIN_FUNC_GPIO << MCU_SEL_S) | (1u << FUN_DRV_S);
    REG(GPIO_FUNC0_OUT_SEL_CFG_REG + 4 * 48) = SIG_GPIO_OUT_IDX | GPIO_FUNC0_OEN_SEL;
    REG(GPIO_OUT1_W1TC_REG) = 1u << 16;
    REG(GPIO_ENABLE1_W1TS_REG) = 1u << 16;
    delay_us(300);
    for (unsigned bit = 0; bit < 24; ++bit) {
        uint32_t start = cpu_cycles();
        REG(GPIO_OUT1_W1TS_REG) = 1u << 16;
        while (cpu_cycles() - start < 72) {}
        REG(GPIO_OUT1_W1TC_REG) = 1u << 16;
        while (cpu_cycles() - start < 300) {}
    }
    delay_us(300);
}

/* Continuous hardware MCLK, independent of both CPU-driven data lanes.
 * The restored crystal feeds the ESP BBPLL; PLL240 / 12 gives 20 MHz on
 * GPIO41 -> Br B12/F4. No DMA, I2S data transfer or per-edge CPU writes. */
#ifndef ESPDR_NARROWBAND
static void init_forwarded_clock(void)
{
    REG(GPIO_ENABLE_W1TC_REG) = 1u << 8; /* release former clock pad */
    REG(GPIO_ENABLE1_W1TC_REG) = 1u << 9;
    periph_ll_enable_clk_clear_rst(PERIPH_I2S0_MODULE);
    I2S0.tx_clkm_conf.clk_en = 1;
    i2s_ll_tx_disable_clock(&I2S0);
    i2s_ll_tx_clk_set_src(&I2S0, I2S_CLK_SRC_PLL_240M);
    const hal_utils_clk_div_t divider = {.integer = 12, .denominator = 0, .numerator = 0};
    i2s_ll_tx_set_mclk(&I2S0, &divider);
    i2s_ll_mclk_bind_to_tx_clk(&I2S0);
    i2s_ll_tx_enable_clock(&I2S0);
    REG(PERIPHS_IO_MUX_GPIO0_U + 4 * 41) = (PIN_FUNC_GPIO << MCU_SEL_S) | FUN_IE | (1u << FUN_DRV_S);
    REG(GPIO_FUNC0_OUT_SEL_CFG_REG + 4 * 41) = I2S0_MCLK_OUT_IDX | GPIO_FUNC0_OEN_SEL;
    REG(GPIO_ENABLE1_W1TS_REG) = 1u << 9;
    memory_barrier();
}
#endif

/* ---- link pins ------------------------------------------------------------------ */

static void configure_pads(void)
{
    for (unsigned i = 0; i < LINK_LINES; i++) {
        unsigned gpio = link_gpio[i];
        unsigned drive = (gpio == 17 || gpio == 18) ? LINK_DRIVE_10MA_GPIO17_18 : LINK_DRIVE_10MA;
        REG(PERIPHS_IO_MUX_GPIO0_U + 4 * gpio) = (1u << MCU_SEL_S) | FUN_IE | (drive << FUN_DRV_S);
    }
    memory_barrier();
}

static void set_outputs(bool enable)
{
    uint32_t low = 0, high = 0;
    for (unsigned i = 0; i < LINK_LINES; i++) {
        unsigned gpio = link_gpio[i];
        if (gpio < 32)
            low |= 1u << gpio;
        else
            high |= 1u << (gpio - 32);
    }
    if (enable) {
        configure_pads();
        dedic_gpio_cpu_ll_write_all(0); /* core 1 keeps its port at 0 when idle */
        REG(GPIO_ENABLE_W1TS_REG) = low;
        REG(GPIO_ENABLE1_W1TS_REG) = high;
    } else {
        REG(GPIO_ENABLE_W1TC_REG) = low;
        REG(GPIO_ENABLE1_W1TC_REG) = high;
    }
    memory_barrier();
    outputs_enabled = enable;
}

static void init_link(void)
{
    configure_pads();
    set_outputs(false);
    REG(SYSTEM_CPU_PERI_CLK_EN_REG) |= SYSTEM_CLK_EN_DEDICATED_GPIO;
    for (unsigned i = 0; i < LINK_LINES; i++)
        REG(GPIO_FUNC0_OUT_SEL_CFG_REG + 4 * link_gpio[i]) = link_signal[i] | GPIO_FUNC0_OEN_SEL;
    dedic_gpio_cpu_ll_write_all(0);
}

/* ---- control protocol ---------------------------------------------------------------- */

static uint32_t crc32(const uint8_t *data, unsigned size)
{
    uint32_t crc = 0xFFFFFFFFu;
    while (size--) {
        crc ^= *data++;
        for (int bit = 0; bit < 8; bit++)
            crc = (crc & 1) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
    }
    return ~crc;
}

static uint32_t load_le(const uint8_t *p, unsigned bytes)
{
    uint32_t value = 0;
    for (unsigned i = 0; i < bytes; i++)
        value |= (uint32_t)p[i] << (8 * i);
    return value;
}

static void store_le(uint8_t *p, uint32_t value, unsigned bytes)
{
    for (unsigned i = 0; i < bytes; i++)
        p[i] = (uint8_t)(value >> (8 * i));
}

static void reply(uint8_t op, uint8_t status, uint16_t sequence, uint32_t value)
{
    uint8_t response[CTL_RESPONSE_BYTES] = {CTL_RESPONSE_MAGIC, CTL_NODE_ESP, op, status};
    store_le(response + 4, sequence, 2);
    store_le(response + 8, value, 4);
    store_le(response + 12, crc32(response, 12), 4);
    serial_write(response, sizeof(response));
}

static uint32_t info(unsigned what)
{
    switch (what) {
    case 0: return CTL_ESP_FIRMWARE_ID;
    case 1: return load_le(mac, 4);
    case 2: return load_le(mac + 4, 2);
    case 3:
        return 0
#ifdef ESPDR_NARROWBAND
               | CTL_BUILD_NARROWBAND
#endif
#ifdef ESPDR_TX
               | CTL_BUILD_TX
#endif
            ;
    default: return 0;
    }
}


#ifdef ESPDR_TX
static bool tx_pending; /* a TX_OP_BEGIN was answered: the stream runs after the reply */
#endif

static uint8_t execute(uint8_t op, uint32_t arg, uint32_t *value)
{
    switch (op) {
    case CTL_INFO:
        if (arg > 3)
            return CTL_BAD_ARGUMENT;
        *value = info(arg);
        return CTL_OK;
    case CTL_SAFE:
        set_outputs(false);
        return CTL_OK;
    case CTL_STATUS:
#ifdef ESPDR_NARROWBAND
        if (arg >= ESP_STAT_COUNT && arg < NB_STAT_COUNT) {
            *value = stream_stat(arg);
            return CTL_OK;
        }
#endif
        if (arg >= ESP_STAT_COUNT)
            return CTL_BAD_ARGUMENT;
        *value = arg == ESP_STAT_RADIO || arg >= ESP_STAT_LO_HZ ? radio_stat(arg) : capture_stat(arg);
        return CTL_OK;
    case ESP_OUTPUTS:
        if (arg > 1)
            return CTL_BAD_ARGUMENT;
        set_outputs(arg);
        return CTL_OK;
    case ESP_RUN:
        if (arg > 65535)
            return CTL_BAD_ARGUMENT;
#ifdef ESPDR_NARROWBAND
        /* No link lines: the stream goes over USB. It needs the 16 Msps dump. */
        if (radio_stat(ESP_STAT_RADIO) != ESP_RADIO_OK || !(radio_dump_control() & DUMP_CTRL_16MSPS))
            return CTL_NOT_READY;
#else
        if (radio_stat(ESP_STAT_RADIO) != ESP_RADIO_OK || !outputs_enabled)
            return CTL_NOT_READY;
#endif
        *value = capture_run(arg);
        return *value ? CTL_RUN_FAILED : CTL_OK;
#ifdef ESPDR_NARROWBAND
    case NB_SET_DECIM:
    case NB_SET_FORMAT:
    case NB_SET_OUTSHIFT:
        return stream_set(op, arg, value);
    case NB_BENCH:
        if ((arg >> 16) > 3)
            return CTL_BAD_ARGUMENT;
        *value = stream_bench(arg & 0xFFFF, arg >> 16);
        return CTL_OK;
    case NB_DSPBENCH:
        if ((arg & 15) < 2 || (arg & 15) > 4)
            return CTL_BAD_ARGUMENT;
        *value = stream_dsp_bench(arg);
        return CTL_OK;
#endif
#ifdef ESPDR_TX
    case TX_OP_LO:
    case TX_OP_RATE:
    case TX_OP_DRIFT:
    case TX_OP_LIMIT:
    case TX_OP_RANGE:
        return radio_tx_set(op, arg);
    case TX_OP_TEMP:
        *value = radio_tx_temp(arg);
        return CTL_OK;
    case TX_OP_BEGIN:
        tx_pending = false;
        {
            uint8_t st = radio_tx_begin(value);
            tx_pending = st == CTL_OK;
            return st;
        }
#endif
#ifdef ESPDR_IQTEST
    case IQ_OP_BEGIN ... IQ_OP_LPAR:
        return radio_iq_op(op, arg, value);
#endif
    case ESP_STOP: /* the run, if any, has already ended */
    case ESP_ARG_HIGH: /* kept by the command loop */
        return CTL_OK;
    default:
        return radio_set(op, arg, value);
    }
}

void app_main(void)
{
    platform_init();
    rgb_led_off();
    init_link();
    start_core1();
    read_mac(mac);
    radio_init();
#ifndef ESPDR_NARROWBAND
    init_forwarded_clock();
#endif

    uint8_t request[CTL_REQUEST_BYTES];
    unsigned received = 0;
    uint16_t arg_high = 0; /* from ESP_ARG_HIGH, for the next request only */
    uint32_t last_command = cpu_cycles(), last_byte = last_command;
    for (;;) {
        uint32_t now = cpu_cycles();
#ifdef ESPDR_IQTEST
        radio_iq_watchdog();
#endif
        if (outputs_enabled && now - last_command > IDLE_RELEASE_CYCLES)
            set_outputs(false);
        if (received && now - last_byte > PARTIAL_REQUEST_CYCLES) {
            received = 0; /* abandon a request whose sender went away */
            arg_high = 0;
        }

        int byte = serial_read();
        if (byte < 0)
            continue;
        last_byte = cpu_cycles();
        if (received == 0 && byte != CTL_REQUEST_MAGIC)
            continue;
        request[received++] = (uint8_t)byte;
        if (received < sizeof(request))
            continue;
        received = 0;
        if (load_le(request + 6, 4) != crc32(request, 6)) {
            /* Misaligned: resume from the next magic byte in what we have. A
             * high argument half belongs only to the request right after it. */
            arg_high = 0;
            for (unsigned i = 1; i < sizeof(request); i++) {
                if (request[i] != CTL_REQUEST_MAGIC)
                    continue;
                received = sizeof(request) - i;
                for (unsigned j = 0; j < received; j++)
                    request[j] = request[i + j];
                break;
            }
            continue;
        }

        uint8_t op = request[1];
        uint16_t arg = (uint16_t)load_le(request + 2, 2);
        uint16_t sequence = (uint16_t)load_le(request + 4, 2);
        uint32_t value = 0;
        uint8_t status = execute(op, (uint32_t)arg_high << 16 | arg, &value);
        arg_high = op == ESP_ARG_HIGH ? arg : 0;
        reply(op, status, sequence, value);
#ifdef ESPDR_TX
        if (tx_pending) {
            tx_pending = false;
            uint32_t summary = radio_tx_run();
            reply(TX_OP_END, CTL_OK, sequence, summary);
            received = 0;
        }
#endif
        last_command = cpu_cycles();
    }
}
