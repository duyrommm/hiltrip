import os
import random
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session, abort, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)   # đặt SECRET_KEY trên Render (Environment)
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")   # đặt ADMIN_KEY trên Render → /admin cần ?key=<ADMIN_KEY> một lần

_lock = threading.Lock()

game_data = {
    "phase": "LOBBY",         # LOBBY -> NIGHT -> DAY -> END
    "players": {},            # { username: {"role": str, "alive": bool, "target": str, "acted": bool} }
    "night_result": "",
    "vote_result": "",        # kết quả treo cổ trong ngày
    "voted_today": False,
    "winner": None,           # None | "Wolf" | "Village"
    "version": 0,             # tăng mỗi khi trạng thái đổi → trình duyệt tự cập nhật
}

ROLES = {
    "Wolf":     {"name": "Ma Sói",   "icon": "🐺", "cls": "wolf",  "hint": "Đêm nay chọn một người để cắn."},
    "Seer":     {"name": "Tiên Tri", "icon": "🔮", "cls": "seer",  "hint": "Mỗi đêm soi một người, biết người đó có phải Sói không."},
    "Guard":    {"name": "Bảo Vệ",   "icon": "🛡️", "cls": "guard", "hint": "Mỗi đêm che chở một người khỏi Sói. Không bảo vệ cùng một người hai đêm liền."},
    "Villager": {"name": "Dân Làng", "icon": "🧑‍🌾", "cls": "villa", "hint": "Nhắm mắt ngủ và chờ trời sáng."},
}


def bump():
    game_data["version"] += 1


def check_win():
    """Sói thắng khi số Sói còn sống >= số người còn lại; Dân thắng khi hết Sói.
    Có người thắng → chuyển sang END. Trả về True nếu ván đã kết thúc."""
    alive = [i for i in game_data["players"].values() if i["role"] and i["alive"]]
    wolves = sum(1 for i in alive if i["role"] == "Wolf")
    others = len(alive) - wolves
    if wolves == 0:
        game_data["winner"] = "Village"
    elif wolves >= others:
        game_data["winner"] = "Wolf"
    else:
        return False
    game_data["phase"] = "END"
    return True


@app.before_request
def guard_admin():
    if not ADMIN_KEY or not request.path.startswith("/admin"):
        return None
    if request.args.get("key") == ADMIN_KEY:
        session["is_admin"] = True
    if not session.get("is_admin"):
        abort(403)
    return None


