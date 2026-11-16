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
    "hunter": None,           # tên Thợ Săn đang chờ bắn
    "hunter_ctx": "night",    # Thợ Săn chết vì đêm ("night") hay vì treo cổ ("vote")
    "game_id": 0,             # tăng mỗi ván mới → lá bài úp lại
    "spy": None,              # trạng thái ván Truy Tìm Gián Điệp
    "version": 0,             # tăng mỗi khi trạng thái đổi → trình duyệt tự cập nhật
}

ROLES = {
    "Wolf":     {"name": "Ma Sói",   "icon": "🐺", "cls": "wolf",  "hint": "Đêm nay chọn một người để cắn."},
    "Seer":     {"name": "Tiên Tri", "icon": "🔮", "cls": "seer",  "hint": "Mỗi đêm soi một người, biết người đó có phải Sói không."},
    "Guard":    {"name": "Bảo Vệ",   "icon": "🛡️", "cls": "guard", "hint": "Mỗi đêm che chở một người khỏi Sói. Không bảo vệ cùng một người hai đêm liền."},
    "Witch":    {"name": "Phù Thuỷ", "icon": "🧪", "cls": "witch", "hint": "Có 1 bình cứu và 1 bình độc, mỗi bình dùng một lần trong cả ván."},
    "Hunter":   {"name": "Thợ Săn",  "icon": "🏹", "cls": "hunter", "hint": "Khi chết, bạn được bắn chết ngay một người."},
    "Idiot":    {"name": "Thằng Ngốc", "icon": "🤡", "cls": "idiot", "hint": "Bị treo cổ thì lật bài và sống tiếp, nhưng mất quyền bỏ phiếu."},
    "Villager": {"name": "Dân Làng", "icon": "🧑‍🌾", "cls": "villa", "hint": "Nhắm mắt ngủ và chờ trời sáng."},
}


SPY_START_FORM = """
<form action="/admin/spy/start" method="POST" class="stack">
  <details class="note"><summary>Tự nhập cặp từ (không bắt buộc)</summary>
    <div style="margin-top:12px">
      <input type="text" name="civ" maxlength="40" placeholder="Từ của Dân, ví dụ: Cà phê" autocomplete="off">
      <input type="text" name="spy" maxlength="40" placeholder="Từ của Gián điệp, ví dụ: Trà sữa" autocomplete="off">
      <p class="count" style="margin:0">Để trống thì app tự bốc ngẫu nhiên trong {{ npairs }} cặp có sẵn.</p>
    </div>
  </details>
  <button type="submit" class="btn-seer" {{ 'disabled' if total < 3 }}>Bắt đầu Truy Tìm Gián Điệp</button>
</form>
"""


def bump():
    game_data["version"] += 1


def can_vote(i):
    return i["role"] and i["alive"] and not (i["role"] == "Idiot" and i.get("revealed"))


def wolf_target():
    """Mục tiêu của bầy Sói: được chọn nhiều nhất, hoà thì lấy của con chọn trước."""
    picks = [i["target"] for i in game_data["players"].values()
             if i["role"] == "Wolf" and i["alive"] and i["target"] in game_data["players"]]
    return max(picks, key=picks.count) if picks else None


def wolves_done():
    return all(i["acted"] for i in game_data["players"].values() if i["role"] == "Wolf" and i["alive"])


def role_label(name):
    r = ROLES[game_data["players"][name]["role"]]
    return f"{r['icon']} {r['name']}"


def start_hunter_or_finish(dead, ctx):
    """dead: danh sách người vừa chết. Nếu có Thợ Săn → chờ bắn; không thì kiểm tra thắng thua."""
    players = game_data["players"]
    hunter = next((d for d in dead if players[d]["role"] == "Hunter"), None)
    if hunter and any(i["role"] and i["alive"] for i in players.values()):
        game_data.update(phase="HUNTER", hunter=hunter, hunter_ctx=ctx)
        return
    if not check_win():
        game_data["phase"] = "DAY"


