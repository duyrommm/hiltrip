import os
import json
import random
import threading
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template_string, request, redirect, url_for, session, abort, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(32)   # đặt SECRET_KEY trên Render (Environment)
# Phiên đăng nhập sống 7 ngày: tắt trình duyệt mở lại vẫn vào đúng người chơi
app.permanent_session_lifetime = timedelta(days=7)
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
    "tod": None,              # Thật hay Thách
    "mission": None,          # Điệp vụ Vũng Tàu, chơi suốt chuyến, không bị xoá khi reset phòng
    "owners": {},             # tên người chơi -> mã trình duyệt đã tạo tên đó (chống trùng tên)
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
<form action="/admin/spy/start" method="POST" class="stack"><input type="hidden" name="gid" value="{{ gid() }}">
  <details class="note"><summary>Quản trò tự nhập từ (nếu muốn)</summary>
    <div style="margin-top:12px">
      <input type="text" name="civ" maxlength="40" placeholder="Từ của Dân, ví dụ: Cà phê" autocomplete="off">
      <input type="text" name="spy" maxlength="40" placeholder="Từ của Gián điệp, ví dụ: Trà sữa" autocomplete="off">
      <p class="count" style="margin:0">Để trống thì app tự bốc ngẫu nhiên trong {{ npairs }} cặp có sẵn.</p>
    </div>
  </details>
  <button type="submit" class="btn-seer" {{ 'disabled' if total < 3 }}>Bắt đầu Truy Tìm Gián Điệp</button>
</form>
"""


# ---------------------------------------------------------------- lưu trạng thái (PostgreSQL)
# Có DATABASE_URL → mỗi lần trạng thái đổi sẽ lưu vào DB, khởi động lại thì nạp lại.
# Nhờ vậy Render ngủ/khởi động lại cũng không mất phòng, điểm, nhiệm vụ.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
VN_TZ = timezone(timedelta(hours=7))
log = logging.getLogger("hiltrip")


def _db():
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=5, autocommit=True)


def db_load():
    if not DATABASE_URL:
        return
    try:
        with _db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS hiltrip_state "
                      "(id INT PRIMARY KEY, data TEXT NOT NULL, updated TIMESTAMPTZ DEFAULT now())")
            row = c.execute("SELECT data FROM hiltrip_state WHERE id = 1").fetchone()
        if row:
            saved = json.loads(row[0])
            game_data.update(saved)
            log.warning("Đã nạp trạng thái từ DB (version %s)", game_data.get("version"))
    except Exception as e:                      # DB lỗi thì vẫn chạy trong bộ nhớ
        log.warning("Không nạp được DB: %s", e)


def db_save():
    if not DATABASE_URL:
        return
    try:
        data = json.dumps(game_data, ensure_ascii=False)
        with _db() as c:
            c.execute("INSERT INTO hiltrip_state (id, data, updated) VALUES (1, %s, now()) "
                      "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated = now()", (data,))
    except Exception as e:
        log.warning("Không lưu được DB: %s", e)


def bump():
    game_data["version"] += 1
    db_save()


AVATARS = ["🦀", "🐙", "🐬", "🐢", "🦈", "🐳", "🦑", "🐠", "🦞", "🐚",
           "🐶", "🐱", "🦊", "🐼", "🐨", "🐯", "🦁", "🐸", "🐵", "🐧",
           "🦄", "🐝", "🦉", "🐻"]


def av(name):
    """Icon của người chơi để hiện cạnh tên."""
    if not name or name.startswith("🤖"):
        return ""
    return (game_data["players"].get(name) or {}).get("avatar") or "🙂"


app.jinja_env.globals["av"] = av
app.jinja_env.globals["gid"] = lambda: game_data["game_id"]


def fresh_gid():
    """Chống bấm lặp: form bắt đầu ván mang số ván lúc hiện nút. Bấm lần 2 (số đã cũ) thì bỏ qua."""
    v = request.form.get("gid")
    if v is None:
        return True
    try:
        return int(v) == game_data["game_id"]
    except ValueError:
        return False


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


LAST_SEEN = {}        # tên -> lần cuối máy của người đó còn mở app (chỉ trong bộ nhớ)
ACTIVE_SECS = 25      # trong khoảng này coi như người đó đang online


def browser_id():
    """Mỗi trình duyệt có một mã riêng, giữ 7 ngày trong cookie."""
    b = session.get("bid")
    if not b:
        b = secrets.token_hex(8)
        session["bid"] = b
    session.permanent = True
    return b


def name_known(name):
    m = game_data.get("mission")
    return name in game_data["players"] or bool(m and name in m["agents"])


@app.before_request
def touch_session():
    """Ghi nhận người chơi đang online; người chơi cũ chưa có chủ thì gán chủ là trình duyệt này."""
    u = session.get("username")
    if not u:
        return None
    owners = game_data.setdefault("owners", {})
    bid = browser_id()
    if owners.get(u) is None:
        owners[u] = bid
    if owners.get(u) == bid:
        LAST_SEEN[u] = time.time()
    else:
        session.pop("username", None)   # tên đã được vào lại trên máy khác: máy này về trang nhập tên
    return None


@app.before_request
def guard_admin():
    if not ADMIN_KEY or not request.path.startswith("/admin"):
        return None
    if request.args.get("key") == ADMIN_KEY:
        session["is_admin"] = True
        session.permanent = True
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
.brand span a + a{margin-left:6px}
.avs{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:14px}
.avs label{position:relative;cursor:pointer}
.avs input{position:absolute;opacity:0;width:1px;height:1px}
.avs span{display:flex;align-items:center;justify-content:center;font-size:26px;height:48px;border:1px solid var(--line);border-radius:10px;background:var(--ink)}
.avs input:checked + span{border:2px solid var(--villa);background:var(--ink-2);transform:scale(1.06)}
.avs input:disabled + span{opacity:.22;cursor:not-allowed}
.avs input:focus-visible + span{outline:3px solid var(--seer);outline-offset:2px}
.banner{display:block;text-decoration:none;color:var(--moon);font-weight:700;border:1px solid var(--guard);background:var(--ink-2);border-radius:var(--radius);padding:14px 16px;margin-bottom:14px}
.banner small{display:block;color:var(--mist);font-weight:400;margin-top:2px}
.center{text-align:center}
.wheel-wrap{position:relative;width:290px;height:290px;margin:4px auto 18px}
.wheel{width:100%;height:100%;border-radius:50%;border:4px solid var(--villa);position:relative;box-shadow:0 0 0 6px var(--ink-2)}
.wi{position:absolute;left:50%;top:50%;font-size:24px;line-height:1}
.pointer{position:absolute;left:50%;top:-14px;transform:translateX(-50%);width:0;height:0;border-left:14px solid transparent;border-right:14px solid transparent;border-top:24px solid var(--villa);z-index:2}
.hub{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:54px;height:54px;border-radius:50%;background:var(--ink-2);border:3px solid var(--villa);display:flex;align-items:center;justify-content:center;font-size:26px}
.btn-spin{background:var(--villa);color:#1d1a10;font-size:20px;padding:16px}
.btn-truth{background:var(--seer);color:#fff;font-size:18px;padding:18px 10px}
.btn-dare{background:var(--hunter);color:#1d1a10}
.duo .btn-dare{font-size:18px;padding:18px 10px}
.btn-ok{background:var(--ok);color:#10261a;font-size:18px}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tod-card{border-radius:18px;padding:22px 18px;margin:6px 0 14px;background:var(--ink-2);border:2px solid var(--seer);text-align:center}
.tod-card.dare{border-color:var(--hunter)} .tod-card.pen{border-color:var(--wolf)}
.tod-card .kind{font-weight:700;color:var(--mist);margin-bottom:8px}
.tod-card p{margin:0;font:600 22px/1.35 "Lora",Georgia,serif}
.after-spin.hide{visibility:hidden}
button.armed{outline:3px solid var(--wolf);outline-offset:2px}
.ptabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
.ptabs::-webkit-scrollbar{display:none}
.ptabs button{flex:0 0 auto;width:auto;padding:9px 13px;font-size:14px;font-weight:500;background:var(--ink-2);color:var(--mist);border:1px solid var(--line);border-radius:999px}
.ptabs button.on{background:var(--villa);color:#1d1a10;border-color:var(--villa);font-weight:700}
.plist summary{list-style:none} .plist summary::-webkit-details-marker{display:none}
.plist[open] summary{margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chip{width:auto;padding:6px 10px;font-size:14px;font-weight:500;background:var(--ink);color:var(--moon);border:1px solid var(--line);border-radius:999px}
.addrow{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.addrow input{margin:0;flex:1}
.addrow .mini{padding:10px 14px}
.wbox{position:relative;width:100%;max-width:340px;margin:6px auto 14px}
.wbox canvas{position:relative;z-index:1;cursor:pointer;display:block;margin:0 auto;border-radius:50%;box-shadow:0 6px 24px rgba(0,0,0,.35)}
.wpointer{position:absolute;left:50%;top:-10px;transform:translateX(-50%);width:0;height:0;border-left:13px solid transparent;border-right:13px solid transparent;border-top:24px solid #6366f1;z-index:2;filter:drop-shadow(0 2px 2px rgba(0,0,0,.4))}
.wspin{position:absolute;z-index:3;left:50%;top:50%;transform:translate(-50%,-50%);width:78px;height:78px;border-radius:50%;padding:0;background:#6366f1;color:#fff;font-size:17px;border:4px solid #fff;box-shadow:0 2px 10px rgba(0,0,0,.35)}
.wspin:disabled{opacity:.85}
.wspin:active{transform:translate(-50%,-50%) scale(.95)}
.result{text-align:center;margin:4px 0 12px}
.result .big,.big{font:700 28px/1.25 "Lora",Georgia,serif}
.grid3{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.team{border-width:2px}
.bignum{font-size:28px;min-width:40px;text-align:center}
.draws{list-style:none;padding:0;margin:14px 0 0}
.draws li{display:flex;align-items:center;gap:12px;background:var(--ink-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:8px;font-weight:600;animation:pop .35s ease both}
.medal{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;font-weight:700;color:#1d1a10;background:#e98a3a}
.medal.m1{background:#f2c12e}.medal.m2{background:#b9c2cf}.medal.m3{background:#cd8a52}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.dice{font-size:84px;line-height:1.1;letter-spacing:6px}
.coin{font-size:96px;line-height:1.1}
.coin.flip{animation:flip .7s ease}
@keyframes flip{0%{transform:rotateY(0)}100%{transform:rotateY(1080deg)}}
button.sel{border-color:var(--villa);color:var(--moon)}
@media (prefers-reduced-motion:reduce){.coin.flip,.draws li{animation:none}}
.mini{display:inline-block;width:auto;padding:4px 12px;font-size:13px;font-weight:500}
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

  var lastSpin = null, lastDeg = 0;
  function spinWheel(){
    var w = app.querySelector('.wheel'); if (!w) return;
    var id = w.dataset.spin, deg = parseFloat(w.dataset.deg) || 0;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (lastSpin !== null && id !== lastSpin && deg && !reduce){
      var res = app.querySelector('.after-spin');
      if (res) res.classList.add('hide');
      w.style.transition = 'none'; w.style.transform = 'rotate(' + (lastDeg % 360) + 'deg)';
      requestAnimationFrame(function(){ requestAnimationFrame(function(){
        w.style.transition = 'transform 4.2s cubic-bezier(.15,.7,.2,1)';
        w.style.transform = 'rotate(' + deg + 'deg)';
      }); });
      setTimeout(function(){ var r = app.querySelector('.after-spin'); if (r) r.classList.remove('hide'); }, 4300);
    } else {
      w.style.transform = 'rotate(' + deg + 'deg)';
    }
    lastSpin = id; lastDeg = deg;
  }

  function wire(){
    spinWheel();
    app.querySelectorAll('form[data-ajax]').forEach(function(f){
      f.onsubmit = function(e){
        e.preventDefault();
        f.querySelectorAll('button').forEach(function(b){ b.disabled = true; });
        fetch(f.action, {method:'POST', body:new FormData(f)}).then(poll, poll);
      };
    });
    var c = app.querySelector('.card-role');
    if (c){
      if (opened) c.classList.add('open');
      c.onclick = function(){ opened = !opened; c.classList.toggle('open', opened); document.body.classList.toggle('revealed', opened); };
    }
    // Xác nhận bằng cách chạm 2 lần (không dùng hộp thoại confirm của trình duyệt,
    // vì trình duyệt trong Zalo/Messenger hoặc trình duyệt đã chặn hộp thoại sẽ không hiện)
    app.querySelectorAll('form[data-confirm]').forEach(function(f){
      f.onsubmit = function(e){
        if (f.dataset.armed === '1') return true;
        e.preventDefault();
        var b = f.querySelector('button'); if (!b) return false;
        var mini = b.classList.contains('mini');
        f.dataset.armed = '1';
        var old = b.innerHTML;
        b.innerHTML = mini ? 'Chắc chưa?' : '⚠️ Chạm lần nữa để xác nhận';
        b.classList.add('armed');
        var note = null;
        if (!mini){
          note = document.createElement('p');
          note.className = 'count'; note.style.margin = '6px 0 0'; note.textContent = f.dataset.confirm;
          f.appendChild(note);
        }
        setTimeout(function(){
          f.dataset.armed = ''; b.innerHTML = old; b.classList.remove('armed');
          if (note) note.remove();
        }, 5000);
        return false;
      };
    });
    // Khoá nút sau khi gửi để không bấm lặp khi mạng chậm
    app.querySelectorAll('form:not([data-ajax])').forEach(function(f){
      if (f.dataset.lockWired) return; f.dataset.lockWired = '1';
      f.addEventListener('submit', function(e){
        if (e.defaultPrevented) return;
        setTimeout(function(){
          f.querySelectorAll('button[type=submit]').forEach(function(b){ b.disabled = true; b.innerHTML = 'Đang xử lý…'; });
        }, 0);
      });
    });
    var cls = app.querySelector('[data-body]');
    if (cls) document.body.className = cls.dataset.body;
    document.body.classList.toggle('revealed', opened && !!c);
  }

  function refresh(){
    if (app.querySelector('[data-static]')) return;
    if (app.querySelector('form[action="/join"]')) return;
    if (app.querySelector('form[data-armed="1"]')) { setTimeout(refresh, 1500); return; }
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

  if (app.querySelector('[data-static]')) live.style.display = 'none';
  wire();
  // quay lại trang bằng nút Back trên iPhone: tải lại để nút không bị khoá
  window.addEventListener('pageshow', function(e){ if (e.persisted) location.reload(); });
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
<div class="brand"><span>{{ 'HILtrip' if (not me or phase == 'LOBBY') else 'HILtrip Ma Sói' }}</span>
  <span>{% if phase == 'LOBBY' or not me %}<a href="/picker">🎲 Random</a> {% endif %}{% if mission_on %}<a href="/mission">🎯 Điệp vụ</a> {% endif %}{% if me %}<a href="/logout">Thoát</a>{% endif %}</span></div>

{% if not me and join_msg and join_msg[0] == 'confirm' %}
  <h1>Bạn vào lại?</h1>
  <p class="sub">Tên <b>{{ av(join_msg[1]) }} {{ join_msg[1] }}</b> đã có trong phòng, đang dùng trên một máy hoặc trình duyệt khác.</p>
  <div class="panel stack">
    <p style="margin:0">Nếu đó là bạn (đổi máy, mở lại bằng trình duyệt khác…), bấm <b>Đúng, là tôi</b> để vào lại đúng vai và điểm của mình.</p>
    <form method="POST" action="/join"><input type="hidden" name="username" value="{{ join_msg[1] }}"><input type="hidden" name="confirm" value="1">{% if keep_avatar %}<input type="hidden" name="avatar" value="{{ keep_avatar }}">{% endif %}
      <button type="submit">Đúng, là tôi</button></form>
    <a class="btn btn-ghost" href="/">Không phải, tôi chọn tên khác</a>
  </div>

{% elif not me %}
  <h1>Vào phòng chơi</h1>
  <p class="sub">Chọn một icon và nhập tên để mọi người nhận ra bạn.</p>
  {% if join_msg and join_msg[0] == 'busy' %}<div class="note bad" style="margin-bottom:14px">Tên “{{ join_msg[1] }}” đang có người dùng trên máy khác. Bạn chọn tên khác nhé, ví dụ thêm họ hoặc biệt danh.</div>{% endif %}
  {% if in_app %}<div class="note" style="margin-bottom:14px">Bạn đang mở link trong Zalo/Messenger. Nên bấm <b>⋯ → Mở bằng trình duyệt</b> (Chrome/Safari) để lần sau mở lại không phải nhập tên.</div>{% endif %}
  <form method="POST" action="/join" class="panel">
    <div class="avs" role="radiogroup" aria-label="Chọn icon">
      {% for e in avatars %}<label title="{{ e }}"><input type="radio" name="avatar" value="{{ e }}" {{ 'disabled' if e in taken }}><span>{{ e }}</span></label>{% endfor %}
    </div>
    <input type="text" name="username" maxlength="30" placeholder="Tên của bạn, ví dụ: Duy đẹp trai" required autocomplete="off">
    <button type="submit">Vào phòng</button>
    <p class="count" style="margin:10px 0 0">Icon mờ là đã có người chọn. Không chọn thì app chọn giúp. Đã chơi rồi thì nhập <b>đúng tên cũ</b> để giữ điểm.</p>
  </form>
  {% if phase != 'LOBBY' %}<p class="sub" style="margin-top:14px">Ván đang diễn ra. Bạn sẽ được chia vai ở ván sau.</p>{% endif %}

{% elif phase == 'LOBBY' %}
  <h1>Chào {{ av(me) }} {{ me }}</h1>
  <p class="sub">Đang chờ quản trò chọn trò chơi. Màn hình sẽ tự chuyển.</p>
  {% if mission_on %}<a class="banner" href="/mission">🎯 Điệp vụ Vũng Tàu đang diễn ra<small>Chạm để xem vai và nhiệm vụ của bạn</small></a>{% endif %}
  <div class="panel">
    <h2>Trong phòng <span class="count">· {{ players|length }} người</span></h2>
    <ul class="roster">
      {% for p in players %}<li class="{{ 'me' if p == me }}"><span>{{ av(p) }} {{ p }}</span>{% if p == me %}<span class="count">bạn</span>{% endif %}</li>{% endfor %}
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
    <div class="note bad" style="margin-top:14px">Bạn đã bị loại. Hãy im lặng giúp trò chơi vui hơn.</div>
  {% elif info and info.role == 'Wolf' %}
    <div class="panel secret" style="margin-top:14px">
    {% if mates %}<p class="sub" style="margin-bottom:12px">Đồng bọn: <b class="r-wolf">{{ mates|join(', ') }}</b></p>{% endif %}
    {% if not info.acted %}
      <h2>Cắn ai đêm nay?</h2>
      <form method="POST" action="/action">
        <select name="target" required><option value="" disabled selected>Chọn người…</option>
          {% for p, i in players.items() %}{% if p != me and i.role and i.alive and i.role != 'Wolf' %}<option value="{{ p }}">{{ av(p) }} {{ p }}</option>{% endif %}{% endfor %}
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
        <select name="target" required><option value="" disabled selected>Chọn người…</option>
          {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}">{{ av(p) }} {{ p }}{% if p in seen %} (đã soi){% endif %}</option>{% endif %}{% endfor %}
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
        {% for p, w in seen.items() %}<li><span>{{ av(p) }} {{ p }}</span>{% if w %}<span class="tag dead">Sói</span>{% else %}<span class="tag ok">Không phải Sói</span>{% endif %}</li>{% endfor %}
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
        <select name="target" required><option value="" disabled selected>Chọn người…</option>
          {% for p, i in players.items() %}{% if i.role and i.alive and p != info.last %}<option value="{{ p }}">{{ av(p) }} {{ p }}{% if p == me %} (chính mình){% endif %}</option>{% endif %}{% endfor %}
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
  {% if info and not info.alive %}<div class="note bad" style="margin-bottom:14px">Bạn đã bị loại. Hãy im lặng giúp trò chơi vui hơn.</div>{% endif %}
  <div class="panel">
    <h2>Dân làng <span class="count">· còn {{ alive }} / {{ total }}</span></h2>
    <ul class="roster">
      {% for p, i in players.items() %}{% if i.role %}
      <li class="{{ 'out' if not i.alive }} {{ 'me' if p == me }}"><span>{{ av(p) }} {{ p }}</span>
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
    <div class="note bad">Bạn đã bị loại nên không bỏ phiếu. Hãy im lặng giúp trò chơi vui hơn.</div>
  {% elif info.role == 'Idiot' and info.revealed %}
    <div class="note">Bạn là Thằng Ngốc đã lộ bài nên không được bỏ phiếu nữa.</div>
  {% else %}
  <div class="panel">
    <form method="POST" action="/vote">
      <select name="target" required><option value="" disabled selected>Chọn người…</option>
        {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}" {{ 'selected' if info.vote == p }}>{{ av(p) }} {{ p }}</option>{% endif %}{% endfor %}
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
      <select name="target" required><option value="" disabled selected>Chọn người…</option>
        {% for p, i in players.items() %}{% if p != me and i.role and i.alive %}<option value="{{ p }}">{{ av(p) }} {{ p }}</option>{% endif %}{% endfor %}
        <option value="__none__">Không bắn ai</option>
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
      <li class="{{ 'out' if not i.alive }}"><span>{{ av(p) }} {{ p }}</span><span class="r-{{ roles[i.role].cls }}">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span></li>
      {% endif %}{% endfor %}
    </ul>
  </div>
{% endif %}
</div>
"""


