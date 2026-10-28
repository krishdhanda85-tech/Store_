"""Profit Centre: every paid sale remembers the cost it was sold at.

Why: profit used to be calculated as  sale amount - (cost price TODAY) x quantity.  When a cost or
price changed, the profit of ALL old sales changed too.  Now each paid order stores `unit_cost`
(the cost per item at the moment it was paid) and never changes it afterwards.

It works with SQLite triggers, so every way an order can become paid (crypto, wallet, admin approve,
reseller API) is covered without touching those code paths.  Safe to call on every start.
"""
from datetime import datetime, timedelta, timezone

RANGES = [("today", "Today"), ("7d", "7 days"), ("30d", "30 days"), ("all", "All time")]


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def ensure_profit_tracking(c):
    """Add unit_cost columns, the price log and the triggers (only for tables that exist)."""
    t = _tables(c)
    if "products" not in t:
        return
    c.execute("""CREATE TABLE IF NOT EXISTS price_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, product_id INTEGER NOT NULL,
        field TEXT NOT NULL, old_value REAL, new_value REAL)""")
    # "what does one item cost me right now" (own cost first, else supplier cost) - same rule as before
    cur_cost = "(SELECT cost FROM products WHERE id = NEW.product_id)"
    if "provider_map" in t:
        cur_cost = f"COALESCE({cur_cost}, (SELECT cost FROM provider_map WHERE product_id = NEW.product_id))"

    for table in ("orders", "api_reseller_orders"):
        if table not in t:
            continue
        if "unit_cost" not in _cols(c, table):
            c.execute(f"ALTER TABLE {table} ADD COLUMN unit_cost REAL")
        for ev, when in (("ins", "AFTER INSERT ON " + table), ("upd", "AFTER UPDATE OF status ON " + table)):
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS trg_{table}_cost_{ev} {when}
                WHEN NEW.status = 'paid' AND NEW.unit_cost IS NULL
                BEGIN UPDATE {table} SET unit_cost = {cur_cost} WHERE id = NEW.id; END""")

    c.execute("""CREATE TRIGGER IF NOT EXISTS trg_products_price_log AFTER UPDATE OF price, cost ON products
        BEGIN
          INSERT INTO price_log(ts,product_id,field,old_value,new_value)
            SELECT datetime('now'), NEW.id, 'price', OLD.price, NEW.price WHERE OLD.price IS NOT NEW.price;
          INSERT INTO price_log(ts,product_id,field,old_value,new_value)
            SELECT datetime('now'), NEW.id, 'cost', OLD.cost, NEW.cost WHERE OLD.cost IS NOT NEW.cost;
        END""")
    if "provider_map" in t:
        c.execute("""CREATE TRIGGER IF NOT EXISTS trg_supplier_cost_log AFTER UPDATE OF cost ON provider_map
            WHEN OLD.cost IS NOT NEW.cost
            BEGIN INSERT INTO price_log(ts,product_id,field,old_value,new_value)
              VALUES(datetime('now'), NEW.product_id, 'supplier cost', OLD.cost, NEW.cost); END""")

    # one-time: old paid orders get today's cost (their real old cost was never saved, so this is the best we have)
    if "settings" in t:
        done = c.execute("SELECT value FROM settings WHERE key='profit_backfill_done'").fetchone()
        if not done:
            back = "(SELECT cost FROM products WHERE id = {tb}.product_id)"
            if "provider_map" in t:
                back = f"COALESCE({back}, (SELECT cost FROM provider_map WHERE product_id = {{tb}}.product_id))"
            for table in ("orders", "api_reseller_orders"):
                if table in t:
                    c.execute(f"UPDATE {table} SET unit_cost = {back.format(tb=table)} "
                              f"WHERE status='paid' AND unit_cost IS NULL")
            c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('profit_backfill_done','1')")
    c.commit()


def fill_missing_costs(c):
    """Give paid sales that have NO cost the product's current cost. Returns how many were filled."""
    t = _tables(c)
    n = 0
    for table in ("orders", "api_reseller_orders"):
        if table not in t:
            continue
        expr = "(SELECT cost FROM products WHERE id = {tb}.product_id)"
        if "provider_map" in t:
            expr = "COALESCE(" + expr + ", (SELECT cost FROM provider_map WHERE product_id = {tb}.product_id))"
        cur = c.execute(f"UPDATE {table} SET unit_cost = {expr.format(tb=table)} "
                        f"WHERE status='paid' AND unit_cost IS NULL AND {expr.format(tb=table)} IS NOT NULL")
        n += cur.rowcount
    c.commit()
    return n


