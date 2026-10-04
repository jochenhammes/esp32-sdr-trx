#!/usr/bin/env python3
"""Self test of lora_np.py: loopback with random timing and frequency offsets, and (with gnuradio) against gr-lora_sdr at SF7 in both directions.
usage: python3 scripts/lora_iq/selftest.py [--gr]     (--gr needs LD_LIBRARY_PATH and PYTHONPATH as in l0a_loopback.py)"""
import argparse
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import lora_np as ln  # noqa: E402


def loopback(sf, bw, os_, n=20, length=12, cr=1, snr_db=None, cfo_hz=0.0, seed=1, preamble=8):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    fs = bw * os_
    ok = 0
    for _ in range(n):
        payload = bytes(rng.randrange(32, 127) for _ in range(length))
        iq = ln.frame_iq(payload, sf, bw, os_, cr, preamble)
        pad_a = rng.randrange(5, 3 * (1 << sf) * os_)
        sig = np.concatenate([np.zeros(pad_a, np.complex64), iq, np.zeros(6 * (1 << sf) * os_, np.complex64)])
        t = np.arange(len(sig)) / fs
        sig = sig * np.exp(2j * np.pi * (cfo_hz * t + rng.random()))
        if snr_db is not None:
            p_noise = 10 ** (-snr_db / 10) * (fs / bw)            # signal power 1 in the bandwidth bw
            sig = sig + np.sqrt(p_noise / 2) * (nrng.standard_normal(len(sig)) + 1j * nrng.standard_normal(len(sig)))
        r = ln.demodulate(sig, fs, sf, bw, preamble=preamble)
        ok += bool(r and r.get("crc_ok") and r.get("payload") == payload)
    return ok, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gr", action="store_true")
    args = ap.parse_args()
    for sf, bw, os_, cfo in ((7, 125e3, 4, 0), (7, 125e3, 4, 3000), (5, 4e6, 10, 12600), (6, 4e6, 10, 12600), (5, 8e6, 2, -12600), (8, 62.5e3, 4, 500), (12, 125e3, 2, 0), (5, 125e3, 1, 0)):
        for cr in (1, 4):
            ok, n = loopback(sf, bw, os_, cr=cr, cfo_hz=cfo)
            print(f"numpy loopback SF{sf} BW {bw / 1e3:g} kHz x{os_} CR 4/{4 + cr} CFO {cfo:+.0f} Hz: {ok}/{n}", flush=True)
    if args.gr:
        gr_compare()


def gr_compare():
    sys.path.insert(0, os.environ.get("PLUTO_TX_DIR", os.path.expanduser("~/Dokumente/plutosdr")))
    import pmt
    from gnuradio import blocks, gr
    from pluto_advanced_rx.lora_rx import LoraRxDecoder
    from pluto_tx.lora import LoraTxEncoder
    sf, bw, mult, cr = 7, 125000, 4, 1
    rng = random.Random(5)
    payloads = [bytes(rng.randrange(32, 127) for _ in range(12)) for _ in range(5)]
    # numpy transmitter -> gr-lora_sdr receiver
    sig = np.concatenate([np.zeros(2000, np.complex64)] + [np.concatenate([ln.frame_iq(p, sf, bw, mult, cr), np.zeros(8 * (1 << sf) * mult, np.complex64)]) for p in payloads])
    tb = gr.top_block()
    src = blocks.vector_source_c(list(sig), False)
    rx = LoraRxDecoder(sf, bw, cr, 868.1e6, True, False, 2, 8, 0x34, mult)
    sink = blocks.message_debug()
    tb.connect(src, rx)
    tb.msg_connect(rx, "msg", sink, "store")
    tb.run()
    got = [pmt.symbol_to_string(sink.get_message(i)) for i in range(sink.num_messages())]
    print(f"numpy TX -> gr-lora_sdr RX (SF7): {sum(g.encode('latin-1') == p for g, p in zip(got, payloads))}/{len(payloads)} frames equal")
    # gr-lora_sdr transmitter -> numpy receiver
    tb = gr.top_block()
    tx = LoraTxEncoder(sf, bw, cr, True, False, 2, 8, 0x34, mult)
    vs = blocks.vector_sink_c()
    tb.connect(tx, vs)
    tb.start()
    import time
    for p in payloads:
        tx.send_payload_bytes(p)
        time.sleep(0.4)
    time.sleep(0.5)
    tb.stop()
    tb.wait()
    data = np.array(vs.data(), dtype=np.complex64)
    ok = 0
    # compare the symbols/IQ directly: numpy frame against the gr-lora_sdr output (find by correlation)
    for p in payloads:
        ref = ln.frame_iq(p, sf, bw, mult, cr)
        c = np.abs(np.correlate(data, ref[: 10 * (1 << sf) * mult], mode="valid"))
        k = int(np.argmax(c))
        seg = data[k:k + len(ref)]
        if len(seg) == len(ref):
            ok += float(np.max(np.abs(seg - ref))) < 1e-2
    print(f"gr-lora_sdr TX equals the numpy frame (SF7): {ok}/{len(payloads)} frames sample-for-sample (to 0.01)")
    r = ln.demodulate(data, bw * mult, sf, bw)
    print("gr-lora_sdr TX -> numpy RX first frame:", r and (r['payload'], r['crc_ok']))


if __name__ == "__main__":
    main()
