import hmac
import os
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from functools import wraps

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "Front-end")

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "Templates"),
    static_folder=FRONTEND_DIR,
    static_url_path="/static",
)
# Signs the login cookie. Set SECRET_KEY so admin logins survive a restart.
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# Firebase is used when both the key file and database URL are available;
# otherwise the app runs in demo mode with in-memory sample data.
SERVICE_ACCOUNT_KEY = os.environ.get(
    "FIREBASE_KEY_FILE", os.path.join(BASE_DIR, "serviceAccountKey.json")
)
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL")

ALERT_THRESHOLD = 90
POINTS_PER_DEPOSIT = 10
DEFAULT_BIN = "bin_001"


class FirebaseStore:
    def __init__(self, key_file, db_url):
        import firebase_admin
        from firebase_admin import credentials, db

        firebase_admin.initialize_app(
            credentials.Certificate(key_file), {"databaseURL": db_url}
        )
        self.db = db

    def get(self, path):
        return self.db.reference(path).get()

    def update(self, path, values):
        self.db.reference(path).update(values)

    def push(self, path, value):
        self.db.reference(path).push().set(value)


class DemoStore:
    def __init__(self):
        self.data = {
            "bins": {
                "bin_001": {"fill_level": 92, "status": "FULL", "location": "Gikomba Market", "last_updated": "2026-10-15 09:30"},
                "bin_002": {"fill_level": 56, "status": "FILLING", "location": "Westlands Mall", "last_updated": "2026-10-15 09:28"},
                "bin_003": {"fill_level": 18, "status": "OK", "location": "Kenyatta Avenue", "last_updated": "2026-10-15 09:25"},
            },
            "consumers": {
                "consumer_001": {"name": "Amina", "email": "amina@example.com", "points_balance": 120},
                "consumer_002": {"name": "Brian", "email": "brian@example.com", "points_balance": 80},
                "consumer_003": {"name": "Wanjiru", "email": "wanjiru@example.com", "points_balance": 150},
            },
            "deposits": {
                "d1": {"consumer_id": "consumer_001", "bin_id": "bin_001", "deposit_timestamp": "2026-10-14 16:02", "points_earned": 10},
                "d2": {"consumer_id": "consumer_003", "bin_id": "bin_002", "deposit_timestamp": "2026-10-14 17:45", "points_earned": 10},
            },
        }

    def _node(self, path):
        node = self.data
        for part in path.strip("/").split("/"):
            node = node.setdefault(part, {})
        return node

    def get(self, path):
        node = self.data
        for part in path.strip("/").split("/"):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def update(self, path, values):
        self._node(path).update(values)

    def push(self, path, value):
        node = self._node(path)
        node[f"d{len(node) + 1}"] = value


if os.path.exists(SERVICE_ACCOUNT_KEY) and FIREBASE_DB_URL:
    store = FirebaseStore(SERVICE_ACCOUNT_KEY, FIREBASE_DB_URL)
    DEMO_MODE = False
else:
    store = DemoStore()
    DEMO_MODE = True


@app.context_processor
def inject_mode():
    return {"demo_mode": DEMO_MODE, "is_admin": session.get("is_admin", False)}


# In demo mode the admin password defaults to "admin"; with Firebase it must be set.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ("admin" if DEMO_MODE else None)


# Key the bin sensors (or the simulator) send with each reading.
DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY") or ("demo-device-key" if DEMO_MODE else None)
BIN_STATUSES = {"OK", "FILLING", "FULL"}


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def bin_colour(fill_level):
    if fill_level >= ALERT_THRESHOLD:
        return "RED"
    if fill_level >= 50:
        return "ORANGE"
    return "GREEN"


def get_consumer(consumer_id):
    consumer = store.get(f"consumers/{consumer_id}")
    if consumer is None:
        abort(404, description=f"Consumer '{consumer_id}' not found")
    return {**consumer, "consumer_id": consumer_id}


# Bins already alerted, so the dashboard's auto-refresh doesn't resend emails.
alerted_bins = set()


