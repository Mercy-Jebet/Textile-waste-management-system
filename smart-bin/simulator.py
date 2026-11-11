"""Simulates the smart bin's sensors without any hardware.

It follows the same steps as Back-end/main.py on the NodeMCU:
  1. "Measure" the distance from the lid to the top of the textiles (cm)
  2. Convert the distance into a fill level (%) and status (OK / FILLING / FULL)
  3. Send the reading to the server
  4. Between readings, ask the server whether someone scanned the QR code. If so,
     "open the flap", pretend the IR sensor and scale saw a deposit, and report it.

Instead of a real HC-SR04 sensor, the textile pile grows as simulated people
make deposits, with small random sensor noise and the occasional bad echo.
Once the bin is FULL, a collector empties it a few readings later. Most simulated
QR deposits are normal, but some are empty (nothing went in) or suspiciously heavy.

Start the web app first (python app.py), then in a second terminal:
    python smart-bin/simulator.py
    python smart-bin/simulator.py --bin bin_002 --interval 1 --readings 40
"""

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request


def device_key_from_env():
    """DEVICE_API_KEY from the environment or the project's .env file, like app.py uses."""
    if os.environ.get("DEVICE_API_KEY"):
        return os.environ["DEVICE_API_KEY"]
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() == "DEVICE_API_KEY" and value.strip():
                    return value.strip().strip('"').strip("'")
    return "demo-device-key"

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


def api_post(server, path, device_key, data):
    """Returns (reply as a dict or None, short description of what happened)."""
    req = urllib.request.Request(
        f"{server}{path}",
        data=json.dumps(data).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Device-Key": device_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read() or b"{}"), f"sent ({resp.status})"
    except urllib.error.HTTPError as e:
        return None, f"server rejected it ({e.code})"
    except urllib.error.URLError as e:
        return None, f"server unreachable ({e.reason}) - is app.py running?"


def send_reading(server, bin_id, device_key, fill_level, status):
    return api_post(server, f"/api/bins/{bin_id}", device_key,
                    {"fill_level": fill_level, "status": status})[1]


def simulate_qr_deposit(bin_):
    """What the real bin measures after a scan: (weight added in kg, IR sensor triggered)."""
    time.sleep(2)  # the consumer puts their textiles in
    roll = random.random()
    if roll < 0.08:  # scanned, then walked away without depositing
        return round(random.gauss(0, 0.02), 3), False
    if roll < 0.13:  # stones or metal hidden in the bag
        weight = round(random.uniform(6, 12), 2)
    else:
        weight = round(random.uniform(0.3, 3.0), 2)
    bin_.pile_height = min(BIN_HEIGHT, bin_.pile_height + weight * 2)  # about 2 cm per kg
    return weight, True


def poll_for_scans(args, bin_):
    """Asks the server once a second whether someone scanned the bin, for one reading interval."""
    end = time.time() + args.interval
    while time.time() < end:
        reply, _ = api_post(args.server, f"/api/bins/{args.bin}/session", args.key, {})
        if reply and reply.get("session_id"):
            print("     >> QR code scanned: flap opened")
            weight, ir_triggered = simulate_qr_deposit(bin_)
            reply, result = api_post(args.server, f"/api/bins/{args.bin}/deposit", args.key, {
                "session_id": reply["session_id"], "weight_kg": weight, "ir_triggered": ir_triggered})
            if reply:
                result = f"+{reply['points']} pts" if reply["accepted"] else f"not counted: {reply['reason']}"
            print(f"     >> flap closed | IR {'triggered' if ir_triggered else 'saw nothing'} | "
                  f"{weight:+.2f} kg | {result}")
        time.sleep(max(0, min(1, end - time.time())))


def bar(fill_level, width=20):
    filled = round(fill_level / 100 * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def main():
    parser = argparse.ArgumentParser(description="Simulate a smart textile bin's sensors")
    parser.add_argument("--bin", default="bin_001", help="bin id to report as (default: bin_001)")
    parser.add_argument("--server", default="http://127.0.0.1:5000", help="web app address")
    parser.add_argument("--key", default=device_key_from_env(), help="device key (default: DEVICE_API_KEY from .env)")
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

            if args.offline:
                time.sleep(args.interval)
            else:
                poll_for_scans(args, bin_)
    except KeyboardInterrupt:
        print("\nSimulation stopped")


if __name__ == "__main__":
    main()
