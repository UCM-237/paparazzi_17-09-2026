#!/bin/bash
# Adds the Paparazzi Center (with the Jack the lad icon) to the applications
# menu and the dock of the current user (no root needed).
#   sw/tools/install_desktop_launcher.sh            install
#   sw/tools/install_desktop_launcher.sh --remove   uninstall
set -e
home=$(cd "$(dirname "$0")/../.." && pwd)
name=paparazzi-jack-the-lad
apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
icons=${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor

if [ "$1" = "--remove" ]; then
  rm -f "$apps/$name.desktop"
  for s in 16 32 48 64 128 256 512; do rm -f "$icons/${s}x${s}/apps/$name.png"; done
  echo "Launcher removed."
else
  mkdir -p "$apps"
  for s in 16 32 48 64 128 256 512; do
    mkdir -p "$icons/${s}x${s}/apps"
    cp "$home/data/pictures/jack_the_lad/jack_the_lad_$s.png" "$icons/${s}x${s}/apps/$name.png"
  done
  sed "s|@PAPARAZZI_HOME@|$home|g" "$home/data/desktop/$name.desktop.in" > "$apps/$name.desktop"
  chmod 644 "$apps/$name.desktop"
  echo "Launcher installed: $apps/$name.desktop (Paparazzi in $home)"
fi
command -v update-desktop-database > /dev/null && update-desktop-database -q "$apps" || true
command -v gtk-update-icon-cache > /dev/null && gtk-update-icon-cache -q -t "$icons" 2> /dev/null || true
