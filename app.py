import hmac
import io
import json
import os
import random
import secrets
import time
import re
import smtplib
import socket
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import wraps

import qrcode
from flask import (Flask, abort, flash, has_request_context, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "Front-end")


def load_env_file(path):
    """Reads KEY=value lines from .env (kept out of Git) into the environment.
    Variables already set in the terminal win; empty values are ignored."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            key, sep, value = line.strip().partition("=")
            value = value.strip().strip('"').strip("'")
            if sep and value and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value)


load_env_file(os.path.join(BASE_DIR, ".env"))

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "Templates"),
    static_folder=FRONTEND_DIR,
    static_url_path="/static",
)
# Signs the login cookie. Set SECRET_KEY so admin logins survive a restart.
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)
# Browsers won't send the login cookie with cross-site form posts.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Behind a tunnel (cloudflared/ngrok) for phones on mobile data: trust its
# X-Forwarded-Proto/Host headers so links and QR codes use its https:// address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Firebase is used when the service account key file is present;
# otherwise the app runs in demo mode with in-memory sample data.
SERVICE_ACCOUNT_KEY = os.environ.get(
    "FIREBASE_KEY_FILE", os.path.join(BASE_DIR, "serviceAccountKey.json")
)
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL")
if not FIREBASE_DB_URL and os.path.exists(SERVICE_ACCOUNT_KEY):
    # Default Realtime Database URL for the key's project.
    with open(SERVICE_ACCOUNT_KEY, encoding="utf-8") as f:
        FIREBASE_DB_URL = f"https://{json.load(f)['project_id']}-default-rtdb.firebaseio.com"

ALERT_THRESHOLD = 90
# An open alert closes by itself if a reading drops below this (emptied but not marked).
ALERT_RESOLVE_BELOW = 50
POINTS_PER_DEPOSIT = 10
USERNAME_RE = r"[A-Za-z0-9_-]{3,30}"
# Minimum gap between two deposits by the same consumer at the same bin.
SCAN_COOLDOWN_SECONDS = 5 * 60
# The bin's scale must measure at least this much for a deposit to earn points.
MIN_DEPOSIT_KG = 0.1
# Heavier deposits (stones, metal) earn nothing and are flagged for the admin.
MAX_DEPOSIT_KG = 5.0
# After a scan, the bin has this long to open its flap...
SESSION_PICKUP_SECONDS = 60
# ...and this long in total to report what was deposited.
SESSION_MAX_SECONDS = 120
ACTIVE_SESSION = {"waiting", "open"}


class FirebaseStore:
    def __init__(self, key_file, db_url):
        import firebase_admin
        from firebase_admin import credentials, db

        from firebase_admin import exceptions

        firebase_admin.initialize_app(
            credentials.Certificate(key_file), {"databaseURL": db_url}
        )
        self.db = db
        try:
            db.reference("/").get(shallow=True)
        except exceptions.NotFoundError:
            raise SystemExit(
                f"No Realtime Database at {db_url}. Check FIREBASE_DB_URL - it should look like "
                "https://<project-id>-default-rtdb.firebaseio.com (copy it from the Firebase Console's "
                "Realtime Database page), or remove it so the address is taken from the key file."
            )

    def get(self, path):
        return self.db.reference(path).get()

    def update(self, path, values):
        self.db.reference(path).update(values)

    def set(self, path, value):
        self.db.reference(path).set(value)

    def push(self, path, value):
        self.db.reference(path).push().set(value)

    def delete(self, path):
        self.db.reference(path).delete()


def now_str(minutes_ago=0):
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M")


class DemoStore:
    def __init__(self):
        demo_hash = generate_password_hash("password123")
        self.data = {
            "bins": {
                "bin_001": {"fill_level": 92, "status": "FULL", "location": "Gikomba Market", "last_updated": now_str(2)},
                "bin_002": {"fill_level": 56, "status": "FILLING", "location": "Westlands Mall", "last_updated": now_str(4)},
                "bin_003": {"fill_level": 18, "status": "OK", "location": "Kenyatta Avenue", "last_updated": now_str(7)},
            },
            "alerts": {
                "bin_001": {"fill_level": 92, "raised_at": now_str(20), "email_sent": False,
                            "email_note": "demo data, no email was sent"},
            },
            "consumers": {
                # Demo accounts all use the password "password123".
                "amina": {"name": "Amina", "email": "amina@example.com", "points_balance": 120, "password_hash": demo_hash},
                "brian": {"name": "Brian", "email": "brian@example.com", "points_balance": 80, "password_hash": demo_hash},
                "wanjiru": {"name": "Wanjiru", "email": "wanjiru@example.com", "points_balance": 150, "password_hash": demo_hash},
            },
            "collectors": {
                "collector": {"name": "Demo Collector", "email": "collector@example.com", "password_hash": demo_hash},
            },
            "deposits": {
                "d1": {"consumer_id": "amina", "bin_id": "bin_001", "deposit_timestamp": now_str(60 * 18),
                       "points_earned": 10, "weight_kg": 1.4, "accepted": True},
                "d2": {"consumer_id": "wanjiru", "bin_id": "bin_002", "deposit_timestamp": now_str(60 * 16),
                       "points_earned": 10, "weight_kg": 2.1, "accepted": True},
                "d3": {"consumer_id": "brian", "bin_id": "bin_001", "deposit_timestamp": now_str(60 * 3),
                       "points_earned": 0, "weight_kg": 8.6, "accepted": False, "flagged": True,
                       "note": "the deposit was unusually heavy and has been flagged for review"},
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

    def set(self, path, value):
        parent, _, key = path.strip("/").rpartition("/")
        (self._node(parent) if parent else self.data)[key] = value

    def push(self, path, value):
        node = self._node(path)
        node[f"d{len(node) + 1}"] = value

    def delete(self, path):
        parent, _, key = path.strip("/").rpartition("/")
        node = self.get(parent) if parent else self.data
        if isinstance(node, dict):
            node.pop(key, None)


if os.path.exists(SERVICE_ACCOUNT_KEY) and FIREBASE_DB_URL:
    store = FirebaseStore(SERVICE_ACCOUNT_KEY, FIREBASE_DB_URL)
    DEMO_MODE = False
else:
    store = DemoStore()
    DEMO_MODE = True


# Lets the scan page play the bin's part, for testing without hardware.
# Always on in demo mode; set SIMULATE_BIN=1 to allow it with Firebase too.
SIMULATE_BIN = DEMO_MODE or os.environ.get("SIMULATE_BIN") == "1"


@app.context_processor
def inject_mode():
    return {
        "demo_mode": DEMO_MODE,
        "simulate_bin": SIMULATE_BIN,
        "is_admin": session.get("is_admin", False),
        "user_id": session.get("user_id"),
        "collector_id": session.get("collector_id"),
    }


# In demo mode the admin password defaults to "admin"; with Firebase it must be set.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or ("admin" if DEMO_MODE else None)


# Key the bin sensors (or the simulator) send with each reading.
DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY") or ("demo-device-key" if DEMO_MODE else None)
BIN_STATUSES = {"OK", "FILLING", "FULL"}


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def collector_required(view):
    """Collector pages: only logged-in (and email-verified) collectors."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        collector_id = session.get("collector_id")
        account = store.get(f"collectors/{collector_id}") if collector_id else None
        if not account or account.get("disabled"):
            session.pop("collector_id", None)
            if account:
                flash(DISABLED_MESSAGE)
            return redirect(url_for("collector_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def staff_name():
    return session["collector_id"]


def login_required(view):
    """Consumer pages: sends signed-out visitors to the login page, then back."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        user_id = session.get("user_id")
        account = store.get(f"consumers/{user_id}") if user_id else None
        if not account or account.get("disabled"):
            session.pop("user_id", None)
            if account:
                flash(DISABLED_MESSAGE)
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


DISABLED_MESSAGE = "This account has been turned off by the admin. Please contact them."


def create_collector(username, name, email, password):
    """Adds a collector account. Returns an error message, or None if it worked."""
    if not re.fullmatch(USERNAME_RE, username) or username.lower() == "admin":
        return "Username must be 3-30 letters, numbers, _ or - (and not 'admin')"
    if not re.fullmatch(EMAIL_RE, email):
        return "Enter a valid email address (bin-full alerts go there)"
    if len(password) < 6:
        return "Password must be at least 6 characters"
    if store.get(f"collectors/{username}") is not None or store.get(f"consumers/{username}") is not None:
        return f"The username '{username}' is already taken"
    store.update(f"collectors/{username}", {
        "name": name or username, "email": email, "email_verified": False, "alerts_enabled": True,
        "password_hash": generate_password_hash(password), "created": now_str(),
    })
    return None


def safe_next(default):
    """Only follow ?next= to pages on this site."""
    next_page = request.args.get("next", "")
    return next_page if next_page.startswith("/") and not next_page.startswith("//") else default


def get_bin(bin_id):
    bin_data = store.get(f"bins/{bin_id}")
    if bin_data is None:
        abort(404, description=f"Bin '{bin_id}' not found")
    return {**bin_data, "bin_id": bin_id}


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


def send_email(recipients, subject, body):
    """Sends one email through Gmail. Returns (sent, note) so callers can say what really happened."""
    sender = os.environ.get("ALERT_EMAIL_FROM")
    password = os.environ.get("ALERT_EMAIL_PASSWORD")
    recipients = sorted({r for r in recipients if r})
    if not (sender and password):
        return False, "email not configured (set ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD)"
    if not recipients:
        return False, "no one to email (no collector has alerts turned on)"
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as s:
            try:
                s.login(sender, password)
            except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected):
                # Gmail rejects (or just hangs up on) a normal account password.
                return False, ("Gmail refused the login. ALERT_EMAIL_PASSWORD in .env must be a 16-letter "
                               "Gmail App Password (myaccount.google.com/apppasswords), not the normal password")
            s.send_message(msg)
        return True, "sent to " + msg["To"]
    except (smtplib.SMTPException, OSError) as e:
        return False, f"sending failed: {e}"


def alert_recipients():
    """Collectors who want alerts, plus anyone in ALERT_EMAIL_TO (comma-separated)."""
    extra = {e.strip() for e in os.environ.get("ALERT_EMAIL_TO", "").split(",") if e.strip()}
    return extra | {c["email"] for c in (store.get("collectors") or {}).values()
                    if c.get("email") and c.get("alerts_enabled", True) and not c.get("disabled")}


def send_alert_email(bin_id, location, why):
    link = request.host_url + "dashboard" if has_request_context() else ""
    sent, note = send_email(
        alert_recipients(),
        f"Bin Full Alert: {bin_id} ({location})",
        f"Bin {bin_id} at {location} is full: {why}. Please collect it.\n\n"
        f"Open the collector dashboard to acknowledge the alert and mark the bin as emptied:\n{link}",
    )
    if not sent:
        print(f"[alert] {bin_id} is full ({why}); {note}")
    return sent, note


# A bin is full when EITHER its textiles weigh 90% of its capacity OR the pile reaches 90% of
# its height (light fabric can fill the space before it gets heavy).
BIN_CAPACITY_KG = float(os.environ.get("BIN_CAPACITY_KG") or 10)


def bin_levels(bin_id, b, deposits=None):
    """How full a bin is, by volume (ultrasonic sensor) and by weight (scale)."""
    volume = b.get("fill_level", 0) or 0
    if isinstance(b.get("weight_kg"), (int, float)):
        kg, source = max(0.0, b["weight_kg"]), "scale"   # the bin's load cell reports it
    else:
        # No scale reading (simulator or older firmware): add up what was weighed in
        # since the bin was last emptied. Everything weighed went into the bin, points or not.
        if deposits is None:
            deposits = (store.get("deposits") or {}).values()
        emptied_ts = b.get("emptied_ts")  # exact time (seconds) the bin was last emptied

        def since_emptied(d):
            if emptied_ts is not None:
                return d.get("ts", 0) >= emptied_ts
            return d.get("deposit_timestamp", "") >= b.get("last_emptied", "")

        kg = sum(max(0, d.get("weight_kg") or 0) for d in deposits
                 if d.get("bin_id") == bin_id and since_emptied(d))
        source = "deposits"
    weight_pct = round(min(100.0, kg / BIN_CAPACITY_KG * 100), 1)
    fill = max(volume, weight_pct)
    return {
        "volume_pct": volume, "kg_inside": round(kg, 2), "weight_source": source,
        "weight_pct": weight_pct, "fill": fill, "status": level_status(fill),
    }


def level_status(fill):
    return "FULL" if fill >= ALERT_THRESHOLD else "FILLING" if fill >= 50 else "OK"


def full_reason(lv):
    reasons = []
    if lv["weight_pct"] >= ALERT_THRESHOLD:
        reasons.append(f"weight {lv['kg_inside']:.1f} kg of {BIN_CAPACITY_KG:g} kg ({lv['weight_pct']:.0f}%)")
    if lv["volume_pct"] >= ALERT_THRESHOLD:
        reasons.append(f"volume {lv['volume_pct']:.0f}% full")
    return " and ".join(reasons)


# Logging in takes two steps: the password, then a 6-digit code emailed to the account's address.
# The code is kept (hashed) in the database, never in the browser's cookie.
CODE_TTL_SECONDS = 10 * 60
CODE_MAX_TRIES = 5
RESEND_AFTER_SECONDS = 30
EMAIL_RE = r"[^@\s/]+@[^@\s/]+\.[A-Za-z]{2,}"
# kind -> (database table, session key once logged in, page to land on)
ACCOUNT_KINDS = {
    "collector": ("collectors", "collector_id", "dashboard"),
    "consumer": ("consumers", "user_id", "rewards"),
}


def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'*' * max(1, len(name) - 2)}@{domain}"


def send_login_code(kind, username, email):
    """Emails a fresh code. Returns False if it couldn't be sent."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    store.set(f"login_codes/{kind}_{username}", {
        "code_hash": generate_password_hash(code),
        "email": email,
        "expires": int(time.time()) + CODE_TTL_SECONDS,
        "sent": int(time.time()),
        "tries": 0,
    })
    sent, note = send_email(
        [email], f"Your login code: {code}",
        f"Your Smartie Bin login code is {code}.\n"
        f"It expires in {CODE_TTL_SECONDS // 60} minutes.\n\n"
        "If you didn't just try to log in, someone knows your password: change it.",
    )
    if sent:
        return True
    if DEMO_MODE:
        flash(f"Demo mode, so no email was sent. Your code is {code}.")
        return True
    print(f"[login] couldn't email a code to {email}: {note}")
    store.delete(f"login_codes/{kind}_{username}")
    flash(f"Couldn't send the code: {note}.")
    return False


