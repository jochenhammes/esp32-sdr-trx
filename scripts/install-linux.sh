#!/usr/bin/env bash
# Installs esp32-sdr-trx (the commands espdr-rx and espdr-tx) for your user on Linux, in a virtual environment of its own.
#
#   chmod +x install-linux.sh            # a download from a browser has no execute right yet
#   ./install-linux.sh esp32_sdr_trx-1.0.0-py3-none-any.whl
#   ./install-linux.sh https://github.com/jochenhammes/esp32-sdr-trx/releases/download/v1.0.0/esp32_sdr_trx-1.0.0-py3-none-any.whl
#
# Options:  --audio     also install sounddevice, for a sound card as the transmit source
#           --no-udev   do not offer to install the udev rule
#           --yes       do not ask (the udev rule is installed with sudo)
#           --uninstall remove what this script installed
# Environment:  ESPDR_VENV (default ~/.local/share/esp32-sdr-trx/venv)  ESPDR_BIN (default ~/.local/bin)
set -euo pipefail

VENV="${ESPDR_VENV:-$HOME/.local/share/esp32-sdr-trx/venv}"
BIN="${ESPDR_BIN:-$HOME/.local/bin}"
RULE_DST=/etc/udev/rules.d/70-espdr.rules
audio=0; udev=1; yes=0; uninstall=0; wheel=""

for a in "$@"; do
  case "$a" in
    --audio) audio=1 ;;
    --no-udev) udev=0 ;;
    --yes) yes=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    -*) echo "unknown option $a (see --help)" >&2; exit 2 ;;
    *) wheel="$a" ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }

if [ "$uninstall" = 1 ]; then
  say "removing $BIN/espdr-rx, $BIN/espdr-tx and $VENV"
  rm -f "$BIN/espdr-rx" "$BIN/espdr-tx"
  rm -rf "$VENV"
  [ -e "$RULE_DST" ] && echo "the udev rule $RULE_DST stays; remove it with: sudo rm $RULE_DST && sudo udevadm control --reload"
  echo "done"; exit 0
fi

if [ -z "$wheel" ]; then
  # a wheel next to this script or in dist/ (a source tree after `python -m build`)
  here="$(cd "$(dirname "$0")" && pwd)"
  wheel="$(ls "$here"/esp32_sdr_trx-*.whl "$here"/../dist/esp32_sdr_trx-*.whl 2>/dev/null | tail -n 1 || true)"
  [ -n "$wheel" ] || { echo "give the wheel (a file or a URL) as an argument; see --help" >&2; exit 2; }
fi

say "Python"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 not found. Debian/Ubuntu: sudo apt install python3 python3-venv python3-pip" >&2; exit 1; }
"$PY" - <<'PYEOF' || { echo "Python 3.10 or newer is required (esptool 5 needs it)." >&2; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PYEOF
"$PY" --version

say "virtual environment in $VENV"
if ! "$PY" -m venv "$VENV" 2>/dev/null; then
  echo "could not create a virtual environment. Debian/Ubuntu: sudo apt install python3-venv" >&2
  exit 1
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
say "installing $wheel"
"$VENV/bin/python" -m pip install --quiet --upgrade "$wheel"
if [ "$audio" = 1 ]; then
  say "sound card support (sounddevice)"
  "$VENV/bin/python" -m pip install --quiet sounddevice
  echo "on Linux sounddevice also needs PortAudio: sudo apt install libportaudio2"
fi

say "commands in $BIN"
mkdir -p "$BIN"
for c in espdr-rx espdr-tx; do
  ln -sf "$VENV/bin/$c" "$BIN/$c"     # the scripts in the venv are executable already; the link makes them available without activating it
done
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "note: $BIN is not in your PATH. Add this line to ~/.profile (then log in again), or start the tools with their full path:"
     echo "      export PATH=\"$BIN:\$PATH\"" ;;
esac

if [ "$udev" = 1 ]; then
  say "udev rule (keeps ModemManager away from the board and lets you use it without the dialout group)"
  rule="$("$VENV/bin/python" - <<'PYEOF'
import importlib.resources as r
print(r.files("espdr").joinpath("udev/70-espdr.rules"))
PYEOF
)"
  if [ -e "$RULE_DST" ]; then
    echo "already installed: $RULE_DST"
  elif [ ! -f "$rule" ]; then
    echo "the rule is not in this package; get udev/70-espdr.rules from the repository and copy it to $RULE_DST"
  else
    go=n
    if [ "$yes" = 1 ]; then go=y; else read -r -p "install $RULE_DST with sudo? [y/N] " go || true; fi
    if [ "$go" = y ] || [ "$go" = Y ]; then
      sudo cp "$rule" "$RULE_DST" && sudo udevadm control --reload && sudo udevadm trigger
      echo "installed; replug the board"
    else
      echo "skipped. Later: sudo cp $rule $RULE_DST && sudo udevadm control --reload && sudo udevadm trigger"
    fi
  fi
fi

say "check"
"$BIN/espdr-rx" --version
"$BIN/espdr-tx" --version
echo
echo "Done. Connect the board with both USB cables, then run: espdr-rx    (receiver, rtl_tcp server for SDR++)"
echo "                                                      or: espdr-tx -h (transmitter; read docs/transmitter.md first)"
