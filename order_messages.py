"""Customer messages for an order: received -> (delay notice) -> delivered / problem.

Usage (in the place where the order is taken and delivered):

    from order_messages import OrderNotifier
    n = OrderNotifier(lambda text: bot.send_message(chat_id, text))   # any function that sends text
    n.start()            # right after the order is placed: sends "received", arms the delay notice
    ...                  # provider buys the product (ensure_funds may top up here)
    n.done()             # product delivered: cancels the delay notice, sends "delivered"
    # or, if the order failed:
    n.failed()

Texts can be changed without editing code: set env ORDER_MSG_RECEIVED / ORDER_MSG_DELAY /
ORDER_MSG_DELIVERED / ORDER_MSG_FAILED (use \\n for a new line). Delay notice time: ORDER_DELAY_NOTICE_SEC (default 360).
"""
import logging
import os
import threading

log = logging.getLogger("order_messages")

MESSAGES = {
    "received": ("✅ Thank you for your order!\n"
                 "Your order is being processed and will be delivered here within 2 to 6 minutes. "
                 "Please keep this chat open.\n"
                 "We appreciate your patience."),
    "delay": ("Thank you for waiting. Your order is taking slightly longer than usual, but it is still in progress. "
              "We expect to deliver it within the next 5 minutes."),
    "delivered": ("🎉 Your order has been delivered!\n"
                  "Thank you for choosing us. We hope you are happy with your purchase.\n"
                  "If you face any issue, just reply here and we will be glad to help. We would love to see you again. 💙"),
    "failed": ("We are sorry, we could not complete your order automatically.\n"
               "Please reply here and our team will resolve it for you as soon as possible."),
}


def text(key):
    """Message text; env ORDER_MSG_<KEY> overrides the default."""
    return (os.getenv("ORDER_MSG_" + key.upper()) or MESSAGES[key]).replace("\\n", "\n")


class OrderNotifier:
    def __init__(self, send, delay_after=None):
        self.send = send
        try:
            self.delay_after = float(delay_after if delay_after is not None else os.getenv("ORDER_DELAY_NOTICE_SEC", 360))
        except ValueError:
            self.delay_after = 360.0
        self._timer = None
        self._closed = False
        self._lock = threading.Lock()

    def _say(self, key):
        try:
            self.send(text(key))
        except Exception:
            log.warning("could not send order message %r", key, exc_info=True)   # never break the order because of a message

    def start(self):
        self._say("received")
        with self._lock:
            if self._closed or self.delay_after <= 0:
                return
            self._timer = threading.Timer(self.delay_after, self._late)
            self._timer.daemon = True
            self._timer.start()

    def _late(self):
        with self._lock:
            if self._closed:
                return
        self._say("delay")

    def _finish(self, key):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._timer:
                self._timer.cancel()
        self._say(key)

    def done(self):
        self._finish("delivered")

    def failed(self):
        self._finish("failed")
