"""Growth & retention features (database layer).

Mixed into db.Database, so every shop (main + each seller) gets them with its own data.

Features: bulk (quantity) prices, flash deals, VIP tier discounts, promo codes, deposit bonus,
cashback, restock alerts, product ratings/reviews, broadcasts, referral leaderboard,
low-balance reminders, FAQ text.

Everything is configured from the admin site (Growth menu); settings live in the `settings` table.
Customer messages are queued in the existing `outbox` table, so the bot delivers them (HTML).
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_tiers (
    product_id INTEGER NOT NULL,
    min_qty INTEGER NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (product_id, min_qty)
);
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- 'percent' (discount on the next purchase) or 'credit' (free wallet money)
    value REAL NOT NULL,
    max_uses INTEGER DEFAULT 0,      -- 0 = unlimited
    used INTEGER DEFAULT 0,
    expires_at TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS promo_uses (
    code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    used_at TEXT,
    PRIMARY KEY (code, user_id)
);
CREATE TABLE IF NOT EXISTS restock_subs (
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    created_at TEXT,
    PRIMARY KEY (user_id, product_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS flash_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    price REAL NOT NULL,
    ends_at TEXT NOT NULL,
    created_at TEXT,
    announced INTEGER DEFAULT 0,
    notify_users INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT,
    audience TEXT,
    n INTEGER,
    created_at TEXT
);
"""

USER_COLUMNS = [("promo_code", "TEXT"), ("cashback_earned", "REAL DEFAULT 0"), ("low_bal_at", "TEXT")]
ORDER_COLUMNS = [("review_asked", "INTEGER DEFAULT 0")]

AUDIENCES = {
    "all": "Everyone who started the bot",
    "buyers": "Customers who bought at least once",
    "nobuy": "Users who never bought",
    "balance": "Users with money in their wallet",
    "inactive": "Buyers with no purchase in the last 14 days",
}

