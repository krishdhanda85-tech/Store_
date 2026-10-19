import hashlib
import secrets
import reseller_api
from flask import Blueprint, request, redirect, url_for, flash, render_template_string

admin_api = Blueprint('reseller_admin', __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<style>
.api-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap;padding:22px;border-radius:18px;background:linear-gradient(135deg,#102b35,#142034);color:#f5fbff;margin-bottom:18px;border:1px solid #294353}
.api-hero h2{margin:0 0 7px;color:#fff}.api-hero p{margin:0;color:#b6c7d5;max-width:640px}.api-chip{border:1px solid #386b62;color:#78e5c8;background:#13382f;padding:6px 10px;border-radius:99px;font-size:12px;font-weight:800;white-space:nowrap}
.api-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(280px,.85fr);gap:16px;margin-bottom:16px}.api-card{border:1px solid var(--line);border-radius:15px;padding:20px;background:var(--surface);min-width:0}.api-card h3{font-size:17px;margin-bottom:5px}.api-card .sub{color:var(--muted);font-size:13px;margin-bottom:16px}.api-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.api-form .wide{grid-column:1/-1}.api-form label{display:block;font-size:13px;font-weight:700;margin-bottom:5px}.api-form input{width:100%;min-width:0}.api-form button{width:100%;padding:11px}.api-endpoints{display:grid;gap:9px}.api-endpoint{display:flex;align-items:center;gap:9px;flex-wrap:wrap;border:1px solid var(--line);border-radius:10px;padding:10px}.api-method{font-size:10px;font-weight:900;padding:3px 6px;border-radius:5px;background:var(--accent-soft);color:var(--link)}.api-endpoint code{overflow-wrap:anywhere}.api-note{font-size:12px;color:var(--muted);margin-top:12px}.api-table form{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:4px 0}.api-table input{min-width:85px;width:100px}.api-table button{white-space:nowrap}.api-actions{display:grid;gap:6px}.api-empty{padding:20px;text-align:center;color:var(--muted)}@media(max-width:760px){.api-grid{grid-template-columns:1fr}.api-form{grid-template-columns:1fr}.api-form .wide{grid-column:auto}.api-hero{padding:18px}.api-card{padding:15px}.api-table table{min-width:760px}}
</style>
<div class="api-hero"><div><h2>Reseller API</h2><p>Create and manage API access for resellers, adjust their discounts or balance, and connect external Telegram bots or websites to your shop.</p></div><span class="api-chip">API v1 · JSON</span></div>
<div class="api-grid"><section class="api-card"><h3>Create reseller</h3><p class="sub">Generate a private API key for a new reseller.</p>
<form method="post" class="api-form"><input type="hidden" name="action" value="create">
<div class="wide"><label>Reseller name</label><input name="name" required maxlength="80" placeholder="e.g. Partner Shop"></div>
<div class="wide"><label>Telegram user ID <span class="muted">(optional; orders use this user's bot wallet)</span></label><input name="user_id" type="number" placeholder="e.g. 7095639003"></div>
<div><label>Discount from base price (%)</label><input name="discount" type="number" min="0" max="90" step="0.1" value="0"></div>
<div style="display:flex;align-items:end"><button type="submit">Create API key</button></div></form>
<p class="api-note">The key is shown after creation. Copy it privately; never post it in a public channel.</p></section>
<section class="api-card"><h3>Connection details</h3><p class="sub">Use these endpoints in your reseller software.</p><div class="api-endpoints">
<div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/products</code></div><div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/balance</code></div><div class="api-endpoint"><span class="api-method">POST</span><code>/api/v1/orders</code></div><div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/orders</code></div></div>
<p class="api-note">Authentication: <code>Authorization: Bearer YOUR_KEY</code> or <code>X-API-Key: YOUR_KEY</code>. Full setup guide: <a href="/api/docs" target="_blank" rel="noopener">open API documentation ↗</a>.</p></section></div>
<section class="api-card"><h3>Reseller accounts</h3><p class="sub">Manage access, wallet balance, and pricing. Disabling a reseller immediately blocks their API key.</p><div class="scroll api-table"><table>
<tr><th>ID</th><th>Reseller</th><th>Telegram ID</th><th>Balance</th><th>Discount</th><th>Status</th><th>Actions</th></tr>
{% for r in rows %}<tr><td>{{r.id}}</td><td><strong>{{r.name}}</strong></td><td>{{r.user_id or '-'}}</td>
<td><strong>{{'%.2f'|format(r.wallet if r.user_id else r.balance)}}</strong>{% if r.user_id %}<div class="muted">Bot wallet</div>{% endif %}</td>
<td><form method="post"><input type="hidden" name="action" value="discount"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="discount" type="number" min="0" max="90" step="0.1" value="{{r.price_discount}}" aria-label="Discount percentage"><button>Save %</button></form></td>
<td><span class="badge" style="background:{{ '#d9f7e8' if r.active else '#fde2e2' }};color:{{ '#126b45' if r.active else '#a12424' }}">{{'Active' if r.active else 'Disabled'}}</span></td>
<td><div class="api-actions"><form method="post"><input type="hidden" name="action" value="topup"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="amount" type="number" min="0.01" step="0.01" required placeholder="Amount" aria-label="Top-up amount"><button>Add balance</button></form>
<form method="post"><input type="hidden" name="action" value="toggle"><input type="hidden" name="reseller_id" value="{{r.id}}"><button class="{{ 'red' if r.active else 'green' }}">{{'Disable access' if r.active else 'Enable access'}}</button></form></div></td></tr>
{% else %}<tr><td colspan="7"><div class="api-empty">No resellers yet. Create your first reseller above.</div></td></tr>{% endfor %}
</table></div></section>
{% endblock %}"""


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
                        key = reseller_api._new_key()
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
        import web
        return web.render_str(PAGE, rows=rows)
    app.register_blueprint(admin_api)
