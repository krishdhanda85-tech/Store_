"""Profit Centre: every paid sale remembers the cost it was sold at.

Why: profit used to be calculated as  sale amount - (cost price TODAY) x quantity.  When a cost or
price changed, the profit of ALL old sales changed too.  Now each paid order stores `unit_cost`
(the cost per item at the moment it was paid) and never changes it afterwards.

It works with SQLite triggers, so every way an order can become paid (crypto, wallet, admin approve,
reseller API) is covered without touching those code paths.  Safe to call on every start.
"""
from datetime import datetime, timedelta, timezone

RANGES = [("today", "Today"), ("yesterday", "Yesterday"), ("day_before", "2 days ago"),
          ("7d", "7 days"), ("30d", "30 days"), ("all", "All time")]


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
    try:                                  # DM sales / profit add-minus / replacements
        import profit_plus
        profit_plus.ensure(c)
    except Exception:
        import logging; logging.getLogger(__name__).exception("profit_plus setup failed")


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


def _parse_day(s):
    try:
        return datetime.strptime((s or "").strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def window(rng, dfrom=None, dto=None):
    """Return (start_iso, end_iso, label, rng).  end is EXCLUSIVE; either may be None (= no limit).

    A custom date range (dfrom / dto as YYYY-MM-DD, both inclusive) wins over `rng` and gives rng='custom'.
    Days are UTC days, the same as every other chart on the site.
    """
    a, b = _parse_day(dfrom), _parse_day(dto)
    if a or b:
        a, b = a or b, b or a
        if a > b:
            a, b = b, a
        label = a.strftime("%d %b %Y") if a == b else "%s - %s" % (a.strftime("%d %b %Y"), b.strftime("%d %b %Y"))
        return a.isoformat(), (b + timedelta(days=1)).isoformat(), label, "custom"
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if rng in ("yesterday", "day_before"):
        day = today - timedelta(days=1 if rng == "yesterday" else 2)
        return day.isoformat(), (day + timedelta(days=1)).isoformat(), day.strftime("%d %b %Y"), rng
    s = since(rng)
    return s, None, dict(RANGES).get(rng, "All time"), rng


def where_ts(col, start, end):
    """SQL condition (no WHERE) and args for a timestamp column inside [start, end)."""
    conds, args = [], []
    if start:
        conds.append("%s >= ?" % col); args.append(start)
    if end:
        conds.append("%s < ?" % col); args.append(end)
    return " AND ".join(conds), tuple(args)


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


def profit_data(c, rng="30d", pid=None, ledger_limit=300, dfrom=None, dto=None):
    sc = _sales_cte(c)
    conds, args = [], []
    s, e, _lbl, _r = window(rng, dfrom, dto)
    if s:
        conds.append("x.ts >= ?"); args.append(s)
    if e:
        conds.append("x.ts < ?"); args.append(e)
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
    _add_extras(c, d, s, e, pid, ledger_limit)
    return d


def _add_extras(c, d, s, e, pid, ledger_limit):
    """Merge DM sales, manual profit add/minus and replacement losses into the numbers."""
    import profit_plus as pp
    sm = pp.summary(c, s, e, pid)
    d["sales_rev"], d["sales_cost"], d["sales_profit"] = d["rev"], d["cost"], d["profit"]
    dm, ad, mi, rp = sm["dm_sale"], sm["add"], sm["minus"], sm["replacement"]
    d["dm"], d["repl"] = dm, rp
    d["add_total"], d["add_n"] = ad["profit"], ad["n"]
    d["minus_total"], d["minus_n"] = -mi["profit"], mi["n"]
    d["repl_loss"], d["repl_n"] = rp["cost"], rp["n"]
    d["extra"] = sum(v["profit"] for v in sm.values())
    d["total_rev"] = d["rev"] + dm["rev"]
    d["total_cost"] = d["cost"] + dm["cost"] + rp["cost"]
    d["net_profit"] = d["profit"] + d["extra"]
    d["net_margin"] = d["net_profit"] / d["total_rev"] * 100 if d["total_rev"] else 0.0

    # per product: fold the entries in
    rows = {r["product_id"]: dict(r, dm_units=0, adj=0.0, loss=0.0) for r in d["by_product"]}
    for en in pp.entries(c, s, e, pid, limit=100000):
        r = rows.setdefault(en["product_id"], dict(product_id=en["product_id"], name=en["pname"] or "Manual / other",
                                                   units=0, rev=0.0, cost=0.0, profit=0.0, dm_units=0, adj=0.0, loss=0.0))
        if en["kind"] == "dm_sale":
            r["units"] += en["qty"]; r["dm_units"] += en["qty"]; r["rev"] += en["revenue"]
            r["cost"] += en["cost"]; r["profit"] += en["profit"]
        elif en["kind"] == "replacement":
            r["loss"] += en["cost"]; r["cost"] += en["cost"]; r["profit"] += en["profit"]
        else:
            r["adj"] += en["profit"]; r["profit"] += en["profit"]
    d["by_product"] = sorted(rows.values(), key=lambda x: x["profit"], reverse=True)

    # one activity list: bot/API sales + everything you booked by hand
    act = [dict(ts=r["ts"], kind=("api" if r["src"] == "api" else "sale"), title=r["name"], qty=r["q"],
                rev=r["a"] or 0.0, cost=(r["q"] or 0) * (r["uc"] or 0.0), profit=r["profit"] or 0.0,
                ref=r["oid"], eid=None, note="") for r in d["ledger"]]
    act += [dict(ts=en["ts"], kind=en["kind"], title=en["title"] or en["pname"] or "", qty=en["qty"],
                 rev=en["revenue"], cost=en["cost"], profit=en["profit"], ref=en["order_id"] or "",
                 eid=en["id"], note=en["note"] or "") for en in pp.entries(c, s, e, pid, limit=ledger_limit)]
    act.sort(key=lambda x: str(x["ts"] or ""), reverse=True)
    for x in act:
        try:
            x["when"] = datetime.fromisoformat(str(x["ts"])).strftime("%d %b, %H:%M")
        except Exception:
            x["when"] = str(x["ts"] or "")[:16]
    d["activity"] = act[:ledger_limit]

    # daily net profit chart (last 60 days of the period)
    today = datetime.now(timezone.utc).date()
    last = (datetime.fromisoformat(e).date() - timedelta(days=1)) if e else today
    first = datetime.fromisoformat(s).date() if s else last - timedelta(days=29)
    first = max(first, last - timedelta(days=59))
    if first > last:
        first = last
    lo, hi = datetime.combine(first, datetime.min.time(), timezone.utc).isoformat(), \
        datetime.combine(last + timedelta(days=1), datetime.min.time(), timezone.utc).isoformat()
    sc = _sales_cte(c)
    w = " WHERE x.ts >= ? AND x.ts < ?" + (" AND x.product_id = ?" if pid else "")
    a = [lo, hi] + ([pid] if pid else [])
    got = {r["d"]: r["p"] for r in c.execute(
        sc + "SELECT substr(x.ts,1,10) d, SUM(x.a - x.q*COALESCE(x.uc,0)) p FROM x" + w + " GROUP BY d", a)}
    ex = pp.by_day(c, lo, hi) if not pid else {}
    if pid:
        w2, a2 = " WHERE ts >= ? AND ts < ? AND product_id = ?", [lo, hi, pid]
        try:
            ex = {r["d"]: r["p"] for r in c.execute(
                "SELECT substr(ts,1,10) d, SUM(profit) p FROM profit_entries" + w2 + " GROUP BY d", a2)}
        except Exception:
            ex = {}
    days, cur = [], first
    while cur <= last:
        k = cur.isoformat()
        days.append(dict(day=k, label=cur.strftime("%d %b"), profit=round((got.get(k) or 0) + (ex.get(k) or 0), 4)))
        cur += timedelta(days=1)
    mp = max([x["profit"] for x in days if x["profit"] > 0] + [0])
    mn = max([-x["profit"] for x in days if x["profit"] < 0] + [0])
    span = (mp + mn) or 1.0
    H = 130.0
    zero = 8 + mp / span * H
    n = max(len(days), 1)
    bw = 600.0 / n
    for i, x in enumerate(days):
        h = abs(x["profit"]) / span * H
        x["x"], x["w"] = round(i * bw + bw * 0.14, 2), round(bw * 0.72, 2)
        x["h"] = round(max(h, 1.5 if x["profit"] else 0), 2)
        x["y"] = round(zero - x["h"], 2) if x["profit"] >= 0 else round(zero, 2)
    d["days"], d["zero_y"] = days, round(zero, 2)
