"""Finding the board, telling which firmware runs on it, and loading the right image into its RAM.

A board with two USB ports has a USB-UART bridge (CH343, CH340, CP210x or FTDI) whose RTS/DTR lines drive EN and BOOT: esptool resets
the chip into its ROM loader through it, no button needed. The firmware then talks on the chip's native USB Serial/JTAG port
(USB id 303a:1001). A board with only the native port can be loaded too, with BOOT held and RESET tapped (`native=True`).

Images run from RAM and nothing is written to the flash unless `flash_receiver()` is used; a reset brings back whatever the flash holds.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

from . import nb

# USB-UART bridges used on ESP32-S3 boards: (vendor, product)
BRIDGES = {(0x1A86, 0x55D3), (0x1A86, 0x7523), (0x1A86, 0x55D4), (0x10C4, 0xEA60), (0x0403, 0x6001), (0x0403, 0x6010),
           (0x0403, 0x6014)}
NATIVE = (0x303A, 0x1001)

CTL_BUILD_NARROWBAND, CTL_BUILD_TX = 1, 2   # CTL_INFO argument 3 (firmware/protocol/control.h)
RX, TX = "rx", "tx"


class BoardError(Exception):
    """Something the user can fix: the message says how."""


def _ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def bridge_ports():
    return [p.device for p in _ports() if (p.vid, p.pid) in BRIDGES]


def native_ports():
    return [p.device for p in _ports() if (p.vid, p.pid) == NATIVE]


def find_bridge(hint=None):
    if hint:
        return hint
    found = bridge_ports()
    if len(found) > 1:
        raise BoardError(f"several USB-UART bridges found ({', '.join(found)}); name the board's with --bridge-port")
    return found[0] if found else None


def find_native(hint=None):
    if hint:
        return hint
    found = native_ports()
    if len(found) > 1:
        raise BoardError(f"several ESP32-S3 boards found ({', '.join(found)}); choose one with --port")
    return found[0] if found else None


def probe(port, timeout=1.5):
    """Asks the firmware on `port` what it is: returns its build flags (CTL_BUILD_*), or None if nothing sensible answers."""
    import serial
    try:
        link = nb.open_link(port)
    except (serial.SerialException, OSError):
        return None
    try:
        link.send(nb.CTL_INFO, 0)
        status, fid = link._response(nb.CTL_INFO, timeout=timeout)
        if status != 0 or fid != nb.FIRMWARE_ID:
            return None
        link.send(nb.CTL_INFO, 3)
        status, flags = link._response(nb.CTL_INFO, timeout=timeout)
        return flags if status == 0 else CTL_BUILD_NARROWBAND   # a receiver image from before the flags existed
    except nb.ProtocolError:
        return None
    finally:
        try:
            link.ser.close()
        except Exception:
            pass


def kind_of(flags):
    return None if flags is None else TX if flags & CTL_BUILD_TX else RX


def image_path(kind, override=None):
    """The firmware image of this package (or `override`, or a build in a source tree)."""
    if override:
        p = Path(override)
        if not p.exists():
            raise BoardError(f"{p}: no such file")
        return p
    here = Path(__file__).resolve().parent
    candidates = [here / "images" / f"{kind}.bin", here.parents[1] / "firmware" / f"build-{kind}" / "iq-source.bin"]
    for p in candidates:
        if p.exists():
            return p
    raise BoardError(f"no {kind} firmware image found (looked in {', '.join(str(c.parent) for c in candidates)}); "
                     f"install a release of esp32-sdr-trx, or build one with `make -C firmware{' TX=1' if kind == TX else ''}`")


VERBOSE = False     # set by the command lines' -v: show esptool's own output


def _esptool(args, what):
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3"] + args
    try:
        if VERBOSE:
            rc = subprocess.call(cmd)
            out = ""
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except OSError as e:
        raise BoardError(f"cannot run esptool: {e}")
    if rc != 0:
        tail = "\n".join(out.strip().splitlines()[-8:])
        raise BoardError(f"esptool failed while {what}:\n{tail}\nCheck the cable and the port, and that no other program "
                         f"(a terminal, ModemManager, another espdr tool) holds it. Run again with -v for the full output.")


def _wait_native(timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = native_ports()
        if found:
            return found[0]
        time.sleep(0.3)
    return None


def load_ram(kind, bridge=None, native=False, image=None, log=print):
    """Loads the image into the chip's RAM and waits for the native port; returns its device name."""
    path = image_path(kind, image)
    if native:
        found = native_ports()
        if len(found) != 1:
            raise BoardError("expected exactly one ESP32-S3 on its native USB port (303a:1001); hold BOOT, tap RESET, and try again")
        port, before = found[0], "usb-reset"
    else:
        port = find_bridge(bridge)
        if not port:
            raise BoardError("no USB-UART bridge found, so the firmware cannot be loaded. Connect the board's UART port (the "
                             "second USB-C port) as well, or put a board with only the native port in download mode (hold BOOT, "
                             "tap RESET) and use --native.")
        before = "default-reset"
    log(f"loading the {'transmitter' if kind == TX else 'receiver'} firmware into RAM through {port} (about 8 s) ...")
    _esptool(["--port", port, "--before", before, "--after", "no-reset", "--no-stub", "load-ram", str(path)], "loading the image")
    native_port = _wait_native()
    if not native_port:
        raise BoardError("the image was loaded, but no native USB port (303a:1001) appeared. Connect the board's native USB port "
                         "too, not only the UART port.")
    return native_port


