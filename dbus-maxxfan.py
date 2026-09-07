#!/usr/bin/env python3
"""
dbus-maxxfan.py - publishes a MaxxAir MaxxFan Deluxe on the Venus OS D-Bus as
com.victronenergy.switch, so the fan gets a card in the switch pane of the GX
display, the remote console and VRM.

The fan has no data connection of any kind. It is commanded over infrared by an
Arduino running the maxxfan_tx sketch from arduino/ in this repository, which
hangs on a USB serial port of the GX device. Infrared is one way, so this driver
keeps the fan state itself and stores it in the Venus settings; the GX shows the
state that was last sent, not a reading from the fan.

Every change transmits the complete state, because that is what the remote
control protocol carries - there is no "speed only" message. Changes are
therefore collected for a moment before a packet goes out, so dragging a slider
does not put twenty packets on the air.
"""
import glob
import os
import subprocess
import sys
import time

import serial
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

for _p in ("/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
           "/opt/victronenergy/velib_python",
           os.path.join(os.path.dirname(__file__), "velib_python")):
    if os.path.isdir(_p):
        sys.path.insert(1, _p)
        break
from vedbus import VeDbusService  # noqa: E402

VERSION = "1.3"
SERVICE_CLASS = "switch"
FALLBACK_INSTANCE = 41
BAUD = 115200
SERIAL_STARTER = "/opt/victronenergy/serial-starter"
# The Arduino resets when the port is opened, then runs its bootloader.
RESET_WAIT = 2.0
# A packet takes about 150 ms on the air, so give the sketch room to answer.
REPLY_TIMEOUT = 2.0
# Collect changes for this long before transmitting, so that dragging a slider
# or flipping two switches in a row results in one packet, not five.
COALESCE_MS = 600
# A transmit that failed is worth repeating - the state is already stored and
# shown, so giving up silently would leave the display lying about the fan.
RETRY_MS = 3000
MAX_RETRIES = 3
# Consecutive failures after which the process gives up and lets daemontools
# start it again, which re-runs the port search. A USB re-enumeration leaves
# the old file descriptor dead while the by-id path is still there, and that
# state is not recoverable from inside the process.
MAX_FAILURES = 4
# Re-send the current state every so often, in case the fan was operated with
# the hand held remote in the meantime. 0 disables it.
REFRESH_S = 900
# Wait before exiting when no transmitter was found. Long on purpose: every
# restart re-runs the port search, and probing a port belonging to another
# driver disturbs it briefly.
NO_PORT_WAIT = 60
# Module level state, 0x100 = connected.
STATE_CONNECTED, STATE_DISCONNECTED = 0x100, 0
# Channel status: 0x00 = off, 0x09 = on.
STATUS_OFF, STATUS_ON = 0x00, 0x09

MIN_C, MAX_C = -2, 37          # thermostat range the fan accepts
SPEEDS = tuple(range(10, 101, 10))

HERE = os.path.dirname(os.path.abspath(__file__))
HEX_FILE = os.path.join(HERE, "arduino", "maxxfan_tx.hex")
HEX_VERSION_FILE = HEX_FILE + ".ver"
sys.path.insert(0, os.path.join(HERE, "tools"))


def log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def c_to_f(degc):
    """Celsius to the Fahrenheit value the fan expects.

    Not a rounded conversion: the fan stores Fahrenheit and its remote displays
    Celsius, and the mapping the remote uses truncates towards zero. Checked
    against every setpoint that appears in the reference captures - a rounded
    conversion is off by one degree Fahrenheit on 18 of the 40 values in this
    range.
    """
    degc = max(MIN_C, min(MAX_C, int(degc)))
    return int(degc * 1.8) + 32


