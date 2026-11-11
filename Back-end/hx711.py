"""Reads the load cell through the HX711 amplifier (MicroPython).

MicroPython has no built-in HX711 driver, so this talks to it directly:
the HX711 pulls DT low when a reading is ready, then sends 24 bits,
one per pulse on SCK.
"""
import time

from machine import Pin, disable_irq, enable_irq


class HX711:
    def __init__(self, dt_pin, sck_pin, factor=1.0):
        self.dt = Pin(dt_pin, Pin.IN)
        self.sck = Pin(sck_pin, Pin.OUT, value=0)
        self.factor = factor  # raw units per kg, from calibrate.py

    def read_raw(self, timeout_ms=1000):
        start = time.ticks_ms()
        while self.dt.value() == 1:
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("HX711 not responding - check the DT and SCK wires")
            time.sleep_ms(1)
        value = 0
        # SCK must not stay high for more than 60 us or the HX711 switches off,
        # so nothing may interrupt this loop.
        state = disable_irq()
        try:
            for _ in range(24):
                self.sck.on()
                self.sck.off()
                value = (value << 1) | self.dt.value()
            # A 25th pulse selects channel A, gain 128, for the next reading.
            self.sck.on()
            self.sck.off()
        finally:
            enable_irq(state)
        if value & 0x800000:  # negative number (two's complement)
            value -= 0x1000000
        return value

    def read_average(self, times=10):
        return sum(self.read_raw() for _ in range(times)) / times

    def read_kg(self, times=10):
        """Weight in kg, not zeroed: only the difference between two readings is meaningful."""
        return self.read_average(times) / self.factor