# Emailed login codes are off for now. Set LOGIN_EMAIL_CODES=1 in .env to turn them back on.
LOGIN_EMAIL_CODES = os.environ.get("LOGIN_EMAIL_CODES") == "1"


def begin_verification(kind, username, next_page, welcome=None):
    """Password was right: log them in, or (with LOGIN_EMAIL_CODES on) email them a code first."""
    session.clear()
    if not LOGIN_EMAIL_CODES:
        session[ACCOUNT_KINDS[kind][1]] = username
        if welcome:
            flash(welcome)
        return redirect(next_page)
    session["pending"] = {"kind": kind, "id": username, "next": next_page, "welcome": welcome}
    email = (store.get(f"{ACCOUNT_KINDS[kind][0]}/{username}") or {}).get("email")
    if email:
        send_login_code(kind, username, email)
    return redirect(url_for("verify", who=kind))


def close_alert(bin_id, alert, how):
    store.push("alert_history", {**alert, "bin_id": bin_id, "resolved_at": now_str(), "resolved": how})
    store.delete(f"alerts/{bin_id}")


def check_alert(bin_id):
    """Opens one alert (and sends one email) when a bin reaches 90% by weight or volume.
    It stays open until a collector marks the bin as emptied, so readings hovering
    around 90% don't resend it. Called after every sensor reading and every deposit."""
    b = store.get(f"bins/{bin_id}") or {}
    lv = bin_levels(bin_id, b)
    alert = store.get(f"alerts/{bin_id}")
    if lv["fill"] >= ALERT_THRESHOLD and not alert:
        why = full_reason(lv)
        sent, note = send_alert_email(bin_id, b.get("location", bin_id), why)
        store.set(f"alerts/{bin_id}", {
            "fill_level": lv["fill"], "reason": why, "raised_at": now_str(),
            "email_sent": sent, "email_note": note,
        })
    elif alert and lv["fill"] < ALERT_RESOLVE_BELOW:
        close_alert(bin_id, alert, f"reading dropped to {lv['fill']}% without being marked as emptied")


