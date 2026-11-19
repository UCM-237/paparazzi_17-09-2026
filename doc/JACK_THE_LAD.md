# Paparazzi without OCaml (Jack\_the\_lad): Changes and User Guide

Nov 19, 2026 · Juan Jiménez (UCM)

> This file is the reference copy of the guide and is kept in both branches, `Jack_the_lad` and `Jack_the_lad_boat`. Change it here, with a commit, like any other file of the repository.

Jack\_the\_lad is the UCM Paparazzi with its whole ground segment rewritten in Python: no OCaml is needed to install, build, simulate or fly. The airborne code (what runs on the Matek autopilots) is unchanged, so firmware built on `rover` and on `Jack_the_lad` behaves the same.

This guide is for the UCM rover and boat teams, including people who have never used Paparazzi. Read it in order the first time:

- **Part 1** lists what changed compared with the `rover` branch. Skip it if you never used the old version.
- **Part 2** explains how the pieces fit together.
- **Part 3** installs everything on a new computer.
- **Part 4** runs a rover in simulation, with no hardware.
- **Part 5** builds and uploads firmware to a real Matek board and drives it from the ground station.
- **Part 6** collects tips, troubleshooting and a glossary.

Both branches are on GitHub, in UCM-237/paparazzi\_17-09-2026: `Jack_the_lad` for the rovers and `Jack_the_lad_boat` for the boats (Part 3, Step 2). Commands in this guide assume the repository is at `~/paparazzi`.

## Part 1. What changed from the rover branch

Every OCaml program was replaced by a Python one with the same options and the same outputs, and the OCaml sources were deleted. In numbers: 29 commits, 306 files, about 16,200 lines added and 41,200 removed. The firmware you upload to the rovers is built from the same C code as before.

### Why

- The OCaml Ivy library shipped with Ubuntu (ivy-ocaml) uses AVX instructions. On CPUs without AVX, such as the Core i3-550 of the lab computer, every OCaml program that talks to the bus (link, server, messages, plotter) crashed at start.
- OCaml and its GTK2 libraries are hard to install on recent Ubuntu, and few students can maintain OCaml code.
- Python was already used by the Paparazzi Center and the pprzlink message generator.

### What replaced what

| Task | Old program (OCaml) | New program (Python) | How it was checked |
| --- | --- | --- | --- |
| Generate aircraft code (airframe, flight plan, settings, modules, makefile) | `sw/tools/generators/gen_aircraft.out` | `sw/tools/generators/gen_aircraft.py` + library `sw/lib/python/pprzgen/` | Identical output to OCaml for all 522 conf/aircraft/target combinations |
| Generate GPS / ABI / Xsens headers | `gen_ubx.out`, `gen_mtk.out`, `gen_xsens.out`, `gen_abi.out` | `gen_ubx.py`, `gen_mtk.py`, `gen_xsens.py`, `gen_abi.py` | Byte-identical headers |
| Radio link to the aircraft (Data Link) | `sw/ground_segment/tmtc/link` | `sw/ground_segment/tmtc/link.py` | Differential tests against OCaml, serial and XBee |
| Server (aircraft state for the GCS, logs) | `tmtc/server` | `tmtc/server.py` | Numeric code bit-exact against OCaml (7,600 values) |
| Messages viewer | `tmtc/messages` | `tmtc/messages.py` (PyQt5) | Tested in simulation |
| Real-time plotter | `sw/logalizer/plotter` | `sw/logalizer/rt_plotter.py` | Same command-line options |
| Log player (replay) | `sw/logalizer/play`, `play-nox` | `sw/logalizer/play.py` (`-nogui` = play-nox) | Replayed a 41 s log through server and GCS |
| Log plotter | `sw/logalizer/logplotter` | `sw/logalizer/logplotter.py` | Same CSV export format |
| SD-card log converter | `sw/logalizer/sd2log` | `sw/logalizer/sd2log.py` | Byte-identical .data, .log, .tlm |
| Environment simulator (wind, time scale, GPS off) | `sw/simulator/gaia` | `sw/simulator/gaia.py` | Same WORLD\_ENV messages |
| Joystick | `sw/ground_segment/joystick/input2ivy` | `joystick/input2ivy.py` (SDL2) | Same config parsing for all 34 joystick files; real gamepad not yet tried |
| Ivy relays | `ivy2udp`, `ivy2serial`, `ivy_tcp_aircraft`, `ivy_tcp_controller`, `broadcaster` | Same names with `.py` | End-to-end tests |
| Ground control station | `sw/ground_segment/cockpit/gcs` | PprzGCS (`pprzgcs`, a separate C++ program) | Already used by the team |
| Paparazzi Center | `sw/supervision/paparazzicenter` (legacy) | `sw/supervision/python/paparazzicenter.py` (already the default) | `./paparazzi -legacy` now opens the Python one |

