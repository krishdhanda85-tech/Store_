"""
Smart promo: reads every customer's PAID order history and picks the best reason + product to
message them about. Pure data analysis (no AI). Gemini is only used later, to write the hook line.

Reasons (highest priority first):
  renew      bought a product ~one cycle ago (their own repeat gap, else SMART_RENEW_DAYS) -> time to renew
  pricedrop  a product they bought before is discounted now
  crosssell  customers who bought the same product also bought X (needs SMART_MIN_COPURCHASE people)
  loyal      3+ paid orders -> thank them and offer their favourite
  winback    no purchase for SMART_WINBACK_DAYS -> "we missed you" with their favourite
  newcomer   joined, never bought -> general pick (caller chooses the product)

Spam guard: a user gets at most one smart message per SMART_COOLDOWN_DAYS (newcomers: 7 days) and
the same product is not pushed to the same user again for SMART_PRODUCT_COOLDOWN_DAYS.

Env (all optional): SMART_RENEW_DAYS=28  SMART_WINBACK_DAYS=45  SMART_COOLDOWN_DAYS=3
  SMART_PRODUCT_COOLDOWN_DAYS=14  SMART_MIN_COPURCHASE=2  SMART_LOYAL_ORDERS=3  SMART_MAX_PER_RUN=300
"""
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


def _int(name, default):
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


RENEW_DAYS = _int("SMART_RENEW_DAYS", 28)
WINBACK_DAYS = _int("SMART_WINBACK_DAYS", 45)
COOLDOWN_DAYS = _int("SMART_COOLDOWN_DAYS", 3)
PRODUCT_COOLDOWN_DAYS = _int("SMART_PRODUCT_COOLDOWN_DAYS", 14)
MIN_COPURCHASE = _int("SMART_MIN_COPURCHASE", 2)
LOYAL_ORDERS = _int("SMART_LOYAL_ORDERS", 3)
MAX_PER_RUN = _int("SMART_MAX_PER_RUN", 300)
NEWCOMER_COOLDOWN_DAYS = 7
NEWCOMER_MIN_AGE_DAYS = 1


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


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
    users = c.execute("SELECT user_id, COALESCE(balance,0) bal, created_at FROM users").fetchall()
    log = c.execute("SELECT user_id, product_id, sent_at FROM smart_promo_log").fetchall()
    c.close()
    return orders, users, log


def analyze(db, user_ids, products, discount_of, is_available, now=None):
    """products: active product rows. discount_of(p) -> percent (0 = none). is_available(p) -> bool.
    Returns [{uid, reason, pid, facts{}, balance}] (pid None for newcomers: caller picks a product)."""
    now = now or datetime.now(timezone.utc)
    by_id = {p["id"]: p for p in products}
    ok = {pid for pid, p in by_id.items() if is_available(p)}
    orders, users, log = _load(db)
    wanted = set(user_ids)

    # history per user: {uid: {pid: [datetimes]}}
    hist = defaultdict(lambda: defaultdict(list))
    n_orders = Counter()
    spent = Counter()
    for o in orders:
        t = _parse(o["ts"])
        if t is None:
            continue
        hist[o["user_id"]][o["product_id"]].append(t)
        n_orders[o["user_id"]] += 1
        spent[o["user_id"]] += float(o["amount"] or 0)

    # who bought what together (people-count per product pair) + popularity
    pop = Counter()
    co = defaultdict(Counter)
    for uid, per in hist.items():
        pids = [p for p in per if p in by_id][:30]
        for a in pids:
            pop[a] += 1
            for b in pids:
                if a != b:
                    co[a][b] += 1

    # spam guard from the log
    last_any, last_prod = {}, {}
    for r in log:
        t = _parse(r["sent_at"])
        if t is None:
            continue
        if t > last_any.get(r["user_id"], datetime.min.replace(tzinfo=timezone.utc)):
            last_any[r["user_id"]] = t
        k = (r["user_id"], r["product_id"])
        if t > last_prod.get(k, datetime.min.replace(tzinfo=timezone.utc)):
            last_prod[k] = t

    uinfo = {u["user_id"]: u for u in users}
    out = []

    def blocked(uid, pid):
        t = last_prod.get((uid, pid))
        return bool(t and now - t < timedelta(days=PRODUCT_COOLDOWN_DAYS))

    for uid in user_ids:
        if len(out) >= MAX_PER_RUN:
            break
        if uid not in wanted:
            continue
        per = {p: sorted(ts) for p, ts in hist.get(uid, {}).items() if p in by_id}
        u = uinfo.get(uid)
        balance = float(u["bal"]) if u else 0.0
        cool = COOLDOWN_DAYS if per else NEWCOMER_COOLDOWN_DAYS
        t_last = last_any.get(uid)
        if t_last and now - t_last < timedelta(days=cool):
            continue

        base = {"uid": uid, "balance": balance, "orders": n_orders.get(uid, 0), "spent": spent.get(uid, 0.0)}

        if not per:  # ---------------- never bought
            created = _parse(u["created_at"]) if u else None
            if created and now - created < timedelta(days=NEWCOMER_MIN_AGE_DAYS):
                continue
            out.append({**base, "reason": "newcomer", "pid": None, "facts": {}})
            continue

        last_all = max(ts[-1] for ts in per.values())
        days_all = (now - last_all).days

        # ---- 1) renew: most overdue product, using the user's own repeat cycle when known
        best = None
        for pid, ts in per.items():
            if pid not in ok or blocked(uid, pid):
                continue
            days = sorted({t.date() for t in ts})
            gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days >= 1]
            cycle = min(90, max(7, statistics.median(gaps))) if gaps else RENEW_DAYS
            gap = (now - ts[-1]).days
            if cycle * 0.85 <= gap <= max(cycle * 2.5, WINBACK_DAYS):
                ratio = gap / cycle
                if best is None or ratio > best[0]:
                    best = (ratio, pid, gap, len(ts))
        if best:
            out.append({**base, "reason": "renew", "pid": best[1],
                        "facts": {"days_ago": best[2], "times": best[3]}})
            continue

        # ---- 2) pricedrop: something they bought before is discounted now
        drops = [(discount_of(by_id[p]), p) for p in per if p in ok and not blocked(uid, p)
                 and discount_of(by_id[p]) > 0]
        if drops:
            pct, pid = max(drops)
            out.append({**base, "reason": "pricedrop", "pid": pid,
                        "facts": {"days_ago": (now - per[pid][-1]).days, "times": len(per[pid])}})
            continue

        # ---- 3) crosssell: what other buyers of their products also bought
        cand = Counter()
        src = {}
        for pid in per:
            for other, n in co.get(pid, {}).items():
                if other in per or other not in ok or blocked(uid, other) or n < MIN_COPURCHASE:
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
        if fav and n_orders.get(uid, 0) >= LOYAL_ORDERS:
            out.append({**base, "reason": "loyal", "pid": fav[0],
                        "facts": {"days_ago": days_all, "times": len(per[fav[0]])}})
            continue

        # ---- 5) winback
        if fav and days_all >= WINBACK_DAYS:
            out.append({**base, "reason": "winback", "pid": fav[0],
                        "facts": {"days_ago": days_all, "times": len(per[fav[0]])}})
            continue
        # recent happy buyer with nothing due: stay quiet (no spam)
    return out
