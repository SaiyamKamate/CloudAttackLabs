// ShopCloud page: list products, sign in, show "my orders".
// The token is kept in memory only (gone on reload). Text goes in with
// textContent, never innerHTML.
(function () {
  "use strict";
  let token = null;
  const $ = (id) => document.getElementById(id);
  const money = (n) => "$" + n.toFixed(2);

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  async function loadProducts() {
    const list = $("products");
    try {
      const res = await fetch("/api/products");
      const body = await res.json();
      list.replaceChildren();
      for (const p of body.products) {
        const item = el("li", undefined, "product");
        item.append(el("h3", p.name), el("p", p.description), el("p", money(p.price) + (p.in_stock ? "" : " · out of stock"), "price"));
        list.append(item);
      }
    } catch (e) {
      list.replaceChildren(el("li", "Could not load products.", "error"));
    }
  }

  async function showOrders(username) {
    $("login-box").hidden = true;
    $("account-box").hidden = false;
    $("who").textContent = username;
    const box = $("orders");
    const res = await fetch("/api/orders", { headers: { Authorization: "Bearer " + token } });
    if (res.status === 403) {
      box.replaceChildren(el("p", "This account is not allowed to view orders.", "muted"));
      return;
    }
    if (!res.ok) {
      box.replaceChildren(el("p", "Could not load orders (" + res.status + ").", "error"));
      return;
    }
    const { orders } = await res.json();
    if (orders.length === 0) {
      box.replaceChildren(el("p", "No orders yet.", "muted"));
      return;
    }
    const table = el("table");
    const head = el("tr");
    for (const h of ["#", "Item", "Qty", "Total", "Status", "Date"]) head.append(el("th", h));
    table.append(head);
    for (const o of orders) {
      const row = el("tr");
      for (const v of [o.id, o.item, o.quantity, money(o.total), o.status, o.created_at.slice(0, 10)]) row.append(el("td", String(v)));
      table.append(row);
    }
    box.replaceChildren(table);
  }

  async function login(event) {
    event.preventDefault();
    const form = event.target;
    const error = $("login-error");
    error.hidden = true;
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    form.password.value = "";
    if (!res.ok) {
      error.textContent = res.status === 429 ? "Too many attempts. Try again later." : "Wrong username or password.";
      error.hidden = false;
      return;
    }
    const body = await res.json();
    token = body.token;
    await showOrders(body.username);
  }

  function logout() {
    token = null;
    $("account-box").hidden = true;
    $("login-box").hidden = false;
    $("orders").replaceChildren();
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("login-form").addEventListener("submit", login);
    $("logout").addEventListener("click", logout);
    loadProducts();
  });
})();
