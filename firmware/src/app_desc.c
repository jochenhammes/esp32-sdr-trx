/*
 * Application descriptor for booting the narrowband image from flash.
 *
 * ESP-IDF's second-stage bootloader reads an esp_app_desc_t from the start of an application's first segment and
 * checks its eFuse block revision limits; without one it takes the first data bytes for a descriptor and refuses the
 * image. The loader also expects at least one segment that it maps through the flash cache (read-only data at
 * 0x3C000000 and code at 0x42000000) and asserts without them. The narrowband link therefore puts this descriptor at the
 * start of a tiny read-only flash segment, as every ESP-IDF application does, and adds a 16-byte code segment.
 * Nothing here is used at run time.
 */
#include <stdint.h>

struct app_desc {
    uint32_t magic_word;          /* ESP_APP_DESC_MAGIC_WORD */
    uint32_t secure_version;
    uint32_t reserved1[2];
    char version[32];
    char project_name[32];
    char time[16];
    char date[16];
    char idf_ver[32];
    uint8_t app_elf_sha256[32];
    uint16_t min_efuse_blk_rev_full;
    uint16_t max_efuse_blk_rev_full;
    uint8_t mmu_page_size;        /* log2 of the MMU page size: 64 KB on the ESP32-S3 */
    uint8_t reserved3[3];
    uint32_t reserved2[18];
};

_Static_assert(sizeof(struct app_desc) == 256, "esp_app_desc_t is 256 bytes");

const struct app_desc __attribute__((section(".rodata.app_desc"), used)) espdr_app_desc = {
    .magic_word = 0xABCD5432u,
    .version = "narrowband",
    .project_name = "espdr-narrowband",
    .min_efuse_blk_rev_full = 0,
    .max_efuse_blk_rev_full = 9999,
    .mmu_page_size = 16,
};

const uint8_t __attribute__((section(".flash.text"), used, aligned(16))) espdr_irom_placeholder[16] = {0};
