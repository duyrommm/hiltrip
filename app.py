import random
from flask import Flask, render_template_string, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = 'hiltrip2026_secret_key'

game_data = {
    "phase": "LOBBY",         # LOBBY -> NIGHT -> DAY -> VOTE -> END
    "players": {},            # { username: {"role": str, "alive": bool, "target": str, "acted": bool} }
    "night_result": "Đêm qua mọi thứ đều bình yên..."
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HILtrip Ma Sói Mini</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #121212; color: #e0e0e0; font-family: sans-serif; }
        .card { background-color: #1e1e1e; border: 1px solid #333; }
        .btn-custom { width: 100%; margin-bottom: 10px; }
    </style>
</head>
<body class="py-4">
    <div class="container" style="max-width: 500px;">
        <h3 class="text-center text-warning mb-3">🐺 HILTRIP MA SÓI MINI</h3>
        
        {% if not session.get('username') %}
        <div class="card p-4 shadow">
            <h5 class="mb-3 text-center">Nhập tên của bạn để tham gia</h5>
            <form method="POST" action="/join">
                <div class="mb-3">
                    <input type="text" name="username" class="form-control" placeholder="Tên hiển thị (VD: Khoa, Duy...)" required autocomplete="off">
                </div>
                <button type="submit" class="btn btn-warning w-100 fw-bold">Vào phòng chơi</button>
            </form>
        </div>
        
        {% else %}
        <div class="d-flex justify-content-between align-items-center mb-3 bg-dark p-2 rounded border border-secondary">
            <span>Xin chào, <b>{{ session.get('username') }}</b></span>
            <a href="/logout" class="btn btn-sm btn-outline-danger">Thoát</a>
        </div>

        {% if phase == 'LOBBY' %}
        <div class="card p-4 text-center">
            <h5 class="text-info">Phòng chờ đang mở</h5>
            <p class="text-muted">Đang chờ Quản trò chia vai trò...</p>
            <hr class="border-secondary">
            <h6 class="text-start">Thành viên đã vào ({{ players|length }} người):</h6>
            <ul class="list-group list-group-flush text-start bg-transparent">
                {% for p in players.keys() %}
                <li class="list-group-item bg-transparent text-light border-secondary">{{ loop.index }}. {{ p }}</li>
                {% endfor %}
            </ul>
        </div>

        {% elif phase == 'NIGHT' %}
        <div class="card p-4">
            <h4 class="text-danger text-center">🌙 TRỜI ĐÃ TỐI...</h4>
            <div class="alert alert-secondary text-center">
                Vai trò của bạn: <b class="text-warning fs-5">
                    {% if my_info.role == 'Wolf' %} 🐺 MA SÓI
                    {% elif my_info.role == 'Seer' %} 🔮 TIÊN TRI
                    {% elif my_info.role == 'Guard' %} 🛡️ BẢO VỆ
                    {% else %} 🧑 DÂN LÀNG {% endif %}
                </b>
            </div>
            
            {% if not my_info.alive %}
            <div class="alert alert-danger text-center">Bạn đã bị loại! Hãy quan sát mọi người.</div>
            {% else %}
                {% if my_info.role == 'Wolf' %}
                    {% if not my_info.acted %}
                    <p>Hãy chọn một người để "cắn" đêm nay:</p>
                    <form method="POST" action="/action">
                        <select name="target" class="form-select bg-dark text-light border-secondary mb-3">
                            {% for p, info in players.items() %}
                                {% if p != session.get('username') and info.alive %}
                                <option value="{{ p }}">{{ p }}</option>
                                {% endif %}
                            {% endfor %}
                        </select>
                        <button type="submit" class="btn btn-danger btn-custom">Xác nhận mục tiêu</button>
                    </form>
                    {% else %}
                    <div class="alert alert-success text-center">Bạn đã chọn mục tiêu xong. Hãy đợi trời sáng!</div>
                    {% endif %}
                {% else %}
                <p class="text-center text-muted">Trời tối, bạn nhắm mắt ngủ và chờ sáng...</p>
                {% endif %}
            {% endif %}
        </div>

        {% elif phase == 'DAY' or phase == 'VOTE' %}
        <div class="card p-4">
            <h4 class="text-warning text-center">☀️ TRỜI ĐÃ SÁNG</h4>
            <div class="alert alert-info">
                {{ game_data.night_result }}
            </div>
            <hr class="border-secondary">
            <h5 class="text-center">Danh sách người chơi:</h5>
            <ul class="list-group list-group-flush bg-transparent mb-3">
                {% for p, info in players.items() %}
                <li class="list-group-item bg-transparent text-light border-secondary d-flex justify-content-between align-items-center">
                    <span>{{ p }}</span>
                    <span>
                        {% if not info.alive %}
                        <span class="badge bg-danger">Đã chết</span>
                        {% else %}
                        <span class="badge bg-success">Còn sống</span>
                        {% endif %}
                    </span>
                </li>
                {% endfor %}
            </ul>
        </div>

        {% elif phase == 'END' %}
        <div class="card p-4 text-center">
            <h3 class="text-success">🏁 KẾT THÚC VÁN CHƠI</h3>
            <p class="mt-3">Cảm ơn mọi người đã tham gia HILtrip Ma Sói!</p>
            <a href="/admin" class="btn btn-warning">Về trang Admin Panel</a>
        </div>
        {% endif %}

        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    username = session.get('username')
    my_info = game_data["players"].get(username) if username else None
    return render_template_string(HTML_TEMPLATE, 
                                  phase=game_data["phase"], 
                                  players=game_data["players"], 
                                  my_info=my_info,
                                  game_data=game_data)

@app.route('/join', methods=['POST'])
def join():
    username = request.form.get('username').strip()
    if username:
        session['username'] = username
        if username not in game_data["players"]:
            game_data["players"][username] = {"role": "Villager", "alive": True, "target": None, "acted": False}
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/action', methods=['POST'])
def action():
    username = session.get('username')
    target = request.form.get('target')
    if username and username in game_data["players"]:
        game_data["players"][username]["target"] = target
        game_data["players"][username]["acted"] = True
    return redirect(url_for('index'))

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HILtrip Admin Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body { background-color: #121212; color: #e0e0e0; font-family: sans-serif; }</style>
</head>
<body class="py-4">
    <div class="container" style="max-width: 600px;">
        <h2 class="text-warning text-center mb-4">🎛️ QUẢN TRÒ ADMIN PANEL</h2>
        
        <div class="card bg-dark border-secondary p-4 mb-3">
            <h5>Trạng thái: <span class="text-info">{{ game_data.phase }}</span></h5>
            <p>Tổng số người chơi: <b>{{ game_data.players|length }}</b></p>
            <hr class="border-secondary">
            <h6 class="text-muted">Danh sách phòng (Bảo mật vai trò):</h6>
            <ul class="list-group mb-3">
                {% for p, info in game_data.players.items() %}
                <li class="list-group-item bg-transparent text-light border-secondary d-flex justify-content-between align-items-center">
                    <span>{{ p }}</span>
                    <span>
                        {% if info.alive %}
                            <span class="badge bg-success">Còn sống</span>
                        {% else %}
                            <span class="badge bg-danger">Đã chết</span>
                        {% endif %}
                    </span>
                </li>
                {% endfor %}
            </ul>
        </div>

        <div class="card bg-dark border-secondary p-4">
            <h5 class="mb-3">Điều khiển vòng chơi:</h5>
            <form action="/admin/assign" method="POST" class="mb-2">
                <button type="submit" class="btn btn-warning w-100 fw-bold">1. Phân vai & Bật Pha Đêm (NIGHT)</button>
            </form>
            <form action="/admin/next_phase/DAY" method="POST" class="mb-2">
                <button type="submit" class="btn btn-danger w-100">2. Chuyển sang Sáng (DAY - Xem kết quả)</button>
            </form>
            <form action="/admin/next_phase/END" method="POST" class="mb-2">
                <button type="submit" class="btn btn-success w-100">3. Kết thúc ván (END)</button>
            </form>
            <form action="/admin/reset" method="POST">
                <button type="submit" class="btn btn-outline-secondary w-100 mt-3">Reset toàn bộ phòng</button>
            </form>
        </div>
        <div class="text-center mt-3">
            <a href="/" class="text-warning">Quay lại màn hình người chơi của tôi</a>
        </div>
    </div>
</body>
</html>
"""

@app.route('/admin')
def admin_panel():
    return render_template_string(ADMIN_TEMPLATE, game_data=game_data)

@app.route('/admin/assign', methods=['POST'])
def admin_assign():
    usernames = list(game_data["players"].keys())
    total = len(usernames)
    
    if total <= 3:
        roles = ["Wolf"] + ["Villager"] * (total - 1)
    elif total <= 6:
        roles = ["Wolf", "Seer"] + ["Villager"] * (total - 2)
    elif total <= 10:
        roles = ["Wolf", "Seer", "Guard"] + ["Villager"] * (total - 3)
    else:
        roles = ["Wolf", "Wolf", "Seer", "Guard"] + ["Villager"] * (total - 4)
        
    random.shuffle(roles)
    
    for i, uname in enumerate(usernames):
        game_data["players"][uname]["role"] = roles[i]
        game_data["players"][uname]["alive"] = True
        game_data["players"][uname]["target"] = None
        game_data["players"][uname]["acted"] = False
        
    game_data["phase"] = "NIGHT"
    game_data["night_result"] = "Đêm qua trời yên biển lặng..."
    return redirect(url_for('admin_panel'))

@app.route('/admin/next_phase/<phase>', methods=['POST'])
def admin_next_phase(phase):
    if phase == "DAY":
        wolf_target = None
        for uname, info in game_data["players"].items():
            if info["role"] == "Wolf" and info["target"]:
                wolf_target = info["target"]
                break
        
        if wolf_target and wolf_target in game_data["players"]:
            game_data["players"][wolf_target]["alive"] = False
            game_data["night_result"] = f"Đêm qua, Sói đã ghé thăm và lấy đi một mạng: {wolf_target}!"
        else:
            game_data["night_result"] = "Đêm qua mọi người ngủ ngon, không ai bị thương."
            
    game_data["phase"] = phase
    return redirect(url_for('admin_panel'))

@app.route('/admin/reset', methods=['POST'])
def admin_reset():
    game_data["phase"] = "LOBBY"
    game_data["players"] = {}
    game_data["night_result"] = "Đêm qua mọi thứ đều bình yên..."
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)