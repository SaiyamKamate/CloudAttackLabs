"""Scenario script: repeated failed logins against the lab ShopCloud.

Run by the Scenario Engine as a separate process:

    python authentication_abuse.py <base_url> '<json parameters>'

It sends `attempts` login requests for `username` with a deliberately wrong
password, waiting `interval_seconds` between them, then prints one JSON summary
line on stdout. Standard library only, so it needs no dependencies.

The password below is always wrong on purpose. The script never receives and
never uses a real account password; its job is to produce failed-login
telemetry in the lab, nothing more.
"""
import json
import sys
import time
import urllib.error
import urllib.request

# Deliberately invalid. Not any lab account's real password.
WRONG_PASSWORD = "wrong-password-not-real-cloudattacklab"


def send_login(base_url, username):
    """POST /api/login with the wrong password. Returns the HTTP status, or None on a transport error."""
    data = json.dumps({"username": username, "password": WRONG_PASSWORD}).encode()
    request = urllib.request.Request(
        base_url + "/api/login", data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return None


def main():
    base_url = sys.argv[1].rstrip("/")
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    username = str(params.get("username", "lab_user"))
    attempts = int(params.get("attempts", 1))
    interval = float(params.get("interval_seconds", 0))

    unauthorized = rate_limited = other = errors = 0
    for i in range(attempts):
        status = send_login(base_url, username)
        if status == 401:
            unauthorized += 1
        elif status == 429:
            rate_limited += 1
        elif status is None:
            errors += 1
        else:
            other += 1
        if interval > 0 and i < attempts - 1:
            time.sleep(interval)

    summary = {
        "scenario": "authentication_abuse",
        "target": base_url,
        "username": username,
        "attempts_sent": attempts,
        "unauthorized_401": unauthorized,
        "rate_limited_429": rate_limited,
        "other": other,
        "errors": errors,
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
