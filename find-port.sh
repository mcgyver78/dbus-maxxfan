#!/bin/sh
# Prints every by-id path that could be the Arduino, one per line, no duplicates.
#
# These are USB-serial chip names, not device names. Nano clones use a CH340,
# originals an FTDI, a few boards a CP210x - but so do plenty of other things,
# including Victron's own RS485-to-USB interface, which is an FT232R. A match
# here therefore means "worth asking", never "this is ours".
#
# Nothing is acted on because it appears in this list. The driver asks each
# candidate to identify itself, and hands a port that does not answer straight
# back to serial-starter.
for f in /dev/serial/by-id/usb-*1a86* \
         /dev/serial/by-id/usb-*CH340* \
         /dev/serial/by-id/usb-*ch341* \
         /dev/serial/by-id/usb-*FTDI* \
         /dev/serial/by-id/usb-*FT232* \
         /dev/serial/by-id/usb-*Arduino* \
         /dev/serial/by-id/usb-*CP210*; do
    [ -e "$f" ] && echo "$f"
done | sort -u
exit 0
