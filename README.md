# Smartie Bin

*For the wear n' tear.*

A smart textile recycling bin for Nairobi. People drop unwanted clothes into the bin, scan its QR code with their phone and earn reward points. Sensors in the bin measure how full it is, collectors get an email when a bin is full, and an admin manages accounts and reviews deposits.

The bin (a NodeMCU with sensors) sends its readings to a Flask web app, which stores everything in a Firebase Realtime Database and serves the website.

```
Smart bin (NodeMCU) --readings, deposits--> Flask app (app.py) <--> Firebase Realtime Database
                    <--"was I scanned?"----      |   ^
                                                 |   +-- phones and laptops (consumers, collectors, admin)
                                                 +--> Gmail --> collector's inbox
```

## Features

- **Consumers** register, scan a bin's QR code, open the bin from their phone and earn 10 points per verified deposit. A leaderboard ranks everyone.
- **Deposit verification:** the bin's IR sensor must see textiles go in and its scale must weigh at least 100 g. Deposits over 5 kg are flagged for review. One deposit per person per bin every 5 minutes.
- **Collectors** see every bin's fill level by weight (capacity 10 kg) and volume (ultrasonic sensor). When a bin reaches 90% by either, they get one email. They acknowledge the alert and mark the bin as emptied.
- **Admin** turns accounts off or on, adds collectors, and marks deposits invalid (taking the points back) or approves flagged ones.
- **Optional login codes** emailed at each login.
- **Fill-level history** saved for every reading, with a script that turns it into charts.

## Project layout

| Path | What it is |
|---|---|
| `app.py` | The Flask web app: pages, device API, deposit rules, alerts, accounts |
| `Front-end/Templates/` | The website's HTML pages (Jinja2) |
| `Front-end/style.css`, `Front-end/images/` | Design, logo and photos |
| `Back-end/main.py` | Bin firmware (MicroPython on the NodeMCU) |
| `Back-end/hx711.py` | Load cell (HX711) driver |
| `Back-end/calibrate.py` | One-off scale calibration |
| `Back-end/configure.example.py` | Template for the bin's settings (`configure.py`) |
| `smart-bin/simulator.py` | Simulates the bin, for testing without hardware |
| `seed_firebase.py` | Creates the starting bins in Firebase |
| `add_collector.py` | Creates a collector account from the console |
| `history_charts.py` | Fill-level charts and CSV summaries for the report |

## Running the web app

Requires Python 3.11.

1. Install the libraries:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **Firebase:** put the service account key from your Firebase project in the project folder as `serviceAccountKey.json`. Without it the app runs in **demo mode** with sample data, which is enough to try everything.
3. **Settings:** create a file called `.env` in the project folder (it is kept out of Git):
   ```
   # Gmail account that sends alerts and login codes (needs a Gmail App Password)
   ALERT_EMAIL_FROM=your-alerts-account@gmail.com
   ALERT_EMAIL_PASSWORD=sixteenletterapppassword
   ALERT_EMAIL_TO=                # optional extra recipients, comma-separated

   ADMIN_PASSWORD=choose-one      # for /admin/login
   DEVICE_API_KEY=long-random-text   # the bin and simulator must send this
   SECRET_KEY=long-random-text       # keeps people logged in across restarts

   HOST=0.0.0.0                   # let phones and the bin on the same Wi-Fi reach the app
   SIMULATE_BIN=1                 # show "pretend to be the bin" on the scan page
   LOGIN_EMAIL_CODES=0            # 1 = email a 6-digit code at every login
   BIN_CAPACITY_KG=10
   ```
4. With Firebase, create the bins once: `python seed_firebase.py`
5. Start the app: `python app.py`, then open http://127.0.0.1:5000

| Who | Login page |
|---|---|
| Consumers | `/login` (or register at `/register`) |
| Collectors | `/collector/login` (accounts are created by the admin) |
| Admin | `/admin/login` |

Demo mode accounts: consumers `amina`, `brian`, `wanjiru` and collector `collector`, all with password `password123`; admin password `admin`.

## Demonstrating with a phone

- **Same Wi-Fi or a phone hotspot:** with `HOST=0.0.0.0`, open `/qr-demo` on the laptop and scan the codes. They point to the laptop's network address. Allow Python through Windows Firewall when asked.
- **Phone on mobile data:** run `cloudflared tunnel --url http://localhost:5000` and open the `https://...trycloudflare.com` address it prints (on the laptop too) followed by `/qr-demo`. The address changes every time the tunnel starts.

## Testing without the bin

```
python smart-bin/simulator.py                       # bin_001, a reading every 3 s
python smart-bin/simulator.py --bin bin_002 --interval 1 --readings 40
```

The simulator uses the same device API and key (read from `.env`) as the real bin. It reports rising fill levels and handles QR scans with simulated deposits.

## Setting up the bin (NodeMCU)

1. Flash MicroPython onto the NodeMCU.
2. Copy `Back-end/configure.example.py` to `Back-end/configure.py` and fill in the hotspot's Wi-Fi name and password, `SERVER_URL` (the address `app.py` prints at start-up), `DEVICE_KEY` (same as `DEVICE_API_KEY` in `.env`) and `BIN_ID`.
3. Wire the components (pins are listed at the top of `Back-end/main.py`). Each component's code is commented out in `main.py` until you uncomment it, so it can be added one part at a time.
4. With the empty bin on the scale, run `calibrate.py` and paste its `SCALE_FACTOR` and `SCALE_ZERO` lines into `configure.py`.
5. Copy `main.py`, `hx711.py` and `configure.py` to the board (for example with Thonny) and restart it.

## Fill-level charts

After the bin or simulator has run for a while:
```
python history_charts.py              # all bins
python history_charts.py --bin bin_001 --days 2
```
Charts and CSV files are saved in `reports/`.

## Security notes

- `.env`, `serviceAccountKey.json` and `Back-end/configure.py` hold secrets and are not committed.
- Only the Flask app accesses Firebase, using the service account. Set the Realtime Database rules to deny all other access: `{"rules": {".read": false, ".write": false}}`.
- Passwords and login codes are stored only as hashes. The bin must send the device key with every request.
- Debug mode is off whenever the app is reachable from the network.

## Future work

- Permanent cloud hosting (Firebase Hosting can't run Flask; a Python host such as Render would be needed, and the ESP8266 would have to use HTTPS)
- Sign in with Google (Firebase Authentication)
- Redeeming points for rewards
- Camera-based checking of deposited items
