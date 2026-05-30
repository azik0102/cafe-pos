server.py
#!/usr/bin/env python3
"""
☕ CAFE POS SERVER
Hech nima o'rnatmasdan ishlaydi — faqat Python3 kerak
"""
import http.server
import sqlite3
import json
import threading
import time
import os
import sys
import socket
from urllib.parse import urlparse, parse_qs

DB_FILE = os.path.join(os.path.dirname(__file__), "cafe.db")
PORT = int(os.environ.get("PORT", 3000))

# ─── DATABASE ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS menu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'Boshqa',
            price INTEGER NOT NULL,
            available INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            waiter_id TEXT,
            waiter_name TEXT,
            table_id TEXT,
            items TEXT DEFAULT '[]',
            comment TEXT DEFAULT '',
            status TEXT DEFAULT 'yangi',
            total INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%H:%M', 'now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'bosh',
            waiter TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT DEFAULT 'kg',
            quantity REAL DEFAULT 0,
            min_level REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT 'boshqa',
            created_at TEXT DEFAULT (date('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at REAL DEFAULT (julianday('now'))
        );
    """)

    # Seed data
    if c.execute("SELECT COUNT(*) FROM menu").fetchone()[0] == 0:
        menu_items = [
            ("Qora kofe","Ichimlik",15000),("Kapuchino","Ichimlik",22000),
            ("Latte","Ichimlik",25000),("Choy","Ichimlik",10000),
            ("Cesar salat","Salat",45000),("Olivye","Salat",35000),
            ("Shashlik","Asosiy",85000),("Lag'mon","Asosiy",55000),
            ("Plov","Asosiy",60000),("Non","Boshqa",5000),
            ("Mineral suv","Ichimlik",8000),("Limonad","Ichimlik",18000),
        ]
        c.executemany("INSERT INTO menu (name,category,price) VALUES (?,?,?)", menu_items)

    if c.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 0:
        c.executemany("INSERT INTO tables (id) VALUES (?)", [(i,) for i in range(1,13)])

    if c.execute("SELECT COUNT(*) FROM inventory").fetchone()[0] == 0:
        inv = [
            ("Kofe donlari","kg",15,5),("Sut","l",30,10),
            ("Shakar","kg",8,3),("Un","kg",25,10),
            ("Go'sht","kg",12,5),("Sabzavot","kg",20,8),
        ]
        c.executemany("INSERT INTO inventory (name,unit,quantity,min_level) VALUES (?,?,?,?)", inv)

    conn.commit()
    conn.close()

def push_event(etype, data):
    conn = get_db()
    conn.execute("INSERT INTO events (type,data) VALUES (?,?)", (etype, json.dumps(data, ensure_ascii=False)))
    conn.execute("DELETE FROM events WHERE created_at < julianday('now') - 0.01")  # 15 daqiqadan eski
    conn.commit()
    conn.close()

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ─── HTTP SERVER ──────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Konsolni tozalab turish

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, path):
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_html(os.path.join(os.path.dirname(__file__), "index.html"))
            return

        conn = get_db()

        if path == "/api/menu":
            self.send_json(rows_to_list(conn.execute("SELECT * FROM menu").fetchall()))

        elif path == "/api/orders":
            rows = rows_to_list(conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall())
            for r in rows:
                r["items"] = json.loads(r["items"] or "[]")
            self.send_json(rows)

        elif path == "/api/tables":
            self.send_json(rows_to_list(conn.execute("SELECT * FROM tables").fetchall()))

        elif path == "/api/inventory":
            self.send_json(rows_to_list(conn.execute("SELECT * FROM inventory").fetchall()))

        elif path == "/api/transactions":
            self.send_json(rows_to_list(conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 300").fetchall()))

        elif path == "/api/poll":
            # Long polling — yangi eventlarni kutish
            since_id = int(qs.get("since", ["0"])[0])
            deadline = time.time() + 20  # 20 soniya kutish
            while time.time() < deadline:
                rows = rows_to_list(conn.execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT 20", (since_id,)
                ).fetchall())
                if rows:
                    for r in rows:
                        r["data"] = json.loads(r["data"])
                    self.send_json({"events": rows, "last_id": rows[-1]["id"]})
                    conn.close()
                    return
                time.sleep(0.5)
                conn.close()
                conn = get_db()
            self.send_json({"events": [], "last_id": since_id})

        elif path == "/api/state":
            # To'liq holat — dastlabki yuklash uchun
            orders = rows_to_list(conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 200").fetchall())
            for o in orders:
                o["items"] = json.loads(o["items"] or "[]")
            self.send_json({
                "menu": rows_to_list(conn.execute("SELECT * FROM menu").fetchall()),
                "orders": orders,
                "tables": rows_to_list(conn.execute("SELECT * FROM tables").fetchall()),
                "inventory": rows_to_list(conn.execute("SELECT * FROM inventory").fetchall()),
                "transactions": rows_to_list(conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 300").fetchall()),
            })

        else:
            self.send_response(404)
            self.end_headers()

        conn.close()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        conn = get_db()

        if path == "/api/orders":
            items_json = json.dumps(body.get("items", []), ensure_ascii=False)
            table_id = str(body.get("tableId", ""))
            cur = conn.execute(
                "INSERT INTO orders (waiter_id,waiter_name,table_id,items,comment,total,status) VALUES (?,?,?,?,?,?,'yangi')",
                (body["waiterId"], body["waiterName"], table_id,
                 items_json, body.get("comment",""), body.get("total",0))
            )
            oid = cur.lastrowid
            if table_id != "yetkazish":
                conn.execute("UPDATE tables SET status='band', waiter=? WHERE id=?",
                             (body["waiterName"], int(table_id)))
            conn.commit()
            order = dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
            order["items"] = json.loads(order["items"])
            push_event("new_order", order)
            push_event("tables_updated", rows_to_list(conn.execute("SELECT * FROM tables").fetchall()))
            self.send_json(order)

        elif path == "/api/menu":
            cur = conn.execute("INSERT INTO menu (name,category,price) VALUES (?,?,?)",
                               (body["name"], body.get("category","Boshqa"), body["price"]))
            conn.commit()
            item = dict(conn.execute("SELECT * FROM menu WHERE id=?", (cur.lastrowid,)).fetchone())
            push_event("menu_updated", rows_to_list(conn.execute("SELECT * FROM menu").fetchall()))
            self.send_json(item)

        elif path == "/api/inventory":
            cur = conn.execute("INSERT INTO inventory (name,unit,quantity,min_level) VALUES (?,?,?,?)",
                               (body["name"], body.get("unit","kg"), body.get("quantity",0), body.get("minLevel",0)))
            conn.commit()
            item = dict(conn.execute("SELECT * FROM inventory WHERE id=?", (cur.lastrowid,)).fetchone())
            push_event("inventory_updated", rows_to_list(conn.execute("SELECT * FROM inventory").fetchall()))
            self.send_json(item)

        elif path == "/api/transactions":
            cur = conn.execute("INSERT INTO transactions (type,amount,description,category) VALUES (?,?,?,?)",
                               (body["type"], body["amount"], body.get("description",""), body.get("category","boshqa")))
            conn.commit()
            tx = dict(conn.execute("SELECT * FROM transactions WHERE id=?", (cur.lastrowid,)).fetchone())
            push_event("transactions_updated", rows_to_list(conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 300").fetchall()))
            self.send_json(tx)

        else:
            self.send_response(404)
            self.end_headers()

        conn.close()

    def do_PATCH(self):
        path = urlparse(self.path).path
        body = self.read_body()
        conn = get_db()

        parts = path.split("/")

        if len(parts) == 5 and parts[2] == "orders" and parts[4] == "status":
            oid = int(parts[3])
            status = body["status"]
            conn.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
            if status == "tulangan":
                order = dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
                tid = order["table_id"]
                if tid != "yetkazish":
                    conn.execute("UPDATE tables SET status='bosh', waiter=NULL WHERE id=?", (int(tid),))
                    push_event("tables_updated", rows_to_list(conn.execute("SELECT * FROM tables").fetchall()))
                conn.execute("INSERT INTO transactions (type,amount,description,category) VALUES ('kirim',?,?,'savdo')",
                             (order["total"], f"Buyurtma #{oid} (Stol #{tid})"))
                push_event("transactions_updated", rows_to_list(conn.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 300").fetchall()))
            conn.commit()
            order = dict(conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
            order["items"] = json.loads(order["items"] or "[]")
            push_event("order_updated", order)
            if status == "tayyor":
                push_event("order_ready", {"orderId": oid, "waiterId": order["waiter_id"], "tableId": order["table_id"]})
            self.send_json(order)

        elif len(parts) == 4 and parts[2] == "menu":
            mid = int(parts[3])
            conn.execute("UPDATE menu SET available=? WHERE id=?", (1 if body.get("available") else 0, mid))
            conn.commit()
            push_event("menu_updated", rows_to_list(conn.execute("SELECT * FROM menu").fetchall()))
            self.send_json({"ok": True})

        elif len(parts) == 4 and parts[2] == "inventory":
            iid = int(parts[3])
            conn.execute("UPDATE inventory SET quantity=? WHERE id=?", (body["quantity"], iid))
            conn.commit()
            push_event("inventory_updated", rows_to_list(conn.execute("SELECT * FROM inventory").fetchall()))
            self.send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

        conn.close()

# ─── START ────────────────────────────────────────────────────────────────────
def get_local_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip.startswith(("192.","10.","172.")) and ":" not in ip:
                if ip not in ips:
                    ips.append(ip)
    except:
        pass
    return ips

if __name__ == "__main__":
    init_db()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

    print("\n╔═══════════════════════════════════════════╗")
    print("║          ☕  CAFE POS SERVER               ║")
    print("╠═══════════════════════════════════════════╣")
    print(f"║  Port: {PORT}                                  ║")
    for ip in get_local_ips():
        line = f"║  🌐 http://{ip}:{PORT}"
        print(line + " " * (44 - len(line)) + "║")
    print(f"║  💻 http://localhost:{PORT}                   ║")
    print("╠═══════════════════════════════════════════╣")
    print("║  Telefondan: yuqoridagi 🌐 manzilni oling ║")
    print("║  To'xtatish: Ctrl+C                       ║")
    print("╚═══════════════════════════════════════════╝\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Server to'xtatildi.")