### New files and folders

- `sw/lib/python/pprzgen/` - the code generators' library: XML parser compatible with the old one, airframe, flight plan, settings, modules, makefile and geodesy code.
- `sw/lib/python/pprzground/` - the ground segment library: message coding (`messages.py`), PPRZ and XBee framing (`transport.py`), Ivy helpers (`ivybus.py`), server state (`server_core.py`), relays (`bridge.py`).
- `tests/generators/` and `tests/ground/` - automatic tests (Part 6).
- `tests/docker/check_in_container.sh` - checks a Docker image.

### What you build now

The top-level `make` no longer compiles any OCaml. It installs the Python pprzlink library, generates the message headers, and compiles the few C ground tools (`app_server`, `ivy2nmea`, `gpsd2ivy`, `plotprofile`, `openlog2tlm`, `sdlogger_download`, `mergelogs`). The Paparazzi Center itself needs no build: it is Python.

### Bugs fixed

The first four were inherited from the OCaml code. The others appeared or became visible with Python:

| Problem | Effect before | Now |
| --- | --- | --- |
| Building `ap` after `nps` (or the reverse) | Failed with "Rover firmware should use generated autopilot" until Clean | Works in any order |
| Server option `-log_name` | The data overwrote the log description file | `.log` and `.data` written separately |
| EGM96 geoid height below sea level | Read as about +655 m (no effect in Madrid, +51 m) | Correct sign |
| `gvf.c` with GCC 14 | Compilation error | Fixed (also in branch `fix-gcc14-gvf`) |
| Ivy agents started at the same moment | Every message could arrive twice | Sent once per agent |
| Subscriptions sent during connection | Server could lose the link at startup | Handshake sent first |
| Space in the name "Paparazzi server" | Server connected to itself | Fixed, name kept (PprzGCS needs it) |
| Server slow to start with logging | A GCS started at the same time saw no aircraft | Server joins the bus when ready |
| Center on a Wayland desktop | Warning at every start; JavaScript errors from the doc tab | Silent |

### What behaves differently

- The old OCaml GCS is gone; use PprzGCS. Sessions in `conf/control_panel.xml` already use it.
- The server's `-kml` option (Google Earth export) was not ported.
- The ground programs run with the system Python, `/usr/bin/python3`, even if another Python (for example Anaconda) is first in your PATH.
- Install with an explicit package list instead of `paparazzi-dev` (Part 3); Docker images are provided.

## Part 2. How the system works

Paparazzi has two halves: the **airborne code**, C firmware that runs on the rover's autopilot board, and the **ground segment**, programs on your PC that talk to the rover and show what it does. The ground programs never talk to each other directly: they all exchange text messages on a local network bus called **Ivy**.

```text
  Rover (autopilot board)              Simulator (target nps, on the PC)
          |  telemetry radio (XBee)          |  UDP
          +----------------+-----------------+
                           |
                 Data Link (link.py)
                           |
  ================ Ivy bus (local network) ================
        |              |                |               |
   Server          PprzGCS        Messages,        other tools
   (server.py)     map, blocks,   plotters         (joystick, gaia,
   state, logs     settings       (viewers)         relays...)
```

Only the Data Link talks to the rover, over the telemetry radio, or over UDP when the simulator stands in for it. Everything else (Server, PprzGCS, viewers, tools) only listens and talks on the bus, so tools can be added or closed at any time.

### From XML files to a running rover

The firmware is not written by hand for each rover; it is generated from its description:

1. You describe the rover in XML: airframe, flight plan, telemetry, radio, settings (Part 5).
2. The generator `gen_aircraft.py` turns them into C headers and a makefile in `var/aircrafts/<rover>/`.
3. The compiler builds them with the C code of `sw/airborne/`: `arm-none-eabi-gcc` for target `ap`, the PC compiler for target `nps`.
4. For `ap`, `dfu-util` uploads the result to the Matek board; for `nps`, the result runs on the PC.

