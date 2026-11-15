"""Scenario script: a restricted account repeatedly requests a resource it may not read.

Run by the Scenario Engine as a separate process:

    python unauthorized_resource_access.py <base_url> '<json parameters>'

It logs in as `username` (the restricted lab test account), then sends
`attempts` requests for GET /api/orders with that token, waiting
`interval_seconds` between them. Every request must be refused with 403. It
prints one JSON summary line on stdout. Standard library only.

Exit code:
    0  every request was refused with 403
    1  the login did not work, so nothing could be tested
    2  at least one request was not refused with 403. If any returned 200 the
       summary says "access_control_failed": true: the restriction is broken.

The account is a synthetic lab test account (docs/shopcloud-interface.md,
section 2). Its password comes from LAB_TEST_ACCOUNTS when the platform sets
it, otherwise the documented default. The token is never printed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

RESOURCE = "/api/orders"

# Documented synthetic test accounts this script may use.
DEFAULT_TEST_ACCOUNTS = {"lab_restricted": "lab-restricted-pass"}


def test_account_password(username):
    raw = os.environ.get("LAB_TEST_ACCOUNTS")
    if raw:
        try:
            configured = json.loads(raw)
        except ValueError:
            configured = {}
        if isinstance(configured, dict) and username in configured:
            return str(configured[username])
    return DEFAULT_TEST_ACCOUNTS.get(username)


def login(base_url, username, password):
    """POST /api/login. Returns (status, token); either may be None."""
    data = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        base_url + "/api/login", data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode() or "{}")
            token = body.get("token") if isinstance(body, dict) else None
            return response.status, token if isinstance(token, str) else None
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def request_resource(base_url, token):
    """GET the protected resource. Returns the HTTP status, or None on a transport error."""
    request = urllib.request.Request(
        base_url + RESOURCE, method="GET", headers={"Authorization": "Bearer " + token}
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
    username = str(params.get("username", "lab_restricted"))
    attempts = int(params.get("attempts", 1))
    interval = float(params.get("interval_seconds", 0))

    summary = {
        "scenario": "unauthorized_resource_access",
        "target": base_url,
        "username": username,
        "resource": "GET " + RESOURCE,
        "attempts_sent": 0,
        "denied_403": 0,
        "other": 0,          # anything that was not a 403
        "granted_200": 0,    # the part of "other" that was a 200
        "errors": 0,         # the part of "other" that got no answer at all
        "access_control_failed": False,
    }

    password = test_account_password(username)
    if password is None:
        summary["error"] = "no test credentials for account '%s'" % username
        print(json.dumps(summary))
        sys.stderr.write(summary["error"] + "\n")
        return 1

    login_status, token = login(base_url, username, password)
    summary["login_status"] = login_status
    if login_status != 200 or token is None:
        summary["error"] = "login as '%s' did not return a token (HTTP %s)" % (username, login_status)
        print(json.dumps(summary))
        sys.stderr.write(summary["error"] + "\n")
        return 1

    for i in range(attempts):
        status = request_resource(base_url, token)
        summary["attempts_sent"] += 1
        if status == 403:
            summary["denied_403"] += 1
        else:
            summary["other"] += 1
            if status == 200:
                summary["granted_200"] += 1
            elif status is None:
                summary["errors"] += 1
        if interval > 0 and i < attempts - 1:
            time.sleep(interval)

    summary["access_control_failed"] = summary["granted_200"] > 0
    print(json.dumps(summary))

    if summary["access_control_failed"]:
        sys.stderr.write(
            "access control failed: %d of %d requests by '%s' returned 200\n"
            % (summary["granted_200"], summary["attempts_sent"], username)
        )
        return 2
    if summary["other"] > 0:
        sys.stderr.write(
            "%d of %d requests were not refused with 403\n" % (summary["other"], summary["attempts_sent"])
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
