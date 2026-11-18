# Hey Emacs, this is a -*- makefile -*-
#
#   Copyright (C) 2004 Pascal Brisset, Antoine Drouin
#   Copyright (C) 2012 The Paparazzi Team
#
# This file is part of paparazzi.
#
# paparazzi is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2, or (at your option)
# any later version.
#
# paparazzi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with paparazzi; see the file COPYING.  If not, write to
# the Free Software Foundation, 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#

# Quiet compilation
Q=@

CC = gcc

GLIB_CFLAGS  = -Wall -fPIC $(shell pkg-config glib-2.0 --cflags)
GLIB_LDFLAGS = $(shell pkg-config glib-2.0 --libs)
SDL_LDFLAGS = $(shell sdl-config --libs)

# the joystick agent is input2ivy.py (SDL2 through ctypes); test_stick is a
# small SDL1 C program to check the channels of an input device
all: test_stick

test_stick: test_sdl_stick.o
	@echo BUILD $@
	$(Q)$(CC) -g -O2 -DSTICK_DBG $(GLIB_CFLAGS) -o $@ $^ sdl_stick.c $(GLIB_LDFLAGS) $(SDL_LDFLAGS)

%.o : %.c
	@echo CC $@
	$(Q)$(CC) -c -O2 $(GLIB_CFLAGS) $<

clean:
	$(Q)rm -f *~ core *.o *.bak test*stick

.PHONY: all clean
