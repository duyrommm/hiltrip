"""Bộ test HILtrip. Chạy:  python -m pytest -q
Không cần mạng hay database. Có PostgreSQL thì đặt TEST_DATABASE_URL để chạy thêm test khởi động lại."""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ADMIN_KEY = "k"


@pytest.fixture
def A(monkeypatch):
    """Nạp app mới tinh cho mỗi test (trạng thái sạch, có khoá quản trò)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("ADMIN_KEY", ADMIN_KEY)
    import app as mod
    mod = importlib.reload(mod)
    mod.app.config["TESTING"] = True
    return mod


def admin(A):
    """Trình duyệt của quản trò (đã mở /admin?key=...)."""
    c = A.app.test_client()
    assert c.get("/admin?key=" + ADMIN_KEY).status_code == 200
    return c


def join(A, name, avatar=None, client=None):
    """Một trình duyệt mới nhập tên vào phòng."""
    c = client or A.app.test_client()
    data = {"username": name}
    if avatar:
        data["avatar"] = avatar
    c.post("/join", data=data)
    return c


def html(c, url="/"):
    return c.get(url).get_data(as_text=True)


def players(A, *names):
    return {n: join(A, n) for n in names}


def by_role(A):
    out = {}
    for n, i in A.game_data["players"].items():
        out.setdefault(i["role"], []).append(n)
    return out


def set_roles(A, mapping):
    """Gán vai cố định sau khi chia vai, để test không phụ thuộc may rủi."""
    for n, r in mapping.items():
        A.game_data["players"][n]["role"] = r
