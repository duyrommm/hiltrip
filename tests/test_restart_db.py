"""Máy chủ khởi động lại (Render ngủ dậy / deploy): ván, điểm và phiên đăng nhập phải còn.
Cần PostgreSQL: TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_restart_db.py"""
import importlib
import os

import pytest

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="chưa đặt TEST_DATABASE_URL")


def fresh(monkeypatch, secret=None):
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("ADMIN_KEY", "k")
    if secret:
        monkeypatch.setenv("SECRET_KEY", secret)
    else:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    import app as mod
    return importlib.reload(mod)


@pytest.mark.parametrize("secret", [None, "khoa-tren-render"])
def test_restart_keeps_game_and_login(monkeypatch, secret):
    import psycopg
    with psycopg.connect(DB, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS hiltrip_state")
    A = fresh(monkeypatch, secret)
    ad = A.app.test_client()
    ad.get("/admin?key=k")
    pl = {}
    for n in ["Duy", "Nhi", "Như", "Vàng", "Huy"]:
        c = A.app.test_client()
        c.post("/join", data={"username": n})
        pl[n] = c
    ad.post("/admin/assign")
    role = A.game_data["players"]["Như"]["role"]
    cookie = pl["Như"].get_cookie("session").value
    admin_cookie = ad.get_cookie("session").value

    B = fresh(monkeypatch, secret)                      # "khởi động lại"
    assert B.game_data["phase"] == "NIGHT" and B.game_data["players"]["Như"]["role"] == role
    c2 = B.app.test_client()
    c2.set_cookie("session", cookie)
    page = c2.get("/").get_data(as_text=True)
    assert B.ROLES[role]["name"] in page and "Vào phòng chơi" not in page   # không bị đăng xuất
    a2 = B.app.test_client()
    a2.set_cookie("session", admin_cookie)
    assert a2.get("/admin").status_code == 200