# ---------------------------------------------------------------- giao diện chung
SHELL = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#161b33">
<title>{{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;700&family=Lora:wght@600;700&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#161b33; --ink-2:#212847; --line:#343c63;
  --moon:#efe6cf; --mist:#a9adc4;
  --wolf:#e0574a; --seer:#a98be6; --guard:#5bb8b4; --villa:#d6b25e;
  --ok:#6fcf8e; --dead:#e0574a;
  --radius:14px;
}
*{box-sizing:border-box}
html,body{margin:0}
body{
  min-height:100vh; background:var(--ink); color:var(--moon);
  font:400 16px/1.55 "Be Vietnam Pro",system-ui,sans-serif;
  transition:background .8s ease,color .8s ease;
}
/* Ban ngày: cả trang chuyển sang sáng */
body.day{ --ink:#f1e9d6; --ink-2:#fffaf0; --line:#ddd0b2; --moon:#2b2a33; --mist:#6c6657;
  --ok:#2f7d4a; --dead:#b8352a; --wolf:#b8352a; --seer:#6b4fb3; --guard:#2a7b78; --villa:#8a6512; }
.wrap{max-width:480px;margin:0 auto;padding:20px 16px 48px}
.brand{font:700 15px/1 "Lora",Georgia,serif;color:var(--mist);display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}
.brand a{color:var(--mist);font:500 14px "Be Vietnam Pro",system-ui,sans-serif;text-decoration:none;border:1px solid var(--line);padding:6px 12px;border-radius:999px}
h1{font:700 30px/1.15 "Lora",Georgia,serif;margin:0 0 6px}
.sub{color:var(--mist);margin:0 0 22px}
.panel{background:var(--ink-2);border:1px solid var(--line);border-radius:var(--radius);padding:18px}
.panel + .panel{margin-top:14px}
.panel h2{font:600 15px/1.3 "Be Vietnam Pro",system-ui,sans-serif;color:var(--mist);margin:0 0 10px}
input[type=text],select{
  width:100%;font:inherit;color:var(--moon);background:var(--ink);
  border:1px solid var(--line);border-radius:10px;padding:13px 14px;margin-bottom:12px;
}
button,.btn{
  display:block;width:100%;font:700 16px "Be Vietnam Pro",system-ui,sans-serif;border:0;border-radius:10px;
  padding:14px;cursor:pointer;background:var(--villa);color:#1d1a10;text-align:center;text-decoration:none;
}
button:active{transform:translateY(1px)}
:focus-visible{outline:3px solid var(--seer);outline-offset:2px}
.btn-wolf{background:var(--wolf);color:#fff}
.btn-seer{background:var(--seer);color:#fff}
.btn-guard{background:var(--guard);color:#fff}
.btn-ghost{background:transparent;color:var(--mist);border:1px solid var(--line)}
.roster{list-style:none;margin:0;padding:0}
.roster li{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.roster li:last-child{border-bottom:0}
.roster .me{font-weight:700}
.tag{font-size:13px;padding:3px 10px;border-radius:999px;border:1px solid currentColor}
.tag.ok{color:var(--ok)} .tag.dead{color:var(--dead)}
.roster li.out span:first-child{text-decoration:line-through;color:var(--mist)}
.count{color:var(--mist);font-weight:500}
.note{border-radius:10px;padding:12px 14px;background:var(--ink);border:1px solid var(--line)}
.note.done{border-color:var(--ok);color:var(--ok)}
.note.bad{border-color:var(--dead);color:var(--dead)}

/* Lá bài vai trò: chạm để lật, mặc định úp để người bên cạnh không nhìn thấy */
.card-role{
  width:100%;border:1px dashed var(--line);background:var(--ink-2);color:var(--mist);
  border-radius:18px;padding:30px 18px;text-align:center;font-weight:500;
}
.card-role .face{display:none}
.card-role.open{border-style:solid}
.card-role.open .back{display:none}
.card-role.open .face{display:block}
.secret{display:none}
body.revealed .secret{display:block}
.card-role .icon{font-size:56px;line-height:1}
.card-role .rname{font:700 30px/1.2 "Lora",Georgia,serif;margin:10px 0 4px}
.card-role .rhint{color:var(--mist);font-weight:400}
.card-role.wolf.open{border-color:var(--wolf)}  .wolf .rname{color:var(--wolf)}
.card-role.seer.open{border-color:var(--seer)}  .seer .rname{color:var(--seer)}
.card-role.guard.open{border-color:var(--guard)}.guard .rname{color:var(--guard)}
.card-role.villa.open{border-color:var(--villa)}.villa .rname{color:var(--villa)}
.r-wolf{color:var(--wolf)} .r-seer{color:var(--seer)} .r-guard{color:var(--guard)} .r-villa{color:var(--villa)}

.phase-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px;vertical-align:middle}
.live{font-size:13px;color:var(--mist);text-align:center;margin-top:18px}
.live.off .phase-dot{background:var(--dead)}
.flash{animation:flash .6s ease}
@keyframes flash{from{opacity:.35}to{opacity:1}}
@media (prefers-reduced-motion:reduce){body{transition:none}.flash{animation:none}}
.stack > * + *{margin-top:10px}
</style>
</head>
<body class="{{ body_cls }}">
<div class="wrap">
  <div id="app">{{ body|safe }}</div>
  <p class="live" id="live"><span class="phase-dot"></span>Tự cập nhật</p>
</div>
<script>
(function(){
  var v = {{ version }}, partial = "{{ partial_url }}", busy = false;
  var app = document.getElementById('app'), live = document.getElementById('live');
  var opened = false;  // lá bài đã lật chưa — giữ nguyên sau mỗi lần cập nhật

  function wire(){
    var c = app.querySelector('.card-role');
    if (c){
      if (opened) c.classList.add('open');
      c.onclick = function(){ opened = !opened; c.classList.toggle('open', opened); document.body.classList.toggle('revealed', opened); };
    }
    app.querySelectorAll('form[data-confirm]').forEach(function(f){
      f.onsubmit = function(){ return confirm(f.dataset.confirm); };
    });
    var cls = app.querySelector('[data-body]');
    if (cls) document.body.className = cls.dataset.body;
    document.body.classList.toggle('revealed', opened && !!c);
  }

  function refresh(){
    var a = document.activeElement;
    if (busy || (a && (a.tagName === 'SELECT' || a.tagName === 'INPUT'))) { setTimeout(refresh, 1500); return; }
    busy = true;
    // giữ lựa chọn đang chọn dở trong ô select
    var sel = app.querySelector('select'), keep = sel ? sel.value : null;
    fetch(partial, {cache:'no-store'}).then(function(r){
      if (r.redirected || !r.ok) { location.reload(); return null; }
      return r.text();
    }).then(function(html){
      if (html === null) return;
      var phaseBefore = (app.querySelector('[data-phase]')||{}).dataset;
      app.innerHTML = html;
      var phaseAfter = (app.querySelector('[data-phase]')||{}).dataset;
      if (phaseBefore && phaseAfter && phaseBefore.phase !== phaseAfter.phase){
        app.classList.remove('flash'); void app.offsetWidth; app.classList.add('flash');
        if (phaseAfter.phase === 'NIGHT') opened = false;
      }
      var s2 = app.querySelector('select');
      if (s2 && keep) { s2.value = keep; if (s2.value !== keep) s2.selectedIndex = 0; }
      wire();
    }).finally(function(){ busy = false; });
  }

  function poll(){
    fetch('/state', {cache:'no-store'}).then(function(r){ return r.json(); }).then(function(d){
      live.classList.remove('off');
      if (d.v !== v){ v = d.v; refresh(); }
    }).catch(function(){ live.classList.add('off'); });
  }

  wire();
  setInterval(function(){ if (!document.hidden) poll(); }, 2000);
  document.addEventListener('visibilitychange', function(){ if (!document.hidden) poll(); });
})();
</script>
</body>
</html>"""


def page(title, body, body_cls, partial_url):
    if request.args.get("partial"):
        return body
    return render_template_string(SHELL, title=title, body=body, body_cls=body_cls,
                                  partial_url=partial_url, version=game_data["version"])


# ---------------------------------------------------------------- màn hình người chơi
PLAYER_BODY = """
<div data-phase="{{ phase }}" data-body="{{ body_cls }}">
<div class="brand"><span>HILtrip Ma Sói</span>
  {% if me %}<a href="/logout">Thoát</a>{% endif %}</div>

{% if not me %}
  <h1>Vào làng</h1>
  <p class="sub">Nhập tên để mọi người nhận ra bạn.</p>
  <form method="POST" action="/join" class="panel">
    <input type="text" name="username" maxlength="30" placeholder="Tên của bạn, ví dụ: Khoa" required autocomplete="off" autofocus>
    <button type="submit">Vào phòng</button>
  </form>
  {% if phase != 'LOBBY' %}<p class="sub" style="margin-top:14px">Ván đang diễn ra. Bạn sẽ được chia vai ở ván sau.</p>{% endif %}

{% elif phase == 'LOBBY' %}
  <h1>Chào {{ me }}</h1>
  <p class="sub">Đang chờ quản trò chia vai. Màn hình sẽ tự chuyển.</p>
  <div class="panel">
    <h2>Trong phòng <span class="count">· {{ players|length }} người</span></h2>
    <ul class="roster">
      {% for p in players %}<li class="{{ 'me' if p == me }}"><span>{{ p }}</span>{% if p == me %}<span class="count">bạn</span>{% endif %}</li>{% endfor %}
    </ul>
  </div>

{% elif phase == 'NIGHT' %}
  <h1>🌙 Trời tối</h1>
  <p class="sub">Che màn hình rồi chạm vào lá bài để xem vai.</p>
  {% if role %}
  <button type="button" class="card-role {{ role.cls }}">
    <span class="back">Chạm để lật bài</span>
    <span class="face"><span class="icon">{{ role.icon }}</span>
      <span class="rname" style="display:block">{{ role.name }}</span>
      <span class="rhint">{{ role.hint }}</span></span>
  </button>
  {% else %}
  <div class="note">Bạn vào sau khi chia vai nên ván này chỉ xem. Chờ ván sau nhé.</div>
  {% endif %}

  {% if info and not info.alive %}
    <div class="note bad" style="margin-top:14px">Bạn đã bị loại. Hãy giữ im lặng và quan sát.</div>
  {% elif info and info.role == 'Wolf' %}
    <div class="panel secret" style="margin-top:14px">
    {% if mates %}<p class="sub" style="margin-bottom:12px">Đồng bọn: <b class="r-wolf">{{ mates|join(', ') }}</b></p>{% endif %}
    {% if not info.acted %}
      <h2>Cắn ai đêm nay?</h2>
      <form method="POST" action="/action">
        <select name="target">
          {% for p, i in players.items() %}{% if p != me and i.role and i.alive and i.role != 'Wolf' %}<option value="{{ p }}">{{ p }}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-wolf">Chốt mục tiêu</button>
      </form>
    {% else %}
      <div class="note done">Đã chốt: {{ info.target }}. Chờ trời sáng.</div>
    {% endif %}
    </div>
  {% elif info and info.role == 'Seer' %}
    <div class="panel secret" style="margin-top:14px">
    {% if not info.acted %}
      <h2>Soi ai đêm nay?</h2>
      <form method="POST" action="/action">
        <select name="target">
          {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}">{{ p }}{% if p in seen %} (đã soi){% endif %}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-seer">Soi người này</button>
      </form>
    {% else %}
      {% if seen[info.target] %}<div class="note bad">🐺 {{ info.target }} là Sói!</div>
      {% else %}<div class="note done">{{ info.target }} không phải Sói.</div>{% endif %}
    {% endif %}
    {% if seen %}
      <h2 style="margin-top:16px">Đã soi</h2>
      <ul class="roster">
        {% for p, w in seen.items() %}<li><span>{{ p }}</span>{% if w %}<span class="tag dead">Sói</span>{% else %}<span class="tag ok">Không phải Sói</span>{% endif %}</li>{% endfor %}
      </ul>
    {% endif %}
    </div>
  {% elif info and info.role == 'Guard' %}
    <div class="panel secret" style="margin-top:14px">
    {% if not info.acted %}
      <h2>Bảo vệ ai đêm nay?</h2>
      <form method="POST" action="/action">
        <select name="target">
          {% for p, i in players.items() %}{% if i.role and i.alive and p != info.last %}<option value="{{ p }}">{{ p }}{% if p == me %} (chính mình){% endif %}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-guard">Bảo vệ người này</button>
      </form>
      {% if info.last %}<p class="sub" style="margin:10px 0 0">Đêm qua bạn đã bảo vệ {{ info.last }} nên đêm nay không chọn lại được.</p>{% endif %}
    {% else %}
      <div class="note done">Đêm nay bạn bảo vệ: {{ info.target }}.</div>
    {% endif %}
    </div>
  {% endif %}

{% elif phase == 'DAY' %}
  <h1>☀️ Trời sáng</h1>
  <p class="sub">{{ night_result }}</p>
  {% if vote_result %}<div class="note" style="margin-bottom:14px">{{ vote_result }}</div>
  {% else %}<p class="sub">Thảo luận xem ai là Sói. Quản trò sẽ mở biểu quyết.</p>{% endif %}
  {% if info and not info.alive %}<div class="note bad" style="margin-bottom:14px">Bạn đã bị loại.</div>{% endif %}
  <div class="panel">
    <h2>Dân làng <span class="count">· còn {{ alive }} / {{ total }}</span></h2>
    <ul class="roster">
      {% for p, i in players.items() %}{% if i.role %}
      <li class="{{ 'out' if not i.alive }} {{ 'me' if p == me }}"><span>{{ p }}</span>
        {% if i.alive %}<span class="tag ok">Còn sống</span>{% else %}<span class="tag dead">Đã chết</span>{% endif %}</li>
      {% endif %}{% endfor %}
    </ul>
  </div>

{% elif phase == 'VOTE' %}
  <h1>⚖️ Biểu quyết</h1>
  <p class="sub">Chọn người bạn nghĩ là Sói. Ai nhiều phiếu nhất sẽ bị treo cổ; hoà phiếu thì không ai chết.</p>
  {% if not info or not info.role %}
    <div class="note">Bạn đang xem ván này, không bỏ phiếu.</div>
  {% elif not info.alive %}
    <div class="note bad">Bạn đã bị loại nên không bỏ phiếu.</div>
  {% else %}
  <div class="panel">
    <form method="POST" action="/vote">
      <select name="target">
        {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}" {{ 'selected' if info.vote == p }}>{{ p }}</option>{% endif %}{% endfor %}
      </select>
      <button type="submit" class="btn-wolf">{{ 'Đổi phiếu' if info.vote else 'Bỏ phiếu' }}</button>
    </form>
    {% if info.vote %}<div class="note done" style="margin-top:12px">Bạn đã bầu: {{ info.vote }}. Có thể đổi đến khi quản trò chốt.</div>{% endif %}
  </div>
  {% endif %}
  <p class="sub" style="margin-top:14px">Đã bỏ phiếu: {{ voted }} / {{ alive }} người.</p>

{% elif phase == 'END' %}
  {% if winner == 'Wolf' %}<h1>🐺 Sói thắng</h1>
  {% elif winner == 'Village' %}<h1>🏡 Dân làng thắng</h1>
  {% else %}<h1>Hết ván</h1>{% endif %}
  <p class="sub">{{ vote_result or night_result }} Lật bài toàn bộ, cảm ơn mọi người đã chơi!</p>
  <div class="panel">
    <ul class="roster">
      {% for p, i in players.items() %}{% if i.role %}
      <li class="{{ 'out' if not i.alive }}"><span>{{ p }}</span><span class="r-{{ roles[i.role].cls }}">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span></li>
      {% endif %}{% endfor %}
    </ul>
  </div>
{% endif %}
</div>
"""


@app.route('/')
def index():
    username = session.get('username')
    # phòng đã reset → tên cũ không còn trong danh sách → về màn hình nhập tên
    if username and username not in game_data["players"]:
        session.pop('username', None)
        username = None
    info = game_data["players"].get(username) if username else None
    phase = game_data["phase"]
    body_cls = "day" if phase in ("DAY", "VOTE", "END") else ""
    players = game_data["players"]
    body = render_template_string(
        PLAYER_BODY, phase=phase, me=username, info=info, players=players,
        role=ROLES.get(info["role"]) if info and info["role"] else None,
        roles=ROLES, night_result=game_data["night_result"], body_cls=body_cls,
        vote_result=game_data["vote_result"], winner=game_data["winner"],
        seen=(info or {}).get("seen") or {},
        mates=[n for n, i in players.items() if i["role"] == "Wolf" and n != username]
              if info and info["role"] == "Wolf" else [],
        voted=sum(1 for i in players.values() if i["role"] and i["alive"] and i.get("vote")),
        alive=sum(1 for i in players.values() if i["role"] and i["alive"]),
        total=sum(1 for i in players.values() if i["role"]))
    return page("HILtrip Ma Sói", body, body_cls, "/?partial=1")


@app.route('/state')
def state():
    return jsonify(v=game_data["version"])


@app.route('/join', methods=['POST'])
def join():
    username = (request.form.get('username') or '').strip()[:30]
    if username:
        session['username'] = username
        with _lock:
            if username not in game_data["players"]:
                # vào giữa ván: chưa có vai, xem đến ván sau
                role = "Villager" if game_data["phase"] == "LOBBY" else None
                game_data["players"][username] = {"role": role, "alive": True, "target": None, "acted": False, "vote": None}
                bump()
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))


@app.route('/action', methods=['POST'])
def action():
    username = session.get('username')
    target = request.form.get('target')
    with _lock:
        players = game_data["players"]
        me = players.get(username)
        t = players.get(target)
        if not (me and t and game_data["phase"] == "NIGHT" and me["alive"] and not me["acted"]
                and t["role"] and t["alive"]):
            return redirect(url_for('index'))
        ok = False
        if me["role"] == "Wolf":
            ok = t["role"] != "Wolf"
        elif me["role"] == "Seer":
            ok = target != username
            if ok:
                me.setdefault("seen", {})[target] = (t["role"] == "Wolf")
        elif me["role"] == "Guard":
            ok = target != me.get("last")
        if ok:
            me["target"] = target
            me["acted"] = True
            bump()
    return redirect(url_for('index'))


@app.route('/vote', methods=['POST'])
def vote():
    username = session.get('username')
    target = request.form.get('target')
    with _lock:
        me = game_data["players"].get(username)
        t = game_data["players"].get(target)
        if (me and t and game_data["phase"] == "VOTE" and me["role"] and me["alive"]
                and t["role"] and t["alive"] and target != username):
            me["vote"] = target
            bump()
    return redirect(url_for('index'))


# ---------------------------------------------------------------- màn hình quản trò
PHASE_VI = {"LOBBY": "Phòng chờ", "NIGHT": "🌙 Đêm", "DAY": "☀️ Ngày", "VOTE": "⚖️ Biểu quyết", "END": "Hết ván"}

ADMIN_BODY = """
<div data-phase="{{ phase }}" data-body="{{ body_cls }}">
<div class="brand"><span>HILtrip Quản trò</span><a href="/">Màn hình người chơi</a></div>
<h1>{{ phase_vi[phase] }}</h1>
<p class="sub">{{ total }} người trong phòng{% if phase != 'LOBBY' %} · còn sống {{ alive }}{% endif %}</p>

{% if phase == 'NIGHT' %}
<div class="panel">
  <h2>Hành động đêm nay</h2>
  <ul class="roster">
    {% for r in night_status %}<li><span>{{ r.label }}</span>
      {% if r.state == 'done' %}<span class="tag ok">Xong</span>
      {% elif r.state == 'dead' %}<span class="count">đã chết</span>
      {% else %}<span class="count">đang chọn…</span>{% endif %}</li>{% endfor %}
  </ul>
  {% if night_ready %}<div class="note done" style="margin-top:12px">Mọi người đã chọn xong. Có thể gọi trời sáng.</div>{% endif %}
</div>
{% endif %}

{% if phase == 'END' and winner %}
<div class="panel"><div class="note done">{{ '🐺 Phe Sói thắng' if winner == 'Wolf' else '🏡 Phe Dân làng thắng' }}</div></div>
{% elif phase == 'DAY' and vote_result %}
<div class="panel"><div class="note">{{ vote_result }}</div></div>
{% endif %}

{% if phase == 'VOTE' %}
<div class="panel">
  <h2>Phiếu bầu <span class="count">· {{ voted }}/{{ alive }} đã bỏ</span></h2>
  <ul class="roster">
    {% for name, n in tally %}<li><span>{{ name }}</span><span class="tag dead">{{ n }} phiếu</span></li>
    {% else %}<li><span class="count">Chưa có phiếu nào.</span></li>{% endfor %}
  </ul>
</div>
{% endif %}

<div class="panel">
  <h2>Người chơi</h2>
  <ul class="roster">
    {% for p, i in players.items() %}
    <li class="{{ 'out' if not i.alive }}"><span>{{ p }}</span>
      {% if not i.role %}<span class="count">vào sau, xem</span>
      {% elif phase == 'LOBBY' %}<span class="count">đã vào</span>
      {% elif phase == 'END' %}<span class="r-{{ roles[i.role].cls }}">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span>
      {% elif i.alive %}<span class="tag ok">Còn sống</span>{% else %}<span class="tag dead">Đã chết</span>{% endif %}
    </li>
    {% else %}<li><span class="count">Chưa có ai. Gửi link trang chủ cho mọi người.</span></li>{% endfor %}
  </ul>
</div>

<div class="panel stack">
  {% if phase in ('LOBBY', 'END') %}
  <form action="/admin/assign" method="POST"><button type="submit" {{ 'disabled' if total < 3 }}>Chia vai và bắt đầu đêm</button></form>
  {% if total < 3 %}<p class="count" style="margin:0">Cần ít nhất 3 người.</p>{% endif %}
  {% elif phase == 'NIGHT' %}
  <form action="/admin/next_phase/DAY" method="POST"><button type="submit">Gọi trời sáng</button></form>
  {% elif phase == 'DAY' and not voted_today %}
  <form action="/admin/open_vote" method="POST"><button type="submit">Mở biểu quyết treo cổ</button></form>
  <form action="/admin/next_night" method="POST"><button type="submit" class="btn-ghost">Bỏ qua, sang đêm tiếp theo</button></form>
  {% elif phase == 'DAY' %}
  <form action="/admin/next_night" method="POST"><button type="submit">Sang đêm tiếp theo</button></form>
  {% elif phase == 'VOTE' %}
  <form action="/admin/close_vote" method="POST" data-confirm="Chốt phiếu và treo cổ người nhiều phiếu nhất?"><button type="submit" class="btn-wolf">Chốt phiếu</button></form>
  {% endif %}
  {% if phase in ('DAY', 'VOTE') %}
  <form action="/admin/next_phase/END" method="POST" data-confirm="Kết thúc ván và lật bài cho mọi người?"><button type="submit" class="btn-ghost">Kết thúc ván, lật bài</button></form>
  {% endif %}
  <form action="/admin/reset" method="POST" data-confirm="Xoá toàn bộ người chơi và làm lại từ đầu?"><button type="submit" class="btn-ghost">Xoá phòng, làm lại từ đầu</button></form>
</div>
</div>
"""


@app.route('/admin')
def admin_panel():
    players = game_data["players"]
    phase = game_data["phase"]
    body_cls = "day" if phase in ("DAY", "VOTE", "END") else ""
    wolves = [i for i in players.values() if i["role"] == "Wolf" and i["alive"]]
    voters = [i for i in players.values() if i["role"] and i["alive"]]
    body = render_template_string(
        ADMIN_BODY, phase=phase, phase_vi=PHASE_VI, players=players, body_cls=body_cls,
        roles=ROLES, winner=game_data["winner"], vote_result=game_data["vote_result"],
        voted_today=game_data["voted_today"], tally=tally_votes(),
        voted=sum(1 for i in voters if i.get("vote")),
        night_status=night_status(), night_ready=all(r["state"] != "wait" for r in night_status()),
        total=len(players), alive=sum(1 for i in players.values() if i["role"] and i["alive"]),
        wolves=len(wolves), wolves_done=sum(1 for i in wolves if i["acted"]))
    return page("HILtrip Quản trò", body, body_cls, "/admin?partial=1")


@app.route('/admin/assign', methods=['POST'])
def admin_assign():
    with _lock:
        usernames = list(game_data["players"].keys())
        total = len(usernames)
        if total < 3:
            return redirect(url_for('admin_panel'))
        if total <= 3:
            roles = ["Wolf"] + ["Villager"] * (total - 1)
        elif total <= 6:
            roles = ["Wolf", "Seer"] + ["Villager"] * (total - 2)
        elif total <= 8:
            roles = ["Wolf", "Seer", "Guard"] + ["Villager"] * (total - 3)
        else:   # từ 9 người: 2 Sói
            roles = ["Wolf", "Wolf", "Seer", "Guard"] + ["Villager"] * (total - 4)
        random.shuffle(roles)
        for i, uname in enumerate(usernames):
            game_data["players"][uname].update(role=roles[i], alive=True, target=None, acted=False, vote=None, seen={}, last=None)
        game_data.update(phase="NIGHT", night_result="", vote_result="", voted_today=False, winner=None)
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/next_night', methods=['POST'])
def admin_next_night():
    with _lock:
        if game_data["phase"] != "DAY":
            return redirect(url_for('admin_panel'))
        for info in game_data["players"].values():
            info.update(target=None, acted=False, vote=None)
        game_data.update(phase="NIGHT", vote_result="", voted_today=False)
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/next_phase/<phase>', methods=['POST'])
def admin_next_phase(phase):
    if phase not in ("DAY", "END"):
        abort(400)
    with _lock:
        if phase == "DAY" and game_data["phase"] == "NIGHT":
            # nhiều Sói: lấy mục tiêu được chọn nhiều nhất, hoà thì lấy của con Sói chọn trước
            picks = [i["target"] for i in game_data["players"].values()
                     if i["role"] == "Wolf" and i["alive"] and i["target"] in game_data["players"]]
            wolf_target = max(picks, key=picks.count) if picks else None
            guarded = None
            for i in game_data["players"].values():
                if i["role"] == "Guard" and i["alive"] and i["acted"]:
                    guarded = i["target"]
                    i["last"] = i["target"]          # đêm sau không được bảo vệ lại người này
                elif i["role"] == "Guard":
                    i["last"] = None
            if wolf_target and wolf_target == guarded:
                wolf_target = None               # được Bảo Vệ che chở → không ai chết, không tiết lộ
            if wolf_target:
                game_data["players"][wolf_target]["alive"] = False
                game_data["night_result"] = f"Đêm qua Sói đã ghé thăm. {wolf_target} không qua khỏi."
            else:
                game_data["night_result"] = "Đêm qua yên bình, không ai bị cắn."
            game_data["vote_result"] = ""
            if not check_win():
                game_data["phase"] = "DAY"
        elif phase == "END":
            game_data["phase"] = "END"
        bump()
    return redirect(url_for('admin_panel'))


def night_status():
    rows = []
    ps = [i for i in game_data["players"].values() if i["role"]]
    wolves = [i for i in ps if i["role"] == "Wolf" and i["alive"]]
    rows.append({"label": f"🐺 Ma Sói ({sum(1 for i in wolves if i['acted'])}/{len(wolves)})",
                 "state": "done" if all(i["acted"] for i in wolves) else "wait"})
    for role, label in (("Seer", "🔮 Tiên Tri"), ("Guard", "🛡️ Bảo Vệ")):
        found = [i for i in ps if i["role"] == role]
        if not found:
            continue
        i = found[0]
        rows.append({"label": label, "state": "dead" if not i["alive"] else ("done" if i["acted"] else "wait")})
    return rows


def tally_votes():
    alive = {n for n, i in game_data["players"].items() if i["role"] and i["alive"]}
    counts = {}
    for n in alive:
        v = game_data["players"][n].get("vote")
        if v in alive:
            counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


@app.route('/admin/open_vote', methods=['POST'])
def admin_open_vote():
    with _lock:
        if game_data["phase"] == "DAY" and not game_data["voted_today"]:
            for info in game_data["players"].values():
                info["vote"] = None
            game_data["phase"] = "VOTE"
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/close_vote', methods=['POST'])
def admin_close_vote():
    with _lock:
        if game_data["phase"] != "VOTE":
            return redirect(url_for('admin_panel'))
        tally = tally_votes()
        if not tally:
            game_data["vote_result"] = "Không ai bỏ phiếu, hôm nay không ai bị treo cổ."
        elif len(tally) > 1 and tally[0][1] == tally[1][1]:
            game_data["vote_result"] = f"Hoà phiếu ({tally[0][1]} phiếu), hôm nay không ai bị treo cổ."
        else:
            name, n = tally[0]
            game_data["players"][name]["alive"] = False
            game_data["vote_result"] = f"Làng đã treo cổ {name} với {n} phiếu."
        game_data["voted_today"] = True
        if not check_win():
            game_data["phase"] = "DAY"
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/reset', methods=['POST'])
def admin_reset():
    with _lock:
        game_data.update(phase="LOBBY", players={}, night_result="", vote_result="",
                         voted_today=False, winner=None)
        bump()
    return redirect(url_for('admin_panel'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