@app.route("/")
def home():
    deposits = [d for d in (store.get("deposits") or {}).values() if d.get("accepted", True)]
    return render_template(
        "home.html",
        bin_count=len(store.get("bins") or {}),
        consumer_count=len(store.get("consumers") or {}),
        deposit_count=len(deposits),
        total_kg=sum(d.get("weight_kg", 0) for d in deposits),
        points=POINTS_PER_DEPOSIT,
    )


@app.route("/dashboard")
@collector_required
def dashboard():
    """Every bin's capacity, fullest first, with its alert (if any)."""
    alerts = store.get("alerts") or {}
    deposits = (store.get("deposits") or {}).values()
    all_bins = store.get("bins") or {}
    # A bin can be full without an alert (e.g. it filled up before weight-based alerts
    # existed and hasn't reported since): open the missing alert now. It's stored, so
    # this happens once, not on every refresh.
    for bin_id, b in all_bins.items():
        if bin_id not in alerts and bin_levels(bin_id, b, deposits)["fill"] >= ALERT_THRESHOLD:
            check_alert(bin_id)
            alerts = store.get("alerts") or {}
    bins = []
    for bin_id, b in all_bins.items():
        lv = bin_levels(bin_id, b, deposits)
        bins.append({**b, **lv, "bin_id": bin_id, "fill_level": lv["fill"],
                     "space_left": round(100 - lv["fill"], 1), "colour": bin_colour(lv["fill"]),
                     "alert": alerts.get(bin_id)})
    bins.sort(key=lambda b: (b["alert"] is None, -b["fill_level"]))
    collections = list((store.get("collections") or {}).values())[-5:][::-1]
    me = store.get(f"collectors/{staff_name()}") or {}
    return render_template("dashboard.html", bins=bins, collections=collections, me=me,
                           open_alerts=len(alerts), threshold=ALERT_THRESHOLD, capacity=BIN_CAPACITY_KG)


