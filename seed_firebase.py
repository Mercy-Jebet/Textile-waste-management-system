"""Creates the starting bin records in Firebase. Run once: python seed_firebase.py

Existing bins are left alone, so it is safe to run again.
"""
from datetime import datetime

import app

BINS = {
    "bin_001": "Gikomba Market",
    "bin_002": "Westlands Mall",
    "bin_003": "Kenyatta Avenue",
}

if app.DEMO_MODE:
    raise SystemExit("serviceAccountKey.json not found - nothing to seed.")

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
for bin_id, location in BINS.items():
    if app.store.get(f"bins/{bin_id}") is None:
        app.store.update(f"bins/{bin_id}", {
            "fill_level": 0, "status": "OK", "location": location, "last_updated": now,
        })
        print(f"Created {bin_id} ({location})")
    else:
        print(f"{bin_id} already exists, skipped")
