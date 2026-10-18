import hashlib
import secrets
import reseller_api
from flask import Blueprint, request, redirect, url_for, flash, render_template_string

admin_api = Blueprint('reseller_admin', __name__)

PAGE = r"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reseller API</title><style>body{font:16px system-ui;background:#0b1020;color:#eee;margin:20px}section{background:#172238;padding:16px;border-radius:12px;margin:14px 0}input,button{padding:10px;margin:5px 0;max-width:100%;box-sizing:border-box}input{background:#0d1524;color:white;border:1px solid #345;width:100%}button{background:#2563eb;color:white;border:0;border-radius:8px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px;border-bottom:1px solid #345}a{color:#9cc5ff}.wrap{overflow-x:auto}.msg{overflow-wrap:anywhere;background:#20385a;padding:10px}</style></head><body><p><a href="/">← Admin home</a></p><h2>Reseller API management</h2>{% for cat,msg in get_flashed_messages(with_categories=true) %}<div class="msg">{{msg}}</div>{% endfor %}<section><h3>Create reseller</h3><form method="post"><input type="hidden" name="action" value="create"><label>Name</label><input name="name" required maxlength="80" placeholder="Reseller name"><label>Telegram user ID (optional: API orders then use that user's bot wallet)</label><input name="user_id" type="number" placeholder="e.g. 7095639003"><label>Discount from shop base price (%)</label><input name="discount" type="number" min="0" max="90" step="0.1" value="0"><button>Create API key</button></form><p>API key is displayed only once. Copy it and give it privately to the reseller.</p></section><section><h3>Resellers</h3><div class="wrap"><table><tr><th>ID</th><th>Name</th><th>Telegram ID</th><th>Balance</th><th>Discount</th><th>Status</th><th>Actions</th></tr>{% for r in rows %}<tr><td>{{r.id}}</td><td>{{r.name}}</td><td>{{r.user_id or '—'}}</td><td>{{'%.2f'|format(r.wallet if r.user_id else r.balance)}}{% if r.user_id %} <small>(bot wallet)</small>{% endif %}</td><td><form method="post"><input type="hidden" name="action" value="discount"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="discount" type="number" min="0" max="90" step="0.1" value="{{r.price_discount}}"><button>Save %</button></form></td><td>{{'Active' if r.active else 'Disabled'}}</td><td><form method="post"><input type="hidden" name="action" value="topup"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="amount" type="number" min="0.01" step="0.01" required placeholder="Top-up amount"><button>Add balance</button></form><form method="post"><input type="hidden" name="action" value="toggle"><input type="hidden" name="reseller_id" value="{{r.id}}"><button>{{'Disable' if r.active else 'Enable'}}</button></form></td></tr>{% else %}<tr><td colspan="7">No resellers created yet.</td></tr>{% endfor %}</table></div></section><section><h3>API endpoints</h3><p><code>GET /api/v1/products</code> · <code>GET /api/v1/balance</code> · <code>POST /api/v1/orders</code> · <code>GET /api/v1/orders</code> · <code>GET /api/v1/orders/ORDER_ID</code></p><p>Send header <code>Authorization: Bearer YOUR_API_KEY</code>. See README for examples.</p></section></body></html>"""


def register(app, db):
    @admin_api.route('/resellers', methods=['GET', 'POST'])
    def resellers():
        if request.method == 'POST':
            action = request.form.get('action', 'create')
            c = db.conn()
            try:
                if action == 'create':
                    name = request.form.get('name', '').strip()[:80]
                    if not name:
                        flash('Reseller name is required.', 'err')
                    else:
                        key = 'rs_live_' + secrets.token_urlsafe(32)
                        try: discount = float(request.form.get('discount', '0') or 0)
                        except ValueError: discount = 0
                        discount = max(0.0, min(90.0, discount))
                        try: tg = int(request.form.get('user_id', '').strip() or 0) or None
                        except ValueError: tg = None
                        try:
                            reseller_api.init_schema()   # makes sure the key_enc column exists
                            c.execute('INSERT INTO api_resellers(name,key_hash,balance,active,price_discount,created_at,user_id,key_enc) VALUES(?,?,0,1,?,?,?,?)',
                                      (name, hashlib.sha256(key.encode()).hexdigest(), discount, db._now(), tg, reseller_api._encrypt_key(key)))
                            c.commit()
                            flash('NEW API KEY (copy now; it will not be shown again): ' + key)
                        except Exception:
                            flash('That Telegram ID already has a reseller account.', 'err')
                elif action == 'topup':
                    try: rid, amount = int(request.form.get('reseller_id', '0')), float(request.form.get('amount', '0'))
                    except ValueError: rid, amount = 0, 0
                    if amount <= 0 or amount > 1000000:
                        flash('Enter a positive top-up amount.', 'err')
                    else:
                        row = c.execute('SELECT user_id FROM api_resellers WHERE id=? AND active=1', (rid,)).fetchone()
                        if row and row['user_id']:
                            db.admin_topup(row['user_id'], amount)   # linked: top up the bot wallet
                            flash('Bot wallet topped up.')
                        else:
                            cur = c.execute('UPDATE api_resellers SET balance=ROUND(balance+?,2) WHERE id=? AND active=1', (amount, rid))
                            c.commit()
                            flash('Reseller balance topped up.' if cur.rowcount else 'Reseller not found or disabled.', '' if cur.rowcount else 'err')
                elif action == 'discount':
                    try: rid, d = int(request.form.get('reseller_id', '0')), float(request.form.get('discount', '0') or 0)
                    except ValueError: rid, d = 0, 0
                    d = max(0.0, min(90.0, d))
                    c.execute('UPDATE api_resellers SET price_discount=? WHERE id=?', (d, rid))
                    c.commit(); flash('Discount updated.')
                elif action == 'toggle':
                    try: rid = int(request.form.get('reseller_id', '0'))
                    except ValueError: rid = 0
                    c.execute('UPDATE api_resellers SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?', (rid,))
                    c.commit(); flash('Reseller status updated.')
            finally:
                c.close()
            return redirect(url_for('reseller_admin.resellers'))
        c = db.conn()
        rows = c.execute('SELECT r.id,r.name,r.balance,r.active,r.price_discount,r.created_at,r.user_id,COALESCE(u.balance,0) AS wallet '
                         'FROM api_resellers r LEFT JOIN users u ON u.user_id=r.user_id ORDER BY r.id DESC').fetchall()
        c.close()
        return render_template_string(PAGE, rows=rows)
    app.register_blueprint(admin_api)