@app.route("/dashboard/alerts", methods=["POST"])
@collector_required
def toggle_alert_emails():
    """Each collector chooses whether bin-full alerts are emailed to them."""
    path = f"collectors/{staff_name()}"
    on = not (store.get(path) or {}).get("alerts_enabled", True)
    store.update(path, {"alerts_enabled": on})
    flash("Bin-full alerts will be emailed to you." if on else "You won't be emailed bin-full alerts.")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/<bin_id>/acknowledge", methods=["POST"])
@collector_required
def acknowledge_alert(bin_id):
    """A collector says "I've seen this and I'm on my way"."""
    get_bin(bin_id)
    if store.get(f"alerts/{bin_id}") is None:
        flash(f"{bin_id} has no open alert.")
    else:
        store.update(f"alerts/{bin_id}", {"acknowledged_by": staff_name(), "acknowledged_at": now_str()})
        flash(f"Alert for {bin_id} acknowledged.")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/<bin_id>/emptied", methods=["POST"])
@collector_required
def mark_emptied(bin_id):
    """Records a collection and closes the bin's alert. The bin's next sensor
    reading confirms the real level, and reopens the alert if it is still full."""
    bin_data = get_bin(bin_id)
    lv = bin_levels(bin_id, bin_data)
    store.push("collections", {
        "bin_id": bin_id,
        "location": bin_data.get("location", ""),
        "emptied_by": staff_name(),
        "emptied_at": now_str(),
        "fill_level_before": lv["fill"],
        "kg_collected": lv["kg_inside"],
    })
    emptied = {"fill_level": 0, "status": "OK", "last_emptied": now_str(), "emptied_ts": time.time()}
    if lv["weight_source"] == "scale":
        emptied["weight_kg"] = 0  # until the bin's scale reports again
    store.update(f"bins/{bin_id}", emptied)
    save_history(bin_id, "emptied")  # so the charts show the drop to zero
    alert = store.get(f"alerts/{bin_id}")
    if alert:
        close_alert(bin_id, alert, f"marked as emptied by {staff_name()}")
    flash(f"{bin_id} marked as emptied.")
    return redirect(url_for("dashboard"))


@app.route("/rewards")
@login_required
def rewards():
    consumer = get_consumer(session["user_id"])
    deposits = store.get("deposits") or {}
    return render_template("rewards.html", consumer=consumer, deposits=deposits)


@app.route("/qr/<bin_id>.png")
def bin_qr(bin_id):
    """The QR code printed on a bin's sticker. It encodes that bin's scan URL."""
    get_bin(bin_id)
    return qr_png(public_base() + url_for("scan", bin_id=bin_id))


@app.route("/qr/site.png")
def site_qr():
    """Opens the website on a phone, for logging in before scanning a bin."""
    return qr_png(public_base() + url_for("qr_demo"))


def qr_png(text):
    buf = io.BytesIO()
    qrcode.make(text).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=0)


# The address phones use to reach this laptop. Set PUBLIC_URL in .env to override it
# (for example when using a tunnel, or if the laptop has several network adapters).
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
LISTENING_ON_NETWORK = os.environ.get("HOST", "127.0.0.1") == "0.0.0.0"


