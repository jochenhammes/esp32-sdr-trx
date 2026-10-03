---
name: Board / hardware report
about: Tell us what you tried the narrowband mode on, whether it worked or not
title: "[hardware] <board> on <OS>"
labels: hardware report
---

**Board** (name, ESP32-S3 module, number of USB ports, USB-UART bridge chip):

**Host** (OS, kernel, Python version, USB port type / hub):

**Release or commit used:**

**Result** (loaded? `bench` MB/s? a run of 60 s? SDR++ via the bridge?):

**Output of**

```
lsusb | grep -iE "303a|1a86|10c4|0403"
python host/python/espdr_nb.py bench --seconds 5
python host/python/espdr_nb.py dspbench
```

**Anything odd** (spurs, wrong frequency, resets, ports that changed):
