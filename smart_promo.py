"""
Smart promo: reads every customer's PAID order history and picks the best reason + product to
message them about. Pure data analysis (no AI). Gemini is only used later, to write the hook line.

Reasons (highest priority first):
  renew      bought a product ~one cycle ago (their own repeat gap, else renew_days) -> time to renew
  pricedrop  a product they bought before is discounted now
  crosssell  customers who bought the same product also bought X
  loyal      3+ paid orders -> thank them and offer their favourite
  winback    no purchase for winback_days -> "we missed you" (+ optional personal promo code)
  newcomer   joined, never bought -> general pick (the caller chooses the product)

Everything is configurable on the admin website (Smart Promo page). Values are stored in the shop's
`settings` table (keys sp_*), with the environment variables / defaults below as the fallback.

Spam guard: at most one smart message per cooldown_days (newcomers: 7 days); the same product is not
pushed to the same user again for product_cooldown_days. Messages are sent near the hour each customer
usually buys at (shop timezone), never during quiet hours.
"""
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

REASONS = ("renew", "pricedrop", "crosssell", "loyal", "winback", "newcomer")


def _env(name, default):
    v = os.getenv(name, "")
    return v if v != "" else str(default)


# key -> (default, type)   Environment variables of the old version still work as defaults.
FIELDS = {
    "renew_days": (_env("SMART_RENEW_DAYS", 28), int),
    "winback_days": (_env("SMART_WINBACK_DAYS", 45), int),
    "cooldown_days": (_env("SMART_COOLDOWN_DAYS", 3), int),
    "product_cooldown_days": (_env("SMART_PRODUCT_COOLDOWN_DAYS", 14), int),
    "min_copurchase": (_env("SMART_MIN_COPURCHASE", 2), int),
    "loyal_orders": (_env("SMART_LOYAL_ORDERS", 3), int),
    "max_per_run": (_env("SMART_MAX_PER_RUN", 300), int),
    "newcomer_cooldown_days": (7, int),
    "tz_offset_min": (330, int),          # shop timezone, minutes from UTC (330 = India)
    "quiet_start": (23, int),             # no messages from this local hour ...
    "quiet_end": (8, int),                # ... until this one
    "smart_time": (1, int),               # 1 = send near each customer's usual buying hour
    "window": (2, int),                   # +- hours around that usual hour
    "winback_pct": (0, int),              # 0 = no personal code; else % off in a one-time code
    "winback_code_days": (7, int),
    "attr_days": (3, int),                # a purchase within N days after a message counts as its result
    "names": (1, int),                    # greet by first name
    "language": (1, int),                 # write in the customer's language
    "urgency": (1, int),                  # real flash-deal countdown / low-stock lines
    "reviews": (1, int),                  # show the product's real rating
}
for _r in REASONS:
    FIELDS["on_" + _r] = (1, int)

NEWCOMER_MIN_AGE_DAYS = 1


def get_cfg(db):
    """Current settings: website value if saved, else default."""
    out = {}
    for k, (d, typ) in FIELDS.items():
        raw = db.get_setting("sp_" + k, d)
        try:
            out[k] = typ(float(raw))
        except (TypeError, ValueError):
            out[k] = typ(float(d))
    return out


def save_cfg(db, form):
    """form: {key: str}. Checkboxes (on_*, smart_time, names, ...) are '1' when ticked, missing otherwise."""
    flags = {k for k in FIELDS if k.startswith("on_")} | {"smart_time", "names", "language", "urgency", "reviews"}
    limits = {"renew_days": (3, 365), "winback_days": (7, 720), "cooldown_days": (1, 60),
              "product_cooldown_days": (1, 120), "min_copurchase": (1, 50), "loyal_orders": (2, 50),
              "max_per_run": (1, 5000), "tz_offset_min": (-720, 840), "quiet_start": (0, 23),
              "quiet_end": (0, 23), "window": (1, 12), "winback_pct": (0, 90), "winback_code_days": (1, 60),
              "attr_days": (1, 30)}
    for k in FIELDS:
        if k in flags:
            db.set_setting("sp_" + k, 1 if form.get(k) else 0)
        elif k in limits:
            lo, hi = limits[k]
            try:
                v = int(float(form.get(k, FIELDS[k][0])))
            except (TypeError, ValueError):
                v = int(float(FIELDS[k][0]))
            db.set_setting("sp_" + k, max(lo, min(hi, v)))


# ------------------------------------------------------------------ time helpers
def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def local_hour(dt, tz_min):
    return (dt + timedelta(minutes=tz_min)).hour


def _in_quiet(h, qs, qe):
    if qs == qe:
        return False
    return (h >= qs or h < qe) if qs > qe else (qs <= h < qe)


def eligible_now(h, pref, cfg):
    """Is local hour `h` a good moment to message a customer whose usual buying hour is `pref`?"""
    qs, qe = cfg["quiet_start"], cfg["quiet_end"]
    if _in_quiet(h, qs, qe):
        return False
    if not cfg["smart_time"]:
        return True
    if pref is None:
        return 10 <= h <= 21
    if _in_quiet(pref, qs, qe):
        pref = qe                                   # usual hour is in the quiet time -> first hour after it
    d = min((h - pref) % 24, (pref - h) % 24)
    return d <= cfg["window"]


