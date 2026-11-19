"""Benign (non-attack) traffic for the CloudAttackLab evaluation.

Normal use of the lab, sent the way a person would use the shop, plus normal
commands in the test container:

- browsing: the shop page `/`, the product list and single products;
- `lab_user` signing in with the right password and reading its orders;
- `lab_restricted` signing in with the right password and browsing products;
- now and then one wrong password (a typo) followed by the right one, at
  least `typo_gap_seconds` apart, so failed logins stay single and spread out;
- now and then one ordinary command in the test container (`date`, `ls /`,
  ...), never the scenario marker.

For the benign baseline (`NearMissPlan`) the failures are not random but
scheduled, so that each one stays just under a rule's threshold:

- 9 wrong logins within 60 s (the failed-login rule fires at 10 in 60 s);
- 4 access denials for `lab_restricted` on /api/orders (the rule fires at 5);
- single wrong passwords, each more than 60 s from any other failure;
- a few ordinary commands in the test container, without the scenario marker.

Every client, benign or not, reaches ShopCloud from the Docker gateway
address, so all failed logins count against one source IP: the plan keeps the
burst and the single typos more than 60 s apart, or they would add up to 10.

It talks to ShopCloud directly on its published port (like a browser) and to
the test container with `docker exec` (like an operator). It never calls the
platform API. Every action is logged with its time and result so the
evaluation can tell benign events from scenario events afterwards.

The test account passwords are the published lab seed values from
`shopcloud/shop/db.py`; the wrong password is deliberately not any real one.

Usable as a module (BenignTraffic, run in a thread) or on its own:

    python3 tools/benign_traffic.py --minutes 5 --out benign.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

SHOPCLOUD_URL = "http://127.0.0.1:8081"
# Published lab seed values (shopcloud/shop/db.py). Local lab only.
ACCOUNTS = {"lab_user": "lab-user-pass", "lab_restricted": "lab-restricted-pass"}
TYPO_PASSWORD = "benign-typo-not-a-real-password"
# Ordinary, read-only commands. None contains the scenario marker.
CONTAINER_COMMANDS = (
    ("date",),
    ("ls", "/"),
    ("cat", "/etc/os-release"),
    ("uptime",),
    ("df", "-h"),
    ("whoami",),
)
SCENARIO_MARKER = "cloudattacklab-scenario-marker"
TEST_TARGET_FILTER = ["--filter", "label=cloudattacklab.role=test-target",
                      "--filter", "label=com.docker.compose.project=cloudattacklab-lab"]


@dataclass
class Action:
    time: str            # UTC ISO time the action started
    kind: str            # browse | login | orders | typo | container_cmd | failed_login | denied
    username: Optional[str]
    target: str          # path or command
    status: Optional[int]  # HTTP status or the command's exit code; None on a transport error
    error: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BenignTraffic:
    base_url: str = SHOPCLOUD_URL
    seed: int = 1
    typo_users: tuple[str, ...] = ("lab_user", "lab_restricted")
    typo_gap_seconds: float = 40.0
    container_gap_seconds: float = 45.0
    pause_range: tuple[float, float] = (2.0, 4.0)
    container_commands: bool = True
    actions: list[Action] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_typo = 0.0
        self._last_container = 0.0
        self._lock = threading.Lock()

    # --- HTTP -------------------------------------------------------------
    def _http(self, method: str, path: str, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read()
                return response.status, raw, None
        except urllib.error.HTTPError as error:
            return error.code, error.read(), None
        except (urllib.error.URLError, OSError) as error:
            return None, b"", str(error)

    def _log(self, kind, username, target, status, error=None) -> None:
        with self._lock:
            self.actions.append(Action(_now(), kind, username, target, status, error))

    def _login(self, username: str, password: str, kind: str = "login") -> Optional[str]:
        status, raw, error = self._http("POST", "/api/login", {"username": username, "password": password})
        self._log(kind, username, "/api/login", status, error)
        if status == 200:
            try:
                return json.loads(raw).get("token")
            except ValueError:
                return None
        return None

    # --- actions ----------------------------------------------------------
    def browse(self) -> None:
        path = self.rng.choice(["/", "/api/products", f"/api/products/{self.rng.randint(1, 5)}"])
        status, _, error = self._http("GET", path)
        self._log("browse", None, path, status, error)

    def read_orders(self) -> None:
        token = self._login("lab_user", ACCOUNTS["lab_user"])
        if token:
            status, _, error = self._http("GET", "/api/orders", token=token)
            self._log("orders", "lab_user", "/api/orders", status, error)

    def restricted_browse(self) -> None:
        token = self._login("lab_restricted", ACCOUNTS["lab_restricted"])
        if token:
            status, _, error = self._http("GET", "/api/products", token=token)
            self._log("browse", "lab_restricted", "/api/products", status, error)

    def typo(self) -> None:
        """One wrong password, then the right one, as a person would."""
        user = self.rng.choice(self.typo_users)
        self._login(user, TYPO_PASSWORD, kind="typo")
        time.sleep(self.rng.uniform(1.0, 3.0))
        self._login(user, ACCOUNTS[user])
        self._last_typo = time.monotonic()

    def container_command(self) -> None:
        command = self.rng.choice(CONTAINER_COMMANDS)
        assert SCENARIO_MARKER not in " ".join(command)
        self._last_container = time.monotonic()
        try:
            ids = subprocess.run(["docker", "ps", "-q", *TEST_TARGET_FILTER],
                                 capture_output=True, text=True, timeout=20).stdout.split()
            if not ids:
                self._log("container_cmd", None, " ".join(command), None, "test container not running")
                return
            done = subprocess.run(["docker", "exec", ids[0], *command], capture_output=True, text=True, timeout=20)
            self._log("container_cmd", None, " ".join(command), done.returncode,
                      None if done.returncode == 0 else done.stderr.strip()[:200])
        except (OSError, subprocess.SubprocessError) as exc:
            self._log("container_cmd", None, " ".join(command), None, str(exc)[:200])

    def failed_login(self, username: str = "lab_user") -> None:
        """One wrong password, not followed by the right one (part of a near-miss burst)."""
        self._login(username, TYPO_PASSWORD, kind="failed_login")

    def denied_orders(self) -> None:
        """lab_restricted asks for /api/orders, which it may not read (ShopCloud answers 403)."""
        token = self._login("lab_restricted", ACCOUNTS["lab_restricted"])
        if token:
            status, _, error = self._http("GET", "/api/orders", token=token)
            self._log("denied", "lab_restricted", "/api/orders", status, error)

    def step(self) -> None:
        now = time.monotonic()
        if self.typo_users and now - self._last_typo >= self.typo_gap_seconds and self.rng.random() < 0.3:
            self.typo()
        elif self.container_commands and now - self._last_container >= self.container_gap_seconds and self.rng.random() < 0.3:
            self.container_command()
        else:
            self.rng.choices([self.browse, self.read_orders, self.restricted_browse], weights=[5, 3, 2])[0]()

    # --- running ----------------------------------------------------------
    def run_for(self, seconds: float) -> None:
        start = time.monotonic()
        # Start the gaps now, so the first typo/command is not in the first seconds.
        self._last_typo = self._last_container = start
        while not self._stop.is_set() and time.monotonic() - start < seconds:
            self.step()
            self._stop.wait(self.rng.uniform(*self.pause_range))

    def start(self, seconds: float = 24 * 3600) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_for, args=(seconds,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for action in self.actions:
            out[action.kind] = out.get(action.kind, 0) + 1
        return out

    def write_csv(self, path) -> None:
        with open(path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(Action.__dataclass_fields__))
            writer.writeheader()
            for action in self.actions:
                writer.writerow(asdict(action))


@dataclass
class NearMissPlan:
    """A fixed schedule of near-miss actions, run in its own thread next to the
    background traffic (which should then have no random typos or commands).

    Offsets are seconds from `start()`. The default plan fits in 560 s:

    - 30 s: 9 wrong logins as lab_user, 3 s apart (24 s; threshold is 10 in 60 s);
    - 130 to 550 s: 6 single typos (wrong then right password), 84 s apart,
      alternating lab_user and lab_restricted, the first 76 s after the burst;
    - 180 s: 4 denied /api/orders requests as lab_restricted, 4 s apart (threshold 5);
    - 90, 270, 450 s: one ordinary command in the test container.
    """

    traffic: BenignTraffic
    burst_at: float = 30.0
    burst_size: int = 9
    burst_gap: float = 3.0
    typo_times: tuple[float, ...] = (130.0, 214.0, 298.0, 382.0, 466.0, 550.0)
    denied_at: float = 180.0
    denied_count: int = 4
    denied_gap: float = 4.0
    command_times: tuple[float, ...] = (90.0, 270.0, 450.0)

    def schedule(self) -> list[tuple[float, str, object]]:
        t = self.traffic
        items: list[tuple[float, str, object]] = []
        items += [(self.burst_at + i * self.burst_gap, "failed_login", t.failed_login) for i in range(self.burst_size)]
        users = ("lab_user", "lab_restricted")
        for i, at in enumerate(self.typo_times):
            items.append((at, "typo", lambda u=users[i % 2]: self._typo(u)))
        items += [(self.denied_at + i * self.denied_gap, "denied", t.denied_orders) for i in range(self.denied_count)]
        items += [(at, "container_cmd", t.container_command) for at in self.command_times]
        return sorted(items, key=lambda item: item[0])

    def _typo(self, user: str) -> None:
        t = self.traffic
        t._login(user, TYPO_PASSWORD, kind="typo")
        time.sleep(t.rng.uniform(1.0, 3.0))
        t._login(user, ACCOUNTS[user])

    def last_offset(self) -> float:
        return max(at for at, _, _ in self.schedule())

    def run(self) -> None:
        start = time.monotonic()
        for at, _, action in self.schedule():
            wait = start + at - time.monotonic()
            if self.traffic._stop.wait(max(0.0, wait)):
                return
            action()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--url", default=SHOPCLOUD_URL)
    parser.add_argument("--no-container", action="store_true", help="skip the test-container commands")
    parser.add_argument("--out", default="benign_actions.csv")
    args = parser.parse_args()
    traffic = BenignTraffic(base_url=args.url, seed=args.seed, container_commands=not args.no_container)
    traffic.run_for(args.minutes * 60)
    traffic.write_csv(args.out)
    print(json.dumps(traffic.counts()))


if __name__ == "__main__":
    main()
