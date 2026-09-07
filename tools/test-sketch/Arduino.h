#pragma once
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#define F_CPU 16000000UL
#define OUTPUT 1
#define LOW 0
#define HEX 16
#define _BV(b) (1u << (b))
#define F(x) (x)
enum { WGM11=1, WGM12=3, WGM13=4, CS10=0, COM1A1=7, PB1=1 };
extern uint8_t TCCR1A, TCCR1B, PORTB;
extern uint16_t ICR1, OCR1A;
void pinMode(int, int);
void digitalWrite(int, int);
unsigned long micros(void);
struct SerialClass {
  void begin(long);
  int available();
  int read();
  void print(const char*);
  void print(char);
  void print(uint8_t, int);
  void println(const char*);
  void println();
};
extern SerialClass Serial;