# key, default label, default type, allowed types, dimming (min, max, step, unit)
TOGGLE, MOMENTARY, DIMMER, SETPOINT, STEPPED, DROPDOWN, SLIDER = 1, 0, 2, 3, 4, 6, 7
OUTPUTS = (
    ("fan",       "Fan",           TOGGLE,    (TOGGLE,),                   None),
    ("speed",     "Speed",         SLIDER,    (DIMMER, SLIDER),            (10, 100, 10, "%")),
    ("direction", "Direction",     DROPDOWN,  (TOGGLE, DROPDOWN),          (0, 1, 1, None)),
    ("cover",     "Lid",           TOGGLE,    (TOGGLE,),                   None),
    ("mode",      "Mode",          DROPDOWN,  (TOGGLE, DROPDOWN),          (0, 1, 1, None)),
    ("setpoint",  "Auto setpoint", SETPOINT,  (SETPOINT, SLIDER),          (MIN_C, MAX_C, 1, None)),
    ("resend",    "Resend",        MOMENTARY, (MOMENTARY,),                None),
    ("beep",      "Beep",          MOMENTARY, (MOMENTARY,),                None),
    # The card sorts its elements by label, so a name starting with T puts this
    # one after "Speed" - bottom right, out of the way of the fan controls.
    ("update",    "Transmitter",   MOMENTARY, (MOMENTARY,),                None),
)
LABELS = {"direction": ["Intake", "Exhaust"], "mode": ["Manual", "Auto"]}
# Outputs whose value lives in /Dimming; a write to their /State means nothing.
VALUE_ONLY = ("speed", "setpoint")
# Buttons: they act on the press and pop back out.
BUTTONS = ("resend", "beep", "update")


def hex_version():
    """The version of the sketch shipped in this package, or None."""
    try:
        with open(HEX_VERSION_FILE) as fh:
            v = fh.read().strip()
        return v if v and os.path.exists(HEX_FILE) else None
    except Exception:
        return None


def tty_of(port):
    return os.path.basename(os.path.realpath(port))


def serial_starter(script, tty):
    """Hand a tty to serial-starter, or take it away from it.

    Only ever called for one specific port: releasing a port blindly would stop
    whatever Venus driver owns it, and nothing would put it back.
    """
    path = os.path.join(SERIAL_STARTER, script)
    if not os.access(path, os.X_OK):
        return
    try:
        subprocess.call([path, tty])
    except Exception as e:
        log("%s %s failed: %s" % (script, tty, e))


def find_ports():
    """Serial ports that could carry the Arduino, most likely first.

    Nano clones use a CH340, originals an FTDI, and a few boards a CP210x.
    These are chip names, not device names: Victron's own RS485-to-USB
    interface is an FTDI as well, so a match means "worth asking", never "this
    is ours". Nothing here is acted on until it has identified itself, and a
    port that does not answer is handed straight back to serial-starter.
    """
    hits = []
    for pattern in ("*1a86*", "*CH340*", "*ch341*", "*FTDI*", "*FT232*",
                    "*Arduino*", "*CP210*"):
        hits += sorted(glob.glob("/dev/serial/by-id/usb-" + pattern))
    seen, ordered = set(), []
    for h in hits:
        if h not in seen:            # an FTDI matches two of the patterns
            seen.add(h)
            ordered.append(h)
    return ordered


