# dbus-maxxfan — working notes

A Venus OS driver that puts a MaxxAir MaxxFan Deluxe roof fan on the GX device's
D-Bus as `com.victronenergy.switch`, so it gets a card in the switch pane. The
fan has no data connection of any kind; it is commanded over infrared by an
Arduino Nano with an IR LED, hanging on a USB serial port of the GX.

`ReadMe.md` is the user-facing documentation, bilingual EN/DE, and it is
detailed. This file is the part that is easy to get wrong and expensive to
rediscover.

## The one rule

**Nothing goes on the air unless a person asked for it.** Infrared is one way.
The driver can never read the fan back, so every packet it sends on its own
initiative is a blind write, and a blind write eventually undoes something
somebody just did by hand. This is not a style preference — it was a real bug
(v1.6): a 15-minute "refresh" re-asserted the stored state, and fans started by
themselves a quarter of an hour after being switched off at the fan.

Consequences that must stay true:

- Startup is silent. The state is published, never transmitted.
- `REFRESH_S = 0` ships as the default and there are tests that say so.
- The sketch transmits only on an `S` or `R` command.
- `Resend` is the user-facing way to re-assert. That is what it is for.

## Hardware and the system this runs on

- Arduino Nano (ATmega328P), IR LED on **pin 9 (OC1A)** through a resistor.
  Pin 9 is not a free choice: timer 1 generates the 38 kHz carrier in hardware,
  which is what keeps the serial port and `millis()` alive during a packet.
- Lars' transmitter is an **FTDI FT232R, serial `A50285BI`**, on the GX as
  `ttyUSB0`; the service registers as
  `com.victronenergy.switch.maxxfan_ttyUSB0`, device instance 41.
- That GX runs **Venus OS v3.79** (current stable large) with the new UI —
  the switch pane is a gui-v2 feature, the classic UI does not draw it.
- **There is no SSH access to that Cerbo.** Everything has to be reachable from
  the GX menus, the Package manager, or the card itself. That constraint is why
  the firmware version is on the card, why flashing is a button, and why the
  USB stick install exists. It can be switched on from the display if it ever
  has to be (Settings → General → superuser → root password → SSH on LAN), but
  do not design as if a console is available.
- On that GX an **Autoterm heater sits on an FTDI** and a Buck-Boost on a
  CP210x. Both look exactly like transmitter candidates. See "serial-starter".

## The infrared protocol

Reverse-engineered by others, verified here: `tools/verify-encoder.py` decodes
99 recorded remote signals, re-encodes them with our encoder, and requires an
exact match. That check found two errors in the widely-copied ESPHome
component, so trust this repository over the internet on these two points:

- **834 µs per symbol**, not the 800 µs usually quoted (measured average over
  the 99 captures).
- **Celsius → Fahrenheit truncates**: `int(degC * 1.8) + 32`. A rounded
  conversion is off by one on 18 of the 40 values in the −2…37 °C range.

38 kHz carrier, RS232-like framing per byte (1 start bit, 8 data bits LSB
first, 2 stop bits), mark = 0 and space = 1. 16-byte packet:

```
0..9   preamble  5A A5 80 7F 40 BF 20 DF 10 CC
10     state     bit0 on, bit1 special, bit2 exhaust,
                 bit3 lid open, bit4 auto, bit5 warn (beep)
11     speed     0, 10, 20 ... 100
12     setpoint  °F, 29..99 - the fan's own unit
13     FF
14     23
15     XOR of bytes 10..14
```

`special = auto || (on && !cover)`. It overrides the fan's own coupling of lid
and fan — ceiling-fan mode, running with the lid shut, which the fan otherwise
refuses.

**Every packet carries the complete state.** There is no "speed only" message.
That is why changes are coalesced for `COALESCE_MS` before a packet goes out,
and it is also why a stale re-send is so destructive.

## Venus OS / GX specifics

- `com.victronenergy.switch`, paths under `/SwitchableOutput/<key>/…`. Types:
  0 momentary, 1 toggle, 2 dimmable, 3 temperature setpoint, 4 stepped switch,
  6 dropdown, 7 basic slider. `ValidTypes` is a bitfield.
