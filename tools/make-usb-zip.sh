#!/bin/sh
# make-usb-zip.sh - build the USB stick that installs this driver on a GX
# device with no console, no SSH and no laptop.
#
# Venus OS unpacks archives it finds on removable media at boot, and
# SetupHelper's PackageManager picks up package archives lying next to them.
# That is the whole trick: the stick carries the package, an empty flag file
# that says "install what you find", and a note for whoever is holding it.
#
#   sh tools/make-usb-zip.sh [outdir]
#
# The result is outdir/maxxfan-usb.zip plus the loose files it contains, so
# the same script serves CI and a hand-made stick.
#
# SetupHelper's own installer is not included: its repository carries no
# licence, so it is not ours to redistribute. It is one click away and the
# note on the stick says where from.
set -e

HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-"$HERE/usb-build"}
BRANCH=$(cut -d: -f2 "$HERE/gitHubInfo" 2>/dev/null || echo latest)
NAME=dbus-maxxfan
VERSION=$(cat "$HERE/version")
SETUPHELPER_URL=https://github.com/kwindrem/SetupHelper/raw/main/venus-data-SetupHelperInstall.tgz

rm -rf "$OUT"
mkdir -p "$OUT/stage/$NAME-$BRANCH"

# The archive name is what PackageManager parses: <package>-<branch>.tar.gz,
# with a matching top level directory inside. Same shape as the .tar.gz GitHub
# hands out for a branch, which is the other supported way to get one.
tar -cf - -C "$HERE" \
    --exclude .git --exclude .github --exclude usb-build \
    --exclude __pycache__ --exclude .DS_Store . |
    tar -xf - -C "$OUT/stage/$NAME-$BRANCH"
tar -czf "$OUT/$NAME-$BRANCH.tar.gz" -C "$OUT/stage" "$NAME-$BRANCH"
rm -rf "$OUT/stage"

# Empty on purpose. Its presence is the instruction; PackageManager never
# reads it.
: > "$OUT/AUTO_INSTALL_PACKAGES"

cat > "$OUT/LIESMICH.txt" <<EOF
MaxxFan-Steuerung fuer Victron GX  --  Installation per USB-Stick
MaxxFan control for Victron GX     --  install from a USB stick
dbus-maxxfan $VERSION

----------------------------------------------------------------- DEUTSCH ---

Es fehlt noch eine Datei. Lade sie hier herunter:

  $SETUPHELPER_URL

Das ist SetupHelper, der Paketmanager fuer Venus OS. Ohne ihn passiert
nichts. Safari packt .tgz-Dateien automatisch aus - dann in den Safari-
Einstellungen "Sichere Dateien nach dem Laden oeffnen" ausschalten und
noch einmal laden.

So muss der Stick danach aussehen (FAT32 formatiert, alles direkt auf dem
Stick, nicht in einem Ordner):

  USB-Stick
  |-- venus-data-SetupHelperInstall.tgz
  |-- $NAME-$BRANCH.tar.gz
  \`-- AUTO_INSTALL_PACKAGES

Dann:

  1. Stick in das GX-Geraet stecken (Cerbo, Ekrano, CCGX ...).
  2. GX-Geraet neu starten - Strom weg, Strom dran.
  3. Warten, bis die Oberflaeche wieder da ist. Das dauert ein paar Minuten,
     laenger als sonst.
  4. Stick abziehen.

Danach steht unter Einstellungen ganz unten "Package manager", und der
MaxxFan taucht als Geraet auf, sobald der Arduino am USB-Port steckt.

Kommt der MaxxFan nicht: Stick drinlassen und noch einmal neu starten.

--------------------------------------------------------------- ENGLISH ---

One file is missing. Download it here:

  $SETUPHELPER_URL

That is SetupHelper, the package manager for Venus OS. Nothing happens
without it. Safari unpacks .tgz files by itself - turn off "Open safe files
after downloading" in Safari's settings and download it again.

The stick has to end up looking like this (formatted FAT32, everything
loose on the stick, not inside a folder):

  USB stick
  |-- venus-data-SetupHelperInstall.tgz
  |-- $NAME-$BRANCH.tar.gz
  \`-- AUTO_INSTALL_PACKAGES

Then:

  1. Put the stick in the GX device (Cerbo, Ekrano, CCGX ...).
  2. Power the GX device off and on again.
  3. Wait for the display to come back. It takes a few minutes, longer than
     a normal start.
  4. Pull the stick out.

Settings then has "Package manager" at the bottom, and the MaxxFan appears
as a device as soon as the Arduino is plugged into a USB port.

If the MaxxFan does not appear, leave the stick in and reboot once more.
EOF

( cd "$OUT" && rm -f maxxfan-usb.zip &&
  zip -q -X maxxfan-usb.zip "$NAME-$BRANCH.tar.gz" AUTO_INSTALL_PACKAGES \
      LIESMICH.txt )

echo "$OUT/maxxfan-usb.zip"
unzip -l "$OUT/maxxfan-usb.zip" | sed -n '4,8p'
