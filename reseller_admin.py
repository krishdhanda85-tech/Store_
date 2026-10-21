import hashlib
import json
import secrets
import reseller_api
from flask import Blueprint, request, redirect, url_for, flash, render_template_string

admin_api = Blueprint('reseller_admin', __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<style>
.api-hero{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap;padding:22px;border-radius:18px;background:linear-gradient(135deg,#102b35,#142034);color:#f5fbff;margin-bottom:18px;border:1px solid #294353}
.api-hero h2{margin:0 0 7px;color:#fff}.api-hero p{margin:0;color:#b6c7d5;max-width:640px}.api-chip{border:1px solid #386b62;color:#78e5c8;background:#13382f;padding:6px 10px;border-radius:99px;font-size:12px;font-weight:800;white-space:nowrap}
.api-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(280px,.85fr);gap:16px;margin-bottom:16px}.api-card{border:1px solid var(--line);border-radius:15px;padding:20px;background:var(--surface);min-width:0}.api-card h3{font-size:17px;margin-bottom:5px}.api-card .sub{color:var(--muted);font-size:13px;margin-bottom:16px}.api-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.api-form .wide{grid-column:1/-1}.api-form label{display:block;font-size:13px;font-weight:700;margin-bottom:5px}.api-form input{width:100%;min-width:0}.api-form button{width:100%;padding:11px}.api-endpoints{display:grid;gap:9px}.api-endpoint{display:flex;align-items:center;gap:9px;flex-wrap:wrap;border:1px solid var(--line);border-radius:10px;padding:10px}.api-method{font-size:10px;font-weight:900;padding:3px 6px;border-radius:5px;background:var(--accent-soft);color:var(--link)}.api-endpoint code{overflow-wrap:anywhere}.api-note{font-size:12px;color:var(--muted);margin-top:12px}.api-table form{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:4px 0}.api-table input{min-width:85px;width:100px}.api-table button{white-space:nowrap}.api-actions{display:grid;gap:6px}.api-empty{padding:20px;text-align:center;color:var(--muted)}@media(max-width:760px){.api-grid{grid-template-columns:1fr}.api-form{grid-template-columns:1fr}.api-form .wide{grid-column:auto}.api-hero{padding:18px}.api-card{padding:15px}.api-table table{min-width:760px}}
.rs-wrap{margin-bottom:18px}.rs-head h3{font-size:19px;margin:0 0 4px}.rs-head .sub{color:var(--muted);font-size:13px;margin:0 0 14px}
.rs-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}.rs-stat{border:1px solid var(--line);background:var(--surface);border-radius:14px;padding:12px 14px}.rs-stat span{display:block;font-size:24px;font-weight:800;line-height:1.1}.rs-stat small{color:var(--muted);font-size:12px}.rs-stat.warn{border-color:#8a5a1f;background:rgba(240,165,60,.10)}
.rs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.rs-card{border:1px solid var(--line);background:var(--surface);border-radius:18px;padding:16px;display:flex;flex-direction:column;gap:12px;min-width:0}.rs-card.off{opacity:.62}
.rs-top{display:flex;align-items:center;gap:12px}.rs-avatar{width:42px;height:42px;border-radius:13px;background:linear-gradient(135deg,#1f8f7a,#2b6cb0);color:#fff;font-weight:800;font-size:18px;display:flex;align-items:center;justify-content:center;flex:none}
.rs-id{flex:1;min-width:0}.rs-id strong{display:block;overflow-wrap:anywhere;font-size:16px}.rs-tg{font-size:12px;color:var(--muted);margin-top:2px}.rs-tg code{font-size:13px;color:inherit;background:var(--accent-soft);padding:1px 6px;border-radius:6px;user-select:all}.rs-tg.unl{color:#e0a040;font-weight:700}
.rs-pill{font-size:11px;font-weight:800;padding:4px 10px;border-radius:99px;flex:none}.rs-pill.on{background:#d9f7e8;color:#126b45}.rs-pill.no{background:#fde2e2;color:#a12424}
.rs-bal{border-radius:14px;background:var(--accent-soft);padding:12px 14px}.rs-bal small{color:var(--muted);font-size:12px}.rs-amt{font-size:30px;font-weight:800;letter-spacing:-.5px;line-height:1.15}
.rs-warn{font-size:12.5px;line-height:1.45;border-radius:12px;padding:10px 12px;background:rgba(240,165,60,.13);border:1px solid #8a5a1f;color:#f0c27a}.rs-warn.soft{background:transparent;border-color:var(--line);color:var(--muted)}
.rs-acts{display:grid;gap:10px}.rs-acts form{margin:0}.rs-acts label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px}.rs-row{display:flex;gap:8px}.rs-row input[type=number]{flex:1;min-width:0;width:auto}.rs-row button{flex:none;min-width:76px}.rs-toggle{width:100%}
.rs-foot{font-size:11px;color:var(--muted);margin-top:auto}
@media(max-width:760px){.rs-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.rs-grid{grid-template-columns:1fr}}
.rs-bots{border:1px dashed var(--line);border-radius:14px;padding:10px 12px;display:grid;gap:8px}.rs-bots small{color:var(--muted);font-size:12px;font-weight:700}.rs-chips{display:flex;flex-wrap:wrap;gap:6px}.rs-chip{display:inline-flex;align-items:center;gap:4px;margin:0;border-radius:99px;background:var(--accent-soft);padding:3px 4px 3px 10px}.rs-chip code{font-size:13px;color:inherit;background:none;padding:0;user-select:all}.rs-chip button{border:0;background:transparent;color:var(--muted);padding:0 7px;min-width:0;font-size:16px;line-height:1;cursor:pointer}.rs-chip.dupe{outline:1px solid #8a5a1f}.rs-none{font-size:12px;color:var(--muted)}
</style>
<div class="api-hero"><div><h2>Reseller API</h2><p>Create and manage API access for resellers, adjust their discounts or balance, and connect external Telegram bots or websites to your shop.</p></div><span class="api-chip">API v1 · JSON</span></div>
<section class="api-card" style="margin-bottom:16px"><div class="page-head"><div><h3>Seller availability</h3><p class="sub" style="margin:4px 0 0">Reseller bots can check this status before showing a Request OTP button.</p></div><span class="badge" style="background:{{ '#d9f7e8' if online else '#fde2e2' }};color:{{ '#126b45' if online else '#a12424' }}">{{ 'ONLINE' if online else 'OFFLINE' }}</span></div><form method="post" class="inline"><input type="hidden" name="action" value="presence"><input type="hidden" name="online" value="{{ 0 if online else 1 }}"><button class="{{ 'red' if online else 'green' }}" style="max-width:220px">{{ 'Go Offline' if online else 'I’m Online — Accept OTP Requests' }}</button><span class="muted">{{ presence_updated or 'Not changed yet' }}</span></form></section>
<div class="api-grid"><section class="api-card"><h3>Create reseller</h3><p class="sub">Generate a private API key for a new reseller.</p>
<form method="post" class="api-form"><input type="hidden" name="action" value="create">
<div class="wide"><label>Reseller name</label><input name="name" required maxlength="80" placeholder="e.g. Partner Shop"></div>
<div class="wide"><label>Telegram user ID <span class="muted">(required; the reseller API uses this user's bot wallet)</span></label><input name="user_id" type="number" required placeholder="e.g. 7095639003"></div>
<div><label>Discount from base price (%)</label><input name="discount" type="number" min="0" max="90" step="0.1" value="0"></div>
<div style="display:flex;align-items:end"><button type="submit">Create API key</button></div></form>
<p class="api-note">The key is shown after creation. Copy it privately; never post it in a public channel.</p></section>
<section class="api-card"><h3>Connection details</h3><p class="sub">Use these endpoints in your reseller software.</p><div class="api-endpoints">
<div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/products</code></div><div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/balance</code></div><div class="api-endpoint"><span class="api-method">POST</span><code>/api/v1/orders</code></div><div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/orders</code></div><div class="api-endpoint"><span class="api-method">GET</span><code>/api/v1/seller-status</code></div><div class="api-endpoint"><span class="api-method">POST</span><code>/api/v1/orders/ORDER_ID/request-otp</code></div></div>
<p class="api-note">Authentication: <code>Authorization: Bearer YOUR_KEY</code> or <code>X-API-Key: YOUR_KEY</code>. Full setup guide: <a href="/api/docs" target="_blank" rel="noopener">open API documentation ↗</a>.</p></section></div>
<section class="api-card" style="margin-bottom:16px"><h3>Made-to-order queue</h3><p class="sub">Order aane par pending rahega. Pehle OTP bhej, phir profile aur PIN. OTP requests reseller bot se aayengi only when you are online.</p><div class="scroll api-table"><table>
<tr><th>Order</th><th>Product</th><th>Reseller</th><th>Qty</th><th>Customer reference</th><th>Contact</th><th>Status / Action</th></tr>
{% for o in mto_orders %}<tr><td><strong>{{o.id}}</strong><div class="muted">{{o.created_at}}</div></td><td>{{o.product_name}}</td><td>{{o.reseller_name}}</td><td>{{o.quantity}}</td><td>{{o.customer_ref or '-'}}</td><td>{{o.contact or '-'}}</td><td>
{% if o.status in ('processing','otp_requested','otp_ready') %}<form method="post" action="{{url_for('reseller_admin.resellers')}}"><input type="hidden" name="action" value="complete_simple"><input type="hidden" name="order_id" value="{{o.id}}"><label>Product ready? Delivery details (1 line per item)</label><textarea name="details" required maxlength="5000" rows="2" placeholder="email:password / link / code"></textarea><button class="green" type="submit" onclick="return confirm('Mark DONE and send to reseller customer?')">Done - send to reseller</button></form><hr>{% endif %}
{% if o.status in ('processing','otp_requested') %}<form method="post" action="{{url_for('reseller_admin.resellers')}}"><input type="hidden" name="action" value="send_otp"><input type="hidden" name="order_id" value="{{o.id}}"><label>OTP to send first</label><input name="otp" required maxlength="500" autocomplete="off" placeholder="Enter current OTP"><button class="green" type="submit" {% if not online %}disabled{% endif %}>Send OTP to reseller API</button></form>{% if not online %}<div class="muted">Set status online to accept/request OTP.</div>{% endif %}
{% elif o.status == 'otp_ready' %}<span class="badge">OTP sent</span><form method="post" action="{{url_for('reseller_admin.resellers')}}"><input type="hidden" name="action" value="complete_mto"><input type="hidden" name="order_id" value="{{o.id}}"><label>Profile / login details</label><textarea name="profile" required maxlength="5000" rows="2" placeholder="Profile details"></textarea><label>PIN</label><input name="pin" required maxlength="1000" autocomplete="off" placeholder="Enter PIN"><button class="green" type="submit" onclick="return confirm('Publish profile and PIN to this reseller order?')">Send Profile + PIN & Complete</button></form>
{% else %}<span class="badge">{{o.status}}</span><details><summary>Delivered details</summary><p><b>OTP:</b> <code>{{o.otp_text or '—'}}</code></p><p><b>Profile:</b> <pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{o.profile_text or '—'}}</pre></p><p><b>PIN:</b> <code>{{o.pin_text or '—'}}</code></p></details>{% endif %}</td></tr>
{% else %}<tr><td colspan="7"><div class="api-empty">No made-to-order reseller orders yet.</div></td></tr>{% endfor %}
</table></div><p class="api-note">The reseller bot must poll <code>GET /api/v1/orders/ORDER_ID</code>: status <code>otp_ready</code> means send OTP; status <code>completed</code> means send profile and PIN. It can check <code>GET /api/v1/seller-status</code> for online/offline.</p></section>
<section class="rs-wrap">
<div class="rs-head"><div><h3>Reseller accounts</h3><p class="sub">Each reseller is matched by <b>API key → Telegram ID → bot wallet</b>. The name is only a label.</p></div></div>
<div class="rs-stats">
<div class="rs-stat"><span>{{ rows|length }}</span><small>Resellers</small></div>
<div class="rs-stat"><span>{{ rows|selectattr('active')|list|length }}</span><small>Active</small></div>
<div class="rs-stat"><span>{{ rows|selectattr('user_id')|list|length }}</span><small>Wallet linked</small></div>
<div class="rs-stat {{ 'warn' if rows|rejectattr('user_id')|list|length }}"><span>{{ rows|rejectattr('user_id')|list|length }}</span><small>Not linked</small></div>
</div>
<div class="rs-grid">
{% for r in rows %}
<article class="rs-card {{ '' if r.active else 'off' }}">
<div class="rs-top">
<div class="rs-avatar">{{ (r.name or '?')[:1]|upper }}</div>
<div class="rs-id"><strong>{{r.name}}</strong>
{% if r.user_id %}<div class="rs-tg">Telegram ID <code>{{r.user_id}}</code></div>{% else %}<div class="rs-tg unl">Not linked to a Telegram ID</div>{% endif %}</div>
<span class="rs-pill {{ 'on' if r.active else 'no' }}">{{'Active' if r.active else 'Disabled'}}</span>
</div>
<div class="rs-bots"><small>Bot IDs</small>
<div class="rs-chips">{% for b in r.bots %}<form method="post" class="rs-chip {{ 'dupe' if b in r.bot_warn else '' }}"><input type="hidden" name="action" value="remove_bot"><input type="hidden" name="reseller_id" value="{{r.id}}"><input type="hidden" name="bot_id" value="{{b}}"><code>{{b}}</code><button title="Remove" aria-label="Remove bot ID {{b}}" onclick="return confirm('Remove bot ID {{b}}?')">×</button></form>{% else %}<span class="rs-none">none added</span>{% endfor %}</div>
{% if r.bot_warn %}<div class="rs-warn">⚠️ {{ r.bot_warn|join(', ') }} also exists as its own reseller account. Disable that extra account to avoid confusion.</div>{% endif %}
<form method="post" class="rs-row"><input type="hidden" name="action" value="add_bot"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="bot_id" type="number" required placeholder="Add bot ID" aria-label="Add bot ID"><button>Add</button></form></div>
<div class="rs-bal"><small>{{ 'Bot wallet balance' if r.user_id else 'Separate API balance' }}</small><div class="rs-amt">${{'%.2f'|format(r.wallet if r.user_id else r.balance)}}</div></div>
{% if r.dup %}<div class="rs-warn">⚠️ Another linked account has this same name. Make sure the reseller's bot uses the key of the <b>linked</b> one, otherwise it will show no balance.</div>{% elif not r.user_id %}<div class="rs-warn soft">This account has its own balance. Link a Telegram ID to use the bot wallet automatically.</div>{% endif %}
<div class="rs-acts">
<form method="post"><label>Discount %</label><div class="rs-row"><input type="hidden" name="action" value="discount"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="discount" type="number" min="0" max="90" step="0.1" value="{{r.price_discount}}" aria-label="Discount percentage"><button>Save</button></div></form>
<form method="post"><label>Add balance</label><div class="rs-row"><input type="hidden" name="action" value="topup"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="amount" type="number" min="0.01" step="0.01" required placeholder="Amount" aria-label="Top-up amount"><button>Add</button></div></form>
{% if not r.user_id %}<form method="post"><label>Link bot wallet</label><div class="rs-row"><input type="hidden" name="action" value="link"><input type="hidden" name="reseller_id" value="{{r.id}}"><input name="user_id" type="number" required placeholder="Telegram ID" aria-label="Telegram ID"><button>Link</button></div></form>{% endif %}
<form method="post"><input type="hidden" name="action" value="toggle"><input type="hidden" name="reseller_id" value="{{r.id}}"><button class="rs-toggle {{ 'red' if r.active else 'green' }}">{{'Disable access' if r.active else 'Enable access'}}</button></form>
</div>
<div class="rs-foot">ID #{{r.id}} · created {{ (r.created_at or '')[:10] }}</div>
</article>
{% else %}<div class="api-empty">No resellers yet. Create your first reseller above.</div>{% endfor %}
</div></section>
<script>
// While the reseller dashboard is open, refresh the seller-online heartbeat.
(function(){
  function beat(){fetch("/reseller-admin/presence-heartbeat",{method:"POST",credentials:"same-origin",headers:{"X-Requested-With":"fetch"}}).catch(function(){});}
  beat(); setInterval(beat,60000);
})();
</script>
{% endblock %}"""


def register(app, db):
    @admin_api.route('/resellers', methods=['GET', 'POST'])
    def resellers():
        if request.method == 'POST':
            action = request.form.get('action', 'create')
            if action == 'presence':
                online = 1 if request.form.get('online') == '1' else 0
                oc = reseller_api.connect()
                try:
                    oc.execute("INSERT INTO api_shop_presence(id,online,updated_at) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET online=excluded.online,updated_at=excluded.updated_at", (online, reseller_api.now()))
                    oc.commit()
                    flash('Seller status set to ' + ('ONLINE. Reseller bots can now show Request OTP.' if online else 'OFFLINE. New OTP requests are blocked.'))
                finally:
                    oc.close()
                return redirect(url_for('reseller_admin.resellers'))
            c = db.conn()
            try:
                if action == 'create':
                    name = request.form.get('name', '').strip()[:80]
                    try: tg_check = int(request.form.get('user_id', '').strip() or 0)
                    except ValueError: tg_check = 0
                    if not name:
                        flash('Reseller name is required.', 'err')
                    elif tg_check <= 0:
                        flash('Telegram user ID is required, so the reseller always uses their bot wallet.', 'err')
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
                elif action == 'send_otp':
                    order_id = request.form.get('order_id', '').strip()[:80]
                    otp = request.form.get('otp', '').strip()[:500]
                    if not order_id or not otp:
                        flash('Order ID and OTP are required.', 'err')
                    else:
                        oc = reseller_api.connect()
                        try:
                            oc.execute('BEGIN IMMEDIATE')
                            order = oc.execute("SELECT * FROM api_reseller_orders WHERE id=? AND status IN ('processing','otp_requested')", (order_id,)).fetchone()
                            if not order:
                                oc.rollback(); flash('Order is not waiting for OTP.', 'err')
                            else:
                                oc.execute("UPDATE api_reseller_orders SET otp_text=?,status='otp_ready' WHERE id=?", (otp, order_id))
                                oc.commit()
                                flash('OTP saved for this order. The reseller bot can now fetch it and send it to its customer.')
                        except Exception:
                            oc.rollback(); flash('Could not save OTP. Check server logs.', 'err')
                        finally:
                            oc.close()
                elif action == 'complete_mto':
                    order_id = request.form.get('order_id', '').strip()[:80]
                    profile = request.form.get('profile', '').strip()[:5000]
                    pin = request.form.get('pin', '').strip()[:1000]
                    if not order_id or not profile or not pin:
                        flash('Order ID, profile details and PIN are required.', 'err')
                    else:
                        oc = reseller_api.connect()
                        try:
                            oc.execute('BEGIN IMMEDIATE')
                            order = oc.execute("SELECT * FROM api_reseller_orders WHERE id=? AND status='otp_ready'", (order_id,)).fetchone()
                            if not order:
                                oc.rollback(); flash('Order must have OTP sent before profile and PIN.', 'err')
                            else:
                                oc.execute("UPDATE api_reseller_orders SET profile_text=?,pin_text=?,status='completed' WHERE id=? AND status='otp_ready'", (profile, pin, order_id))
                                oc.commit()
                                flash('Profile and PIN are now available to the reseller API. Order completed.')
                        except Exception:
                            oc.rollback(); flash('Could not complete order. Check server logs.', 'err')
                        finally:
                            oc.close()
                elif action == 'complete_simple':
                    order_id = request.form.get('order_id', '').strip()[:80]
                    details = request.form.get('details', '').strip()[:5000]
                    if not order_id or not details:
                        flash('Order ID and delivery details are required.', 'err')
                    else:
                        items = [ln.strip() for ln in details.splitlines() if ln.strip()]
                        oc = reseller_api.connect()
                        try:
                            oc.execute('BEGIN IMMEDIATE')
                            order = oc.execute("SELECT * FROM api_reseller_orders WHERE id=? AND status IN ('processing','otp_requested','otp_ready')", (order_id,)).fetchone()
                            if not order:
                                oc.rollback(); flash('Order is not pending.', 'err')
                            else:
                                oc.execute("UPDATE api_reseller_orders SET items_json=?,status='completed' WHERE id=?", (json.dumps(items), order_id))
                                oc.commit()
                                flash('Order done. The reseller bot will receive the details and send them to its customer.')
                        except Exception:
                            oc.rollback(); flash('Could not complete order. Check server logs.', 'err')
                        finally:
                            oc.close()
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
                elif action == 'link':
                    try: rid, tg = int(request.form.get('reseller_id', '0')), int(request.form.get('user_id', '0'))
                    except ValueError: rid, tg = 0, 0
                    row = c.execute('SELECT user_id, balance FROM api_resellers WHERE id=?', (rid,)).fetchone()
                    if not row or tg <= 0:
                        flash('Reseller or Telegram ID is invalid.', 'err')
                    elif row['user_id']:
                        flash('This reseller is already linked.', 'err')
                    elif c.execute('SELECT 1 FROM api_resellers WHERE user_id=?', (tg,)).fetchone():
                        flash('That Telegram ID already has a reseller account.', 'err')
                    else:
                        old = float(row['balance'] or 0)
                        c.execute('UPDATE api_resellers SET user_id=?, balance=0 WHERE id=?', (tg, rid))
                        c.commit()
                        if old > 0:
                            db.admin_topup(tg, old)      # carry the old separate balance into the bot wallet
                        flash('Linked. This reseller API now uses the bot wallet balance automatically.')
                elif action in ('add_bot', 'remove_bot'):
                    try: rid, bid = int(request.form.get('reseller_id', '0')), int(request.form.get('bot_id', '0'))
                    except ValueError: rid, bid = 0, 0
                    reseller_api.init_schema()
                    row = c.execute('SELECT bot_ids FROM api_resellers WHERE id=?', (rid,)).fetchone()
                    if not row or bid <= 0:
                        flash('Reseller or bot ID is invalid.', 'err')
                    else:
                        mine = [x for x in str(row['bot_ids'] or '').split(',') if x.strip()]
                        if action == 'remove_bot':
                            mine = [x for x in mine if x != str(bid)]
                            c.execute('UPDATE api_resellers SET bot_ids=? WHERE id=?', (','.join(mine), rid))
                            c.commit(); flash('Bot ID removed.')
                        else:
                            taken = None
                            for o in c.execute('SELECT id,name,bot_ids FROM api_resellers WHERE id!=?', (rid,)).fetchall():
                                if str(bid) in [x.strip() for x in str(o['bot_ids'] or '').split(',')]:
                                    taken = o; break
                            if str(bid) in mine:
                                flash('That bot ID is already added here.', 'err')
                            elif taken:
                                flash('Bot ID %s is already added to reseller #%s (%s).' % (bid, taken['id'], taken['name']), 'err')
                            else:
                                mine.append(str(bid))
                                c.execute('UPDATE api_resellers SET bot_ids=? WHERE id=?', (','.join(mine), rid))
                                c.commit(); flash('Bot ID added.')
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
        try: reseller_api.init_schema()   # makes sure the bot_ids column exists
        except Exception: pass
        c = db.conn()
        rows = c.execute('SELECT r.id,r.name,r.balance,r.active,r.price_discount,r.created_at,r.user_id,r.bot_ids,COALESCE(u.balance,0) AS wallet '
                         'FROM api_resellers r LEFT JOIN users u ON u.user_id=r.user_id ORDER BY r.id DESC').fetchall()
        c.close()
        rows = [dict(r) for r in rows]
        linked_names = {(r['name'] or '').strip().lower() for r in rows if r['user_id']}
        wallet_ids = {str(r['user_id']) for r in rows if r['user_id']}
        for r in rows:
            r['bots'] = [x.strip() for x in str(r.get('bot_ids') or '').split(',') if x.strip()]
            r['bot_warn'] = [b for b in r['bots'] if b in wallet_ids and b != str(r['user_id'])]
        for r in rows:
            r['dup'] = (not r['user_id']) and (r['name'] or '').strip().lower() in linked_names
        oc = reseller_api.connect()
        try:
            mto_orders = oc.execute("""SELECT o.*,p.name AS product_name,r.name AS reseller_name
                FROM api_reseller_orders o JOIN products p ON p.id=o.product_id
                JOIN api_resellers r ON r.id=o.reseller_id
                WHERE o.status IN ('processing','otp_requested','otp_ready','completed')
                ORDER BY CASE WHEN o.status='otp_requested' THEN 0 WHEN o.status='processing' THEN 1 WHEN o.status='otp_ready' THEN 2 ELSE 3 END,o.created_at DESC LIMIT 100""").fetchall()
            presence = oc.execute("SELECT online,updated_at FROM api_shop_presence WHERE id=1").fetchone()
            online = bool(presence['online']) if presence else False
            presence_updated = presence['updated_at'] if presence else None
            if online and presence_updated:
                try:
                    from datetime import datetime, timezone
                    stamp = datetime.fromisoformat(str(presence_updated).replace('Z', '+00:00'))
                    if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
                    online = (datetime.now(timezone.utc) - stamp).total_seconds() <= 180
                except (TypeError, ValueError):
                    online = False
        finally:
            oc.close()
        import web
        return web.render_str(PAGE, rows=rows, mto_orders=mto_orders, online=online, presence_updated=presence_updated)
    @admin_api.route('/reseller-admin/presence-heartbeat', methods=['POST'])
    def presence_heartbeat():
        oc = reseller_api.connect()
        try:
            oc.execute("UPDATE api_shop_presence SET updated_at=? WHERE id=1 AND online=1", (reseller_api.now(),))
            oc.commit()
        finally:
            oc.close()
        return ('', 204)

    app.register_blueprint(admin_api)
