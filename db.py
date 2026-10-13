import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone

DEFAULT_REFERRAL_PERCENT = float(os.getenv("REFERRAL_PERCENT", "3"))  # used until the admin sets one

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
        CREATE TABLE IF NOT EXISTS deposits (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT,
            status TEXT DEFAULT 'pending',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            paid_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status INTEGER DEFAULT 0,   -- 0 waiting, 1 sent, 2 gave up
            tries INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ticket_msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender TEXT NOT NULL,       -- 'user' or 'admin'
            text TEXT,
            photo_id TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ticket_links (
            chat_id INTEGER NOT NULL,
            msg_id INTEGER NOT NULL,
            ticket_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, msg_id)
        );
        """)
        self._migrate(c)
        c.commit(); c.close()

    def _migrate(self, c):
        def cols(table):
            return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        add = {
            "users": [("balance", "REAL DEFAULT 0"), ("spent", "REAL DEFAULT 0"),
                      ("ref_earned", "REAL DEFAULT 0"), ("referred_by", "INTEGER"),
                      ("created_at", "TEXT"), ("ref_code", "TEXT")],
            "products": [("description", "TEXT"), ("delivery", "TEXT"), ("warranty", "TEXT"),
                         ("category", "TEXT"), ("old_price", "REAL"), ("kind", "TEXT DEFAULT 'auto'"),
                         ("prompt", "TEXT")],
            "orders": [("paid_with", "TEXT DEFAULT 'crypto'"), ("contact", "TEXT"), ("admin_note", "TEXT")],
        }
        for table, columns in add.items():
            have = cols(table)
            for name, ddl in columns:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        c.execute("UPDATE users SET created_at=? WHERE created_at IS NULL", (self._now(),))

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def ensure_user(self, user_id, username=None):
        """Create the user row if missing. Returns True when the user is new."""
        c=self.conn()
        cur=c.execute("INSERT OR IGNORE INTO users(user_id,username,language,created_at) VALUES(?,?,NULL,?)",
                      (user_id,username,self._now()))
        created = cur.rowcount == 1
        if username:
            c.execute("UPDATE users SET username=? WHERE user_id=?", (username,user_id))
        c.commit(); c.close()
        return created

    def set_user_language(self, user_id, language, username=None):
        self.ensure_user(user_id, username)
        c=self.conn()
        c.execute("UPDATE users SET language=? WHERE user_id=?", (language,user_id))
        c.commit(); c.close()

    def touch_username(self, user_id, username):
        self.ensure_user(user_id, username)

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
        rows=c.execute(f"""SELECT p.id,p.name,p.price,p.description,p.delivery,p.warranty,p.category,p.old_price,p.kind,p.prompt,
        (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) stock
        FROM products p WHERE p.active=1 ORDER BY p.id""").fetchall()
        c.close(); return rows

    def get_product(self,pid):
        c=self.conn(); r=c.execute("""SELECT p.id,p.name,p.price,p.description,p.delivery,p.warranty,p.category,p.old_price,p.kind,p.prompt,
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
        c.execute("UPDATE orders SET status='paid',paid_at=?,paid_with='crypto' WHERE id=?",(datetime.now(timezone.utc).isoformat(),oid))
        self._credit_spend(c, o["user_id"], o["amount"])
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

    # ------------------------------------------------------------ wallet
    def _credit_spend(self, c, uid, amount):
        """Add to the user's lifetime spend and pay the referral commission (same transaction)."""
        c.execute("UPDATE users SET spent=ROUND(COALESCE(spent,0)+?,2) WHERE user_id=?", (amount, uid))
        r = c.execute("SELECT referred_by FROM users WHERE user_id=?", (uid,)).fetchone()
        pct = self._setting(c, "referral_percent", DEFAULT_REFERRAL_PERCENT)
        if r and r["referred_by"] and pct > 0:
            bonus = round(amount * pct / 100, 2)
            if bonus > 0:
                c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2), ref_earned=ROUND(COALESCE(ref_earned,0)+?,2) "
                          "WHERE user_id=?", (bonus, bonus, r["referred_by"]))

    def get_account(self, uid):
        c=self.conn(); r=c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone(); c.close()
        return r

    def set_referrer(self, uid, referrer_id):
        """Only for brand-new users; never self-referral; referrer must exist."""
        if uid == referrer_id:
            return False
        c=self.conn()
        ok = c.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,)).fetchone()
        changed = False
        if ok:
            changed = c.execute("UPDATE users SET referred_by=? WHERE user_id=? AND referred_by IS NULL",
                                (referrer_id, uid)).rowcount == 1
            c.commit()
        c.close()
        return changed

    def referral_count(self, uid):
        c=self.conn(); n=c.execute("SELECT COUNT(*) n FROM users WHERE referred_by=?", (uid,)).fetchone()["n"]; c.close()
        return n

    def add_balance(self, uid, amount):
        self.ensure_user(uid)
        c=self.conn()
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (amount, uid))
        c.commit(); c.close()

    def buy_with_balance(self, uid, pid, qty, unit):
        """Pay an order from the wallet. Returns (order_id, items) or an error string:
        'balance' (not enough funds) / 'stock' (not enough stock)."""
        amount = round(unit * qty, 2)
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        u=c.execute("SELECT COALESCE(balance,0) b FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u or u["b"] + 1e-9 < amount:
            c.rollback(); c.close(); return "balance"
        links=c.execute("SELECT id,link FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?",(pid,qty)).fetchall()
        if len(links) != qty:
            c.rollback(); c.close(); return "stock"
        for x in links:
            c.execute("UPDATE stock SET sold=1 WHERE id=?", (x["id"],))
        oid="ORD-"+secrets.token_hex(5).upper()
        now=self._now()
        c.execute("INSERT INTO orders(id,user_id,product_id,quantity,unit_price,amount,status,expires_at,created_at,paid_at,paid_with) "
                  "VALUES(?,?,?,?,?,?,'paid',?,?,?,'balance')", (oid,uid,pid,qty,unit,amount,now,now,now))
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)-?,2) WHERE user_id=?", (amount, uid))
        self._credit_spend(c, uid, amount)
        c.commit(); c.close()
        return oid, [x["link"] for x in links]

    def create_deposit(self, uid, amount, method, expires):
        did="DEP-"+secrets.token_hex(5).upper()
        c=self.conn()
        c.execute("INSERT INTO deposits(id,user_id,amount,method,status,expires_at,created_at) VALUES(?,?,?,?,'pending',?,?)",
                  (did,uid,amount,method,expires.isoformat(),self._now()))
        c.commit(); c.close(); return did

    def get_deposit(self, did):
        c=self.conn(); r=c.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone(); c.close(); return r

    def pending_deposits(self):
        c=self.conn(); rows=c.execute("SELECT * FROM deposits WHERE status='pending'").fetchall(); c.close(); return rows

    def fail_deposit(self, did):
        c=self.conn()
        c.execute("UPDATE deposits SET status='failed' WHERE id=? AND status='pending'", (did,))
        c.commit(); c.close()

    def credit_deposit(self, did):
        """Mark a verified deposit paid and credit the wallet once. Returns the amount or None."""
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        d=c.execute("SELECT * FROM deposits WHERE id=? AND status='pending'", (did,)).fetchone()
        if not d:
            c.rollback(); c.close(); return None
        c.execute("UPDATE deposits SET status='paid',paid_at=? WHERE id=?", (self._now(), did))
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (d["amount"], d["user_id"]))
        c.commit(); c.close()
        return d["amount"]

    def expire_deposits(self, now):
        c=self.conn()
        rows=c.execute("SELECT * FROM deposits WHERE status='pending' AND expires_at<=?", (now.isoformat(),)).fetchall()
        c.execute("UPDATE deposits SET status='failed' WHERE status='pending' AND expires_at<=?", (now.isoformat(),))
        c.commit(); c.close(); return rows

    def user_transactions(self, uid, limit=10):
        c=self.conn()
        rows=c.execute("""
        SELECT 'deposit' kind, amount, paid_at ts, id FROM deposits WHERE user_id=? AND status='paid'
        UNION ALL
        SELECT CASE status WHEN 'refunded' THEN 'refund' ELSE 'purchase' END kind, amount,
               COALESCE(paid_at, created_at) ts, id
        FROM orders WHERE user_id=? AND status IN ('paid','processing','refunded')
        ORDER BY ts DESC LIMIT ?""", (uid,uid,limit)).fetchall()
        c.close(); return rows

    # ------------------------------------------------------------ settings
    def _setting(self, c, key, default):
        r=c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not r or r["value"] is None:
            return default
        try:
            return type(default)(r["value"])
        except (TypeError, ValueError):
            return default

    def get_setting(self, key, default):
        c=self.conn(); v=self._setting(c, key, default); c.close(); return v

    def set_setting(self, key, value):
        c=self.conn()
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))
        c.commit(); c.close()

    def referral_percent(self):
        return self.get_setting("referral_percent", DEFAULT_REFERRAL_PERCENT)

    # ------------------------------------------------------------ referral codes
    def get_ref_code(self, uid):
        self.ensure_user(uid)
        c=self.conn()
        r=c.execute("SELECT ref_code FROM users WHERE user_id=?", (uid,)).fetchone()
        code=r["ref_code"] if r else None
        while not code:
            cand="".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
            if not c.execute("SELECT 1 FROM users WHERE ref_code=?", (cand,)).fetchone():
                c.execute("UPDATE users SET ref_code=? WHERE user_id=?", (cand, uid)); c.commit(); code=cand
        c.close(); return code

    def user_by_ref_code(self, code):
        c=self.conn(); r=c.execute("SELECT user_id FROM users WHERE ref_code=?", (code.upper(),)).fetchone(); c.close()
        return r["user_id"] if r else None

    # ------------------------------------------------------------ products (admin)
    PRODUCT_FIELDS = ("name", "price", "old_price", "category", "description", "delivery", "warranty", "kind", "prompt")

    def add_product(self, name, price, kind="auto"):
        c=self.conn()
        r=c.execute("SELECT id FROM products WHERE name=? AND active=1", (name,)).fetchone()
        if r:
            c.close(); return r["id"]
        pid=c.execute("INSERT INTO products(name,price,kind) VALUES(?,?,?)", (name, price, kind)).lastrowid
        c.commit(); c.close(); return pid

    def update_product(self, pid, **fields):
        fields={k:v for k,v in fields.items() if k in self.PRODUCT_FIELDS}
        if not fields:
            return False
        sets=", ".join(f"{k}=?" for k in fields)
        c=self.conn()
        cur=c.execute(f"UPDATE products SET {sets} WHERE id=?", (*fields.values(), pid))
        c.commit(); c.close(); return cur.rowcount == 1

    def set_product_info(self, pid, field, value):
        if field not in ("description", "delivery", "warranty"):
            return False
        return self.update_product(pid, **{field: value})

    def list_categories(self):
        c=self.conn()
        rows=c.execute("SELECT DISTINCT category FROM products WHERE active=1 AND category IS NOT NULL AND TRIM(category)!='' ORDER BY category").fetchall()
        c.close(); return [r["category"] for r in rows]

    def search_products(self, q, limit=10):
        c=self.conn()
        like="%"+q.replace("%","").replace("_","")+"%"
        rows=c.execute(f"""SELECT {self._PCOLS},
        (SELECT COUNT(*) FROM stock s WHERE s.product_id=p.id AND s.sold=0) stock
        FROM products p WHERE p.active=1 AND (p.name LIKE ? OR p.category LIKE ?) ORDER BY p.id LIMIT ?""",
        (like, like, limit)).fetchall()
        c.close(); return rows

    _PCOLS = "p.id,p.name,p.price,p.description,p.delivery,p.warranty,p.category,p.old_price,p.kind,p.prompt"

    # ------------------------------------------------------------ made-to-order (manual) orders
    def buy_manual(self, uid, pid, unit, contact):
        """Hold the money and queue a manual order. Returns the order id, or 'balance'."""
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        u=c.execute("SELECT COALESCE(balance,0) b FROM users WHERE user_id=?", (uid,)).fetchone()
        if not u or u["b"] + 1e-9 < unit:
            c.rollback(); c.close(); return "balance"
        oid="ORD-"+secrets.token_hex(5).upper()
        now=self._now()
        c.execute("INSERT INTO orders(id,user_id,product_id,quantity,unit_price,amount,status,expires_at,created_at,paid_with,contact) "
                  "VALUES(?,?,?,?,?,?,'processing',?,?,'balance',?)", (oid,uid,pid,1,unit,unit,now,now,contact))
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)-?,2) WHERE user_id=?", (unit, uid))
        c.commit(); c.close(); return oid

    def pending_manual(self):
        c=self.conn()
        rows=c.execute("""SELECT o.*, p.name AS product, u.username AS username FROM orders o
        LEFT JOIN products p ON p.id=o.product_id LEFT JOIN users u ON u.user_id=o.user_id
        WHERE o.status='processing' ORDER BY o.created_at""").fetchall()
        c.close(); return rows

    def complete_manual(self, oid, note=None):
        """Mark a processing order done (once). Returns the order row or None."""
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        o=c.execute("SELECT * FROM orders WHERE id=? AND status='processing'", (oid,)).fetchone()
        if not o:
            c.rollback(); c.close(); return None
        c.execute("UPDATE orders SET status='paid',paid_at=?,admin_note=? WHERE id=?", (self._now(), note, oid))
        self._credit_spend(c, o["user_id"], o["amount"])
        c.commit(); c.close(); return o

    def reject_manual(self, oid, note=None):
        """Refund a processing order to the wallet (once). Returns the order row or None."""
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        o=c.execute("SELECT * FROM orders WHERE id=? AND status='processing'", (oid,)).fetchone()
        if not o:
            c.rollback(); c.close(); return None
        c.execute("UPDATE orders SET status='refunded',paid_at=?,admin_note=? WHERE id=?", (self._now(), note, oid))
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (o["amount"], o["user_id"]))
        c.commit(); c.close(); return o

    # ------------------------------------------------------------ outbox (site -> customer, sent once)
    def queue_message(self, uid, text):
        c=self.conn()
        c.execute("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)", (uid, text, self._now()))
        c.commit(); c.close()

    def outbox_claim(self, limit=20):
        """Atomically claim waiting messages; each message is handed out exactly once."""
        c=self.conn()
        c.execute("BEGIN IMMEDIATE")
        rows=c.execute("SELECT * FROM outbox WHERE status=0 ORDER BY id LIMIT ?", (limit,)).fetchall()
        for r in rows:
            c.execute("UPDATE outbox SET status=1 WHERE id=?", (r["id"],))
        c.commit(); c.close(); return rows

    def outbox_failed(self, oid, max_tries=3):
        """Sending failed: put it back for another try, or give up after max_tries."""
        c=self.conn()
        c.execute("UPDATE outbox SET tries=tries+1, status=CASE WHEN tries+1>=? THEN 2 ELSE 0 END WHERE id=?",
                  (max_tries, oid))
        c.commit(); c.close()

    # ------------------------------------------------------------ users (admin)
    def find_users(self, q, limit=20):
        c=self.conn()
        q=(q or "").strip().lstrip("@")
        if q.isdigit():
            rows=c.execute("SELECT * FROM users WHERE user_id=?", (int(q),)).fetchall()
        elif q:
            rows=c.execute("SELECT * FROM users WHERE username LIKE ? ORDER BY created_at DESC LIMIT ?", ("%"+q+"%", limit)).fetchall()
        else:
            rows=c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        c.close(); return rows

    def admin_topup(self, uid, amount):
        """Add (or, if negative, remove) wallet money by hand; shows up in the user's transactions."""
        self.ensure_user(uid)
        c=self.conn()
        now=self._now()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (amount, uid))
        if amount > 0:
            c.execute("INSERT INTO deposits(id,user_id,amount,method,status,expires_at,created_at,paid_at) VALUES(?,?,?,?,'paid',?,?,?)",
                      ("ADM-"+secrets.token_hex(4).upper(), uid, amount, "admin", now, now, now))
        c.commit(); c.close()

    # ------------------------------------------------------------ support tickets
    def create_ticket(self, uid, kind, text, photo_id=None):
        c=self.conn(); now=self._now()
        tid=c.execute("INSERT INTO tickets(user_id,kind,created_at) VALUES(?,?,?)", (uid, kind, now)).lastrowid
        c.execute("INSERT INTO ticket_msgs(ticket_id,sender,text,photo_id,created_at) VALUES(?,?,?,?,?)",
                  (tid, "user", text, photo_id, now))
        c.commit(); c.close(); return tid

    def add_ticket_msg(self, tid, sender, text):
        c=self.conn()
        c.execute("INSERT INTO ticket_msgs(ticket_id,sender,text,created_at) VALUES(?,?,?,?)", (tid, sender, text, self._now()))
        c.commit(); c.close()

    def link_ticket_msg(self, chat_id, msg_id, tid):
        c=self.conn()
        c.execute("INSERT OR REPLACE INTO ticket_links(chat_id,msg_id,ticket_id) VALUES(?,?,?)", (chat_id, msg_id, tid))
        c.commit(); c.close()

    def ticket_from_link(self, chat_id, msg_id):
        c=self.conn(); r=c.execute("SELECT ticket_id FROM ticket_links WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)).fetchone(); c.close()
        return r["ticket_id"] if r else None

    def get_ticket(self, tid):
        c=self.conn(); r=c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone(); c.close(); return r

    def ticket_msgs(self, tid):
        c=self.conn(); rows=c.execute("SELECT * FROM ticket_msgs WHERE ticket_id=? ORDER BY id", (tid,)).fetchall(); c.close(); return rows

    def user_tickets(self, uid, limit=5):
        c=self.conn(); rows=c.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall(); c.close(); return rows

    def all_tickets(self, status=None, limit=100):
        c=self.conn()
        sql="""SELECT t.*, u.username FROM tickets t LEFT JOIN users u ON u.user_id=t.user_id"""
        args=()
        if status:
            sql+=" WHERE t.status=?"; args=(status,)
        rows=c.execute(sql+" ORDER BY t.id DESC LIMIT ?", (*args, limit)).fetchall()
        c.close(); return rows

    def set_ticket_status(self, tid, status):
        c=self.conn(); c.execute("UPDATE tickets SET status=? WHERE id=?", (status, tid)); c.commit(); c.close()
