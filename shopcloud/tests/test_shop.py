"""What ShopCloud adds beyond the interface: data, products, the page, reset of the data."""
import sqlite3

from conftest import LAB, token_for


def db(app):
    conn = sqlite3.connect(app.state.db.path)
    conn.row_factory = sqlite3.Row
    return conn


def test_seed_data_is_there_and_passwords_are_hashed(app):
    with db(app) as conn:
        users = {r["username"]: r for r in conn.execute("SELECT * FROM users")}
        assert set(users) == {"lab_user", "lab_restricted"}
        assert users["lab_user"]["role"] == "user" and users["lab_restricted"]["role"] == "restricted"
        for row in users.values():
            assert row["password_hash"].startswith("scrypt$")
            assert "pass" not in row["password_hash"]
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5
        owners = {r[0] for r in conn.execute("SELECT user_id FROM orders")}
        assert owners == {users["lab_user"]["id"]}


def test_only_a_hash_of_each_token_is_stored(app, client):
    token = token_for(client)
    with db(app) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert len(stored) == 1 and stored[0] != token and token not in stored[0]


def test_expired_sessions_are_refused(app, client):
    token = token_for(client)
    with db(app) as conn:
        conn.execute("UPDATE sessions SET expires_at = 0")
    assert client.get("/api/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_products_list_and_detail(client, logs):
    body = client.get("/api/products").json()
    assert [p["name"] for p in body["products"]][:2] == ["Mechanical Keyboard", "Wireless Mouse"]
    assert body["products"][0] == {
        "id": 1, "sku": "KB-100", "name": "Mechanical Keyboard",
        "description": "Tenkeyless keyboard with brown switches.", "price": 49.0, "in_stock": True,
    }
    assert client.get("/api/products/3").json()["name"] == "27-inch Monitor"
    for missing in ("99", "abc", "-1", "1e3", "99999999999999"):
        response = client.get(f"/api/products/{missing}")
        assert response.status_code == 404 and response.json() == {"error": "not_found"}, missing

    events = [(r["event"], r["status"], r["level"]) for r in logs()]
    assert events[:2] == [("products_listed", 200, "info"), ("product_viewed", 200, "info")]
    assert set(events[2:]) == {("product_not_found", 404, "info")}


def test_web_page_is_served_logged_and_has_a_strict_csp(client, logs):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "ShopCloud" in response.text and 'id="login-form"' in response.text and "My orders" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/static/shop.js").status_code == 200
    assert [r["event"] for r in logs()] == ["page_viewed"]  # static files are not logged


def test_unknown_paths_and_methods_are_logged(client, logs):
    assert client.get("/api/nothing").status_code == 404
    assert client.get("/lab/nothing").status_code == 404
    assert client.delete("/api/orders").status_code == 405
    assert [(r["event"], r["status"], r["level"]) for r in logs()] == [
        ("not_found", 404, "warning"), ("not_found", 404, "warning"), ("method_not_allowed", 405, "warning"),
    ]


def test_lab_rate_limit_read_and_rejected_are_logged(client, logs):
    client.get("/lab/rate-limit", headers=LAB)
    client.post("/lab/rate-limit", json={"enabled": "x"}, headers=LAB)
    assert [(r["event"], r["status"]) for r in logs()] == [("rate_limit_read", 200), ("rate_limit_rejected", 422)]


def test_reset_restores_the_seeded_data(app, client):
    before_orders = client.get("/api/orders", headers={"Authorization": f"Bearer {token_for(client)}"}).json()
    before_products = client.get("/api/products").json()
    with db(app) as conn:  # drift away from the baseline
        conn.execute("DELETE FROM orders WHERE id = 3")
        conn.execute("UPDATE products SET price_cents = 1, stock = 0 WHERE id = 1")
        conn.execute("INSERT INTO products VALUES (99, 'X', 'Extra', 'Not seed data', 100, 1)")
        conn.execute("UPDATE users SET role = 'restricted' WHERE username = 'lab_user'")

    assert client.post("/lab/reset", headers=LAB).status_code == 200

    with db(app) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert client.get("/api/products").json() == before_products
    after = client.get("/api/orders", headers={"Authorization": f"Bearer {token_for(client)}"}).json()
    assert after == before_orders


def test_start_up_seeds_the_baseline_even_over_an_old_database(tmp_path):
    from shop.main import create_app

    path = str(tmp_path / "shop.db")
    first = create_app(db_path=path, lab_token="t")
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM orders")
    create_app(db_path=path, lab_token="t")  # a new container starts at the baseline
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
    assert first.state.db.path == path
