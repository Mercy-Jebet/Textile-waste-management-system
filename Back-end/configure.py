# Settings for the bin's NodeMCU. Copy this file to the board with main.py and hx711.py.
WIFI_SSID = "YourWiFiName"
WIFI_PASSWORD = "YourWiFiPassword"

# The computer running app.py, as the NodeMCU sees it on the Wi-Fi.
# Start the app with HOST=0.0.0.0 and use that computer's IP address here.
SERVER_URL = "http://192.168.1.20:5000"
# Must match DEVICE_API_KEY on the server ("demo-device-key" in demo mode).
DEVICE_KEY = "demo-device-key"
BIN_ID = "bin_001"

# Raw HX711 units per kg, and the raw reading with the EMPTY bin on the scale.
# Run calibrate.py once and paste both lines it prints here. While SCALE_ZERO is None
# the bin only reports each deposit's weight, and the server adds them up instead.
SCALE_FACTOR = 1.0
SCALE_ZERO = None
