#!/usr/bin/env python3
"""Fit the thermal drift model (espdr.thermal) to measured carriers, also to transmissions that were already corrected (their correction is added back).

usage: fit_thermal.py OUT_MODEL.json  ab1.json [ab2.json ...] [--series drift-series.json] [--per-run-offset] [--fixed k=19.1,t0=47.7]
       fit_thermal.py --eval MODEL.json  ab1.json ... [--series ...]      (no fit: the residuals of a model, with the offset it contains)
Prints the parameters and, for every transmission, the residual before and after the fit.
"""
import json
import sys

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, __file__.rsplit("/", 3)[0] + "/src")
from espdr import thermal  # noqa: E402

NOMINAL = {"usb": 101000.0, "rtty": 102222.0, "fm": 100000.0}
NAMES = ["k", "t0", "t_inf", "tau_chip", "tau_xtal", "early_hz", "early_tau"]          # plus one offset c_<mode> per kind of transmission (fm, usb, rtty)
START = dict(k=19.1, t0=47.7, t_inf=56.0, tau_chip=37.0, tau_xtal=6.0, early_hz=0.0, early_tau=10.0)
BOUNDS = dict(k=(0, 60), t0=(38, 60), t_inf=(50, 58), tau_chip=(5, 300), tau_xtal=(0.5, 120), early_hz=(-1500, 1500), early_tau=(1, 120))


def runs_ab(d, used_params):
    A = np.array(d["samples"])
    ev = d["events"]
    out = []
    for s, e in zip([x for x in ev if x["kind"] == "start"], [x for x in ev if x["kind"] == "end"]):
        m = (A[:, 0] >= s["t"] - 0.2) & (A[:, 0] <= e["t"] + 0.3) & (A[:, 2] > 40)
        r = A[m]
        if len(r) < 5:
            continue
        col = 3 if s["mode"] == "rtty" else 1
        t = r[:, 0] - (r[0, 0] - 0.17)
        f = r[:, col] - NOMINAL[s["mode"]]
        if s["variant"] == "B":                              # what the model had cancelled is added back
            f = f + thermal.error_hz(t, s["temp"], used_params)
        out.append(dict(mode=s["mode"], name=f"{s['mode']} {s['length']} s {s['variant']}", Ts=s["temp"], t=t, f=f))
    return out


def runs_series(d):
    A = np.array(d["samples"])
    T = np.array(d["temps"])
    ev = d["events"]
    starts = [x for x in ev if x[1] in ("burst", "long1", "long2")]
    ends = [x for x in ev if x[1].endswith(" end")]
    out = []
    for s, e in zip(starts, ends):
        m = (A[:, 0] >= s[0] - 0.2) & (A[:, 0] <= e[0] + 0.3) & (A[:, 2] > 40)
        r = A[m]
        if len(r) < 5:
            continue
        k = np.flatnonzero(T[:, 0] <= s[0] + 0.3)
        out.append(dict(mode="fm", name=f"fm {s[1]} {e[0] - s[0]:.0f} s", Ts=T[k[-1], 1], t=r[:, 0] - (r[0, 0] - 0.17), f=r[:, 1] - NOMINAL["fm"]))
    return out


def model(p, run):
    q = dict(thermal.DEFAULT, **{k: p[k] for k in NAMES})
    return p.get("c_" + run["mode"], 0.0) + thermal.error_hz(run["t"], run["Ts"], q)


