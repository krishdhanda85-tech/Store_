"""Order history helpers: group/sub-order summaries and the downloadable order file (.txt).

A bulk made-to-order purchase (e.g. 6 Spotify emails) is stored as one order row per email:
ORD-XXXXXXXXXX-1, ORD-XXXXXXXXXX-2 ... all sharing group_id = ORD-XXXXXXXXXX.
The customer sees ONE line (the group) in My Orders; the admin site's Pending orders page still
lists every sub-order separately, so each one can be marked done / refunded on its own.
"""
from datetime import datetime

WORD = {"paid": "Done", "processing": "Pending", "pending": "Pending",
        "refunded": "Refunded", "failed": "Failed"}
ICON = {"paid": "✅", "processing": "⏳", "pending": "⏳", "refunded": "↩️", "failed": "❌"}


def word(status) -> str:
    return WORD.get(str(status), str(status))


def group_summary(g) -> str:
    """Short status for one group row (see Database.user_order_groups)."""
    n, done, pend, bad = int(g["n"] or 0), int(g["done"] or 0), int(g["pend"] or 0), int(g["bad"] or 0)
    if n <= 1:
        return "Done" if done else ("Pending" if pend else "Refunded")
    parts = []
    if done:
        parts.append(f"{done} done")
    if pend:
        parts.append(f"{pend} pending")
    if bad:
        parts.append(f"{bad} refunded")
    return ", ".join(parts)


def group_icon(g) -> str:
    if int(g["pend"] or 0):
        return "⏳"
    if int(g["bad"] or 0) and not int(g["done"] or 0):
        return "↩️"
    return "✅"


def _date(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return str(iso or "")


def build_txt(gid, rows, items_by_order, name) -> tuple:
    """-> (filename, bytes). rows = every order row of the group (oldest first)."""
    total = sum(float(r["amount"] or 0) for r in rows)
    qty = sum(int(r["quantity"] or 0) for r in rows)
    out = [f"Order: {gid}", f"Product: {name}" + (f" x{qty}" if qty > 1 else ""),
           f"Date: {_date(rows[0]['created_at'])}", f"Total: ${total:.2f}", ""]
    n = 0
    for r in rows:
        items = items_by_order.get(r["id"]) or []
        if items:                                    # auto delivery: the delivered accounts / keys / links
            for it in items:
                n += 1
                out.append(f"{n}. {it}")
        else:                                        # made to order: one line per email
            n += 1
            line = f"{n}. {r['id']}  {r['contact'] or ''}  {word(r['status'])}"
            if r["admin_note"]:
                line += f"  ({r['admin_note']})"
            out.append(line)
    return f"order_{gid}.txt", ("\n".join(out) + "\n").encode("utf-8")
