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

/** @file modules/benchmark/cmsis_bench.h
 *  Benchmark of ARM CMSIS-DSP against the equivalent Paparazzi code.
 */

#ifndef CMSIS_BENCH_H
#define CMSIS_BENCH_H

#define CMSIS_BENCH_NB_TESTS 9

extern void cmsis_bench_init(void);
extern void cmsis_bench_periodic(void);

#endif /* CMSIS_BENCH_H */
