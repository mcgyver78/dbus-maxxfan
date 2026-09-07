#!/usr/bin/env python3
"""Exercises the driver's D-Bus behaviour without a GX device.

Fakes VeDbusService with the same accept/reject semantics as the real
vedbus.VeDbusItemExport.SetValue (callback returns True -> the value is stored
and signalled; False -> the old value is kept), a SettingsDevice, and enough of
GLib to drive the timers by hand.
"""
import importlib.util
import os
import sys
import types

# tools/ lives inside the package
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- fakes ---------------------------------------------------------------
timers = []          # (delay, callback) in the order they were armed
started, stopped = [], []


class FakeGLib(object):
    @staticmethod
    def timeout_add(delay, cb):
        timers.append([delay, cb, True])
        return len(timers) - 1

    @staticmethod
    def timeout_add_seconds(delay, cb):
        return FakeGLib.timeout_add(delay * 1000, cb)

    @staticmethod
    def idle_add(cb, *args):
        timers.append([0, lambda: cb(*args), True])
        return len(timers) - 1

    @staticmethod
    def source_remove(sid):
        timers[sid][2] = False


def run_timers(limit=20):
    """Fire every armed timer once, oldest first."""
    for _ in range(limit):
        for t in timers:
            if t[2]:
                t[2] = False
                t[1]()
                break
        else:
            return


class FakeService(dict):
    """dict of path -> value, plus the callback contract of vedbus."""

    def __init__(self, name, bus=None, register=None):
        dict.__init__(self)
        self.name = name
        self.callbacks = {}
        self.writeable = set()
        self.registered = False

    def add_path(self, path, value, description="", writeable=False,
                 onchangecallback=None, **kw):
        self[path] = value
        if writeable:
            self.writeable.add(path)
        if onchangecallback is not None:
            self.callbacks[path] = onchangecallback

    def register(self):
        self.registered = True

    def set_value(self, path, value):
        """What a remote SetValue does. Returns the D-Bus return code."""
        if path not in self.writeable:
            return 1
        if self.get(path) == value:
            return 0
        cb = self.callbacks.get(path)
        if cb is None or cb(path, value):
            self[path] = value          # vedbus.py:592 local_set_value
            return 0
        return 2                        # NOT OK, old value kept


class FakeSettings(dict):
    pass


for name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi",
             "gi.repository", "vedbus", "settingsdevice"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["dbus"].SystemBus = lambda *a, **k: None
sys.modules["dbus"].mainloop = sys.modules["dbus.mainloop"]
sys.modules["dbus.mainloop"].glib = sys.modules["dbus.mainloop.glib"]
sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda **k: None
sys.modules["gi.repository"].GLib = FakeGLib
sys.modules["vedbus"].VeDbusService = FakeService
sys.modules["settingsdevice"].SettingsDevice = FakeSettings

