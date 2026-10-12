import sqlite3
from datetime import datetime, timezone

class Database:
    def __init__(self, path):
        self.path = path

    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        c = self.conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            language TEXT DEFAULT 'en'
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            link TEXT NOT NULL,
            sold INTEGER DEFAULT 0,
            added_by INTEGER,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS user_prices (
            username TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            price REAL NOT NULL,
            PRIMARY KEY(username, product_id)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            binance_order_id TEXT,
            checkout_url TEXT,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            failure_reason TEXT
        );
        """)
        c.commit(); c.close()

    def set_user_language(self, user_id, language, username=None):
        c=self.conn()
        c.execute(
            "INSERT INTO users(user_id,language,username) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET language=excluded.language, "
            "username=COALESCE(excluded.username, users.username)",
            (user_id,language,username)
        )
        c.commit(); c.close()

    def touch_username(self, user_id, username):
        if not username:
            return
        c=self.conn()
        c.execute(
            "INSERT INTO users(user_id,username) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
            (user_id, username)
        )
        c.commit(); c.close()

    def get_user_language(self, user_id):
        c=self.conn(); r=c.execute("SELECT language FROM users WHERE user_id=?",(user_id,)).fetchone(); c.close()
        return r["language"] if r else None

    def is_admin(self, uid):
        c=self.conn(); r=c.execute("SELECT 1 FROM admins WHERE user_id=?",(uid,)).fetchone(); c.close(); return bool(r)

    def add_admin(self, uid, added_by):
        c=self.conn(); c.execute("INSERT OR REPLACE INTO admins VALUES(?,?,?)",(uid,added_by,datetime.now(timezone.utc).isoformat())); c.commit(); c.close()

    def remove_admin(self, uid):
        c=self.conn(); c.execute("DELETE FROM admins WHERE user_id=?",(uid,)); c.commit(); c.close()

    def add_product_link(self, name, price, link, added_by):
        c=self.conn()
        r=c.execute("SELECT id FROM products WHERE name=? AND active=1",(name,)).fetchone()
        if r: pid=r["id"]
        else:
            pid=c.execute("INSERT INTO products(name,price) VALUES(?,?)",(name,price)).lastrowid
        c.execute("INSERT INTO stock(product_id,link,added_by,added_at) VALUES(?,?,?,?)",(pid,link,added_by,datetime.now(timezone.utc).isoformat()))
        c.commit(); c.close()

    def list_products(self):
        c=self.conn()
        rows=c.execute("""SELECT p.id,p.name,p.price,
        (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) stock
        FROM products p WHERE p.active=1 ORDER BY p.id""").fetchall()
        c.close(); return rows

    def get_product(self,pid):
        c=self.conn(); r=c.execute("""SELECT p.id,p.name,p.price,
        (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) stock
        FROM products p WHERE p.id=? AND p.active=1""",(pid,)).fetchone(); c.close(); return r

    def get_effective_price(self, uid, pid, default):
        c=self.conn()
        u=c.execute("SELECT username FROM users WHERE user_id=?",(uid,)).fetchone()
        price=None
        if u and u["username"]:
            r=c.execute("SELECT price FROM user_prices WHERE username=? AND product_id=?",(u["username"].lstrip("@"),pid)).fetchone()
            price=r["price"] if r else None
        c.close(); return price if price is not None else default

    def set_product_price(self,pid,price):
        c=self.conn(); c.execute("UPDATE products SET price=? WHERE id=?",(price,pid)); c.commit(); c.close()

    def set_user_price(self,username,pid,price):
        c=self.conn(); c.execute("INSERT OR REPLACE INTO user_prices VALUES(?,?,?)",(username,pid,price)); c.commit(); c.close()

    def create_order(self,uid,pid,qty,unit,amount,expires):
        import secrets
        oid="ORD-"+secrets.token_hex(5).upper()
        c=self.conn(); c.execute("INSERT INTO orders(id,user_id,product_id,quantity,unit_price,amount,status,binance_order_id,checkout_url,expires_at,created_at,paid_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid,uid,pid,qty,unit,amount,"pending",None,None,expires.isoformat(),datetime.now(timezone.utc).isoformat(),None))
        c.commit(); c.close(); return oid

    def attach_binance_order(self,oid,bid,url):
        c=self.conn(); c.execute("UPDATE orders SET binance_order_id=?,checkout_url=? WHERE id=?",(bid,url,oid)); c.commit(); c.close()

    def get_order(self,oid):
        c=self.conn(); r=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); c.close(); return r

    def mark_order_failed(self,oid,reason):
        c=self.conn(); c.execute("UPDATE orders SET status='failed',failure_reason=? WHERE id=? AND status='pending'",(reason,oid)); c.commit(); c.close()

    def fulfill_order(self,oid):
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        o=c.execute("SELECT * FROM orders WHERE id=? AND status='pending'",(oid,)).fetchone()
        if not o:
            c.rollback(); c.close(); return None
        links=c.execute("SELECT id,link FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?",(o["product_id"],o["quantity"])).fetchall()
        if len(links) != o["quantity"]:
            c.rollback(); c.close(); return None
        for x in links:
            c.execute("UPDATE stock SET sold=1 WHERE id=?",(x["id"],))
        c.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),oid))
        c.commit(); c.close()
        return [x["link"] for x in links]

    def expire_orders(self, now):
        c=self.conn()
        rows=c.execute("SELECT o.*,u.language FROM orders o LEFT JOIN users u ON u.user_id=o.user_id WHERE o.status='pending' AND o.expires_at<=?",(now.isoformat(),)).fetchall()
        c.execute("UPDATE orders SET status='failed',failure_reason='timeout' WHERE status='pending' AND expires_at<=?",(now.isoformat(),))
        c.commit(); c.close(); return rows

    def payment_stats(self):
        c=self.conn()
        r=c.execute("""SELECT COUNT(*) total,
        SUM(status='paid') paid,SUM(status='pending') pending,SUM(status='failed') failed,
        COALESCE(SUM(CASE WHEN status='paid' THEN amount ELSE 0 END),0) revenue FROM orders""").fetchone()
        c.close()
        return {k: (r[k] or 0) for k in r.keys()}

    def user_orders(self, uid, limit=8):
        c=self.conn()
        rows=c.execute("""SELECT o.id,o.quantity,o.amount,o.status,p.name FROM orders o
        LEFT JOIN products p ON p.id=o.product_id WHERE o.user_id=? AND (o.status!='failed' OR o.failure_reason='timeout')
        ORDER BY o.created_at DESC LIMIT ?""",(uid,limit)).fetchall()
        c.close(); return rows