The Paparazzi Center runs all these steps with buttons (Build, Upload) and starts the ground programs in groups called **sessions**.

### Where things are

| Folder | Contains |
| --- | --- |
| `conf/` | Your configuration: `conf.xml` (list of aircraft), airframes, flight plans, telemetry, radios, modules, control panel (sessions) |
| `sw/airborne/` | The firmware C code |
| `sw/ground_segment/` | Ground programs: `tmtc/` (link, server, messages, relays), `joystick/` |
| `sw/logalizer/` | Log tools: player, plotters, sd2log |
| `sw/supervision/python/` | The Paparazzi Center |
| `sw/lib/python/` | Python libraries (`pprzgen`, `pprzground`, pprzlink) |
| `sw/tools/generators/` | Code generators |
| `sw/ext/` | External libraries (submodules): ChibiOS, libopencm3, pprzlink |
| `var/` | Generated files, builds and logs. Never edit by hand. |
| `docker/`, `tests/` | Container images and automatic tests |

## Part 3. Installing from scratch

A new computer needs Ubuntu 24.04, about 20 packages, the repository and one `make`: about 30 minutes, most of it downloading. If you only want to try things, the Docker route at the end of this part avoids touching your system.

### Requirements

| Item | Minimum | Notes |
| --- | --- | --- |
| Operating system | Ubuntu 24.04 LTS (64-bit PC) | Other versions may work but were not tested. Windows: use WSL2 with Ubuntu 24.04. |
| Processor | Any x86-64 | No AVX needed any more. |
| Memory | 4 GB | 8 GB is comfortable with the simulator and PprzGCS open. |
| Disk | 10 GB free | The repository with submodules is about 3 GB; build outputs add more. |
| Network | Internet during installation | Map tiles in PprzGCS also need it. |
| Hardware for real rovers | USB cable for the Matek board; telemetry radio (XBee or similar) | Not needed for simulation. |

### Step 1. Install the packages

Open a terminal and run:

```bash
sudo add-apt-repository -y ppa:paparazzi-uav/ppa
sudo apt-get update
sudo apt-get -f -y install pprzgcs paparazzi-jsbsim ivy-c-dev ivy-python dfu-util \
    make gcc g++ git bzip2 bc pkg-config libgsl-dev libusb-dev libpcre2-dev libpcre3-dev \
    libxml2-dev libglib2.0-dev libsdl1.2-dev \
    python-is-python3 python3-lxml python3-numpy python3-yaml python3-unidecode python3-serial \
    python3-pyqt5 python3-pyqt5.qtsvg python3-pyqtgraph libsdl2-2.0-0 speech-dispatcher \
    gcc-arm-none-eabi libnewlib-arm-none-eabi gdb-multiarch
```

The Paparazzi PPA provides PprzGCS, JSBSim and the Ivy libraries. Ubuntu provides the rest, including the ARM cross-compiler (GCC 13.2), which builds the rover firmware. Do not install `paparazzi-dev`: it pulls in the old OCaml packages, which are no longer needed.

### Step 2. Get the code

```bash
cd ~
git clone https://github.com/UCM-237/paparazzi_17-09-2026.git paparazzi
cd ~/paparazzi
git checkout Jack_the_lad
```

There are two branches without OCaml on GitHub. Check out the one for your vehicles:

- `Jack_the_lad`: the rovers (built from the team branch `rover`).
- `Jack_the_lad_boat`: the boats Boyi, Boyi2, Boyi\_Test and CYANOA (built from the `boat` branch of UCM-237/paparazzi). Use `git checkout Jack_the_lad_boat` instead of the last line above.

Give your user access to the USB boards (needed once):

```bash
sudo cp conf/system/udev/rules/*.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo usermod -aG dialout,plugdev $USER
```

Log out and back in so the new groups apply.

### Step 3. Select the UCM configuration

Paparazzi reads the list of aircraft from `conf/conf.xml`. Point it at the UCM list:

```bash
ln -sf airframes/UCM/conf.xml conf/conf.xml
```

### Step 4. Build

```bash
make
```

The first run downloads the submodules (ChibiOS, libopencm3, pprzlink and others) and takes several minutes. It ends without errors. If it stops on a missing submodule, run `git submodule update --init --recursive` and `make` again.

**ARM CMSIS-DSP library (optional).** `make` does not download it, and only the aircraft that use the `cmsis_dsp` module need it. Today that means only the benchmark aircraft (Bench\_CMSIS\_F405, and Bench\_CMSIS\_F765 in `Jack_the_lad`). Rovers and boats build without it. Download it once before building one of those aircraft:

