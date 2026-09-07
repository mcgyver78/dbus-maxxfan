#!/usr/bin/env python3
"""Drives tools/flash.py against a simulated STK500v1 bootloader on a pty.

The simulator answers like optiboot: framed commands, INSYNC/OK, a page
buffer it programs into a flash image, and a signature. It also models the
part that actually bites - the bootloader only listens for a short while
after the port is opened.
"""
import importlib.util
import os
import pty
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "flash", os.path.join(os.path.dirname(os.path.abspath(__file__)), "flash.py"))
fl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fl)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(label)
        print("FAIL %-50s got %r want %r" % (label, got, want))
    else:
        print("ok   %-50s %r" % (label, got))


class Bootloader(threading.Thread):
    daemon = True

    def __init__(self, fd, signature=b"\x1e\x95\x0f", window=None,
                 flaky_pages=()):
        super().__init__()
        self.fd = fd
        self.signature = signature
        self.window = window          # seconds it answers, None = forever
        self.flaky_pages = set(flaky_pages)
        self.flash = bytearray(b"\xff" * 32768)
        self.addr = 0
        self.page_writes = 0
        self.started = time.time()
        self.in_progmode = False

    # -- framing ----------------------------------------------------------
    def reply(self, payload=b""):
        os.write(self.fd, bytes([fl.RESP_INSYNC]) + payload +
                 bytes([fl.RESP_OK]))

    def run(self):
        buf = b""
        while True:
            try:
                buf += os.read(self.fd, 256)
            except OSError:
                return
            while buf:
                used = self.handle(buf)
                if used == 0:
                    break
                buf = buf[used:]

    def handle(self, buf):
        """Returns how many bytes of buf formed a complete command."""
        if self.window is not None and time.time() - self.started > self.window:
            return len(buf)           # the bootloader has handed over
        cmd = buf[0]
        if cmd in (fl.CMD_GET_SYNC, fl.CMD_READ_SIGN, fl.CMD_ENTER_PROGMODE,
                   fl.CMD_LEAVE_PROGMODE):
            if len(buf) < 2:
                return 0
            if cmd == fl.CMD_READ_SIGN:
                self.reply(self.signature)
            else:
                if cmd == fl.CMD_ENTER_PROGMODE:
                    self.in_progmode = True
                self.reply()
            return 2
        if cmd == fl.CMD_LOAD_ADDRESS:
            if len(buf) < 4:
                return 0
            self.addr = ((buf[2] << 8) | buf[1]) * 2
            self.reply()
            return 4
        if cmd in (fl.CMD_PROG_PAGE, fl.CMD_READ_PAGE):
            if len(buf) < 5:
                return 0
            size = (buf[1] << 8) | buf[2]
            if cmd == fl.CMD_READ_PAGE:
                if len(buf) < 5:
                    return 0
                self.reply(bytes(self.flash[self.addr:self.addr + size]))
                return 5
            if len(buf) < 5 + size:
                return 0
            page = self.addr // size
            data = buf[4:4 + size]
            if page in self.flaky_pages:
                data = bytes([b ^ 0xFF for b in data])   # write it wrong
            self.flash[self.addr:self.addr + size] = data
            self.page_writes += 1
            self.reply()
            return 5 + size
        os.write(self.fd, b"\x15")    # NOSYNC
        return 1


def start(**kw):
    master, slave = pty.openpty()
    bl = Bootloader(master, **kw)
    bl.start()
    return bl, os.ttyname(slave)


# ---- 1. Intel HEX parsing -------------------------------------------------
def hexfile(path, chunks):
    """chunks: list of (address, bytes)."""
    with open(path, "w") as fh:
        for addr, data in chunks:
            for off in range(0, len(data), 16):
                part = data[off:off + 16]
                a = addr + off
                rec = [len(part), (a >> 8) & 0xFF, a & 0xFF, 0] + list(part)
                rec.append((-sum(rec)) & 0xFF)
                fh.write(":" + bytes(rec).hex().upper() + "\n")
        fh.write(":00000001FF\n")