IN_APP_UA = ("Zalo", "FBAN", "FBAV", "FB_IAB", "Messenger", "Instagram", "Line/")


@app.route('/')
def index(join_msg=None, keep_avatar=None):
    username = session.get('username')
    # phòng đã reset → tên cũ không còn trong danh sách → về màn hình nhập tên
    if username and username not in game_data["players"]:
        session.pop('username', None)
        username = None
    if username and game_data["phase"] == "SPY":
        return spy_index(username)
    if username and game_data["phase"] == "TOD" and game_data.get("tod"):
        return tod_render()
    info = game_data["players"].get(username) if username else None
    phase = game_data["phase"]
    body_cls = "day" if phase in ("DAY", "VOTE", "HUNTER", "END") else ""
    players = game_data["players"]
    body = render_template_string(
        PLAYER_BODY, phase=phase, me=username, info=info, players=players,
        avatars=AVATARS, taken={i.get("avatar") for i in players.values()},
        join_msg=join_msg, keep_avatar=keep_avatar,
        in_app=any(k in request.headers.get("User-Agent", "") for k in IN_APP_UA),
        mission_on=bool(game_data.get("mission")),
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
    return page("HILtrip", body, body_cls, "/?partial=1")


@app.route('/state')
def state():
    return jsonify(v=game_data["version"])


@app.route('/join', methods=['POST'])
def join():
    username = (request.form.get('username') or '').strip()[:30]
    if username:
        bid = browser_id()
        with _lock:
            owners = game_data.setdefault("owners", {})
            owner = owners.get(username)
            if name_known(username) and owner and owner != bid:
                # tên đã thuộc về một trình duyệt khác
                if time.time() - LAST_SEEN.get(username, 0) < ACTIVE_SECS:
                    return index(join_msg=("busy", username))          # người kia đang online: chặn trùng tên
                if not request.form.get("confirm"):
                    return index(join_msg=("confirm", username), keep_avatar=request.form.get("avatar"))
            owners[username] = bid
            LAST_SEEN[username] = time.time()
            session['username'] = username
            players = game_data["players"]
            pick = request.form.get('avatar')
            taken = {i.get("avatar") for n, i in players.items() if n != username}
            if pick not in AVATARS or pick in taken:
                pick = None
            if username not in players:
                # vào giữa ván: chưa có vai, xem đến ván sau
                role = "Villager" if game_data["phase"] == "LOBBY" else None
                free = [e for e in AVATARS if e not in taken] or AVATARS
                players[username] = {"role": role, "alive": True, "target": None, "acted": False, "vote": None,
                                     "avatar": pick or random.choice(free)}
                bump()
            elif pick:
                players[username]["avatar"] = pick   # vào lại tên cũ: đổi icon nếu muốn
                bump()
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    u = session.pop('username', None)
    if u:
        LAST_SEEN.pop(u, None)     # đã thoát: không còn tính là đang online
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
<div class="brand"><span>HILtrip Quản trò</span><span><a href="/picker?from=admin">🎲</a> <a href="/admin/mission">🎯 Điệp vụ</a> <a href="/">Người chơi</a></span></div>
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
    {% for name, n in tally %}<li><span>{{ av(name) }} {{ name }}</span><span class="tag dead">{{ n }} phiếu</span></li>
    {% else %}<li><span class="count">Chưa có phiếu nào.</span></li>{% endfor %}
  </ul>
</div>
{% endif %}

<div class="panel">
  <h2>Người chơi</h2>
  <ul class="roster">
    {% for p, i in players.items() %}
    <li class="{{ 'out' if not i.alive }}"><span>{{ av(p) }} {{ p }}</span>
      {% if not i.role %}<span class="count">vào sau, xem</span>
      {% elif phase == 'LOBBY' %}<form method="POST" action="/admin/kick" style="display:inline" data-confirm="Xoá {{ p }} khỏi phòng?"><input type="hidden" name="name" value="{{ p }}"><button type="submit" class="btn-ghost mini">Xoá</button></form>
      {% elif phase == 'END' %}<span class="r-{{ roles[i.role].cls }}">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span>
      {% else %}<span>{% if i.revealed %}<span class="r-{{ roles[i.role].cls }}" style="margin-right:8px">{{ roles[i.role].icon }} {{ roles[i.role].name }}</span>{% endif %}
        {% if i.alive %}<span class="tag ok">Còn sống</span>{% else %}<span class="tag dead">Đã chết</span>{% endif %}</span>{% endif %}
    </li>
    {% else %}<li><span class="count">Chưa có ai. Gửi link trang chủ cho mọi người.</span></li>{% endfor %}
  </ul>
</div>

<div class="panel stack">
  {% if phase in ('LOBBY', 'END') %}
  <form action="/admin/assign" method="POST"><input type="hidden" name="gid" value="{{ gid() }}"><button type="submit" {{ 'disabled' if total < 3 }}>🐺 Chơi Ma Sói: chia vai, bắt đầu đêm</button></form>
  """ + SPY_START_FORM.replace("Bắt đầu Truy Tìm Gián Điệp", "🕵️ Chơi Truy Tìm Gián Điệp") + """
  <form action="/admin/tod/start" method="POST"><input type="hidden" name="gid" value="{{ gid() }}"><button type="submit" class="btn-dare" {{ 'disabled' if total < 2 }}>🎡 Chơi Thật hay Thách</button></form>
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
    if game_data["phase"] == "TOD" and game_data.get("tod"):
        return tod_render(admin=True)
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


@app.route('/admin/kick', methods=['POST'])
def admin_kick():
    name = request.form.get("name")
    with _lock:
        if game_data["phase"] == "LOBBY" and name in game_data["players"]:
            game_data["players"].pop(name)     # điểm Điệp vụ vẫn giữ theo tên
            LAST_SEEN.pop(name, None)
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/assign', methods=['POST'])
def admin_assign():
    with _lock:
        if not fresh_gid():
            return redirect(url_for('admin_panel'))
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
    ("Facebook", "Threads"), ("Zalo", "Messenger"), ("Bia", "Rượu"), ("Karaoke", "Quán bar"),
    ("Tết âm lịch", "Trung thu"), ("Áo dài", "Áo bà ba"), ("Nón lá", "Mũ bảo hiểm"), ("Sầu riêng", "Mít"),
    ("Xoài", "Đu đủ"), ("Dưa hấu", "Thanh long"), ("Gà rán", "Gà nướng"), ("Lẩu", "Đồ nướng"),
    ("Bóng đá", "Bóng chuyền"), ("Cầu lông", "Tennis"), ("Bơi lội", "Lặn biển"), ("Máy bay", "Tàu hoả"),
    ("Khách sạn", "Homestay"), ("Đà Lạt", "Sa Pa"), ("Phú Quốc", "Nha Trang"), ("Netflix", "YouTube"),
    ("Shopee", "Lazada"), ("Grab", "Taxi"), ("Sếp", "Đồng nghiệp"), ("Họp", "Hội thảo"),
    ("Excel", "Google Sheets"), ("Lương", "Thưởng"), ("Nghỉ phép", "Nghỉ lễ"), ("Cơm tấm", "Cơm gà"),
    ("Trà đá", "Nước mía"), ("Kem", "Sữa chua"), ("Bánh tráng trộn", "Gỏi cuốn"), ("Hủ tiếu", "Mì Quảng"),
    ("Chè", "Tào phớ"), ("Bánh xèo", "Bánh khọt"), ("Vịt", "Ngỗng"), ("Hổ", "Sư tử"),
    ("Bác sĩ", "Y tá"), ("Giáo viên", "Gia sư"), ("Ca sĩ", "Diễn viên"), ("Son môi", "Phấn má"),
    ("Ô", "Áo mưa"), ("Dép", "Giày"), ("Gối", "Chăn/Mền"), ("Tủ lạnh", "Tủ đông"),
    ("Máy giặt", "Máy rửa chén"), ("Mặt trăng", "Mặt trời"), ("Mưa", "Bão"), ("Lửa trại", "Pháo hoa"),
    ("Lều", "Võng"), ("Kính râm", "Kính cận"), ("Nhẫn", "Dây chuyền"), ("Đám cưới", "Đám hỏi"),
    ("Sinh nhật", "Kỷ niệm"), ("Ma", "Zombie"), ("Ông già Noel", "Táo quân"), ("Bánh chưng", "Bánh tét"),
    ("Bãi Trước", "Bãi Sau"), ("Vali", "Balo"), ("Kem chống nắng", "Sữa dưỡng thể"), ("Sạc dự phòng", "Cục sạc"),
    ("Kính bơi", "Phao cứu sinh"), ("Thuốc say xe", "Kẹo cao su"), ("Cà phê sữa đá", "Cà phê đen"), ("Trà chanh", "Trà đào"),
    ("Bánh tráng nướng", "Bánh tráng cuốn"), ("Bún riêu", "Bún mọc"), ("Bò bía", "Gỏi cuốn"), ("Bắp xào", "Khoai lang nướng"),
    ("Sữa tươi", "Sữa chua dẻo"), ("Bánh flan", "Rau câu"), ("Chè khúc bạch", "Chè Thái"), ("Máy lạnh", "Quạt máy"),
    ("Áo thun", "Áo sơ mi"), ("Giày thể thao", "Dép sandal"), ("Chăn bông", "Chiếu điều hòa"), ("Gội đầu", "Tắm hoa sen"),
    ("Nồi cơm điện", "Nồi chiên không dầu"), ("Bàn là", "Máy sấy tóc"), ("Bếp từ", "Bếp ga"), ("Máy hút bụi", "Chổi quét nhà"),
    ("Cốc thủy tinh", "Ca nhựa"), ("Móc phơi đồ", "Kẹp quần áo"), ("Bút bi", "Bút chì"), ("Gôm tẩy", "Bút xóa"),
    ("Sổ tay", "Giấy note"), ("Kẹp giấy", "Dập ghim"), ("Kéo", "Dao rọc giấy"), ("Bìa hồ sơ", "Bao thư"),
    ("Bảng trắng", "Bút lông bảng"), ("Thước kẻ", "Compa"), ("Siêu thị", "Chợ đêm"), ("Máy giặt", "Tiệm giặt ủi"),
    ("Đồng hồ báo thức", "Chuông nhà thờ"), ("Sofa", "Ghế salon"), ("Thang máy", "Thang bộ"), ("Lịch treo tường", "Sổ tay"),
    ("Tủ lạnh", "Thùng đá"), ("Bàn ăn", "Bàn tiệc"), ("Bàn chải răng", "Tăm xỉa"), ("Xích đu", "Ghế bập bênh"),
    ("Quạt trần", "Máy lạnh"), ("Bình hoa", "Chậu cây"), ("Gối ôm", "Gấu bông"), ("Phở bò", "Hủ tiếu"),
    ("Trà sữa", "Sinh tố"), ("Bánh mì", "Burger"), ("Lẩu thái", "Lẩu kim chi"), ("Bánh plan", "Bánh trứng"),
    ("Cơm tấm", "Bún thịt nướng"), ("Cà phê phin", "Cà phê máy"), ("Nem nướng", "Chả giò"), ("Bắp rang", "Snack khoai"),
    ("Nước mía", "Nước dừa"), ("Chè bưởi", "Chè thái"), ("Xôi mặn", "Xôi ngọt"), ("Thư viện", "Hiệu sách"),
    ("Công viên nước", "Hồ bơi"), ("Ga tàu", "Sân bay"), ("Bưu điện", "Cửa hàng tiện lợi"), ("Chợ nổi", "Làng nghề"),
    ("Phố đi bộ", "Night club"), ("Núi Bà Đen", "Bà Nà Hills"), ("Phố cổ Hội An", "Làng gốm Bát Tràng"), ("Bến Nhà Rồng", "Dinh Độc Lập"),
    ("Gym", "Yoga"), ("Mèo nhà", "Mèo rừng"), ("Chó Husky", "Chó Phú Quốc"), ("Cá heo", "Cá voi"),
    ("Đại bàng", "Diều hâu"), ("Rùa biển", "Rùa cạn"), ("Ong mật", "Bướm"), ("Voi", "Tê giác"),
    ("Cú mèo", "Chim cánh cụt"), ("Hươu cao cổ", "Ngựa vằn"), ("Khỉ đột", "Tinh tinh"), ("Running Man", "Squid Game"),
    ("Hai Phượng", "Lật Mặt"), ("Phim truyền hình", "Phim điện ảnh"), ("Cửa hàng tiện lợi", "Tạp hoá"), ("Freeship", "Giao hoả tốc"),
    ("Hoàn tiền", "Đổi trả"), ("Giờ ra chơi", "Giờ thể dục"), ("Bảng đen", "Máy chiếu"), ("Bài kiểm tra", "Bài thuyết trình"),
    ("Căn tin", "Đồ ăn vặt học sinh"), ("Kẹo mút", "Kẹo cao su"), ("Cặp sách", "Ba lô"), ("Lớp trưởng", "Bí thư lớp"),
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
<div class="brand"><span>HILtrip Gián Điệp</span><span>{% if mission_on %}<a href="/mission">🎯 Điệp vụ</a> {% endif %}<a href="/logout">Thoát</a></span></div>

{% if not mine %}
  <h1>🕵️ Đang có ván Gián Điệp</h1>
  <p class="sub">Bạn vào sau khi chia từ nên ván này chỉ xem. Chờ ván sau nhé.</p>

{% elif sp.phase == 'END' %}
  <h1>{% if sp.winner == 'civ' %}🏡 Dân thắng{% elif sp.winner == 'spy' %}🕵️ Gián điệp thắng{% elif sp.winner == 'white' %}🎩 Mũ Trắng thắng{% else %}Hết ván{% endif %}</h1>
  <p class="sub">{{ sp.result }}</p>
  <div class="panel">
    <p style="margin:0 0 6px">Từ của Dân: <b class="r-villa">{{ sp.civ_word }}</b></p>
    <p style="margin:0">Từ của Gián điệp: <b class="r-wolf">{{ sp.spy_word }}</b></p>
  </div>
  {% if mission_on %}<a class="banner" href="/mission" style="margin-top:14px">🎯 Điệp vụ Vũng Tàu đang diễn ra<small>Chạm để xem vai và nhiệm vụ của bạn</small></a>{% endif %}

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
  {% if sp.phase != 'VOTE' and not mine.alive %}<div class="note bad" style="margin-top:14px">Bạn đã bị loại. Hãy im lặng giúp trò chơi vui hơn.</div>{% endif %}

  {% if sp.phase == 'VOTE' %}
    <div class="panel" style="margin-top:14px">
    {% if not mine.alive %}
      <div class="note bad">Bạn đã bị loại nên không bỏ phiếu. Hãy im lặng giúp trò chơi vui hơn.</div>
    {% else %}
      <form method="POST" action="/spy/vote">
        <select name="target" required><option value="" disabled selected>Chọn người…</option>
          {% for p in sp.order %}{% if p != me %}<option value="{{ p }}" {{ 'selected' if mine.vote == p }}>{{ av(p) }} {{ p }}</option>{% endif %}{% endfor %}
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
    {% for p in sp.order %}<li class="{{ 'me' if p == me }}"><span>{{ av(p) }} {{ p }}</span>{% if p == me %}<span class="count">bạn</span>{% endif %}</li>{% endfor %}
  </ol>{% endif %}
  {% set outs = [] %}{% for p, i in sp.roster.items() %}{% if not i.alive or sp.phase == 'END' %}{% set _ = outs.append(p) %}{% endif %}{% endfor %}
  {% if outs %}
  <h2 {% if sp.phase in ('DESC', 'VOTE') %}style="margin-top:16px"{% endif %}>{{ 'Lật bài' if sp.phase == 'END' else 'Đã bị loại' }}</h2>
  <ul class="roster">
    {% for p in outs %}{% set i = sp.roster[p] %}<li class="{{ 'out' if not i.alive }}"><span>{{ av(p) }} {{ p }}</span>
      <span class="r-{{ sroles[i.role].cls }}">{{ sroles[i.role].icon }} {{ sroles[i.role].name }}</span></li>{% endfor %}
  </ul>
  {% endif %}
</div>
</div>
"""

SPY_ADMIN = """
<div data-phase="SPY-{{ sp.phase }}" data-game="{{ game_id }}" data-body="">
<div class="brand"><span>HILtrip Quản trò</span><span><a href="/picker?from=admin">🎲</a> <a href="/admin/mission">🎯 Điệp vụ</a> <a href="/">Người chơi</a></span></div>
<h1>🕵️ Gián Điệp{% if sp.phase != 'END' %}, vòng {{ sp.round }}{% endif %}</h1>
<p class="sub">{{ alive }} người còn trong cuộc · {{ nspy }} gián điệp{% if nwhite %}, {{ nwhite }} Mũ Trắng{% endif %} lúc chia</p>

{% if sp.result %}<div class="panel"><div class="note {{ 'done' if sp.phase == 'END' }}">{{ sp.result }}</div></div>{% endif %}

{% if sp.phase == 'VOTE' %}
<div class="panel">
  <h2>Phiếu bầu <span class="count">· {{ voted }}/{{ alive }} đã bỏ</span></h2>
  <ul class="roster">
    {% for name, n in tally %}<li><span>{{ av(name) }} {{ name }}</span><span class="tag dead">{{ n }} phiếu</span></li>
    {% else %}<li><span class="count">Chưa có phiếu nào.</span></li>{% endfor %}
  </ul>
</div>
{% endif %}

<div class="panel">
  {% if sp.phase in ('DESC', 'VOTE') %}<h2>Thứ tự nói</h2>
  <ol class="roster order">{% for p in sp.order %}<li><span>{{ av(p) }} {{ p }}</span></li>{% endfor %}</ol>{% endif %}
  <h2 {% if sp.phase in ('DESC', 'VOTE') %}style="margin-top:16px"{% endif %}>Tất cả người chơi</h2>
  <ul class="roster">
    {% for p, i in sp.roster.items() %}<li class="{{ 'out' if not i.alive }}"><span>{{ av(p) }} {{ p }}</span>
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
  <form action="/admin/assign" method="POST"><input type="hidden" name="gid" value="{{ gid() }}"><button type="submit">Chuyển sang Ma Sói</button></form>
  <form action="/admin/tod/start" method="POST"><input type="hidden" name="gid" value="{{ gid() }}"><button type="submit" class="btn-dare">🎡 Chơi Thật hay Thách</button></form>
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
    body = render_template_string(SPY_BODY, me=username, mine=sp["roster"].get(username),
                                  mission_on=bool(game_data.get("mission")), **spy_ctx())
    return page("HILtrip Gián Điệp", body, "", "/?partial=1")


def spy_admin():
    body = render_template_string(SPY_ADMIN, **spy_ctx())
    return page("HILtrip Quản trò", body, "", "/admin?partial=1")


@app.route('/admin/spy/start', methods=['POST'])
def spy_start():
    with _lock:
        if not fresh_gid():
            return redirect(url_for('admin_panel'))
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


# ================================================================ ĐIỆP VỤ VŨNG TÀU (chơi suốt chuyến đi)
# {t} = tên mục tiêu. Nhiệm vụ nhẹ nhàng, không ép uống, không trêu chọc quá đà.
MISSION_STAGES = [
    {"key": "road",   "name": "Khởi hành",          "when": "8h, 28/11",      "icon": "🚗",
     "tip": "Hai đoàn đang đi riêng, nhiệm vụ chặng này làm qua nhóm chat hoặc điện thoại."},
    {"key": "umi",    "name": "Cà phê Umi+",        "when": "10h30, 28/11",   "icon": "☕",
     "tip": "Hai đoàn vừa hội ngộ, tranh thủ lúc chào hỏi."},
    {"key": "lau",    "name": "Lẩu cá đuối",        "when": "12h, 28/11",     "icon": "🍲",
     "tip": "Bàn ăn đông người là chỗ dễ giăng bẫy nhất."},
    {"key": "villa",  "name": "Villa và biển",      "when": "14h, 28/11",     "icon": "🏖️",
     "tip": "Lúc nhận phòng, tắm biển, nghỉ ngơi."},
    {"key": "cho",    "name": "Chợ & chuẩn bị tiệc", "when": "16h, 28/11",     "icon": "🦀",
     "tip": "Một team đi chợ Xóm Lưới, một team ở villa chuẩn bị. Mục tiêu ở team khác thì làm qua nhóm chat hoặc chờ gặp lại, nhiệm vụ chưa xong vẫn giữ sang chặng tiệc."},
    {"key": "tiec",   "name": "Tiệc nướng",         "when": "18h, 28/11",     "icon": "🍢",
     "tip": "Chặng cuối trước giờ lật bài."},
]

MISSIONS = {
    "road": [
        "Khiến {t} gửi một sticker hoặc hình ảnh con vật bất kỳ vào nhóm chat chung.",
        "Khiến {t} nhắn vào nhóm chat một câu có chữ “biển”.",
        "Khiến {t} gửi một tấm ảnh đang trên đường vào nhóm chat.",
        "Khiến {t} trả lời tin nhắn của bạn bằng đúng một chữ “Ok”.",
        "Khiến {t} thả tim một tin nhắn của bạn trong nhóm chat.",
        "Khiến {t} nhắn hoặc gọi hỏi bạn “tới đâu rồi?”.",
        "Khiến {t} nhắn vào nhóm chat tên một món muốn ăn ở Vũng Tàu.",
        "Khiến {t} gửi một biểu tượng mặt khóc/mặt buồn vào nhóm chat.",
        "Khiến {t} nhắn vào nhóm chat một câu có chữ “cà phê”.",
        "Khiến {t} gửi một tin nhắn thoại cho bạn.",
        "Khiến {t} nhắn hỏi bạn đã ăn gì chưa.",
        "Khiến {t} gửi vào nhóm chat ảnh đôi dép hoặc đôi giày đang mang.",
    ],
    "umi": [
        "Khiến {t} gọi đúng món nước giống bạn.",
        "Khiến {t} cho bạn nếm thử nước của họ.",
        "Khiến {t} chụp ảnh ly nước của bạn.",
        "Khiến {t} đổi chỗ ngồi với bạn.",
        "Khiến {t} khen view của quán.",
        "Khiến {t} hỏi mật khẩu wifi của quán.",
        "Khiến {t} nhận xét nước của họ ngọt hay không ngọt.",
        "Khiến {t} chụp giùm bạn một tấm ảnh với biển phía sau.",
        "Khiến {t} kể đoàn của họ đi đường có gì vui.",
        "Khiến {t} chỉ cho mình bạn xem một tấm ảnh trong điện thoại của họ.",
    ],
    "lau": [
        "Khiến {t} gắp đồ ăn vào chén bạn.",
        "Khiến {t} rót nước cho bạn.",
        "Khiến {t} khen nồi lẩu hoặc món ăn bất kỳ hiện tại ngon.",
        "Khiến {t} cụng ly với bạn hai lần.",
        "Khiến {t} đưa giùm bạn chén nước chấm hoặc cái muỗng.",
        "Khiến {t} nói chữ “cay”.",
        "Khiến {t} đề nghị gọi thêm một món.",
        "Khiến {t} chụp ảnh nồi lẩu hoặc món ăn bất kỳ hiện tại.",
        "Khiến {t} hỏi bạn “ăn no chưa?”.",
        "Khiến {t} đưa giùm bạn khăn giấy.",
    ],
    "villa": [
        "Khiến {t} chụp ảnh bạn đang tạo dáng ở hồ bơi hoặc bãi biển.",
        "Khiến {t} giúp bạn mang một món đồ vào phòng.",
        "Khiến {t} nhận xét về phòng của bạn.",
        "Khiến {t} xuống nước cùng lúc với bạn.",
        "Khiến {t} hỏi mượn bạn một món đồ (kem chống nắng, khăn, dép…).",
        "Khiến {t} ngồi nghỉ cạnh bạn ít nhất 5 phút.",
        "Khiến {t} khen villa đẹp.",
        "Khiến {t} rủ bạn đi dạo biển.",
    ],
    "cho": [
        "Khiến {t} đoán giá một ký hải sản (ghẹ, mực, tôm…).",
        "Khiến {t} chọn giúp bạn một món ăn liên quan đến hải sản.",
        "Khiến {t} cầm giùm bạn món đồ bất kỳ ít nhất 5 phút.",
        "Khiến {t} giải thích cho bạn cách chọn hải sản tươi.",
        "Khiến {t} gửi vào nhóm chat một tấm ảnh có hải sản hoặc đồ ăn.",
        "Khiến {t} gợi ý một món nên có trong tiệc nướng tối nay.",
        "Khiến {t} nếm thử một món ăn vặt bất kỳ.",
        "Khiến {t} đọc to tên 1 người bất kỳ trong nhóm.",
    ],
    "tiec": [
        "Khiến {t} hát theo một câu của bài đang mở.",
        "Khiến {t} nâng ly và nói một câu chúc.",
        "Khiến {t} kể một chuyện vui trong chuyến đi.",
        "Khiến {t} lấy giùm bạn một loại nước uống bất kỳ.",
        "Khiến {t} chụp ảnh cùng bạn.",
        "Khiến {t} chấm thử nước sốt bạn đưa.",
    ],
    "any": [
        "Khiến {t} nói từ “tuyệt vời”.",
        "Khiến {t} đưa giùm bạn một món đồ (điện thoại, ly nước, khăn giấy…).",
        "Rủ {t} chụp chung một tấm selfie.",
        "Khiến {t} hỏi bạn mấy giờ rồi.",
        "Khiến {t} đập tay với bạn.",
        "Khiến {t} kể tên món ăn họ thích nhất.",
        "Khiến {t} nhắc đến tên một con vật.",
        "Khiến {t} cười thành tiếng vì câu chuyện của bạn.",
        "Khiến {t} sử dụng 1 món đồ bất kỳ của bạn.",
        "Khiến {t} đọc to một dòng chữ trên biển hiệu hoặc menu.",
        "Khiến {t} chỉ đường hoặc chỉ chỗ cho bạn.",
        "Khiến {t} hỏi bạn “thật không?”.",
        "Khiến {t} kể về chuyến du lịch gần nhất của họ.",
        "Khiến {t} nói tên một bài hát.",
        "Khiến {t} giơ ngón cái với bạn.",
        "Khiến {t} khen trang phục của bạn.",
        "Khiến {t} chọn giùm bạn giữa hai lựa chọn.",
        "Khiến {t} nói một câu tiếng Anh.",
        "Khiến {t} dạy bạn một mẹo nhỏ (chụp ảnh, xếp đồ…).",
        "Khiến {t} kể một kỷ niệm hồi đi học.",
        "Khiến {t} hỏi bạn “ăn gì chưa?”.",
        "Khiến {t} dùng từ “chill”.",
        "Khiến {t} gọi tên bạn ba lần trong 60 phút.",
        "Khiến {t} khen ảnh bạn chụp.",
    ],
}

# Nhiệm vụ riêng của Gián điệp: không có mục tiêu, tác động nhẹ lên cả nhóm
SABOTAGE = {
    "road": ["Khiến ít nhất 2 người gửi cùng một loại sticker vào nhóm chat.",
             "Khiến ít nhất 2 người nhắn trong nhóm chat rằng họ đói bụng."],
    "any": [
        "Khiến cả bàn (ít nhất 4 người) cụng ly cùng một lúc.",
        "Khiến 2 người khác đổi chỗ ngồi cho nhau.",
        "Khiến ít nhất 4 người cùng tạo trái tim trong một tấm ảnh.",
        "Khiến một người gọi đúng món mà bạn gợi ý.",
        "Khiến ít nhất 2 người hát cùng một đoạn bài hát bất kỳ (tối thiểu 2 câu hát).",
        "Rủ được 3 người cùng uống một loại nước.",
        "Khiến cả nhóm vỗ tay cùng một lúc.",
        "Khiến 2 người tranh luận vui về một chủ đề bạn khơi mào (ví dụ: biển Vũng Tàu hay Nha Trang đẹp hơn).",
        "Khiến ít nhất 3 người cùng nhìn lên trời một lúc.",
        "Khiến cả đoàn chụp một tấm ảnh nhóm theo kiểu bạn đề xuất.",
        "Khiến một người ngồi vào đúng chỗ bạn đã chọn sẵn cho họ.",
        "Khiến ít nhất 3 người nói “I love you!”.",
    ],
}

PTS = {"done": 2, "sab": 3, "catch": 2, "caught": -1, "wrong": -1, "vote_ok": 2, "spy_free": 5}


from markupsafe import Markup, escape


def now_vn():
    return datetime.now(VN_TZ).strftime("%H:%M %d/%m")


def m_get():
    return game_data.get("mission")


def m_add_agents(m):
    for name in game_data["players"]:
        if name not in m["agents"]:
            m["agents"][name] = {"role": "agent", "score": 0, "vote": None, "inbox": [], "used": []}


def m_derange(names):
    if len(names) < 2:
        return {}
    while True:
        perm = names[:]
        random.shuffle(perm)
        if all(a != b for a, b in zip(names, perm)):
            return dict(zip(names, perm))


def m_pick(pool, used, taken):
    fresh = [t for t in pool if t not in used and t not in taken]
    if fresh:
        return random.choice(fresh)
    fresh = [t for t in pool if t not in used]
    return random.choice(fresh or pool)


def m_open_stage(m, idx):
    m["stage"] = idx
    m_add_agents(m)
    key = MISSION_STAGES[idx]["key"]
    names = list(m["agents"].keys())
    targets = m_derange(names)
    taken = set()
    for name in names:
        a = m["agents"][name]
        pool = MISSIONS[key] if key == "road" else MISSIONS[key] + MISSIONS["any"]
        text = m_pick(pool, a["used"], taken)
        taken.add(text)
        a["used"].append(text)
        m["tasks"].append({"id": m["next_id"], "stage": idx, "owner": name, "target": targets.get(name),
                           "text": text, "kind": "mission", "status": "open", "note": "", "at": ""})
        m["next_id"] += 1
        if a["role"] == "spy":
            spool = SABOTAGE.get(key, []) + SABOTAGE["any"]
            stext = m_pick(spool, a["used"], set())
            a["used"].append(stext)
            m["tasks"].append({"id": m["next_id"], "stage": idx, "owner": name, "target": None,
                               "text": stext, "kind": "sab", "status": "open", "note": "", "at": ""})
            m["next_id"] += 1


def m_text(task):
    """Nội dung nhiệm vụ, tên mục tiêu in đậm."""
    parts = [escape(p) for p in task["text"].split("{t}")]
    who = Markup("<b>%s</b>") % (task["target"] or "")
    return Markup(who.join(parts))


def m_notify(m, name, text):
    a = m["agents"].get(name)
    if a is not None:
        a["inbox"].insert(0, {"at": now_vn(), "text": text})
        del a["inbox"][8:]


def m_spy_count(n):
    return 1 if n <= 8 else (2 if n <= 12 else 3)


MISSION_BODY = """
<div data-phase="M-{{ m.phase if m else 'NONE' }}" data-game="m{{ m.started if m else '' }}" data-body="">
<div class="brand"><span>HILtrip Điệp vụ</span><a href="/">Trò chơi</a></div>

{% if not m %}
  <h1>🎯 Điệp vụ Vũng Tàu</h1>
  <p class="sub">Chưa bắt đầu. Quản trò sẽ mở điệp vụ lúc xuất phát, màn hình sẽ tự chuyển.</p>

{% elif not me %}
  <h1>🎯 Điệp vụ Vũng Tàu</h1>
  <p class="sub">Vào trang chủ nhập tên trước đã. Nếu bạn đã chơi rồi, hãy nhập <b>đúng tên cũ</b> để giữ điểm.</p>
  <a class="btn" href="/">Nhập tên</a>

{% elif not a %}
  <h1>🎯 Điệp vụ Vũng Tàu</h1>
  <p class="sub">Bạn vào sau khi điệp vụ bắt đầu. Bạn sẽ có nhiệm vụ khi quản trò mở chặng tiếp theo.</p>

{% elif m.phase == 'REVEAL' %}
  <h1>🎉 Lật bài điệp vụ</h1>
  <p class="sub">{{ m.result }}</p>
  <div class="panel">
    <h2>Gián điệp</h2>
    <ul class="roster">{% for n in spies %}<li><span>🦹 {{ n }}</span><span class="{{ 'tag ok' if n in caught else 'tag dead' }}">{{ 'Bị bắt' if n in caught else 'Thoát' }}</span></li>{% endfor %}</ul>
  </div>
  <div class="panel">
    <h2>Giải thưởng</h2>
    <ul class="roster">{% for title, who in awards %}<li><span>{{ title }}</span><b>{{ who }}</b></li>{% endfor %}</ul>
  </div>
  <div class="panel">
    <h2>Bảng điểm</h2>
    <ol class="roster order">{% for n, ag in board %}<li class="{{ 'me' if n == me }}"><span>{{ av(n) }} {{ n }}{% if ag.role == 'spy' %} 🦹{% endif %}</span><b>{{ ag.score }} điểm</b></li>{% endfor %}</ol>
  </div>
  {% for st in stages %}{% set ts = done_by_stage[loop.index0] %}{% if ts %}
  <div class="panel">
    <h2>{{ st.icon }} {{ st.name }}</h2>
    <ul class="roster">{% for t in ts %}<li style="display:block">
      <div><b>{{ t.owner }}</b>{% if t.kind == 'sab' %} 🦹{% endif %}: {{ t.html }}</div>
      <div class="count">{% if t.status == 'done' %}✅ Hoàn thành{% else %}🚨 Bị {{ t.target }} bắt quả tang{% endif %}{% if t.note %}: “{{ t.note }}”{% endif %}</div>
    </li>{% endfor %}</ul>
  </div>
  {% endif %}{% endfor %}

{% else %}
  {% if m.phase == 'VOTE' %}
  <h1>🕵️ Truy tìm gián điệp</h1>
  <p class="sub">Trong đoàn có {{ nspy }} gián điệp. Bầu cho người bạn nghi nhất, đoán đúng +{{ pts.vote_ok }} điểm.</p>
  {% else %}
  <h1>{{ stage.icon }} {{ stage.name }}</h1>
  <p class="sub">Chặng {{ m.stage + 1 }}/{{ stages|length }}. {{ stage.tip }}</p>
  {% endif %}

  {% for msg in a.inbox[:2] %}<div class="note {{ 'bad' if '🚨' in msg.text else ('done' if '✅' in msg.text else '') }}" style="margin-bottom:10px"><span class="count">{{ msg.at }}</span> {{ msg.text }}</div>{% endfor %}

  <button type="button" class="card-role {{ 'wolf' if a.role == 'spy' else 'guard' }}">
    <span class="back">Chạm để xem vai và nhiệm vụ</span>
    <span class="face">
      <span class="icon">{{ '🦹' if a.role == 'spy' else '🕶️' }}</span>
      <span class="rname" style="display:block">{{ 'Gián điệp' if a.role == 'spy' else 'Điệp viên' }}</span>
      <span class="rhint">{% if a.role == 'spy' %}{% if mates %}Đồng bọn: {{ mates|join(', ') }}. {% endif %}Làm nhiệm vụ thật tự nhiên, đừng để ai nghi ngờ.{% else %}Làm nhiệm vụ thật tự nhiên. Cuối chuyến cả đoàn sẽ truy tìm gián điệp.{% endif %}
      <br>Điểm của bạn: <b>{{ a.score }}</b></span>
    </span>
  </button>

  {% if a.intel %}
  <div class="panel secret" style="margin-top:14px">
    <h2>🔎 Manh mối của bạn</h2>
    <ul class="roster">{% for n, r in a.intel.items() %}<li><span>{{ av(n) }} {{ n }}</span>{% if r == 'spy' %}<span class="tag dead">🦹 Gián điệp</span>{% else %}<span class="tag ok">🕶️ Điệp viên</span>{% endif %}</li>{% endfor %}</ul>
    <p class="count" style="margin:10px 0 0">Bạn biết được nhờ bắt quả tang. Giữ bí mật hay chia sẻ là tuỳ bạn.</p>
  </div>
  {% endif %}

  {% if m.phase == 'PLAY' %}
  <div class="secret">
    {% for t in open_tasks %}
    <div class="panel" style="margin-top:14px">
      <h2>{{ stages[t.stage].icon }} {{ 'Nhiệm vụ gián điệp' if t.kind == 'sab' else 'Nhiệm vụ' }}{% if t.stage != m.stage %} <span class="count">· từ chặng {{ stages[t.stage].name }}</span>{% endif %}</h2>
      <p style="margin:0 0 12px;font-size:18px">{{ t.html }}</p>
      <form method="POST" action="/mission/done">
        <input type="hidden" name="id" value="{{ t.id }}">
        <input type="text" name="note" maxlength="120" placeholder="Kể ngắn cách bạn làm (tuỳ chọn)" autocomplete="off">
        <button type="submit" class="{{ 'btn-wolf' if t.kind == 'sab' else 'btn-guard' }}">Đã hoàn thành (+{{ pts.sab if t.kind == 'sab' else pts.done }})</button>
      </form>
    </div>
    {% else %}
    <div class="note" style="margin-top:14px">Bạn đã xong hết nhiệm vụ. Chờ quản trò mở chặng tiếp theo nhé.</div>
    {% endfor %}
    {% if closed_tasks %}
    <div class="panel" style="margin-top:14px">
      <h2>Đã làm</h2>
      <ul class="roster">{% for t in closed_tasks %}<li style="display:block"><div>{{ t.html }}</div>
        <div class="count">{{ '✅ Hoàn thành' if t.status == 'done' else '🚨 Bị bắt quả tang' }}</div></li>{% endfor %}</ul>
    </div>
    {% endif %}
  </div>

  <div class="panel" style="margin-top:14px">
    <h2>Có người đang giăng bẫy bạn?</h2>
    <p class="sub" style="margin-bottom:12px">Chọn người bạn nghi đang làm nhiệm vụ nhắm vào bạn. Đúng +{{ pts.catch }} và biết được người đó thuộc phe nào; sai {{ pts.wrong }}.</p>
    <form method="POST" action="/mission/catch" data-confirm="Chắc chắn bắt quả tang người này? Sai sẽ bị trừ điểm.">
      <select name="who" required><option value="" disabled selected>Chọn người…</option>{% for n in others %}<option value="{{ n }}">{{ av(n) }} {{ n }}</option>{% endfor %}</select>
      <button type="submit" class="btn-wolf">🚨 Bắt quả tang</button>
    </form>
  </div>

  {% elif m.phase == 'VOTE' %}
  <div class="panel" style="margin-top:14px">
    <form method="POST" action="/mission/vote">
      <select name="who" required><option value="" disabled selected>Chọn người…</option>{% for n in others %}<option value="{{ n }}" {{ 'selected' if a.vote == n }}>{{ av(n) }} {{ n }}</option>{% endfor %}</select>
      <button type="submit" class="btn-wolf">{{ 'Đổi phiếu' if a.vote else 'Bầu người này' }}</button>
    </form>
    {% if a.vote %}<div class="note done" style="margin-top:12px">Bạn đã bầu: {{ a.vote }}. Có thể đổi đến khi quản trò chốt.</div>{% endif %}
  </div>
  <p class="sub" style="margin-top:14px">Đã bầu: {{ voted }} / {{ total }} người.</p>
  {% endif %}
{% endif %}
</div>
"""

MISSION_ADMIN = """
<div data-phase="MA-{{ m.phase if m else 'NONE' }}" data-body="">
<div class="brand"><span>HILtrip Quản trò</span><a href="/admin">Trò chơi</a></div>
<h1>🎯 Điệp vụ Vũng Tàu</h1>

{% if not m %}
  <p class="sub">Trò chơi kéo dài cả chuyến đi. Mỗi người nhận vai bí mật (Điệp viên hoặc Gián điệp) và mỗi chặng một nhiệm vụ: khiến một người khác làm một việc nhỏ mà không bị phát hiện. Tối 28/11 cả đoàn truy tìm gián điệp và lật bài.</p>
  <div class="panel">
    <h2>Người tham gia <span class="count">· {{ total }} người, {{ spy_n }} gián điệp</span></h2>
    <p class="sub" style="margin:0">Mọi người đã nhập tên ở trang chủ sẽ tham gia. Ai vào sau sẽ được thêm khi mở chặng tiếp theo, với vai Điệp viên.</p>
  </div>
  <div class="panel stack">
    <form action="/admin/mission/start" method="POST" data-confirm="Bắt đầu điệp vụ? Mọi người sẽ nhận vai và nhiệm vụ chặng 1.">
      <button type="submit" {{ 'disabled' if total < 4 }}>Bắt đầu điệp vụ (chặng 1: Khởi hành)</button></form>
    {% if total < 4 %}<p class="count" style="margin:0">Cần ít nhất 4 người.</p>{% endif %}
  </div>
  <div class="panel stack">
    <h2>🧪 Chơi thử một mình</h2>
    <p class="sub" style="margin:0">App thêm 5 người chơi ảo để bạn tự kiểm tra trọn vòng: nhận nhiệm vụ, làm nhiệm vụ, bắt quả tang, mở chặng, biểu quyết, lật bài. {% if not me %}<b>Nhập tên ở trang chủ trước</b> để bạn cũng có nhiệm vụ.{% else %}Bạn chơi với tên <b>{{ me }}</b>.{% endif %}</p>
    <form action="/admin/mission/demo" method="POST">
      <select name="role">
        <option value="">Vai của bạn: ngẫu nhiên</option>
        <option value="agent">Vai của bạn: Điệp viên</option>
        <option value="spy">Vai của bạn: Gián điệp</option>
      </select>
      <button type="submit" class="btn-ghost">Bắt đầu chơi thử</button>
    </form>
  </div>

{% else %}
  <p class="sub">Bạn cũng là người chơi: vai và nhiệm vụ của bạn nằm ở <a href="/mission" style="color:inherit">trang Điệp vụ</a>. Trang này không hiện nhiệm vụ, vai hay điểm của ai.</p>
  {% if m.demo %}
  <div class="panel stack">
    <h2>🧪 Đang chơi thử</h2>
    <p class="sub" style="margin:0">Người chơi ảo: {{ m.bots|join(', ') }}. Bấm nút dưới để họ hành động như người thật:
      {% if m.phase == 'PLAY' %}làm xong nhiệm vụ (trừ nhiệm vụ nhắm vào bạn, để bạn thử bắt quả tang) và bắt quả tang bạn nếu bạn đang giăng bẫy họ.{% elif m.phase == 'VOTE' %}bỏ phiếu ngẫu nhiên.{% else %}ván thử đã lật bài.{% endif %}</p>
    {% if m.phase != 'REVEAL' %}<form action="/admin/mission/demo_act" method="POST"><button type="submit" class="btn-ghost">🤖 Người chơi ảo hành động</button></form>{% endif %}
    <p class="count" style="margin:0">Thử xong bấm “Xoá điệp vụ”: người chơi ảo cũng bị xoá theo.</p>
  </div>
  {% endif %}
  <div class="panel">
    <h2>Lộ trình</h2>
    <ol class="roster order">{% for st in stages %}<li class="{{ 'me' if m.phase == 'PLAY' and loop.index0 == m.stage }}"><span>{{ st.icon }} {{ st.name }}</span>
      <span class="count">{% if m.phase == 'PLAY' and loop.index0 == m.stage %}đang chơi{% elif loop.index0 < m.stage or m.phase != 'PLAY' %}xong{% else %}{{ st.when }}{% endif %}</span></li>{% endfor %}</ol>
  </div>
  <div class="panel">
    <h2>Tiến độ</h2>
    <ul class="roster">
      <li><span>Người tham gia</span><b>{{ m.agents|length }}</b></li>
      <li><span>Nhiệm vụ đã hoàn thành</span><b>{{ n_done }}</b></li>
      <li><span>Lần bắt quả tang trúng</span><b>{{ n_caught }}</b></li>
      {% if m.phase == 'VOTE' %}<li><span>Đã bầu gián điệp</span><b>{{ voted }} / {{ m.agents|length }}</b></li>{% endif %}
    </ul>
  </div>
  <div class="panel stack">
    {% if m.phase == 'PLAY' %}
      {% if m.stage + 1 < stages|length %}{% set nx = stages[m.stage + 1] %}
      <form action="/admin/mission/next" method="POST" data-confirm="Mở chặng {{ nx.name }}? Mọi người nhận nhiệm vụ mới, nhiệm vụ cũ chưa làm vẫn giữ."><input type="hidden" name="stage" value="{{ m.stage }}">
        <button type="submit">Mở chặng tiếp: {{ nx.icon }} {{ nx.name }}</button></form>
      {% endif %}
      <form action="/admin/mission/vote" method="POST" data-confirm="Dừng nhận nhiệm vụ và mở biểu quyết truy tìm gián điệp?">
        <button type="submit" class="{{ 'btn-wolf' if m.stage + 1 == stages|length else 'btn-ghost' }}">🕵️ Mở biểu quyết truy tìm gián điệp</button></form>
    {% elif m.phase == 'VOTE' %}
      <form action="/admin/mission/reveal" method="POST" data-confirm="Chốt phiếu và lật bài cho cả đoàn?">
        <button type="submit" class="btn-wolf">🎉 Chốt phiếu và lật bài</button></form>
    {% else %}
      <a class="btn" href="/mission">Mở màn hình lật bài</a>
    {% endif %}
    <form action="/admin/mission/reset" method="POST" data-confirm="Xoá toàn bộ điệp vụ, mất hết điểm và nhiệm vụ?">
      <button type="submit" class="btn-ghost">Xoá điệp vụ</button></form>
  </div>
{% endif %}
</div>
"""


def m_reveal_ctx(m):
    agents = m["agents"]
    spies = [n for n, a in agents.items() if a["role"] == "spy"]
    board = sorted(agents.items(), key=lambda kv: -kv[1]["score"])
    done_by_stage = [[] for _ in MISSION_STAGES]
    victims = {}
    for t in m["tasks"]:
        if t["status"] in ("done", "caught"):
            done_by_stage[t["stage"]].append(dict(t, html=m_text(t)))
        if t["status"] == "done" and t["target"]:
            victims[t["target"]] = victims.get(t["target"], 0) + 1
    awards = []
    agents_only = [kv for kv in board if kv[1]["role"] == "agent"]
    if agents_only:
        awards.append(("🏆 Điệp viên xuất sắc", agents_only[0][0]))
    spy_board = [kv for kv in board if kv[1]["role"] == "spy"]
    if spy_board:
        awards.append(("🦹 Gián điệp lì nhất", spy_board[0][0]))
    if victims:
        top = max(victims.items(), key=lambda kv: kv[1])
        awards.append((f"😇 Bị “dụ” nhiều nhất ({top[1]} lần)", top[0]))
    catches = {}
    for t in m["tasks"]:
        if t["status"] == "caught":
            catches[t["target"]] = catches.get(t["target"], 0) + 1
    if catches:
        top = max(catches.items(), key=lambda kv: kv[1])
        awards.append((f"👀 Mắt thần ({top[1]} lần bắt quả tang)", top[0]))
    return dict(spies=spies, board=board, done_by_stage=done_by_stage, awards=awards,
                caught=m.get("caught", []))


@app.route('/mission')
def mission_page():
    m = m_get()
    me = session.get('username')
    a = m["agents"].get(me) if (m and me) else None
    ctx = dict(m=m, me=me, a=a, stages=MISSION_STAGES, pts=PTS)
    if m:
        ctx["stage"] = MISSION_STAGES[m["stage"]]
        ctx["others"] = [n for n in m["agents"] if n != me]
        ctx["nspy"] = sum(1 for x in m["agents"].values() if x["role"] == "spy")
        ctx["voted"] = sum(1 for x in m["agents"].values() if x["vote"])
        ctx["total"] = len(m["agents"])
        if a:
            mine = [dict(t, html=m_text(t)) for t in m["tasks"] if t["owner"] == me]
            ctx["open_tasks"] = [t for t in mine if t["status"] == "open"][::-1]
            ctx["closed_tasks"] = [t for t in mine if t["status"] != "open"][::-1]
            ctx["mates"] = [n for n, x in m["agents"].items() if x["role"] == "spy" and n != me] if a["role"] == "spy" else []
        if m["phase"] == "REVEAL":
            ctx.update(m_reveal_ctx(m))
    body = render_template_string(MISSION_BODY, **ctx)
    return page("HILtrip Điệp vụ", body, "", "/mission?partial=1")


@app.route('/mission/done', methods=['POST'])
def mission_done():
    me = session.get('username')
    with _lock:
        m = m_get()
        if m and m["phase"] == "PLAY" and me in m["agents"]:
            try:
                tid = int(request.form.get("id", -1))
            except ValueError:
                tid = -1
            t = next((x for x in m["tasks"] if x["id"] == tid and x["owner"] == me and x["status"] == "open"), None)
            if t:
                t.update(status="done", note=(request.form.get("note") or "").strip()[:120], at=now_vn())
                gain = PTS["sab"] if t["kind"] == "sab" else PTS["done"]
                m["agents"][me]["score"] += gain
                m_notify(m, me, f"✅ Đã ghi nhận nhiệm vụ, +{gain} điểm.")
                bump()
    return redirect(url_for('mission_page'))


@app.route('/mission/catch', methods=['POST'])
def mission_catch():
    me = session.get('username')
    who = request.form.get("who")
    with _lock:
        m = m_get()
        if m and m["phase"] == "PLAY" and me in m["agents"] and who in m["agents"] and who != me:
            hits = [t for t in m["tasks"] if t["owner"] == who and t["target"] == me
                    and t["kind"] == "mission" and t["status"] == "open"]
            if hits:
                t = hits[-1]
                t.update(status="caught", at=now_vn())
                m["agents"][me]["score"] += PTS["catch"]
                m["agents"][who]["score"] += PTS["caught"]
                side = "🦹 Gián điệp" if m["agents"][who]["role"] == "spy" else "🕶️ Điệp viên"
                m["agents"][me].setdefault("intel", {})[who] = m["agents"][who]["role"]
                m_notify(m, me, f"✅ Chính xác! {who} đang giăng bẫy bạn. +{PTS['catch']} điểm. Manh mối: {who} là {side}.")
                m_notify(m, who, f"🚨 {me} đã bắt quả tang bạn, nhiệm vụ “{t['text'].replace('{t}', me)}” bị huỷ. {PTS['caught']} điểm.")
            else:
                m["agents"][me]["score"] += PTS["wrong"]
                m_notify(m, me, f"🚨 Sai rồi, {who} không có nhiệm vụ nào đang nhắm vào bạn. {PTS['wrong']} điểm.")
            bump()
    return redirect(url_for('mission_page'))


@app.route('/mission/vote', methods=['POST'])
def mission_vote():
    me = session.get('username')
    who = request.form.get("who")
    with _lock:
        m = m_get()
        if m and m["phase"] == "VOTE" and me in m["agents"] and who in m["agents"] and who != me:
            m["agents"][me]["vote"] = who
            bump()
    return redirect(url_for('mission_page'))


@app.route('/admin/mission')
def mission_admin():
    m = m_get()
    ctx = dict(m=m, stages=MISSION_STAGES, total=len(game_data["players"]), me=session.get('username'),
               spy_n=m_spy_count(len(game_data["players"])))
    if m:
        ctx["n_done"] = sum(1 for t in m["tasks"] if t["status"] == "done")
        ctx["n_caught"] = sum(1 for t in m["tasks"] if t["status"] == "caught")
        ctx["voted"] = sum(1 for x in m["agents"].values() if x["vote"])
    body = render_template_string(MISSION_ADMIN, **ctx)
    return page("HILtrip Quản trò", body, "", "/admin/mission?partial=1")


def m_create(force_role=None, me=None):
    m = {"phase": "PLAY", "stage": 0, "agents": {}, "tasks": [], "next_id": 1,
         "started": now_vn(), "result": "", "caught": [], "demo": False, "bots": []}
    m_add_agents(m)
    names = list(m["agents"])
    k = m_spy_count(len(names))
    pool = [n for n in names if n != me] if force_role and me in names else names
    if force_role == "spy" and me in names:
        spies = [me] + random.sample(pool, k - 1)
    else:
        spies = random.sample(pool, k)
    for n in spies:
        m["agents"][n]["role"] = "spy"
    m_open_stage(m, 0)
    return m


@app.route('/admin/mission/start', methods=['POST'])
def mission_start():
    with _lock:
        if not m_get() and len(game_data["players"]) >= 4:
            game_data["mission"] = m_create()
            bump()
    return redirect(url_for('mission_admin'))


BOT_NAMES = ["🤖 Ảo An", "🤖 Ảo Bình", "🤖 Ảo Chi", "🤖 Ảo Dũng", "🤖 Ảo Em"]


@app.route('/admin/mission/demo', methods=['POST'])
def mission_demo():
    me = session.get('username')
    role = request.form.get("role") or None
    with _lock:
        if not m_get():
            for b in BOT_NAMES:
                if b not in game_data["players"]:
                    game_data["players"][b] = {"role": "Villager" if game_data["phase"] == "LOBBY" else None,
                                               "alive": True, "target": None, "acted": False, "vote": None}
            m = m_create(force_role=role if me in game_data["players"] else None, me=me)
            if role == "agent" and me in m["agents"] and m["agents"][me]["role"] == "spy":
                # đổi vai: bạn làm Điệp viên, một người ảo nhận vai Gián điệp thay
                swap = next(b for b in BOT_NAMES if m["agents"][b]["role"] == "agent")
                m["agents"][me]["role"], m["agents"][swap]["role"] = "agent", "spy"
                for t in m["tasks"]:
                    if t["kind"] == "sab" and t["owner"] == me:
                        t["owner"] = swap
            m["demo"], m["bots"] = True, list(BOT_NAMES)
            game_data["mission"] = m
            bump()
    return redirect(url_for('mission_admin'))


@app.route('/admin/mission/demo_act', methods=['POST'])
def mission_demo_act():
    with _lock:
        m = m_get()
        if not (m and m.get("demo")):
            return redirect(url_for('mission_admin'))
        bots = [b for b in m["bots"] if b in m["agents"]]
        humans = [n for n in m["agents"] if n not in bots]
        if m["phase"] == "PLAY":
            for t in m["tasks"]:
                if t["owner"] in bots and t["status"] == "open" and t["target"] not in humans:
                    t.update(status="done", note="(người chơi ảo)", at=now_vn())
                    m["agents"][t["owner"]]["score"] += PTS["sab"] if t["kind"] == "sab" else PTS["done"]
            # người ảo bắt quả tang người thật đang nhắm vào mình
            for t in m["tasks"]:
                if (t["owner"] in humans and t["target"] in bots and t["kind"] == "mission"
                        and t["status"] == "open" and random.random() < 0.5):
                    t.update(status="caught", at=now_vn())
                    m["agents"][t["target"]]["score"] += PTS["catch"]
                    m["agents"][t["owner"]]["score"] += PTS["caught"]
                    m_notify(m, t["owner"], f"🚨 {t['target']} đã bắt quả tang bạn, nhiệm vụ “{t['text'].replace('{t}', t['target'])}” bị huỷ. {PTS['caught']} điểm.")
        elif m["phase"] == "VOTE":
            for b in bots:
                m["agents"][b]["vote"] = random.choice([n for n in m["agents"] if n != b])
        bump()
    return redirect(url_for('mission_admin'))


@app.route('/admin/mission/next', methods=['POST'])
def mission_next():
    with _lock:
        m = m_get()
        sent = request.form.get("stage")
        same = sent is None or sent == str(m["stage"] if m else "")
        if m and same and m["phase"] == "PLAY" and m["stage"] + 1 < len(MISSION_STAGES):
            m_open_stage(m, m["stage"] + 1)
            for n in m["agents"]:
                m_notify(m, n, f"📍 Đã mở chặng {MISSION_STAGES[m['stage']]['name']}, bạn có nhiệm vụ mới.")
            bump()
    return redirect(url_for('mission_admin'))


@app.route('/admin/mission/vote', methods=['POST'])
def mission_open_vote():
    with _lock:
        m = m_get()
        if m and m["phase"] == "PLAY":
            m["phase"] = "VOTE"
            for x in m["agents"].values():
                x["vote"] = None
            bump()
    return redirect(url_for('mission_admin'))


@app.route('/admin/mission/reveal', methods=['POST'])
def mission_reveal():
    with _lock:
        m = m_get()
        if m and m["phase"] == "VOTE":
            agents = m["agents"]
            spies = [n for n, x in agents.items() if x["role"] == "spy"]
            counts = {}
            for x in agents.values():
                if x["vote"]:
                    counts[x["vote"]] = counts.get(x["vote"], 0) + 1
            # Gián điệp bị bắt nếu lọt vào nhóm nhiều phiếu nhất (số chỗ = số gián điệp)
            ranked = sorted(counts.values(), reverse=True)
            cut = ranked[len(spies) - 1] if len(ranked) >= len(spies) else (ranked[-1] if ranked else 1)
            caught = [s for s in spies if counts.get(s, 0) >= max(cut, 1)]
            for x in agents.values():
                if x["vote"] in spies:
                    x["score"] += PTS["vote_ok"]
            for s in spies:
                if s not in caught:
                    agents[s]["score"] += PTS["spy_free"]
            m["caught"] = caught
            if len(caught) == len(spies):
                m["result"] = "Cả đoàn đã lật mặt toàn bộ gián điệp!"
            elif caught:
                m["result"] = f"Bắt được {len(caught)}/{len(spies)} gián điệp. Kẻ còn lại đã cao chạy xa bay!"
            else:
                m["result"] = "Không bắt được gián điệp nào. Phe gián điệp thắng lớn!"
            m["phase"] = "REVEAL"
            bump()
    return redirect(url_for('mission_admin'))


@app.route('/admin/mission/reset', methods=['POST'])
def mission_reset():
    with _lock:
        m = m_get()
        if m and m.get("demo"):
            for b in m.get("bots", []):
                game_data["players"].pop(b, None)
        game_data["mission"] = None
        bump()
    return redirect(url_for('mission_admin'))


# ================================================================ THẬT HAY THÁCH (Truth or Dare)
# Bộ câu cho party dân văn phòng: vui, gần gũi; không hỏi lương, chuyện riêng tư, không nói xấu ai, không ép uống.
TOD_TRUTH = [
    "Thói quen kỳ lạ nhất của bạn mà ít ai biết là gì?",
    "Biệt danh hồi nhỏ của bạn là gì? Vì sao có biệt danh đó?",
    "Lần gần nhất bạn ngủ quên rồi đi làm trễ là khi nào?",
    "Món ăn bạn không thích nhưng vẫn phải ăn vì lịch sự là món gì?",
    "Bạn đã từng ngủ gật trong cuộc họp chưa? Kể đi!",
    "Nếu được đổi công việc với một người trong phòng một ngày, bạn chọn ai?",
    "Bài hát “ruột” của bạn mỗi lần đi karaoke là bài gì?",
    "Ứng dụng bạn mở nhiều nhất trên điện thoại là gì?",
    "Điều bạn tự hào nhất trong năm nay là gì?",
    "Nếu trúng số, việc đầu tiên bạn làm là gì?",
    "Kỷ niệm đáng nhớ nhất của bạn với đồng nghiệp là gì?",
    "Lần sau đi du lịch, bạn muốn rủ ai trong phòng đi cùng nhất?",
    "Thần tượng hồi nhỏ của bạn là ai?",
    "Món bạn nấu ngon nhất là món gì?",
    "Bạn sợ con vật nào nhất?",
    "Tật xấu bạn muốn bỏ nhất là gì?",
    "Nếu được làm sếp một ngày, điều đầu tiên bạn thay đổi là gì?",
    "Bạn đã từng gửi nhầm tin nhắn cho ai chưa? Chuyện gì xảy ra?",
    "Ấn tượng đầu tiên của bạn về người đang ngồi bên phải là gì?",
    "Bộ phim bạn xem đi xem lại nhiều nhất là phim gì?",
    "Hồi nhỏ bạn mơ ước làm nghề gì?",
    "Khoảnh khắc buồn cười nhất của chuyến đi này đến giờ là gì?",
    "Theo bạn, ai trong phòng hát hay nhất?",
    "Theo bạn, ai trong phòng sẽ dậy muộn nhất sáng mai?",
    "Bạn từng chạy deadline đến mấy giờ sáng?",
    "Món đồ vô dụng nhất bạn từng mua online là gì?",
    "Kỹ năng bí mật mà ít ai biết của bạn là gì?",
    "Nếu được chọn một siêu năng lực, bạn chọn gì?",
    "Câu nói cửa miệng của bạn là gì?",
    "Việc đầu tiên bạn làm mỗi sáng khi tới chỗ làm là gì?",
    "Nếu được nghỉ phép một tháng, bạn sẽ đi đâu?",
    "Lần “quê” nhất của bạn ở chỗ làm là khi nào?",
    "Bạn thích biển hay núi hơn? Vì sao?",
    "Bộ phim nào từng làm bạn khóc?",
    "Ai trong phòng là người bạn thấy chăm chỉ nhất?",
    "Ai trong phòng có gu ăn mặc đẹp nhất theo bạn?",
    "Bạn từng giả vờ đang bận để né một việc gì chưa?",
    "Món ăn bạn có thể ăn mỗi ngày không chán là gì?",
    "Bạn là “cú đêm” hay “chim sớm”?",
    "Lần gần nhất bạn cười đến chảy nước mắt là vì chuyện gì?",
    "Bạn đã từng quên tên một người ngay khi họ vừa giới thiệu chưa?",
    "Nếu phải sống trên đảo hoang và chỉ mang theo 3 thứ, bạn mang gì?",
    "Bạn thường hát trong nhà tắm bài gì?",
    "Điều bạn thích nhất ở chỗ làm hiện tại là gì?",
    "Nếu được quay lại tuổi 18, bạn muốn nhắn nhủ gì với bản thân?",
    "Ai là người đầu tiên bạn nhắn tin khi có chuyện vui?",
    "Bạn từng làm rơi điện thoại vào đâu “khó đỡ” nhất?",
    "Có món gì bạn từng nấu hỏng thảm hại chưa?",
    "Trò chơi điện thoại bạn từng nghiện nhất là trò gì?",
    "Một điều bạn muốn học trong năm tới là gì?",
]

TOD_DARE = [
    "Hát to một đoạn điệp khúc bài hát bất kỳ.",
    "Bắt chước cách nói chuyện của một người trong phòng để mọi người đoán là ai.",
    "Nhảy một điệu bất kỳ trong 15 giây.",
    "Khen mỗi người trong phòng một câu thật lòng.",
    "Làm 10 cái squat, vừa làm vừa đếm to.",
    "Đứng một chân trong 30 giây.",
    "Kể một câu chuyện cười.",
    "Diễn tả một con vật bằng hành động, không nói, để mọi người đoán.",
    "Chụp một tấm selfie mặt thật hài rồi gửi vào nhóm chat chuyến đi.",
    "Nói liên tục 30 giây về món ăn yêu thích, không được ngập ngừng.",
    "Giới thiệu người bên phải như MC giới thiệu ngôi sao lên sân khấu.",
    "Nói câu “Chào mừng đến Vũng Tàu” bằng giọng của một vùng miền khác.",
    "Tạo 3 dáng người mẫu để người bên cạnh chụp ảnh.",
    "Làm hướng dẫn viên du lịch, giới thiệu Vũng Tàu trong 30 giây.",
    "Cười thật to trong 10 giây.",
    "Hát “Happy Birthday” tặng người bên trái, dù hôm nay không phải sinh nhật họ.",
    "Để cả phòng đặt cho bạn một biệt danh, dùng đến hết buổi tối.",
    "Nói chuyện như robot cho đến lượt tiếp theo.",
    "Bắt chước tiếng con vật do cả phòng chọn.",
    "Làm 3 động tác thể dục giữa giờ, cả phòng làm theo.",
    "Kể lại một bộ phim trong 3 câu để mọi người đoán tên phim.",
    "Dạy cả phòng một động tác nhảy đơn giản.",
    "Làm ít nhất một người bật cười trong 30 giây.",
    "Giữ mặt nghiêm túc 20 giây trong khi mọi người chọc cười.",
    "Lấy nước mời từng người trong phòng.",
    "Đọc rap tên tất cả mọi người trong phòng.",
    "Gửi một sticker dễ thương nhất bạn có vào nhóm chat chuyến đi.",
    "Kể 3 điều bạn biết ơn trong năm nay.",
    "Đoán tên bài hát người bên trái đang ngân nga (họ được chọn bài).",
    "Thuyết trình 30 giây vì sao bạn xứng đáng được “nhân viên của chuyến đi”.",
    "Nói “Vũng Tàu ơi, tui tới rồi!” thật to.",
    "Làm 5 cái chống đẩy (hoặc 10 cái nhảy dang tay).",
    "Vừa vỗ tay vừa hát một bài thiếu nhi.",
    "Bắt chước một quảng cáo truyền hình quen thuộc.",
    "Đi một vòng quanh phòng theo kiểu người mẫu catwalk.",
    "Kể một kỷ niệm vui với người ngồi đối diện.",
    "Chọn một người và cùng nhau tạo dáng chụp một tấm ảnh thật “ngầu”.",
    "Nói một câu tiếng Anh với giọng thật “Tây”.",
    "Đặt cho người bên trái một biệt danh dễ thương và giải thích vì sao.",
    "Uống một ly nước lọc một hơi (nước lọc thôi nhé!).",
    "Cầm điện thoại giả làm phóng viên phỏng vấn nhanh 2 người.",
    "Vẽ chân dung người bên phải trong 1 phút rồi cho cả phòng xem.",
    "Đọc to tin nhắn ngắn gần nhất bạn gửi trong nhóm chat chuyến đi.",
    "Hát một câu bằng giọng thật trầm, rồi một câu bằng giọng thật cao.",
    "Đổi chỗ ngồi với người bạn chọn cho đến hết vòng.",
    "Kể tên 10 món hải sản trong 20 giây.",
    "Diễn lại cảnh bạn ngủ quên dậy trễ đi làm.",
    "Làm động tác chào cờ thật nghiêm trang trong 10 giây.",
    "Khen bản thân 3 câu thật tự tin.",
    "Dùng 3 từ để mô tả từng người trong phòng.",
]

TOD_PENALTY = [
    "Uống cạn một ly (bia hay nước ngọt tuỳ bạn) và hô “Dô!” thật to.",
    "Bị cả phòng hỏi liền 3 câu Sự thật, không được né câu nào.",
    "Lần sau bị quay trúng, bạn bắt buộc chọn Thử thách.",
    "Đổi ảnh đại diện Zalo thành ảnh do cả phòng chọn, giữ trong 1 tiếng.",
    "Đăng story “Tui thua Thật hay Thách ở Vũng Tàu 🥲” và giữ đến sáng mai.",
    "Để người bên phải soạn một tin nhắn gửi vào nhóm chat chuyến đi bằng điện thoại của bạn (cả phòng duyệt trước khi gửi).",
    "Làm phục vụ: rót nước cho cả bàn đến hết vòng sau.",
    "Hát một câu tình ca thật “diễn sâu”, có động tác tay.",
    "Nhảy 20 giây theo bài cả phòng chọn, không được dừng.",
    "Đi kiểu vịt một vòng quanh bàn, vừa đi vừa kêu “quạc quạc”.",
    "Cắn một miếng chanh mà không được nhăn mặt.",
    "Dán giấy note do cả phòng viết lên trán, giữ đến hết lượt sau.",
    "Gọi điện cho một người trong đoàn do cả phòng chọn và nói: “Cứu tui với, tui thua rồi!”",
    "Phát biểu cảm nghĩ 30 giây như vừa nhận giải Oscar.",
    "Làm 15 cái squat, mỗi cái hô tên một người trong phòng.",
    "Giữ tư thế plank 30 giây trong khi cả phòng đếm.",
    "Bắt chước điệu bộ của một người do cả phòng chọn, đến khi có người đoán đúng.",
    "Gửi một tin nhắn thoại hát “Happy Birthday” vào nhóm chat chuyến đi.",
    "Cho cả phòng xem tấm selfie “xấu” nhất bạn còn giữ trong máy (bạn tự chọn).",
    "Đến hết lượt sau, câu nào bạn nói cũng phải kết thúc bằng “nha người đẹp”.",
]

TOD_PTS = {"truth": 1, "dare": 2, "skip": 0}

WHEEL_COLORS = ["#2b3466", "#3a2f5e", "#24485a", "#4a3a2a", "#2f4a3a", "#4a2f3f"]


def is_admin():
    return (not ADMIN_KEY) or bool(session.get("is_admin"))


def tod_new():
    return {"phase": "SPIN", "spin_id": 0, "picked": None, "deg": 0, "wheel": [],
            "kind": None, "card": "", "swapped": False, "penalty": "",
            "used": {"truth": [], "dare": []}, "custom": {"truth": [], "dare": []},
            "stats": {}, "history": []}


def tod_deck(t, kind):
    base = TOD_TRUTH if kind == "truth" else TOD_DARE
    return t["custom"][kind] + base


def tod_draw(t, kind, avoid=None):
    deck = tod_deck(t, kind)
    fresh = [q for q in deck if q not in t["used"][kind] and q != avoid]
    if not fresh:                      # dùng hết bộ câu thì xào lại
        t["used"][kind] = []
        fresh = [q for q in deck if q != avoid] or deck
    # câu tự thêm được ưu tiên bốc trước
    custom = [q for q in fresh if q in t["custom"][kind]]
    q = random.choice(custom or fresh)
    t["used"][kind].append(q)
    return q


def tod_stat(t, name):
    return t["stats"].setdefault(name, {"truth": 0, "dare": 0, "skip": 0, "picked": 0, "pts": 0})


def tod_spin(t, forced=None):
    names = list(game_data["players"].keys())
    if not names:
        return
    if forced in names:
        pick = forced
    else:
        least = min(tod_stat(t, n)["picked"] for n in names)
        pool = [n for n in names if tod_stat(t, n)["picked"] == least]
        if len(pool) > 1 and t["picked"] in pool:
            pool.remove(t["picked"])           # không quay trúng 2 lần liền
        pick = random.choice(pool)
    seg = 360 / len(names)
    idx = names.index(pick)
    jitter = random.uniform(-0.3, 0.3) * seg
    t.update(phase="CHOOSE", picked=pick, wheel=names, kind=None, card="", swapped=False, penalty="",
             spin_id=t["spin_id"] + 1, deg=round(360 * 6 + (360 - (idx + 0.5) * seg) + jitter, 1))
    tod_stat(t, pick)["picked"] += 1


def tod_wheel(t):
    names = t["wheel"] if t["phase"] != "SPIN" or t["wheel"] else []
    names = names or list(game_data["players"].keys())
    n = max(len(names), 1)
    seg = 360 / n
    stops = ", ".join(f"{WHEEL_COLORS[i % len(WHEEL_COLORS)]} {i * seg:.2f}deg {(i + 1) * seg:.2f}deg" for i in range(n))
    items = [{"name": nm, "icon": av(nm) or "🙂", "a": round((i + 0.5) * seg, 2)} for i, nm in enumerate(names)]
    return {"bg": f"conic-gradient({stops})", "items": items}


TOD_BODY = """
<div data-phase="TOD-{{ t.phase }}" data-game="{{ game_id }}" data-body="">
<div class="brand"><span>HILtrip Thật hay Thách</span>
  <span>{% if mission_on and not admin %}<a href="/mission">🎯 Điệp vụ</a> {% endif %}{% if admin %}<a href="/">Người chơi</a>{% else %}<a href="/logout">Thoát</a>{% endif %}</span></div>

{% if t.phase == 'END' %}
  <h1>🏆 Bảng vàng Thật hay Thách</h1>
  <p class="sub">Cảm ơn mọi người đã chơi hết mình!</p>
  <div class="panel">
    <ul class="roster">{% for title, who in awards %}<li><span>{{ title }}</span><b>{{ av(who) }} {{ who }}</b></li>{% endfor %}</ul>
  </div>
  <div class="panel">
    <h2>Điểm <span class="count">· sự thật +{{ pts.truth }}, thử thách +{{ pts.dare }}</span></h2>
    <ol class="roster order">{% for n, s in board %}<li><span>{{ av(n) }} {{ n }}</span><span class="count">{{ s.truth }} thật · {{ s.dare }} thách · {{ s.skip }} né · <b>{{ s.pts }}</b></span></li>{% endfor %}</ol>
  </div>
{% else %}
  <div class="wheel-wrap" aria-hidden="true">
    <div class="pointer"></div>
    <div class="wheel" data-spin="{{ t.spin_id }}" data-deg="{{ t.deg }}" style="background:{{ wheel.bg }}">
      {% for it in wheel['items'] %}<span class="wi" style="transform:translate(-50%,-50%) rotate({{ it.a }}deg) translateY(-112px) rotate(-{{ it.a }}deg)">{{ it.icon }}</span>{% endfor %}
    </div>
    <div class="hub">🎡</div>
  </div>

  {% if t.phase == 'SPIN' %}
    <h1 class="center">Ai sẽ là người tiếp theo?</h1>
    {% if t.history %}{% set h = t.history[0] %}<p class="sub center">Lượt trước: {{ av(h.name) }} {{ h.name }} {{ h.result }}.</p>{% endif %}
    <form method="POST" action="/tod/spin" data-ajax><button type="submit" class="btn-spin">🎡 Quay!</button></form>
    {% if admin %}
    <form method="POST" action="/admin/tod/spin" class="panel" style="margin-top:14px" data-ajax>
      <h2>Chỉ định người chơi (không quay ngẫu nhiên)</h2>
      <select name="who" required><option value="" disabled selected>Chọn người…</option>{% for n in names %}<option value="{{ n }}">{{ av(n) }} {{ n }}</option>{% endfor %}</select>
      <button type="submit" class="btn-ghost">Chọn người này</button>
    </form>
    {% endif %}
  {% else %}
    <div class="after-spin">
      <h1 class="center">{{ av(t.picked) }} {{ t.picked }}{% if t.picked == me %} (bạn!){% endif %}</h1>
      {% if t.phase == 'CHOOSE' %}
        {% if t.picked == me or admin %}
        <p class="sub center">{{ 'Bạn chọn gì?' if t.picked == me else 'Chọn hộ nếu người chơi không cầm điện thoại:' }}</p>
        <div class="duo">
          <form method="POST" action="/tod/choose" data-ajax><input type="hidden" name="kind" value="truth"><button type="submit" class="btn-truth">🤫 Sự thật</button></form>
          <form method="POST" action="/tod/choose" data-ajax><input type="hidden" name="kind" value="dare"><button type="submit" class="btn-dare">🔥 Thử thách</button></form>
        </div>
        {% else %}
        <p class="sub center">đang chọn Sự thật hay Thử thách…</p>
        {% endif %}
      {% else %}
        <div class="tod-card {{ t.kind }}{{ ' pen' if t.phase == 'PENALTY' }}">
          <div class="kind">{% if t.phase == 'PENALTY' %}🙈 Hình phạt{% elif t.kind == 'truth' %}🤫 Sự thật{% else %}🔥 Thử thách{% endif %}</div>
          <p>{{ t.penalty if t.phase == 'PENALTY' else t.card }}</p>
        </div>
        {% if t.picked == me or admin %}
          {% if t.phase == 'CARD' %}
          <form method="POST" action="/tod/done" data-ajax><button type="submit" class="btn-ok">✅ Xong (+{{ pts[t.kind] }})</button></form>
          <div class="duo" style="margin-top:10px">
            <form method="POST" action="/tod/swap" data-ajax><button type="submit" class="btn-ghost" {{ 'disabled' if t.swapped }}>🔁 {{ 'Đã đổi câu' if t.swapped else 'Đổi câu khác' }}</button></form>
            <form method="POST" action="/tod/skip" data-ajax><button type="submit" class="btn-ghost">🙈 Bỏ qua</button></form>
          </div>
          {% else %}
          <form method="POST" action="/tod/done" data-ajax><button type="submit" class="btn-ok">Đã chịu phạt, quay tiếp</button></form>
          {% endif %}
        {% else %}
          <p class="sub center" style="margin-top:12px">{{ 'Chờ ' ~ t.picked ~ ' chịu phạt…' if t.phase == 'PENALTY' else 'Cả phòng cùng xem ' ~ t.picked ~ ' làm nhé!' }}</p>
        {% endif %}
      {% endif %}
    </div>
  {% endif %}

  {% if board %}
  <div class="panel" style="margin-top:18px">
    <h2>Bảng điểm</h2>
    <ol class="roster order">{% for n, s in board[:5] %}<li class="{{ 'me' if n == me }}"><span>{{ av(n) }} {{ n }}</span><b>{{ s.pts }}</b></li>{% endfor %}</ol>
  </div>
  {% endif %}

  {% if admin %}
  <form method="POST" action="/admin/tod/custom" class="panel stack" style="margin-top:14px">
    <h2>Thêm câu riêng của nhóm <span class="count">· đã thêm {{ t.custom.truth|length }} thật, {{ t.custom.dare|length }} thách</span></h2>
    <select name="kind" required><option value="truth">🤫 Sự thật</option><option value="dare">🔥 Thử thách</option></select>
    <input type="text" name="text" maxlength="160" required placeholder="Ví dụ: Kể lại vụ lạc đường đi chợ Xóm Lưới" autocomplete="off">
    <button type="submit" class="btn-ghost">Thêm vào bộ câu (được bốc trước)</button>
  </form>
  {% endif %}
{% endif %}

{% if admin %}
<div class="panel stack" style="margin-top:14px">
  {% if t.phase == 'END' %}
  <form action="/admin/tod/start" method="POST"><input type="hidden" name="gid" value="{{ gid() }}"><button type="submit" class="btn-seer">🎡 Chơi lại Thật hay Thách</button></form>
  {% else %}
  <form action="/admin/tod/end" method="POST" data-confirm="Kết thúc và xem bảng vàng?"><button type="submit" class="btn-ghost">🏆 Kết thúc, xem bảng vàng</button></form>
  {% endif %}
  <form action="/admin/lobby" method="POST" data-confirm="Về phòng chờ để chọn trò khác?"><button type="submit" class="btn-ghost">Về phòng chờ, chọn trò khác</button></form>
</div>
{% endif %}
</div>
"""


def tod_board(t):
    return sorted(((n, s) for n, s in t["stats"].items() if s["picked"]), key=lambda kv: -kv[1]["pts"])


def tod_awards(t):
    st = [(n, s) for n, s in t["stats"].items() if s["picked"]]
    out = []
    for key, title in (("dare", "🔥 Dũng cảm nhất"), ("truth", "🤫 Thật thà nhất"), ("skip", "🙈 Hay né nhất")):
        best = max(st, key=lambda kv: kv[1][key], default=None)
        if best and best[1][key] > 0:
            out.append((f"{title} ({best[1][key]} lần)", best[0]))
    return out


def tod_render(admin=False):
    t = game_data["tod"]
    ctx = dict(t=t, me=session.get('username'), admin=admin, game_id=game_data["game_id"],
               wheel=tod_wheel(t), pts=TOD_PTS, board=tod_board(t), names=list(game_data["players"]),
               mission_on=bool(game_data.get("mission")), awards=tod_awards(t) if t["phase"] == "END" else [])
    body = render_template_string(TOD_BODY, **ctx)
    return page("HILtrip Thật hay Thách", body, "", "/admin?partial=1" if admin else "/?partial=1")


def tod_can_act(t):
    return session.get('username') == t["picked"] or is_admin()


@app.route('/admin/tod/start', methods=['POST'])
def tod_start():
    with _lock:
        if not fresh_gid():
            return redirect(url_for('admin_panel'))
        if game_data["players"]:
            game_data["tod"] = tod_new()
            game_data["phase"] = "TOD"
            game_data["game_id"] += 1
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/tod/spin', methods=['POST'])
def tod_spin_route():
    with _lock:
        t = game_data.get("tod")
        if (game_data["phase"] == "TOD" and t and t["phase"] == "SPIN"
                and (session.get('username') in game_data["players"] or is_admin())):
            tod_spin(t)
            bump()
    return redirect(url_for('index'))


@app.route('/admin/tod/spin', methods=['POST'])
def tod_force_spin():
    with _lock:
        t = game_data.get("tod")
        if game_data["phase"] == "TOD" and t and t["phase"] == "SPIN":
            tod_spin(t, forced=request.form.get("who"))
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/tod/choose', methods=['POST'])
def tod_choose():
    kind = request.form.get("kind")
    with _lock:
        t = game_data.get("tod")
        if t and game_data["phase"] == "TOD" and t["phase"] == "CHOOSE" and kind in ("truth", "dare") and tod_can_act(t):
            t.update(phase="CARD", kind=kind, card=tod_draw(t, kind))
            bump()
    return redirect(url_for('index'))


@app.route('/tod/swap', methods=['POST'])
def tod_swap():
    with _lock:
        t = game_data.get("tod")
        if t and game_data["phase"] == "TOD" and t["phase"] == "CARD" and not t["swapped"] and tod_can_act(t):
            t.update(card=tod_draw(t, t["kind"], avoid=t["card"]), swapped=True)
            bump()
    return redirect(url_for('index'))


@app.route('/tod/skip', methods=['POST'])
def tod_skip():
    with _lock:
        t = game_data.get("tod")
        if t and game_data["phase"] == "TOD" and t["phase"] == "CARD" and tod_can_act(t):
            s = tod_stat(t, t["picked"])
            s["skip"] += 1
            t["history"].insert(0, {"name": t["picked"], "kind": t["kind"], "text": t["card"], "result": "đã né và chịu phạt"})
            del t["history"][10:]
            t.update(phase="PENALTY", penalty=random.choice(TOD_PENALTY))
            bump()
    return redirect(url_for('index'))


@app.route('/tod/done', methods=['POST'])
def tod_done():
    with _lock:
        t = game_data.get("tod")
        if t and game_data["phase"] == "TOD" and t["phase"] in ("CARD", "PENALTY") and tod_can_act(t):
            if t["phase"] == "CARD":
                s = tod_stat(t, t["picked"])
                s[t["kind"]] += 1
                s["pts"] += TOD_PTS[t["kind"]]
                label = "nói thật" if t["kind"] == "truth" else "hoàn thành thử thách"
                t["history"].insert(0, {"name": t["picked"], "kind": t["kind"], "text": t["card"], "result": label})
                del t["history"][10:]
            t.update(phase="SPIN", kind=None, card="", penalty="")
            bump()
    return redirect(url_for('index'))


@app.route('/admin/tod/custom', methods=['POST'])
def tod_custom():
    kind = request.form.get("kind")
    text = (request.form.get("text") or "").strip()[:160]
    with _lock:
        t = game_data.get("tod")
        if t and kind in ("truth", "dare") and text and text not in t["custom"][kind]:
            t["custom"][kind].append(text)
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/tod/end', methods=['POST'])
def tod_end():
    with _lock:
        t = game_data.get("tod")
        if t and game_data["phase"] == "TOD":
            t["phase"] = "END"
            bump()
    return redirect(url_for('admin_panel'))


@app.route('/admin/lobby', methods=['POST'])
def admin_lobby():
    with _lock:
        game_data["phase"] = "LOBBY"
        for i in game_data["players"].values():
            i.update(role="Villager", alive=True, target=None, acted=False, vote=None)
        bump()
    return redirect(url_for('admin_panel'))


# ================================================================ RANDOM PICKER (chạy trên máy người bấm)
PICKER_BODY = r"""
<div data-static="1" data-body="">
<div class="brand"><span>HILtrip Random</span><span><a href="{{ back }}">← Quay lại</a></span></div>

<div class="ptabs" role="tablist">
  <button type="button" data-tab="wheel" class="on">🎡 Vòng quay</button>
  <button type="button" data-tab="team">👥 Chia team</button>
  <button type="button" data-tab="draw">🎟️ Bốc thăm</button>
  <button type="button" data-tab="dice">🎲 Xúc xắc</button>
  <button type="button" data-tab="coin">🪙 Đồng xu</button>
</div>

<details class="panel plist" id="plist">
  <summary><b>Danh sách</b> <span class="count" id="pcount"></span> <span class="count">· chạm để sửa</span></summary>
  <div class="chips" id="chips"></div>
  <div class="addrow">
    <input type="text" id="padd" maxlength="40" placeholder="Thêm tên hoặc món, ví dụ: Lẩu" autocomplete="off">
    <button type="button" id="paddbtn" class="mini">Thêm</button>
  </div>
  <div class="addrow">
    <button type="button" id="pall" class="btn-ghost mini">Lấy cả phòng</button>
    <button type="button" id="pclear" class="btn-ghost mini">Xoá hết</button>
  </div>
  <p class="count" style="margin:8px 0 0">Chạm vào tên để bỏ ra. Danh sách dùng chung cho cả 5 công cụ, chỉ lưu trên máy bạn.</p>
</details>

<section data-pane="wheel">
  <div class="wbox">
    <div class="wpointer"></div>
    <canvas id="wheel" width="320" height="320" aria-label="Vòng quay"></canvas>
    <button type="button" id="spin" class="wspin">Quay!</button>
  </div>
  <div id="wres" class="result" hidden></div>
  <label class="check" style="justify-content:center"><input type="checkbox" id="wremove"> Trúng rồi thì bỏ khỏi vòng quay</label>
</section>

<section data-pane="team" hidden>
  <div class="panel stack">
    <h2>Chọn nhanh</h2>
    <div class="grid3">
      <button type="button" class="btn-ghost" data-size="2">2 vs 2</button>
      <button type="button" class="btn-ghost" data-size="3">3 vs 3</button>
      <button type="button" class="btn-ghost" data-size="4">4 vs 4</button>
      <button type="button" class="btn-ghost" data-size="5">5 vs 5</button>
      <button type="button" class="btn-ghost" data-teams="2">Chia 2 đội</button>
      <button type="button" class="btn-ghost" data-teams="3">Chia 3 đội</button>
      <button type="button" class="btn-ghost" data-teams="4">Chia 4 đội</button>
    </div>
    <p class="count" style="margin:0">“2 vs 2”: mỗi đội đúng 2 người, ai dư thì vào nhóm chờ. “Chia 2 đội”: chia hết mọi người, các đội hơn kém nhau tối đa 1 người.</p>
  </div>
  <div id="teams"></div>
</section>

<section data-pane="draw" hidden>
  <div class="panel stack">
    <h2>Bốc mấy người?</h2>
    <div class="addrow"><button type="button" class="btn-ghost mini" id="dminus">−</button><b id="dn" class="bignum">1</b><button type="button" class="btn-ghost mini" id="dplus">+</button></div>
    <button type="button" id="drawbtn">🎟️ Bốc thăm</button>
  </div>
  <ol id="draws" class="draws"></ol>
</section>

<section data-pane="dice" hidden>
  <div class="panel stack center">
    <div class="addrow" style="justify-content:center"><button type="button" class="btn-ghost mini" data-dice="1">1 viên</button><button type="button" class="btn-ghost mini" data-dice="2">2 viên</button><button type="button" class="btn-ghost mini" data-dice="3">3 viên</button></div>
    <div id="dice" class="dice">⚀</div>
    <p id="dsum" class="sub center" style="margin:0"></p>
    <button type="button" id="rollbtn">🎲 Tung xúc xắc</button>
  </div>
</section>

<section data-pane="coin" hidden>
  <div class="panel stack center">
    <div id="coin" class="coin">🪙</div>
    <p id="coinres" class="sub center" style="margin:0"></p>
    <button type="button" id="flipbtn">🪙 Tung đồng xu</button>
    <p id="coinhist" class="count center" style="margin:0"></p>
  </div>
</section>

<script>
(function(){
  var ROOM = {{ room|tojson }};          // [{name, icon}]
  var KEY = 'hiltrip_picker_v1';
  var COLORS = ['#a78bfa','#f2b04e','#5fae5f','#5aa9e6','#ec6b6b','#6cc9c0'];
  function store(get, val){ try { if (get) return JSON.parse(localStorage.getItem(KEY) || 'null'); localStorage.setItem(KEY, JSON.stringify(val)); } catch(e){ return null; } }
  function rnd(n){ var a = new Uint32Array(1); (window.crypto || window.msCrypto).getRandomValues(a); return a[0] % n; }
  function shuffle(arr){ var a = arr.slice(); for (var i = a.length - 1; i > 0; i--){ var j = rnd(i + 1); var t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function iconOf(n){ for (var i = 0; i < ROOM.length; i++) if (ROOM[i].name === n) return ROOM[i].icon; return ''; }
  function label(n){ var ic = iconOf(n); return (ic ? ic + ' ' : '') + n; }

  var saved = store(true);
  var items = (saved && saved.items && saved.items.length) ? saved.items : ROOM.map(function(p){ return p.name; });
  function save(){ store(false, {items: items}); }

  // ---------- danh sách
  var chips = document.getElementById('chips'), pcount = document.getElementById('pcount');
  function renderList(){
    chips.innerHTML = items.map(function(n, i){ return '<button type="button" class="chip" data-i="' + i + '">' + esc(label(n)) + ' ✕</button>'; }).join('');
    pcount.textContent = '· ' + items.length + ' mục';
    drawWheel(); clampDraw();
  }
  chips.onclick = function(e){ var b = e.target.closest('.chip'); if (!b) return; items.splice(+b.dataset.i, 1); save(); renderList(); };
  function addItem(){ var inp = document.getElementById('padd'); var v = inp.value.trim().slice(0, 40); if (v && items.indexOf(v) < 0){ items.push(v); save(); renderList(); } inp.value = ''; }
  document.getElementById('paddbtn').onclick = addItem;
  document.getElementById('padd').onkeydown = function(e){ if (e.key === 'Enter'){ e.preventDefault(); addItem(); } };
  document.getElementById('pall').onclick = function(){ ROOM.forEach(function(p){ if (items.indexOf(p.name) < 0) items.push(p.name); }); save(); renderList(); };
  document.getElementById('pclear').onclick = function(){ items = []; save(); renderList(); };
  function needItems(min){ if (items.length >= min) return true; document.getElementById('plist').open = true; alertBox('Cần ít nhất ' + min + ' mục trong danh sách.'); return false; }
  function alertBox(msg){ var r = document.querySelector('section:not([hidden]) .result, #teams, #draws'); var el = document.getElementById('pmsg'); if (!el){ el = document.createElement('div'); el.id = 'pmsg'; el.className = 'note bad'; el.style.margin = '10px 0'; document.getElementById('plist').after(el); } el.textContent = msg; setTimeout(function(){ el.remove(); }, 2500); }

  // ---------- tab
  document.querySelectorAll('.ptabs button').forEach(function(b){
    b.onclick = function(){
      document.querySelectorAll('.ptabs button').forEach(function(x){ x.classList.toggle('on', x === b); });
      document.querySelectorAll('section[data-pane]').forEach(function(s){ s.hidden = s.dataset.pane !== b.dataset.tab; });
      if (b.dataset.tab === 'wheel') drawWheel();
    };
  });

  // ---------- vòng quay
  var cv = document.getElementById('wheel'), ctx = cv.getContext('2d'), rot = 0, spinning = false;
  function sizeCanvas(){
    var w = Math.min(340, document.querySelector('.wbox').clientWidth || 320), dpr = window.devicePixelRatio || 1;
    cv.style.width = w + 'px'; cv.style.height = w + 'px'; cv.width = w * dpr; cv.height = w * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); return w;
  }
  function drawWheel(){
    var w = sizeCanvas(), r = w / 2, n = items.length;
    ctx.clearRect(0, 0, w, w);
    if (!n){ ctx.fillStyle = '#343c63'; ctx.beginPath(); ctx.arc(r, r, r - 4, 0, 2 * Math.PI); ctx.fill(); return; }
    var seg = 2 * Math.PI / n;
    for (var i = 0; i < n; i++){
      var a0 = -Math.PI / 2 + rot + i * seg;
      ctx.beginPath(); ctx.moveTo(r, r); ctx.arc(r, r, r - 4, a0, a0 + seg); ctx.closePath();
      var col = COLORS[i % COLORS.length]; if (n % COLORS.length === 1 && i === n - 1) col = COLORS[2];
      ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = 'rgba(255,255,255,.85)'; ctx.lineWidth = 2; ctx.stroke();
      var mid = a0 + seg / 2, left = Math.cos(mid) < 0;      // nửa trái: lật chữ cho đọc xuôi
      ctx.save(); ctx.translate(r, r); ctx.rotate(left ? mid + Math.PI : mid);
      var fs = Math.max(11, Math.min(17, 220 / Math.max(n, 6)));
      ctx.font = '700 ' + fs + 'px "Be Vietnam Pro", system-ui, sans-serif';
      ctx.textAlign = left ? 'left' : 'right'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#fff';
      ctx.shadowColor = 'rgba(0,0,0,.35)'; ctx.shadowBlur = 3;
      var t = label(items[i]); var maxw = r - 46;
      while (ctx.measureText(t).width > maxw && t.length > 3) t = t.slice(0, -2) + '…';
      ctx.fillText(t, left ? -(r - 14) : r - 14, 0); ctx.restore();
    }
    ctx.beginPath(); ctx.arc(r, r, r - 2, 0, 2 * Math.PI); ctx.strokeStyle = '#fff'; ctx.lineWidth = 4; ctx.stroke();
  }
  function indexAtPointer(){
    var n = items.length, seg = 2 * Math.PI / n;
    var a = ((-rot) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
    return Math.min(n - 1, Math.floor(a / seg));
  }
  window.__wheelAt = function(){ return items[indexAtPointer()]; };
  var wres = document.getElementById('wres');
  document.getElementById('spin').onclick = function(){
    if (spinning || !needItems(2)) return;
    spinning = true; wres.hidden = true; this.disabled = true;
    var n = items.length, seg = 2 * Math.PI / n, win = rnd(n);
    var jitter = (rnd(1000) / 1000 - 0.5) * seg * 0.7;
    var want = -((win + 0.5) * seg + jitter);
    var delta = ((want - rot) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) + 2 * Math.PI * 6;
    var from = rot, dur = 4200, t0 = null;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var btn = this;
    function done(){
      rot = from + delta; drawWheel(); spinning = false; btn.disabled = false;
      var name = items[indexAtPointer()];
      wres.innerHTML = '<div class="big">🎉 ' + esc(label(name)) + '</div>';
      wres.hidden = false; wres.dataset.name = name;
      if (document.getElementById('wremove').checked){ items.splice(items.indexOf(name), 1); save(); setTimeout(renderList, 900); }
    }
    if (reduce){ done(); return; }
    function step(ts){
      if (t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      rot = from + delta * e; drawWheel();
      if (p < 1) requestAnimationFrame(step); else done();
    }
    requestAnimationFrame(step);
  };
  cv.onclick = function(){ document.getElementById('spin').click(); };   // chạm vào vòng quay cũng quay
  window.addEventListener('resize', drawWheel);

  // ---------- chia team
  var TEAMS = [['🔴','Đỏ','#ec6b6b'],['🔵','Xanh','#5aa9e6'],['🟢','Lá','#5fae5f'],['🟡','Vàng','#f2b04e'],['🟣','Tím','#a78bfa'],['🟠','Cam','#f08a4b'],['⚪','Trắng','#c9ccd8'],['🟤','Nâu','#a47551']];
  window.__splitTeams = function(list, size, count){
    var s = shuffle(list), teams = [], bench = [];
    if (size){
      var k = Math.floor(s.length / size);
      for (var i = 0; i < k; i++) teams.push(s.slice(i * size, (i + 1) * size));
      bench = s.slice(k * size);
    } else {
      for (var j = 0; j < count; j++) teams.push([]);
      s.forEach(function(x, i){ teams[i % count].push(x); });
    }
    return {teams: teams, bench: bench};
  };
  document.querySelectorAll('[data-size],[data-teams]').forEach(function(b){
    b.onclick = function(){
      var size = +b.dataset.size || 0, count = +b.dataset.teams || 0;
      if (!needItems(size ? size * 2 : count * 1 + 1)) return;
      var r = window.__splitTeams(items, size, count);
      var html = r.teams.map(function(t, i){
        var c = TEAMS[i % TEAMS.length];
        return '<div class="panel team" style="border-color:' + c[2] + '"><h2 style="color:' + c[2] + '">' + c[0] + ' Đội ' + c[1] + ' <span class="count">· ' + t.length + ' người</span></h2>' +
               '<ul class="roster">' + t.map(function(x){ return '<li><span>' + esc(label(x)) + '</span></li>'; }).join('') + '</ul></div>';
      }).join('');
      if (r.bench.length) html += '<div class="panel"><h2>⏳ Chờ lượt sau <span class="count">· ' + r.bench.length + ' người</span></h2><ul class="roster">' + r.bench.map(function(x){ return '<li><span>' + esc(label(x)) + '</span></li>'; }).join('') + '</ul></div>';
      html += '<button type="button" class="btn-ghost" id="again">🔁 Chia lại</button>';
      var box = document.getElementById('teams'); box.innerHTML = html;
      box.dataset.teams = JSON.stringify(r);
      document.getElementById('again').onclick = function(){ b.click(); };
      box.scrollIntoView({behavior: 'smooth', block: 'start'});
    };
  });

  // ---------- bốc thăm
  var dn = 1;
  function clampDraw(){ dn = Math.max(1, Math.min(dn, Math.max(items.length, 1))); document.getElementById('dn').textContent = dn; }
  document.getElementById('dminus').onclick = function(){ dn--; clampDraw(); };
  document.getElementById('dplus').onclick = function(){ dn++; clampDraw(); };
  document.getElementById('drawbtn').onclick = function(){
    if (!needItems(1)) return;
    var pick = shuffle(items).slice(0, dn), box = document.getElementById('draws');
    box.innerHTML = pick.map(function(x, i){ return '<li style="animation-delay:' + (i * 0.25) + 's"><span class="medal m' + Math.min(i + 1, 4) + '">' + (i + 1) + '</span>' + esc(label(x)) + '</li>'; }).join('');
    box.dataset.pick = JSON.stringify(pick);
  };

  // ---------- xúc xắc
  var FACES = ['⚀','⚁','⚂','⚃','⚄','⚅'], ndice = 1;
  document.querySelectorAll('[data-dice]').forEach(function(b){ b.onclick = function(){ ndice = +b.dataset.dice; document.querySelectorAll('[data-dice]').forEach(function(x){ x.classList.toggle('sel', x === b); }); document.getElementById('dice').textContent = Array(ndice + 1).join('⚀'); document.getElementById('dsum').textContent = ''; }; });
  document.querySelector('[data-dice="1"]').classList.add('sel');
  document.getElementById('rollbtn').onclick = function(){
    var el = document.getElementById('dice'), sum = document.getElementById('dsum'), k = 0, btn = this; btn.disabled = true;
    var vals = [];
    var iv = setInterval(function(){
      vals = []; for (var i = 0; i < ndice; i++) vals.push(rnd(6) + 1);
      el.textContent = vals.map(function(v){ return FACES[v - 1]; }).join('');
      if (++k >= 10){ clearInterval(iv); btn.disabled = false; el.dataset.vals = JSON.stringify(vals);
        sum.textContent = ndice > 1 ? 'Tổng: ' + vals.reduce(function(a, b){ return a + b; }, 0) : 'Được ' + vals[0] + ' điểm'; }
    }, 60);
  };

  // ---------- đồng xu
  var hist = {h: 0, t: 0};
  document.getElementById('flipbtn').onclick = function(){
    var c = document.getElementById('coin'), res = document.getElementById('coinres'), btn = this; btn.disabled = true;
    c.classList.remove('flip'); void c.offsetWidth; c.classList.add('flip'); res.textContent = '';
    setTimeout(function(){
      var heads = rnd(2) === 0; heads ? hist.h++ : hist.t++;
      res.innerHTML = '<span class="big">' + (heads ? 'Ngửa' : 'Sấp') + '</span>'; res.dataset.v = heads ? 'H' : 'T';
      document.getElementById('coinhist').textContent = 'Đã tung: ' + hist.h + ' ngửa · ' + hist.t + ' sấp';
      btn.disabled = false;
    }, 700);
  };

  renderList();
})();
</script>
</div>
"""

PICKER_CSS = """
.ptabs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
.ptabs::-webkit-scrollbar{display:none}
.ptabs button{flex:0 0 auto;width:auto;padding:9px 13px;font-size:14px;font-weight:500;background:var(--ink-2);color:var(--mist);border:1px solid var(--line);border-radius:999px}
.ptabs button.on{background:var(--villa);color:#1d1a10;border-color:var(--villa);font-weight:700}
.plist summary{list-style:none} .plist summary::-webkit-details-marker{display:none}
.plist[open] summary{margin-bottom:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chip{width:auto;padding:6px 10px;font-size:14px;font-weight:500;background:var(--ink);color:var(--moon);border:1px solid var(--line);border-radius:999px}
.addrow{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.addrow input{margin:0;flex:1}
.addrow .mini{padding:10px 14px}
.wbox{position:relative;width:100%;max-width:340px;margin:6px auto 14px}
.wbox canvas{position:relative;z-index:1;cursor:pointer;display:block;margin:0 auto;border-radius:50%;box-shadow:0 6px 24px rgba(0,0,0,.35)}
.wpointer{position:absolute;left:50%;top:-10px;transform:translateX(-50%);width:0;height:0;border-left:13px solid transparent;border-right:13px solid transparent;border-top:24px solid #6366f1;z-index:2;filter:drop-shadow(0 2px 2px rgba(0,0,0,.4))}
.wspin{position:absolute;z-index:3;left:50%;top:50%;transform:translate(-50%,-50%);width:78px;height:78px;border-radius:50%;padding:0;background:#6366f1;color:#fff;font-size:17px;border:4px solid #fff;box-shadow:0 2px 10px rgba(0,0,0,.35)}
.wspin:disabled{opacity:.85}
.wspin:active{transform:translate(-50%,-50%) scale(.95)}
.result{text-align:center;margin:4px 0 12px}
.result .big,.big{font:700 28px/1.25 "Lora",Georgia,serif}
.grid3{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
.team{border-width:2px}
.bignum{font-size:28px;min-width:40px;text-align:center}
.draws{list-style:none;padding:0;margin:14px 0 0}
.draws li{display:flex;align-items:center;gap:12px;background:var(--ink-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:8px;font-weight:600;animation:pop .35s ease both}
.medal{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;font-weight:700;color:#1d1a10;background:#e98a3a}
.medal.m1{background:#f2c12e}.medal.m2{background:#b9c2cf}.medal.m3{background:#cd8a52}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.dice{font-size:84px;line-height:1.1;letter-spacing:6px}
.coin{font-size:96px;line-height:1.1}
.coin.flip{animation:flip .7s ease}
@keyframes flip{0%{transform:rotateY(0)}100%{transform:rotateY(1080deg)}}
button.sel{border-color:var(--villa);color:var(--moon)}
@media (prefers-reduced-motion:reduce){.coin.flip,.draws li{animation:none}}
"""


@app.route('/picker')
def picker():
    room = [{"name": n, "icon": av(n)} for n, i in game_data["players"].items()]
    back = "/admin" if request.args.get("from") == "admin" else "/"
    body = render_template_string(PICKER_BODY, room=room, back=back)
    return page("HILtrip Random", body, "", "/picker?partial=1")


# nạp trạng thái đã lưu (nếu có DATABASE_URL)
db_load()

# Không đặt SECRET_KEY trên Render: dùng khoá lưu trong database, để khởi động lại không đăng xuất cả đoàn
if not os.environ.get("SECRET_KEY") and DATABASE_URL:
    if not game_data.get("_secret"):
        game_data["_secret"] = secrets.token_hex(32)
        db_save()
    app.secret_key = game_data["_secret"]


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
