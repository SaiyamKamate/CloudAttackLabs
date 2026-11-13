"""Check that a running ShopCloud follows docs/shopcloud-interface.md.

Works against the fake one and the real one. Uses only the standard library.

    python tools/check_shopcloud.py http://127.0.0.1:8081 --lab-token lab-dev-token

It resets the target at the start and the end, so only run it against a lab.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

results = []


def call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def check(name, condition, detail=""):
    results.append(bool(condition))
    print(f"{'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  -> ' + str(detail)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--lab-token", default="lab-dev-token")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    lab = {"X-Lab-Token": args.lab_token}

    status, body = call(base, "GET", "/health")
    check("GET /health returns 200 and status ok", status == 200 and body.get("status") == "ok", (status, body))

    status, _ = call(base, "POST", "/lab/reset", headers=lab)
    check("POST /lab/reset returns 200", status == 200, status)
    status, _ = call(base, "POST", "/lab/reset", headers={"X-Lab-Token": "wrong"})
    check("lab endpoints reject a wrong token with 403", status == 403, status)

    status, body = call(base, "POST", "/api/login", {"username": "lab_user", "password": "wrong"})
    check("wrong password returns 401", status == 401, (status, body))

    status, body = call(base, "POST", "/api/login", {"username": "lab_user", "password": "lab-user-pass"})
    user_token = body.get("token")
    check("lab_user can log in and gets a token", status == 200 and user_token, (status, body))

    status, body = call(base, "POST", "/api/login", {"username": "lab_restricted", "password": "lab-restricted-pass"})
    restricted_token = body.get("token")
    check("lab_restricted can log in and gets a token", status == 200 and restricted_token, (status, body))

    status, _ = call(base, "GET", "/api/orders")
    check("orders without a token returns 401", status == 401, status)
    status, body = call(base, "GET", "/api/orders", headers={"Authorization": f"Bearer {user_token}"})
    check("lab_user can read orders (200)", status == 200 and "orders" in body, (status, body))
    status, _ = call(base, "GET", "/api/orders", headers={"Authorization": f"Bearer {restricted_token}"})
    check("lab_restricted is denied orders (403)", status == 403, status)

    status, body = call(base, "GET", "/lab/rate-limit", headers=lab)
    check("rate limit is off at baseline", status == 200 and body.get("enabled") is False, (status, body))

    settings = {"enabled": True, "max_attempts": 3, "window_seconds": 60}
    status, body = call(base, "POST", "/lab/rate-limit", settings, headers=lab)
    check("rate limit can be switched on", status == 200 and body.get("enabled") is True, (status, body))

    codes = [call(base, "POST", "/api/login", {"username": "lab_user", "password": "wrong"})[0] for _ in range(5)]
    check("with the limit on: 3 attempts get 401, then 429", codes == [401, 401, 401, 429, 429], codes)

    status, _ = call(base, "POST", "/lab/reset", headers=lab)
    status2, body = call(base, "GET", "/lab/rate-limit", headers=lab)
    check("reset restores the baseline (limit off)", status == 200 and body.get("enabled") is False, (status, body))
    status, _ = call(base, "GET", "/api/orders", headers={"Authorization": f"Bearer {user_token}"})
    check("reset clears old tokens (401)", status == 401, status)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
