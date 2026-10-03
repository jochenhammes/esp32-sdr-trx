# Releasing (for maintainers)

A release is a git tag `vX.Y.Z` on `main`. The `Release` workflow (`.github/workflows/release.yml`) then

1. builds the receiver and the transmitter image in the `espressif/idf:v5.5.5` container (`make -C firmware images`),
2. builds the wheel with the images inside (`src/espdr/images/`), the version taken from the tag,
3. installs the wheel into a clean environment and runs both commands' self-tests,
4. publishes the wheel, the two images, and `SHA256SUMS` on the release page with generated release notes.

Before tagging:

* CI is green on `main` (host tests on Python 3.10 and 3.12, firmware images and the checks on them, the wheel installed clean).
* `python scripts/gen-reference.py --check` is clean (the command reference matches `-h`).
* Test the CI-built wheel on the board: `pipx install --force <wheel>`; then `espdr-rx` (SDR++ receives), `espdr-tx --dry-run`, a short real
  transmission into a dummy load or at a distance with a receiver listening, `espdr-rx --flash` and a power cycle if the flash path changed.
* The release notes say what was tested on which board, and repeat the safety and licence notice of the transmitter guide.

Version numbers: `v1.0.0-rc1` for the first release candidate, `v1.0.0` when it has been tested by someone other than the author on another board.
The `rx`/`tx` images are built from the same tag as the tools; the tools check what runs on the board (`CTL_INFO`) and load the matching image.
