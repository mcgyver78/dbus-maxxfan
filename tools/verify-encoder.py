#!/usr/bin/env python3
"""Verify the MaxxFan packet encoder against real remote captures.

Reads the Flipper Zero raw captures from skypeachblue/maxxfan-reversing,
decodes each one into its state/speed/temperature fields, re-encodes it
with our own encoder and checks that the resulting pulse train matches
the recording.
"""
import sys

PREAMBLE = [0x5A, 0xA5, 0x80, 0x7F, 0x40, 0xBF, 0x20, 0xDF, 0x10, 0xCC]
BIT_US = 834          # measured symbol period of the original remote


def build_packet(fan_on, speed, exhaust, cover_open, auto_mode, temp_f,
                 special=None, warn=False):
    if special is None:
        special = auto_mode or (fan_on and not cover_open)
    state = ((0x01 if fan_on else 0) | (0x02 if special else 0) |
             (0x04 if exhaust else 0) | (0x08 if cover_open else 0) |
             (0x10 if auto_mode else 0) | (0x20 if warn else 0))
    p = list(PREAMBLE) + [state, speed, temp_f, 0xFF, 0x23]
    p.append(p[10] ^ p[11] ^ p[12] ^ p[13] ^ p[14])
    return p


def encode(packet):
    """Return the merged pulse train: [mark, space, mark, space, ...] in us."""
    symbols = []                       # True = mark (carrier on), False = space
    for byte in packet:
        symbols.append(True)           # start bit is a zero -> mark
        for j in range(8):             # data bits, least significant first
            symbols.append(not (byte >> j) & 1)
        symbols.extend((False, False))  # two stop bits
    symbols.extend([False] * 8)        # end of transmission
    pulses, run = [], 0
    current = symbols[0]
    for s in symbols:
        if s == current:
            run += 1
        else:
            pulses.append(run * BIT_US)
            current, run = s, 1
    pulses.append(run * BIT_US)
    return pulses


def bit_period(pulses):
    """Estimate the symbol period of a recording, in microseconds."""
    best = None
    for t in (x / 10.0 for x in range(7000, 9500)):
        err = sum(abs(d - round(d / t) * t) for d in pulses)
        if best is None or err < best[1]:
            best = (t, err)
    return best[0]


def decode(pulses, period=None):
    """Turn a recorded pulse train back into 16 bytes, or None."""
    period = period or BIT_US
    bits = []
    for i, dur in enumerate(pulses):
        n = int(round(dur / period))
        if n < 1:
            return None
        bits.extend([i % 2 == 0] * n)   # even index = mark = zero bit
    # Recordings stop at the last mark, so the trailing spaces of the final
    # byte may be missing. Pad them back.
    if len(bits) < 176:
        bits.extend([False] * (176 - len(bits)))
    packet, pos = [], 0
    for _ in range(16):
        if pos + 11 > len(bits) or not bits[pos]:
            return None                 # missing start bit
        byte = 0
        for j in range(8):
            if not bits[pos + 1 + j]:
                byte |= 1 << j
        if bits[pos + 9] or bits[pos + 10]:
            return None                 # stop bits must be spaces
        packet.append(byte)
        pos += 11
    return packet


def describe(p):
    state, speed, temp_f = p[10], p[11], p[12]
    chk = p[10] ^ p[11] ^ p[12] ^ p[13] ^ p[14]
    return {
        "fan_on": bool(state & 0x01), "special": bool(state & 0x02),
        "exhaust": bool(state & 0x04), "cover_open": bool(state & 0x08),
        "auto_mode": bool(state & 0x10), "warn": bool(state & 0x20),
        "speed": speed, "temp_f": temp_f,
        "temp_c": round((temp_f - 32) / 1.8, 1),
        "preamble_ok": p[:10] == PREAMBLE,
        "const_ok": p[13] == 0xFF and p[14] == 0x23,
        "checksum_ok": chk == p[15],
    }


def read_collection(path):
    signals, name = [], None
    for line in open(path):
        line = line.strip()
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            data = [int(x) for x in line.split(":", 1)[1].split()]
            signals.append((name, data))
    return signals


def main(path):
    signals = read_collection(path)
    ok = bad = 0
    seen = []
    for name, data in signals:
        period = bit_period(data)
        p = decode(data, period)
        if p is None:
            print("%-16s could not be decoded" % name)
            bad += 1
            continue
        d = describe(p)
        if not (d["preamble_ok"] and d["const_ok"] and d["checksum_ok"]):
            print("%-16s decoded but failed a structural check: %s" % (name, d))
            bad += 1
            continue
        # re-encode from the decoded fields and compare against the recording
        again = build_packet(d["fan_on"], d["speed"], d["exhaust"],
                             d["cover_open"], d["auto_mode"], d["temp_f"],
                             special=d["special"], warn=d["warn"])
        if again != p:
            print("%-16s re-encoded packet differs: %s vs %s" % (name, again, p))
            bad += 1
            continue
        # compare the symbol-run pattern, which is what actually carries the
        # information; the recordings run at ~834 us per symbol, not 800
        ours = [round(x / BIT_US) for x in encode(again)]
        theirs = [round(x / period) for x in data]
        if ours[:len(theirs)] != theirs:
            print("%-16s symbol runs differ\n  ours   %s\n  theirs %s"
                  % (name, ours[:len(theirs)], theirs))
            bad += 1
            continue
        ok += 1
        seen.append((name, d, period))
    print("\n%d of %d recorded signals round-tripped cleanly\n" % (ok, ok + bad))
    print("%-16s %-4s %-5s %-4s %-6s %-5s %-7s %s" %
          ("signal", "fan", "speed", "dir", "cover", "auto", "temp", "symbol"))
    for name, d, period in seen:
        print("%-16s %-4s %-5d %-4s %-6s %-5s %-7s %.0f us" % (
            name, "on" if d["fan_on"] else "off", d["speed"],
            "out" if d["exhaust"] else "in",
            "open" if d["cover_open"] else "closed",
            "auto" if d["auto_mode"] else "man",
            "%.1f C" % d["temp_c"], period))


if __name__ == "__main__":
    main(sys.argv[1])
