/* Host check of the CMSIS-DSP benchmark logic (not of its timings): runs
 * modules/benchmark/cmsis_bench.c on the PC with the portable C code of
 * CMSIS-DSP, and checks that Paparazzi and CMSIS give the same results. */
#include <stdio.h>
#include <stdint.h>
#include <time.h>
#include "modules/benchmark/cmsis_bench.h"

extern float cmsis_bench_results[];

uint32_t cmsis_bench_host_cycles(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint32_t)(t.tv_sec * 1000000000ull + t.tv_nsec);  /* "cycles" = ns */
}
uint32_t get_sys_time_usec(void) { return cmsis_bench_host_cycles() / 1000; }
float get_sys_time_float(void) { return 100.f; }

int main(void)
{
  const char *names[] = {"mat_mult 3x3", "mat_mult 6x6", "mat_mult 12x12", "mat_inv 3x3", "mat_inv 6x6",
                         "mat_inv 12x12", "lowpass x64", "PID x64 (update_pid_f)", "PID x64 (same formula)"};
  cmsis_bench_init();
  cmsis_bench_periodic();
  int bad = 0;
  for (int i = 0; i < CMSIS_BENCH_NB_TESTS; i++) {
    float *r = &cmsis_bench_results[1 + 3 * i];
    int ok = r[2] < 0 || r[2] < 1e-4f;
    bad += !ok;
    printf("%-24s max diff %-10g %s\n", names[i], r[2], ok ? "OK" : "DIFFERENT");
  }
  printf(bad ? "FAILED\n" : "ALL RESULTS MATCH\n");
  return bad != 0;
}
