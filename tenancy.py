"""Multi-seller core: every seller gets their OWN shop inside this one deployment.

What is separate per seller
  * database file   sellers/s<id>.db   (products, stock, coupons/prices, users, wallets, orders,
                                        tickets, UPI accounts, settings, reseller API keys)
  * Telegram bot    their own BOT TOKEN, owner Telegram ID, optional channels
  * payments        their own Binance API key + secret + Pay ID and their own BEP20 address,
                    so customer money goes straight to the seller
  * admin site      the seller logs in with e-mail + password and only ever sees their shop

The main shop (id 1, the owner of the platform) keeps working exactly as before: it is
configured with Railway variables and keeps using the existing shop.db. Sellers never get the
owner's supplier/coupon API (CUPPON_API_KEY), products or customers.

How the rest of the code stays simple
  * `tenancy.db` and `tenancy.binance` are proxies. They always point at the shop that is
    handling the current Telegram update / web request / API call (a ContextVar), so the old
    handler code (`db.get_setting(...)`) works unchanged for every seller.
  * The master registry (master.db) only holds the seller accounts and their (encrypted)
    connection settings.
"""
import contextlib
import contextvars
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

import requests
from werkzeug.security import check_password_hash, generate_password_hash

from binance_pay import BinancePayClient
from provider import ShopDB, decrypt, encrypt

log = logging.getLogger("tenancy")

MAIN_ID = 1
DB_PATH = os.getenv("DB_PATH", "shop.db")
DATA_DIR = os.path.dirname(os.path.abspath(DB_PATH))
MASTER_PATH = os.getenv("MASTER_DB_PATH") or os.path.join(DATA_DIR, "master.db")
SELLER_DIR = os.getenv("SELLER_DATA_DIR") or os.path.join(DATA_DIR, "sellers")
PLATFORM_NAME = os.getenv("SHOP_NAME", "Digital Shop")

