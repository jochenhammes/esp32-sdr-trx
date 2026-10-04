#!/usr/bin/env python3
"""L0a of PLAN-LORA-IQ.md: what can gr-lora_sdr do? Software loopback LoraTxEncoder -> LoraRxDecoder at several SF and bandwidths, bit-exact over random payloads.

Needs pluto-tx (PLUTO_TX_DIR, default ~/Dokumente/plutosdr) and its gr-lora_sdr build:
  LD_LIBRARY_PATH=$PLUTO_TX_DIR/gr-lora_sdr/build/lib PYTHONPATH=$PLUTO_TX_DIR /usr/bin/python3 scripts/lora_iq/l0a_loopback.py [--cases sf:bw_khz:mult,...] [--payloads 20]
"""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.environ.get("PLUTO_TX_DIR", os.path.expanduser("~/Dokumente/plutosdr")))
import pmt  # noqa: E402
from gnuradio import blocks, gr  # noqa: E402
from pluto_advanced_rx.lora_rx import LoraRxDecoder  # noqa: E402
from pluto_tx.lora import LoraTxEncoder  # noqa: E402


def run_case(sf, bw, mult, payloads, cr=1, preamble=8, sync_word=0x34, timeout=20.0):
    tb = gr.top_block()
    tx = LoraTxEncoder(sf, bw, cr, True, False, 2, preamble, sync_word, mult)
    rx = LoraRxDecoder(sf, bw, cr, 868.1e6, True, False, 2, preamble, sync_word, mult)
    sink = blocks.message_debug()
    tb.connect(tx, rx)
    tb.msg_connect(rx, "msg", sink, "store")
    tb.start()
    got = []
    t0 = time.time()
    for p in payloads:
        tx.send_payload_bytes(p)
        n = len(got)
        t1 = time.time()
        while time.time() - t1 < timeout:
            if sink.num_messages() > len(got):
                m = sink.get_message(len(got))
                got.append(pmt.symbol_to_string(m) if pmt.is_symbol(m) else str(m))
                break
            time.sleep(0.02)
        else:
            break
    tb.stop()
    tb.wait()
    ok = sum(1 for p, g in zip(payloads, got) if g.encode("latin-1", "replace") == p)
    return ok, len(got), time.time() - t0, got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="7:125:4,7:500:4,5:125:4,6:125:4,5:4000:10,6:4000:10,5:8000:2,5:8000:10")
    ap.add_argument("--payloads", type=int, default=20)
    ap.add_argument("--length", type=int, default=12)
    args = ap.parse_args()
    rng = random.Random(1)
    for case in args.cases.split(","):
        sf, bw_khz, mult = (float(x) for x in case.split(":"))
        sf, mult, bw = int(sf), int(mult), int(bw_khz * 1000)
        payloads = [bytes(rng.randrange(32, 127) for _ in range(args.length)) for _ in range(args.payloads)]
        try:
            ok, n, dt, got = run_case(sf, bw, mult, payloads)
            print(f"SF{sf} BW {bw_khz:g} kHz x{mult} (fs {bw * mult / 1e6:g} Msps): {ok}/{len(payloads)} bit-exact, {n} frames received, {dt:.1f} s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"SF{sf} BW {bw_khz:g} kHz x{mult}: FAILED {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
