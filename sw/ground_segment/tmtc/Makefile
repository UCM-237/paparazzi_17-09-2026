# Hey Emacs, this is a -*- makefile -*-
#
#   Copyright (C) 2003-2006 Pascal Brisset, Antoine Drouin
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

# Quiet
Q=@

include ../../../conf/Makefile.local
CONF = ../../../conf
VAR = ../../../var

# the Ivy tools of this directory are Python scripts (link.py, server.py,
# messages.py, ivy2udp.py, ...); only the C ones are built here
all: app_server ivy2nmea gpsd2ivy

clean:
	$(Q)rm -f *.bak *~ core *.o app_server gpsd2ivy c_ivy_client_example_1 c_ivy_client_example_2 ivy2nmea

CC = gcc

GLIBIVY_CFLAGS = -Wall -fPIC $(shell pkg-config --cflags ivy-glib)
GLIBIVY_LDFLAGS = $(shell pkg-config --libs ivy-glib) $(shell pcre-config --libs)

#
# try to find include locations on OSX
#
UNAME = $(shell uname -s)
ifeq ("$(UNAME)","Darwin")
  IVY_C_LIB_INC = $(shell if test -d /opt/paparazzi/lib; then echo "-L/opt/paparazzi/lib"; elif test -d /opt/local/lib; then echo "-L/opt/local/lib"; fi)
  IVY_C_INCLUDES = $(shell if test -d /opt/paparazzi/include; then echo "-I/opt/paparazzi/include"; elif test -d /opt/local/include; then echo "-I/opt/local/include"; fi)
  GLIBIVY_CFLAGS += $(IVY_C_LIB_INC) $(IVY_C_INCLUDES)
endif


app_server: app_server.c Makefile
	@echo OL $@
	$(Q)$(CC) $(shell pkg-config libxml-2.0 gio-2.0 ivy-glib --cflags) -o $@ $< -lm -lz $(shell pkg-config libxml-2.0 gio-2.0 ivy-glib libpcre --libs)

gpsd2ivy: gpsd2ivy.c Makefile
ifeq (, $(shell which gpsd))
	$(info GPSD not installed)
else
	$(Q)$(CC) $(GLIBIVY_CFLAGS) -o $@ $< $(GLIBIVY_LDFLAGS) -lgps
endif

ivy2nmea: ivy2nmea.c Makefile
	@echo OL $@
	$(Q)$(CC) $(GLIBIVY_CFLAGS) -o $@ $< $(GLIBIVY_LDFLAGS)

c_ivy_client_example_1: c_ivy_client_example_1.c Makefile
	$(CC) $(GLIBIVY_CFLAGS) -o $@ $< $(GLIBIVY_LDFLAGS)

c_ivy_client_example_2: c_ivy_client_example_2.c Makefile
	$(CC) $(GLIBIVY_CFLAGS) -o $@ $< $(GLIBIVY_LDFLAGS)

.PHONY: all clean
