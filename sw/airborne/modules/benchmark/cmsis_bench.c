/*
 * Copyright (C) 2026 UCM
 *
 * This file is part of paparazzi.
 *
 * paparazzi is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2, or (at your option)
 * any later version.
 */

/** @file modules/benchmark/cmsis_bench.c
 *  Benchmark of ARM CMSIS-DSP against the equivalent Paparazzi code.
 *
 *  Times are CPU cycles counted by the Cortex-M DWT cycle counter; for each
 *  test the smallest of several repetitions is kept (an interrupt during a
 *  repetition only makes that one longer).
 */

#include "modules/benchmark/cmsis_bench.h"
#include "std.h"
#include "math/pprz_algebra_float.h"
#include "filters/low_pass_filter.h"
#include "filters/pid.h"
#ifndef CMSIS_BENCH_HOST
#include "mcu_periph/sys_time.h"
#include "pprzlink/messages.h"
#include "modules/datalink/downlink.h"
#else
extern uint32_t get_sys_time_usec(void);
extern float get_sys_time_float(void);
#define DOWNLINK_SEND_PAYLOAD_FLOAT(_c, _d, _n, _v) {}
#endif
#include "arm_math.h"
#include <math.h>
#include <string.h>

#ifndef CMSIS_BENCH_HOST
/* Cortex-M debug registers (same addresses on M4 and M7) */
#define DEMCR       (*(volatile uint32_t *)0xE000EDFC)
#define DWT_CTRL    (*(volatile uint32_t *)0xE0001000)
#define DWT_CYCCNT  (*(volatile uint32_t *)0xE0001004)
#define DWT_LAR     (*(volatile uint32_t *)0xE0001FB0)
#else
/* host check of the benchmark logic (tests/cmsis_bench/host_check.sh) */
extern uint32_t cmsis_bench_host_cycles(void);
static uint32_t DEMCR, DWT_CTRL, DWT_LAR, DWT_CYCCNT;
#define cycles() cmsis_bench_host_cycles()
#endif

#define CMSIS_BENCH_DELAY_S 5.f   // run the benchmark this long after boot
#define BLOCK 64                  // samples per filter / PID test
#define NMAX 12

float cmsis_bench_results[1 + 3 * CMSIS_BENCH_NB_TESTS];
#define results cmsis_bench_results
static bool done;
volatile float cmsis_bench_sink;  // keeps the compiler from removing work

#ifndef CMSIS_BENCH_HOST
static inline uint32_t cycles(void) { return DWT_CYCCNT; }
#endif

/* deterministic pseudo-random numbers in [-1, 1] */
static uint32_t seed = 12345;
static float rnd(void)
{
  seed = seed * 1664525u + 1013904223u;
  return (float)(seed >> 8) / 8388608.f - 1.f;
}

static float a_m[NMAX][NMAX], b_m[NMAX][NMAX], o_pprz[NMAX][NMAX], o_cmsis[NMAX][NMAX];

static float max_diff(float x[NMAX][NMAX], float y[NMAX][NMAX], int n)
{
  float d = 0.f;
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
      float e = fabsf(x[i][j] - y[i][j]);
      if (e > d) { d = e; }
    }
  }
  return d;
}

/* CMSIS wants contiguous n x n data: pack / unpack the NMAX x NMAX arrays */
static float pa[NMAX * NMAX], pb[NMAX * NMAX], po[NMAX * NMAX];
static void pack(float *dst, float src[NMAX][NMAX], int n)
{
  for (int i = 0; i < n; i++) { for (int j = 0; j < n; j++) { dst[i * n + j] = src[i][j]; } }
}
static void unpack(float dst[NMAX][NMAX], const float *src, int n)
{
  for (int i = 0; i < n; i++) { for (int j = 0; j < n; j++) { dst[i][j] = src[i * n + j]; } }
}

static void fill(int n, bool invertible)
{
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < n; j++) {
      a_m[i][j] = rnd();
      b_m[i][j] = rnd();
    }
    if (invertible) { a_m[i][i] += (float)n; }  // diagonally dominant
  }
}

