# dbus-maxxfan

Venus OS driver for the MaxxAir MaxxFan Deluxe roof fan, controlled over infrared.
Venus-OS-Treiber für den MaxxAir MaxxFan Deluxe, gesteuert über Infrarot.

**[English](#english) · [Deutsch](#deutsch)**

---

## English

Publishes a MaxxAir MaxxFan Deluxe on the Venus OS D-Bus as
`com.victronenergy.switch`, so the fan gets its own card in the switch pane of
the GX display, the remote console and VRM — even though it has no data
connection of any kind.

Commands reach the fan the same way the hand held remote sends them: as infrared.
An Arduino with an infrared LED, running the sketch in [`arduino/`](arduino/),
hangs on a USB serial port of the GX device and does the transmitting.

### What the driver publishes

One card named *MaxxFan*, holding eight controls:

![The MaxxFan card in the switch pane](docs/switch-pane.png)

| Output | Type | Content |
|---|---|---|
| `fan` | toggle | Fan on / off |
| `speed` | basic slider | 10 … 100 %, in steps of ten |
| `direction` | dropdown | Intake / Exhaust |
| `cover` | toggle | Lid open / closed |
| `mode` | dropdown | Manual / Auto (thermostat) |
| `setpoint` | temperature setpoint | −2 … 37 °C, the thermostat's target |
| `resend` | momentary | Transmit the current state again |
| `beep` | momentary | Make the fan beep twice, to find it or test the path |
| `update` | momentary | Shows both firmware versions, and flashes the Arduino |

Every control also appears under `/SwitchableOutput/<name>/…` on D-Bus, so
Node-RED and MQTT can read and write the same values.

The service is named after the port the Arduino got — `maxxfan_ttyUSB0` in the
examples below, but `ttyUSB1` as soon as something else enumerates first, and it
can change across reboots. `dbus -y | grep maxxfan` gives the current one. The
*device instance* is stable: it lives in
`/Settings/Devices/maxxfan/ClassAndVrmInstance`.

The type of each control can be changed from the GX device page, and the change
is stored — if the slider for the speed does not suit you, set it to a dimmer
instead. `ValidTypes` lists what each output allows.

**A note on language.** The labels above are plain text the driver publishes, so
they stay English whatever the GX is set to — while the words the interface
supplies itself (*on/off*, *press*, *min/max*) follow the device's language
setting. On a German GX the card therefore reads half English. A driver cannot
translate its own labels, but you can rename every control and it is stored:

```bash
S=com.victronenergy.switch.maxxfan_ttyUSB0
dbus -y $S /SwitchableOutput/fan/Settings/CustomName SetValue "Lüfter"
dbus -y $S /SwitchableOutput/speed/Settings/CustomName SetValue "Drehzahl"
```

The entries of the two dropdowns come from `Settings/Labels` and stay as the
driver sets them.

### Infrared only goes one way

The fan never answers. Everything the GX display shows is **the state the driver
last transmitted**, not a reading. If somebody uses the hand held remote, the two
drift apart until the next command from the GX — which, because the protocol
carries the complete state in every packet, immediately puts the fan back in
step with the display.

Two consequences worth knowing:

- **The driver does not transmit at startup.** Re-sending the stored state on
  every reboot of the GX would start a fan that somebody deliberately switched
  off by hand. The state is published, not sent.
- **A periodic refresh runs every 15 minutes** (`REFRESH_S` in the driver) and
  re-sends the current state, so a fan operated by hand comes back under GX
  control on its own. Set `REFRESH_S = 0` to switch that off and use the
  *Resend* button instead.

Because the protocol has no "speed only" message, every change transmits all
eight fields. Changes are collected for 600 ms first, so dragging the speed
control produces one packet rather than twenty.

### Installation

#### With SetupHelper (recommended)

Install [SetupHelper](https://github.com/kwindrem/SetupHelper), then in the GX
menu go to *Settings → Package manager → Inactive packages → new* and enter:

| Field | Value |
|---|---|
| Package name | `dbus-maxxfan` |
| GitHub user | `mcgyver78` |
| GitHub branch or tag | `latest` |

Then *Proceed* → *Install*.

#### Manually

SetupHelper is needed either way — the setup script is a thin wrapper around it.
This route only skips the Package manager screen.

```bash
cd /data
git clone https://github.com/mcgyver78/dbus-maxxfan.git
/data/dbus-maxxfan/setup
```

### Requirements

- Venus OS v3.70 or newer with the **new user interface** enabled — the switch
  pane is a gui-v2 feature. On the classic interface the device appears in the
  device list, but its controls are not drawn.
- Python 3 and `pyserial`, both shipped with Venus OS
- An Arduino on a USB port of the GX device, flashed with
  [`arduino/maxxfan_tx`](arduino/maxxfan_tx)

### Hardware

```
  +-----------+   USB serial    +----------------+   38 kHz IR   +-----------+
  |  Cerbo GX |<--------------->|  Arduino Nano  | ~~~~~~~~~~~~> |  MaxxFan  |
  |           |     115200      |   maxxfan_tx   |    940 nm     |   Deluxe  |
  +-----------+                 +----------------+               +-----------+
        ^                                                              :
        | D-Bus, switch pane                nothing comes back - see "Not implemented"
```

| Part | Note |
|---|---|
| ATmega328P board | classic Nano, Uno or Pro Mini. Not a Nano Every and not an ESP: the sketch drives timer 1 directly |
| Infrared LED, 940 nm | e.g. TSAL6200, or the LED of a KY-005 module |
| Resistor 180 … 220 Ω | sets the LED current to roughly 20 mA |
| USB cable to the GX device | a data cable, not a charging cable |

That is the entire circuit:

```
   D9 (OC1A) --[ 180 ... 220 R ]--|>|-- GND
                                 IR LED
                            anode       cathode
                          (long leg)   (short leg, flat side)
```

**Pin 9 is not a free choice.** The 38 kHz carrier is generated by timer 1 in
hardware, and timer 1 drives that pin. Doing the carrier in hardware is also the
reason this sketch does not disable interrupts: a packet is 176 symbols long and
occupies the LED for about 150 ms, and a sketch that bit-bangs the carrier has to
block interrupts for that whole window — losing every serial byte that arrives in
it. Here the serial port keeps running while a packet is on the air.

**Range.** Driven straight from the pin the LED runs at about 20 mA, good for a
metre or two. That is plenty, because the LED belongs right next to the fan's own
receiver window anyway. If you want more, drive it through an NPN transistor
(BC337, 2N3904) from 5 V with 22 … 47 Ω — the carrier is only a third duty cycle
and a packet lasts 150 ms, so the LED takes the higher pulse current easily.

**Where it goes.** The LED at the fan, the USB cable to the GX device. Ordinary
USB is good for about three metres; beyond that use an active cable.

**Current state: transmit only.** There is no receiver in the circuit, so what
somebody does with the hand held remote is invisible to the driver. Adding one
means a 38 kHz receiver module (TSOP38238, TSOP4838 or VS1838B) with its output
on D2 — see [Not implemented](#not-implemented).

Flash the sketch with the Arduino IDE — board *Arduino Nano*, processor
*ATmega328P*, or *ATmega328P (Old Bootloader)* on most clones — or with
`arduino-cli`. No libraries are needed. It compiles to about 3.4 KB, a tenth of
the flash.

#### Flashing it, and what goes wrong

Every one of these cost time here, so they are written down rather than
rediscovered.

| Symptom | Cause |
|---|---|
| `bad CPU type in executable` while compiling | Apple Silicon without Rosetta — the AVR toolchain is Intel only. `softwareupdate --install-rosetta --agree-to-license` |
| `expected unqualified-id before '<' token` | the `.ino` contains HTML. It was copied from the rendered GitHub page instead of the file; use the *Raw* button or the clone |
| No *Processor* entry in the *Tools* menu | wrong board. `ATmega328P Xplained Mini` is the same chip on a different platform and has no bootloader options. It must be *Arduino Nano* from *Arduino AVR Boards* — the first line of the verbose output has to read `FQBN: arduino:avr:nano` |
| `stk500_recv(): programmer is not responding`, `not in sync` | older board with the old bootloader: *Tools → Processor → ATmega328P (Old Bootloader)* |
| `cannot open port …: No such file or directory` | the board dropped off the bus. Re-plug it and pick the port again — the IDE holds on to a stale selection |
| The size summary appears but no avrdude output at all | *Verify* was pressed, not *Upload*. Both print the summary, only *Upload* runs avrdude |
| Upload fails although the port exists | the serial monitor is holding the port. Close it first |
| No port under *Tools → Port* at all | nearly always a charge-only USB cable. The board selection has no influence on whether a port appears |
| No reply in the serial monitor | 115200 baud, and the line ending must be *Newline*. Without it the sketch never sees a complete command |

On macOS pick the **`/dev/cu.…`** port, not `/dev/tty.…` — the tty variant blocks
until the device asserts DCD, and the upload then hangs without an error.

The board here is an FTDI Nano, which carries a unique serial number and shows up
as `usb-FTDI_FT232R_USB_UART_<serial>-if00-port0`. CH340 clones usually have none,
which is why the driver verifies the port by asking rather than by name.

#### Serial protocol

115200 8N1, one command per line:

```
?                                                     -> MAXXFAN 1 1.3
S <on> <speed> <exhaust> <cover> <auto> <degF> <warn> -> OK <32 hex digits>
R                                                     -> OK <32 hex digits>
```

`S` builds the packet, transmits it and echoes it back as hex, so the driver can
log exactly what went out. `R` repeats the last packet unchanged. Values out of
range are refused with `ERR` rather than sent. The setpoint is given in
Fahrenheit here because that is the unit on the wire; the driver converts.

The identification query is what makes the port safe to find. The driver globs
`/dev/serial/by-id/` for CH340, FTDI, CP210x and Arduino names, and then asks
each candidate to identify itself before sending it a single command.

Those are chip names, not device names, and the distinction matters: Victron's
VE.Direct-USB and MK3-USB cables carry their own descriptors and never match,
but the **RS485-to-USB interface** — the cable that connects a Carlo Gavazzi
grid meter — is a plain FTDI FT232R and looks exactly like an Arduino in the
by-id listing. A match is therefore only ever a reason to ask, never a reason to
act. See [Serial starter](#serial-starter) for what that means in practice.

### Updating the Arduino from the GX

Venus OS has no avrdude, and does not need one: the bootloader the Arduino IDE
talks to speaks STK500v1, and [`tools/flash.py`](tools/flash.py) implements
enough of it to program an ATmega328P over pyserial. Once the transmitter is
plugged into the GX device, it never has to go back to a laptop.

**Where the versions are.** The device page shows the sketch under *Firmware
version* and the driver in the *Connection* row. On the card, the element in
the bottom right carries both:

```
Transmitter 1.3 (up to date)
Transmitter 1.2 (update to 1.3)      <- press to flash
Transmitter pre-1.3 (update to 1.3)  <- a sketch older than this mechanism
```

It sorts after *Speed* because the card orders its controls by label, which is
why it sits out of the way of the fan controls. Rename it and the driver stops
touching the label.

**Flashing only happens when you press it.** Not on install, not on a version
mismatch. The one exception is repair: if the port that identified as ours last
time now has a bootloader but no working sketch - an update that was
interrupted - the driver finishes the job at startup. There is no doubt about
whose board that is, and the alternative is a fan that stays dead until somebody
carries a laptop to it.

A bootloader alone is never enough to start flashing on any other port. It only
says "an AVR lives here", not whose project it runs.

**What cannot be updated this way:** a board whose bootloader was erased (needs
an ISP programmer), one with auto-reset disabled - cutting RESET-EN is a common
trick to stop the board rebooting when a port is opened, and it stops flashing
too - and anything that is not an ATmega328P or 168, which the signature check
rejects. One case fails silently: a sketch built for 16 MHz running on an 8 MHz
board reports its version happily and transmits infrared at half speed. A
signature cannot tell clock rates apart.

An interrupted write cannot brick the board. The bootloader sits in a protected
section of flash and cannot overwrite itself, so the worst case is a broken
sketch and a working bootloader - exactly the state the repair path above is
for.

**Keeping the firmware in step.** `arduino/maxxfan_tx.hex` is what gets flashed,
so it has to match the sketch; a stale one would make the card offer an update
that installs something older. [`tools/build-hex.sh`](tools/build-hex.sh)
rebuilds it and the version file from `SKETCH_VERSION`, and
[`tools/check-hex.py`](tools/check-hex.py) — which a GitHub workflow runs on
every change — verifies that they agree.

The check reads the version string **out of the committed `.hex`**, where the
sketch's identify reply puts it, rather than comparing against a fresh build.
Two toolchain versions produce different binaries from identical source, so a
byte comparison fails for reasons that have nothing to do with the firmware
being current. Bump `SKETCH_VERSION` whenever you change the sketch, and the
check has something to catch.

### Why `switch` and not a fan service

Venus OS has no service class for a ventilation fan. `com.victronenergy.switch`
is the one the GUI scans for `/SwitchableOutput/x/…`, and it offers exactly the
controls this device needs: toggles, a stepped value, dropdowns with labels, a
temperature setpoint and momentary buttons. The same API is what the GX IO
extender, Node-RED virtual switches and third party digital switches use, so the
fan sits on the switch pane next to them and needs no GUI modification at all.

### Protocol

38 kHz carrier, one third duty. The bit stream looks like RS232: one start bit,
eight data bits least significant first, two stop bits. A **mark encodes a zero
and a space encodes a one**, each symbol **834 µs** long. Sixteen bytes, no
framing beyond that:

| Byte | Content |
|---|---|
| 0–9 | fixed preamble `5A A5 80 7F 40 BF 20 DF 10 CC` |
| 10 | state bits |
| 11 | speed, 0 … 100 in steps of ten |
| 12 | thermostat setpoint in °F |
| 13 | always `FF` |
| 14 | always `23` |
| 15 | XOR of bytes 10 … 14 |

State byte 10:

| Bit | Meaning |
|---|---|
| 0 (0x01) | fan on |
| 1 (0x02) | special — see below |
| 2 (0x04) | direction: 0 intake, 1 exhaust |
| 3 (0x08) | lid open |
| 4 (0x10) | auto mode (thermostat) |
| 5 (0x20) | warn — the fan beeps twice |

The **special bit** overrides the fan's own coupling of lid and motor. It is set
in thermostat mode, and for ceiling fan mode — running with the lid closed —
which the fan otherwise refuses. The sketch derives it as
`auto || (on && !lid_open)`, which matches all 99 reference recordings.

Every packet carries the complete state. There are no incremental commands, and
that is why a driver for this fan has to remember what it last sent.

#### The setpoint is stored in Fahrenheit

The remote displays Celsius but transmits Fahrenheit, and the conversion it uses
**truncates towards zero**:

```
degF = trunc(degC × 1.8) + 32
```

That is not the same as rounding. A rounded conversion is off by one degree
Fahrenheit on 18 of the 40 setpoints in the −2 … 37 °C range — 21 °C becomes
70 °F instead of the 69 °F the remote sends. The driver uses the truncating form, so a setpoint entered on
the GX matches the one shown on the fan.

#### Verified against the original remote

[`tools/verify-encoder.py`](tools/verify-encoder.py) decodes the 99 signals that
[skypeachblue](https://github.com/skypeachblue/maxxfan-reversing) captured from
an original remote with a Flipper Zero, re-encodes each one and compares the
result symbol by symbol:

```bash
python3 tools/verify-encoder.py Maxxfan_collection.ir
```

All 99 reproduce exactly. [`tools/test-driver-logic.py`](tools/test-driver-logic.py)
covers the other half — it fakes the D-Bus service with the same accept/reject
semantics vedbus really has and checks that a control which sends step numbers
is refused rather than stored, that a failed transmit is retried and then gives
up, that a port which does not identify is handed back to serial-starter, and
that a stored value the fan cannot do is snapped into range. Both run without a
GX device.

The capture comparison is also where the 834 µs symbol period
and the truncating temperature conversion come from — both differ from the
widely used [ESPHome component](https://github.com/brown-studios/esphome-maxxfan-protocol),
which the fan accepts as well, but matching the original leaves the widest
margin on a weak or off-axis infrared path.

### Serial starter

Venus OS attaches a service to every new `ttyUSB` and probes it for VE.Direct and
MK2. That probing toggles DTR, which on an Arduino means a reset, so the port has
to be taken away from serial-starter before the driver can use it.

The driver does that **for one port at a time, and only around the question**:
it releases a candidate with `stop-tty.sh`, asks it to identify itself, and if
the answer is not `MAXXFAN` it calls `start-tty.sh` and hands the port straight
back. A foreign device loses its Venus driver for about a second instead of
until the next reboot. The port that did answer is remembered in the settings,
so from the second start onwards no other port is touched at all.

For the same reason the driver opens the port once and keeps it open; re-opening
it per command would reboot the transmitter every time.

**The permanent fix, if you want one.** Releasing a port at all is a workaround.
The Venus way to keep serial-starter off a specific device is a udev rule keyed
on its serial number — your Arduino has one, an FTDI always does:

```bash
/opt/victronenergy/swupdate-scripts/remount-rw.sh
echo 'ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_SERIAL_SHORT}=="A50285BI", ENV{VE_SERVICE}="ignore"' \
    >> /etc/udev/rules.d/serial-starter.rules
udevadm control --reload
```

Substitute your own serial number — the driver logs the by-id path it found, and
the serial is the part before `-if00`. This survives replugging but not a
firmware update, because the root filesystem is replaced; the driver's own
release still covers that case.

### Troubleshooting

**Nothing appears in the device list.** Look at the log first:

```bash
tail -f /var/log/dbus-maxxfan/current
```

`no MaxxFan transmitter on this port` means a port was found but did not
identify itself — usually the sketch is not flashed, or the wrong board is
plugged in. `no candidate USB serial port` means no CH340, FTDI, CP210x or
Arduino device is present at all.

**The device is there but has no controls.** The switch pane needs the new user
interface. Check *Settings → Display* on the GX, or the Remote Console.

**Commands are accepted but the fan does not react.** Check the infrared path
first with the *Beep* button — it is the cheapest test there is, because it
changes nothing else. If the fan stays silent, the LED is not reaching the
receiver: too far, too far off axis, or the resistor is too large. The remote
itself works over a couple of metres, so aim the LED at the fan's own receiver
window rather than at the ceiling.

**The GX shows a state the fan is not in.** Somebody used the hand held remote.
Press *Resend*, or wait for the periodic refresh.

**The speed control does nothing, and the fan beeps at every step.** The control
was configured as a *stepped switch*. That type sends the number of the position
it sits on — 1 to 7 — and ignores the minimum, maximum and step size a driver
publishes, so every position arrives as a single digit. The driver refuses those
values and says so in the log. Put it back to the basic slider:

```bash
dbus -y com.victronenergy.switch.maxxfan_ttyUSB0 \
     /SwitchableOutput/speed/Settings/Type SetValue 7
```

That is also why the stepped switch is not offered for the speed any more.

**Testing by hand.** Stop the service first — two processes on one serial port
means both see garbage:

```bash
svc -d /service/dbus-maxxfan
sleep 2
# the port the driver identified, not just the first candidate
PORT=$(grep -o '/dev/serial/by-id/[^ ]*' /var/log/dbus-maxxfan/current | tail -1)
python3 -c "
import serial, time
s = serial.Serial('$PORT', 115200, timeout=2); time.sleep(2)
s.write(b'?\n'); print(s.readline())
s.write(b'S 1 50 1 1 0 69 0\n'); print(s.readline())"
svc -u /service/dbus-maxxfan
```

### Not implemented

- **No feedback.** An infrared receiver on the Arduino could decode what the hand
  held remote sends and keep the driver in step. The sketch would only need a
  decoder and a report line; the protocol is fully known.
- **No measured temperature.** The setpoint control can display a measured value
  next to the target through `/SwitchableOutput/setpoint/Measurement`. Feeding it
  from an existing Venus temperature sensor would make the auto mode much easier
  to judge.
- **No ceiling fan mode of its own.** Setting the fan on with the lid closed
  produces it, but there is no separate control for it.

### Acknowledgements

Protocol groundwork by [skypeachblue](https://github.com/skypeachblue/maxxfan-reversing)
and [wingspinner](https://github.com/wingspinner), packet layout confirmed
against [brown-studios/esphome-maxxfan-protocol](https://github.com/brown-studios/esphome-maxxfan-protocol).
The idea of driving the fan from an Arduino on the GX comes from the
[Pekaway tutorial](https://pekaway.de/blogs/tutorials/maxxfan-uber-infrarot-steuern)
and [ffroehlcke/maxx-wifi-controller](https://github.com/ffroehlcke/maxx-wifi-controller).

### License

MIT

---

## Deutsch

Meldet einen MaxxAir MaxxFan Deluxe auf dem D-Bus von Venus OS als
`com.victronenergy.switch` an. Damit bekommt der Lüfter eine eigene Karte im
Switch-Pane des GX-Displays, in der Remote Console und im VRM — obwohl er
überhaupt keine Datenverbindung besitzt.

Die Befehle erreichen ihn auf demselben Weg wie die von der Handfernbedienung:
per Infrarot. Ein Arduino mit Infrarot-LED, bespielt mit dem Sketch aus
[`arduino/`](arduino/), hängt an einem USB-Port des GX-Geräts und sendet.

### Was der Treiber liefert

Eine Karte namens *MaxxFan* mit acht Bedienelementen:

![Die MaxxFan-Karte im Switch-Pane](docs/switch-pane.png)

| Ausgang | Typ | Inhalt |
|---|---|---|
| `fan` | Schalter | Lüfter ein / aus |
| `speed` | Schieberegler | 10 … 100 %, in Zehnerschritten |
| `direction` | Auswahl | Intake / Exhaust |
| `cover` | Schalter | Deckel offen / zu |
| `mode` | Auswahl | Manual / Auto (Thermostat) |
| `setpoint` | Temperatur-Sollwert | −2 … 37 °C |
| `resend` | Taster | Aktuellen Zustand erneut senden |
| `beep` | Taster | Lüfter zweimal piepen lassen |
| `update` | Taster | Zeigt beide Firmware-Versionen und flasht den Arduino |

Alle Elemente liegen zusätzlich unter `/SwitchableOutput/<name>/…` auf dem D-Bus
und sind damit aus Node-RED und über MQTT les- und schreibbar.

Der Dienstname richtet sich nach dem Port, den der Arduino bekommen hat — unten
steht überall `maxxfan_ttyUSB0`, es kann aber `ttyUSB1` sein, sobald etwas
anderes zuerst erkannt wird, und er kann sich nach einem Neustart ändern.
`dbus -y | grep maxxfan` zeigt den aktuellen. Die *Geräteinstanz* bleibt stabil,
sie steht in `/Settings/Devices/maxxfan/ClassAndVrmInstance`.

Der Typ jedes Elements lässt sich auf der GX-Geräteseite umstellen und wird
gespeichert — wem der Schieberegler für die Drehzahl nicht gefällt, stellt ihn
auf Dimmer um. `ValidTypes` sagt je Ausgang, was erlaubt ist.

**Zur Sprache.** Die Beschriftungen oben sind reiner Text, den der Treiber
veröffentlicht — sie bleiben also englisch, egal worauf das GX eingestellt ist.
Die Wörter, die die Oberfläche selbst beisteuert (*Ein/Aus*, *Drücken*,
*Min/Max*), folgen dagegen der Spracheinstellung des Geräts. Auf einem deutschen
GX liest sich die Karte deshalb halb englisch. Übersetzen kann ein Treiber seine
Beschriftungen nicht, umbenennen lässt sich aber jedes Element, und das wird
gespeichert:

```bash
S=com.victronenergy.switch.maxxfan_ttyUSB0
dbus -y $S /SwitchableOutput/fan/Settings/CustomName SetValue "Lüfter"
dbus -y $S /SwitchableOutput/speed/Settings/CustomName SetValue "Drehzahl"
```

Die Einträge der beiden Auswahllisten kommen aus `Settings/Labels` und bleiben
so, wie der Treiber sie setzt.

### Infrarot geht nur in eine Richtung

Der Lüfter antwortet nie. Was das GX-Display zeigt, ist **der zuletzt gesendete
Zustand**, kein Messwert. Wer die Handfernbedienung benutzt, bringt beide
auseinander — bis zum nächsten Befehl vom GX, der den Lüfter sofort wieder in
Deckung bringt, weil jedes Paket den vollständigen Zustand trägt.

Zwei Punkte, die daraus folgen:

- **Beim Start wird nichts gesendet.** Den gespeicherten Zustand bei jedem
  Neustart des GX erneut zu funken würde einen Lüfter starten, den jemand
  bewusst von Hand ausgeschaltet hat. Der Zustand wird veröffentlicht, nicht
  gesendet.
- **Alle 15 Minuten läuft eine Auffrischung** (`REFRESH_S` im Treiber) und sendet
  den aktuellen Zustand erneut, damit ein von Hand bedienter Lüfter von selbst
  wieder unter GX-Kontrolle kommt. `REFRESH_S = 0` schaltet das ab; dann bleibt
  der *Resend*-Taster.

Weil das Protokoll kein „nur Drehzahl"-Kommando kennt, überträgt jede Änderung
alle acht Felder. Änderungen werden deshalb erst 600 ms gesammelt — am
Drehzahlregler zu ziehen erzeugt so ein Paket statt zwanzig.

### Installation

#### Mit SetupHelper (empfohlen)

[SetupHelper](https://github.com/kwindrem/SetupHelper) installieren, dann im
GX-Menü unter *Settings → Package manager → Inactive packages → new* eintragen:

| Feld | Wert |
|---|---|
| Package name | `dbus-maxxfan` |
| GitHub user | `mcgyver78` |
| GitHub branch or tag | `latest` |

Anschließend *Proceed* → *Install*.

#### Manuell

SetupHelper wird so oder so gebraucht — das setup-Skript ist nur eine dünne
Hülle darum. Dieser Weg spart lediglich den Umweg über die Package-Manager-Maske.

```bash
cd /data
git clone https://github.com/mcgyver78/dbus-maxxfan.git
/data/dbus-maxxfan/setup
```

### Voraussetzungen

- Venus OS ab v3.70 mit **neuer Oberfläche** — das Switch-Pane gibt es nur in
  gui-v2. In der klassischen Oberfläche taucht das Gerät zwar in der Geräteliste
  auf, seine Bedienelemente werden aber nicht gezeichnet.
- Python 3 und `pyserial`, beides in Venus OS enthalten
- Ein Arduino an einem USB-Port des GX-Geräts mit dem Sketch aus
  [`arduino/maxxfan_tx`](arduino/maxxfan_tx)

### Hardware

```
  +-----------+  USB seriell   +----------------+   38 kHz IR   +-----------+
  |  Cerbo GX |<-------------->|  Arduino Nano  | ~~~~~~~~~~~~> |  MaxxFan  |
  |           |     115200     |   maxxfan_tx   |    940 nm     |   Deluxe  |
  +-----------+                +----------------+               +-----------+
        ^                                                              :
        | D-Bus, Switch-Pane            kein Rueckkanal - siehe "Nicht umgesetzt"
```

| Teil | Anmerkung |
|---|---|
| ATmega328P-Board | klassischer Nano, Uno oder Pro Mini. Kein Nano Every und kein ESP: der Sketch spricht Timer 1 direkt an |
| Infrarot-LED, 940 nm | z. B. TSAL6200, oder die LED eines KY-005-Moduls |
| Widerstand 180 … 220 Ω | stellt den LED-Strom auf rund 20 mA |
| USB-Kabel zum GX-Gerät | ein Datenkabel, kein Ladekabel |

Mehr ist die Schaltung nicht:

```
   D9 (OC1A) --[ 180 ... 220 R ]--|>|-- GND
                                 IR-LED
                            Anode        Kathode
                          (langes Bein) (kurzes Bein, abgeflachte Seite)
```

**Pin 9 ist nicht frei wählbar.** Der 38-kHz-Träger kommt aus Timer 1 in
Hardware, und der bedient genau diesen Pin. Dass der Träger in Hardware entsteht,
ist auch der Grund, warum dieser Sketch die Interrupts nicht abschaltet: Ein
Paket ist 176 Symbole lang und belegt die LED rund 150 ms. Wer den Träger per
Software erzeugt, muss dieses ganze Fenster über die Interrupts sperren — und
verliert jedes serielle Byte, das darin ankommt. Hier läuft die serielle
Schnittstelle weiter, während gesendet wird.

**Reichweite.** Direkt vom Pin getrieben läuft die LED mit etwa 20 mA, das reicht
für ein bis zwei Meter. Mehr braucht es nicht, denn die LED gehört ohnehin direkt
neben das Empfangsfenster des Lüfters. Wer trotzdem mehr will, treibt sie über
einen NPN-Transistor (BC337, 2N3904) aus 5 V mit 22 … 47 Ω — der Träger hat nur
ein Drittel Tastverhältnis und ein Paket dauert 150 ms, den höheren Impulsstrom
steckt die LED locker weg.

**Wohin damit.** Die LED an den Lüfter, das USB-Kabel zum GX-Gerät. Normales USB
trägt etwa drei Meter, darüber hinaus ein aktives Kabel nehmen.

**Aktueller Stand: nur senden.** Ein Empfänger ist nicht verbaut. Was jemand mit
der Handfernbedienung macht, sieht der Treiber deshalb nicht. Dafür bräuchte es
ein 38-kHz-Empfängermodul (TSOP38238, TSOP4838 oder VS1838B) mit dem Ausgang an
D2 — siehe [Nicht umgesetzt](#nicht-umgesetzt).

Geflasht wird mit der Arduino-IDE — Board *Arduino Nano*, Prozessor *ATmega328P*,
bei den meisten Clones *ATmega328P (Old Bootloader)* — oder mit `arduino-cli`.
Bibliotheken sind keine nötig. Kompiliert belegt der Sketch rund 3,4 kB, ein
Zehntel des Flash.

#### Flashen, und was dabei schiefgeht

Jeder dieser Punkte hat hier Zeit gekostet, deshalb stehen sie hier, statt neu
gefunden zu werden.

| Symptom | Ursache |
|---|---|
| `bad CPU type in executable` beim Kompilieren | Apple Silicon ohne Rosetta — die AVR-Toolchain gibt es nur für Intel. `softwareupdate --install-rosetta --agree-to-license` |
| `expected unqualified-id before '<' token` | in der `.ino` steht HTML. Sie wurde von der gerenderten GitHub-Seite kopiert statt aus der Datei; den *Raw*-Button oder den Klon nehmen |
| Kein Eintrag *Prozessor* im Menü *Werkzeuge* | falsches Board. `ATmega328P Xplained Mini` ist derselbe Chip auf einer anderen Plattform und kennt keine Bootloader-Varianten. Es muss *Arduino Nano* aus *Arduino AVR Boards* sein — in der ersten Zeile der ausführlichen Ausgabe muss `FQBN: arduino:avr:nano` stehen |
| `stk500_recv(): programmer is not responding`, `not in sync` | älteres Board mit altem Bootloader: *Werkzeuge → Prozessor → ATmega328P (Old Bootloader)* |
| `cannot open port …: No such file or directory` | das Board ist vom Bus verschwunden. Neu einstecken und den Port erneut auswählen — die IDE hält an der alten Auswahl fest |
| Die Größenangabe erscheint, aber keine avrdude-Ausgabe | es wurde *Überprüfen* gedrückt, nicht *Hochladen*. Beide geben die Größe aus, nur *Hochladen* startet avrdude |
| Upload scheitert, obwohl der Port da ist | der Serial Monitor hält den Port. Vorher schließen |
| Unter *Werkzeuge → Port* taucht gar nichts auf | fast immer ein Ladekabel ohne Datenleitungen. Die Board-Auswahl hat darauf keinen Einfluss |
| Keine Antwort im Serial Monitor | 115200 Baud, und das Zeilenende muss auf *Neue Zeile* stehen. Sonst sieht der Sketch nie ein vollständiges Kommando |

Unter macOS den **`/dev/cu.…`**-Port nehmen, nicht `/dev/tty.…` — die tty-Variante
blockiert, bis das Gerät DCD meldet, und der Upload hängt dann ohne Fehlermeldung.

Das Board hier ist ein FTDI-Nano, der eine eindeutige Seriennummer trägt und als
`usb-FTDI_FT232R_USB_UART_<seriennummer>-if00-port0` erscheint. CH340-Clones haben
meist keine — deshalb prüft der Treiber den Port durch Nachfragen und nicht über
den Namen.

#### Serielles Protokoll

115200 8N1, ein Kommando je Zeile:

```
?                                                     -> MAXXFAN 1 1.3
S <on> <speed> <exhaust> <cover> <auto> <degF> <warn> -> OK <32 Hexziffern>
R                                                     -> OK <32 Hexziffern>
```

`S` baut das Paket, sendet es und gibt es als Hex zurück, damit der Treiber
mitschreiben kann, was tatsächlich rausging. `R` wiederholt das letzte Paket
unverändert. Werte außerhalb des zulässigen Bereichs werden mit `ERR` abgelehnt,
nicht gesendet. Der Sollwert steht hier in Fahrenheit, weil das die Einheit auf
dem Draht ist — der Treiber rechnet um.

Die Typabfrage ist das, was die Portsuche sicher macht. Der Treiber sucht in
`/dev/serial/by-id/` nach CH340-, FTDI-, CP210x- und Arduino-Namen und lässt
jeden Kandidaten sich identifizieren, bevor ein einziges Kommando hinausgeht.

Das sind Chipnamen, keine Gerätenamen, und der Unterschied ist wichtig: Victrons
VE.Direct-USB- und MK3-USB-Kabel bringen eigene Deskriptoren mit und tauchen nie
auf — das **RS485-zu-USB-Interface** dagegen, das Kabel zum Carlo-Gavazzi-Zähler,
ist ein blanker FTDI FT232R und sieht in der by-id-Liste aus wie ein Arduino. Ein
Treffer ist deshalb immer nur ein Grund nachzufragen, nie ein Grund zu handeln.
Was das praktisch heißt, steht unter [Serial-Starter](#serial-starter-1).

### Den Arduino vom GX aus aktualisieren

Venus OS hat kein avrdude und braucht auch keins: Der Bootloader, mit dem die
Arduino-IDE redet, spricht STK500v1, und [`tools/flash.py`](tools/flash.py)
setzt davon so viel um, wie zum Programmieren eines ATmega328P nötig ist - über
pyserial, das auf Venus vorhanden ist. Steckt der Sender einmal am GX-Gerät,
muss er nie wieder an einen Laptop.

**Wo die Versionen stehen.** Auf der Geräteseite steht der Sketch unter
*Firmware version* und der Treiber in der Zeile *Connection*. Auf der Karte
trägt das Element unten rechts beide:

```
Transmitter 1.3 (up to date)
Transmitter 1.2 (update to 1.3)      <- Druck flasht
Transmitter pre-1.3 (update to 1.3)  <- Sketch älter als dieser Mechanismus
```

Es sortiert hinter *Speed*, weil die Karte ihre Elemente nach der Beschriftung
ordnet - deshalb sitzt es abseits der Lüfterbedienelemente. Benennst du es um,
lässt der Treiber die Beschriftung in Ruhe.

**Geflasht wird nur auf Knopfdruck.** Nicht bei der Installation, nicht bei
einer Versionsabweichung. Die einzige Ausnahme ist die Reparatur: Hat der Port,
der sich zuletzt als unserer gemeldet hat, jetzt einen Bootloader, aber keinen
funktionierenden Sketch - ein abgebrochenes Update also -, bringt der Treiber
beim Start zu Ende, was angefangen wurde. Wessen Board das ist, steht dort außer
Frage, und die Alternative wäre ein Lüfter, der tot bleibt, bis jemand mit einem
Laptop hinfährt.

Auf jedem anderen Port reicht ein Bootloader nie zum Flashen. Er sagt nur "hier
wohnt ein AVR", nicht, wessen Projekt darauf läuft.

**Was sich so nicht aktualisieren lässt:** ein Board mit gelöschtem Bootloader
(braucht einen ISP-Programmer), eines mit abgeschaltetem Auto-Reset - die
RESET-EN-Brücke aufzutrennen ist ein verbreiteter Trick, damit das Board beim
Öffnen eines Ports nicht neu startet, und er sperrt das Flashen gleich mit - und
alles, was kein ATmega328P oder 168 ist; das fängt die Signaturprüfung ab. Ein
Fall geht still schief: ein für 16 MHz gebauter Sketch auf einem 8-MHz-Board
meldet brav seine Version und funkt mit halber Symbolzeit. Den Takt kann eine
Signatur nicht unterscheiden.

Kaputtflashen kann man das Board nicht. Der Bootloader liegt in einem
geschützten Flash-Bereich und kann sich nicht selbst überschreiben; schlimmster
Fall ist ein kaputter Sketch bei intaktem Bootloader - genau der Zustand, für
den der Reparaturweg oben da ist.

**Firmware und Sketch zusammenhalten.** Geflasht wird `arduino/maxxfan_tx.hex`,
die Datei muss also zum Sketch passen — eine veraltete würde die Karte ein
Update anbieten lassen, das etwas Älteres installiert.
[`tools/build-hex.sh`](tools/build-hex.sh) baut sie samt Versionsdatei aus
`SKETCH_VERSION` neu, und [`tools/check-hex.py`](tools/check-hex.py) — von einem
GitHub-Workflow bei jeder Änderung ausgeführt — prüft, dass beide
zusammenpassen.

Die Prüfung liest die Versionszeichenkette **aus der committeten `.hex`**, wo
die Identifikationsantwort des Sketches sie ablegt, statt gegen einen frischen
Build zu vergleichen. Zwei Toolchain-Versionen erzeugen aus identischem
Quelltext verschiedene Binärdateien, ein Byte-Vergleich schlägt also aus Gründen
fehl, die mit der Aktualität der Firmware nichts zu tun haben. Beim Ändern des
Sketches `SKETCH_VERSION` hochzählen, dann hat die Prüfung etwas zu greifen.

### Warum `switch` und kein Lüfter-Dienst

Venus OS kennt keine Dienstklasse für einen Lüfter. `com.victronenergy.switch`
ist die, nach deren `/SwitchableOutput/x/…`-Pfaden die Oberfläche sucht, und sie
bietet genau die Elemente, die dieses Gerät braucht: Schalter, einen gestuften
Wert, Auswahllisten mit Beschriftungen, einen Temperatur-Sollwert und Taster.
Dieselbe API benutzen der GX-IO-Extender, die Virtual Switches aus Node-RED und
digitale Schalter von Drittanbietern — der Lüfter sitzt also im Switch-Pane
neben ihnen, ganz ohne GUI-Modifikation.

### Protokoll

38-kHz-Träger, ein Drittel Tastverhältnis. Der Bitstrom sieht aus wie RS232: ein
Startbit, acht Datenbits mit dem niederwertigsten zuerst, zwei Stoppbits. Ein
**Mark kodiert eine Null, ein Space eine Eins**, jedes Symbol **834 µs** lang.
Sechzehn Bytes, mehr Rahmen gibt es nicht:

| Byte | Inhalt |
|---|---|
| 0–9 | feste Präambel `5A A5 80 7F 40 BF 20 DF 10 CC` |
| 10 | Zustandsbits |
| 11 | Drehzahl, 0 … 100 in Zehnerschritten |
| 12 | Thermostat-Sollwert in °F |
| 13 | immer `FF` |
| 14 | immer `23` |
| 15 | XOR der Bytes 10 … 14 |

Zustandsbyte 10:

| Bit | Bedeutung |
|---|---|
| 0 (0x01) | Lüfter an |
| 1 (0x02) | special — siehe unten |
| 2 (0x04) | Richtung: 0 Intake, 1 Exhaust |
| 3 (0x08) | Deckel offen |
| 4 (0x10) | Auto-Modus (Thermostat) |
| 5 (0x20) | warn — der Lüfter piept zweimal |

Das **special-Bit** hebt die eingebaute Kopplung von Deckel und Motor auf. Es ist
im Thermostatbetrieb gesetzt und im Ceiling-Fan-Modus — Lüfter an bei
geschlossenem Deckel —, den der Lüfter sonst verweigert. Der Sketch leitet es als
`auto || (on && !deckel_offen)` ab; das stimmt mit allen 99 Referenzaufnahmen
überein.

Jedes Paket trägt den kompletten Zustand. Inkrementelle Kommandos gibt es nicht,
und genau deshalb muss ein Treiber für dieses Gerät sich merken, was er zuletzt
gesendet hat.

#### Der Sollwert steht in Fahrenheit

Die Fernbedienung zeigt Celsius an, überträgt aber Fahrenheit — und rechnet dabei
**gegen null abgeschnitten** um:

```
degF = trunc(degC × 1,8) + 32
```

Das ist nicht dasselbe wie Runden. Gerundet liegt man bei 20 der 38 Sollwerte um
ein Grad Fahrenheit daneben — bei 18 der 40 Werte im Bereich −2 … 37 °C: aus
21 °C würden 70 °F statt der 69 °F, die die Fernbedienung sendet. Der Treiber schneidet ab, damit ein am GX eingestellter
Sollwert dem entspricht, den der Lüfter anzeigt.

#### Geprüft gegen die Originalfernbedienung

[`tools/verify-encoder.py`](tools/verify-encoder.py) dekodiert die 99 Signale,
die [skypeachblue](https://github.com/skypeachblue/maxxfan-reversing) mit einem
Flipper Zero von einer Originalfernbedienung aufgezeichnet hat, kodiert jedes neu
und vergleicht Symbol für Symbol:

```bash
python3 tools/verify-encoder.py Maxxfan_collection.ir
```

Alle 99 stimmen exakt. [`tools/test-driver-logic.py`](tools/test-driver-logic.py)
deckt die andere Hälfte ab: Es bildet den D-Bus-Dienst mit derselben
Annehmen/Ablehnen-Logik nach, die vedbus tatsächlich hat, und prüft, dass ein
Bedienelement, das Stufennummern schickt, abgelehnt statt gespeichert wird, dass
eine fehlgeschlagene Sendung wiederholt wird und der Dienst danach aufgibt, dass
ein Port ohne Identifikation an den serial-starter zurückgeht und dass ein
gespeicherter Wert, den der Lüfter nicht kann, in den zulässigen Bereich gerückt
wird. Beides läuft ohne GX-Gerät.

Aus dem Vergleich mit den Aufnahmen stammen auch die 834 µs Symbolzeit
und die abschneidende Temperaturumrechnung — beides weicht von der verbreiteten
[ESPHome-Komponente](https://github.com/brown-studios/esphome-maxxfan-protocol)
ab, die der Lüfter zwar ebenfalls akzeptiert; nah am Original zu bleiben lässt
aber den größten Spielraum bei schwacher oder schräger Infrarotstrecke.

### Serial-Starter

Venus OS hängt an jedes neue `ttyUSB` einen Dienst und probiert VE.Direct und
MK2 durch. Dieses Abtasten zieht DTR, und das heißt bei einem Arduino: Reset. Der
Port muss dem serial-starter also entzogen werden, bevor der Treiber ihn nutzen
kann.

Das tut der Treiber **für genau einen Port und nur um die Frage herum**: Er gibt
einen Kandidaten mit `stop-tty.sh` frei, lässt ihn sich identifizieren, und wenn
die Antwort nicht `MAXXFAN` lautet, ruft er `start-tty.sh` und gibt den Port
sofort zurück. Ein fremdes Gerät verliert seinen Venus-Treiber damit für etwa
eine Sekunde statt bis zum nächsten Neustart. Der Port, der geantwortet hat,
steht danach in den Settings — ab dem zweiten Start wird kein anderer mehr
angefasst.

Aus demselben Grund öffnet der Treiber den Port einmal und hält ihn offen — ihn
pro Kommando neu zu öffnen würde den Sender jedes Mal neu starten.

**Die dauerhafte Lösung, wenn du eine willst.** Einen Port überhaupt freizugeben
ist ein Behelf. Der Venus-Weg, serial-starter von einem bestimmten Gerät
fernzuhalten, ist eine udev-Regel auf die Seriennummer — dein Arduino hat eine,
FTDI-Chips haben immer eine:

```bash
/opt/victronenergy/swupdate-scripts/remount-rw.sh
echo 'ACTION=="add", ENV{ID_BUS}=="usb", ENV{ID_SERIAL_SHORT}=="A50285BI", ENV{VE_SERVICE}="ignore"' \
    >> /etc/udev/rules.d/serial-starter.rules
udevadm control --reload
```

Die eigene Seriennummer einsetzen — der Treiber schreibt den gefundenen
by-id-Pfad ins Log, die Seriennummer ist der Teil vor `-if00`. Das übersteht
Umstecken, aber kein Firmware-Update, weil das Root-Dateisystem ersetzt wird;
dafür greift dann wieder die Freigabe durch den Treiber.

### Fehlersuche

**In der Geräteliste taucht nichts auf.** Zuerst ins Log schauen:

```bash
tail -f /var/log/dbus-maxxfan/current
```

`no MaxxFan transmitter on this port` heißt, ein Port wurde gefunden, hat sich
aber nicht identifiziert — meist ist der Sketch nicht geflasht oder das falsche
Board steckt. `no candidate USB serial port` heißt, es ist überhaupt kein CH340-,
FTDI-, CP210x- oder Arduino-Gerät da.

**Das Gerät ist da, hat aber keine Bedienelemente.** Das Switch-Pane braucht die
neue Oberfläche. Unter *Settings → Display* am GX oder in der Remote Console
nachsehen.

**Kommandos werden angenommen, der Lüfter reagiert nicht.** Die Infrarotstrecke
zuerst mit dem *Beep*-Taster prüfen — der billigste Test überhaupt, weil er sonst
nichts verändert. Bleibt der Lüfter still, kommt die LED nicht an: zu weit, zu
schräg, oder der Vorwiderstand ist zu groß. Die Originalfernbedienung schafft ein
paar Meter, also die LED auf das Empfängerfenster des Lüfters richten und nicht
irgendwohin an die Decke.

**Das GX zeigt einen Zustand, in dem der Lüfter nicht ist.** Da war die
Handfernbedienung am Werk. *Resend* drücken oder die Auffrischung abwarten.

**Der Drehzahlregler bewirkt nichts, der Lüfter piept bei jeder Stufe.** Dann
steht das Element auf *Stufenschalter*. Dieser Typ überträgt die Nummer der
Position, auf der er steht — 1 bis 7 — und ignoriert Minimum, Maximum und
Schrittweite, die der Treiber angibt; jede Stufe kommt also als einstellige Zahl
an. Der Treiber weist solche Werte ab und schreibt es ins Log. Zurück auf den
Schieberegler:

```bash
dbus -y com.victronenergy.switch.maxxfan_ttyUSB0 \
     /SwitchableOutput/speed/Settings/Type SetValue 7
```

Aus demselben Grund wird der Stufenschalter für die Drehzahl gar nicht mehr
angeboten.

**Von Hand testen.** Vorher den Dienst stoppen — zwei Prozesse auf einem
seriellen Port bedeuten für beide Müll:

```bash
svc -d /service/dbus-maxxfan
sleep 2
# der Port, den der Treiber identifiziert hat, nicht einfach der erste Kandidat
PORT=$(grep -o '/dev/serial/by-id/[^ ]*' /var/log/dbus-maxxfan/current | tail -1)
python3 -c "
import serial, time
s = serial.Serial('$PORT', 115200, timeout=2); time.sleep(2)
s.write(b'?\n'); print(s.readline())
s.write(b'S 1 50 1 1 0 69 0\n'); print(s.readline())"
svc -u /service/dbus-maxxfan
```

### Nicht umgesetzt

- **Keine Rückmeldung.** Ein Infrarotempfänger am Arduino könnte mitlesen, was
  die Handfernbedienung sendet, und den Treiber nachführen. Der Sketch bräuchte
  dafür nur einen Dekoder und eine Meldezeile — das Protokoll ist vollständig
  bekannt.
- **Keine Ist-Temperatur.** Das Sollwert-Element kann über
  `/SwitchableOutput/setpoint/Measurement` einen Messwert neben dem Sollwert
  anzeigen. Aus einem vorhandenen Venus-Temperatursensor gespeist, wäre der
  Auto-Modus deutlich besser zu beurteilen.
- **Kein eigener Ceiling-Fan-Modus.** Lüfter an bei geschlossenem Deckel erzeugt
  ihn, ein eigenes Bedienelement dafür gibt es nicht.

### Dank

Protokoll-Vorarbeit von [skypeachblue](https://github.com/skypeachblue/maxxfan-reversing)
und [wingspinner](https://github.com/wingspinner), Paketaufbau gegengeprüft mit
[brown-studios/esphome-maxxfan-protocol](https://github.com/brown-studios/esphome-maxxfan-protocol).
Die Idee, den Lüfter von einem Arduino am GX aus zu steuern, stammt aus dem
[Pekaway-Tutorial](https://pekaway.de/blogs/tutorials/maxxfan-uber-infrarot-steuern)
und von [ffroehlcke/maxx-wifi-controller](https://github.com/ffroehlcke/maxx-wifi-controller).

### Lizenz

MIT
