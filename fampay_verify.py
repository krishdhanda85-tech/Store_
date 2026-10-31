"""
FamPay payment check through Gmail (IMAP) - used to AUTO-approve UPI top-ups.

How it fits: the customer pays the exact INR amount and sends the 12-digit UTR (existing flow).
The bot then looks in the Gmail inbox that receives your FamPay "money received" e-mails.
Only if ALL of these are true the deposit is approved automatically:
  1. the e-mail comes from an allowed sender domain (FAMPAY_ALLOWED_SENDERS)
  2. Gmail itself says DKIM passed for that domain (stops fake e-mails sent to your inbox)
  3. it is a credit ("received"/"credited"), not a debit
  4. the INR amount in the e-mail is EXACTLY the requested amount
  5. the customer's UTR is written in the e-mail
  6. the e-mail is newer than the top-up request
Anything that does not match is NOT rejected - it stays in the normal manual review on the admin site.
A UTR can only be used once (deposits.utr is checked in db.submit_utr), so a payment cannot be replayed.

Railway variables (feature is OFF unless all three are set):
  FAMPAY_GMAIL=you@gmail.com
  FAMPAY_APP_PASSWORD=<16-char Google App Password>
  FAMPAY_ALLOWED_SENDERS=domain1.com,domain2.com   <- the domain in the From address of a real FamPay e-mail
  FAMPAY_REQUIRE_DKIM=1                            <- keep 1 (0 = less safe)
"""
import html as _html
import imaplib
import logging
import os
import re
import ssl
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email import message_from_bytes, policy
from email.utils import parsedate_to_datetime

log = logging.getLogger("fampay")

GMAIL = os.getenv("FAMPAY_GMAIL", "").strip()
APP_PASSWORD = os.getenv("FAMPAY_APP_PASSWORD", "").replace(" ", "")
ALLOWED = [d.strip().lower().lstrip("@") for d in os.getenv("FAMPAY_ALLOWED_SENDERS", "").split(",") if d.strip()]
REQUIRE_DKIM = os.getenv("FAMPAY_REQUIRE_DKIM", "1") != "0"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def enabled() -> bool:
    return bool(GMAIL and APP_PASSWORD and ALLOWED)


def _paise(v):
    try:
        n = Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if n <= 0:
        return None
    return int((n * 100).quantize(Decimal("1")))


def _amounts(text: str):
    """All amounts in the text, as paise. Prefers numbers written with Rs/INR/₹."""
    cur = [p for p in (_paise(m) for m in re.findall(
        r"(?:rs\.?|inr|₹)\s*([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)", text, re.I)) if p]
    if cur:
        return cur
    return [p for p in (_paise(m) for m in re.findall(
        r"(?<![\w.,])([0-9]+(?:,[0-9]{2,3})*(?:\.[0-9]{1,2})?)(?![\w]|\.[0-9]|,[0-9])", text)) if p]


def _domain_ok(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == a or d.endswith("." + a) for a in ALLOWED)


def _dkim_ok(msg) -> bool:
    """Trust only the TOP Authentication-Results header (added last, by Gmail's own server)."""
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False
    top = re.sub(r"\s+", " ", str(headers[0])).strip().lower()
    if not re.match(r"^mx\.google\.com\b", top):
        return False
    for seg in top.split(";"):
        seg = seg.strip()
        if not seg.startswith("dkim=pass"):
            continue
        m = re.search(r"header\.(?:d|i)=@?([^\s;]+)", seg)
        if m:
            dom = m.group(1).split("@")[-1]
            if _domain_ok(dom):
                return True
    return False


def _text(msg) -> str:
    out = [str(msg.get("Subject") or "")]
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            body = part.get_content()
        except Exception:
            body = ""
        if part.get_content_type() == "text/html":
            body = _html.unescape(re.sub(r"<[^>]+>", " ", body))
        out.append(body)
    return "\n".join(out)


def evaluate(msg, inr_amount, utr: str, created_at: datetime, now: datetime = None):
    """Pure check of one e-mail. Returns (ok, reason)."""
    now = now or datetime.now(timezone.utc)
    addr = (msg.get("From") or "")
    m = re.search(r"@([A-Za-z0-9.\-]+)", addr)
    if not m or not _domain_ok(m.group(1)):
        return False, "sender not allowed"
    if REQUIRE_DKIM and not _dkim_ok(msg):
        return False, "dkim not verified"
    try:
        sent = parsedate_to_datetime(str(msg.get("Date")))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
    except Exception:
        return False, "no date"
    if sent < created_at - timedelta(minutes=5) or sent > now + timedelta(minutes=5):
        return False, "email time outside request window"
    text = _text(msg).lower()
    if "debited" in text or not any(w in text for w in ("received", "credited")):
        return False, "not a credit"
    want = _paise(inr_amount)
    if want is None or want not in _amounts(text):
        return False, "amount differs"
    if utr not in text:
        return False, "utr not in email"
    return True, "ok"


def _imap_date(d: datetime) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _as_dt(s) -> datetime:
    try:
        d = datetime.fromisoformat(str(s))
    except Exception:
        return datetime.now(timezone.utc) - timedelta(hours=6)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def verify_pending(rows):
    """rows: deposit rows with id, utr, inr_amount, created_at. One Gmail login for all.
    Returns [(deposit_id, ok, reason)]. Never raises."""
    results = []
    if not enabled() or not rows:
        return results
    conn = None
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ssl.create_default_context())
        conn.login(GMAIL if "@" in GMAIL else GMAIL + "@gmail.com", APP_PASSWORD)
        conn.select("INBOX", readonly=True)
        for d in rows:
            utr = str(d["utr"] or "")
            if not re.fullmatch(r"\d{12}", utr):
                results.append((d["id"], False, "bad utr"))
                continue
            created = _as_dt(d["created_at"])
            since = _imap_date(created - timedelta(days=1))
            typ, data = conn.search(None, "SINCE", since, "TEXT", utr)
            ids = data[0].split() if typ == "OK" and data and data[0] else []
            ok, why = False, "no matching email yet"
            for mid in ids[-10:]:
                typ, parts = conn.fetch(mid, "(BODY.PEEK[])")
                if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                msg = message_from_bytes(parts[0][1], policy=policy.default)
                ok, why = evaluate(msg, d["inr_amount"], utr, created)
                if ok:
                    break
            results.append((d["id"], ok, why))
    except Exception:
        log.exception("FamPay Gmail check failed")
    finally:
        try:
            if conn:
                conn.logout()
        except Exception:
            pass
    return results