static void bench_mult(int n, float *r)
{
  uint32_t best_p = UINT32_MAX, best_c = UINT32_MAX;
  fill(n, false);
  MAKE_MATRIX_PTR(ap, a_m, NMAX);
  MAKE_MATRIX_PTR(bp, b_m, NMAX);
  MAKE_MATRIX_PTR(op, o_pprz, NMAX);
  pack(pa, a_m, n);
  pack(pb, b_m, n);
  arm_matrix_instance_f32 A, B, O;
  arm_mat_init_f32(&A, n, n, pa);
  arm_mat_init_f32(&B, n, n, pb);
  arm_mat_init_f32(&O, n, n, po);
  for (int rep = 0; rep < 20; rep++) {
    uint32_t t0 = cycles();
    float_mat_mul(op, ap, bp, n, n, n);
    uint32_t t1 = cycles();
    arm_mat_mult_f32(&A, &B, &O);
    uint32_t t2 = cycles();
    if (t1 - t0 < best_p) { best_p = t1 - t0; }
    if (t2 - t1 < best_c) { best_c = t2 - t1; }
  }
  unpack(o_cmsis, po, n);
  r[0] = best_p;
  r[1] = best_c;
  r[2] = max_diff(o_pprz, o_cmsis, n);
}

static void bench_inv(int n, float *r)
{
  uint32_t best_p = UINT32_MAX, best_c = UINT32_MAX;
  fill(n, true);
  MAKE_MATRIX_PTR(ap, a_m, NMAX);
  MAKE_MATRIX_PTR(op, o_pprz, NMAX);
  static float pwork[NMAX * NMAX];
  arm_matrix_instance_f32 A, O;
  arm_mat_init_f32(&A, n, n, pwork);
  arm_mat_init_f32(&O, n, n, po);
  for (int rep = 0; rep < 10; rep++) {
    uint32_t t0 = cycles();
    float_mat_invert(op, ap, n);
    uint32_t t1 = cycles();
    pack(pwork, a_m, n);           // the copy is counted: CMSIS works in place
    arm_mat_inverse_f32(&A, &O);
    uint32_t t2 = cycles();
    if (t1 - t0 < best_p) { best_p = t1 - t0; }
    if (t2 - t1 < best_c) { best_c = t2 - t1; }
  }
  unpack(o_cmsis, po, n);
  r[0] = best_p;
  r[1] = best_c;
  r[2] = max_diff(o_pprz, o_cmsis, n);
}

static float in[BLOCK], out_p[BLOCK], out_c[BLOCK];

static void bench_lowpass(float *r)
{
  uint32_t best_p = UINT32_MAX, best_c = UINT32_MAX;
  for (int i = 0; i < BLOCK; i++) { in[i] = rnd(); }
  Butterworth2LowPass lp;
  float coeffs[5], bq_state[4];
  arm_biquad_casd_df1_inst_f32 bq;
  for (int rep = 0; rep < 10; rep++) {
    init_butterworth_2_low_pass(&lp, 0.05f, 0.01f, 0.f);
    /* same filter in CMSIS form: y = b0 x + b1 x1 + b2 x2 + a1 y1 + a2 y2 */
    coeffs[0] = lp.b[0]; coeffs[1] = lp.b[1]; coeffs[2] = lp.b[0];
    coeffs[3] = -lp.a[0]; coeffs[4] = -lp.a[1];
    arm_biquad_cascade_df1_init_f32(&bq, 1, coeffs, bq_state);
    uint32_t t0 = cycles();
    for (int i = 0; i < BLOCK; i++) { out_p[i] = update_butterworth_2_low_pass(&lp, in[i]); }
    uint32_t t1 = cycles();
    arm_biquad_cascade_df1_f32(&bq, in, out_c, BLOCK);
    uint32_t t2 = cycles();
    if (t1 - t0 < best_p) { best_p = t1 - t0; }
    if (t2 - t1 < best_c) { best_c = t2 - t1; }
  }
  float d = 0.f;
  for (int i = 0; i < BLOCK; i++) { float e = fabsf(out_p[i] - out_c[i]); if (e > d) { d = e; } }
  r[0] = best_p;
  r[1] = best_c;
  r[2] = d;
}