# open     = a new seller is active at once
# approval = a new seller waits until the owner approves (default, safest)
# closed   = nobody can sign up
SELLER_SIGNUP = os.getenv("SELLER_SIGNUP", "approval").strip().lower()
MAX_SELLERS = int(os.getenv("MAX_SELLERS", "50"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
CHANNEL_RE = re.compile(r"^(@[A-Za-z0-9_]{4,}|-?\d{5,20})$")

_lock = threading.RLock()
_cache = {}


class NoTenant(RuntimeError):
    """Code that needs a shop ran outside any shop context."""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ master database
def _m():
    c = sqlite3.connect(MASTER_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")
    return c


def init_master():
    os.makedirs(os.path.dirname(os.path.abspath(MASTER_PATH)), exist_ok=True)
    os.makedirs(SELLER_DIR, exist_ok=True)
    c = _m()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sellers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE,
        pw_hash TEXT,
        name TEXT,
        status TEXT DEFAULT 'pending',        -- pending | active | suspended
        is_main INTEGER DEFAULT 0,
        db_path TEXT,
        bot_token_enc TEXT,
        bot_token_hash TEXT UNIQUE,
        bot_username TEXT,
        bot_state TEXT DEFAULT '',            -- running | error | stopped (filled by the bot manager)
        bot_error TEXT DEFAULT '',
        owner_tg_id INTEGER DEFAULT 0,
        bn_key_enc TEXT,
        bn_secret_enc TEXT,
        pay_id TEXT DEFAULT '',
        deposit_address TEXT DEFAULT '',
        deposit_network TEXT DEFAULT '',
        ch_required TEXT DEFAULT '',
        ch_url TEXT DEFAULT '',
        ch_sales TEXT DEFAULT '',
        created_at TEXT
    );
    """)
    c.commit()
    c.close()


def ensure_main():
    """The platform owner's shop (id 1) always exists and keeps the old shop.db."""
    c = _m()
    row = c.execute("SELECT id FROM sellers WHERE id=?", (MAIN_ID,)).fetchone()
    if not row:
        c.execute("INSERT INTO sellers(id,name,status,is_main,db_path,created_at) VALUES(?,?,?,?,?,?)",
                  (MAIN_ID, PLATFORM_NAME, "active", 1, DB_PATH, _now()))
    else:
        c.execute("UPDATE sellers SET name=?, status='active', is_main=1, db_path=? WHERE id=?",
                  (PLATFORM_NAME, DB_PATH, MAIN_ID))
    c.commit()
    c.close()


def _row(tid):
    c = _m()
    r = c.execute("SELECT * FROM sellers WHERE id=?", (tid,)).fetchone()
    c.close()
    return r


# ------------------------------------------------------------------ the shop object
class Tenant:
    """One shop: its database, its payment client, its bot settings."""

    def __init__(self, row):
        self.id = row["id"]
        self.is_main = bool(row["is_main"])
        self.status = row["status"]
        self.row = dict(row)
        self.db_path = row["db_path"]
        self.email = row["email"] or ""
        self.bot_username = row["bot_username"] or ""
        if self.is_main:
            self.name = PLATFORM_NAME
            self.bot_token = os.getenv("BOT_TOKEN", "")
            self.owner_id = int(os.getenv("OWNER_ID", "0") or 0)
            self.required_channel_id = os.getenv("REQUIRED_CHANNEL_ID", "").strip()
            self.required_channel_url = os.getenv("REQUIRED_CHANNEL_URL", "").strip()
            self.sales_channel_id = os.getenv("SALES_CHANNEL_ID", self.required_channel_id).strip()
            pay_cfg = None                         # None = Railway variables (old behaviour)
        else:
            self.name = row["name"] or "Shop"
            self.bot_token = decrypt(row["bot_token_enc"]) if row["bot_token_enc"] else ""
            self.owner_id = int(row["owner_tg_id"] or 0)
            self.required_channel_id = (row["ch_required"] or "").strip()
            self.required_channel_url = (row["ch_url"] or "").strip()
            self.sales_channel_id = (row["ch_sales"] or self.required_channel_id).strip()
            pay_cfg = {
                "api_key": decrypt(row["bn_key_enc"]) if row["bn_key_enc"] else "",
                "secret": decrypt(row["bn_secret_enc"]) if row["bn_secret_enc"] else "",
                "pay_id": row["pay_id"] or "",
                "deposit_address": row["deposit_address"] or "",
                "network": row["deposit_network"] or "",
            }
        d = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(d, exist_ok=True)
        self.db = ShopDB(self.db_path)
        self.db.init()
        self.binance = BinancePayClient(pay_cfg, self.db_path)
        token = _cur.set(self)
        try:
            import reseller_api
            reseller_api.init_schema()             # the shop's own API-key tables
        finally:
            _cur.reset(token)

    @property
    def can_run_bot(self):
        return self.status == "active" and bool(self.bot_token) and bool(self.owner_id)


# ------------------------------------------------------------------ current-shop context
_cur = contextvars.ContextVar("tenant", default=None)


def current_or_none():
    return _cur.get()


def cur():
    t = _cur.get()
    if t is None:
        raise NoTenant("No shop is selected for this request/update.")
    return t


def set_current(t):
    return _cur.set(t)


def reset_current(token):
    _cur.reset(token)


@contextlib.contextmanager
def use(t):
    tok = _cur.set(t)
    try:
        yield t
    finally:
        _cur.reset(tok)


class _Proxy:
    """Looks like the current shop's object; every attribute is read from it at call time."""

    def __init__(self, getter):
        object.__setattr__(self, "_getter", getter)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_getter")(), name)


db = _Proxy(lambda: cur().db)
binance = _Proxy(lambda: cur().binance)


def get_tenant(tid):
    """The shop with this id (rebuilt when its settings changed), or None."""
    with _lock:
        r = _row(tid)
        if not r:
            _cache.pop(tid, None)
            return None
        t = _cache.get(tid)
        if t is None or t.row != dict(r):
            t = Tenant(r)
            _cache[tid] = t
        return t


def all_rows():
    c = _m()
    rows = [dict(r) for r in c.execute("SELECT * FROM sellers ORDER BY id")]
    c.close()
    return rows


def runnable_tenants():
    """Shops whose bot should be running right now."""
    out = []
    for r in all_rows():
        try:
            t = get_tenant(r["id"])
        except Exception:
            log.exception("could not load shop %s", r["id"])
            continue
        if t and t.can_run_bot:
            out.append(t)
    return out


def set_bot_state(tid, state, error=""):
    c = _m()
    c.execute("UPDATE sellers SET bot_state=?, bot_error=? WHERE id=?", (state, (error or "")[:300], tid))
    c.commit()
    c.close()


# ------------------------------------------------------------------ sign up / log in
class SellerError(ValueError):
    """A message that is safe to show to the person on the website."""


def signup_mode():
    return SELLER_SIGNUP if SELLER_SIGNUP in ("open", "approval", "closed") else "approval"


def create_seller(email, password, shop_name):
    if signup_mode() == "closed":
        raise SellerError("New seller sign-ups are closed.")
    email = (email or "").strip().lower()
    shop_name = (shop_name or "").strip()
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise SellerError("Enter a valid e-mail address.")
    if len(password or "") < 8:
        raise SellerError("Password must be at least 8 characters.")
    if not (2 <= len(shop_name) <= 40):
        raise SellerError("Shop name must be 2 to 40 characters.")
    c = _m()
    try:
        n = c.execute("SELECT COUNT(*) FROM sellers WHERE is_main=0").fetchone()[0]
        if n >= MAX_SELLERS:
            raise SellerError("The platform is full right now. Please contact the owner.")
        status = "active" if signup_mode() == "open" else "pending"
        try:
            cur_ = c.execute("INSERT INTO sellers(email,pw_hash,name,status,is_main,created_at) VALUES(?,?,?,?,0,?)",
                             (email, generate_password_hash(password), shop_name, status, _now()))
        except sqlite3.IntegrityError:
            raise SellerError("That e-mail is already registered. Log in instead.")
        tid = cur_.lastrowid
        c.execute("UPDATE sellers SET db_path=? WHERE id=?", (os.path.join(SELLER_DIR, f"s{tid}.db"), tid))
        c.commit()
    finally:
        c.close()
    return tid


def authenticate(email, password):
    """Seller id for a correct e-mail + password, else None."""
    c = _m()
    r = c.execute("SELECT id,pw_hash,is_main FROM sellers WHERE email=? AND is_main=0",
                  ((email or "").strip().lower(),)).fetchone()
    c.close()
    if r and r["pw_hash"] and check_password_hash(r["pw_hash"], password or ""):
        return r["id"]
    # keep the cost of a miss similar to a hit
    check_password_hash(generate_password_hash("x"), password or "")
    return None


def set_status(tid, status):
    if status not in ("pending", "active", "suspended") or tid == MAIN_ID:
        raise SellerError("Not allowed.")
    c = _m()
    c.execute("UPDATE sellers SET status=? WHERE id=?", (status, tid))
    c.commit()
    c.close()


def change_password(tid, old, new):
    r = _row(tid)
    if not r or not r["pw_hash"] or not check_password_hash(r["pw_hash"], old or ""):
        raise SellerError("Current password is wrong.")
    if len(new or "") < 8:
        raise SellerError("New password must be at least 8 characters.")
    c = _m()
    c.execute("UPDATE sellers SET pw_hash=? WHERE id=?", (generate_password_hash(new), tid))
    c.commit()
    c.close()


# ------------------------------------------------------------------ connection settings
def _telegram_get_me(token):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=12)
        j = r.json()
    except Exception:
        raise SellerError("Could not reach Telegram to check the bot token. Try again.")
    if not j.get("ok"):
        raise SellerError("Telegram says this bot token is not valid. Copy it again from @BotFather.")
    return j["result"].get("username") or ""


def save_connection(tid, f, get_me=None):
    """Save a seller's bot + payment settings from the website form (dict-like `f`).
    Blank secret fields keep the value that is already stored."""
    if tid == MAIN_ID:
        raise SellerError("The main shop is configured with Railway variables.")
    get_me = get_me or _telegram_get_me
    r = _row(tid)
    if not r:
        raise SellerError("Shop not found.")
    upd = {}

    name = (f.get("shop_name") or "").strip()
    if name:
        if not (2 <= len(name) <= 40):
            raise SellerError("Shop name must be 2 to 40 characters.")
        upd["name"] = name

    owner = (f.get("owner_tg_id") or "").strip()
    if owner:
        if not owner.isdigit() or not (5 <= len(owner) <= 15):
            raise SellerError("Owner Telegram ID must be a number (get it from @userinfobot).")
        upd["owner_tg_id"] = int(owner)

    token = (f.get("bot_token") or "").strip()
    if token:
        if not TOKEN_RE.match(token):
            raise SellerError("That does not look like a bot token (it looks like 123456789:AA...).")
        if token == os.getenv("BOT_TOKEN", ""):
            raise SellerError("This bot token is already in use.")
        h = _hash(token)
        c = _m()
        clash = c.execute("SELECT id FROM sellers WHERE bot_token_hash=? AND id<>?", (h, tid)).fetchone()
        c.close()
        if clash:
            raise SellerError("This bot token is already used by another shop.")
        upd["bot_username"] = get_me(token)
        upd["bot_token_enc"] = encrypt(token)
        upd["bot_token_hash"] = h
        upd["bot_state"] = ""
        upd["bot_error"] = ""

    key = (f.get("bn_key") or "").strip()
    secret = (f.get("bn_secret") or "").strip()
    if key:
        upd["bn_key_enc"] = encrypt(key)
    if secret:
        upd["bn_secret_enc"] = encrypt(secret)

    pay_id = (f.get("pay_id") or "").strip()
    if pay_id or "pay_id" in f:
        if pay_id and not (pay_id.isdigit() and 4 <= len(pay_id) <= 20):
            raise SellerError("Binance Pay ID must be digits only.")
        upd["pay_id"] = pay_id

    addr = (f.get("deposit_address") or "").strip()
    if addr or "deposit_address" in f:
        if addr and not ADDR_RE.match(addr):
            raise SellerError("BEP20 address must look like 0x followed by 40 letters/digits.")
        upd["deposit_address"] = addr

    if "deposit_network" in f:
        upd["deposit_network"] = (f.get("deposit_network") or "").strip()[:40]

    for form_key, col in (("ch_required", "ch_required"), ("ch_sales", "ch_sales")):
        if form_key in f:
            v = (f.get(form_key) or "").strip()
            if v and not CHANNEL_RE.match(v):
                raise SellerError("Channel must be @username or a numeric ID like -1001234567890.")
            upd[col] = v
    if "ch_url" in f:
        v = (f.get("ch_url") or "").strip()
        if v and not v.startswith("https://t.me/"):
            raise SellerError("Channel link must start with https://t.me/")
        upd["ch_url"] = v

    if not upd:
        return
    sets = ", ".join(f"{k}=?" for k in upd)
    c = _m()
    try:
        c.execute(f"UPDATE sellers SET {sets} WHERE id=?", (*upd.values(), tid))
        c.commit()
    except sqlite3.IntegrityError:
        raise SellerError("This bot token is already used by another shop.")
    finally:
        c.close()


def _hash(token):
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def check_payments(t):
    """Human-readable connectivity report for one shop (website 'Test payments' button)."""
    out = []
    b = t.binance
    out.append(f"Binance Pay: {'configured' if b.binance_enabled else 'not set (key, secret and Pay ID needed)'}")
    if b.binance_enabled:
        try:
            rows = b._bpay_history(True)
            out.append(f"  OK - Binance answered ({len(rows)} Pay transactions in the last hour).")
        except Exception as e:
            out.append(f"  FAILED - {e}")
    out.append(f"BEP20 USDT: {'configured' if b.bsc_enabled else 'not set (deposit address needed)'}")
    if b.bsc_enabled:
        try:
            n = int(b._rpc("eth_blockNumber", []), 16)
            out.append(f"  OK - BSC node answered (block {n}).")
        except Exception as e:
            out.append(f"  FAILED - {e}")
    return out


def seller_stats(t):
    """Small numbers for the owner's seller list."""
    try:
        c = t.db.conn()
        try:
            users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            orders = c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE status='paid'").fetchone()
            products = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        finally:
            c.close()
        return dict(users=users, orders=orders[0], revenue=orders[1], products=products)
    except Exception:
        return dict(users=0, orders=0, revenue=0.0, products=0)


def init_all():
    """Call once at start-up (web and bot import this)."""
    init_master()
    ensure_main()
    get_tenant(MAIN_ID)
