#!/usr/bin/env bash
# Copies the firmware images that `make -C firmware images` builds into the Python package, so that `espdr-rx` and `espdr-tx` find them.
set -euo pipefail
cd "$(dirname "$0")/.."
dst=src/espdr/images
mkdir -p "$dst"
cp firmware/build-rx/iq-source.bin "$dst/rx.bin"
cp firmware/build-tx/iq-source.bin "$dst/tx.bin"
cp firmware/flash/bootloader.bin firmware/flash/partition-table.bin "$dst/"
( cd "$dst" && sha256sum rx.bin tx.bin bootloader.bin partition-table.bin > SHA256SUMS.txt )
echo "staged in $dst:"; ls -l "$dst"