def flash_receiver(bridge=None, image=None, yes=False, log=print):
    """Writes the receiver image with ESP-IDF's bootloader and a partition table to the flash: the board then starts the receiver
    by itself at every power-up. Overwrites whatever is in the flash."""
    path = image_path(RX, image)
    here = Path(__file__).resolve().parent
    for base in (here / "images", here.parents[1] / "firmware" / "flash"):
        parts = [base / "bootloader.bin", base / "partition-table.bin"]
        if all(p.exists() for p in parts):
            break
    else:
        raise BoardError("bootloader.bin and partition-table.bin are missing from the installation")
    port = find_bridge(bridge)
    if not port:
        raise BoardError("no USB-UART bridge found; connect the board's UART port to write its flash")
    log("This writes the bootloader (0x0), a partition table (0x8000) and the receiver (0x10000) to the board's flash. Whatever program "
        "is in the flash now is lost. To get back to RAM-only use, run: python -m esptool erase-flash")
    if not yes:
        try:
            answer = input("Continue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            raise BoardError("not written")
    log(f"writing the receiver to flash through {port} ...")
    _esptool(["--port", port, "--baud", "921600", "--before", "default-reset", "--after", "hard-reset", "write-flash",
              "--flash-mode", "dio", "--flash-freq", "80m", "--flash-size", "detect",
              "0x0", str(parts[0]), "0x8000", str(parts[1]), "0x10000", str(path)], "writing the flash")
    return _wait_native()


def reset(bridge=None):
    """Resets the chip through the bridge's RTS/DTR lines so that it boots what its flash holds. Returns False if there is no bridge."""
    import serial
    port = find_bridge(bridge)
    if not port:
        return False
    with serial.Serial(port, 115200) as s:
        s.dtr = False       # GPIO0 high: boot from flash
        s.rts = True        # EN low
        time.sleep(0.15)
        s.rts = False
    return True


def ensure(kind, port=None, bridge=None, load="auto", native=False, image=None, log=print):
    """Makes sure that the firmware of `kind` (RX or TX) runs on the board and returns the native port's name.

    load: "auto" loads when another firmware (or none) answers, "never" only checks, "always" loads in any case."""
    found = find_native(port)
    state = None
    if found and load != "always":
        state = kind_of(probe(found))
        if state == kind:
            return found
    if load == "never":
        what = "no native USB port (303a:1001) found" if not found else (
            f"the {'transmitter' if state == TX else 'receiver'} firmware is running, not the {'transmitter' if kind == TX else 'receiver'}"
            if state else f"{found} does not answer as an espdr firmware")
        raise BoardError(f"{what}; run without --no-load to load the right firmware")
    if state and state != kind:
        log(f"the {'transmitter' if state == TX else 'receiver'} firmware is running; replacing it")
    new = load_ram(kind, bridge=bridge, native=native, image=image, log=log)
    flags = probe(new, timeout=3.0)
    if kind_of(flags) != kind:
        raise BoardError(f"the {kind} firmware was loaded, but {new} does not answer as one. Unplug and replug the board and try again.")
    return new
