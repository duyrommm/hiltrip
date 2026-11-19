"""Trang Random (/picker): phía máy chủ. Phần chạy trên trình duyệt nằm trong tests/e2e_mobile.py."""
import json
import re

from conftest import admin, join, html, players


def room_json(page):
    m = re.search(r"var ROOM = (\[.*?\]);", page)
    assert m, "thiếu danh sách phòng"
    return json.loads(m.group(1))


def test_picker_open_without_login(A):
    r = A.app.test_client().get("/picker")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "Vòng quay" in page and "Chia team" in page and "Bốc thăm" in page
    assert room_json(page) == []


def test_picker_lists_room_players_with_icons(A):
    join(A, "Duy đẹp trai", avatar="🦀")
    join(A, "Như", avatar="🐬")
    room = room_json(html(A.app.test_client(), "/picker"))
    assert {"name": "Duy đẹp trai", "icon": "🦀"} in room and {"name": "Như", "icon": "🐬"} in room


def test_picker_names_are_escaped(A):
    join(A, '</script><b>x')
    page = html(A.app.test_client(), "/picker")
    assert "</script><b>x" not in page                    # tên lạ không phá trang


def test_picker_is_static_page(A):
    page = html(A.app.test_client(), "/picker")
    assert 'data-static="1"' in page                      # không bị tự làm mới xoá kết quả


def test_picker_links_and_back(A):
    c = join(A, "Duy")
    assert 'href="/picker"' in html(c)                    # phòng chờ có nút Random
    ad = admin(A)
    assert 'href="/picker?from=admin"' in html(ad, "/admin")
    assert 'href="/admin">← Quay lại' in html(ad, "/picker?from=admin")
    assert 'href="/">← Quay lại' in html(c, "/picker")


def test_picker_hidden_during_games_for_players(A):
    ps = players(A, "A1", "A2", "A3", "A4")
    admin(A).post("/admin/assign")
    assert 'href="/picker"' not in html(ps["A1"])         # đang chơi Ma Sói thì không hiện, tránh bấm nhầm
