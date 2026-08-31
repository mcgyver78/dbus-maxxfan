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

VERSION = "1.1"
SERVICE_CLASS = "switch"
FALLBACK_INSTANCE = 41
BAUD = 115200
# The Arduino resets when the port is opened, then runs its bootloader.
RESET_WAIT = 2.0
# A packet takes about 150 ms on the air, so give the sketch room to answer.
REPLY_TIMEOUT = 2.0
# Collect changes for this long before transmitting, so that dragging a slider
# or flipping two switches in a row results in one packet, not five.
COALESCE_MS = 600
# Re-send the current state every so often, in case the fan was operated with
# the hand held remote in the meantime. 0 disables it.
REFRESH_S = 900
# Module level state, 0x100 = connected.
STATE_CONNECTED = 0x100
# Channel status: 0x00 = off, 0x09 = on.
STATUS_OFF, STATUS_ON = 0x00, 0x09

MIN_C, MAX_C = -2, 37          # thermostat range the fan accepts
SPEEDS = tuple(range(10, 101, 10))


def log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def c_to_f(degc):
    """Celsius to the Fahrenheit value the fan expects.

    Not a rounded conversion: the fan stores Fahrenheit and its remote displays
    Celsius, and the mapping the remote uses truncates towards zero. Verified
    against 34 setpoints captured from an original remote - a rounded
    conversion is off by one degree Fahrenheit on more than half of them.
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
)
LABELS = {"direction": ["Intake", "Exhaust"], "mode": ["Manual", "Auto"]}


def find_port():
    """Serial ports that could carry the Arduino, most likely first.

    Nano clones use a CH340, originals an FTDI, and a few boards a CP210x. The
    Victron cables on the same GX device have their own by-id names and are
    never matched here; anything that does match is still asked to identify
    itself before a single command is sent to it.
    """
    hits = []
    for pattern in ("*1a86*", "*CH340*", "*ch341*", "*FTDI*", "*FT232*",
                    "*Arduino*", "*CP210*"):
        hits += sorted(glob.glob("/dev/serial/by-id/usb-" + pattern))
    seen, ordered = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return ordered


class Transmitter(object):
    """Serial link to the Arduino running maxxfan_tx."""

    def __init__(self, port):
        self.port = port
        self.ser = serial.Serial(port, BAUD, 8, "N", 1, timeout=0.5)
        # Opening the port pulls DTR and resets the board. Do not touch DTR
        # again afterwards, or every command would reboot the Arduino.
        time.sleep(RESET_WAIT)
        self.ser.reset_input_buffer()
        self.firmware = self._identify()

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
            if chunk:
                buf += chunk
                if b"\n" in buf:
                    return buf.split(b"\n")[0].strip().decode("ascii", "replace")
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

    def resend(self):
        answer = self._command("R")
        if not answer.startswith("OK"):
            raise IOError("transmitter answered %r to a resend" % answer)
        return answer[3:].strip()


def open_settings(bus, name):
    from settingsdevice import SettingsDevice
    prefix = "/Settings/Devices/%s/" % name
    entries = {
        "instance": [prefix + "ClassAndVrmInstance",
                     "%s:%d" % (SERVICE_CLASS, FALLBACK_INSTANCE), 0, 0],
        "fan":       [prefix + "Fan", 0, 0, 1],
        "speed":     [prefix + "Speed", 50, 10, 100],
        "direction": [prefix + "Direction", 1, 0, 1],
        "cover":     [prefix + "Cover", 1, 0, 1],
        "mode":      [prefix + "Mode", 0, 0, 1],
        "setpoint":  [prefix + "Setpoint", 21, MIN_C, MAX_C],
        "group":     [prefix + "Group", "MaxxFan", 0, 0],
    }
    for key, label, default_type, _valid, _dim in OUTPUTS:
        entries["type_" + key] = [prefix + "Type/" + key, default_type, 0, 13]
        entries["name_" + key] = [prefix + "CustomName/" + key, label, 0, 0]
    return SettingsDevice(bus, entries, eventCallback=None, timeout=10)


def device_instance(bus, name):
    try:
        s = open_settings(bus, name)
        stored = str(s["instance"])
        cls, _, num = stored.partition(":")
        instance = int(num) if num.isdigit() else FALLBACK_INSTANCE
        if cls != SERVICE_CLASS:
            log("device class migrated from %s to %s" % (cls, SERVICE_CLASS))
            s["instance"] = "%s:%d" % (SERVICE_CLASS, instance)
        return instance, s
    except Exception as e:
        log("settings not available (%s), using instance %d - the fan state "
            "will then only be remembered until the next restart"
            % (e, FALLBACK_INSTANCE))
        return FALLBACK_INSTANCE, None


class Driver(object):
    def __init__(self, port):
        self.port = port
        self.tx = Transmitter(port)
        log("transmitter on %s answers %r" % (port, self.tx.firmware))

        bus = dbus.SystemBus()
        instance, self.settings = device_instance(bus, "maxxfan")
        self.state = self._load_state()
        self.pending = None            # GLib source id of the coalescing timer
        self.last_error = None

        svcname = "com.victronenergy.%s.maxxfan_%s" % (
            SERVICE_CLASS, os.path.basename(os.path.realpath(port)))
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
                   % (os.path.basename(os.path.realpath(port)), VERSION))
        s.add_path("/DeviceInstance", instance)
        s.add_path("/ProductId", 0xFFFF)
        s.add_path("/ProductName", "MaxxFan")
        s.add_path("/CustomName", "MaxxFan", writeable=True)
        s.add_path("/FirmwareVersion", self.tx.firmware)
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
        s.add_path(base + "/Settings/Group", self._setting("group", "MaxxFan"),
                   writeable=True)
        s.add_path(base + "/Settings/ShowUIControl", 1, writeable=True)
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
        return {"fan": int(self._setting("fan", 0)),
                "speed": int(self._setting("speed", 50)),
                "direction": int(self._setting("direction", 1)),
                "cover": int(self._setting("cover", 1)),
                "mode": int(self._setting("mode", 0)),
                "setpoint": int(self._setting("setpoint", 21))}

    # ------------------------------------------------------------- publishing

    def _publish(self):
        """Mirror the shadow state onto the switch paths."""
        s, st = self.svc, self.state
        s["/SwitchableOutput/fan/State"] = st["fan"]
        s["/SwitchableOutput/fan/Status"] = STATUS_ON if st["fan"] else STATUS_OFF
        for key in ("speed", "direction", "cover", "mode", "setpoint"):
            base = "/SwitchableOutput/%s" % key
            if key == "cover":
                s[base + "/State"] = st["cover"]
                s[base + "/Status"] = STATUS_ON if st["cover"] else STATUS_OFF
                continue
            # Sliders and dropdowns carry their value in /Dimming; /State must
            # still be valid or the GUI hides the control altogether.
            s[base + "/State"] = 1
            s[base + "/Status"] = STATUS_ON
            s[base + "/Dimming"] = st[key]
        for key in ("resend", "beep"):
            s["/SwitchableOutput/%s/Status" % key] = STATUS_OFF

    # --------------------------------------------------------------- handlers

    def _on_type(self, key, value):
        self._store("type_" + key, int(value))
        return True

    def _on_name(self, key, value):
        self._store("name_" + key, str(value))
        return True

    def _on_state(self, key, value):
        value = int(value)
        if key in ("resend", "beep"):
            # Momentary: act on the press, then let the button pop back out.
            if value:
                GLib.idle_add(self._momentary, key)
            return True
        if key == "fan":
            self.state["fan"] = 1 if value else 0
        elif key == "cover":
            self.state["cover"] = 1 if value else 0
        elif key in ("direction", "mode"):
            # Only meaningful when the user configured these as toggles.
            self.state[key] = 1 if value else 0
        else:
            return True                # speed and setpoint are set through /Dimming
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
                # published here - so every position arrives as a single digit
                # and would end up as the lowest speed. Refuse it instead of
                # sending the fan a speed the user did not ask for.
                log("speed %s ignored - this control sends step numbers, not "
                    "percent. Set /SwitchableOutput/speed/Settings/Type to 7."
                    % value)
                return True
        elif key in ("direction", "mode"):
            value = 1 if value else 0
        elif key == "setpoint":
            value = max(MIN_C, min(MAX_C, value))
        self.state[key] = value
        self._store(key, value)
        self._schedule()
        return True

    def _momentary(self, key):
        try:
            if key == "beep":
                self._transmit(warn=True)
            else:
                self.svc["/LastPacket"] = self.tx.resend()
                log("state re-sent on request")
                self._ok()
        except Exception as e:
            self._failed(e)
        self.svc["/SwitchableOutput/%s/State" % key] = 0
        return False

    # ------------------------------------------------------------ transmitting

    def _schedule(self):
        if self.pending is not None:
            GLib.source_remove(self.pending)
        self.pending = GLib.timeout_add(COALESCE_MS, self._fire)

    def _fire(self):
        self.pending = None
        try:
            self._transmit()
        except Exception as e:
            self._failed(e)
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
        self.svc["/Connected"] = 1
        self.svc["/State"] = STATE_CONNECTED

    def _failed(self, exc):
        text = str(exc)
        if text != self.last_error:
            log("transmit failed: %s" % text)
            self.last_error = text
        self.svc["/Connected"] = 0
        if not os.path.exists(self.port):
            log("port disappeared - restarting the service")
            sys.exit(1)                # daemontools starts us again


def open_transmitter(argv):
    if len(argv) > 1:
        return Driver(argv[1])
    ports = find_port()
    if not ports:
        log("no candidate USB serial port under /dev/serial/by-id/")
        return None
    for port in ports:
        try:
            return Driver(port)
        except IOError as e:
            log("%s: %s" % (os.path.basename(port), e))
        except serial.SerialException as e:
            log("%s: %s" % (os.path.basename(port), e))
    log("none of the %d candidate ports answered the identification" % len(ports))
    return None


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    log("dbus-maxxfan %s starting" % VERSION)
    driver = open_transmitter(sys.argv)
    if driver is None:
        time.sleep(10)
        sys.exit(1)
    GLib.MainLoop().run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("aborted: %s" % exc)
        time.sleep(10)                 # do not hammer daemontools
        sys.exit(1)
