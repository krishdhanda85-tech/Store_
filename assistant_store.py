"""Shared helpers for the AI Support Assistant: settings, shop info, and the answer log.
Used by the bot (bot.py) and the admin page (assistant_admin.py), so both always agree.
Settings live in the shop's `settings` table, so changes on the website are live in the bot at once."""
from datetime import datetime, timezone

DEFAULT_NAME = "Assistant"
DEFAULT_WARRANTY = ("Every product has its own warranty, shown on the product page before you pay. "
                    "If something does not work, open Support and send your order number.")


def ensure_table(db):
    c = db.conn()
    c.execute("CREATE TABLE IF NOT EXISTS ai_support_log ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, question TEXT, answer TEXT, "
              "outcome TEXT, created_at TEXT)")
    c.commit()
    c.close()


def cfg(db, env_on=True, env_max=150):
    """Current assistant settings (website value wins over the Railway variable)."""
    on = str(db.get_setting("ai_support_on", "1" if env_on else "0")) != "0"
    try:
        mx = int(float(db.get_setting("ai_support_max", env_max)))
    except (TypeError, ValueError):
        mx = env_max
    return {
        "on": on, "max": max(0, mx),
        "name": (str(db.get_setting("ai_name", DEFAULT_NAME)) or DEFAULT_NAME).strip()[:30],
        "extra": str(db.get_setting("ai_extra", "") or "").strip()[:4000],
        "words": [w.strip().lower() for w in str(db.get_setting("ai_escalate_words", "") or "").split(",") if w.strip()],
    }


def shop_info(db, warranty=DEFAULT_WARRANTY, extra=""):
    """Public shop facts the assistant may use (no user data)."""
    lines = []
    for p in db.list_products()[:40]:
        manual = (p["kind"] or "auto") == "manual"
        stock = "always available (made to order)" if manual else ("in stock" if p["stock"] > 0 else "out of stock")
        bits = [f"{p['name']}: ${float(p['price']):.2f}", stock]
        for label, key in (("delivery", "delivery"), ("warranty", "warranty"), ("about", "description")):
            v = (p[key] or "").strip().replace("\n", " ")
            if v:
                bits.append(f"{label}: {v[:160]}")
        lines.append("- " + "; ".join(bits))
    text = ("Products:\n" + "\n".join(lines) + f"\n\nGeneral warranty policy: {warranty}\n"
            "Payments: wallet top-up with USDT (BEP20 / Binance Pay) or UPI where enabled. "
            "Orders are delivered in the bot chat right after payment.")
    if extra:
        text += "\n\nExtra information from the shop owner:\n" + extra
    return text[:9000]


def wants_human(words, text):
    t = (text or "").lower()
    return any(w in t for w in words)


def log_answer(db, user_id, question, answer, outcome):
    try:
        ensure_table(db)
        c = db.conn()
        cur = c.execute("INSERT INTO ai_support_log(user_id,question,answer,outcome,created_at) VALUES(?,?,?,?,?)",
                        (user_id, (question or "")[:1000], (answer or "")[:1000], outcome,
                         datetime.now(timezone.utc).isoformat()))
        c.commit()
        rid = cur.lastrowid
        c.close()
        return rid
    except Exception:
        return None


def set_outcome(db, log_id, outcome):
    if not log_id:
        return
    try:
        c = db.conn()
        c.execute("UPDATE ai_support_log SET outcome=? WHERE id=?", (outcome, log_id))
        c.commit()
        c.close()
    except Exception:
        pass


def recent(db, limit=40):
    ensure_table(db)
    c = db.conn()
    rows = c.execute("SELECT l.*, u.username FROM ai_support_log l LEFT JOIN users u ON u.user_id=l.user_id "
                     "ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return rows


def stats(db):
    ensure_table(db)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c = db.conn()
    r = c.execute("SELECT COUNT(*) total, "
                  "SUM(outcome='answered') pending, SUM(outcome='solved') solved, "
                  "SUM(outcome='human') human, SUM(outcome='escalated') escalated, "
                  "SUM(substr(created_at,1,10)=?) today FROM ai_support_log", (today,)).fetchone()
    c.close()
    return {k: (r[k] or 0) for k in ("total", "pending", "solved", "human", "escalated", "today")}
