from datetime import datetime

from flask import Flask, render_template

app = Flask(__name__)

SAMPLE_BINS = [
    {"id": "BIN-014", "location": "Central Market", "fill": 82, "weight": 18.4, "status": "Collection due"},
    {"id": "BIN-009", "location": "Riverside Mall", "fill": 56, "weight": 12.1, "status": "Ready"},
    {"id": "BIN-021", "location": "North Station", "fill": 94, "weight": 21.7, "status": "Collection due"},
    {"id": "BIN-006", "location": "City Library", "fill": 31, "weight": 7.6, "status": "Ready"},
    {"id": "BIN-018", "location": "West Community Hub", "fill": 8, "weight": 1.9, "status": "Online"},
]


@app.get("/")
def dashboard():
    total_weight = sum(item["weight"] for item in SAMPLE_BINS)
    due_count = sum(item["status"] == "Collection due" for item in SAMPLE_BINS)
    average_fill = round(sum(item["fill"] for item in SAMPLE_BINS) / len(SAMPLE_BINS))
    return render_template(
        "index.html",
        bins=SAMPLE_BINS,
        total_weight=total_weight,
        due_count=due_count,
        average_fill=average_fill,
        updated_at=datetime.now().strftime("%H:%M"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)

 
import firebase_admin
from firebase_admin import credentials, db, auth
from flask import Flask, render_template, request, redirect
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import qrcode

app = Flask(__name__)


cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://your-project-default-rtdb.firebaseio.com'
})



# Home page
@app.route('/')
def home():
    return render_template('home.html')


@app.route('/dashboard')
def dashboard():
    bin_data = db.reference('bins/bin_001').get()
    if bin_data['fill_level'] >= 90:
        send_alert_email(bin_data['fill_level'])
    return render_template('dashboard.html', bin=bin_data)

@app.route('/rewards/<consumer_id>')
def rewards(consumer_id):
    consumer = db.reference(f'consumers/{consumer_id}').get()
    deposits = db.reference('deposits').get()
    return render_template('rewards.html',
        consumer=consumer,
        deposits=deposits
    )


@app.route('/deposit/<consumer_id>', methods=['POST'])
def deposit(consumer_id):
    ref = db.reference(f'consumers/{consumer_id}')
    data = ref.get()
    new_points = data['points_balance'] + 10
    ref.update({'points_balance': new_points})
    db.reference('deposits').push().set({
        'consumer_id': consumer_id,
        'bin_id': 'bin_001',
        'deposit_timestamp': str(datetime.now()),
        'points_earned': 10
    })
    return redirect(f'/rewards/{consumer_id}')


@app.route('/leaderboard')
def leaderboard():
    consumers = db.reference('consumers').get()
    return render_template('leaderboard.html', consumers=consumers)


@app.route('/admin')
def admin():
    bins = db.reference('bins').get()
    consumers = db.reference('consumers').get()
    deposits = db.reference('deposits').get()
    return render_template('admin.html',
        bins=bins,
        consumers=consumers,
        deposits=deposits
    )


def send_alert_email(fill_level):
    msg = MIMEText(
        f'ALERT: Bin is {fill_level}% full. Please collect immediately.'
    )
    msg['Subject'] = 'Bin Full Alert — Textile Waste System'
    msg['From'] = 'your_email@gmail.com'
    msg['To'] = 'collector@gmail.com'
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login('your_email@gmail.com', 'your_app_password')
        s.send_message(msg)


if __name__ == '__main__':
    app.run(debug=True)   




from classes import Sensor, Bin, Consumer, Collector, Deposit, Dashboard


import firebase_admin
from firebase_admin import credentials, db
from flask import Flask, render_template

app = Flask(__name__)


cred = credentials.Certificate('serviceAccountKey.json')
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://your-project-default-rtdb.firebaseio.com'
})


@app.route('/')
def home():
    return render_template('home.html')

if __name__ == '__main__':
    app.run(debug=True)