#!/bin/bash
# Checks on the PC that the CMSIS-DSP benchmark compares equal computations
# (CMSIS-DSP built with its portable C code; the timings are not meaningful).
set -e
cd "$(dirname "$0")/../.."
D=sw/ext/cmsis-dsp
T=$(mktemp -d); trap 'rm -rf $T' EXIT
gcc -O2 -std=gnu11 -DCMSIS_BENCH_HOST -D__GNUC_PYTHON__ -DDISABLEFLOAT16 -DARM_MATH_LOOPUNROLL \
  -Isw/airborne -Isw/include -I$D/Include -I$D/PrivateInclude -Wno-unused-parameter \
  tests/cmsis_bench/host_check.c sw/airborne/modules/benchmark/cmsis_bench.c \
  sw/airborne/math/pprz_algebra_float.c \
  $D/Source/MatrixFunctions/arm_mat_{init,mult,inverse}_f32.c \
  $D/Source/FilteringFunctions/arm_biquad_cascade_df1_{init_,}f32.c \
  $D/Source/ControllerFunctions/arm_pid_init_f32.c -lm -o $T/check
$T/check
