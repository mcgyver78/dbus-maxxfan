/* Drives the sketch's loop() with a scripted serial input and captures every
 * reply, so the line assembly can be tested without hardware. */
#include "Arduino.h"
#include <string>
#include <deque>
#include <iostream>

uint8_t TCCR1A, TCCR1B, PORTB;
uint16_t ICR1, OCR1A;
SerialClass Serial;

static std::deque<char> rx;
static std::string tx;

void pinMode(int, int) {}
void digitalWrite(int, int) {}
unsigned long micros(void)
{
    /* Advance fast enough that the transmit busy-wait finishes at once. */
    static unsigned long t = 0;
    t += 1000000UL;
    return t;
}
void SerialClass::begin(long) {}
int SerialClass::available() { return (int)rx.size(); }
int SerialClass::read()
{
    if (rx.empty()) return -1;
    char c = rx.front(); rx.pop_front(); return c;
}
void SerialClass::print(const char *s) { tx += s; }
void SerialClass::print(char c) { tx += c; }
void SerialClass::print(uint8_t v, int)
{
    char b[8]; snprintf(b, sizeof b, "%X", v); tx += b;
}
void SerialClass::println(const char *s) { tx += s; tx += "\n"; }
void SerialClass::println() { tx += "\n"; }

void setup();
void loop();

static std::string feed(const std::string &in)
{
    tx.clear();
    for (char c : in) rx.push_back(c);
    while (!rx.empty()) loop();
    return tx;
}

static int failures = 0;
static void check(const char *label, const std::string &got, const std::string &want)
{
    bool ok = (got == want);
    if (!ok) failures++;
    std::cout << (ok ? "ok   " : "FAIL ") << label;
    if (!ok) std::cout << "  got [" << got << "] want [" << want << "]";
    std::cout << "\n";
}

int main()
{
    setup();
    tx.clear();

    /* Read the identify string from the sketch rather than retyping it, so a
     * version bump does not break the test - only its shape is asserted. */
    const std::string ident = feed("?\n");
    bool shape = ident.compare(0, 10, "MAXXFAN 1 ") == 0 &&
                 ident.size() > 11 && ident[ident.size() - 1] == '\n';
    check("identify answers MAXXFAN <proto> <version>",
          shape ? "yes" : ("no: " + ident), "yes");

    /* A line longer than the buffer must be discarded whole. Before the fix
     * the tail was executed: 64 spaces then a full command turned the fan to
     * 100 % and beeped. */
    std::string overlong = std::string(64, ' ') + "S 1 100 1 0 0 99 1\n";
    check("overlong line refused", feed(overlong), "ERR line too long\n");

    /* Exactly filling the buffer must not go silent - the driver would sit in
     * its read timeout and mark the fan disconnected. */
    check("buffer-length line answers", feed(std::string(64, 'x') + "\n"),
          "ERR line too long\n");
    check("63 chars still parsed", feed(std::string(63, 'x') + "\n"),
          "ERR unknown command\n");

    /* CR-only clients must not hang forever. */
    check("bare CR terminates", feed("?\r"), ident);
    check("CRLF is not two lines", feed("?\r\n"), ident);

    /* strtol: atoi wrapped at 16 bits, so 65636 arrived as 100. */
    check("16-bit wrap refused", feed("S 65537 65636 1 1 0 65606 1\n"),
          "ERR expected 7 values\n");
    check("trailing junk refused", feed("S 1 50 1 1 0 70 1x\n"),
          "ERR expected 7 values\n");
    check("non-numeric refused", feed("S a b c d e f g\n"),
          "ERR expected 7 values\n");
    check("six values refused", feed("S 1 50 1 1 0 70\n"),
          "ERR expected 7 values\n");

    /* A good command still works, and the warn bit is one-shot. */
    std::string beep = feed("S 1 50 1 1 0 70 1\n");
    check("beep accepted", beep.substr(0, 3), "OK ");
    std::string again = feed("R\n");
    check("resend does not repeat the beep",
          again.substr(0, 3) + again.substr(23, 2), "OK 0D");

    std::cout << (failures ? "\nFAILURES\n" : "\nall sketch line checks passed\n");
    return failures ? 1 : 0;
}
