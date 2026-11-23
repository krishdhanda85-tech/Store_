"""Profit Centre extras.

  * DM sales        - things you sell in Telegram DMs (outside the bot): revenue - cost = profit.
  * Profit add/minus - any manual correction (+ extra income, - expense / loss) with a note.
  * Replacements    - a customer's item stopped working: swap it for a fresh one, send it to the customer
                      on Telegram, and book the cost of the new item as a LOSS (no new money came in).

Everything is stored in `profit_entries` (one row per money event) so the Profit centre, the dashboard
and the CSV all read the same numbers.  `replacements` keeps the swap history (old item -> new item).
"""
import html
import re
from datetime import datetime, timezone

KINDS = {"dm_sale": "DM sale", "add": "Profit +", "minus": "Profit -", "replacement": "Replacement"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS profit_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
        product_id INTEGER, order_id TEXT, title TEXT, qty INTEGER DEFAULT 1,
        revenue REAL DEFAULT 0, cost REAL DEFAULT 0, profit REAL DEFAULT 0, note TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_profit_entries_ts ON profit_entries(ts)")
    c.execute("""CREATE TABLE IF NOT EXISTS replacements(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, order_id TEXT NOT NULL, product_id INTEGER,
        user_id INTEGER, item_row INTEGER, old_item TEXT, new_item TEXT, stock_id INTEGER,
        reason TEXT, cost REAL DEFAULT 0, entry_id INTEGER, notified INTEGER DEFAULT 0)""")
    c.commit()


def _money(v, name="Amount", allow_zero=True):
    try:
        x = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("%s must be a number." % name)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("%s must be a number." % name)
    if x < 0 or (x == 0 and not allow_zero):
        raise ValueError("%s must be %s." % (name, "zero or more" if allow_zero else "more than zero"))
    return round(x, 4)


def _ts(day):
    """Timestamp for an entry: now, or noon UTC of a past day the admin picked."""
    day = (day or "").strip()[:10]
    if day:
        try:
            d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("That date is not valid.")
        if d.date() != datetime.now(timezone.utc).date():
            return d.replace(hour=12).isoformat()
    return _now()


def current_cost(c, pid):
    """What one item costs right now (own cost first, else supplier cost). None if unknown."""
    if not pid:
        return None
    r = c.execute("SELECT cost FROM products WHERE id=?", (pid,)).fetchone()
    if r and r[0] is not None:
        return float(r[0])
    if "provider_map" in _tables(c):
        r = c.execute("SELECT cost FROM provider_map WHERE product_id=?", (pid,)).fetchone()
        if r and r[0] is not None:
            return float(r[0])
    return None


def _product(c, pid):
    return c.execute("SELECT id, name, price FROM products WHERE id=?", (pid,)).fetchone() if pid else None


# ------------------------------------------------------------------ DM sale
def add_dm_sale(c, pid, title, qty, price_each, cost_each, day="", note="", take_stock=False):
    """Book a sale made in DM. Returns (entry_id, [stock items taken]). Raises ValueError for bad input."""
    pid = int(pid) if pid else None
    prod = _product(c, pid)
    if pid and not prod:
        raise ValueError("That product does not exist.")
    title = (title or "").strip()[:80] or (prod["name"] if prod else "")
    if not title:
        raise ValueError("Pick a product or type a name for what you sold.")
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if not 1 <= qty <= 100000:
        raise ValueError("Quantity must be 1 or more.")
    price = _money(price_each if str(price_each).strip() != "" else (prod["price"] if prod else ""), "Sale price")
    if str(cost_each).strip() == "":
        cost = current_cost(c, pid) or 0.0
    else:
        cost = _money(cost_each, "Cost")
    ts = _ts(day)
    items = []
    c.execute("BEGIN IMMEDIATE")
    try:
        if take_stock:
            if not pid:
                raise ValueError("Choose a product to take items from stock.")
            rows = c.execute("SELECT id, link FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?",
                             (pid, qty)).fetchall()
            if len(rows) < qty:
                raise ValueError("Only %d in stock for this product (you asked for %d)." % (len(rows), qty))
            for r in rows:
                c.execute("UPDATE stock SET sold=1 WHERE id=?", (r["id"],))
            items = [r["link"] for r in rows]
        rev, cst = round(price * qty, 4), round(cost * qty, 4)
        cur = c.execute("INSERT INTO profit_entries(ts,kind,product_id,title,qty,revenue,cost,profit,note) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (ts, "dm_sale", pid, title, qty, rev, cst, round(rev - cst, 4), (note or "").strip()[:300]))
        eid = cur.lastrowid
        c.commit()
    except Exception:
        c.rollback()
        raise
    return eid, items


# ------------------------------------------------------------------ profit + / -
def add_adjustment(c, kind, amount, note, pid=None, day=""):
    if kind not in ("add", "minus"):
        raise ValueError("Unknown type.")
    amt = _money(amount, "Amount", allow_zero=False)
    note = (note or "").strip()[:300]
    if not note:
        raise ValueError("Write a short note so you remember why.")
    pid = int(pid) if pid else None
    prod = _product(c, pid)
    if pid and not prod:
        raise ValueError("That product does not exist.")
    signed = amt if kind == "add" else -amt
    cur = c.execute("INSERT INTO profit_entries(ts,kind,product_id,title,qty,revenue,cost,profit,note) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (_ts(day), kind, pid, (prod["name"] if prod else note[:80]), 1, 0, 0, signed, note))
    c.commit()
    return cur.lastrowid


def delete_entry(c, eid):
    row = c.execute("SELECT id FROM profit_entries WHERE id=?", (eid,)).fetchone()
    if not row:
        return False
    c.execute("DELETE FROM profit_entries WHERE id=?", (eid,))
    c.execute("UPDATE replacements SET entry_id=NULL WHERE entry_id=?", (eid,))
    c.commit()
    return True


# ------------------------------------------------------------------ replacements
def find_orders(c, q):
    """Paid orders matching an order id / group id / @username / Telegram id, each with its delivered items."""
    q = (q or "").strip()
    if not q:
        return []
    like = "%" + q.replace("%", "").replace("_", "").lstrip("@") + "%"
    conds, args = ["o.id=?", "o.group_id=?", "o.id LIKE ?"], [q, q, like]
    if q.lstrip("@").isdigit() and not q.startswith("@"):
        conds.append("o.user_id=?"); args.append(int(q))
    if "users" in _tables(c):
        conds.append("o.user_id IN (SELECT user_id FROM users WHERE username LIKE ?)"); args.append(like)
    rows = c.execute("SELECT o.id, o.group_id, o.user_id, o.product_id, o.quantity, o.amount, o.unit_cost, "
                     "COALESCE(o.paid_at,o.created_at) ts, p.name pname, u.username FROM orders o "
                     "LEFT JOIN products p ON p.id=o.product_id LEFT JOIN users u ON u.user_id=o.user_id "
                     "WHERE o.status='paid' AND (" + " OR ".join(conds) + ") ORDER BY ts DESC LIMIT 20",
                     args).fetchall()
    out = []
    for r in rows:
        o = dict(r)
        o["its"] = [dict(i) for i in c.execute(
            "SELECT i.id, i.idx, i.item, (SELECT COUNT(*) FROM replacements x WHERE x.item_row=i.id) swaps "
            "FROM order_items i WHERE i.order_id=? ORDER BY i.idx, i.id", (r["id"],))]
        o["cost_now"] = current_cost(c, r["product_id"])
        o["stock"] = c.execute("SELECT COUNT(*) FROM stock WHERE product_id=? AND sold=0", (r["product_id"],)).fetchone()[0]
        out.append(o)
    return out


def replace_item(c, item_row, manual_item="", cost_override="", reason="", notify=True):
    """Swap ONE delivered item of an order for a fresh one.

    Fresh item = what you paste, else the next unsold stock line of the same product.
    The cost of the fresh item is booked as a loss. The customer gets the new item on Telegram.
    """
    cost_override = str(cost_override or "").strip()
    manual_item = (manual_item or "").strip()
    reason = (reason or "").strip()[:200]
    cost = _money(cost_override, "Cost") if cost_override != "" else None
    c.execute("BEGIN IMMEDIATE")
    try:
        it = c.execute("SELECT i.id, i.order_id, i.item, o.product_id, o.user_id, o.unit_cost, o.status, p.name pname "
                       "FROM order_items i JOIN orders o ON o.id=i.order_id LEFT JOIN products p ON p.id=o.product_id "
                       "WHERE i.id=?", (item_row,)).fetchone()
        if not it or it["status"] != "paid":
            raise ValueError("That item was not found on a paid order.")
        pid, stock_id = it["product_id"], None
        if manual_item:
            new = manual_item
        else:
            s = c.execute("SELECT id, link FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 1", (pid,)).fetchone()
            if not s:
                raise ValueError("No stock left for this product. Add stock first, or paste the new item yourself.")
            c.execute("UPDATE stock SET sold=1 WHERE id=?", (s["id"],))
            new, stock_id = s["link"], s["id"]
        if cost is None:
            cost = current_cost(c, pid)
            if cost is None:
                cost = float(it["unit_cost"] or 0.0)
        c.execute("UPDATE order_items SET item=? WHERE id=?", (new, item_row))
        eid = c.execute("INSERT INTO profit_entries(ts,kind,product_id,order_id,title,qty,revenue,cost,profit,note) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (_now(), "replacement", pid, it["order_id"], it["pname"] or "Replacement", 1, 0, cost, -cost,
                         reason)).lastrowid
        c.execute("INSERT INTO replacements(ts,order_id,product_id,user_id,item_row,old_item,new_item,stock_id,reason,"
                  "cost,entry_id,notified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (_now(), it["order_id"], pid, it["user_id"], item_row, it["item"], new, stock_id, reason, cost, eid,
                   1 if notify else 0))
        if notify:
            msg = ("🔁 <b>Replacement ready</b>\n🧾 Order <code>%s</code> · %s\n\n<code>%s</code>\n\n"
                   "Sorry for the trouble, thank you for your patience!"
                   % (html.escape(it["order_id"]), html.escape(it["pname"] or ""), html.escape(new)))
            c.execute("INSERT INTO outbox(user_id,text,created_at) VALUES(?,?,?)", (it["user_id"], msg, _now()))
        c.commit()
    except Exception:
        c.rollback()
        raise
    return dict(new=new, old=it["item"], cost=cost, user_id=it["user_id"], order_id=it["order_id"],
                from_stock=stock_id is not None)


def recent_replacements(c, limit=40, start=None, end=None, pid=None):
    conds, args = [], []
    if start:
        conds.append("r.ts >= ?"); args.append(start)
    if end:
        conds.append("r.ts < ?"); args.append(end)
    if pid:
        conds.append("r.product_id = ?"); args.append(pid)
    w = (" WHERE " + " AND ".join(conds)) if conds else ""
    return [dict(r) for r in c.execute(
        "SELECT r.*, COALESCE(p.name,'Deleted product') pname, u.username FROM replacements r "
        "LEFT JOIN products p ON p.id=r.product_id LEFT JOIN users u ON u.user_id=r.user_id" + w +
        " ORDER BY r.id DESC LIMIT ?", args + [limit])]


# ------------------------------------------------------------------ reading (used by profit.py / dashboard)
def _w(start, end, pid=None, col="ts"):
    conds, args = [], []
    if start:
        conds.append("%s >= ?" % col); args.append(start)
    if end:
        conds.append("%s < ?" % col); args.append(end)
    if pid:
        conds.append("product_id = ?"); args.append(pid)
    return ((" WHERE " + " AND ".join(conds)) if conds else ""), args


def summary(c, start=None, end=None, pid=None):
    """{kind: {n, units, rev, cost, profit}} for the period (zeros when nothing was booked)."""
    out = {k: dict(n=0, units=0, rev=0.0, cost=0.0, profit=0.0) for k in KINDS}
    try:
        w, a = _w(start, end, pid)
        for r in c.execute("SELECT kind, COUNT(*) n, COALESCE(SUM(qty),0) units, COALESCE(SUM(revenue),0) rev, "
                           "COALESCE(SUM(cost),0) cost, COALESCE(SUM(profit),0) profit FROM profit_entries" + w +
                           " GROUP BY kind", a):
            if r["kind"] in out:
                out[r["kind"]] = dict(r)
                del out[r["kind"]]["kind"]
    except Exception:
        pass            # table not created yet
    return out


def entries(c, start=None, end=None, pid=None, limit=300):
    try:
        w, a = _w(start, end, pid, "e.ts")
        w = w.replace("product_id", "e.product_id")
        return [dict(r) for r in c.execute(
            "SELECT e.*, p.name pname FROM profit_entries e LEFT JOIN products p ON p.id=e.product_id" + w +
            " ORDER BY e.ts DESC, e.id DESC LIMIT ?", a + [limit])]
    except Exception:
        return []


def by_day(c, start, end):
    """{YYYY-MM-DD: profit} of all entries in [start, end)."""
    try:
        w, a = _w(start, end)
        return {r["d"]: r["p"] for r in c.execute(
            "SELECT substr(ts,1,10) d, SUM(profit) p FROM profit_entries" + w + " GROUP BY d", a)}
    except Exception:
        return {}
