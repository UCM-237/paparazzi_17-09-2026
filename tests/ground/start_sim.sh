#!/bin/sh
# Background simulation session (NPS simulator + Python link + server) on a
# private Ivy bus.   start: tests/ground/start_sim.sh <port> [aircraft]
#                    stop:  tests/ground/start_sim.sh stop <port>
home=$(cd "$(dirname "$0")/../.." && pwd)
if [ "$1" = stop ]; then
  [ -f /tmp/sim_$2.pids ] && kill $(cat /tmp/sim_$2.pids) 2>/dev/null
  pkill -x simsitl 2>/dev/null; rm -f /tmp/sim_$2.pids; exit 0
fi
port=${1:-2799}; ac=${2:-Rover_Tana}; bus=127.255.255.255:$port
export PAPARAZZI_HOME=$home PAPARAZZI_SRC=$home
cd "$home"
/usr/bin/python3 sw/ground_segment/tmtc/server.py -b $bus -n > /tmp/sim_server_$port.log 2>&1 & echo $! > /tmp/sim_$port.pids
/usr/bin/python3 sw/ground_segment/tmtc/link.py -b $bus -udp -udp_broadcast > /tmp/sim_link_$port.log 2>&1 & echo $! >> /tmp/sim_$port.pids
sleep 2
/usr/bin/python3 sw/simulator/pprzsim-launch -a $ac -t nps -b $bus --norc --nodisplay > /tmp/sim_nps_$port.log 2>&1 & echo $! >> /tmp/sim_$port.pids