class Transmitter(object):
    """Serial link to the Arduino running maxxfan_tx."""

    def __init__(self, port):
        self.port = port
        try:
            self.ser = serial.Serial(port, BAUD, 8, "N", 1, timeout=0.5,
                                     exclusive=True)
        except TypeError:              # pyserial older than 3.3
            self.ser = serial.Serial(port, BAUD, 8, "N", 1, timeout=0.5)
        # Opening the port pulls DTR and resets the board. Do not touch DTR
        # again afterwards, or every command would reboot the Arduino.
        time.sleep(RESET_WAIT)
        self.ser.reset_input_buffer()
        self.firmware = self._identify()
        # "MAXXFAN <protocol> <version>"; sketches before 1.3 answer without a
        # version, and those are by definition older than anything we ship.
        parts = self.firmware.split()
        self.version = parts[2] if len(parts) > 2 else None

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _line(self, timeout):
        end = time.time() + timeout
        buf = b""
        while time.time() < end:
            chunk = self.ser.read(64)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                line = line.strip().decode("ascii", "replace")
                if line:               # skip a stray blank line
                    return line
        return ""

    def _command(self, text, timeout=REPLY_TIMEOUT):
        self.ser.reset_input_buffer()
        self.ser.write((text + "\n").encode("ascii"))
        self.ser.flush()
        return self._line(timeout)

    def _identify(self):
        for _ in range(3):
            answer = self._command("?", 1.0)
            if answer.startswith("MAXXFAN"):
                return answer
            time.sleep(0.3)
        raise IOError("no MaxxFan transmitter on this port")

    def send(self, on, speed, exhaust, cover, automode, degc, warn=False):
        cmd = "S %d %d %d %d %d %d %d" % (
            1 if on else 0, speed, 1 if exhaust else 0, 1 if cover else 0,
            1 if automode else 0, c_to_f(degc), 1 if warn else 0)
        answer = self._command(cmd)
        if not answer.startswith("OK"):
            raise IOError("transmitter answered %r to %r" % (answer, cmd))
        return answer[3:].strip()


def open_settings(bus, name):
    from settingsdevice import SettingsDevice
    prefix = "/Settings/Devices/%s/" % name
    entries = {
        "instance": [prefix + "ClassAndVrmInstance",
                     "%s:%d" % (SERVICE_CLASS, FALLBACK_INSTANCE), 0, 0],
        "port":      [prefix + "Port", "", 0, 0],
        "name":      [prefix + "CustomName", "MaxxFan", 0, 0],
        "fan":       [prefix + "Fan", 0, 0, 1],
        "speed":     [prefix + "Speed", 50, 10, 100],
        "direction": [prefix + "Direction", 1, 0, 1],
        "cover":     [prefix + "Cover", 1, 0, 1],
        "mode":      [prefix + "Mode", 0, 0, 1],
        "setpoint":  [prefix + "Setpoint", 21, MIN_C, MAX_C],
    }
    # One subtree per output. Keeping these below Outputs/ leaves the names
    # above free - a leaf and a group cannot share a name in the settings tree.
    for key, label, default_type, _valid, _dim in OUTPUTS:
        out = prefix + "Outputs/" + key + "/"
        entries["type_" + key] = [out + "Type", default_type, 0, 13]
        entries["name_" + key] = [out + "CustomName", label, 0, 0]
        entries["group_" + key] = [out + "Group", "MaxxFan", 0, 0]
        entries["show_" + key] = [out + "ShowUIControl", 1, 0, 7]
    return SettingsDevice(bus, entries, eventCallback=None, timeout=10)


def device_instance(bus, name):
    """The VRM instance, and the settings object everything else is kept in."""
    try:
        s = open_settings(bus, name)
    except Exception as e:
        # Without settings nothing is remembered and every user change is
        # silently dropped, so do not limp along - let daemontools try again.
        log("settings not available: %s" % e)
        return None, None
    instance = FALLBACK_INSTANCE
    try:
        stored = str(s["instance"])
        cls, _, num = stored.partition(":")
        if num.isdigit():
            instance = int(num)
        if cls != SERVICE_CLASS:
            log("device class migrated from %s to %s" % (cls, SERVICE_CLASS))
            s["instance"] = "%s:%d" % (SERVICE_CLASS, instance)
    except Exception as e:
        log("device instance not read (%s), using %d" % (e, instance))
    return instance, s