def since(rng):
    now = datetime.now(timezone.utc)
    if rng == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if rng == "7d":
        return (now - timedelta(days=7)).isoformat()
    if rng == "30d":
        return (now - timedelta(days=30)).isoformat()
    return None


def _sales_cte(c):
    api = "api_reseller_orders" in _tables(c)
    sql = ("WITH x AS (SELECT o.id oid, o.product_id, o.quantity q, o.amount a, o.unit_price up, "
           "COALESCE(o.paid_at, o.created_at) ts, 'bot' src, o.unit_cost uc FROM orders o WHERE o.status='paid'")
    if api:
        sql += (" UNION ALL SELECT id, product_id, quantity, amount, unit_price, created_at, 'api', unit_cost "
                "FROM api_reseller_orders WHERE status='paid'")
    return sql + ") "


def profit_data(c, rng="30d", pid=None, ledger_limit=300):
    sc = _sales_cte(c)
    conds, args = [], []
    s = since(rng)
    if s:
        conds.append("x.ts >= ?"); args.append(s)
    if pid:
        conds.append("x.product_id = ?"); args.append(pid)
    w = (" WHERE " + " AND ".join(conds)) if conds else ""
    d = {}
    r = c.execute(sc + "SELECT COUNT(*) n, COALESCE(SUM(q),0) units, COALESCE(SUM(a),0) rev, "
                  "COALESCE(SUM(q*COALESCE(uc,0)),0) cost, "
                  "COALESCE(SUM(CASE WHEN uc IS NULL THEN 1 ELSE 0 END),0) nocost FROM x" + w, args).fetchone()
    d.update(dict(r))
    d["profit"] = d["rev"] - d["cost"]
    d["margin"] = d["profit"] / d["rev"] * 100 if d["rev"] else 0.0
    d["per_item"] = d["profit"] / d["units"] if d["units"] else 0.0
    d["by_product"] = [dict(x) for x in c.execute(
        sc + "SELECT x.product_id, COALESCE(p.name,'Deleted product') name, SUM(q) units, SUM(a) rev, "
        "SUM(q*COALESCE(uc,0)) cost, SUM(a - q*COALESCE(uc,0)) profit FROM x "
        "LEFT JOIN products p ON p.id = x.product_id" + w + " GROUP BY x.product_id ORDER BY profit DESC", args)]
    d["ledger"] = [dict(x) for x in c.execute(
        sc + "SELECT x.oid, x.src, x.ts, COALESCE(p.name,'Deleted product') name, x.q, "
        "CASE WHEN x.q>0 THEN x.a*1.0/x.q END unit_price, x.uc, x.a, (x.a - x.q*COALESCE(x.uc,0)) profit FROM x "
        "LEFT JOIN products p ON p.id = x.product_id" + w + " ORDER BY x.ts DESC LIMIT ?", args + [ledger_limit])]
    d["changes"] = [dict(x) for x in c.execute(
        "SELECT l.ts, COALESCE(p.name,'Deleted product') name, l.field, l.old_value, l.new_value FROM price_log l "
        "LEFT JOIN products p ON p.id = l.product_id ORDER BY l.id DESC LIMIT 40")] \
        if "price_log" in _tables(c) else []
    return d
