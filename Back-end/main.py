"""Smart textile bin firmware (MicroPython on NodeMCU / ESP8266).

Copy main.py, hx711.py and configure.py to the board. Run calibrate.py once first.

Wiring:
  HC-SR04    TRIG -> D1, ECHO -> 1k -> D2 -> 2k -> GND    fill level
  IR sensor  OUT -> D0 (power it from 3V3)                something went through the opening
  HX711      DT -> D5, SCK -> D6 (power it from 3V3)      weight
  SG90 servo signal -> D7, power from VIN                 the flap

What it does:
  - every 30 s it measures the fill level and sends it to the server
  - every 2 s it asks the server whether someone has scanned the bin's QR code;
    if so it weighs the bin, opens the flap, waits for the textiles to go in,
    closes the flap, weighs again and reports the result
"""
import gc
import time

import machine
import network

try:
    import urequests  # older MicroPython firmware
except ImportError:
    import requests as urequests  # MicroPython 1.20+ renamed it

from configure import BIN_ID, DEVICE_KEY, SCALE_FACTOR, SERVER_URL, WIFI_PASSWORD, WIFI_SSID
from hx711 import HX711

try:
    from configure import SCALE_ZERO  # raw reading of the empty bin, from calibrate.py
except ImportError:
    SCALE_ZERO = None

# NodeMCU pin labels -> ESP8266 GPIO numbers
D0, D1, D2, D5, D6, D7 = 16, 5, 4, 14, 12, 13

BIN_HEIGHT = 50          # cm from sensor to bin floor
READING_INTERVAL = 30    # seconds between fill-level readings
POLL_INTERVAL = 2        # seconds between asking the server about scans
DEPOSIT_WINDOW = 30      # the flap stays open at most this many seconds
SETTLE_TIME = 5          # close this many seconds after the last thing passed the IR sensor
FLAP_CLOSED = 0          # servo angles; adjust to how your flap is mounted
FLAP_OPEN = 90
HTTP_TIMEOUT = 8         # seconds; a request to a dead server must not freeze the bin
DEPOSIT_RETRIES = 3      # attempts to report a deposit if the Wi-Fi drops

trig = machine.Pin(D1, machine.Pin.OUT, value=0)
echo = machine.Pin(D2, machine.Pin.IN)
ir = machine.Pin(D0, machine.Pin.IN)  # LOW when something is in front of it
servo = machine.PWM(machine.Pin(D7), freq=50)
scale = HX711(D5, D6, SCALE_FACTOR)
wifi = network.WLAN(network.STA_IF)
HEADERS = {"X-Device-Key": DEVICE_KEY}


def ensure_wifi():
    if wifi.isconnected():
        return True
    print("Connecting to WiFi...")
    wifi.active(True)
    wifi.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(40):  # give up after 20 s and try again later
        if wifi.isconnected():
            print("Connected! IP:", wifi.ifconfig()[0])
            return True
        time.sleep(0.5)
    print("WiFi not available")
    wifi.disconnect()  # start the next attempt from scratch
    return False


def _send(path, data):
    try:
        return urequests.post(SERVER_URL + path, json=data, headers=HEADERS, timeout=HTTP_TIMEOUT)
    except TypeError:  # older firmware: urequests has no timeout argument
        return urequests.post(SERVER_URL + path, json=data, headers=HEADERS)


def post(path, data):
    """Sends data to the server. Returns its reply as a dict, or None if it failed."""
    if not ensure_wifi():
        return None
    try:
        r = _send(path, data)
        try:
            if r.status_code != 200:
                print("Server replied", r.status_code, r.text)
                return None
            return r.json()
        finally:
            r.close()
    except Exception as e:  # network errors
        print("Request failed:", e)
        return None
    finally:
        gc.collect()  # the ESP8266 has very little memory


def set_flap(angle):
    # The SG90 wants a pulse every 20 ms: 0.5 ms = 0 degrees, 2.5 ms = 180 degrees.
    pulse_ms = 0.5 + angle / 180 * 2.0
    servo.duty(int(pulse_ms / 20 * 1023))


def measure_distance():
    trig.off()
    time.sleep_us(2)
    trig.on()
    time.sleep_us(10)
    trig.off()
    duration = machine.time_pulse_us(echo, 1, 30000)  # gives up after 30 ms
    if duration < 0:
        return None
    return round((duration * 0.0343) / 2, 2)


