# Files for booting the narrowband image from flash

The ESP32-S3 ROM can only load a small first stage from flash, so the image does not start by itself from address 0. These two
files are Espressif's standard second-stage bootloader and a standard partition table. With them the narrowband image sits in the
`factory` application partition and the normal ESP-IDF bootloader loads it into RAM at every power-up:

| File | Flash offset | What |
|---|---|---|
| `bootloader.bin` | `0x0` | ESP-IDF second-stage bootloader (log level *info*, 80 MHz, DIO) |
| `partition-table.bin` | `0x8000` | the default single-application table: `nvs`, `phy_init`, `factory` at `0x10000` |
| *(the narrowband image)* | `0x10000` | `iq-source.bin` from `make NARROWBAND=1`, or `iq-source-nb.bin` from a release |

`python host/python/espdr_load.py --flash iq-source-nb.bin` writes all three. It **overwrites** the board's flash.

## Where they come from

Built unchanged from ESP-IDF (Apache-2.0): the `examples/get-started/hello_world` project, target `esp32s3`, default configuration, ESP-IDF
`release/v5.5` at commit `2553c5ad` (a v5.5.x tree); the files are `build/bootloader/bootloader.bin` and
`build/partition_table/partition-table.bin`. To rebuild them:

```sh
. $IDF_PATH/export.sh
cp -r $IDF_PATH/examples/get-started/hello_world /tmp/hw && cd /tmp/hw
idf.py set-target esp32s3 && idf.py build
```

`SHA256SUMS` lists the files as committed. Any bootloader from ESP-IDF v5.5.x or newer works the same way. The image needs the
application descriptor and the two small flash-mapped segments that the narrowband link adds (see `src/app_desc.c`).

## Limits

Tested on one board whose flash is quad (4 data lines); the bootloader here is configured for DIO flash. A board with octal flash needs a
bootloader built for it. Loading into RAM (without `--flash`) has no such restriction and does not touch the flash at all.
