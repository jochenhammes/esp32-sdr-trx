import json, sys
import numpy as np
from scipy.optimize import least_squares

d = json.load(open(sys.argv[1]))
A = np.array(d["samples"]); T = np.array(d["temps"]); EV = d["events"]
t0 = T[0, 0]
pres = A[:, 2] > 40
idx = np.flatnonzero(pres); runs = []; s = p = idx[0]
for i in idx[1:]:
    if A[i, 0] - A[p, 0] > 0.6: runs.append((s, p)); s = i
    p = i
runs.append((s, p))
R = [A[a:b + 1] for a, b in runs]
starts = [(e[0] - t0, e[1]) for e in EV if e[1] in ("burst", "long1", "long2")]
ends = [e[0] - t0 for e in EV if e[1].endswith(" end")]
segs = [(a, b, k) for (a, k), b in zip(starts, ends)]
tm = T[:, 0] - t0; Tm = T[:, 1]
dt = 0.5
grid = np.arange(0, tm[-1] + 5, dt)
# which grid points are inside a transmission, and the start temperature of that transmission
in_tx = np.zeros(len(grid), bool); seg_of = -np.ones(len(grid), int)
for j, (a, b, k) in enumerate(segs):
    m = (grid >= a + 1.0) & (grid <= b); in_tx |= m; seg_of[m] = j
T_start = [np.interp(a, tm, Tm) for a, b, k in segs]
# data: carrier frequency samples after the first 6 s of long runs and 1.6 s of bursts
data = []
for r, (a, b, k) in zip(R, segs):
    tt = r[:, 0] - t0
    rel = tt - (r[0, 0] - t0 - 0.17)
    m = rel > (6.0 if k.startswith("long") else 1.6)
    data.append((tt[m] - 0.3, r[m, 1], k))          # the Pluto's time stamps run about 0.3 s late (buffering)

def simulate(p):
    c, kq, T0, tau_h, Tinf, tau_x = p
    Td = np.interp(grid, tm, Tm)
    for j, (a, b, k) in enumerate(segs):
        m = seg_of == j
        if m.any():
            Td[m] = Tinf - (Tinf - T_start[j]) * np.exp(-(grid[m] - a - 1.0) / tau_h)
    Tx = np.empty_like(Td); Tx[0] = Td[0]
    al = dt / tau_x
    for i in range(1, len(Td)):
        Tx[i] = Tx[i - 1] + al * (Td[i] - Tx[i - 1])
    return c + kq * (Tx - T0) ** 2, Td, Tx

def resid(p):
    f_grid, _, _ = simulate(p)
    out = []
    for tt, f, k in data:
        w = 0.3 if k.startswith("long") else 1.0
        out.extend(w * (f[::4 if k.startswith("long") else 1] - np.interp(tt[::4 if k.startswith("long") else 1], grid, f_grid)))
    return np.array(out)

best = None
for T0 in (44, 48):
    for tau_x in (10, 40):
        for tau_h in (30, 80):
            sol = least_squares(resid, [99400, 12, T0, tau_h, 60, tau_x], bounds=([98000, 0, 35, 5, 50, 1], [101000, 100, 60, 400, 120, 400]))
            if best is None or sol.cost < best.cost: best = sol
c, kq, T0, tau_h, Tinf, tau_x = best.x
print(f"f = {c:.0f} Hz + {kq:.2f} Hz/C^2 * (Tx - {T0:.1f} C)^2;  crystal temperature follows the chip with tau_x = {tau_x:.0f} s;  heating in a transmission: T -> {Tinf:.1f} C with tau = {tau_h:.0f} s")
f_grid, Td, Tx = simulate(best.x)
tot = []
for tt, f, k in data:
    rr = f - np.interp(tt, grid, f_grid); tot.append((k, np.std(f), np.std(rr), np.abs(rr).max()))
for name in ("long1", "long2"):
    for k, sd, sr, mx in tot:
        if k == name: print(f"{name}: spread {sd:.0f} Hz rms -> residual {sr:.1f} Hz rms (max {mx:.0f})")
b = [x for x in tot if x[0] == "burst"]
print(f"bursts ({len(b)}): spread over the series {np.std(np.concatenate([f for tt, f, k in data if k == 'burst'])):.0f} Hz rms -> residual {np.sqrt(np.mean([x[2] ** 2 for x in b])):.1f} Hz rms (max {max(x[3] for x in b):.0f})")
allr = np.concatenate([f - np.interp(tt, grid, f_grid) for tt, f, k in data]); allf = np.concatenate([f for tt, f, k in data])
print(f"everything: {np.std(allf):.0f} Hz rms -> {np.std(allr):.1f} Hz rms")
np.save(sys.argv[1].replace('.json', '_lagfit.npy'), best.x)