def lan_ip():
    """This laptop's address on the Wi-Fi, skipping VirtualBox, VPN and loopback adapters."""
    candidates = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # picks the adapter used for the internet; sends nothing
        candidates.append(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        candidates += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    for ip in candidates:
        if not ip.startswith(("127.", "169.254.", "192.168.56.")) and not ip.endswith(".0"):
            return ip
    return "127.0.0.1"


def public_base():
    if PUBLIC_URL:
        return PUBLIC_URL
    host, _, port = request.host.partition(":")
    if host in ("127.0.0.1", "localhost"):
        return f"http://{lan_ip()}:{port or '80'}"
    return request.host_url.rstrip("/")


@app.route("/qr-demo")
def qr_demo():
    bins = store.get("bins") or {}
    return render_template("qr_demo.html", bins=bins, phone_url=public_base(),
                           listening=LISTENING_ON_NETWORK or bool(PUBLIC_URL))


# A deposit session ties one scan to one opening of the bin's flap:
#   waiting  - consumer scanned, the bin hasn't opened yet
#   open     - the bin opened its flap and is watching the IR sensor and scale
#   done     - the bin confirmed a deposit and points were given
#   rejected - the bin closed but the deposit didn't count (nothing went in, too light or too heavy)
#   expired  - the bin never answered
def get_session(bin_id):
    """The bin's latest deposit session (or None), closing it if the bin took too long."""
    s = store.get(f"sessions/{bin_id}")
    if not s or s.get("status") not in ACTIVE_SESSION:
        return s
    age = time.time() - s.get("started", 0)
    if (s["status"] == "waiting" and age > SESSION_PICKUP_SECONDS) or age > SESSION_MAX_SECONDS:
        s["status"] = "expired"
        s["message"] = "The bin didn't respond, so no deposit was recorded. Please try again."
        store.update(f"sessions/{bin_id}", {"status": s["status"], "message": s["message"]})
    return s


def finish_deposit(bin_id, s, weight_kg, ir_triggered):
    """Decides whether what the bin measured counts as a deposit, and records it."""
    weight_kg = round(weight_kg, 2)
    user_id = s["consumer_id"]
    flagged = weight_kg > MAX_DEPOSIT_KG
    if not ir_triggered:
        reason = "nothing passed through the bin's opening"
    elif weight_kg < MIN_DEPOSIT_KG:
        reason = f"the scale measured under {int(MIN_DEPOSIT_KG * 1000)} g"
    elif flagged:
        reason = "the deposit was unusually heavy and has been flagged for review"
    else:
        reason = ""
    accepted = not reason
    points = POINTS_PER_DEPOSIT if accepted else 0

    if accepted:
        consumer = store.get(f"consumers/{user_id}") or {}
        store.update(f"consumers/{user_id}", {
            "points_balance": consumer.get("points_balance", 0) + points,
        })
        store.update(f"consumers/{user_id}/last_scans", {bin_id: int(time.time())})
    store.push("deposits", {
        "consumer_id": user_id,
        "bin_id": bin_id,
        "deposit_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ts": time.time(),  # exact time, so emptying the bin a few seconds later resets its weight
        "points_earned": points,
        "weight_kg": weight_kg,
        "accepted": accepted,
        "flagged": flagged,
        "note": reason,
    })

    location = (store.get(f"bins/{bin_id}") or {}).get("location", bin_id)
    if accepted:
        message = f"Deposit of {weight_kg} kg recorded at {location}: +{points} points!"
    else:
        message = f"No points this time: {reason}."
    store.update(f"sessions/{bin_id}", {"status": "done" if accepted else "rejected", "message": message})
    check_alert(bin_id)  # the deposit may have pushed the bin past 90% of its weight capacity
    return {"accepted": accepted, "points": points, "weight_kg": weight_kg, "reason": reason}


def show_result(bin_id, s):
    """Shows a finished session's result to its consumer once."""
    if not s.get("reported"):
        flash(s.get("message", ""))
        store.update(f"sessions/{bin_id}", {"reported": True})


@app.route("/scan/<bin_id>", methods=["GET", "POST"])
@login_required
def scan(bin_id):
    """Where a phone lands after scanning a bin's QR code."""
    bin_data = get_bin(bin_id)
    lv = bin_levels(bin_id, bin_data)
    bin_data.update(fill_level=lv["fill"], status=lv["status"])
    user_id = session["user_id"]
    consumer = get_consumer(user_id)
    last_scan = store.get(f"consumers/{user_id}/last_scans/{bin_id}") or 0
    wait = int(SCAN_COOLDOWN_SECONDS - (time.time() - last_scan))
    s = get_session(bin_id)
    mine = s is not None and s.get("consumer_id") == user_id
    active = s is not None and s.get("status") in ACTIVE_SESSION

    if mine and not active and not s.get("reported"):
        show_result(bin_id, s)
        return redirect(url_for("rewards"))

    if request.method == "POST":
        if wait > 0:
            flash(f"You already deposited at this bin. Try again in {wait // 60 + 1} min.")
        elif active and not mine:
            flash("Someone else is using this bin right now. Please try again in a moment.")
        elif not active:
            store.set(f"sessions/{bin_id}", {
                "session_id": secrets.token_hex(8),
                "consumer_id": user_id,
                "started": int(time.time()),
                "status": "waiting",
            })
        return redirect(url_for("scan", bin_id=bin_id))

    return render_template("scan.html", bin=bin_data, consumer=consumer,
                           wait=max(wait, 0), points=POINTS_PER_DEPOSIT,
                           min_grams=int(MIN_DEPOSIT_KG * 1000),
                           my_session=s if mine and active else None,
                           busy=active and not mine)


@app.route("/scan/<bin_id>/status")
@login_required
def scan_status(bin_id):
    """Polled by the scan page while the consumer is depositing."""
    s = get_session(bin_id)
    if not s or s.get("consumer_id") != session["user_id"]:
        return jsonify({"status": "none"})
    if s["status"] not in ACTIVE_SESSION:
        show_result(bin_id, s)
    return jsonify({"status": s["status"]})


@app.route("/scan/<bin_id>/simulate", methods=["POST"])
@login_required
def simulate_bin(bin_id):
    """Plays the bin's part, so the scan works without hardware.
    The form gives the weight the scale "measured" and whether the IR sensor saw anything."""
    if not SIMULATE_BIN:
        abort(404)
    try:
        weight_kg = float(request.form.get("weight_kg") or random.uniform(0.3, 2.5))
    except ValueError:
        weight_kg = -1
    if not 0 <= weight_kg <= 50:
        flash("Enter a weight between 0 and 50 kg.")
        return redirect(url_for("scan", bin_id=bin_id))
    s = get_session(bin_id)
    if s and s.get("consumer_id") == session["user_id"] and s["status"] in ACTIVE_SESSION:
        finish_deposit(bin_id, s, weight_kg, request.form.get("ir_triggered") == "on")
    return redirect(url_for("scan", bin_id=bin_id))


@app.route("/leaderboard")
def leaderboard():
    consumers = store.get("consumers") or {}
    ranked = sorted(
        ({"name": c.get("name", cid), "points_balance": c.get("points_balance", 0)}
         for cid, c in consumers.items()),
        key=lambda c: c["points_balance"],
        reverse=True,
    )
    return render_template("leaderboard.html", consumers=ranked)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        account = check_account("consumer", username, password)
        if account and account.get("disabled"):
            error = DISABLED_MESSAGE
        elif account:
            return begin_verification("consumer", username, safe_next(url_for("rewards")))
        else:
            error = "Wrong username or password"
    return render_template("login.html", error=error)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """The admin's own login page: just the ADMIN_PASSWORD from .env."""
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session["is_admin"] = True
            return redirect(safe_next(url_for("admin")))
        error = "Wrong admin password"
    return render_template("admin_login.html", error=error, password_set=bool(ADMIN_PASSWORD))


@app.route("/collector/login", methods=["GET", "POST"])
def collector_login():
    """Collectors' own login page. Consumer accounts can't log in here."""
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        account = check_account("collector", username, request.form.get("password", ""))
        if account and account.get("disabled"):
            error = DISABLED_MESSAGE
        elif account:
            return begin_verification("collector", username, safe_next(url_for("dashboard")))
        else:
            error = "Wrong collector username or password"
    return render_template("collector_login.html", error=error)


def check_account(kind, username, password):
    """The account if the username and password are right, otherwise None."""
    if not re.fullmatch(USERNAME_RE, username):
        return None
    account = store.get(f"{ACCOUNT_KINDS[kind][0]}/{username}")
    if account and check_password_hash(account.get("password_hash", ""), password):
        return account
    return None


@app.route("/verify", methods=["GET", "POST"])
def verify():
    """Second login step: the code that was emailed to the account."""
    pending = session.get("pending")
    if not LOGIN_EMAIL_CODES or not pending:
        # The half-finished login was lost (e.g. the app restarted): back to the right login page.
        if LOGIN_EMAIL_CODES and request.method == "POST":
            flash("Your login timed out. Please log in again to get a new code.")
        collector = request.args.get("who") == "collector"
        return redirect(url_for("collector_login" if collector else "login"))
    kind, username = pending["kind"], pending["id"]
    table, session_key, _ = ACCOUNT_KINDS[kind]
    account = store.get(f"{table}/{username}")
    if account is None:
        session.clear()
        return redirect(url_for("login"))
    code_path = f"login_codes/{kind}_{username}"
    record = store.get(code_path)
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "email":
            # Accounts made before email verification, or a typo'd address at registration.
            email = request.form.get("email", "").strip().lower()
            if account.get("email_verified") and account.get("email"):
                error = "This account's email is already verified."
            elif not re.fullmatch(EMAIL_RE, email):
                error = "That doesn't look like an email address."
            elif not send_login_code(kind, username, email):
                error = "We couldn't send the email. Please try again later."
        elif action == "resend":
            email = (record or {}).get("email") or account.get("email")
            if record and time.time() - record.get("sent", 0) < RESEND_AFTER_SECONDS:
                error = f"Please wait {RESEND_AFTER_SECONDS} seconds before asking for another code."
            elif not email or not send_login_code(kind, username, email):
                error = "We couldn't send the email. Please try again later."
        else:
            code = request.form.get("code", "").strip().replace(" ", "")
            if not record or time.time() > record.get("expires", 0):
                error = "That code has expired. Ask for a new one."
            elif record.get("tries", 0) >= CODE_MAX_TRIES:
                error = "Too many wrong codes. Ask for a new one."
            elif not check_password_hash(record["code_hash"], code):
                store.update(code_path, {"tries": record.get("tries", 0) + 1})
                error = "Wrong code. Check the email and try again."
            else:
                store.delete(code_path)
                store.update(f"{table}/{username}", {"email": record["email"], "email_verified": True})
                session.clear()
                session[session_key] = username
                if pending.get("welcome"):
                    flash(pending["welcome"])
                return redirect(pending["next"])
        record = store.get(code_path)

    return render_template(
        "verify.html", error=error,
        sent_to=mask_email(record["email"]) if record else None,
        can_change_email=not (account.get("email_verified") and account.get("email")),
        sending_failed=record is None and bool(account.get("email")),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        name = request.form.get("name", "").strip()[:60] or username
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip().lower()
        if not re.fullmatch(USERNAME_RE, username):
            error = "Username must be 3-30 letters, numbers, _ or -"
        elif not re.fullmatch(EMAIL_RE, email):
            error = "Enter a valid email address. We'll send a code to it."
        elif username.lower() == "admin":
            error = "That username is reserved"
        elif len(password) < 6:
            error = "Password must be at least 6 characters"
        elif password != request.form.get("confirm", ""):
            error = "Passwords don't match"
        elif (store.get(f"consumers/{username}") is not None
              or store.get(f"collectors/{username}") is not None):
            error = "That username is taken"
        else:
            store.update(f"consumers/{username}", {
                "name": name,
                "email": email,
                "email_verified": False,
                "password_hash": generate_password_hash(password),
                "points_balance": 0,
            })
            return begin_verification(
                "consumer", username, safe_next(url_for("rewards")),
                welcome="Welcome! Scan a bin's QR code when you drop off textiles to earn points.")
    return render_template("register.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


def check_device(bin_id):
    """Device endpoints: the request must carry the device key and a sensible bin id."""
    key = request.headers.get("X-Device-Key", "")
    if not DEVICE_API_KEY or not hmac.compare_digest(key, DEVICE_API_KEY):
        abort(401)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", bin_id):
        abort(400, description="Invalid bin id")


@app.route("/api/bins/<bin_id>/session", methods=["POST"])
def claim_session(bin_id):
    """The bin asks every few seconds whether someone has scanned it.
    If so, it gets the session id and opens its flap."""
    check_device(bin_id)
    s = get_session(bin_id)
    if not s or s.get("status") != "waiting":
        return jsonify({"session_id": None})
    store.update(f"sessions/{bin_id}", {"status": "open", "opened": int(time.time())})
    return jsonify({"session_id": s["session_id"]})


@app.route("/api/bins/<bin_id>/deposit", methods=["POST"])
def receive_deposit(bin_id):
    """The bin reports what its IR sensor and scale saw once the flap closed."""
    check_device(bin_id)
    data = request.get_json(silent=True) or {}
    weight_kg = data.get("weight_kg")
    ir_triggered = data.get("ir_triggered")
    if not isinstance(weight_kg, (int, float)) or isinstance(weight_kg, bool):
        abort(400, description="weight_kg must be a number")
    if not isinstance(ir_triggered, bool):
        abort(400, description="ir_triggered must be true or false")
    s = get_session(bin_id)
    if not s or s.get("status") != "open" or s.get("session_id") != data.get("session_id"):
        abort(409, description="No open deposit session with that id")
    return jsonify({"ok": True, **finish_deposit(bin_id, s, weight_kg, ir_triggered)})


@app.route("/api/bins/<bin_id>", methods=["POST"])
def receive_reading(bin_id):
    """Receives a reading from a bin sensor (real or simulated)."""
    check_device(bin_id)
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
    weight = data.get("weight_kg")  # optional: total weight from the bin's load cell
    if weight is not None:
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not -5 <= weight <= 200:
            abort(400, description="weight_kg must be a number of kg")
        reading["weight_kg"] = round(max(0.0, weight), 2)
    store.update(f"bins/{bin_id}", reading)
    save_history(bin_id, "reading")
    check_alert(bin_id)
    return jsonify({"ok": True, "bin_id": bin_id, **reading})


def save_history(bin_id, event):
    """Adds one point to readings/<bin_id>, the fill-level history used for charts
    (history_charts.py). event is "reading" (from the bin) or "emptied" (by a collector)."""
    lv = bin_levels(bin_id, store.get(f"bins/{bin_id}") or {})
    store.push(f"readings/{bin_id}", {
        "ts": int(time.time()),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "volume_pct": lv["volume_pct"],
        "weight_kg": lv["kg_inside"],
        "weight_pct": lv["weight_pct"],
        "fill_pct": lv["fill"],
        "status": lv["status"],
    })


# ---------------------------------------------------------------- admin
def deposit_status(d):
    """One word for a deposit's state, used for the admin's labels and filters."""
    if d.get("invalid"):
        return "invalid"
    if d.get("flagged") and not d.get("points_earned"):
        return "flagged"
    if d.get("accepted", True):
        return "accepted"
    return "rejected"


def check_admin_target(kind, username):
    if kind not in ACCOUNT_KINDS or not re.fullmatch(USERNAME_RE, username):
        abort(404)
    path = f"{ACCOUNT_KINDS[kind][0]}/{username}"
    account = store.get(path)
    if account is None:
        abort(404, description=f"No {kind} called '{username}'")
    return path, account


@app.route("/admin")
@admin_required
def admin():
    deposits = (store.get("deposits") or {}).values()
    return render_template(
        "admin.html",
        bins=store.get("bins") or {},
        consumers=store.get("consumers") or {},
        collectors=store.get("collectors") or {},
        deposit_count=len(deposits),
        total_kg=sum(d.get("weight_kg", 0) for d in deposits if deposit_status(d) == "accepted"),
        flagged_count=sum(deposit_status(d) == "flagged" for d in deposits),
    )


@app.route("/admin/users")
@admin_required
def admin_users():
    deposits = (store.get("deposits") or {}).values()
    counts = {}
    for d in deposits:
        counts[d.get("consumer_id")] = counts.get(d.get("consumer_id"), 0) + 1
    consumers = sorted((store.get("consumers") or {}).items(), key=lambda kv: kv[0])
    collectors = sorted((store.get("collectors") or {}).items(), key=lambda kv: kv[0])
    return render_template("admin_users.html", consumers=consumers, collectors=collectors, counts=counts)


@app.route("/admin/collectors", methods=["POST"])
@admin_required
def admin_add_collector():
    f = request.form
    username = f.get("username", "").strip()
    error = create_collector(username, f.get("name", "").strip()[:60],
                             f.get("email", "").strip().lower(), f.get("password", ""))
    flash(error or f"Collector '{username}' added. They can log in at /collector/login.")
    return redirect(url_for("admin_users") + "#collectors")


@app.route("/admin/users/<kind>/<username>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(kind, username):
    """Turns an account off (it can't log in) or back on."""
    path, account = check_admin_target(kind, username)
    disabled = not account.get("disabled", False)
    store.update(path, {"disabled": disabled})
    flash(f"{username} is now {'turned off' if disabled else 'active again'}.")
    return redirect(url_for("admin_users") + f"#{kind}s")


@app.route("/admin/collectors/<username>/password", methods=["POST"])
@admin_required
def admin_reset_password(username):
    path, _ = check_admin_target("collector", username)
    password = request.form.get("password", "")
    if len(password) < 6:
        flash("The new password must be at least 6 characters.")
    else:
        store.update(path, {"password_hash": generate_password_hash(password)})
        flash(f"Password for collector '{username}' changed.")
    return redirect(url_for("admin_users") + "#collectors")


@app.route("/admin/collectors/<username>/delete", methods=["POST"])
@admin_required
def admin_delete_collector(username):
    path, _ = check_admin_target("collector", username)
    store.delete(path)
    flash(f"Collector '{username}' removed.")
    return redirect(url_for("admin_users") + "#collectors")


@app.route("/admin/deposits")
@admin_required
def admin_deposits():
    show = request.args.get("show", "all")
    deposits = [{**d, "deposit_id": key, "status": deposit_status(d)}
                for key, d in (store.get("deposits") or {}).items()]
    counts = {s: sum(d["status"] == s for d in deposits) for s in ("accepted", "flagged", "rejected", "invalid")}
    if show in counts:
        deposits = [d for d in deposits if d["status"] == show]
    deposits.sort(key=lambda d: d.get("deposit_timestamp", ""), reverse=True)
    return render_template("admin_deposits.html", deposits=deposits[:200], show=show,
                           counts=counts, points=POINTS_PER_DEPOSIT)


def get_deposit(deposit_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", deposit_id):
        abort(404)
    d = store.get(f"deposits/{deposit_id}")
    if d is None:
        abort(404, description="Deposit not found")
    return d


def back_to_deposits():
    """Return to the deposits list the admin was on (keeping its filter), but only on this site."""
    ref = request.referrer or ""
    return redirect(ref if ref.startswith(request.host_url + "admin/deposits") else url_for("admin_deposits"))


def change_points(consumer_id, delta):
    consumer = store.get(f"consumers/{consumer_id}")
    if consumer is not None:
        balance = max(0, consumer.get("points_balance", 0) + delta)
        store.update(f"consumers/{consumer_id}", {"points_balance": balance})


@app.route("/admin/deposits/<deposit_id>/invalidate", methods=["POST"])
@admin_required
def admin_invalidate_deposit(deposit_id):
    """Flags a reward as invalid (e.g. it wasn't textiles) and takes its points back."""
    d = get_deposit(deposit_id)
    reason = request.form.get("reason", "").strip()[:120] or "not a valid deposit"
    if d.get("invalid"):
        flash("That deposit is already marked invalid.")
    else:
        revoked = d.get("points_earned", 0)
        change_points(d.get("consumer_id"), -revoked)
        store.update(f"deposits/{deposit_id}", {
            "invalid": True, "accepted": False, "points_earned": 0, "points_revoked": revoked,
            "note": f"marked invalid by the admin: {reason}", "reviewed_at": now_str(),
        })
        flash(f"Deposit marked invalid. {revoked} points taken back from {d.get('consumer_id')}.")
    return back_to_deposits()


@app.route("/admin/deposits/<deposit_id>/approve", methods=["POST"])
@admin_required
def admin_approve_deposit(deposit_id):
    """Gives the points for a deposit the bin flagged or rejected (or one marked invalid by mistake)."""
    d = get_deposit(deposit_id)
    if d.get("points_earned"):
        flash("That deposit already earned its points.")
    else:
        change_points(d.get("consumer_id"), POINTS_PER_DEPOSIT)
        store.update(f"deposits/{deposit_id}", {
            "invalid": False, "flagged": False, "accepted": True, "points_earned": POINTS_PER_DEPOSIT,
            "note": "approved by the admin", "reviewed_at": now_str(),
        })
        flash(f"Deposit approved. {d.get('consumer_id')} got {POINTS_PER_DEPOSIT} points.")
    return back_to_deposits()


if __name__ == "__main__":
    print("Running in DEMO mode (sample data)" if DEMO_MODE else f"Using Firebase database {FIREBASE_DB_URL}")
    if not (os.environ.get("ALERT_EMAIL_FROM") and os.environ.get("ALERT_EMAIL_PASSWORD")):
        print("WARNING: email is not set up, so bin alerts can't be emailed"
              + (" and only the admin can log in" if LOGIN_EMAIL_CODES and not DEMO_MODE else "")
              + ". Fill in ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD in the .env file.")
    # HOST=0.0.0.0 lets the bin's NodeMCU and phones on the same Wi-Fi reach the app.
    if LISTENING_ON_NETWORK:
        print(f"Phones and the bin on the same Wi-Fi can reach the app at http://{lan_ip()}:5000")
        print(f"  open http://{lan_ip()}:5000/qr-demo on your phone, or scan the codes on that page")
    if not DEVICE_API_KEY:
        print("WARNING: DEVICE_API_KEY is not set, so the bin (and smart-bin/simulator.py) can't send data.")
    # Debug mode (auto-reload, error pages) only when asked for: its debugger must never be
    # reachable from the Wi-Fi.
    debug = os.environ.get("FLASK_DEBUG") == "1" and not LISTENING_ON_NETWORK
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=5000, debug=debug)