/* the CMSIS PID formula in plain C:
 * y[n] = y[n-1] + A0 x[n] + A1 x[n-1] + A2 x[n-2] */
struct plain_pid { float A0, A1, A2, x1, x2, y; };
static inline float plain_pid_step(struct plain_pid *p, float x)
{
  p->y = p->y + p->A0 * x + p->A1 * p->x1 + p->A2 * p->x2;
  p->x2 = p->x1;
  p->x1 = x;
  return p->y;
}

static void bench_pid(float *r_pprz, float *r_plain)
{
  uint32_t best_p = UINT32_MAX, best_c = UINT32_MAX, best_q = UINT32_MAX;
  for (int i = 0; i < BLOCK; i++) { in[i] = rnd(); }
  const float kp = 1.2f, ki = 0.3f, kd = 0.05f;
  struct PID_f pid;
  arm_pid_instance_f32 apid;
  struct plain_pid q;
  float sum_p = 0.f;
  for (int rep = 0; rep < 10; rep++) {
    init_pid_f(&pid, kp, kd, ki, 100.f);
    apid.Kp = kp; apid.Ki = ki; apid.Kd = kd;
    arm_pid_init_f32(&apid, 1);
    q = (struct plain_pid) { kp + ki + kd, -kp - 2.f * kd, kd, 0.f, 0.f, 0.f };
    uint32_t t0 = cycles();
    for (int i = 0; i < BLOCK; i++) { sum_p += update_pid_f(&pid, in[i], 0.01f); }
    uint32_t t1 = cycles();
    for (int i = 0; i < BLOCK; i++) { out_c[i] = arm_pid_f32(&apid, in[i]); }
    uint32_t t2 = cycles();
    for (int i = 0; i < BLOCK; i++) { out_p[i] = plain_pid_step(&q, in[i]); }
    uint32_t t3 = cycles();
    if (t1 - t0 < best_p) { best_p = t1 - t0; }
    if (t2 - t1 < best_c) { best_c = t2 - t1; }
    if (t3 - t2 < best_q) { best_q = t3 - t2; }
  }
  cmsis_bench_sink = sum_p;
  float d = 0.f;
  for (int i = 0; i < BLOCK; i++) { float e = fabsf(out_p[i] - out_c[i]); if (e > d) { d = e; } }
  r_pprz[0] = best_p;  r_pprz[1] = best_c;  r_pprz[2] = -1.f;  // different algorithms
  r_plain[0] = best_q; r_plain[1] = best_c; r_plain[2] = d;
}

void cmsis_bench_init(void)
{
  DEMCR |= (1u << 24);       // TRCENA: enable the DWT
  DWT_LAR = 0xC5ACCE55;      // unlock (needed on Cortex-M7, ignored on M4)
  DWT_CYCCNT = 0;
  DWT_CTRL |= 1u;            // CYCCNTENA
  done = false;
  memset(results, 0, sizeof(results));
}

static void run(void)
{
  /* CPU clock: cycles counted during 20 ms of system time */
  uint32_t u0 = get_sys_time_usec(), c0 = cycles();
  while (get_sys_time_usec() - u0 < 20000) {}
  results[0] = (float)(cycles() - c0) / (float)(get_sys_time_usec() - u0);

  float *r = &results[1];
  bench_mult(3, r);  r += 3;
  bench_mult(6, r);  r += 3;
  bench_mult(12, r); r += 3;
  bench_inv(3, r);   r += 3;
  bench_inv(6, r);   r += 3;
  bench_inv(12, r);  r += 3;
  bench_lowpass(r);  r += 3;
  bench_pid(r, r + 3);
  done = true;
}

void cmsis_bench_periodic(void)
{
  if (!done) {
    if (get_sys_time_float() < CMSIS_BENCH_DELAY_S) { return; }
    run();
  }
  DOWNLINK_SEND_PAYLOAD_FLOAT(DefaultChannel, DefaultDevice, 1 + 3 * CMSIS_BENCH_NB_TESTS, results);
}
