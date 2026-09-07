# Sketch tests without hardware

`lines_main.cpp` replaces the Arduino runtime with a stub that feeds the sketch
a scripted serial stream and captures every reply, so the line assembly, the
number parsing and the identify string can be checked on any machine.

```bash
g++ -std=c++17 -D__AVR_ATmega328P__ -I. -include Arduino.h \
    -x c++ ../../arduino/maxxfan_tx/maxxfan_tx.ino lines_main.cpp \
    -o /tmp/test-sketch -Wall -Wextra && /tmp/test-sketch
```

It does not test timing - the stub's `micros()` runs fast so the transmit loop
returns at once. Timing is verified against the recorded remote signals by
`../verify-encoder.py`.