spec = importlib.util.spec_from_file_location(
    "maxxfan", os.path.join(REPO, "dbus-maxxfan.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.GLib = FakeGLib
mod.REFRESH_S = 0        # fire only the timers a test arms itself


class FakeTx(object):
    def __init__(self):
        self.firmware = "MAXXFAN 1"
        self.sent = []
        self.fail = False
        self.closed = False

    def send(self, **kw):
        if self.fail:
            raise IOError("simulated link failure")
        self.sent.append(kw)
        return "5AA5" + "00" * 14

    def close(self):
        self.closed = True


failures = []


def check(label, got, want):
    if got != want:
        failures.append(label)
        print("FAIL %-52s got %r want %r" % (label, got, want))
    else:
        print("ok   %-52s %r" % (label, got))


def build(**stored):
    del timers[:]
    settings = FakeSettings()
    settings.update({"instance": "switch:41", "port": "/dev/null",
                     "name": "MaxxFan", "fan": 0, "speed": 50, "direction": 1,
                     "cover": 1, "mode": 0, "setpoint": 21})
    for key, label, dt, _v, _d in mod.OUTPUTS:
        settings["type_" + key] = dt
        settings["name_" + key] = label
        settings["group_" + key] = "MaxxFan"
        settings["show_" + key] = 1
    settings.update(stored)
    tx = FakeTx()
    drv = mod.Driver(None, 41, settings, tx, "/dev/null")
    return drv, tx, settings


# ---- 1. the speed guard now refuses -------------------------------------
drv, tx, _ = build()
rc = drv.svc.set_value("/SwitchableOutput/speed/Dimming", 3)
check("stepped-switch value is rejected (rc 2 = NOT OK)", rc, 2)
check("  and /Dimming keeps the old value",
      drv.svc["/SwitchableOutput/speed/Dimming"], 50)
check("  and the shadow state is untouched", drv.state["speed"], 50)
check("  and nothing was transmitted", len(tx.sent), 0)

check("a real percentage is accepted",
      drv.svc.set_value("/SwitchableOutput/speed/Dimming", 70), 0)
run_timers()
check("  and reaches the fan", tx.sent[-1]["speed"], 70)

# ---- 2. /State writes on value-only outputs are refused ------------------
drv, tx, _ = build()
check("a /State write on speed is refused",
      drv.svc.set_value("/SwitchableOutput/speed/State", 0), 2)
check("  and /State stays 1", drv.svc["/SwitchableOutput/speed/State"], 1)

# ---- 3. types outside ValidTypes are refused -----------------------------
drv, tx, _ = build()
check("stepped switch is refused for speed",
      drv.svc.set_value("/SwitchableOutput/speed/Settings/Type", 4), 2)
check("dimmer is accepted for speed",
      drv.svc.set_value("/SwitchableOutput/speed/Settings/Type", 2), 0)

# ---- 4. a stored speed that the fan cannot do is snapped -----------------
drv, tx, _ = build(speed=55)
check("a stored 55 % is snapped to a value the fan accepts",
      drv.state["speed"] in mod.SPEEDS, True)
drv, tx, _ = build(setpoint=99)
check("a stored setpoint out of range is clamped", drv.state["setpoint"], 37)

# ---- 5. direction as a toggle keeps its off position --------------------
drv, tx, _ = build(type_direction=mod.TOGGLE)
check("toggle write accepted",
      drv.svc.set_value("/SwitchableOutput/direction/State", 0), 0)
run_timers()
check("  intake was transmitted", tx.sent[-1]["exhaust"], 0)
check("  and the toggle does not spring back",
      drv.svc["/SwitchableOutput/direction/State"], 0)

drv, tx, _ = build()          # same output as a dropdown
drv.svc.set_value("/SwitchableOutput/direction/Dimming", 0)
run_timers()
check("as a dropdown /State stays valid so the control is drawn",
      drv.svc["/SwitchableOutput/direction/State"], 1)

# ---- 6. a failed transmit is retried, then the service gives up ----------
drv, tx, _ = build()
tx.fail = True
drv.svc.set_value("/SwitchableOutput/fan/State", 1)
run_timers(limit=1)                    # the coalescing timer only
check("a failed transmit marks the device disconnected",
      drv.svc["/Connected"], 0)
check("  and /State no longer claims Connected", drv.svc["/State"], 0)
check("  and a retry is armed", drv.retries, 1)
tx.fail = False
run_timers(limit=1)                    # the retry
check("  the retry gets the command through", tx.sent[-1]["on"], 1)
check("  and the failure counter is cleared", drv.failures, 0)

drv, tx, _ = build()
tx.fail = True
exited = []
try:
    for _ in range(mod.MAX_FAILURES + 2):
        try:
            drv._transmit()
        except Exception as e:
            drv._failed(e)
except SystemExit as e:
    exited.append(e.code)
check("a wedged link makes the service exit for a restart", exited, [1])
check("  and the port is closed on the way out", tx.closed, True)

# ---- 7. both buttons transmit the shadow state --------------------------
drv, tx, _ = build()
drv.svc.set_value("/SwitchableOutput/resend/State", 1)
run_timers()
check("Resend transmits even when the sketch has no packet yet",
      len(tx.sent), 1)
check("  without the warn bit", tx.sent[-1]["warn"], False)
check("  and the button pops back out",
      drv.svc["/SwitchableOutput/resend/State"], 0)
drv.svc.set_value("/SwitchableOutput/beep/State", 1)
run_timers()
check("Beep sets the warn bit", tx.sent[-1]["warn"], True)

# ---- 8. a port that does not answer is handed back to serial-starter ----
calls = []
mod.serial_starter = lambda script, tty: calls.append((script, tty))


class Boom(object):
    def __init__(self, port):
        raise OSError("nothing there")


mod.Transmitter = Boom
mod.time.sleep = lambda _s: None
check("a foreign port is probed and released", mod.probe("/dev/null"), None)
check("  stop-tty first, then start-tty",
      [c[0] for c in calls], ["stop-tty.sh", "start-tty.sh"])

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all driver logic checks passed")