hexfile("/tmp/t.hex", [(0, bytes.fromhex("0C9434000C943E000C943E000C943E00")),
                       (16, bytes.fromhex("DEADBEEF"))])
image = fl.parse_hex("/tmp/t.hex")
check("hex parses to a flat image", len(image), 20)
check("  data at 0x10 is placed", image[16:20].hex(), "deadbeef")

open("/tmp/bad.hex", "w").write(":100000000C9434000C943E000C943E000C943E0000\n")
try:
    fl.parse_hex("/tmp/bad.hex")
    check("a wrong checksum is caught", "no exception", "FlashError")
except fl.FlashError:
    check("a wrong checksum is caught", "FlashError", "FlashError")

# the real sketch, if it has been built
REAL = os.path.join(REPO, "arduino", "maxxfan_tx.hex")
if os.path.exists(REAL):
    real = fl.parse_hex(REAL)
    check("the shipped sketch parses", 3000 < len(real) < 30720, True)

# ---- 2. a normal flash ----------------------------------------------------
bl, port = start()
chip = fl.flash(port, "/tmp/t.hex", log=lambda m: None)
check("flashing reports the chip", chip, "ATmega328P")
check("  one page was written", bl.page_writes, 1)
check("  and the image is in flash", bytes(bl.flash[:20]), image)
check("  padded to the page with 0xff", bl.flash[20], 0xFF)

# ---- 3. verification actually verifies ------------------------------------
bl, port = start(flaky_pages=(0,))
try:
    fl.flash(port, "/tmp/t.hex", log=lambda m: None)
    check("a page written wrong is caught", "no exception", "FlashError")
except fl.FlashError as e:
    check("a page written wrong is caught", "reads back different" in str(e), True)

# ---- 4. wrong chip is refused ---------------------------------------------
bl, port = start(signature=b"\x1e\xa8\x02")
try:
    fl.flash(port, "/tmp/t.hex", log=lambda m: None)
    check("an unknown signature is refused", "no exception", "FlashError")
except fl.FlashError as e:
    check("an unknown signature is refused", "unknown signature" in str(e), True)

# ---- 5. no bootloader at all ----------------------------------------------
master, slave = pty.openpty()          # nothing answering
try:
    fl.flash(os.ttyname(slave), "/tmp/t.hex", log=lambda m: None)
    check("a silent port is refused", "no exception", "FlashError")
except fl.FlashError as e:
    check("a silent port is refused", "no bootloader" in str(e), True)
os.close(master)

# ---- 6. identify() does not program ---------------------------------------
bl, port = start()
check("identify names the chip", fl.identify(port), "ATmega328P")
check("  and writes nothing", bl.page_writes, 0)

master, slave = pty.openpty()
check("identify says nothing about a silent port",
      fl.identify(os.ttyname(slave)), None)
os.close(master)

# ---- 7. a bootloader that has already handed over -------------------------
bl, port = start(window=0.0)
try:
    fl.flash(port, "/tmp/t.hex", log=lambda m: None)
    check("a closed sync window is reported", "no exception", "FlashError")
except fl.FlashError as e:
    check("a closed sync window is reported", "no bootloader" in str(e), True)

# ---- 8. a multi-page image round-trips ------------------------------------
big = bytes(range(256)) * 12           # 3072 bytes, 24 pages
hexfile("/tmp/big.hex", [(0, big)])
bl, port = start()
fl.flash(port, "/tmp/big.hex", log=lambda m: None)
check("a 3 kB image writes 24 pages", bl.page_writes, 24)
check("  and matches byte for byte",
      __import__("hashlib").sha256(bytes(bl.flash[:len(big)])).hexdigest()[:16],
      __import__("hashlib").sha256(big).hexdigest()[:16])

print()
if failures:
    print("%d check(s) failed" % len(failures))
    sys.exit(1)
print("all flash checks passed")