# ------------------------------------------------------------------ log table
def ensure_table(db):
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS smart_promo_log ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, product_id INTEGER, "
              "reason TEXT, sent_at TEXT NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_smart_log_user ON smart_promo_log(user_id)")
    c.commit()
    c.close()


def mark_sent(db, rows):
    """rows: [(user_id, product_id, reason)]"""
    if not rows:
        return
    ensure_table(db)
    now = datetime.now(timezone.utc).isoformat()
    c = db.conn()
    c.executemany("INSERT INTO smart_promo_log(user_id,product_id,reason,sent_at) VALUES(?,?,?,?)",
                  [(u, p, r, now) for u, p, r in rows])
    c.commit()
    c.close()


def _load(db):
    ensure_table(db)
    c = db.conn()
    orders = c.execute("SELECT user_id, product_id, quantity, amount, COALESCE(paid_at, created_at) ts "
                       "FROM orders WHERE status='paid' ORDER BY ts").fetchall()
    users = c.execute("SELECT * FROM users").fetchall()
    log = c.execute("SELECT user_id, product_id, sent_at FROM smart_promo_log").fetchall()
    c.close()
    return orders, users, log


# ------------------------------------------------------------------ analysis
def analyze(db, user_ids, products, discount_of, is_available, now=None, cfg=None):
    """products: active product rows. discount_of(p) -> percent (0 = none). is_available(p) -> bool.
    Returns [{uid, reason, pid, facts{}, balance, orders, spent, name, lang}] (pid None for newcomers)."""
    cfg = cfg or get_cfg(db)
    now = now or datetime.now(timezone.utc)
    by_id = {p["id"]: p for p in products}
    ok = {pid for pid, p in by_id.items() if is_available(p)}
    orders, users, log = _load(db)
    h_now = local_hour(now, cfg["tz_offset_min"])

    hist = defaultdict(lambda: defaultdict(list))
    n_orders, spent = Counter(), Counter()
    hours = defaultdict(Counter)
    for o in orders:
        t = _parse(o["ts"])
        if t is None:
            continue
        hist[o["user_id"]][o["product_id"]].append(t)
        n_orders[o["user_id"]] += 1
        spent[o["user_id"]] += float(o["amount"] or 0)
        hours[o["user_id"]][local_hour(t, cfg["tz_offset_min"])] += 1

    # who bought what together (people count per product pair)
    co = defaultdict(Counter)
    for uid, per in hist.items():
        pids = [p for p in per if p in by_id][:30]
        for a in pids:
            for b in pids:
                if a != b:
                    co[a][b] += 1

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    last_any, last_prod = {}, {}
    for r in log:
        t = _parse(r["sent_at"])
        if t is None:
            continue
        if t > last_any.get(r["user_id"], min_dt):
            last_any[r["user_id"]] = t
        k = (r["user_id"], r["product_id"])
        if t > last_prod.get(k, min_dt):
            last_prod[k] = t

    uinfo = {u["user_id"]: u for u in users}
    out = []

    def blocked(uid, pid):
        t = last_prod.get((uid, pid))
        return bool(t and now - t < timedelta(days=cfg["product_cooldown_days"]))

    for uid in user_ids:
        if len(out) >= cfg["max_per_run"]:
            break
        per = {p: sorted(ts) for p, ts in hist.get(uid, {}).items() if p in by_id}
        u = uinfo.get(uid)
        keys = u.keys() if u is not None else []
        balance = float(u["balance"] or 0) if u is not None and "balance" in keys else 0.0
        name = (u["first_name"] if u is not None and "first_name" in keys else "") or ""
        language = (u["language"] if u is not None and "language" in keys else "") or "en"

        cool = cfg["cooldown_days"] if per else cfg["newcomer_cooldown_days"]
        t_last = last_any.get(uid)
        if t_last and now - t_last < timedelta(days=cool):
            continue
        pref = hours[uid].most_common(1)[0][0] if n_orders.get(uid, 0) >= 2 else None
        if not eligible_now(h_now, pref, cfg):
            continue

        base = {"uid": uid, "balance": balance, "orders": n_orders.get(uid, 0),
                "spent": spent.get(uid, 0.0), "name": name.strip(), "lang": language}

        if not per:  # ---------------- never bought
            if not cfg["on_newcomer"]:
                continue
            created = _parse(u["created_at"]) if u is not None and "created_at" in keys else None
            if created and now - created < timedelta(days=NEWCOMER_MIN_AGE_DAYS):
                continue
            out.append({**base, "reason": "newcomer", "pid": None, "facts": {}})
            continue

        last_all = max(ts[-1] for ts in per.values())
        days_all = (now - last_all).days

        # ---- 1) renew: most overdue product, using the user's own repeat cycle when known
        if cfg["on_renew"]:
            best = None
            for pid, ts in per.items():
                if pid not in ok or blocked(uid, pid):
                    continue
                days = sorted({t.date() for t in ts})
                gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days >= 1]
                cycle = min(90, max(7, statistics.median(gaps))) if gaps else cfg["renew_days"]
                gap = (now - ts[-1]).days
                if cycle * 0.85 <= gap <= max(cycle * 2.5, cfg["winback_days"]):
                    ratio = gap / cycle
                    if best is None or ratio > best[0]:
                        best = (ratio, pid, gap, len(ts))
            if best:
                out.append({**base, "reason": "renew", "pid": best[1],
                            "facts": {"days_ago": best[2], "times": best[3]}})
                continue

        # ---- 2) pricedrop
        if cfg["on_pricedrop"]:
            drops = [(discount_of(by_id[p]), p) for p in per if p in ok and not blocked(uid, p)
                     and discount_of(by_id[p]) > 0]
            if drops:
                pct, pid = max(drops)
                out.append({**base, "reason": "pricedrop", "pid": pid,
                            "facts": {"days_ago": (now - per[pid][-1]).days, "times": len(per[pid])}})
                continue

        # ---- 3) crosssell
        if cfg["on_crosssell"]:
            cand, src = Counter(), {}
            for pid in per:
                for other, n in co.get(pid, {}).items():
                    if other in per or other not in ok or blocked(uid, other) or n < cfg["min_copurchase"]:
                        continue
                    cand[other] += n
                    if other not in src or n > src[other][1]:
                        src[other] = (pid, n)
            if cand:
                pid = cand.most_common(1)[0][0]
                from_pid, n = src[pid]
                out.append({**base, "reason": "crosssell", "pid": pid,
                            "facts": {"from_pid": from_pid, "people": n}})
                continue

        fav = [p for p, _ in sorted(per.items(), key=lambda kv: (-len(kv[1]), -kv[1][-1].timestamp()))
               if p in ok and not blocked(uid, p)]

        # ---- 4) loyal
        if cfg["on_loyal"] and fav and n_orders.get(uid, 0) >= cfg["loyal_orders"]:
            out.append({**base, "reason": "loyal", "pid": fav[0],
                        "facts": {"days_ago": days_all, "times": len(per[fav[0]])}})
            continue

        # ---- 5) winback
        if cfg["on_winback"] and fav and days_all >= cfg["winback_days"]:
            out.append({**base, "reason": "winback", "pid": fav[0],
                        "facts": {"days_ago": days_all, "times": len(per[fav[0]])}})
            continue
        # recent happy buyer with nothing due: stay quiet (no spam)
    return out