```bash
git submodule update --init sw/ext/cmsis-dsp
```

Without it, building one of those aircraft stops with an error saying that `arm_math.h` cannot be found.

**Switching branches.** Each branch has its own messages file (`conf/messages.xml`), but what Paparazzi generates and compiles from it lives in `var/`, which git does not change when you switch branches. After `git checkout` of another branch, run `make` again and, in the Center, do Clean then Build of the aircraft you are going to use. For a real board, open Paparazzi on the same branch that the board's firmware was built from.

### Step 5. Start

```bash
./paparazzi
```

The Paparazzi Center opens.

### Step 6. Add Paparazzi to the applications menu (optional)

Run this once, in a terminal:

```bash
cd ~/paparazzi
sw/tools/install_desktop_launcher.sh
```

Press the Super key (Windows key) and type "Paparazzi": **Paparazzi Center (Jack the lad)** appears with Jack's icon. Right-click it in the dock and choose **Pin to Dash** to keep it there. If it does not appear, log out and back in once. To remove it, run the same script with `--remove`.

The launcher starts whatever branch is checked out in `~/paparazzi`. Continue with Part 4.

### If your computer has another ARM compiler first in PATH

Tools such as STM32CubeIDE or STM32CubeCLT add their own `arm-none-eabi-gcc` (GCC 14) to the PATH. The ChibiOS boards (Rover\_Satur) and the GCC 14 fix work with it, but the tested compiler is Ubuntu's GCC 13. To force it for the STM32 boards, add this line to `conf/Makefile.local`:

```makefile
PREFIX ?= /usr/bin/arm-none-eabi-
```

### Alternative: Docker

Docker gives a ready Ubuntu 24.04 with every package, without changing your system. It still uses your copy of the repository, so builds made inside show up in your folder.

```bash
sudo apt-get install -y docker.io
sudo usermod -aG docker $USER      # then log out and back in
cd ~/paparazzi/docker
docker build --tag=paparazziuav/pprz-dep dep/.    # once, about 10 minutes
docker build --tag=paparazziuav/pprz-dev dev/.
make paparazzi                      # the Paparazzi Center, running in the container
```

Plug in boards before starting the container: it only sees the serial ports present at start. Being in the `docker` group is equivalent to having administrator rights on the computer.

## Part 4. Running a simulation

A simulation runs the real rover firmware on your PC (target `nps`) with a simulated vehicle, so you can test flight plans and code without hardware. It takes three clicks: choose the aircraft, build `nps`, start the Simulation session.

### Step 1. Choose the aircraft and build

1. Start the Center with `./paparazzi`.
2. In the **Configuration** tab, choose an aircraft in the A/C list at the top, for example **Rover\_Tana**.
3. In the build area, set **Target** to `nps` and press **Build**.
4. Watch the console at the bottom. The build ends with a line containing `simsitl` and no red errors. The first build takes a minute or two; later builds are faster.

### Step 2. Start the session

1. Open the **Operation** tab.
2. In **Session**, choose **Simulation** and press the start button beside it (tooltip "Start Session").
3. Four programs appear in the Programs list:
   - **Simulator** - the rover firmware running on your PC.
   - **Data Link** - receives the simulated radio and publishes the telemetry on the Ivy bus.
   - **Server** - keeps the aircraft state for the ground station (no log in this session).
   - **PprzGCS** - the ground control station window.
4. After a few seconds the rover appears on the PprzGCS map at its flight plan's home position.

### Step 3. Drive it

- The panel of the rover in PprzGCS lists the **flight plan blocks**. Clicking a block makes the rover execute it.
- Waypoints can be dragged on the map; the new position is sent to the rover.
- The **Settings** tab of the rover's panel changes parameters (gains, speeds) while it runs.

### Step 4. Look at the data

Add tools to the running session with **Add tool** in the Operation tab:

| Tool | Use it to |
| --- | --- |
| Messages | See every message on the bus, live, with its fields. |
| Real-time Plotter | Plot any field over time. Drag a field from Messages onto the plot. |
| Environment Simulator | Add wind, speed up time (time scale) or switch the GPS off. |
| Joystick | Drive with a gamepad (`input2ivy.py`, config files in `conf/joystick/`). |

### Step 5. Stop

Press **Stop All** (and **Remove All** to clear the list), or close the Center.