DEFAULT_FAQ = (
    "❓ <b>Frequently asked questions</b>\n\n"
    "<b>How do I buy?</b>\nAdd funds to your wallet, open Products, pick an item and quantity, then confirm.\n\n"
    "<b>How fast is delivery?</b>\nInstant for stocked items. Made-to-order items are delivered in the time shown on the product.\n\n"
    "<b>What if something does not work?</b>\nOpen 🛡 Warranty and tap “Claim warranty”, or contact Support.\n\n"
    "<b>Can I get a discount?</b>\nYes: buy in bulk, use promo codes, climb the VIP levels and invite friends with your referral link."
)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def parse_ts(ts):
    try:
        d = datetime.fromisoformat(ts)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_levels(text):
    """'10:5,50:10' -> [(50.0, 10.0), (10.0, 5.0)] (biggest first). Bad parts are skipped."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) >= 2:
            try:
                out.append((float(bits[0]), float(bits[1])))
            except ValueError:
                pass
    return sorted(out, reverse=True)


def _parse_vip(text):
    """'50:Silver:2,200:Gold:5' -> [(200.0, 'Gold', 5.0), (50.0, 'Silver', 2.0)] (highest first)."""
    out = []
    for part in (text or "").replace(";", ",").split(","):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) >= 2:
            try:
                thr = float(bits[0])
                name = bits[1]
                pct = float(bits[2]) if len(bits) > 2 and bits[2] else 0.0
            except ValueError:
                continue
            if name:
                out.append((thr, name, max(0.0, min(pct, 90.0))))
    return sorted(out, reverse=True)


class GrowthMixin:
    # ------------------------------------------------------------ setup
    def _growth_init(self, c):
        c.executescript(SCHEMA)

    # ------------------------------------------------------------ VIP levels
    def vip_levels(self):
        return _parse_vip(self.get_setting("vip_levels", ""))

    def vip_level(self, spent):
        """(name, discount_pct) of the user's current level, or (None, 0)."""
        spent = float(spent or 0)
        for thr, name, pct in self.vip_levels():
            if spent + 1e-9 >= thr:
                return name, pct
        return None, 0.0

    def vip_next(self, spent):
        """(name, amount_still_needed, pct) of the next level up, or None."""
        spent = float(spent or 0)
        higher = [(thr, name, pct) for thr, name, pct in self.vip_levels() if thr > spent + 1e-9]
        if not higher:
            return None
        thr, name, pct = min(higher)
        return name, round(thr - spent, 2), pct

    # ------------------------------------------------------------ bulk prices
    def tiers_for(self, pid):
        c = self.conn()
        rows = c.execute("SELECT min_qty, price FROM price_tiers WHERE product_id=? ORDER BY min_qty", (pid,)).fetchall()
        c.close()
        return rows

    def tiers_all(self):
        c = self.conn()
        rows = c.execute("""SELECT t.product_id, t.min_qty, t.price, p.name, p.price AS base
                            FROM price_tiers t LEFT JOIN products p ON p.id=t.product_id
                            ORDER BY t.product_id, t.min_qty""").fetchall()
        c.close()
        return rows

    def tier_set(self, pid, min_qty, price):
        c = self.conn()
        c.execute("INSERT INTO price_tiers(product_id,min_qty,price) VALUES(?,?,?) "
                  "ON CONFLICT(product_id,min_qty) DO UPDATE SET price=excluded.price", (pid, int(min_qty), float(price)))
        c.commit(); c.close()

    def tier_delete(self, pid, min_qty):
        c = self.conn()
        c.execute("DELETE FROM price_tiers WHERE product_id=? AND min_qty=?", (pid, int(min_qty)))
        c.commit(); c.close()

    # ------------------------------------------------------------ flash deals
    def flash_add(self, pid, price, hours, notify_users=False):
        c = self.conn()
        c.execute("UPDATE flash_sales SET active=0 WHERE product_id=? AND active=1", (pid,))
        fid = c.execute("INSERT INTO flash_sales(product_id,price,ends_at,created_at,notify_users) VALUES(?,?,?,?,?)",
                        (pid, float(price), _iso(_now() + timedelta(hours=float(hours))), self._now(),
                         1 if notify_users else 0)).lastrowid
        c.commit(); c.close()
        return fid

    def flash_end(self, fid):
        c = self.conn()
        c.execute("UPDATE flash_sales SET active=0 WHERE id=?", (fid,))
        c.commit(); c.close()

    def flash_list(self, only_live=False):
        c = self.conn()
        rows = c.execute("""SELECT f.*, p.name AS product, p.price AS base FROM flash_sales f
                            LEFT JOIN products p ON p.id=f.product_id ORDER BY f.id DESC LIMIT 60""").fetchall()
        c.close()
        now = _now()
        out = []
        for r in rows:
            d = dict(r)
            end = parse_ts(d["ends_at"])
            d["live"] = bool(d["active"]) and end is not None and end > now
            if only_live and not d["live"]:
                continue
            out.append(d)
        return out

    def flash_for(self, pid):
        """Live flash deal of a product: {'price','ends_at','end'} or None."""
        c = self.conn()
        rows = c.execute("SELECT price, ends_at FROM flash_sales WHERE product_id=? AND active=1", (pid,)).fetchall()
        c.close()
        now = _now()
        best = None
        for r in rows:
            end = parse_ts(r["ends_at"])
            if end and end > now and (best is None or r["price"] < best["price"]):
                best = {"price": r["price"], "ends_at": r["ends_at"], "end": end}
        return best

    def flash_to_announce(self):
        c = self.conn()
        rows = c.execute("""SELECT f.*, p.name AS product, p.price AS base FROM flash_sales f
                            JOIN products p ON p.id=f.product_id WHERE f.active=1 AND f.announced=0""").fetchall()
        c.close()
        now = _now()
        return [r for r in rows if (parse_ts(r["ends_at"]) or now) > now]

    def flash_mark_announced(self, fid):
        c = self.conn()
        c.execute("UPDATE flash_sales SET announced=1 WHERE id=?", (fid,))
        c.commit(); c.close()

    # ------------------------------------------------------------ promo codes
    def promo_list(self):
        c = self.conn()
        rows = c.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()
        c.close()
        return rows

    def promo_add(self, code, kind, value, max_uses=0, days=None):
        code = (code or "").strip().upper()
        if not code or not code.replace("_", "").replace("-", "").isalnum() or len(code) > 32:
            raise ValueError("Code must be letters/numbers (max 32).")
        if kind not in ("percent", "credit"):
            raise ValueError("Unknown type.")
        value = float(value)
        if value <= 0 or (kind == "percent" and value > 90):
            raise ValueError("Percent must be between 1 and 90; credit must be above 0.")
        expires = _iso(_now() + timedelta(days=float(days))) if days else None
        c = self.conn()
        try:
            c.execute("INSERT INTO promo_codes(code,kind,value,max_uses,expires_at,created_at) VALUES(?,?,?,?,?,?)",
                      (code, kind, value, int(max_uses or 0), expires, self._now()))
            c.commit()
        except Exception:
            raise ValueError("That code already exists.")
        finally:
            c.close()
        return code

    def promo_toggle(self, code):
        c = self.conn()
        c.execute("UPDATE promo_codes SET active=1-COALESCE(active,1) WHERE code=?", (code,))
        c.commit(); c.close()

    def promo_delete(self, code):
        c = self.conn()
        c.execute("DELETE FROM promo_codes WHERE code=?", (code,))
        c.commit(); c.close()

    def _promo_problem(self, c, row, uid):
        """Reason text if the user cannot use this promo now, else None."""
        if not row or not row["active"]:
            return "That code is not valid."
        exp = parse_ts(row["expires_at"]) if row["expires_at"] else None
        if exp and exp <= _now():
            return "That code has expired."
        if row["max_uses"] and (row["used"] or 0) >= row["max_uses"]:
            return "That code has been fully used."
        if c.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (row["code"], uid)).fetchone():
            return "You already used that code."
        return None

    def promo_redeem(self, uid, code):
        """Customer enters a code. Returns (ok, message_text, kind)."""
        self.ensure_user(uid)
        code = (code or "").strip().upper()
        c = self.conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            problem = self._promo_problem(c, row, uid)
            if problem:
                c.rollback()
                return False, problem, None
            if row["kind"] == "credit":
                now = self._now()
                c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (row["value"], uid))
                c.execute("INSERT INTO promo_uses(code,user_id,used_at) VALUES(?,?,?)", (code, uid, now))
                c.execute("UPDATE promo_codes SET used=COALESCE(used,0)+1 WHERE code=?", (code,))
                c.execute("INSERT INTO deposits(id,user_id,amount,method,status,expires_at,created_at,paid_at) "
                          "VALUES(?,?,?,?,'paid',?,?,?)",
                          ("PRM-" + secrets.token_hex(4).upper(), uid, row["value"], "promo", now, now, now))
                c.commit()
                return True, f"🎉 <b>${row['value']:.2f}</b> was added to your wallet!", "credit"
            c.execute("UPDATE users SET promo_code=? WHERE user_id=?", (code, uid))
            c.commit()
            return True, (f"🎟 Code <b>{code}</b> saved: <b>{row['value']:g}% off</b> your next purchase. "
                          f"It is applied automatically when you pay."), "percent"
        finally:
            c.close()

    def promo_active(self, uid):
        """The user's saved percent promo if it is still usable: (code, percent) or None."""
        c = self.conn()
        u = c.execute("SELECT promo_code FROM users WHERE user_id=?", (uid,)).fetchone()
        res = None
        if u and u["promo_code"]:
            row = c.execute("SELECT * FROM promo_codes WHERE code=? AND kind='percent'", (u["promo_code"],)).fetchone()
            if row and not self._promo_problem(c, row, uid):
                res = (row["code"], row["value"])
        c.close()
        return res

    def promo_consume(self, uid):
        """Call after a purchase that used the saved percent promo."""
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        u = c.execute("SELECT promo_code FROM users WHERE user_id=?", (uid,)).fetchone()
        if u and u["promo_code"]:
            code = u["promo_code"]
            c.execute("INSERT OR IGNORE INTO promo_uses(code,user_id,used_at) VALUES(?,?,?)", (code, uid, self._now()))
            c.execute("UPDATE promo_codes SET used=COALESCE(used,0)+1 WHERE code=?", (code,))
            c.execute("UPDATE users SET promo_code=NULL WHERE user_id=?", (uid,))
        c.commit(); c.close()

    # ------------------------------------------------------------ the price a customer pays
    def price_info(self, uid, pid, default, qty=1, checkout=False):
        """Everything that decides the unit price. Order: custom user price (final) -> flash -> bulk
        -> VIP discount -> saved promo (only at checkout)."""
        info = {"unit": float(default), "base": float(default), "custom": False, "flash": False, "bulk": False,
                "vip_name": None, "vip_pct": 0.0, "promo_code": None, "promo_pct": 0.0}
        c = self.conn()
        try:
            u = c.execute("SELECT username, COALESCE(spent,0) spent FROM users WHERE user_id=?", (uid,)).fetchone()
            if u and u["username"]:
                r = c.execute("SELECT price FROM user_prices WHERE username=? AND product_id=?",
                              (u["username"].lstrip("@"), pid)).fetchone()
                if r:
                    info.update(unit=float(r["price"]), custom=True)
                    return info
            price = float(default)
        finally:
            c.close()
        fl = self.flash_for(pid)
        if fl and fl["price"] < price:
            price = float(fl["price"]); info["flash"] = True
        if qty and qty > 1:
            c = self.conn()
            t = c.execute("SELECT price FROM price_tiers WHERE product_id=? AND min_qty<=? ORDER BY min_qty DESC LIMIT 1",
                          (pid, int(qty))).fetchone()
            c.close()
            if t and t["price"] < price:
                price = float(t["price"]); info["bulk"] = True
        name, pct = self.vip_level(u["spent"] if u else 0)
        if pct > 0:
            price *= (1 - pct / 100.0)
            info["vip_name"], info["vip_pct"] = name, pct
        elif name:
            info["vip_name"] = name
        if checkout:
            pr = self.promo_active(uid)
            if pr:
                price *= (1 - pr[1] / 100.0)
                info["promo_code"], info["promo_pct"] = pr
        price = round(price, 2)
        if info["base"] > 0 and price < 0.01:
            price = 0.01
        info["unit"] = price
        return info

    def final_price(self, uid, pid, default, qty=1, checkout=False):
        return self.price_info(uid, pid, default, qty, checkout)["unit"]

    # ------------------------------------------------------------ deposit bonus + cashback
    def deposit_bonus_levels(self):
        return _parse_levels(self.get_setting("deposit_bonus", ""))

    def deposit_bonus_pct(self, amount):
        for thr, pct in self.deposit_bonus_levels():
            if float(amount) + 1e-9 >= thr:
                return pct
        return 0.0

    def _apply_deposit_bonus(self, c, uid, amount):
        """Inside the deposit's own transaction: add the bonus and tell the customer."""
        pct = 0.0
        for thr, p in _parse_levels(self._setting(c, "deposit_bonus", "")):
            if float(amount) + 1e-9 >= thr:
                pct = p
                break
        bonus = round(float(amount) * pct / 100.0, 2)
        if bonus <= 0:
            return 0.0
        now = self._now()
        c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2) WHERE user_id=?", (bonus, uid))
        c.execute("INSERT INTO deposits(id,user_id,amount,method,status,expires_at,created_at,paid_at) "
                  "VALUES(?,?,?,?,'paid',?,?,?)", ("BON-" + secrets.token_hex(4).upper(), uid, bonus, "bonus", now, now, now))
        c.execute("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)",
                  (uid, f"🎁 <b>Deposit bonus!</b> +${bonus:.2f} ({pct:g}%) was added to your wallet on top of your top-up.", now))
        return bonus

    def _apply_cashback(self, c, uid, amount):
        pct = self._setting(c, "cashback_percent", 0.0)
        if pct <= 0:
            return 0.0
        cb = round(float(amount) * pct / 100.0, 2)
        if cb > 0:
            c.execute("UPDATE users SET balance=ROUND(COALESCE(balance,0)+?,2), "
                      "cashback_earned=ROUND(COALESCE(cashback_earned,0)+?,2) WHERE user_id=?", (cb, cb, uid))
        return cb

    # ------------------------------------------------------------ restock alerts
    def restock_subscribe(self, uid, pid):
        self.ensure_user(uid)
        c = self.conn()
        c.execute("INSERT OR IGNORE INTO restock_subs(user_id,product_id,created_at) VALUES(?,?,?)", (uid, pid, self._now()))
        c.commit(); c.close()

    def restock_unsubscribe(self, uid, pid):
        c = self.conn()
        c.execute("DELETE FROM restock_subs WHERE user_id=? AND product_id=?", (uid, pid))
        c.commit(); c.close()

    def restock_is_subscribed(self, uid, pid):
        c = self.conn()
        r = c.execute("SELECT 1 FROM restock_subs WHERE user_id=? AND product_id=?", (uid, pid)).fetchone()
        c.close()
        return bool(r)

    def restock_counts(self):
        c = self.conn()
        rows = c.execute("""SELECT s.product_id, p.name, COUNT(*) n FROM restock_subs s
                            LEFT JOIN products p ON p.id=s.product_id GROUP BY s.product_id ORDER BY n DESC""").fetchall()
        c.close()
        return rows

    def restock_run(self):
        """Queue 'back in stock' messages for products that have stock again. Returns how many were queued."""
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("""SELECT s.user_id, s.product_id, p.name FROM restock_subs s JOIN products p ON p.id=s.product_id
            WHERE p.active=1 AND (SELECT COUNT(*) FROM stock x WHERE x.product_id=p.id AND x.sold=0) > 0""").fetchall()
        now = self._now()
        for r in rows:
            name = (r["name"] or "Product").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            c.execute("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)",
                      (r["user_id"], f"🔔 <b>{name}</b> is back in stock! Open 🛍 Products to grab it before it sells out.", now))
            c.execute("DELETE FROM restock_subs WHERE user_id=? AND product_id=?", (r["user_id"], r["product_id"]))
        c.commit(); c.close()
        return len(rows)

    # ------------------------------------------------------------ reviews
    def review_add(self, order_id, uid, rating):
        """Returns 'ok', 'exists', 'bad' (not the user's paid order) or 'rating'."""
        if not 1 <= int(rating) <= 5:
            return "rating"
        c = self.conn()
        o = c.execute("SELECT product_id FROM orders WHERE id=? AND user_id=? AND status='paid'", (order_id, uid)).fetchone()
        if not o:
            c.close(); return "bad"
        try:
            c.execute("INSERT INTO reviews(order_id,user_id,product_id,rating,created_at) VALUES(?,?,?,?,?)",
                      (order_id, uid, o["product_id"], int(rating), self._now()))
            c.commit()
            res = "ok"
        except Exception:
            res = "exists"
        c.close()
        return res

    def review_comment(self, order_id, uid, text):
        c = self.conn()
        c.execute("UPDATE reviews SET comment=? WHERE order_id=? AND user_id=?", ((text or "").strip()[:400], order_id, uid))
        c.commit(); c.close()

    def review_for_order(self, order_id):
        c = self.conn()
        r = c.execute("SELECT * FROM reviews WHERE order_id=?", (order_id,)).fetchone()
        c.close()
        return r

    def product_rating(self, pid):
        """(average, count); (0, 0) when nobody reviewed it yet."""
        c = self.conn()
        r = c.execute("SELECT AVG(rating) a, COUNT(*) n FROM reviews WHERE product_id=?", (pid,)).fetchone()
        c.close()
        return (round(r["a"], 1), r["n"]) if r and r["n"] else (0, 0)

    def reviews_list(self, limit=100):
        c = self.conn()
        rows = c.execute("""SELECT r.*, p.name AS product, u.username FROM reviews r
            LEFT JOIN products p ON p.id=r.product_id LEFT JOIN users u ON u.user_id=r.user_id
            ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
        c.close()
        return rows

    def review_delete(self, rid):
        c = self.conn()
        c.execute("DELETE FROM reviews WHERE id=?", (rid,))
        c.commit(); c.close()

    def rating_summary(self):
        c = self.conn()
        rows = c.execute("""SELECT p.id, p.name, AVG(r.rating) a, COUNT(*) n FROM reviews r
            JOIN products p ON p.id=r.product_id GROUP BY p.id ORDER BY a DESC, n DESC""").fetchall()
        c.close()
        return rows

    def unreviewed_orders(self, uid, limit=3):
        c = self.conn()
        rows = c.execute("""SELECT o.id, o.product_id, p.name FROM orders o LEFT JOIN products p ON p.id=o.product_id
            WHERE o.user_id=? AND o.status='paid' AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.order_id=o.id)
            ORDER BY o.created_at DESC LIMIT ?""", (uid, limit)).fetchall()
        c.close()
        return rows

    def review_prompt_candidates(self):
        """Orders delivered 3-72 hours ago that were never rated or asked about. Returns one per customer
        (the latest) and marks all of that customer's eligible orders as asked, so nobody is nagged twice."""
        now = _now()
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute("""SELECT o.id, o.user_id, o.product_id, p.name FROM orders o
            LEFT JOIN products p ON p.id=o.product_id
            WHERE o.status='paid' AND COALESCE(o.review_asked,0)=0 AND o.paid_at IS NOT NULL
              AND o.paid_at<=? AND o.paid_at>=?
              AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.order_id=o.id)
            ORDER BY o.paid_at DESC LIMIT 200""", (_iso(now - timedelta(hours=3)), _iso(now - timedelta(hours=72)))).fetchall()
        chosen = {}
        for r in rows:
            chosen.setdefault(r["user_id"], r)
            c.execute("UPDATE orders SET review_asked=1 WHERE id=?", (r["id"],))
        c.commit(); c.close()
        return list(chosen.values())

    def claimable_orders(self, uid, limit=8):
        c = self.conn()
        rows = c.execute("""SELECT o.id, o.product_id, o.quantity, o.created_at, p.name FROM orders o
            LEFT JOIN products p ON p.id=o.product_id WHERE o.user_id=? AND o.status='paid'
            ORDER BY o.created_at DESC LIMIT ?""", (uid, limit)).fetchall()
        c.close()
        return rows

    # ------------------------------------------------------------ broadcast
    def audience_ids(self, audience):
        sql = {
            "all": "SELECT user_id FROM users",
            "buyers": "SELECT DISTINCT user_id FROM orders WHERE status='paid'",
            "nobuy": "SELECT user_id FROM users WHERE user_id NOT IN (SELECT user_id FROM orders WHERE status='paid')",
            "balance": "SELECT user_id FROM users WHERE COALESCE(balance,0)>0",
        }
        c = self.conn()
        if audience == "inactive":
            rows = c.execute("SELECT user_id FROM orders WHERE status='paid' GROUP BY user_id "
                             "HAVING MAX(COALESCE(paid_at,created_at)) < ?", (_iso(_now() - timedelta(days=14)),)).fetchall()
        else:
            rows = c.execute(sql.get(audience, sql["all"])).fetchall()
        c.close()
        return [r["user_id"] for r in rows]

    def broadcast_queue(self, text, audience):
        ids = self.audience_ids(audience)
        now = self._now()
        c = self.conn()
        c.executemany("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)", [(i, text, now) for i in ids])
        c.execute("INSERT INTO broadcasts(text,audience,n,created_at) VALUES(?,?,?,?)", (text, audience, len(ids), now))
        c.commit(); c.close()
        return len(ids)

    def broadcast_history(self, limit=15):
        c = self.conn()
        rows = c.execute("SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        return rows

    def outbox_waiting(self):
        c = self.conn()
        n = c.execute("SELECT COUNT(*) n FROM outbox WHERE status=0").fetchone()["n"]
        c.close()
        return n

    # ------------------------------------------------------------ leaderboard
    def referral_leaderboard(self, days=None, limit=10):
        c = self.conn()
        where, args = "r.referred_by IS NOT NULL", []
        if days:
            where += " AND r.created_at>=?"
            args.append(_iso(_now() - timedelta(days=days)))
        rows = c.execute(f"""SELECT r.referred_by AS uid, COUNT(*) AS n, u.username AS username,
                COALESCE(u.ref_earned,0) AS earned
            FROM users r JOIN users u ON u.user_id=r.referred_by WHERE {where}
            GROUP BY r.referred_by ORDER BY n DESC, earned DESC LIMIT ?""", args + [limit]).fetchall()
        c.close()
        return rows

    def reward_user(self, uid, amount, note):
        """Give a prize to a customer (wallet money + message)."""
        self.admin_topup(uid, amount)
        self.queue_message(uid, f"🏆 <b>{note}</b>\n+${float(amount):.2f} was added to your wallet. Thank you!")

    # ------------------------------------------------------------ low balance reminders
    def lowbal_run(self):
        thr = self.get_setting("low_balance_alert", 1.0)
        if thr <= 0:
            return 0
        c = self.conn()
        rows = c.execute("""SELECT u.user_id, COALESCE(u.balance,0) bal, u.low_bal_at,
            (SELECT MAX(COALESCE(o.paid_at,o.created_at)) FROM orders o WHERE o.user_id=u.user_id AND o.status='paid') last_buy
            FROM users u WHERE COALESCE(u.balance,0) < ? LIMIT 500""", (thr,)).fetchall()
        now = _now()
        sent = 0
        bonus = self.deposit_bonus_levels()
        hint = f"\n🎁 Top up ${bonus[-1][0]:g}+ and get {bonus[-1][1]:g}% extra." if bonus else ""
        for r in rows:
            last = parse_ts(r["last_buy"]) if r["last_buy"] else None
            if not last or not (timedelta(hours=6) <= now - last <= timedelta(days=45)):
                continue
            seen = parse_ts(r["low_bal_at"]) if r["low_bal_at"] else None
            if seen and now - seen < timedelta(days=3):
                continue
            c.execute("UPDATE users SET low_bal_at=? WHERE user_id=?", (_iso(now), r["user_id"]))
            c.execute("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)",
                      (r["user_id"], f"💡 Your wallet balance is <b>${r['bal']:.2f}</b>. Top up in 💰 Wallet so your next order is instant.{hint}",
                       self._now()))
            sent += 1
        c.commit(); c.close()
        return sent

    # ------------------------------------------------------------ small things
    def faq_text(self):
        return (self.get_setting("faq_text", "") or "").strip() or DEFAULT_FAQ