- **Do not offer type 4 (stepped switch) for speed.** The GX sends the position
  index 1…7 and ignores `DimmingMin`/`Max`/`StepSize`. Every step then rounded
  to 10 %: the fan beeped and never changed speed. Type 7 (slider) is the
  default for `speed` and type 4 is not in its `ValidTypes`.
- **The card sorts its elements alphabetically by label**, then wraps into two
  columns. That is the only layout control there is. `Transmitter` is called
  that so it sorts after `Speed` and lands bottom right, clear of the fan
  controls. Renaming an element stops the driver from touching that label.
- **An `onchangecallback` that returns truthy makes vedbus store the value**
  (`vedbus.py:589-593` calls `local_set_value(newvalue)`). A guard that rejects
  a value must `return False`, otherwise the rejected value is stored and
  broadcast anyway. `tools/test-driver-logic.py` models this exactly.
- Settings live under `/Settings/Devices/maxxfan/`. `Port` is the remembered
  transmitter port; `AdoptAttempted` is the one-shot adoption flag.

## serial-starter — the dangerous part

Three bugs here, all the same shape: the driver has to look at ports that
belong to other people, because a CH340/FTDI/CP210x says "an adapter lives
here", never "this is ours".

1. **v1.2** — the run script released *every* candidate port. On a system with
   Victron's RS485-to-USB interface (an FT232R), the grid meter's driver was
   stopped permanently, every 12 seconds. Releasing is now scoped to a single
   port inside `probe()`, with a hand-back when it does not answer.
2. **v1.7** — probing costs other drivers real traffic. Measured on a Cerbo
   with an Autoterm heater on an FTDI: five Venus services probing the same
   port cut the heater from 29 answers out of 29 to 2 out of 30.
3. **v1.8** — v1.7 used "no node under `/dev/serial-starter`" as "somebody else
   owns this port". But *this* driver removes that node too when it claims a
   port, and it does not come back until a reboot. After any restart the driver
   refused its own port and the card vanished. A port without a node is now
   only skipped when another process actually holds it open, read from `/proc`.

The lesson: infer ownership from a fact, not from a heuristic, and always ask
what happens on the *second* start.

## Flashing the Arduino from the GX

Venus OS has no avrdude and does not need one — the bootloader speaks STK500v1,
and `tools/flash.py` implements enough of it over pyserial (which Venus ships).
The reset comes from opening the port, so there is no DTR polarity to get
wrong. An interrupted write cannot brick the board: the bootloader sits in
protected flash.

Flashing without being asked happens in exactly three cases, and each one rests
on knowing whose board it is:

- the **remembered port** has a bootloader but no working sketch (an
  interrupted update, or a swapped-in bare board);
- a **fresh installation** with no port remembered, no MaxxFan answering, and
  exactly one AVR bootloader among the candidates — once per installation,
  recorded in `AdoptAttempted`;
- a **port named on the command line**.

Anywhere else a bootloader is not enough. Two AVRs on the bus and the driver
keeps its hands off rather than overwrite somebody's other project.

Cannot be flashed this way: an erased bootloader (needs ISP), a board with
auto-reset cut, anything that is not an ATmega328P/168. One case fails
silently — a 16 MHz sketch on an 8 MHz board reports its version happily and
transmits at half speed; a signature cannot tell clock rates apart.

Note that a CH340 clone usually carries **no** serial number, so every one of
them appears under the same `/dev/serial/by-id` name — which is what makes a
board swap work, and also means two of them cannot be told apart. FTDI and
CP210x do have serial numbers.

## Testing

Everything is testable without a GX device or an Arduino, and CI runs all of
it. Keep it that way — adding a change without a check that would have caught
the bug is the exception, not the rule.

