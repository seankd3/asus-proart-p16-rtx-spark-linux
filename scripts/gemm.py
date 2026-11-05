#!/usr/bin/env python3
"""Exact dense GEMM through the vendor cuBLAS, with FP32 or BF16 inputs.
Each window repeats C = A*B (n=4096, column-major, CUBLAS_OP_N, alpha 1, beta 0) through cublasGemmEx and
synchronizes after every call. It prints the achieved GFLOP/s measured two ways: host wall clock summed over the
synchronized calls, and a CUDA event span over the whole window (which also includes the short idle gaps between
calls). Both input types write FP32 output and accumulate in FP32 (CUBLAS_COMPUTE_32F) with the default algorithm.
A and B hold small integers, which are exact in BF16 and FP32. Every product and partial sum is an exact FP32
integer, so the result is checked exactly on sampled entries after the warm-up call and at the end. No timed
work runs after a failed warm-up check. No math mode is set (the default is reported).
The dynamic loader finds libcublas.so.13 (the CUDA toolkit, or LD_LIBRARY_PATH) and libcuda.so.1 (the driver).
Exit 0: both checks passed. 1: usage, or a CUDA or cuBLAS call failed. 3: an exact check failed.
Usage: gemm.py [SECONDS [fp32|bf16]]   (default 60 fp32, max 300)"""
import array, ctypes as C, sys, time
ROWS = {"fp32": (0, 4), "bf16": (14, 2)}                   # inputs: cudaDataType (CUDA_R_32F, CUDA_R_16BF), bytes
CTYPE, COMPUTE, ALGO = 0, 68, -1                           # CUDA_R_32F, CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT
try:
    secs, name = sys.argv[1:] + ["60", "fp32"][len(sys.argv) - 1:]   # missing arguments take the defaults
    secs, (atype, width) = min(float(secs), 300), ROWS[name]
except (ValueError, KeyError):
    raise SystemExit(__doc__.splitlines()[-1])
N = 4096
FLOP = 2.0 * N * N * N

cu = C.CDLL("libcuda.so.1")
bl = C.CDLL("libcublas.so.13")
def ck(r, what):
    if r: raise SystemExit(f"{what} failed: status {r}")
bl.cublasGemmEx.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p,   # handle..k, alpha
                            C.c_void_p, C.c_int, C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_void_p,  # A, B, beta
                            C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int]                       # C, compute, algo
cu.cuMemAlloc_v2.argtypes = [C.POINTER(C.c_uint64), C.c_size_t]  # CUdeviceptr = unsigned long long (64-bit)
cu.cuMemFree_v2.argtypes = [C.c_uint64]
cu.cuMemcpyHtoD_v2.argtypes = [C.c_uint64, C.c_char_p, C.c_size_t]
cu.cuMemcpyDtoH_v2.argtypes = [C.c_void_p, C.c_uint64, C.c_size_t]

ck(cu.cuInit(0), "cuInit")
dev = C.c_int(); ck(cu.cuDeviceGet(C.byref(dev), 0), "cuDeviceGet")
def attr(a): v = C.c_int(); ck(cu.cuDeviceGetAttribute(C.byref(v), a, dev), f"attr {a}"); return v.value
drv = C.c_int(); ck(cu.cuDriverGetVersion(C.byref(drv)), "cuDriverGetVersion")
ctx = C.c_void_p(); ck(cu.cuDevicePrimaryCtxRetain(C.byref(ctx), dev), "cuDevicePrimaryCtxRetain")
ck(cu.cuCtxSetCurrent(ctx), "cuCtxSetCurrent")              # cuBLAS's runtime uses this primary context
h = C.c_void_p(); ck(bl.cublasCreate_v2(C.byref(h)), "cublasCreate")
ver = C.c_int(); ck(bl.cublasGetVersion_v2(h, C.byref(ver)), "cublasGetVersion")
mode = C.c_int(); ck(bl.cublasGetMathMode(h, C.byref(mode)), "cublasGetMathMode")
print(f"driver {drv.value} cublas {ver.value} cc {attr(75)}.{attr(76)} sms {attr(16)} math_mode {mode.value} n {N} "
      f"inputs {name} atype {atype} ctype {CTYPE} compute {COMPUTE} algo {ALGO}", flush=True)

