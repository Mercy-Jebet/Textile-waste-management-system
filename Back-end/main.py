import machine
import time
import network
import urequests
import ujson

WIFI_SSID = "YourWiFiName"
WIFI_PASSWORD = "YourWiFiPassword"


FIREBASE_URL = "https://your-project-default-rtdb.firebaseio.com"
FIREBASE_SECRET = "your-firebase-database-secret"


TRIG_PIN = 5   # D1 on NodeMCU
ECHO_PIN = 4   # D2 on NodeMCU
BIN_HEIGHT = 50  # height of bin in cm


def connect_wifi():
    wifi = network.WLAN(network.STA_IF)
    wifi.active(True)
    wifi.connect(WIFI_SSID, WIFI_PASSWORD)
    print("Connecting to WiFi...")
    while not wifi.isconnected():
        time.sleep(0.5)
        print(".")
    print("Connected! IP:", wifi.ifconfig()[0])

def measure_distance():
    trig = machine.Pin(TRIG_PIN, machine.Pin.OUT)
    echo = machine.Pin(ECHO_PIN, machine.Pin.IN)

    # send pulse
    trig.off()
    time.sleep_us(2)
    trig.on()
    time.sleep_us(10)
    trig.off()

    # measure echo
    while echo.value() == 0:
        pulse_start = time.ticks_us()
    while echo.value() == 1:
        pulse_end = time.ticks_us()

    duration = time.ticks_diff(pulse_end, pulse_start)
    distance = (duration * 0.0343) / 2
    return round(distance, 2)


def get_fill_level(distance):
    fill = ((BIN_HEIGHT - distance) / BIN_HEIGHT) * 100
    fill = max(0, min(100, fill))
    return round(fill, 2)


def send_to_firebase(fill_level, status):
    url = f"{FIREBASE_URL}/bins/bin_001.json?auth={FIREBASE_SECRET}"
    data = ujson.dumps({
        "fill_level": fill_level,
        "status": status,
        "last_updated": "2026-10-12"
    })
    try:
        response = urequests.patch(url, data=data)
        print("Firebase updated:", response.text)
        response.close()
    except Exception as e:
        print("Firebase error:", e)


connect_wifi()

while True:
    distance = measure_distance()
    fill_level = get_fill_level(distance)

    if fill_level >= 90:
        status = "FULL"
    elif fill_level >= 50:
        status = "FILLING"
    else:
        status = "OK"

    print(f"Distance: {distance}cm | Fill: {fill_level}% | Status: {status}")

    send_to_firebase(fill_level, status)

    time.sleep(30)  # read every 30 seconds