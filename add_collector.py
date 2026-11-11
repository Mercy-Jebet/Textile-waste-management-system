"""Creates a collector account in Firebase from the command line.
(The admin can also do this on the website: Admin > Users > Add a collector.)

    python add_collector.py <username> "<full name>" <email>

Collectors log in at /collector/login and land on the dashboard.
Their email address receives the bin-full alerts.
"""
import getpass
import sys

import app

if len(sys.argv) != 4:
    raise SystemExit(__doc__)
username, name, email = sys.argv[1:]
if app.DEMO_MODE:
    raise SystemExit("Demo mode: use the built-in account collector / password123.")

password = getpass.getpass("Password (at least 6 characters): ")
if password != getpass.getpass("Repeat it: "):
    raise SystemExit("Passwords don't match. Nothing changed.")

error = app.create_collector(username, name, email.lower(), password)
raise SystemExit(error or f"{username} can now log in as a collector.")
