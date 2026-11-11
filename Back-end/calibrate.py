"""Finds SCALE_FACTOR and SCALE_ZERO for configure.py. Run it once on the NodeMCU (e.g. from Thonny).

You need something with a known weight, such as a 1 kg bag of sugar.
Do it with the bin standing on the scale, EMPTY, exactly as it will be used.
"""
from hx711 import HX711

D5, D6 = 14, 12  # HX711 DT, SCK

scale = HX711(D5, D6)

input("Put the EMPTY bin on the scale (nothing inside), then press Enter...")
empty = scale.read_average(20)
known_kg = float(input("Put the known weight in the bin and type its weight in kg (e.g. 1): "))
loaded = scale.read_average(20)

factor = (loaded - empty) / known_kg
print("Raw reading changed by", loaded - empty)
print("Put these lines in configure.py:")
print("SCALE_FACTOR = %.1f" % factor)
print("SCALE_ZERO = %.1f" % empty)
# A negative factor just means A+ and A- are swapped; it still works.
# Re-run this if you move the bin or change the board it stands on (the zero drifts).
