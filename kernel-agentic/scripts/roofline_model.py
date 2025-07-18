import matplotlib.pyplot as plt
import numpy as np

# No guarantees on the accuracy of the data, please double check the data before using it

# Hardware specs
bw = 5.3e12  # bytes/s
perf_a = 122.6e12  # FLOPs/s
perf_x = 163.4e12  # FLOPs/s

# Generate arithmetic intensity range
oi = np.logspace(-1, 3, 1000)  # 0.1 to 1000 FLOPs/Byte

# Bandwidth roofline
perf_bw = oi * bw

# Plot
fig, ax = plt.subplots(figsize=(7, 5))
ax.loglog(oi, perf_bw, label="Memory BW limit (5.3 TB/s)")
ax.hlines(perf_a, oi[0], oi[-1], linestyles="--", label="MI300A peak FP32 122.6 TFLOP/s")
ax.hlines(perf_x, oi[0], oi[-1], linestyles=":", label="MI300X peak FP32 163.4 TFLOP/s")

# Mark intersection points
oi_a = perf_a / bw
oi_x = perf_x / bw
ax.plot(oi_a, perf_a, 'o', label=f"MI300A crossover @ {oi_a:.1f} FLOP/B")
ax.plot(oi_x, perf_x, 's', label=f"MI300X crossover @ {oi_x:.1f} FLOP/B")

ax.set_xlabel("Arithmetic Intensity (FLOPs/Byte)")
ax.set_ylabel("Performance (FLOP/s)")
ax.set_title("Roofline Model – AMD MI300 Series (FP32)")
ax.grid(True, which="both", ls=":")
ax.legend(loc="lower right")
plt.tight_layout()

plt.show()