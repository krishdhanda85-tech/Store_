"""Admin website page for the AI Support Assistant (menu: Customers > Assistant)."""
import os

from flask import Blueprint, request, redirect, url_for, flash

import assistant_store as store

bp = Blueprint("assistant_admin", __name__)

PAGE = r"""{% extends 'base.html' %}{% block body %}
<h2>AI Assistant</h2>
<p class="muted">Answers customers' <b>General question</b> tickets inside the bot. Payment and bug tickets always go to a human.
Changes here are live in the bot immediately.</p>

<div class="card"><h3>Today</h3>
<div class="scroll"><table><tr><th>Answered today</th><th>Solved</th><th>Wanted a human</th><th>Sent to human by AI</th><th>All time</th></tr>
<tr><td>{{ st.today }}</td><td>{{ st.solved }}</td><td>{{ st.human }}</td><td>{{ st.escalated }}</td><td>{{ st.total }}</td></tr></table></div>
{% if not key %}<p class="failed" style="margin:8px 0 0">GEMINI_API_KEY is not set on the server, so the assistant cannot answer yet (customers go to a human).</p>{% endif %}
</div>

<div class="card"><h3>Settings</h3>
<form method="post"><input type="hidden" name="action" value="save">
<label><input type="checkbox" name="on" value="1" {{ 'checked' if c.on }} style="width:auto;margin-right:6px"> Assistant is ON</label>
<label>Name shown to customers <span class="hint">e.g. Assistant, Support Bot, Aria</span></label>
<input name="name" value="{{ c.name }}" maxlength="30">
<label>Max AI answers per day <span class="hint">keeps you inside the Gemini free tier. 0 = always send to a human</span></label>
<input name="max" type="number" min="0" max="5000" value="{{ c.max }}">
<label>Extra knowledge <span class="hint">anything the assistant should know: refund rules, working hours, how to renew, how to use a product. It answers ONLY from this + your products</span></label>
<textarea name="extra" rows="10" placeholder="Example:&#10;Support hours: 10am-10pm IST.&#10;Renewal: open Products, pick the same plan and pay again.&#10;Refund: only if the account does not work within 24h.">{{ c.extra }}</textarea>
<label>Always send to a human if the message contains <span class="hint">comma separated, e.g. refund, scam, chargeback, not working</span></label>
<input name="words" value="{{ words }}" placeholder="refund, scam, chargeback">
<button type="submit">Save</button></form></div>

<div class="card"><h3>Test the assistant</h3>
<p class="muted">Ask a question like a customer would. This uses your real products and the extra knowledge above (it counts toward today's limit).</p>
<form method="post"><input type="hidden" name="action" value="test">
<input name="q" value="{{ q }}" maxlength="300" placeholder="e.g. How long is the Netflix warranty?" required>
<button type="submit" class="green">Ask</button></form>
{% if tested %}<div class="sub" style="margin-top:10px;padding:10px;white-space:pre-wrap"><b>🤖 {{ c.name }}</b>
{{ answer if answer else '→ No AI answer, the customer would go to a human.' }}
{% if note %}<span class="failed">Reason: {{ note }}</span>{% endif %}</div>{% endif %}
</div>

<div class="card"><h3>Recent conversations</h3>
<div class="scroll"><table><tr><th>When</th><th>Customer</th><th>Question</th><th>Answer</th><th>Result</th></tr>
{% for r in rows %}<tr><td>{{ (r.created_at or '')[:16].replace('T',' ') }}</td>
<td>{{ ('@' + r.username) if r.username else r.user_id }}</td>
<td style="max-width:220px;white-space:pre-wrap">{{ r.question }}</td>
<td style="max-width:280px;white-space:pre-wrap">{{ r.answer or '—' }}</td>
<td><span class="{{ 'paid' if r.outcome == 'solved' else ('failed' if r.outcome in ('human','escalated') else 'muted') }}">{{ labels.get(r.outcome, r.outcome) }}</span></td></tr>
{% else %}<tr><td colspan="5">No conversations yet.</td></tr>{% endfor %}</table></div></div>
{% endblock %}"""

LABELS = {"answered": "Answered", "solved": "Solved ✅", "human": "Asked for a human",
          "escalated": "Sent to human"}


@bp.route("/assistant", methods=["GET", "POST"])
def home():
    import web
    db = web.db
    q, answer, tested, note = "", "", False, ""
    if request.method == "POST":
        f = request.form
        if f.get("action") == "save":
            try:
                mx = int(f.get("max") or 0)
                if not 0 <= mx <= 5000:
                    raise ValueError
            except ValueError:
                flash("Max answers per day must be a number from 0 to 5000.", "err")
                return redirect(url_for("assistant_admin.home"))
            db.set_setting("ai_support_on", 1 if f.get("on") else 0)
            db.set_setting("ai_name", (f.get("name") or store.DEFAULT_NAME).strip()[:30])
            db.set_setting("ai_support_max", mx)
            db.set_setting("ai_extra", (f.get("extra") or "").strip()[:4000])
            db.set_setting("ai_escalate_words", (f.get("words") or "").strip()[:300])
            flash("Assistant settings saved. They are live in the bot now.")
            return redirect(url_for("assistant_admin.home"))
        if f.get("action") == "test":
            import agents
            q = (f.get("q") or "").strip()[:300]
            c = store.cfg(db)
            tested = True
            if q and not store.wants_human(c["words"], q):
                agents.SUPPORT_DAILY_MAX = c["max"]
                info = store.shop_info(db, extra=c["extra"])
                try:
                    answer, note = agents.answer_support_ex(q, info)
                    answer = answer or ""
                except Exception as e:
                    note = "Error: " + str(e)[:300]
            elif q:
                note = "Your 'send to a human' keywords matched this question, so the AI is skipped."
    c = store.cfg(db, env_max=int(os.getenv("SUPPORT_AI_DAILY_MAX", "150") or 150))
    return web.render_str(PAGE, c=c, words=", ".join(c["words"]), st=store.stats(db), rows=store.recent(db),
                          labels=LABELS, key=bool(os.getenv("GEMINI_API_KEY", "").strip()),
                          q=q, answer=answer, tested=tested, note=note)


def register(app):
    app.register_blueprint(bp)