def add_result(ctx, text):
    key = "vote_result" if ctx == "vote" else "night_result"
    game_data[key] = (game_data[key] + " " + text).strip()


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
  --witch:#7fcf6b; --hunter:#ee9a4d; --idiot:#ef7fb5;
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
  --ok:#2f7d4a; --dead:#b8352a; --wolf:#b8352a; --seer:#6b4fb3; --guard:#2a7b78; --villa:#8a6512;
  --witch:#3d7d2c; --hunter:#a8561a; --idiot:#b23d78; }
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
.card-role.witch.open{border-color:var(--witch)}.witch .rname{color:var(--witch)}
.card-role.hunter.open{border-color:var(--hunter)}.hunter .rname{color:var(--hunter)}
.card-role.idiot.open{border-color:var(--idiot)}.idiot .rname{color:var(--idiot)}
.r-witch{color:var(--witch)} .r-hunter{color:var(--hunter)} .r-idiot{color:var(--idiot)}
.btn-witch{background:var(--witch);color:#fff}
details summary{cursor:pointer;font-weight:500}
.order{counter-reset:o}
.order li span:first-child::before{counter-increment:o;content:counter(o) ".";display:inline-block;min-width:28px;color:var(--mist)}
.check{display:flex;gap:10px;align-items:center;margin-bottom:12px;font-weight:500}
.check input{width:22px;height:22px;accent-color:var(--witch)}
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
    var cb = app.querySelector('input[type=checkbox]'), keepCb = cb ? cb.checked : null;
    fetch(partial, {cache:'no-store'}).then(function(r){
      if (r.redirected || !r.ok) { location.reload(); return null; }
      return r.text();
    }).then(function(html){
      if (html === null) return;
      var phaseBefore = (app.querySelector('[data-phase]')||{}).dataset;
      var gameBefore = phaseBefore && phaseBefore.game;
      app.innerHTML = html;
      var phaseAfter = (app.querySelector('[data-phase]')||{}).dataset;
      if (phaseBefore && phaseAfter && phaseBefore.phase !== phaseAfter.phase){
        app.classList.remove('flash'); void app.offsetWidth; app.classList.add('flash');
        if (phaseAfter.phase === 'NIGHT') opened = false;
      }
      if (phaseAfter && gameBefore !== undefined && phaseAfter.game !== gameBefore) opened = false;
      var s2 = app.querySelector('select');
      if (s2 && keep !== null) { s2.value = keep; if (s2.value !== keep) s2.selectedIndex = 0; }
      var cb2 = app.querySelector('input[type=checkbox]'); if (cb2 && keepCb !== null) cb2.checked = keepCb;
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
<div data-phase="{{ phase }}" data-game="{{ game_id }}" data-body="{{ body_cls }}">
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
  <p class="sub">Đang chờ quản trò chọn trò chơi. Màn hình sẽ tự chuyển.</p>
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
  {% elif info and info.role == 'Witch' %}
    <div class="panel secret" style="margin-top:14px">
      <p class="sub" style="margin-bottom:12px">Bình cứu: <b>{{ 'còn' if info.heal else 'đã dùng' }}</b> · Bình độc: <b>{{ 'còn' if info.poison else 'đã dùng' }}</b></p>
    {% if info.acted %}
      <div class="note done">Bạn đã xong lượt đêm nay{% if info.save %}, đã cứu {{ victim }}{% endif %}{% if info.kill %}, đã đầu độc {{ info.kill }}{% endif %}.</div>
    {% elif not wolves_done %}
      <div class="note">Chờ Sói chọn xong. Màn hình sẽ tự cập nhật.</div>
    {% else %}
      <h2>{% if victim %}Đêm nay Sói cắn: <b class="r-wolf">{{ victim }}</b>{% else %}Đêm nay Sói không cắn ai{% endif %}</h2>
      <form method="POST" action="/witch">
        {% if info.heal and victim %}<label class="check"><input type="checkbox" name="save" value="1"> Dùng bình cứu cho {{ victim }}</label>{% endif %}
        {% if info.poison %}
        <select name="kill">
          <option value="">Không dùng bình độc</option>
          {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}">Đầu độc {{ p }}</option>{% endif %}{% endfor %}
        </select>
        {% endif %}
        <button type="submit" class="btn-witch">Xác nhận</button>
      </form>
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
        <span>{% if i.revealed %}<span class="r-{{ roles[i.role].cls }}" style="margin-right:8px">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span>{% endif %}
        {% if i.alive %}<span class="tag ok">Còn sống</span>{% else %}<span class="tag dead">Đã chết</span>{% endif %}</span></li>
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
  {% elif info.role == 'Idiot' and info.revealed %}
    <div class="note">Bạn là Thằng Ngốc đã lộ bài nên không được bỏ phiếu nữa.</div>
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
  <p class="sub" style="margin-top:14px">Đã bỏ phiếu: {{ voted }} / {{ voters }} người.</p>

{% elif phase == 'HUNTER' %}
  <h1>🏹 Thợ Săn ra tay</h1>
  <p class="sub">{{ vote_result if hunter_ctx == 'vote' else night_result }}</p>
  {% if me == hunter %}
  <div class="panel">
    <h2>Bạn là Thợ Săn. Bắn ai trước khi chết?</h2>
    <form method="POST" action="/shoot">
      <select name="target">
        {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}">{{ p }}</option>{% endif %}{% endfor %}
        <option value="">Không bắn ai</option>
      </select>
      <button type="submit" class="btn-wolf">Bắn</button>
    </form>
  </div>
  {% else %}
  <div class="note">{{ hunter }} là Thợ Săn và đang chọn người để bắn…</div>
  {% endif %}

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
    if username and game_data["phase"] == "SPY":
        return spy_index(username)
    info = game_data["players"].get(username) if username else None
    phase = game_data["phase"]
    body_cls = "day" if phase in ("DAY", "VOTE", "HUNTER", "END") else ""
    players = game_data["players"]
    body = render_template_string(
        PLAYER_BODY, phase=phase, me=username, info=info, players=players,
        game_id=game_data["game_id"],
        role=ROLES.get(info["role"]) if info and info["role"] else None,
        roles=ROLES, night_result=game_data["night_result"], body_cls=body_cls,
        vote_result=game_data["vote_result"], winner=game_data["winner"],
        seen=(info or {}).get("seen") or {},
        mates=[n for n, i in players.items() if i["role"] == "Wolf" and n != username]
              if info and info["role"] == "Wolf" else [],
        voted=sum(1 for i in players.values() if can_vote(i) and i.get("vote")),
        voters=sum(1 for i in players.values() if can_vote(i)),
        hunter=game_data["hunter"], hunter_ctx=game_data["hunter_ctx"],
        wolves_done=wolves_done(),
        victim=wolf_target() if info and info["role"] == "Witch" else None,
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
        if (me and t and game_data["phase"] == "VOTE" and can_vote(me)
                and t["role"] and t["alive"] and target != username):
            me["vote"] = target
            bump()
    return redirect(url_for('index'))


@app.route('/witch', methods=['POST'])
def witch():
    username = session.get('username')
    with _lock:
        players = game_data["players"]
        me = players.get(username)
        if not (me and me["role"] == "Witch" and me["alive"] and not me["acted"]
                and game_data["phase"] == "NIGHT" and wolves_done()):
            return redirect(url_for('index'))
        victim = wolf_target()
        me["save"] = bool(request.form.get("save")) and bool(victim) and me.get("heal", False)
        kill = request.form.get("kill") or None
        t = players.get(kill) if kill else None
        me["kill"] = kill if (t and t["role"] and t["alive"] and kill != username and me.get("poison")) else None
        me["acted"] = True
        bump()
    return redirect(url_for('index'))


@app.route('/shoot', methods=['POST'])
def shoot():
    username = session.get('username')
    with _lock:
        if game_data["phase"] == "HUNTER" and game_data["hunter"] == username:
            finish_hunter(request.form.get("target") or None)
    return redirect(url_for('index'))


def finish_hunter(target):
    players = game_data["players"]
    h, ctx = game_data["hunter"], game_data["hunter_ctx"]
    t = players.get(target) if target else None
    if t and t["role"] and t["alive"] and target != h:
        t["alive"] = False
        add_result(ctx, f"🏹 Thợ Săn {h} đã bắn chết {target}.")
    else:
        add_result(ctx, f"🏹 Thợ Săn {h} không bắn ai.")
    game_data["hunter"] = None
    if not check_win():
        game_data["phase"] = "DAY"
    bump()


# ---------------------------------------------------------------- màn hình quản trò
PHASE_VI = {"LOBBY": "Phòng chờ", "NIGHT": "🌙 Đêm", "DAY": "☀️ Ngày", "VOTE": "⚖️ Biểu quyết",
            "HUNTER": "🏹 Thợ Săn", "END": "Hết ván"}

ADMIN_BODY = """
<div data-phase="{{ phase }}" data-game="{{ game_id }}" data-body="{{ body_cls }}">
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
      {% elif r.state == 'later' %}<span class="count">chờ Sói…</span>
      {% else %}<span class="count">đang chọn…</span>{% endif %}</li>{% endfor %}
  </ul>
  {% if night_ready %}<div class="note done" style="margin-top:12px">Mọi người đã chọn xong. Có thể gọi trời sáng.</div>{% endif %}
</div>
{% endif %}

{% if phase == 'END' and winner %}
<div class="panel"><div class="note done">{{ '🐺 Phe Sói thắng' if winner == 'Wolf' else '🏡 Phe Dân làng thắng' }}</div></div>
{% elif phase == 'DAY' and (night_result or vote_result) %}
<div class="panel stack">{% if night_result %}<div class="note">{{ night_result }}</div>{% endif %}
  {% if vote_result %}<div class="note">{{ vote_result }}</div>{% endif %}</div>
{% elif phase == 'HUNTER' %}
<div class="panel"><div class="note">Chờ {{ hunter }} (Thợ Săn) chọn người để bắn trên điện thoại.</div></div>
{% endif %}

{% if phase == 'VOTE' %}
<div class="panel">
  <h2>Phiếu bầu <span class="count">· {{ voted }}/{{ voters }} đã bỏ</span></h2>
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
      {% else %}<span>{% if i.revealed %}<span class="r-{{ roles[i.role].cls }}" style="margin-right:8px">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span>{% endif %}
        {% if i.alive %}<span class="tag ok">Còn sống</span>{% else %}<span class="tag dead">Đã chết</span>{% endif %}</span>{% endif %}
    </li>
    {% else %}<li><span class="count">Chưa có ai. Gửi link trang chủ cho mọi người.</span></li>{% endfor %}
  </ul>
</div>

<div class="panel stack">
  {% if phase in ('LOBBY', 'END') %}
  <form action="/admin/assign" method="POST"><button type="submit" {{ 'disabled' if total < 3 }}>🐺 Chơi Ma Sói: chia vai, bắt đầu đêm</button></form>
  """ + SPY_START_FORM.replace("Bắt đầu Truy Tìm Gián Điệp", "🕵️ Chơi Truy Tìm Gián Điệp") + """
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
  {% elif phase == 'HUNTER' %}
  <form action="/admin/skip_hunter" method="POST" data-confirm="Bỏ lượt bắn của Thợ Săn?"><button type="submit" class="btn-ghost">Thợ Săn không bắn, đi tiếp</button></form>
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
    if game_data["phase"] == "SPY":
        return spy_admin()
    players = game_data["players"]
    phase = game_data["phase"]
    body_cls = "day" if phase in ("DAY", "VOTE", "HUNTER", "END") else ""
    wolves = [i for i in players.values() if i["role"] == "Wolf" and i["alive"]]
    voters = [i for i in players.values() if can_vote(i)]
    body = render_template_string(
        ADMIN_BODY, phase=phase, phase_vi=PHASE_VI, players=players, body_cls=body_cls,
        game_id=game_data["game_id"],
        roles=ROLES, winner=game_data["winner"], vote_result=game_data["vote_result"],
        voted_today=game_data["voted_today"], tally=tally_votes(),
        voted=sum(1 for i in voters if i.get("vote")), voters=len(voters),
        night_result=game_data["night_result"], hunter=game_data["hunter"],
        night_status=night_status(), night_ready=all(r["state"] in ("done", "dead") for r in night_status()),
        total=len(players), npairs=len(WORD_PAIRS),
        alive=sum(1 for i in players.values() if i["role"] and i["alive"]),
        wolves=len(wolves), wolves_done=sum(1 for i in wolves if i["acted"]))
    return page("HILtrip Quản trò", body, body_cls, "/admin?partial=1")


@app.route('/admin/assign', methods=['POST'])
def admin_assign():
    with _lock:
        usernames = list(game_data["players"].keys())
        total = len(usernames)
        if total < 3:
            return redirect(url_for('admin_panel'))
        if total == 3:
            special = ["Wolf"]
        elif total == 4:
            special = ["Wolf", "Seer"]
        elif total <= 6:
            special = ["Wolf", "Seer", "Hunter"]
        elif total <= 8:
            special = ["Wolf", "Seer", "Guard", "Witch"]
        elif total <= 10:   # từ 9 người: 2 Sói
            special = ["Wolf", "Wolf", "Seer", "Guard", "Witch", "Hunter"]
        else:
            special = ["Wolf", "Wolf", "Seer", "Guard", "Witch", "Hunter", "Idiot"]
        roles = special + ["Villager"] * (total - len(special))
        random.shuffle(roles)
        for i, uname in enumerate(usernames):
            game_data["players"][uname].update(role=roles[i], alive=True, target=None, acted=False, vote=None, seen={}, last=None,
                                                 revealed=False, heal=True, poison=True, save=False, kill=None)
        game_data.update(phase="NIGHT", night_result="", vote_result="", voted_today=False, winner=None, hunter=None)
        game_data["game_id"] += 1
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/next_night', methods=['POST'])
def admin_next_night():
    with _lock:
        if game_data["phase"] != "DAY":
            return redirect(url_for('admin_panel'))
        for info in game_data["players"].values():
            info.update(target=None, acted=False, vote=None, save=False, kill=None)
        game_data.update(phase="NIGHT", vote_result="", voted_today=False)
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/next_phase/<phase>', methods=['POST'])
def admin_next_phase(phase):
    if phase not in ("DAY", "END"):
        abort(400)
    with _lock:
        if phase == "DAY" and game_data["phase"] == "NIGHT":
            players = game_data["players"]
            victim = wolf_target()
            guarded = None
            for i in game_data["players"].values():
                if i["role"] == "Guard" and i["alive"] and i["acted"]:
                    guarded = i["target"]
                    i["last"] = i["target"]          # đêm sau không được bảo vệ lại người này
                elif i["role"] == "Guard":
                    i["last"] = None
            witch = next((i for i in players.values()
                          if i["role"] == "Witch" and i["alive"] and i["acted"]), None)
            dead = []
            saved = bool(witch and witch.get("save") and victim)
            if saved:
                witch["heal"] = False
            # được Bảo Vệ che chở hoặc Phù Thuỷ cứu → không chết, không tiết lộ ai đã cứu
            if victim and victim != guarded and not saved:
                dead.append(victim)
            if witch and witch.get("kill"):
                witch["poison"] = False
                k = witch["kill"]
                if players.get(k) and players[k]["alive"] and k not in dead:
                    dead.append(k)
            for d in dead:
                players[d]["alive"] = False
            if dead:
                game_data["night_result"] = f"Đêm qua {' và '.join(sorted(dead, key=str.lower))} đã chết."
            else:
                game_data["night_result"] = "Đêm qua yên bình, không ai chết."
            game_data["vote_result"] = ""
            start_hunter_or_finish(dead, "night")
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
    for role, label in (("Seer", "🔮 Tiên Tri"), ("Guard", "🛡️ Bảo Vệ"), ("Witch", "🧪 Phù Thuỷ")):
        found = [i for i in ps if i["role"] == role]
        if not found:
            continue
        i = found[0]
        if not i["alive"]:
            state = "dead"
        elif i["acted"]:
            state = "done"
        elif role == "Witch" and not wolves_done():
            state = "later"
        else:
            state = "wait"
        rows.append({"label": label, "state": state})
    return rows


def tally_votes():
    alive = {n for n, i in game_data["players"].items() if i["role"] and i["alive"]}
    counts = {}
    for n in alive:
        if not can_vote(game_data["players"][n]):
            continue
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
        dead = []
        if not tally:
            game_data["vote_result"] = "Không ai bỏ phiếu, hôm nay không ai bị treo cổ."
        elif len(tally) > 1 and tally[0][1] == tally[1][1]:
            game_data["vote_result"] = f"Hoà phiếu ({tally[0][1]} phiếu), hôm nay không ai bị treo cổ."
        else:
            name, n = tally[0]
            p = game_data["players"][name]
            if p["role"] == "Idiot" and not p.get("revealed"):
                p["revealed"] = True
                game_data["vote_result"] = (f"Làng treo cổ {name} với {n} phiếu, nhưng {name} là 🤡 Thằng Ngốc! "
                                            f"{name} được tha, nhưng từ giờ không được bỏ phiếu.")
            else:
                p["alive"] = False
                p["revealed"] = True
                game_data["vote_result"] = f"Làng đã treo cổ {name} với {n} phiếu. {name} là {role_label(name)}."
                dead.append(name)
        game_data["voted_today"] = True
        start_hunter_or_finish(dead, "vote")
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/skip_hunter', methods=['POST'])
def admin_skip_hunter():
    with _lock:
        if game_data["phase"] == "HUNTER":
            finish_hunter(None)
    return redirect(url_for('admin_panel'))


@app.route('/admin/reset', methods=['POST'])
def admin_reset():
    with _lock:
        game_data.update(phase="LOBBY", players={}, night_result="", vote_result="",
                         voted_today=False, winner=None, hunter=None, spy=None)
        bump()
    return redirect(url_for('admin_panel'))


# ================================================================ TRUY TÌM GIÁN ĐIỆP
import unicodedata

WORD_PAIRS = [
    ("Cà phê", "Trà sữa"), ("Bánh mì", "Bánh bao"), ("Phở", "Bún bò"), ("Biển", "Hồ bơi"),
    ("Núi", "Đồi"), ("Mèo", "Chó"), ("Xe máy", "Xe đạp"), ("Điện thoại", "Máy tính bảng"),
    ("Facebook", "Instagram"), ("Zalo", "Messenger"), ("Bia", "Rượu"), ("Karaoke", "Quán bar"),
    ("Tết", "Trung thu"), ("Áo dài", "Áo bà ba"), ("Nón lá", "Mũ bảo hiểm"), ("Sầu riêng", "Mít"),
    ("Xoài", "Đu đủ"), ("Dưa hấu", "Thanh long"), ("Gà rán", "Gà nướng"), ("Lẩu", "Đồ nướng"),
    ("Bóng đá", "Bóng chuyền"), ("Cầu lông", "Tennis"), ("Bơi lội", "Lặn biển"), ("Máy bay", "Tàu hoả"),
    ("Khách sạn", "Homestay"), ("Đà Lạt", "Sa Pa"), ("Phú Quốc", "Nha Trang"), ("Netflix", "YouTube"),
    ("Shopee", "Lazada"), ("Grab", "Taxi"), ("Sếp", "Đồng nghiệp"), ("Họp", "Hội thảo"),
    ("Excel", "Google Sheets"), ("Lương", "Thưởng"), ("Nghỉ phép", "Nghỉ lễ"), ("Cơm tấm", "Cơm gà"),
    ("Trà đá", "Nước mía"), ("Kem", "Sữa chua"), ("Bánh tráng trộn", "Gỏi cuốn"), ("Hủ tiếu", "Mì Quảng"),
    ("Chè", "Tào phớ"), ("Bánh xèo", "Bánh khọt"), ("Vịt", "Ngỗng"), ("Hổ", "Sư tử"),
    ("Bác sĩ", "Y tá"), ("Giáo viên", "Gia sư"), ("Ca sĩ", "Diễn viên"), ("Son môi", "Phấn má"),
    ("Ô", "Áo mưa"), ("Dép", "Giày"), ("Gối", "Chăn"), ("Tủ lạnh", "Tủ đông"),
    ("Máy giặt", "Máy rửa bát"), ("Mặt trăng", "Mặt trời"), ("Mưa", "Bão"), ("Lửa trại", "Pháo hoa"),
    ("Lều", "Võng"), ("Kính râm", "Kính cận"), ("Nhẫn", "Dây chuyền"), ("Đám cưới", "Đám hỏi"),
    ("Sinh nhật", "Kỷ niệm"), ("Ma", "Zombie"), ("Ông già Noel", "Táo quân"), ("Bánh chưng", "Bánh tét"),
]

SPY_ROLES = {
    "civ":   {"name": "Dân",       "icon": "🏡", "cls": "villa"},
    "spy":   {"name": "Gián điệp", "icon": "🕵️", "cls": "wolf"},
    "white": {"name": "Mũ Trắng",  "icon": "🎩", "cls": "seer"},
}


def norm(text):
    t = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if c.isalnum() else " " for c in t).split())


def spy_new_order(sp):
    alive = [n for n, i in sp["roster"].items() if i["alive"]]
    random.shuffle(alive)
    if len(alive) > 1 and sp["roster"][alive[0]]["role"] == "white":   # Mũ Trắng không nói đầu
        alive[0], alive[1] = alive[1], alive[0]
    sp["order"] = alive


def spy_check_win(sp):
    alive = [i for i in sp["roster"].values() if i["alive"]]
    civ = sum(1 for i in alive if i["role"] == "civ")
    spies = sum(1 for i in alive if i["role"] == "spy")
    imp = len(alive) - civ
    if imp == 0:
        sp["winner"] = "civ"
    elif civ <= imp:
        sp["winner"] = "spy" if spies else "white"
    else:
        return False
    reason = {"civ": "Dân đã tìm ra hết kẻ trà trộn!",
              "spy": "Gián điệp đã trụ lại đến cuối!",
              "white": "Mũ Trắng đã trụ lại đến cuối!"}[sp["winner"]]
    sp["result"] = (sp["result"] + " " + reason).strip()
    sp["phase"] = "END"
    return True


def spy_next_round(sp):
    if not spy_check_win(sp):
        sp["round"] += 1
        for i in sp["roster"].values():
            i["vote"] = None
        spy_new_order(sp)
        sp["phase"] = "DESC"


def spy_tally(sp):
    alive = {n for n, i in sp["roster"].items() if i["alive"]}
    counts = {}
    for n in alive:
        v = sp["roster"][n]["vote"]
        if v in alive:
            counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])



SPY_CARD = """
<button type="button" class="card-role {{ 'seer' if mine.role == 'white' else 'villa' }}">
  <span class="back">Chạm để xem từ khoá</span>
  <span class="face">
  {% if mine.role == 'white' %}
    <span class="icon">🎩</span><span class="rname" style="display:block">Mũ Trắng</span>
    <span class="rhint">Bạn không có từ khoá. Nghe mọi người rồi mô tả thật khéo để không bị lộ.</span>
  {% else %}
    <span class="icon">🔑</span><span class="rname" style="display:block">{{ mine.word }}</span>
    <span class="rhint">Từ khoá của bạn. Có thể bạn chính là Gián điệp mà không biết!</span>
  {% endif %}
  </span>
</button>
"""

SPY_BODY = """
<div data-phase="SPY-{{ sp.phase }}" data-game="{{ game_id }}" data-body="">
<div class="brand"><span>HILtrip Gián Điệp</span><a href="/logout">Thoát</a></div>

{% if not mine %}
  <h1>🕵️ Đang có ván Gián Điệp</h1>
  <p class="sub">Bạn vào sau khi chia từ nên ván này chỉ xem. Chờ ván sau nhé.</p>

{% elif sp.phase == 'END' %}
  <h1>{% if sp.winner == 'civ' %}🏡 Dân thắng{% elif sp.winner == 'spy' %}🕵️ Gián điệp thắng{% else %}🎩 Mũ Trắng thắng{% endif %}</h1>
  <p class="sub">{{ sp.result }}</p>
  <div class="panel">
    <p style="margin:0 0 6px">Từ của Dân: <b class="r-villa">{{ sp.civ_word }}</b></p>
    <p style="margin:0">Từ của Gián điệp: <b class="r-wolf">{{ sp.spy_word }}</b></p>
  </div>

{% else %}
  {% if sp.phase == 'DESC' %}
    <h1>🕵️ Vòng {{ sp.round }}</h1>
    <p class="sub">Lần lượt mô tả từ khoá của bạn bằng một câu, đừng nói thẳng từ đó.</p>
  {% elif sp.phase == 'VOTE' %}
    <h1>⚖️ Ai là gián điệp?</h1>
    <p class="sub">Chọn người bạn nghi nhất. Hoà phiếu thì không ai bị loại.</p>
  {% else %}
    <h1>🎩 Mũ Trắng đoán từ</h1>
    <p class="sub">{{ sp.result }}</p>
  {% endif %}

  """ + SPY_CARD + """

  {% if sp.phase == 'DESC' and sp.result %}<div class="note" style="margin-top:14px">{{ sp.result }}</div>{% endif %}

  {% if sp.phase == 'VOTE' %}
    <div class="panel" style="margin-top:14px">
    {% if not mine.alive %}
      <div class="note bad">Bạn đã bị loại nên không bỏ phiếu.</div>
    {% else %}
      <form method="POST" action="/spy/vote">
        <select name="target">
          {% for p in sp.order %}{% if p != me %}<option value="{{ p }}" {{ 'selected' if mine.vote == p }}>{{ p }}</option>{% endif %}{% endfor %}
        </select>
        <button type="submit" class="btn-wolf">{{ 'Đổi phiếu' if mine.vote else 'Bỏ phiếu' }}</button>
      </form>
      {% if mine.vote %}<div class="note done" style="margin-top:12px">Bạn đã bầu: {{ mine.vote }}. Có thể đổi đến khi quản trò chốt.</div>{% endif %}
    {% endif %}
    </div>
    <p class="sub" style="margin-top:14px">Đã bỏ phiếu: {{ voted }} / {{ alive }} người.</p>
  {% elif sp.phase == 'GUESS' %}
    <div class="panel" style="margin-top:14px">
    {% if me == sp.guesser %}
      <h2>Từ khoá của Dân là gì?</h2>
      <form method="POST" action="/spy/guess">
        <input type="text" name="guess" maxlength="40" placeholder="Nhập từ bạn đoán" required autocomplete="off">
        <button type="submit" class="btn-seer">Đoán</button>
      </form>
    {% else %}
      <div class="note">{{ sp.guesser }} là Mũ Trắng và đang đoán từ của Dân…</div>
    {% endif %}
    </div>
  {% endif %}
{% endif %}

<div class="panel" style="margin-top:14px">
  {% if sp.phase in ('DESC', 'VOTE') %}<h2>Thứ tự nói vòng {{ sp.round }}</h2>
  <ol class="roster order">
    {% for p in sp.order %}<li class="{{ 'me' if p == me }}"><span>{{ p }}</span>{% if p == me %}<span class="count">bạn</span>{% endif %}</li>{% endfor %}
  </ol>{% endif %}
  {% set outs = [] %}{% for p, i in sp.roster.items() %}{% if not i.alive or sp.phase == 'END' %}{% set _ = outs.append(p) %}{% endif %}{% endfor %}
  {% if outs %}
  <h2 {% if sp.phase in ('DESC', 'VOTE') %}style="margin-top:16px"{% endif %}>{{ 'Lật bài' if sp.phase == 'END' else 'Đã bị loại' }}</h2>
  <ul class="roster">
    {% for p in outs %}{% set i = sp.roster[p] %}<li class="{{ 'out' if not i.alive }}"><span>{{ p }}</span>
      <span class="r-{{ sroles[i.role].cls }}">{{ sroles[i.role].icon }} {{ sroles[i.role].name }}</span></li>{% endfor %}
  </ul>
  {% endif %}
</div>
</div>
"""

SPY_ADMIN = """
<div data-phase="SPY-{{ sp.phase }}" data-game="{{ game_id }}" data-body="">
<div class="brand"><span>HILtrip Quản trò</span><a href="/">Màn hình người chơi</a></div>
<h1>🕵️ Gián Điệp{% if sp.phase != 'END' %}, vòng {{ sp.round }}{% endif %}</h1>
<p class="sub">{{ alive }} người còn trong cuộc · {{ nspy }} gián điệp{% if nwhite %}, {{ nwhite }} Mũ Trắng{% endif %} lúc chia</p>

{% if sp.result %}<div class="panel"><div class="note {{ 'done' if sp.phase == 'END' }}">{{ sp.result }}</div></div>{% endif %}

{% if sp.phase == 'VOTE' %}
<div class="panel">
  <h2>Phiếu bầu <span class="count">· {{ voted }}/{{ alive }} đã bỏ</span></h2>
  <ul class="roster">
    {% for name, n in tally %}<li><span>{{ name }}</span><span class="tag dead">{{ n }} phiếu</span></li>
    {% else %}<li><span class="count">Chưa có phiếu nào.</span></li>{% endfor %}
  </ul>
</div>
{% endif %}

<div class="panel">
  {% if sp.phase in ('DESC', 'VOTE') %}<h2>Thứ tự nói</h2>
  <ol class="roster order">{% for p in sp.order %}<li><span>{{ p }}</span></li>{% endfor %}</ol>{% endif %}
  <h2 {% if sp.phase in ('DESC', 'VOTE') %}style="margin-top:16px"{% endif %}>Tất cả người chơi</h2>
  <ul class="roster">
    {% for p, i in sp.roster.items() %}<li class="{{ 'out' if not i.alive }}"><span>{{ p }}</span>
      {% if not i.alive or sp.phase == 'END' %}<span class="r-{{ sroles[i.role].cls }}">{{ sroles[i.role].icon }} {{ sroles[i.role].name }}</span>
      {% else %}<span class="tag ok">Còn trong cuộc</span>{% endif %}</li>{% endfor %}
  </ul>
</div>

{% if sp.phase == 'END' %}
<div class="panel">
  <p style="margin:0 0 6px">Từ của Dân: <b class="r-villa">{{ sp.civ_word }}</b></p>
  <p style="margin:0">Từ của Gián điệp: <b class="r-wolf">{{ sp.spy_word }}</b></p>
</div>
{% else %}
<div class="panel"><details><summary>Xem từ khoá (chỉ quản trò, đừng để người chơi nhìn)</summary>
  <p style="margin:12px 0 4px">Dân: <b class="r-villa">{{ sp.civ_word }}</b></p>
  <p style="margin:0">Gián điệp: <b class="r-wolf">{{ sp.spy_word }}</b></p></details></div>
{% endif %}

<div class="panel stack">
  {% if sp.phase == 'DESC' %}
  <form action="/admin/spy/open_vote" method="POST"><button type="submit">Mọi người đã nói xong, mở biểu quyết</button></form>
  {% elif sp.phase == 'VOTE' %}
  <form action="/admin/spy/close_vote" method="POST" data-confirm="Chốt phiếu và loại người nhiều phiếu nhất?"><button type="submit" class="btn-wolf">Chốt phiếu</button></form>
  {% elif sp.phase == 'GUESS' %}
  <form action="/admin/spy/skip_guess" method="POST" data-confirm="Coi như Mũ Trắng đoán sai?"><button type="submit" class="btn-ghost">Mũ Trắng không đoán, đi tiếp</button></form>
  {% else %}
  """ + SPY_START_FORM.replace("Bắt đầu Truy Tìm Gián Điệp", "Ván Gián Điệp mới") + """
  <form action="/admin/assign" method="POST"><button type="submit">Chuyển sang Ma Sói</button></form>
  {% endif %}
  {% if sp.phase != 'END' %}
  <form action="/admin/spy/end" method="POST" data-confirm="Kết thúc ván và lật bài cho mọi người?"><button type="submit" class="btn-ghost">Kết thúc ván, lật bài</button></form>
  {% endif %}
  <form action="/admin/reset" method="POST" data-confirm="Xoá toàn bộ người chơi và làm lại từ đầu?"><button type="submit" class="btn-ghost">Xoá phòng, làm lại từ đầu</button></form>
</div>
</div>
"""


def spy_ctx():
    sp = game_data["spy"]
    alive = [i for i in sp["roster"].values() if i["alive"]]
    roles = [i["role"] for i in sp["roster"].values()]
    return dict(sp=sp, sroles=SPY_ROLES, game_id=game_data["game_id"],
                alive=len(alive), voted=sum(1 for i in alive if i["vote"]),
                tally=spy_tally(sp), nspy=roles.count("spy"), nwhite=roles.count("white"),
                npairs=len(WORD_PAIRS), total=len(game_data["players"]))


def spy_index(username):
    sp = game_data["spy"]
    body = render_template_string(SPY_BODY, me=username, mine=sp["roster"].get(username), **spy_ctx())
    return page("HILtrip Gián Điệp", body, "", "/?partial=1")


def spy_admin():
    body = render_template_string(SPY_ADMIN, **spy_ctx())
    return page("HILtrip Quản trò", body, "", "/admin?partial=1")


@app.route('/admin/spy/start', methods=['POST'])
def spy_start():
    with _lock:
        names = list(game_data["players"].keys())
        n = len(names)
        if n < 3:
            return redirect(url_for('admin_panel'))
        civ_w = (request.form.get("civ") or "").strip()[:40]
        spy_w = (request.form.get("spy") or "").strip()[:40]
        if not (civ_w and spy_w) or norm(civ_w) == norm(spy_w):
            civ_w, spy_w = random.choice(WORD_PAIRS)
            if random.random() < 0.5:
                civ_w, spy_w = spy_w, civ_w
        if n <= 6:
            spies, whites = 1, 0
        elif n <= 9:
            spies, whites = 1, 1
        elif n <= 12:
            spies, whites = 2, 1
        else:
            spies, whites = 3, 1
        roles = ["spy"] * spies + ["white"] * whites
        roles += ["civ"] * (n - len(roles))
        random.shuffle(roles)
        roster = {}
        for name, r in zip(names, roles):
            word = civ_w if r == "civ" else (spy_w if r == "spy" else None)
            roster[name] = {"role": r, "word": word, "alive": True, "vote": None}
        sp = {"phase": "DESC", "round": 1, "roster": roster, "order": [], "civ_word": civ_w,
              "spy_word": spy_w, "result": "", "winner": None, "guesser": None}
        spy_new_order(sp)
        game_data["spy"] = sp
        game_data["phase"] = "SPY"
        game_data["game_id"] += 1
        bump()
    return redirect(url_for('admin_panel'))


@app.route('/spy/vote', methods=['POST'])
def spy_vote():
    username = session.get('username')
    target = request.form.get('target')
    with _lock:
        sp = game_data.get("spy")
        if sp and game_data["phase"] == "SPY" and sp["phase"] == "VOTE":
            me, t = sp["roster"].get(username), sp["roster"].get(target)
            if me and t and me["alive"] and t["alive"] and target != username:
                me["vote"] = target
                bump()
    return redirect(url_for('index'))


@app.route('/admin/spy/open_vote', methods=['POST'])
def spy_open_vote():
    with _lock:
        sp = game_data.get("spy")
        if sp and sp["phase"] == "DESC":
            for i in sp["roster"].values():
                i["vote"] = None
            sp["phase"] = "VOTE"
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/spy/close_vote', methods=['POST'])
def spy_close_vote():
    with _lock:
        sp = game_data.get("spy")
        if not sp or sp["phase"] != "VOTE":
            return redirect(url_for('admin_panel'))
        tally = spy_tally(sp)
        if not tally:
            sp["result"] = "Không ai bỏ phiếu, vòng này không ai bị loại."
        elif len(tally) > 1 and tally[0][1] == tally[1][1]:
            sp["result"] = f"Hoà phiếu ({tally[0][1]} phiếu), vòng này không ai bị loại."
        else:
            name, n = tally[0]
            p = sp["roster"][name]
            p["alive"] = False
            r = SPY_ROLES[p["role"]]
            sp["result"] = f"{name} bị loại với {n} phiếu. {name} là {r['icon']} {r['name']}."
            if p["role"] == "white":
                sp["guesser"] = name
                sp["phase"] = "GUESS"
                bump()
                return redirect(url_for('admin_panel'))
        spy_next_round(sp)
        bump()
    return redirect(url_for('admin_panel'))


def spy_resolve_guess(guess):
    sp = game_data["spy"]
    g = sp["guesser"]
    if guess and norm(guess) == norm(sp["civ_word"]):
        sp["result"] += f" {g} đoán đúng từ “{sp['civ_word']}”!"
        sp["winner"] = "white"
        sp["phase"] = "END"
    else:
        shown = f"“{guess}”" if guess else "không đoán"
        sp["result"] += f" {g} đoán {shown}, sai rồi."
        spy_next_round(sp)
    sp["guesser"] = None
    bump()


@app.route('/spy/guess', methods=['POST'])
def spy_guess():
    username = session.get('username')
    with _lock:
        sp = game_data.get("spy")
        if sp and sp["phase"] == "GUESS" and sp["guesser"] == username:
            spy_resolve_guess((request.form.get("guess") or "").strip()[:40])
    return redirect(url_for('index'))


@app.route('/admin/spy/skip_guess', methods=['POST'])
def spy_skip_guess():
    with _lock:
        sp = game_data.get("spy")
        if sp and sp["phase"] == "GUESS":
            spy_resolve_guess("")
    return redirect(url_for('admin_panel'))


@app.route('/admin/spy/end', methods=['POST'])
def spy_end():
    with _lock:
        sp = game_data.get("spy")
        if sp and sp["phase"] != "END":
            sp["phase"] = "END"
            sp["winner"] = None
            sp["result"] = "Quản trò đã kết thúc ván."
            bump()
    return redirect(url_for('admin_panel'))



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
