# ESP32-S3 IQ source firmware, built as a RAM image for the ROM loader.
#
# Requires ESP-IDF v5.5 (for its headers, ROM linker scripts, a few clock
# sources and the vendor PHY library) and its xtensa-esp32s3-elf toolchain.
# After `. $IDF_PATH/export.sh`, a plain `make` builds build/iq-source.bin.

IDF_PATH ?= $(error IDF_PATH is not set; source ESP-IDF v5.5's export.sh)
CROSS ?= xtensa-esp32s3-elf-
ESPTOOL ?= esptool.py
# NARROWBAND=1 builds the FPGA-free receiver: the ESP32-S3 samples at 16 Msps,
# decimates on chip and streams over USB Serial/JTAG (see protocol/narrowband.h).
NARROWBAND ?= 1
# TX=1 adds the experimental transmitter (protocol/transmit.h). It is a separate image: the receiver image contains no transmit code.
TX ?= 0
# IQTEST=1 (with TX=1) adds the research ops of protocol/iqtest.h (DAC playback engine tests); built into build-iq, never released.
IQTEST ?= 0
ifeq ($(IQTEST),1)
BUILD ?= build-iq
else ifeq ($(TX),1)
BUILD ?= build-tx
else ifeq ($(NARROWBAND),1)
BUILD ?= build-rx
else
BUILD ?= build
endif

CC := $(CROSS)gcc
NM := $(CROSS)nm
OBJDUMP := $(CROSS)objdump
IDF := $(IDF_PATH)/components
PROTOCOL := protocol

# Header directories, as ESP-IDF itself uses them for applications and for
# its bootloader (the latter compiles the SDK clock sources below).
APP_INCLUDE_DIRS := \
	soc/esp32s3/include soc/esp32s3/register soc/include esp_common/include \
	hal/esp32s3/include hal/include hal/platform_port/include \
	esp_rom/include esp_rom/esp32s3/include esp_rom/esp32s3 \
	esp_hw_support/include esp_hw_support/port/esp32s3/include \
	esp_phy/include esp_phy/esp32s3/include xtensa/include xtensa/esp32s3/include \
	esp_system/include esp_libc/include log/include esp_timer/include
SDK_INCLUDE_DIRS := \
	esp_hw_support/include esp_hw_support/include/soc esp_hw_support/include/soc/esp32s3 \
	esp_hw_support/dma/include esp_hw_support/ldo/include esp_hw_support/debug_probe/include \
	esp_hw_support/mspi_timing_tuning/include \
	esp_hw_support/mspi_timing_tuning/tuning_scheme_impl/include \
	esp_hw_support/power_supply/include esp_hw_support/port/include \
	esp_hw_support/include/esp_private esp_hw_support/port/esp32s3 \
	esp_hw_support/port/esp32s3/include esp_hw_support/mspi_timing_tuning/port/esp32s3 \
	esp_hw_support/mspi_timing_tuning/port/esp32s3/include \
	log/include esp_rom/include esp_rom/esp32s3/include esp_rom/esp32s3/include/esp32s3 \
	esp_rom/esp32s3 esp_common/include newlib/platform_include \
	xtensa/esp32s3/include xtensa/include xtensa/deprecated_include \
	soc/include soc/esp32s3 soc/esp32s3/include soc/esp32s3/register \
	efuse/include efuse/esp32s3/include spi_flash/include \
	hal/platform_port/include hal/esp32s3/include hal/include \
	bootloader_support/include bootloader_support/bootloader_flash/include \
	bootloader_support/private_include esp_security/include \
	esp_phy/include esp_phy/esp32s3/include
APP_INCLUDES := -I. -Isrc -I$(PROTOCOL) $(addprefix -I$(IDF)/,$(APP_INCLUDE_DIRS))
SDK_INCLUDES := -I. $(addprefix -I$(IDF)/,$(SDK_INCLUDE_DIRS))

ifeq ($(NARROWBAND),1)
NB_FLAGS := -DESPDR_NARROWBAND=1
ifeq ($(TX),1)
NB_FLAGS += -DESPDR_TX=1
ifeq ($(IQTEST),1)
NB_FLAGS += -DESPDR_IQTEST=1
endif
endif
ifeq ($(PROFILE),1)
NB_FLAGS += -DPROFILE=1 # per-section cycle counters in dsp_unit(), read with `espdr_nb.py dspbench --profile`
endif
endif

COMMON_FLAGS := -g -mlongcalls -ffunction-sections -fdata-sections
# Application code. Loop-pattern replacement is disabled so the compiler never
# inserts memset/memcpy calls into core 1's private code.
APP_CFLAGS := $(COMMON_FLAGS) -O3 -std=gnu11 -ffreestanding -fno-builtin -mtext-section-literals \
	-fno-tree-loop-distribute-patterns -Wall -Wextra -Werror $(NB_FLAGS) $(APP_INCLUDES)
# ESP-IDF sources, compiled as ESP-IDF compiles them for its bootloader.
SDK_CFLAGS := $(COMMON_FLAGS) -Os -std=gnu17 -freorder-blocks -fstrict-volatile-bitfields \
	-fno-builtin-memcpy -fno-builtin-memset -fno-builtin-bzero \
	-fno-jump-tables -fno-tree-switch-conversion -fno-stack-protector \
	-DBOOTLOADER_BUILD=1 -DESP_PLATFORM -DNON_OS_BUILD=1 -D_GNU_SOURCE \
	-DSOC_MMU_PAGE_SIZE=CONFIG_MMU_PAGE_SIZE -DSOC_XTAL_FREQ_MHZ=CONFIG_XTAL_FREQ \
	-Wno-unused-parameter $(SDK_INCLUDES)
ASFLAGS := -g -mlongcalls -mtext-section-literals -I$(PROTOCOL) -Isrc

APP_SOURCES := main.c platform.c radio.c capture.c libc.c
ifeq ($(NARROWBAND),1)
# The decimator is built twice: core 1 runs its own copy from its own
# instruction bank, so the cores never contend for instruction fetches.
APP_SOURCES += dsp.c stream.c app_desc.c
NB_OBJECTS := $(BUILD)/dsp_core1.o
endif
SDK_SOURCES := \
	$(IDF)/esp_hw_support/port/esp32s3/rtc_clk.c \
	$(IDF)/esp_hw_support/port/esp32s3/rtc_init.c \
	$(IDF)/esp_hw_support/port/esp32s3/rtc_sleep.c \
	$(IDF)/esp_hw_support/port/esp32s3/rtc_time.c \
	$(IDF)/log/src/noos/log_timestamp.c \
	$(IDF)/esp_phy/esp32s3/phy_init_data.c

OBJECTS := \
	$(addprefix $(BUILD)/,$(APP_SOURCES:.c=.o)) \
	$(addprefix $(BUILD)/sdk/,$(notdir $(SDK_SOURCES:.c=.o))) \
	$(BUILD)/startup.o $(BUILD)/vectors.o $(BUILD)/transmit_lane0.o $(BUILD)/transmit_lane1.o \
	$(NB_OBJECTS)

# The narrowband image can also boot from flash. The ROM loader refuses to load segments into the first 32 KB
# of instruction RAM (0x40370000-0x40377FFF, which it keeps for the flash cache), where the FPGA firmware keeps its
# exception vectors. The narrowband link moves them into core 1's bank; the default link is unchanged.
ifeq ($(NARROWBAND),1)
MEMORY_LD := $(BUILD)/memory.ld
$(BUILD)/memory.ld: memory.ld | $(BUILD)
	sed -E -e 's/^(    vectors \(RX\)  : ORIGIN = )0x40374000, LENGTH = 0x4000/\10x4037C000, LENGTH = 0x1000/' \
		-e '/^MEMORY \{/a\    drom    (R)   : ORIGIN = 0x3C000020, LENGTH = 0x1000\n    irom    (RX)  : ORIGIN = 0x42000020, LENGTH = 0x1000' \
		-e '/^SECTIONS \{/a\    .flash.rodata : { KEEP(*(.rodata.app_desc)) } > drom\n    .flash.text : { KEEP(*(.flash.text)) } > irom' $< > $@
else
MEMORY_LD := memory.ld
endif

ROM_LD := $(IDF)/esp_rom/esp32s3/ld
LINKER_SCRIPTS := -T $(MEMORY_LD) -T $(ROM_LD)/esp32s3.rom.ld -T $(BUILD)/rom-libc.ld \
	-T $(ROM_LD)/esp32s3.rom.libgcc.ld -T $(ROM_LD)/esp32s3.rom.api.ld \
	-T $(IDF)/soc/esp32s3/ld/esp32s3.peripherals.ld
LIBRARIES := $(IDF)/esp_phy/lib/esp32s3/libphy.a -lgcc

.PHONY: all clean images
.DEFAULT_GOAL := all
all: $(BUILD)/iq-source.bin

$(BUILD)/%.o: src/%.c $(wildcard src/*.h) $(wildcard $(PROTOCOL)/*.h) sdkconfig.h | $(BUILD)
	$(CC) $(APP_CFLAGS) -c $< -o $@

$(BUILD)/dsp_core1.o: src/dsp.c src/dsp.h src/dsp_taps.h | $(BUILD)
	$(CC) $(APP_CFLAGS) -DDSP_TAPS_EXTERN -DDSP_FUNC=dsp_unit_core1 '-DDSP_ATTR=__attribute__((section(".core1_text")))' -c $< -o $@

$(BUILD)/sdk/%.o: | $(BUILD)/sdk
	$(CC) $(SDK_CFLAGS) -c $(filter %/$*.c,$(SDK_SOURCES)) -o $@

$(BUILD)/startup.o: src/startup.S | $(BUILD)
	$(CC) $(ASFLAGS) -c $< -o $@

$(BUILD)/vectors.o: src/vectors.S | $(BUILD)
	$(CC) $(ASFLAGS) -c $< -o $@

$(BUILD)/transmit_lane%.o: src/transmit.S $(PROTOCOL)/link.h | $(BUILD)
	$(CC) $(ASFLAGS) -DTX_NAME=transmit_lane$* -DTX_SECTION=.tx_lane$* -c $< -o $@

# The ROM's memcpy/memset/memmove use ROM working memory that captures
# overwrite; drop them so libc.c's versions are linked instead.
$(BUILD)/rom-libc.ld: $(ROM_LD)/esp32s3.rom.libc.ld | $(BUILD)
	sed -E '/^(memcpy|memset|memmove) = /d' $< > $@

$(BUILD)/iq-source.elf: $(OBJECTS) $(BUILD)/rom-libc.ld $(MEMORY_LD)
	$(CC) -nostdlib -Wl,--gc-sections -Wl,--orphan-handling=warn -Wl,--fatal-warnings \
		-Wl,-Map,$(BUILD)/iq-source.map $(LINKER_SCRIPTS) $(OBJECTS) \
		-Wl,--start-group $(LIBRARIES) -Wl,--end-group -o $@
	$(NM) -n $@ > $(BUILD)/iq-source.sym
	@# ROM linker scripts silently override same-named functions; every
	@# symbol this firmware defines must resolve inside the image.
	@for symbol in $$($(NM) -g --defined-only $(filter $(BUILD)/%.o,$(OBJECTS)) | awk 'NF == 3 {print $$3}'); do \
		address=$$(awk -v s="$$symbol" '$$3 == s {print $$1}' $(BUILD)/iq-source.sym); \
		case $$address in 400[0-5]*) echo "error: $$symbol resolves to ROM at 0x$$address" >&2; exit 1;; esac; \
	done
	$(OBJDUMP) -d $@ > $(BUILD)/iq-source.lst

$(BUILD)/iq-source.bin: $(BUILD)/iq-source.elf
	$(ESPTOOL) --chip esp32s3 elf2image $< -o $@

$(BUILD) $(BUILD)/sdk:
	mkdir -p $@

clean:
	rm -rf $(BUILD)

# Both images in one go: the receiver (build-rx) and the one with the transmitter (build-tx). Used by CI and scripts/stage-images.sh.
images:
	$(MAKE) TX=0 NARROWBAND=1 BUILD=build-rx
	$(MAKE) TX=1 NARROWBAND=1 BUILD=build-tx
