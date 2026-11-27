"""Hiệu năng: mẫu giao diện chỉ dịch một lần, và kết quả hiển thị giống hệt cách làm cũ của Flask."""
import flask
from conftest import admin, html, players


def _pages(A, ad, ps):
    """Đi qua các màn hình chính của mọi trò, trả về HTML của người chơi và quản trò."""
    out = []
    def snap(tag):
        out.append((tag + " · người chơi", html(ps["An"], "/")))
        out.append((tag + " · người chơi partial", html(ps["An"], "/?partial=1")))
        out.append((tag + " · quản trò", html(ad, "/admin")))
    gid = lambda: str(A.game_data["game_id"])
    snap("sảnh chờ")
    ad.post("/admin/assign", data={"gid": gid()}); snap("ma sói")
    ad.post("/admin/lobby"); ad.post("/admin/spy/start", data={"gid": gid(), "civ": "Biển", "spy": "Hồ"}); snap("gián điệp")
    ad.post("/admin/lobby"); ad.post("/admin/tod/start", data={"gid": gid()}); snap("thật hay thách")
    ad.post("/admin/lobby"); ad.post("/admin/trap/start", data={"gid": gid(), "skin": "pirate", "mode": "turn", "pen": "beer"}); snap("hải tặc")
    out.append(("random", html(ps["An"], "/picker")))
    out.append(("điệp vụ", html(ps["An"], "/mission")))
    out.append(("điệp vụ · quản trò", html(ad, "/admin/mission")))
    return out


def test_templates_compiled_once(A, monkeypatch):
    ps = players(A, "An", "Bình", "Chi", "Dũng", "Em", "Phúc")
    ad = admin(A)
    calls = []
    real = A.app.jinja_env.from_string
    monkeypatch.setattr(A.app.jinja_env, "from_string", lambda src, *a, **k: (calls.append(1), real(src, *a, **k))[1])
    for _ in range(5):
        html(ps["An"], "/?partial=1"); html(ad, "/admin"); html(ps["Bình"], "/")
    first = len(calls)
    for _ in range(50):
        html(ps["An"], "/?partial=1"); html(ad, "/admin"); html(ps["Bình"], "/")
    assert len(calls) == first, "tải lại trang không được dịch lại mẫu"


def test_same_html_as_flask_original(A, monkeypatch):
    import random
    ps = players(A, "An", "Bình", "Chi", "Dũng", "Em", "Phúc")
    ad = admin(A)
    state = __import__("copy").deepcopy(A.game_data)
    random.seed(7)
    new = _pages(A, ad, ps)
    A.game_data.clear(); A.game_data.update(__import__("copy").deepcopy(state))
    monkeypatch.setattr(A, "render_template_string", flask.render_template_string)
    random.seed(7)
    old = _pages(A, ad, ps)
    assert len(new) == len(old) >= 18
    for (tag, a), (_, b) in zip(new, old):
        assert a == b, f"khác nhau ở màn hình: {tag}"
