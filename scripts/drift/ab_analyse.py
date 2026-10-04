#!/usr/bin/env python3
"""Tables of an ab_series.py recording: per transmission the start temperature, the frequency 2..4 s after the start (against the nominal tone), the largest
excursion from it, the change over the transmission and the fastest change over 10 s (what a receiver's frequency tracking has to follow)."""
import json
import sys

import numpy as np

NOMINAL = {"usb": 101000.0, "rtty": 102222.0}          # Pluto baseband: dial - 100 kHz centre; SSB test tone at +1000 Hz, RTTY middle of the tones 2125 + 85 Hz


def runs(d):
    A = np.array(d["samples"])
    ev = d["events"]
    out = []
    for s, e in zip([x for x in ev if x["kind"] == "start"], [x for x in ev if x["kind"] == "end"]):
        m = (A[:, 0] >= s["t"] - 0.2) & (A[:, 0] <= e["t"] + 0.3) & (A[:, 2] > 40)
        r = A[m]
        if len(r) < 5:
            out.append(dict(ev=s, ok=False))
            continue
        col = 3 if s["mode"] == "rtty" else 1
        tt = r[:, 0] - (r[0, 0] - 0.17)
        out.append(dict(ev=s, ok=True, t=tt, f=r[:, col], cmd=s["cmd"]))
    return out


def stats(r):
    t, f = r["t"], r["f"]
    ref = np.median(f[(t > 2) & (t < 4)])
    tail = np.median(f[t > t[-1] - 4])
    w = 10.0
    rate = 0.0
    for a in np.arange(2, max(t[-1] - w, 2.1), 2.0):
        m = (t >= a) & (t < a + w)
        if m.sum() > 8:
            rate = max(rate, abs(np.polyfit(t[m], f[m], 1)[0]))
    return dict(ref=ref, dev=np.max(np.abs(f[t > 2] - ref)), change=tail - ref, rate=rate, mean=np.mean(f[t > 2]))


def main():
    d = json.load(open(sys.argv[1]))
    T = np.array(d["temps"])
    print(f"{'mode':5s} {'len':>4s} {'var':3s} {'chip C':>7s} {'f(2-4 s) vs nominal':>20s} {'max excursion':>14s} {'change start->end':>18s} {'max rate Hz/s':>14s}")
    for r in runs(d):
        e = r["ev"]
        if not r["ok"]:
            print(f"{e['mode']:5s} {e['length']:4d} {e['variant']:3s} no carrier found")
            continue
        s = stats(r)
        print(f"{e['mode']:5s} {e['length']:4d} {e['variant']:3s} {e['temp']:7.1f} {s['ref'] - NOMINAL[e['mode']]:+20.0f} {s['dev']:14.0f} {s['change']:+18.0f} {s['rate']:14.2f}")


if __name__ == "__main__":
    main()
