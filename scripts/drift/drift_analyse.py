#!/usr/bin/env python3
import json
import sys

import numpy as np

d = json.load(open(sys.argv[1]))
S = np.array(d["samples"])            # t, f from the Pluto centre, snr
T = np.array(d["temps"])              # t, degrees C
EV = d["events"]
CENTER_OFF = 100e3                    # the Pluto's centre is 100 kHz below the dial frequency
present = S[:, 2] > 40
# transmissions = runs of present buffers
runs, cur = [], []
for row, p in zip(S, present):
    if p:
        if cur and row[0] - cur[-1][0] > 0.6:
            runs.append(np.array(cur)); cur = []
        cur.append(row)
    elif cur and row[0] - cur[-1][0] > 0.6:
        runs.append(np.array(cur)); cur = []
if cur:
    runs.append(np.array(cur))
runs = [r for r in runs if len(r) >= 5]
print(f"{len(S)} carrier estimates, {len(runs)} transmissions, {len(T)} temperatures; temperature range {T[:,1].min():.1f} .. {T[:,1].max():.1f} C")


def temp_at(t, before=True):
    """The last temperature before t (or the first one after)."""
    if before:
        k = np.flatnonzero(T[:, 0] <= t)
        return T[k[-1], 1] if len(k) else np.nan
    k = np.flatnonzero(T[:, 0] >= t)
    return T[k[0], 1] if len(k) else np.nan


rows = []
for i, r in enumerate(runs):
    t0 = r[0, 0] - 0.17
    dur = r[-1, 0] - t0
    f = r[:, 1]
    tt = r[:, 0] - t0
    rows.append(dict(i=i, t0=t0, dur=dur, tt=tt, f=f, T_before=temp_at(t0 - 0.2), T_after=temp_at(r[-1, 0], False)))
long = [r for r in rows if r["dur"] > 60]
bursts = [r for r in rows if r["dur"] <= 60]
print(f"long transmissions: {[round(r['dur']) for r in long]} s; bursts: {len(bursts)} of about {np.mean([r['dur'] for r in bursts]):.1f} s")

# 1. inside a long transmission
for n, r in enumerate(long):
    tt, f = r["tt"], r["f"]
    print(f"\n=== long transmission {n + 1}: {r['dur']:.0f} s, chip {r['T_before']:.1f} C before, {r['T_after']:.1f} C after")
    f0 = np.median(f[(tt > 1.0) & (tt < 1.5)])
    print("   t [s]   frequency against the value at t=1.2 s [Hz]   (mean of 1 s windows)")
    for a in (1, 2, 3, 5, 8, 12, 20, 30, 45, 60, 80, 100, 125, 145):
        m = (tt >= a) & (tt < a + 1)
        if m.any():
            print(f"   {a:5d}   {np.mean(f[m]) - f0:+8.1f}")
    # fit f = finf + A exp(-t/tau) + b t over t > 1 s
    from scipy.optimize import curve_fit
    m = tt > 1.0
    try:
        p, _ = curve_fit(lambda t, a, A, tau, b: a + A * np.exp(-t / tau) + b * t, tt[m], f[m], p0=[f[m][-1], 100, 20, 0], maxfev=20000)
        res = f[m] - (p[0] + p[1] * np.exp(-tt[m] / p[2]) + p[3] * tt[m])
        print(f"   fit: f = {p[0] - f0:+.0f} Hz {p[1]:+.0f} Hz * exp(-t/{p[2]:.1f} s) {p[3] * 60:+.2f} Hz/min * t   residual {np.std(res):.1f} Hz rms")
    except Exception as e:  # noqa: BLE001
        print("   fit failed:", e)

# 2. the bursts: frequency in the window 1.5 .. 3.0 s after the start against the chip temperature
if bursts:
    fb = np.array([np.median(r["f"][(r["tt"] > 1.5) & (r["tt"] < 3.0)]) for r in bursts])
    tb = np.array([r["T_before"] for r in bursts])
    ta = np.array([r["T_after"] for r in bursts])
    t_since = np.array([r["t0"] for r in bursts])
    ok = np.isfinite(tb) & np.isfinite(fb)
    print(f"\n=== bursts: {ok.sum()} usable; frequency spread {np.std(fb[ok]):.0f} Hz rms over {fb[ok].max() - fb[ok].min():.0f} Hz")
    print("   burst   chip before [C]  after [C]   f (1.5-3 s) against the first burst [Hz]")
    for k, (a, b, c) in enumerate(zip(tb, ta, fb)):
        print(f"   {k:4d}   {a:8.2f}  {b:8.2f}   {c - fb[0]:+8.1f}")
    for name, x in (("temperature before", tb), ("temperature after", ta), ("mean of both", (tb + ta) / 2)):
        m = ok & np.isfinite(x)
        A = np.vstack([x[m], np.ones(m.sum())]).T
        coef, *_ = np.linalg.lstsq(A, fb[m], rcond=None)
        res = fb[m] - A @ coef
        r2 = 1 - np.var(res) / np.var(fb[m])
        print(f"   f = {coef[0]:+.1f} Hz/C * T {coef[1]:+.0f}   ({name}): R^2 {r2:.2f}, residual {np.std(res):.1f} Hz rms (spread without the correction {np.std(fb[m]):.1f} Hz)")

# 3. temperature course
print("\n=== chip temperature (every 10th read)")
t_ref = T[0, 0]
for t, c in T[::10]:
    print(f"   {t - t_ref:7.0f} s   {c:6.2f} C")
