"""
Logic for the in-bot message editor (/texts). No Telegram imports, so it can be tested on its own.

The owner can change any customer message (the TEXT dictionary in bot.py) from inside the bot.
A custom text is stored in the shop database (settings table, key  txt:<lang>:<key>)  and
is used instead of the built-in one.  "Reset" deletes it and the built-in text is used again.
A broken custom text can never break the shop: bot.py falls back to the built-in text.
"""
import re
import string

PAGE_SIZE = 8
MAX_LEN = 3800

# friendly names for the messages people edit most (everything else shows its key)
LABELS = {
    "welcome": "Welcome + language choice",
    "menu_ready": "Main menu welcome",
    "main_menu": "Main menu",
    "wallet": "Wallet page",
    "btn_addfunds": "Button: Add funds",
    "btn_upi": "Button: UPI",
    "btn_crypto": "Button: Crypto",
    "btn_upi_paid": "Button: I've paid (send UTR)",
    "ask_amount": "Ask deposit amount (crypto)",
    "ask_amount_upi": "Ask deposit amount (UPI)",
    "dep_created": "Crypto deposit created",
    "dep_paid": "Deposit received",
    "dep_expired": "Deposit expired",
    "cancel_dep": "Deposit cancelled",
    "upi_created": "UPI deposit screen (QR + UPI ID)",
    "upi_none": "UPI not available",
    "upi_rejected": "UPI payment not verified",
    "utr_ask": "Ask for UTR",
    "utr_sent": "UTR received (waiting)",
    "utr_invalid": "UTR invalid",
    "utr_dup": "UTR already used",
    "utr_closed": "UTR window closed",
    "txhash_ask": "Ask for TXN hash",
    "txhash_wait": "TXN hash received (waiting)",
    "warranty": "Warranty page",
    "warranty_default": "Warranty text (default)",
    "terms": "Terms & conditions",
    "help": "Help",
    "referral": "Referral page",
    "share_text": "Referral share text",
    "support_list": "Support: ticket list",
    "ticket_created": "Support ticket created",
    "orders": "My orders",
    "placed": "Order placed",
    "bought": "Order delivered",
    "sold_out": "Sold out",
    "out": "Out of stock label",
    "in_stock": "In stock label",
    "need_more": "Not enough balance",
    "api_home": "Reseller API home",
    "api_intro": "Reseller API intro",
}

# values shown in previews
SAMPLES = {
    "id": "UPI-DEMO12345", "credit": "1.00", "amount": "1.00", "bal": "5.00", "inr": "100", "rate": "100",
    "upi": "yourname@upi", "minutes": "30", "utr": "123456789012", "shop": "My Shop", "details": "Address: 0xABC…",
    "name": "Product name", "price": "1.00", "qty": "1", "total": "1.00", "user": "customer", "stock": "10",
}

ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a",
                "tg-spoiler", "tg-emoji", "blockquote"}
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)([^<>]*)>")
_BAD_AMP = re.compile(r"&(?!(amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")


def label(key: str) -> str:
    return LABELS.get(key, key)


def is_plain(key: str) -> bool:
    """Button captions are plain text (Telegram shows HTML tags literally there)."""
    return key.startswith("btn_")


def fields_of(text: str):
    """Placeholder names like {id} used in a text."""
    out = []
    try:
        for _lit, field, _spec, _conv in string.Formatter().parse(text):
            if field is not None and field not in out:
                out.append(field)
    except ValueError:
        pass
    return out


def html_error(s: str):
    """Tiny pre-check of Telegram HTML. Returns an error text or None. (Telegram's own parser is the final judge.)"""
    stack = []
    for m in _TAG.finditer(s):
        closing, name = m.group(1), m.group(2).lower()
        if name not in ALLOWED_TAGS:
            return f"The tag <{name}> is not allowed in Telegram messages."
        if closing:
            if not stack or stack[-1] != name:
                return f"The closing tag </{name}> does not match an opening tag."
            stack.pop()
        else:
            stack.append(name)
    if stack:
        return f"The tag <{stack[-1]}> is not closed."
    rest = _TAG.sub("", s)
    if "<" in rest:
        return "A lone '<' was found. Write it as &lt; if you want to show it."
    if _BAD_AMP.search(rest):
        return "A lone '&' was found. Write it as &amp; if you want to show it."
    return None


def validate(key: str, new: str, default_text: str):
    """Returns an error text, or None if the new text is safe to save."""
    if not new or not new.strip():
        return "The text is empty."
    if len(new) > MAX_LEN:
        return f"Too long ({len(new)} characters). Telegram allows about {MAX_LEN} here."
    allowed = fields_of(default_text)
    if allowed:
        try:
            for _lit, field, spec, conv in string.Formatter().parse(new):
                if field is None:
                    continue
                if spec or conv or field not in allowed:
                    names = ", ".join("{" + a + "}" for a in allowed)
                    return (f"The placeholder {{{field}}} is not valid here. "
                            f"You can use: {names}. To show a literal curly bracket write {{{{ or }}}}.")
        except ValueError:
            return "Curly brackets must be written as {{ and }} unless they are placeholders."
    if is_plain(key):
        if "<" in new or ">" in new:
            return "Button text must be plain text (no HTML tags)."
        return None
    return html_error(new)


class _Safe(dict):
    def __missing__(self, k):
        return f"[{k}]"


def render(text: str, default_text: str) -> str:
    """The text as a customer would see it, with sample values filled in."""
    if not fields_of(default_text):
        return text
    try:
        return string.Formatter().vformat(text, (), _Safe(SAMPLES))
    except Exception:
        return text


def match(key: str, default_text: str, q: str) -> bool:
    q = (q or "").strip().lower()
    return (not q) or q in key.lower() or q in label(key).lower() or q in default_text.lower()
