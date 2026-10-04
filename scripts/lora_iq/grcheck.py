#!/usr/bin/env python3
"""Decode a saved Pluto capture of our transmitter with gr-lora_sdr's receiver (the receive chain of pluto-tx, tested there against a Heltec V3), as an independent check of the frames.
    LD_LIBRARY_PATH=$PLUTO_TX_DIR/gr-lora_sdr/build/lib PYTHONPATH=$PLUTO_TX_DIR /usr/bin/python3 scripts/lora_iq/grcheck.py CAPTURE.npz --sf 8 --bw 62500 --text "DA2JH LORA-P1"
The capture is shifted by the coarse centre, and must be a whole multiple of the bandwidth in sample rate (the receiver wants an integer oversampling)."""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, os.environ.get("PLUTO_TX_DIR", os.path.expanduser("~/Dokumente/plutosdr")))
from espdr import lora_phy as lp  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sf", type=int, required=True)
    ap.add_argument("--bw", type=float, required=True)
    ap.add_argument("--cr", type=int, default=1)
    ap.add_argument("--text", required=True)
    ap.add_argument("--preamble", type=int, default=8)
    ap.add_argument("--sync", type=lambda v: int(v, 0), default=0x34)
    ap.add_argument("--extra", type=float, default=None, help="shift in Hz after the capture's own shift, instead of the coarse centre (see lora_phy.search_centre)")
    ap.add_argument("--scale", action="store_true", help="the capture is 8-bit unsigned (ESP receiver): ignore its mean and scale")
    args = ap.parse_args()
    import pmt
    from gnuradio import blocks, gr
    from pluto_advanced_rx.lora_rx import LoraRxDecoder
    d = np.load(args.file)
    x, fs, sh = d["x"], float(d["fs"]), float(d["shift"])
    t = np.arange(len(x)) / fs
    y = ((x - np.mean(x)) * np.exp(-2j * np.pi * sh * t)).astype(np.complex64)
    c = args.extra if args.extra is not None else lp.coarse_centre(y, fs, args.bw)
    y = (y * np.exp(-2j * np.pi * c * t)).astype(np.complex64)
    mult = int(round(fs / args.bw))
    # lower the level to keep the receiver's thresholds sane, and add some silence at the end
    y = y / (np.max(np.abs(y)) + 1e-9)
    y = np.concatenate([np.zeros(int(0.05 * fs), np.complex64), y, np.zeros(int(0.3 * fs), np.complex64)])
    tb = gr.top_block()
    src = blocks.vector_source_c(list(y), False)
    rx = LoraRxDecoder(args.sf, int(args.bw), args.cr, 868.1e6, True, False, 2, args.preamble, args.sync, mult)
    sink = blocks.message_debug()
    tb.connect(src, rx)
    tb.msg_connect(rx, "msg", sink, "store")
    tb.run()
    got = []
    for i in range(sink.num_messages()):
        try:
            got.append(pmt.symbol_to_string(sink.get_message(i)))
        except Exception:  # noqa: BLE001 - a message that is not a string (a damaged frame)
            got.append("<not text>")
    ok = sum(1 for g in got if g.encode("latin-1", "replace") == args.text.encode("latin-1", "replace"))
    print(f"gr-lora_sdr receiver: {ok} frames with the right text of {len(got)} decoded (coarse centre {c / 1e3:+.1f} kHz, oversampling {mult})")
    for g in got[:3]:
        print("   ", repr(g))


if __name__ == "__main__":
    main()