class Driver(object):
    def __init__(self, bus, instance, settings, tx, port):
        self.port = port
        self.tty = tty_of(port)
        self.tx = tx
        self.settings = settings
        self.state = self._load_state()
        self.pending = None            # GLib source id of the coalescing timer
        self.retries = 0
        self.failures = 0
        self.last_error = None

        svcname = "com.victronenergy.%s.maxxfan_%s" % (SERVICE_CLASS, self.tty)
        try:
            self.svc = VeDbusService(svcname, bus=bus, register=False)
            deferred = True
        except TypeError:              # older velib_python
            self.svc = VeDbusService(svcname, bus=bus)
            deferred = False

        s = self.svc
        s.add_path("/Mgmt/ProcessName", os.path.basename(__file__))
        s.add_path("/Mgmt/ProcessVersion", VERSION)
        # The connection row is the only free text field both GUI versions
        # render on the device page, so the driver version goes there.
        s.add_path("/Mgmt/Connection", "%s (dbus-maxxfan v%s)"
                   % (self.tty, VERSION))
        s.add_path("/DeviceInstance", instance)
        s.add_path("/ProductId", 0xFFFF)
        s.add_path("/ProductName", "MaxxFan")
        s.add_path("/CustomName", self._setting("name", "MaxxFan"),
                   writeable=True,
                   onchangecallback=lambda p, v: self._on_device_name(v))
        s.add_path("/FirmwareVersion", self.tx.version or "pre-1.3")
        s.add_path("/HardwareVersion", 0)
        s.add_path("/Serial", "maxxfan-ir")
        s.add_path("/Connected", 1)
        s.add_path("/State", STATE_CONNECTED)
        # Not a reading from the fan - infrared has no way back. This is the
        # state the driver last transmitted.
        s.add_path("/LastPacket", "", writeable=False)

        for key, label, default_type, valid, dim in OUTPUTS:
            self._add_output(key, label, default_type, valid, dim)

        if deferred:
            s.register()
        log("registered as %s, instance %d" % (svcname, instance))

        self._store("port", port)
        self._publish_versions()
        self._publish()
        # Deliberately silent at startup: re-transmitting here would move a fan
        # that somebody switched off by hand, every time the GX reboots.
        if REFRESH_S:
            GLib.timeout_add_seconds(REFRESH_S, self._refresh)

    # ------------------------------------------------------------- d-bus setup

    def _add_output(self, key, label, default_type, valid, dim):
        s, base = self.svc, "/SwitchableOutput/%s" % key
        s.add_path(base + "/Name", label)
        s.add_path(base + "/State", 0, writeable=True,
                   onchangecallback=lambda p, v, k=key: self._on_state(k, v))
        s.add_path(base + "/Status", STATUS_OFF)
        s.add_path(base + "/Settings/Type", self._setting("type_" + key, default_type),
                   writeable=True,
                   onchangecallback=lambda p, v, k=key: self._on_type(k, v))
        s.add_path(base + "/Settings/ValidTypes", sum(1 << t for t in valid))
        s.add_path(base + "/Settings/CustomName", self._setting("name_" + key, label),
                   writeable=True,
                   onchangecallback=lambda p, v, k=key: self._on_name(k, v))
        s.add_path(base + "/Settings/Group", self._setting("group_" + key, "MaxxFan"),
                   writeable=True,
                   onchangecallback=lambda p, v, k=key: self._on_group(k, v))
        s.add_path(base + "/Settings/ShowUIControl",
                   int(self._setting("show_" + key, 1)), writeable=True,
                   onchangecallback=lambda p, v, k=key: self._on_show(k, v))
        if dim is not None:
            low, high, step, unit = dim
            s.add_path(base + "/Dimming", low, writeable=True,
                       onchangecallback=lambda p, v, k=key: self._on_dimming(k, v))
            s.add_path(base + "/Settings/DimmingMin", low)
            s.add_path(base + "/Settings/DimmingMax", high)
            s.add_path(base + "/Settings/StepSize", step)
            s.add_path(base + "/Settings/Decimals", 0)
            if unit is not None:
                s.add_path(base + "/Settings/Unit", unit)
        if key in LABELS:
            s.add_path(base + "/Settings/Labels", LABELS[key])

    def _setting(self, key, default):
        if self.settings is None:
            return default
        try:
            return self.settings[key]
        except Exception:
            return default

    def _store(self, key, value):
        if self.settings is None:
            return
        try:
            self.settings[key] = value
        except Exception as e:
            log("setting %s not stored: %s" % (key, e))

    def _load_state(self):
        """Read the stored state back, and distrust it.

        Anything can write into the settings tree, and a speed that is not a
        multiple of ten is refused by the sketch - which would leave the driver
        unable to send anything at all until the user happened to move a slider.
        """
        def flag(key, default):
            return 1 if int(self._setting(key, default)) else 0
        try:
            speed = int(self._setting("speed", 50))
        except (TypeError, ValueError):
            speed = 50
        try:
            setpoint = int(self._setting("setpoint", 21))
        except (TypeError, ValueError):
            setpoint = 21
        return {"fan": flag("fan", 0),
                "speed": min(SPEEDS, key=lambda s: abs(s - speed)),
                "direction": flag("direction", 1),
                "cover": flag("cover", 1),
                "mode": flag("mode", 0),
                "setpoint": max(MIN_C, min(MAX_C, setpoint))}

    def _type_of(self, key):
        try:
            return int(self.svc["/SwitchableOutput/%s/Settings/Type" % key])
        except Exception:
            return -1

    # ------------------------------------------------------------- publishing

    def _publish(self):
        """Mirror the shadow state onto the switch paths."""
        s, st = self.svc, self.state
        for key in ("fan", "cover"):
            base = "/SwitchableOutput/%s" % key
            s[base + "/State"] = st[key]
            s[base + "/Status"] = STATUS_ON if st[key] else STATUS_OFF
        for key in ("direction", "mode"):
            base = "/SwitchableOutput/%s" % key
            # As a dropdown the value lives in /Dimming and /State only has to
            # be valid; as a toggle - which ValidTypes offers - /State is the
            # value, and forcing it to 1 would make the switch spring back.
            if self._type_of(key) == TOGGLE:
                s[base + "/State"] = st[key]
                s[base + "/Status"] = STATUS_ON if st[key] else STATUS_OFF
            else:
                s[base + "/State"] = 1
                s[base + "/Status"] = STATUS_ON
            s[base + "/Dimming"] = st[key]
        for key in VALUE_ONLY:
            base = "/SwitchableOutput/%s" % key
            s[base + "/State"] = 1
            s[base + "/Status"] = STATUS_ON
            s[base + "/Dimming"] = st[key]
        for key in BUTTONS:
            s["/SwitchableOutput/%s/Status" % key] = STATUS_OFF

    # --------------------------------------------------------------- handlers

    def _on_type(self, key, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False
        valid = dict((k, v) for k, _l, _d, v, _dim in OUTPUTS)[key]
        if value not in valid:
            log("type %d refused for %s - not in ValidTypes" % (value, key))
            return False
        self._store("type_" + key, value)
        # The type decides how /State is meant to be read, so republish.
        GLib.idle_add(self._publish_once)
        return True

    def _publish_once(self):
        self._publish()
        return False

    def _on_name(self, key, value):
        self._store("name_" + key, str(value))
        return True

    def _on_group(self, key, value):
        self._store("group_" + key, str(value))
        return True

    def _on_show(self, key, value):
        try:
            self._store("show_" + key, int(value))
        except (TypeError, ValueError):
            return False
        return True

    def _on_device_name(self, value):
        self._store("name", str(value))
        return True

    def _on_state(self, key, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return False
        if key in BUTTONS:
            # Momentary: act on the press, then let the button pop back out.
            if value:
                GLib.idle_add(self._momentary, key)
            return True
        if key in VALUE_ONLY:
            # These carry their value in /Dimming. Accepting a /State write
            # would store a number the driver then ignores.
            return False
        self.state[key] = 1 if value else 0
        self._store(key, self.state[key])
        self._schedule()
        return True

    def _on_dimming(self, key, value):
        try:
            value = int(round(float(value)))
        except (TypeError, ValueError):
            return False
        if key == "speed":
            if value not in SPEEDS:
                # The stepped switch (type 4) sends the number of the position
                # it sits on rather than the value, and ignores the range
                # published here - so every position arrives as a single digit.
                # Refusing makes vedbus keep the old value; returning True
                # would store the step number and show it as a percentage.
                log("speed %s refused - this control sends step numbers, not "
                    "percent. Set /SwitchableOutput/speed/Settings/Type to 7."
                    % value)
                return False
        elif key in ("direction", "mode"):
            if value not in (0, 1):
                return False
        elif key == "setpoint":
            if not MIN_C <= value <= MAX_C:
                return False
        self.state[key] = value
        self._store(key, value)
        self._schedule()
        return True

    def _momentary(self, key):
        try:
            if key == "update":
                self._update_transmitter()
            else:
                # Both other buttons transmit the shadow state. The sketch can
                # repeat its own last packet, but after a GX reboot it has none
                # - and that is exactly when somebody presses Resend.
                self._transmit(warn=(key == "beep"))
        except Exception as e:
            self._failed(e)
        self.svc["/SwitchableOutput/%s/State" % key] = 0
        return False

    # ------------------------------------------------------- transmitter fw

    def _update_label(self):
        have = self.tx.version or "pre-1.3"
        want = hex_version()
        if want is None:
            return "Transmitter %s" % have
        if have == want:
            return "Transmitter %s (up to date)" % have
        return "Transmitter %s (update to %s)" % (have, want)

    def _publish_versions(self):
        """Sketch version on the device page, and on the card as a label."""
        self.svc["/FirmwareVersion"] = self.tx.version or "pre-1.3"
        path = "/SwitchableOutput/update/Settings/CustomName"
        current = str(self.svc[path])
        # Leave a name the user chose alone; ours always starts like this.
        if current.startswith("Transmitter"):
            label = self._update_label()
            if current != label:
                self.svc[path] = label
                self._store("name_update", label)

    def _update_transmitter(self):
        """Flash the sketch that ships with this package onto the Arduino.

        Only ever from the button. This blocks for a few seconds; the fan does
        not care, infrared is one way and it keeps its own state meanwhile.
        """
        import flash
        want = hex_version()
        if want is None:
            log("no firmware shipped with this package, nothing to flash")
            return
        log("updating the transmitter from %s to %s"
            % (self.tx.version or "pre-1.3", want))
        self.svc["/Connected"] = 0
        self.tx.close()                # the programmer needs the port
        try:
            chip = flash.flash(self.port, HEX_FILE, log=log)
            log("flashed %s" % chip)
        finally:
            self.tx = Transmitter(self.port)
        log("transmitter now answers %r" % self.tx.firmware)
        if self.tx.version != want:
            log("WARNING: it reports %s, not %s - is the .hex out of date?"
                % (self.tx.version, want))
        self._publish_versions()
        self._ok()

    # ------------------------------------------------------------ transmitting

    def _schedule(self, delay=COALESCE_MS):
        if self.pending is not None:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(delay, self._fire)

    def _fire(self):
        self.pending = None
        try:
            self._transmit()
            self.retries = 0
        except Exception as e:
            self._failed(e)
            if self.retries < MAX_RETRIES:
                self.retries += 1
                log("retrying in %.1f s (%d of %d)"
                    % (RETRY_MS / 1000.0, self.retries, MAX_RETRIES))
                self._schedule(RETRY_MS)
            else:
                self.retries = 0
        return False

    def _refresh(self):
        try:
            self._transmit()
            log("periodic refresh sent")
        except Exception as e:
            self._failed(e)
        return True

    def _transmit(self, warn=False):
        st = self.state
        packet = self.tx.send(on=st["fan"], speed=st["speed"],
                              exhaust=st["direction"], cover=st["cover"],
                              automode=st["mode"], degc=st["setpoint"], warn=warn)
        self.svc["/LastPacket"] = packet
        self._publish()
        log("sent: fan %s, %d%%, %s, lid %s, %s, setpoint %d C -> %s"
            % ("on" if st["fan"] else "off", st["speed"],
               "exhaust" if st["direction"] else "intake",
               "open" if st["cover"] else "closed",
               "auto" if st["mode"] else "manual", st["setpoint"], packet))
        self._ok()

    def _ok(self):
        if self.last_error is not None:
            log("transmitter answering again")
            self.last_error = None
        self.failures = 0
        self.svc["/Connected"] = 1
        self.svc["/State"] = STATE_CONNECTED

    def _failed(self, exc):
        text = str(exc)
        if text != self.last_error:
            log("transmit failed: %s" % text)
            self.last_error = text
        self.failures += 1
        self.svc["/Connected"] = 0
        self.svc["/State"] = STATE_DISCONNECTED
        if not os.path.exists(self.port):
            self._give_up("port disappeared")
        elif tty_of(self.port) != self.tty:
            # Re-plugged: the by-id name is back but points somewhere else, and
            # the file descriptor we hold is dead.
            self._give_up("port moved to %s" % tty_of(self.port))
        elif self.failures >= MAX_FAILURES:
            self._give_up("%d failures in a row" % self.failures)

    def _give_up(self, why):
        log("%s - restarting the service" % why)
        self.tx.close()
        sys.exit(1)                    # daemontools starts us again


def probe(port):
    """Ask one port what is on it, leaving nothing behind if it is not ours.

    serial-starter has to let go of the port before the Arduino can be reached,
    and opening it resets the board anyway. A port that does not identify is
    handed straight back, so a foreign device loses its driver for a second
    rather than until the next reboot.
    """
    tty = tty_of(port)
    serial_starter("stop-tty.sh", tty)
    time.sleep(0.5)
    try:
        return Transmitter(port)
    except Exception as e:
        log("%s: %s" % (os.path.basename(port), e))
        serial_starter("start-tty.sh", tty)
        return None


def open_transmitter(argv, settings):
    """Find the transmitter, touching as few foreign ports as possible."""
    if len(argv) > 1:
        tx = probe(argv[1])
        return (tx, argv[1]) if tx else (None, None)

    known = ""
    if settings is not None:
        try:
            known = str(settings["port"])
        except Exception:
            known = ""
    order = []
    if known and os.path.exists(known):
        order.append(known)            # the one that answered last time
    order += [p for p in find_ports() if p != known]

    if not order:
        log("no candidate USB serial port under /dev/serial/by-id/")
        return None, None
    for port in order:
        tx = probe(port)
        if tx is not None:
            log("transmitter on %s answers %r" % (port, tx.firmware))
            return tx, port
        if port == known:
            tx = repair(port)          # our own port, sketch gone
            if tx is not None:
                return tx, port
    log("none of the %d candidate ports answered the identification" % len(order))
    return None, None


def repair(port):
    """Re-flash the port that used to be ours but has no working sketch.

    This is the one case where flashing happens without being asked: the port
    is the one that identified as MAXXFAN before, so there is no doubt whose
    board it is, and an interrupted update is the likely reason it went quiet.
    Anything else - an unknown port, an ambiguous one - is left alone, because
    a bootloader only says "an AVR lives here", not whose project it runs.
    """
    if hex_version() is None:
        return None
    try:
        import flash
    except ImportError as e:
        log("cannot flash: %s" % e)
        return None
    chip = flash.identify(port)
    if chip is None:
        return None
    log("%s has a %s bootloader but no working sketch - re-flashing"
        % (os.path.basename(port), chip))
    try:
        flash.flash(port, HEX_FILE, log=log)
    except Exception as e:
        log("re-flashing failed: %s" % e)
        return None
    try:
        tx = Transmitter(port)
    except Exception as e:
        log("still no answer after re-flashing: %s" % e)
        return None
    log("recovered, transmitter answers %r" % tx.firmware)
    return tx


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    log("dbus-maxxfan %s starting" % VERSION)
    bus = dbus.SystemBus()
    instance, settings = device_instance(bus, "maxxfan")
    if settings is None:
        time.sleep(10)
        sys.exit(1)
    tx, port = open_transmitter(sys.argv, settings)
    if tx is None:
        time.sleep(NO_PORT_WAIT)
        sys.exit(1)
    Driver(bus, instance, settings, tx, port)
    GLib.MainLoop().run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        log("aborted: %s" % exc)
        time.sleep(10)                 # do not hammer daemontools
        sys.exit(1)