```bash
python3 tools/test-driver-logic.py      # D-Bus behaviour against a fake vedbus
python3 tools/test-flash.py             # the programmer against a simulated
                                        # STK500v1 bootloader on a pty
python3 tools/verify-encoder.py Maxxfan_collection.ir   # 99/99 captures
cd tools/test-sketch && g++ -std=c++17 -D__AVR_ATmega328P__ -I. \
    -include Arduino.h -x c++ ../../arduino/maxxfan_tx/maxxfan_tx.ino \
    lines_main.cpp -o /tmp/t && /tmp/t                  # sketch line handling
python3 tools/check-hex.py              # shipped .hex matches SKETCH_VERSION
python3 tools/check-version.py          # version, VERSION and both changelogs
```

The capture file for the encoder check comes from
`skypeachblue/maxxfan-reversing` and is downloaded by the workflow, not
committed.

`tools/test-flash.py` runs on Linux only. On macOS it hangs for good in
`termios.tcdrain()` on the pty: there is no real UART behind it, so the drain
never completes. The test is not broken and needs no fix — run it in CI, or
under Linux. Everything else in the list runs on the Mac.

`tools/check-hex.py` reads the version string *out of* the committed `.hex`
rather than rebuilding and comparing bytes: two toolchain versions produce
different binaries from identical source, and that says nothing about whether
the firmware is current. This already broke CI once.

## Releasing

Branch is **`latest`**, not main — SetupHelper's `gitHubInfo` says
`mcgyver78:latest`. On a release, keep these in step:

- `version` — leading `v`, e.g. `v1.8`. This is what the Package manager reads.
- `VERSION` in `dbus-maxxfan.py` — no `v`. Published as `/Mgmt/ProcessVersion`
  and in the `Connection` row.
- `changes` — SetupHelper shows this one.
- `ChangeLog` — a second changelog that arrived in v1.8.
- `SKETCH_VERSION` in the `.ino`, plus `arduino/maxxfan_tx.hex` and its `.ver`,
  rebuilt with `tools/build-hex.sh`, **only when the sketch changed**.
  Driver-only releases leave the firmware alone.

v1.8 shipped with two of these forgotten — `VERSION` stayed at 1.7, so the
card reported the previous release, and the v1.8 entry went only into
`ChangeLog` while the Package manager reads `changes`. Both were caught up
after the fact, and `tools/check-version.py` now fails the build on any of
these four drifting apart. Nothing connects those files to each other, so the
check is the only thing that does.

> Still open: whether two changelogs earn their keep. `ChangeLog` holds exactly
> one entry, v1.8, which `changes` now also carries; `changes` has the full
> history back to v1.0 and is the one SetupHelper shows. Dropping `ChangeLog`
> would lose nothing — that is a decision, not a cleanup, so it is left alone.

Two remotes, and both are meant to move together: `origin` is
`github.com/mcgyver78/dbus-maxxfan`, which is what the Package manager fetches,
and `gitlab` is the backup copy at
`git@git.tigerexped.de:tigerexped_playground/dbus-maxxfan.git`. GitLab over
**SSH only** — the HTTPS URL answers 403, because the token in the keychain has
no `Code: Download` for this project.

```bash
git push origin latest && git push gitlab latest
```

Installing: Package manager (`dbus-maxxfan` / `mcgyver78` / `latest`), the USB
stick built by `tools/make-usb-zip.sh` and published by a workflow, or
`git clone` plus `./setup`. The USB route is the one that needs no console.

## House style

- `ReadMe.md` is EN and DE and the two halves must stay in step. Edits are
  easiest as a small Python script doing exact-match replacements on both.
- Comments say *why*, especially where the code looks odd. Most of the odd
  parts here are scar tissue from a specific failure; say which one.
- Plain ASCII in the sketch and in anything that ends up on a USB stick.
- Commit messages: what broke, why, and what now holds the line.

## Deliberately not done

- **No IR receiver.** A TSOP38238 on D2 would let the driver learn what the
  hand remote sent and stop the display from lying. Discussed, not built.
- **No ambient temperature.** Venus knows the cabin temperature, the fan does
  not, and the fan's thermostat uses its own internal sensor. Feeding a Venus
  sensor into the setpoint would be a different feature — a GX-side thermostat
  driving the fan in manual mode — not a fix to this one.