def a(i, l): return (i + 2 * l) % 5 - 2                    # |a| <= 2
def b(l, j): return (3 * l + j) % 7 - 3                    # |b| <= 3; |sum| <= 6*N = 24576 < 2**24: exact
def image(fp32):                                           # top `width` bytes of each little-endian FP32 value:
    out = bytearray(len(fp32) // 4 * width)                # all of it, or its BF16 image (exact for these integers)
    for k in range(width): out[k::width] = fp32[4 - width + k::4]
    return out
def matrix(f, period):                                     # column-major; column c = f(row, c), periodic in c
    cols = [image(array.array("f", (f(r, c) for r in range(N))).tobytes()) for c in range(period)]
    return b"".join(cols[c % period] for c in range(N))
hostA = matrix(a, 5); hostB = matrix(b, 7)
dA, dB, dC = C.c_uint64(), C.c_uint64(), C.c_uint64()
for d, size in ((dA, width), (dB, width), (dC, 4)): ck(cu.cuMemAlloc_v2(C.byref(d), N * N * size), "cuMemAlloc")
ck(cu.cuMemcpyHtoD_v2(dA.value, hostA, len(hostA)), "HtoD A"); ck(cu.cuMemcpyHtoD_v2(dB.value, hostB, len(hostB)), "HtoD B")
del hostA, hostB
one, zero = C.c_float(1), C.c_float(0)
def gemm():
    ck(bl.cublasGemmEx(h, 0, 0, N, N, N, C.byref(one), dA.value, atype, N, dB.value, atype, N, C.byref(zero),
                       dC.value, CTYPE, N, COMPUTE, ALGO), "cublasGemmEx")
SAMPLES = [(i * 613 % N, j * 1031 % N) for i, j in zip(range(64), range(7, 71))] + [(0, 0), (N - 1, N - 1)]
def verify(tag):
    out = (C.c_float * (N * N))()
    ck(cu.cuMemcpyDtoH_v2(C.addressof(out), dC.value, N * N * 4), "DtoH C")
    bad = [(i, j) for i, j in SAMPLES if out[i + j * N] != sum(a(i, l) * b(l, j) for l in range(N))]
    print(f"verify {tag} sampled {len(SAMPLES)} mismatches {len(bad)} {bad[:3]}", flush=True)
    return not bad

gemm(); ck(cu.cuCtxSynchronize(), "warm-up sync")             # library load / heuristics, not timed
ok = verify("warmup")
e0, e1 = C.c_void_p(), C.c_void_p()
ck(cu.cuEventCreate(C.byref(e0), 0), "cuEventCreate"); ck(cu.cuEventCreate(C.byref(e1), 0), "cuEventCreate")
end = time.monotonic() + (secs if ok else 0)               # bad warm-up math: no timed work, exit 3
while time.monotonic() < end:
    calls, host, ws = 0, 0.0, time.monotonic(); wend = ws + 1.0
    ck(cu.cuEventRecord(e0, None), "cuEventRecord")
    while time.monotonic() < wend:
        t = time.perf_counter(); gemm(); ck(cu.cuCtxSynchronize(), "sync"); host += time.perf_counter() - t; calls += 1
    ck(cu.cuEventRecord(e1, None), "cuEventRecord"); ck(cu.cuEventSynchronize(e1), "cuEventSynchronize")
    ms = C.c_float(); ck(cu.cuEventElapsedTime(C.byref(ms), e0, e1), "cuEventElapsedTime")
    print(f"{time.strftime('%H:%M:%S')} calls {calls} host_s {host:.4f} event_s {ms.value / 1e3:.4f} "
          f"host_gflops {calls * FLOP / host / 1e9:.1f} event_gflops {calls * FLOP / (ms.value / 1e3) / 1e9:.1f} "
          f"mono_start {ws:.3f} mono_end {time.monotonic():.3f}", flush=True)   # CLOCK_MONOTONIC, shared by all processes
ok = ok and verify("end")
for d in (dA, dB, dC): ck(cu.cuMemFree_v2(d.value), "cuMemFree")
ck(bl.cublasDestroy_v2(h), "cublasDestroy"); ck(cu.cuDevicePrimaryCtxRelease_v2(dev), "cuDevicePrimaryCtxRelease")
sys.exit(0 if ok else 3)
