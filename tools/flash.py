#!/usr/bin/env python3
"""
flash.py - write an Intel HEX file to an Arduino over its own bootloader.

Venus OS has no avrdude, and it does not need one: the bootloader that the
Arduino IDE talks to speaks STK500v1, which is a handful of framed commands.
This module implements just enough of it to program an ATmega328P, using
pyserial, which Venus OS ships.

The bootloader lives in a protected section of flash and cannot overwrite
itself, so an interrupted write leaves a broken sketch and a working
bootloader - never a dead board. Retrying is always allowed.

Standalone:

    python3 flash.py /dev/serial/by-id/usb-FTDI_... maxxfan_tx.hex
"""
import sys
import time

import serial

# STK500v1, as spoken by optiboot and ATmegaBOOT.
RESP_OK, RESP_INSYNC = 0x10, 0x14
CRC_EOP = 0x20
CMD_GET_SYNC = 0x30
CMD_ENTER_PROGMODE = 0x50
CMD_LEAVE_PROGMODE = 0x51
CMD_LOAD_ADDRESS = 0x55
CMD_PROG_PAGE = 0x64
CMD_READ_PAGE = 0x74
CMD_READ_SIGN = 0x75

PAGE_SIZE = 128                # ATmega328P flash page, in bytes
SIGNATURES = {b"\x1e\x95\x0f": "ATmega328P",
              b"\x1e\x95\x14": "ATmega328",
              b"\x1e\x94\x0b": "ATmega168P",
              b"\x1e\x94\x06": "ATmega168"}
# Optiboot runs at the sketch's baud rate, the older ATmegaBOOT at 57600.
BAUDS = (115200, 57600)
# The bootloader listens for about a second after a reset, then hands over.
SYNC_WINDOW = 2.0


class FlashError(Exception):
    pass


def parse_hex(path):
    """Intel HEX to a flat image starting at address zero."""
    image = bytearray()
    base = 0
    with open(path) as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            if not raw.startswith(":"):
                raise FlashError("%s:%d is not Intel HEX" % (path, lineno))
            try:
                rec = bytes.fromhex(raw[1:])
            except ValueError:
                raise FlashError("%s:%d has a bad hex digit" % (path, lineno))
            count, addr, kind = rec[0], (rec[1] << 8) | rec[2], rec[3]
            data = rec[4:4 + count]
            if len(rec) != count + 5:
                raise FlashError("%s:%d has a wrong length" % (path, lineno))
            if (sum(rec) & 0xFF) != 0:
                raise FlashError("%s:%d fails its checksum" % (path, lineno))
            if kind == 0x00:
                at = base + addr
                if at + count > len(image):
                    image.extend(b"\xff" * (at + count - len(image)))
                image[at:at + count] = data
            elif kind == 0x01:
                break
            elif kind == 0x04:
                base = ((data[0] << 8) | data[1]) << 16
            elif kind == 0x02:
                base = ((data[0] << 8) | data[1]) << 4
            # 03 and 05 are start-address records; nothing to do with them
    if not image:
        raise FlashError("%s contains no data" % path)
    return bytes(image)


class Programmer(object):
    def __init__(self, port, baud, log=None):
        self.port = port
        self.baud = baud
        self.log = log or (lambda _m: None)
        self.ser = None

    # ------------------------------------------------------------- framing

    def _command(self, body, expect=0):
        self.ser.reset_input_buffer()
        self.ser.write(bytes(body) + bytes([CRC_EOP]))
        self.ser.flush()
        head = self.ser.read(1)
        if head != bytes([RESP_INSYNC]):
            raise FlashError("expected INSYNC, got %r" % head)
        payload = self.ser.read(expect) if expect else b""
        if len(payload) != expect:
            raise FlashError("short answer: %d of %d bytes"
                             % (len(payload), expect))
        tail = self.ser.read(1)
        if tail != bytes([RESP_OK]):
            raise FlashError("expected OK, got %r" % tail)
        return payload

    # --------------------------------------------------------- connecting

    def connect(self):
        """Reset the board and catch the bootloader before it hands over.

        The reset comes from opening the port, which is the same thing the
        Arduino IDE relies on and the same reason the sketch prints its banner
        when a serial monitor attaches. No DTR poking of our own, so there is
        no polarity to get wrong.
        """
        self.ser = serial.Serial(self.port, self.baud, timeout=0.3)
        deadline = time.time() + SYNC_WINDOW
        last = None
        while time.time() < deadline:
            try:
                self._command([CMD_GET_SYNC])
                return True
            except FlashError as e:
                last = e
                time.sleep(0.05)
        self.close()
        raise FlashError("no bootloader answered at %d baud (%s)"
                         % (self.baud, last))

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def signature(self):
        return bytes(self._command([CMD_READ_SIGN], expect=3))

    # --------------------------------------------------------- programming

    def _load_address(self, byte_addr):
        word = byte_addr >> 1               # the bootloader counts in words
        self._command([CMD_LOAD_ADDRESS, word & 0xFF, (word >> 8) & 0xFF])

    def write(self, image):
        pages = (len(image) + PAGE_SIZE - 1) // PAGE_SIZE
        for i in range(pages):
            chunk = image[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
            chunk += b"\xff" * (PAGE_SIZE - len(chunk))
            self._load_address(i * PAGE_SIZE)
            self._command([CMD_PROG_PAGE, (PAGE_SIZE >> 8) & 0xFF,
                           PAGE_SIZE & 0xFF, ord("F")] + list(chunk))
        return pages

    def verify(self, image):
        pages = (len(image) + PAGE_SIZE - 1) // PAGE_SIZE
        for i in range(pages):
            want = image[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
            want += b"\xff" * (PAGE_SIZE - len(want))
            self._load_address(i * PAGE_SIZE)
            got = self._command([CMD_READ_PAGE, (PAGE_SIZE >> 8) & 0xFF,
                                 PAGE_SIZE & 0xFF, ord("F")], expect=PAGE_SIZE)
            if bytes(got) != bytes(want):
                raise FlashError("page %d of %d reads back different"
                                 % (i + 1, pages))
        return pages

    def enter(self):
        self._command([CMD_ENTER_PROGMODE])

    def leave(self):
        self._command([CMD_LEAVE_PROGMODE])


def flash(port, hexfile, log=print):
    """Program hexfile onto the board on port. Returns the chip name."""
    image = parse_hex(hexfile)
    log("flashing %d bytes from %s" % (len(image), hexfile))
    last = None
    for baud in BAUDS:
        prog = Programmer(port, baud, log)
        try:
            prog.connect()
        except (FlashError, serial.SerialException) as e:
            last = e
            prog.close()
            continue
        try:
            sig = prog.signature()
            chip = SIGNATURES.get(sig)
            if chip is None:
                raise FlashError("unknown signature %s - not a supported AVR"
                                 % sig.hex())
            log("bootloader at %d baud, %s" % (baud, chip))
            prog.enter()
            log("wrote %d pages" % prog.write(image))
            log("verified %d pages" % prog.verify(image))
            prog.leave()
            return chip
        finally:
            prog.close()
    raise FlashError("no bootloader on %s (%s)" % (port, last))


def identify(port, log=None):
    """Chip name if a bootloader answers, else None. Does not program."""
    for baud in BAUDS:
        prog = Programmer(port, baud, log)
        try:
            prog.connect()
            return SIGNATURES.get(prog.signature())
        except (FlashError, serial.SerialException):
            continue
        finally:
            prog.close()
    return None


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    try:
        print("done: %s" % flash(sys.argv[1], sys.argv[2]))
    except FlashError as exc:
        print("failed: %s" % exc)
        sys.exit(1)