def measure_distance_median(samples=5):
    """Fabric absorbs sound, so single echoes are noisy: take the median of a few."""
    readings = []
    for _ in range(samples):
        d = measure_distance()
        if d is not None:
            readings.append(d)
        time.sleep_ms(60)  # let the previous echo die out
    if len(readings) < 3:
        return None
    readings.sort()
    return readings[len(readings) // 2]


def get_fill_level(distance):
    fill = ((BIN_HEIGHT - distance) / BIN_HEIGHT) * 100
    fill = max(0, min(100, fill))
    return round(fill, 2)


def get_status(fill_level):
    if fill_level >= 90:
        return "FULL"
    if fill_level >= 50:
        return "FILLING"
    return "OK"


def send_reading():
    distance = measure_distance_median()
    if distance is None:
        print("No echo from the ultrasonic sensor - skipping this reading")
        return
    fill_level = get_fill_level(distance)
    status = get_status(fill_level)
    reading = {"fill_level": fill_level, "status": status}
    weight = total_weight()
    if weight is not None:
        reading["weight_kg"] = weight  # the server alerts at 90% of the bin's 10 kg capacity
    print("Distance: %scm | Fill: %s%% | Weight: %s kg | Status: %s" % (distance, fill_level, weight, status))
    post("/api/bins/" + BIN_ID, reading)


def total_weight():
    """Kg of textiles in the bin, or None if the scale isn't calibrated or doesn't answer."""
    if SCALE_ZERO is None:
        return None
    try:
        kg = (scale.read_average(10) - SCALE_ZERO) / SCALE_FACTOR
    except OSError as e:
        print("Scale not responding:", e)
        return None
    return round(max(0, kg), 2)


def run_deposit(session_id):
    print("QR code scanned - opening the bin")
    before = scale.read_kg(15)
    set_flap(FLAP_OPEN)

    opened = last_seen = time.ticks_ms()
    ir_triggered = False
    while True:
        now = time.ticks_ms()
        if ir.value() == 0:
            ir_triggered = True
            last_seen = now
        elif ir_triggered and time.ticks_diff(now, last_seen) > SETTLE_TIME * 1000:
            break
        if time.ticks_diff(now, opened) > DEPOSIT_WINDOW * 1000:
            break
        time.sleep_ms(20)

    # Don't close the flap on a hand or a sleeve still in the opening (wait up to 10 s).
    waited = time.ticks_ms()
    while ir.value() == 0 and time.ticks_diff(time.ticks_ms(), waited) < 10000:
        time.sleep_ms(100)
    set_flap(FLAP_CLOSED)

    time.sleep(2)  # let the scale stop wobbling
    weight_kg = round(scale.read_kg(15) - before, 3)
    print("Flap closed | IR triggered: %s | Added: %s kg" % (ir_triggered, weight_kg))
    report = {"session_id": session_id, "weight_kg": weight_kg, "ir_triggered": ir_triggered}
    reply = None
    for attempt in range(DEPOSIT_RETRIES):
        reply = post("/api/bins/%s/deposit" % BIN_ID, report)
        if reply:
            break
        print("Couldn't report the deposit, retrying (%d/%d)" % (attempt + 1, DEPOSIT_RETRIES))
        time.sleep(3)
    if reply:
        print("Accepted, +%s points" % reply["points"] if reply["accepted"] else "Not counted: " + reply["reason"])
    send_reading()  # the fill level just changed


set_flap(FLAP_CLOSED)
ensure_wifi()
send_reading()
last_reading = time.ticks_ms()

while True:
    try:
        reply = post("/api/bins/%s/session" % BIN_ID, {})
        if reply and reply.get("session_id"):
            run_deposit(reply["session_id"])
        if time.ticks_diff(time.ticks_ms(), last_reading) >= READING_INTERVAL * 1000:
            send_reading()
            last_reading = time.ticks_ms()
    except Exception as e:
        # e.g. the scale stopped answering: keep the flap shut and carry on.
        print("Error:", e)
        set_flap(FLAP_CLOSED)
    time.sleep(POLL_INTERVAL)
