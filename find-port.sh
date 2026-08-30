#!/bin/sh
# Prints every by-id path that could be the Arduino, one per line.
#
# Nano clones use a CH340, originals an FTDI, a few boards a CP210x. Victron's
# own cables carry "VictronEnergy_BV" in their by-id name and are never listed
# here, and neither is a u-blox GPS. Whether a listed port really is the
# transmitter is decided by the driver, which asks it to identify itself
# before sending a single command.
for f in /dev/serial/by-id/usb-*1a86* \
         /dev/serial/by-id/usb-*CH340* \
         /dev/serial/by-id/usb-*ch341* \
         /dev/serial/by-id/usb-*FTDI* \
         /dev/serial/by-id/usb-*FT232* \
         /dev/serial/by-id/usb-*Arduino* \
         /dev/serial/by-id/usb-*CP210*; do
    [ -e "$f" ] && echo "$f"
done
exit 0