### To record and replay a simulation

The built-in Simulation session does not log. To record, start the programs one by one with **Add tool** and give the Server no `-n` option. Logs go to `var/logs/`. The **Replay** session plays a log back to PprzGCS (Part 5, Step 6).

### Without the Center

The same session from a terminal, useful on a remote computer:

```bash
cd ~/paparazzi
make -f Makefile.ac AIRCRAFT=Rover_Tana nps.compile
sw/simulator/pprzsim-launch -a Rover_Tana -t nps &
sw/ground_segment/tmtc/link.py -udp -udp_broadcast &
sw/ground_segment/tmtc/server.py -n &
pprzgcs &
```

## Part 5. Building and uploading code for a real rover

The path to a real rover is: describe it in XML files, build target `ap`, upload over USB in DFU mode, then talk to it through the telemetry radio with a Flight session. The same files serve the simulation, so test there first.

### Step 1. Understand the aircraft files

Each rover is one entry in `conf/airframes/UCM/conf.xml`, which points to the files that describe it:

| File | Folder | What it defines | Example (Rover\_Tana) |
| --- | --- | --- | --- |
| Airframe | `conf/airframes/UCM/` | Board, firmware, modules, servos, parameters | `rover_tana.xml` (board Matek F405 Wing) |
| Flight plan | `conf/flight_plans/UCM/` | Home position, waypoints, blocks (missions) | `steering_rover_gvf_mollifiers.xml` |
| Telemetry | `conf/telemetry/` | Which messages the rover sends and how often | `default_rover_pidcontrol.xml` |
| Radio | `conf/radios/UCM/` | RC transmitter channels | `T16SZ_SBUS_rover.xml` |
| Settings | `conf/settings/` | Parameters changeable from the GCS | listed in `conf.xml` |

Each rover has its own `ac_id` (Rover\_Tana is 1, Rover\_UCM 2, Rover\_Satur 3). Two rovers on the air at the same time must never share an `ac_id`.

### Step 2. Create a new rover

The easiest way is to copy a working one:

1. In the Center, select a similar rover and use **Duplicate** in the Configuration tab. Give it a new name and a new, unused ID.
2. Copy its airframe file to a new name (for example `rover_newname.xml`) and select it for the new rover, so changes do not affect the original.
3. Adjust the airframe: the board (`matek_f405_wing_v1` or `matek_f765_wing`), servo limits, modules, and parameters such as `MAX_DELTA` or `DRIVE_SHAFT_DISTANCE`.
4. Choose or create the flight plan. Set its home (`lat0`, `lon0`) to the place where you will test.
5. Build `nps` and test in simulation (Part 4) before going to the hardware.

### Step 3. Build the firmware

1. Select the rover, set **Target** to `ap`, press **Build**.
2. The build ends with a size table and the path of the result, for example `var/aircrafts/Rover_Tana/ap/ap.elf`. For ChibiOS boards (F765, Rover\_Satur) the files are in `var/aircrafts/Rover_Satur/ap/obj/`.

You can switch between `nps` and `ap` freely; no Clean is needed any more.

### Step 4. Upload to the Matek board

The Matek boards are flashed over USB with `dfu-util`, which needs the board in bootloader (DFU) mode:

1. Unplug the USB cable.
2. Hold the **BOOT** button on the board, plug the USB cable, release the button.
3. Check: `lsusb` shows a line with `STM Device in DFU Mode`.
4. In the Center press **Upload** (same aircraft and target `ap`). The console shows `dfu-util` progress and ends with `Download done`.
5. Unplug and plug again (or power-cycle): the new firmware starts.

From a terminal the same is `make -f Makefile.ac AIRCRAFT=Rover_Tana ap.upload`.

### Step 5. Operate the rover

1. Connect the ground telemetry radio to the PC. With the UCM udev rules, an XBee appears as `/dev/paparazzi/xbee`.
2. In the **Operation** tab choose the session that matches your radio, for example **Flight USB-XBee-API@57600** (Data Link + Server + PprzGCS), and start it.
3. Power the rover. In PprzGCS it appears with its name, GPS position and battery. The server checks that the code on the rover matches your configuration; if it warns that the md5 does not match, rebuild and upload again.
4. Use the RC transmitter for manual mode and PprzGCS blocks for the automatic modes, as in the simulation.

### Step 6. Logs

