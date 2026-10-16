"""Simulates the smart bin's ultrasonic sensor without any hardware.

It follows the same steps as smart-bin/main.py on the NodeMCU:
  1. "Measure" the distance from the lid to the top of the textiles (cm)
  2. Convert the distance into a fill level (%) and status (OK / FILLING / FULL)
  3. Send the reading to the server

Instead of a real HC-SR04 sensor, the textile pile grows as simulated people
make deposits, with small random sensor noise and the occasional bad echo.
Once the bin is FULL, a collector empties it a few readings later.

Start the web app first (python app.py), then in a second terminal:
    python smart-bin/simulator.py
    python smart-bin/simulator.py --bin bin_002 --interval 1 --readings 40
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.request

BIN_HEIGHT = 50          # cm from sensor to bin floor, same as main.py
SENSOR_MAX_RANGE = 400   # HC-SR04 reports up to ~4 m
SPEED_OF_SOUND = 0.0343  # cm per microsecond


class SimulatedBin:
    """The physical bin: how high the textile pile is inside it."""

    def __init__(self, start_fill_cm=0.0):
        self.pile_height = start_fill_cm
        self.readings_since_full = 0

    def simulate_deposits(self):
        # 0-3 people drop off clothes between readings; each bag adds 0.5-3 cm.
        deposits = random.choices([0, 1, 2, 3], weights=[40, 35, 18, 7])[0]
        for _ in range(deposits):
            self.pile_height += random.uniform(0.5, 3.0)
        self.pile_height = min(self.pile_height, BIN_HEIGHT)
        return deposits

    def maybe_collect(self, status):
        # A collector arrives 3-5 readings after the bin is reported FULL.
        if status != "FULL":
            self.readings_since_full = 0
            return False
        self.readings_since_full += 1
        if self.readings_since_full >= random.randint(3, 5):
            self.pile_height = 0.0
            self.readings_since_full = 0
            return True
        return False


class SimulatedUltrasonicSensor:
    """Pretends to be the HC-SR04: returns an echo time, like the real pins would."""

    def __init__(self, bin_):
        self.bin = bin_

    def echo_duration_us(self):
        # 3% of the time the echo is lost (e.g. sound absorbed by soft fabric).
        if random.random() < 0.03:
            return None
        distance = BIN_HEIGHT - self.bin.pile_height + random.gauss(0, 0.4)
        distance = max(2.0, distance)  # sensor's minimum range is ~2 cm
        return distance * 2 / SPEED_OF_SOUND  # there and back

    def measure_distance(self):
        duration = self.echo_duration_us()
        if duration is None:
            return None
        distance = (duration * SPEED_OF_SOUND) / 2  # same formula as main.py
        if distance > SENSOR_MAX_RANGE:
            return None
        return round(distance, 2)


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


def send_reading(server, bin_id, device_key, fill_level, status):
    body = json.dumps({"fill_level": fill_level, "status": status}).encode()
    req = urllib.request.Request(
        f"{server}/api/bins/{bin_id}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Device-Key": device_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return f"sent ({resp.status})"
    except urllib.error.HTTPError as e:
        return f"server rejected reading ({e.code})"
    except urllib.error.URLError as e:
        return f"server unreachable ({e.reason}) - is app.py running?"


def bar(fill_level, width=20):
    filled = round(fill_level / 100 * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def main():
    parser = argparse.ArgumentParser(description="Simulate a smart textile bin sensor")
    parser.add_argument("--bin", default="bin_001", help="bin id to report as (default: bin_001)")
    parser.add_argument("--server", default="http://127.0.0.1:5000", help="web app address")
    parser.add_argument("--key", default="demo-device-key", help="device key (DEVICE_API_KEY on the server)")
    parser.add_argument("--interval", type=float, default=3, help="seconds between readings (real device: 30)")
    parser.add_argument("--readings", type=int, default=0, help="stop after this many readings (0 = run forever)")
    parser.add_argument("--start", type=float, default=0, help="starting fill level in %%")
    parser.add_argument("--offline", action="store_true", help="only print readings, don't send them")
    args = parser.parse_args()

    bin_ = SimulatedBin(start_fill_cm=BIN_HEIGHT * args.start / 100)
    sensor = SimulatedUltrasonicSensor(bin_)

    print(f"Simulating sensor for {args.bin} (bin height {BIN_HEIGHT} cm), Ctrl+C to stop")
    count = 0
    try:
        while args.readings == 0 or count < args.readings:
            count += 1
            deposits = bin_.simulate_deposits()
            distance = sensor.measure_distance()

            if distance is None:
                print(f"#{count:<3} no echo received - skipping this reading")
            else:
                fill_level = get_fill_level(distance)
                status = get_status(fill_level)
                result = "not sent (offline)" if args.offline else send_reading(
                    args.server, args.bin, args.key, fill_level, status)
                print(f"#{count:<3} +{deposits} deposits | distance {distance:5.1f} cm | "
                      f"{bar(fill_level)} {fill_level:5.1f}% {status:<7} | {result}")
                if bin_.maybe_collect(status):
                    print("     >> collector emptied the bin")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nSimulation stopped")


if __name__ == "__main__":
    main()