def send_alert_email(bin_id, fill_level):
    sender = os.environ.get("ALERT_EMAIL_FROM")
    password = os.environ.get("ALERT_EMAIL_PASSWORD")
    recipient = os.environ.get("ALERT_EMAIL_TO")
    if not (sender and password and recipient):
        print(f"[alert] {bin_id} is {fill_level}% full (email not configured)")
        return
    msg = MIMEText(f"ALERT: Bin {bin_id} is {fill_level}% full. Please collect immediately.")
    msg["Subject"] = "Bin Full Alert — Textile Waste System"
    msg["From"] = sender
    msg["To"] = recipient
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(sender, password)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        print(f"[alert] failed to send email: {e}")


def check_alert(bin_id, fill_level):
    if fill_level >= ALERT_THRESHOLD:
        if bin_id not in alerted_bins:
            send_alert_email(bin_id, fill_level)
            alerted_bins.add(bin_id)
    else:
        alerted_bins.discard(bin_id)


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/dashboard")
def dashboard():
    bin_data = store.get(f"bins/{DEFAULT_BIN}") or {}
    fill_level = bin_data.get("fill_level", 0)
    check_alert(DEFAULT_BIN, fill_level)
    return render_template("dashboard.html", bin=bin_data, colour=bin_colour(fill_level))


@app.route("/rewards/<consumer_id>")
def rewards(consumer_id):
    consumer = get_consumer(consumer_id)
    deposits = store.get("deposits") or {}
    return render_template("rewards.html", consumer=consumer, deposits=deposits)


@app.route("/deposit/<consumer_id>", methods=["POST"])
def deposit(consumer_id):
    consumer = get_consumer(consumer_id)
    store.update(
        f"consumers/{consumer_id}",
        {"points_balance": consumer.get("points_balance", 0) + POINTS_PER_DEPOSIT},
    )
    store.push("deposits", {
        "consumer_id": consumer_id,
        "bin_id": DEFAULT_BIN,
        "deposit_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "points_earned": POINTS_PER_DEPOSIT,
    })
    return redirect(f"/rewards/{consumer_id}")


@app.route("/leaderboard")
def leaderboard():
    consumers = store.get("consumers") or {}
    ranked = sorted(
        ({**c, "consumer_id": cid} for cid, c in consumers.items()),
        key=lambda c: c.get("points_balance", 0),
        reverse=True,
    )
    return render_template("leaderboard.html", consumers=ranked)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and hmac.compare_digest(password, ADMIN_PASSWORD):
            session["is_admin"] = True
            next_page = request.args.get("next", "")
            return redirect(next_page if next_page.startswith("/") and not next_page.startswith("//") else url_for("admin"))
        error = "Wrong password" if ADMIN_PASSWORD else "Admin login is disabled: set ADMIN_PASSWORD"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("is_admin", None)
    return redirect(url_for("home"))


@app.route("/api/bins/<bin_id>", methods=["POST"])
def receive_reading(bin_id):
    """Receives a reading from a bin sensor (real or simulated)."""
    key = request.headers.get("X-Device-Key", "")
    if not DEVICE_API_KEY or not hmac.compare_digest(key, DEVICE_API_KEY):
        abort(401)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", bin_id):
        abort(400, description="Invalid bin id")
    data = request.get_json(silent=True) or {}
    fill_level = data.get("fill_level")
    status = data.get("status")
    if not isinstance(fill_level, (int, float)) or not 0 <= fill_level <= 100:
        abort(400, description="fill_level must be a number from 0 to 100")
    if status not in BIN_STATUSES:
        abort(400, description=f"status must be one of {sorted(BIN_STATUSES)}")
    reading = {
        "fill_level": round(fill_level, 2),
        "status": status,
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if isinstance(data.get("location"), str):
        reading["location"] = data["location"][:80]
    store.update(f"bins/{bin_id}", reading)
    check_alert(bin_id, fill_level)
    return jsonify({"ok": True, "bin_id": bin_id, **reading})


@app.route("/admin")
@admin_required
def admin():
    return render_template(
        "admin.html",
        bins=store.get("bins") or {},
        consumers=store.get("consumers") or {},
        deposits=store.get("deposits") or {},
    )


if __name__ == "__main__":
    print("Running in DEMO mode (sample data)" if DEMO_MODE else "Connected to Firebase")
    app.run(host="127.0.0.1", port=5000, debug=True)
