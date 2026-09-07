#!/bin/sh
# Builds arduino/maxxfan_tx.hex and its version file from the sketch.
#
# Needs arduino-cli with the AVR core:
#   arduino-cli core update-index && arduino-cli core install arduino:avr
#
# The .hex is what the driver flashes onto the Arduino, so it has to be rebuilt
# whenever the sketch changes - otherwise the card offers an update that
# installs an older sketch than the one in the repository.
set -e
cd "$(dirname "$0")/.."

FQBN=arduino:avr:nano:cpu=atmega328
OUT=$(mktemp -d)

arduino-cli compile -b "$FQBN" --output-dir "$OUT" arduino/maxxfan_tx
cp "$OUT/maxxfan_tx.ino.hex" arduino/maxxfan_tx.hex

# One source of truth: the version the sketch itself reports.
sed -n 's/^#define SKETCH_VERSION  *"\([^"]*\)".*/\1/p' \
    arduino/maxxfan_tx/maxxfan_tx.ino > arduino/maxxfan_tx.hex.ver
test -s arduino/maxxfan_tx.hex.ver || { echo "no SKETCH_VERSION in the sketch"; exit 1; }

rm -rf "$OUT"
echo "built $(wc -c < arduino/maxxfan_tx.hex) bytes, version $(cat arduino/maxxfan_tx.hex.ver)"
