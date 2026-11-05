# NVPCF route trace for the 620 nvidia-powerd

[`scripts/powerd620-nvpcf-branch.bt`](../scripts/powerd620-nvpcf-branch.bt) records which NVPCF route the
unmodified `nvidia-powerd` takes at start. It does not change the GPU clock. Its 11 probes only read registers
and memory, for the bpftrace child only. The traced `nvidia-powerd` makes its normal vendor calls, which can set
platform values. The trace forces and overrides nothing. The offsets fit only the 620.70.06 binary below.

## Run

Boot the `@nv620` root with bpftrace 0.27.0 and no `nvidia-powerd` process. Start the trace once.

```bash
R=<new private run directory>; mkdir -m 700 "$R"; cd "$R"
[ "$(findmnt -no FSROOT /)" = /@nv620 ] || exit 9
echo 'eb9e0371f73667f242e8d4742e17a21a68470e08cdc6438a84c263a4c3b6f4d2  /usr/bin/nvidia-powerd' | sha256sum -c --quiet || exit 9
[ "$(pgrep -xc nvidia-powerd)" = 0 ] || exit 9
sudo -n timeout -k 5 60 bpftrace -k -v -q -o T-trace.txt -c /usr/bin/nvidia-powerd \
  <repository>/scripts/powerd620-nvpcf-branch.bt 2> T-stderr.txt; echo "rc=$?" > T-rc.txt
pgrep -xc nvidia-powerd; pgrep -xc bpftrace
```

`-k` reports probe-read errors, which bpftrace hides by default. `-v` logs `Child exited with code: N` or
`Child terminated by signal: N`. At 60 s, bpftrace gets TERM, runs END and kills the child. If a process remains
after the run, record its PID and stop.

## Read the result

Report the raw lines, then apply these rules in order.

1. **VALID** only if `T-rc.txt` is `rc=0`, stderr has `Child exited with code: 0`, the trace has BEGIN, END and
   `factory_create_entry`, and stderr has no `Failed to probe_read`, lost-event, attach or parse error.
   Otherwise INCONCLUSIVE. A timeout (124), a kill (137) or a signal-stopped child is never a normal exit.
2. **Route (a), 3x init:** `nvpcf3x_init_ret bool=1`, `factory_3x_true_branch`, `main_no_worker`. Report
   `nvpcf3x_objprim_ptr` with it: 0 means the wrapper skipped its call, nonzero means only that the call path
   was taken. Neither shows a running controller.
3. **Route (b), fallback to 2x/1x:** `nvpcf3x_init_ret bool=0`, `factory_3x_false_branch`, `nvpcf2x_ret` (raw).
4. **Anything else:** INCONCLUSIVE, with the events listed.

Report `nvpcf3x_query_raw` for both routes. It does not decide the route. `has_value=0` means the query failed
with the status in the low 32 bits (`0x56` is not supported). `has_value=1` means only that the query
succeeded. `quit_entry w0=0x2` decides nothing, because 2 is also SIGINT. Report `factory_mobile_clear` if it
occurs.