- **Telemetry logs**: Flight sessions start the Server with logging. Each run writes `var/logs/YY_MM_DD__HH_MM_SS.log` (the configuration) and `.data` (one line per message).
- **Replay**: choose the **Replay** session; the Log File Player opens, choose a `.log` and press Play. PprzGCS shows the run again.
- **Plot**: the Log Plotter tool (`logplotter.py`) plots any field of a log and exports CSV.
- **SD card logs**: if the rover records on its SD card, use **Extract SD logs** in the Center (or `sw/logalizer/sd2log.py <file>`). It converts the binary file into the same `.log`/`.data` pair, so it can be replayed and plotted.

## Part 6. Tips for new users

Most problems come from four places: the wrong branch, a board not in DFU mode, a radio on the wrong port, or code on the rover that does not match the configuration. The table below covers those first.

### Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Center or programs missing files, OCaml errors | You are on the `rover` branch | `git branch` shows the current one; `git checkout Jack_the_lad` |
| Upload fails: "No DFU capable USB device" | Board not in bootloader mode | Hold BOOT while plugging USB; check `lsusb` |
| Upload fails: permission denied | udev rules or groups missing | Part 3, Step 2; then log out and in |
| Rover never appears in PprzGCS | Radio on another port, wrong baud rate, or different `ac_id` | Check `ls /dev/paparazzi/`, the session's Data Link options, and the rover's ID |
| Server prints "live md5 signature ... does not match" | Firmware on the rover was built from another configuration | Rebuild `ap` and upload; for a simulation, rebuild `nps` |
| Simulation starts but no rover | `nps` not built after the last change | Build `nps` again, restart the session |
| `make` error mentioning `/opt/st/...` | Build files made with the STM32CubeCLT compiler, now built with another | `make -C sw/ext/libopencm3 clean` then `make` |
| Python error "No module named ivy" | Ground program run with another Python (Anaconda) | Run it as `/usr/bin/python3 <program>`, or through the Center |

### Adding your own code

Airborne code is organised in **modules**: a C file plus an XML file describing it.

1. Put the C code in `sw/airborne/modules/<your_folder>/` (for example `my_controller.c` and `.h`).
2. Describe it in `conf/modules/my_controller.xml`: the source files, the `init` and `periodic` functions and their frequency, and any settings.
3. Add `<module name="my_controller"/>` to the rover's airframe file.
4. Build `nps`, test in simulation, then build `ap` and upload.

The Center's **Utilities** tab has a Module Creator that writes a starting XML and C file. Look at existing UCM modules such as `gvf_parametric_bare` or `lidar_tfmini` as examples.

### Good habits

- Test every change in simulation before uploading.
- Commit your configuration changes with git (`git add`, `git commit`) so you can go back.
- Keep the PC's clock correct: logs are named by date and time.
- Do not edit files in `var/`: they are generated and are overwritten by every build.

### Checking an installation

These tests need no hardware and take a few minutes each:

| Command | Checks |
| --- | --- |
| `/usr/bin/python3 -u tests/ground/sim_session.py --gcs --log` | Simulation, server, link, PprzGCS, logs |
| `/usr/bin/python3 tests/ground/bridges_test.py` | Ivy relays |
| `bash docker/run.sh paparazziuav/pprz-dev tests/docker/check_in_container.sh` | A Docker image: build, three rovers, simulation |

Each prints `PASS`, `ALL OK` or `ALL CHECKS PASSED` when everything works. More tests are described in `tests/ground/README.md`.

### Glossary

| Term | Meaning |
| --- | --- |
| Airborne code | The C firmware that runs on the autopilot board. |
| `ap` | Build target for the real autopilot board. |
| `nps` | Build target for the simulator on the PC. |
| `ac_id` | Number that identifies each aircraft on the radio and the bus. |
| Ivy bus | Local network messaging that connects the ground programs. |
| PPRZ / pprzlink | Paparazzi's message format and the library that encodes it. |
| Data Link (`link.py`) | Bridge between the radio and the Ivy bus. |
| Server (`server.py`) | Keeps the state of each aircraft and writes the logs. |
| PprzGCS | The ground control station: map, flight plan, settings. |
| Flight plan | XML file with waypoints and blocks (the missions). |
| Module | A piece of airborne code plus its XML description. |
| DFU | USB bootloader mode used to upload firmware to STM32 boards. |
| Session | A set of ground programs started together from the Center. |
| md5 | Fingerprint of a configuration; the server checks it against the rover's. |