# ------------------------------------------------------------------ results (what worked)
def results(db, attr_days=None):
    """Per reason: messages sent, customers who then bought within attr_days, orders and revenue.
    An order counts for the most recent message the customer got before it."""
    cfg = get_cfg(db)
    window = timedelta(days=attr_days or cfg["attr_days"])
    ensure_table(db)
    c = db.conn()
    logs = c.execute("SELECT id, user_id, product_id, reason, sent_at FROM smart_promo_log ORDER BY sent_at").fetchall()
    orders = c.execute("SELECT user_id, product_id, amount, COALESCE(paid_at, created_at) ts FROM orders "
                       "WHERE status='paid'").fetchall()
    c.close()
    by_user = defaultdict(list)
    for r in logs:
        t = _parse(r["sent_at"])
        if t:
            by_user[r["user_id"]].append((t, r))
    agg = {r: {"reason": r, "sent": 0, "buyers": set(), "orders": 0, "same_product": 0, "revenue": 0.0}
           for r in REASONS}
    for r in logs:
        agg.setdefault(r["reason"], {"reason": r["reason"], "sent": 0, "buyers": set(), "orders": 0,
                                     "same_product": 0, "revenue": 0.0})["sent"] += 1
    for o in orders:
        t = _parse(o["ts"])
        msgs = by_user.get(o["user_id"])
        if not t or not msgs:
            continue
        prior = [(mt, r) for mt, r in msgs if mt <= t]
        if not prior:
            continue
        mt, r = prior[-1]
        if t - mt > window:
            continue
        a = agg[r["reason"]]
        a["buyers"].add(o["user_id"])
        a["orders"] += 1
        a["revenue"] += float(o["amount"] or 0)
        if o["product_id"] == r["product_id"]:
            a["same_product"] += 1
    rows = []
    for a in agg.values():
        a["buyers"] = len(a["buyers"])
        a["revenue"] = round(a["revenue"], 2)
        a["rate"] = round(100.0 * a["buyers"] / a["sent"], 1) if a["sent"] else 0.0
        rows.append(a)
    rows.sort(key=lambda a: (-a["sent"], a["reason"]))
    total = {"sent": sum(a["sent"] for a in rows), "buyers": sum(a["buyers"] for a in rows),
             "orders": sum(a["orders"] for a in rows), "revenue": round(sum(a["revenue"] for a in rows), 2)}
    total["rate"] = round(100.0 * total["buyers"] / total["sent"], 1) if total["sent"] else 0.0
    return rows, total
