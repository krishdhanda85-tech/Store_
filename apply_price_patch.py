"""Run in the folder that has your bot.py:  python apply_price_patch.py
Adds '| $price' after the product name inside categories/folders. Makes bot.py.bak first."""
import shutil, sys

OLD = '''    if full:
        label = name if len(name) <= 60 else name[:59].rstrip() + "…"
'''
NEW = '''    if full:
        try:                                    # inside a category: show the price after the name
            tail = f" | ${money(db.get_effective_price(uid, p['id'], p['price']))}"
        except Exception:
            tail = ""
        room = 60 - len(tail)
        label = (name if len(name) <= room else name[:room - 1].rstrip() + "…") + tail
'''

src = open("bot.py", encoding="utf-8").read()
if "inside a category: show the price after the name" in src:
    sys.exit("Already patched. Nothing to do.")
if src.count(OLD) != 1:
    sys.exit("Could not find the exact block in grid_button (your bot.py changed). Nothing was changed.")
shutil.copy2("bot.py", "bot.py.bak")
open("bot.py", "w", encoding="utf-8").write(src.replace(OLD, NEW))
print("Done. bot.py updated, backup saved as bot.py.bak")
