# Installation, step by step

This page assumes nothing: not a virtual environment, not `pipx`. Linux is the tested system. Windows and macOS have not been tried (the tools use
`pyserial` and `esptool` and may work there).

## What you need

* An ESP32-S3 board with **two USB ports** (see [the receiver guide](receiver.md#requirements)) and a USB cable for each. Connect both.
* **Python 3.10 or newer.** Check with `python3 --version`. (Debian/Ubuntu: `sudo apt install python3 python3-venv python3-pip`;
  Fedora: `sudo dnf install python3`; Arch: `sudo pacman -S python`.)
* The **wheel** of a [release](https://github.com/jochenhammes/esp32-sdr-trx/releases) (take the newest): the file called
  `esp32_sdr_trx-X.Y.Z-py3-none-any.whl`. It contains the tools and the firmware images; there is nothing to build.

## The easy way: the install script

Download the wheel and `install-linux.sh` from the release into one folder, then:

```sh
cd ~/Downloads
chmod +x install-linux.sh                # a file from a browser has no execute right yet; without it: "Keine Berechtigung" / "Permission denied"
./install-linux.sh esp32_sdr_trx-X.Y.Z-py3-none-any.whl
```

(or `bash install-linux.sh esp32_sdr_trx-...whl`, which needs no execute right). It creates a virtual environment for the tools in
`~/.local/share/esp32-sdr-trx/venv`, installs the wheel into it, links the two commands into `~/.local/bin` so that you can start them without
activating anything, and offers to install the udev rule (see below; it asks before it uses `sudo`). Add `--audio` if you want to
transmit from a sound card. If `~/.local/bin` is not in your `PATH` the script says how to add it. To remove everything again: `./install-linux.sh --uninstall`.

## By hand, with a virtual environment

A *virtual environment* (venv) is a folder with its own copy of Python and its own packages, so that the tools do not touch the rest of your system.

```sh
python3 -m venv ~/espdr-venv                       # create it once
. ~/espdr-venv/bin/activate                        # activate it: the prompt now starts with (espdr-venv)
pip install ~/Downloads/esp32_sdr_trx-X.Y.Z-py3-none-any.whl
pip install sounddevice                            # optional: a sound card as the transmit source (Linux also: sudo apt install libportaudio2)
espdr-rx                                           # runs; stop it with Ctrl-C
deactivate                                         # leave the venv
```

The next time you open a terminal, activate the venv again (`. ~/espdr-venv/bin/activate`), or skip the activation and call the command by its path:

```sh
~/espdr-venv/bin/espdr-rx
~/espdr-venv/bin/espdr-tx -h
```

To make the commands available everywhere, link them into a folder that is in your `PATH`:

```sh
mkdir -p ~/.local/bin
ln -s ~/espdr-venv/bin/espdr-rx ~/.local/bin/espdr-rx
ln -s ~/espdr-venv/bin/espdr-tx ~/.local/bin/espdr-tx
```

Notes on execute rights: the two commands inside `bin/` of the venv are created executable by `pip`, nothing to do. A script you download yourself
(like `install-linux.sh`) needs `chmod +x name` once; or you start it with `bash name`. If `./espdr-rx` says *Permission denied* you are probably in the
wrong folder or have copied the file instead of linking it: use the path above or `ln -s`.

## `pipx` (if you have it)

`pipx` does the venv for you: `pipx install ~/Downloads/esp32_sdr_trx-X.Y.Z-py3-none-any.whl`, then `pipx inject esp32-sdr-trx sounddevice` for the sound card.
It puts the commands into `~/.local/bin`.

## The udev rule (Linux, once)

Without it ModemManager may probe the board's port with AT commands (any byte it sends stops a run), and you need to be in the `dialout` group to use
the board. The rule fixes both:

```sh
sudo cp 70-espdr.rules /etc/udev/rules.d/          # the file is in the release and in the package (src/espdr/udev/); the install script does this for you
sudo udevadm control --reload && sudo udevadm trigger
```

Unplug and replug the board. If you prefer groups: `sudo usermod -aG dialout $USER` and log in again.

## The first start

```sh
espdr-rx --version                  # the tool answers
espdr-rx                            # loads the receiver firmware into the board (about 8 s) and starts the rtl_tcp server
```

In SDR++ choose the source *RTL-TCP*, host `127.0.0.1`, port `1234`, and press play. For the transmitter read the [transmitter guide](transmitter.md) first. If something
does not work, [the receiver guide](receiver.md#troubleshooting) and [the transmitter guide](transmitter.md#troubleshooting) have a table of symptoms.

## Update and remove

Update: run the install script (or `pip install --upgrade <new wheel>`) again. Remove: `./install-linux.sh --uninstall`, or delete the venv folder and the two links.

## From the source tree (developers)

You need ESP-IDF v5.5.3 or newer to build the firmware (the vendor PHY library and headers come from it):

```sh
git clone https://github.com/jochenhammes/esp32-sdr-trx.git && cd esp32-sdr-trx
. $IDF_PATH/export.sh && make -C firmware images        # build-rx and build-tx
./scripts/stage-images.sh                                # copies the images into src/espdr/images
python3 -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]' && pytest
```