def main():
    argv = sys.argv[1:]
    evaluate = None
    if argv[0] == "--eval":
        argv.pop(0)
        evaluate = argv.pop(0)
        out = None
    else:
        out = argv.pop(0)
    series, files, per_run, fixed = [], [], False, {}
    while argv:
        a = argv.pop(0)
        if a == "--series":
            series.append(argv.pop(0))
        elif a == "--per-run-offset":
            per_run = True
        elif a == "--fixed":
            fixed = {kv.split("=")[0]: float(kv.split("=")[1]) for kv in argv.pop(0).split(",")}
        else:
            files.append(a)
    runs = []
    for f in files:
        runs += runs_ab(json.load(open(f)), thermal.DEFAULT)
    for f in series:
        runs += runs_series(json.load(open(f)))
    runs = [r for r in runs if len(r["t"]) > 4]
    # 1 s medians (equal weight for every second of every transmission)
    pts = []
    for r in runs:
        tt = np.arange(0.5, r["t"][-1], 1.0)
        pts.append(dict(mode=r["mode"], name=r["name"], Ts=r["Ts"], t=np.array([x + 0.5 for x in tt]), f=np.array([np.median(r["f"][(r["t"] >= x) & (r["t"] < x + 1.0)]) if ((r["t"] >= x) & (r["t"] < x + 1.0)).any() else np.nan for x in tt])))
    for r in pts:
        ok = np.isfinite(r["f"])
        r["t"], r["f"] = r["t"][ok], r["f"][ok]
    if evaluate:
        m = json.load(open(evaluate))
        p = dict(START, **{k: v for k, v in m.items() if k in NAMES or k.startswith("c_")})
        print(f"{'transmission':16s} {'Ts':>5s} {'rms (spread)':>13s} {'rms of f - model':>17s} {'mean of f - model':>18s} {'max |f - model - mean|':>23s}")
        allr = []
        for r in pts:
            res = r["f"] - model(p, r)
            allr.append(res)
            print(f"{r['name']:16s} {r['Ts']:5.1f} {np.std(r['f']):13.0f} {np.sqrt(np.mean(res ** 2)):17.0f} {np.mean(res):+18.0f} {np.max(np.abs(res - np.mean(res))):23.0f}")
        allr = np.concatenate(allr)
        print(f"all: spread {np.std(np.concatenate([r['f'] for r in pts])):.0f} Hz rms, model residual {np.sqrt(np.mean(allr ** 2)):.0f} Hz rms, scatter after removing a common offset {np.std(allr):.0f} Hz")
        return
    modes = sorted({r["mode"] for r in pts})
    START.update({"c_" + m: 0.0 for m in modes})
    BOUNDS.update({"c_" + m: (-3000, 3000) for m in modes})
    free = [n for n in NAMES + ["c_" + m for m in modes] if n not in fixed]
    x0 = [START[n] for n in free]
    lo = [BOUNDS[n][0] for n in free] + [-2000] * (len(pts) if per_run else 0)
    hi = [BOUNDS[n][1] for n in free] + [2000] * (len(pts) if per_run else 0)
    x0 += [0.0] * (len(pts) if per_run else 0)

    def params(x):
        p = dict(START, **fixed)
        p.update(dict(zip(free, x[:len(free)])))
        return p

    def resid(x):
        p = params(x)
        return np.concatenate([r["f"] - model(p, r) - (x[len(free) + i] if per_run else 0.0) for i, r in enumerate(pts)])

    best = None
    for k0 in (10.0, 25.0):
        for tx0 in (4.0, 20.0):
            xs = list(x0)
            xs[free.index("k")] = k0
            xs[free.index("tau_xtal")] = tx0 if "tau_xtal" in free else xs[free.index("tau_xtal")] if False else tx0
            sol = least_squares(resid, np.clip(xs, lo, hi), bounds=(lo, hi))
            if best is None or sol.cost < best.cost:
                best = sol
    p = params(best.x)
    print("model:", {k: round(v, 3) for k, v in p.items()})
    print(f"{len(pts)} transmissions, {sum(len(r['t']) for r in pts)} one-second points")
    print(f"{'transmission':16s} {'Ts':>5s} {'rms before':>11s} {'rms after':>10s}")
    tot0 = tot1 = []
    for i, r in enumerate(pts):
        off = best.x[len(free) + i] if per_run else 0.0
        before = r["f"] - np.mean(r["f"])
        after = r["f"] - model(p, r) - off
        print(f"{r['name']:16s} {r['Ts']:5.1f} {np.std(before):11.0f} {np.std(after):10.0f}")
    allr = resid(best.x)
    print(f"all points: rms {np.std(np.concatenate([r['f'] for r in pts])):.0f} Hz before (spread), {np.sqrt(np.mean(allr ** 2)):.0f} Hz after the fit")
    json.dump({k: float(v) for k, v in p.items()} | {"idle": thermal.DEFAULT["idle"]}, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
