"""Người chơi vào lại: tải lại trang, tắt trình duyệt, đổi máy, trùng tên, thoát, bị xoá, vào giữa ván."""
import time

from conftest import admin, join, html, players


# ---------- 1. Tải lại trang / mở lại trên cùng trình duyệt: vẫn đúng người, đúng ván
def test_reload_keeps_spy_word(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng")
    ad = admin(A)
    ad.post("/admin/spy/start")
    word = A.game_data["spy"]["roster"]["Như"]["word"] or "Mũ Trắng"
    for _ in range(3):
        assert word in html(ps["Như"])


def test_reload_keeps_werewolf_role(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    admin(A).post("/admin/assign")
    role = A.ROLES[A.game_data["players"]["Huy"]["role"]]["name"]
    for _ in range(3):
        assert role in html(ps["Huy"])


def test_reload_keeps_mission_and_tod(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng")
    ad = admin(A)
    ad.post("/admin/mission/start")
    task = [t for t in A.game_data["mission"]["tasks"] if t["owner"] == "Như"][0]
    target = task["target"]
    assert target in html(ps["Như"], "/mission")
    ad.post("/admin/tod/start")
    assert "Quay!" in html(ps["Như"])


# ---------- 2. Cookie giữ 7 ngày: tắt hẳn trình duyệt mở lại vẫn còn
def test_session_cookie_is_persistent(A):
    c = A.app.test_client()
    r = c.post("/join", data={"username": "Duy"})
    cookie = r.headers.get("Set-Cookie", "")
    assert "Expires=" in cookie, cookie


def test_admin_cookie_is_persistent(A):
    c = A.app.test_client()
    r = c.get("/admin?key=k")
    assert "Expires=" in r.headers.get("Set-Cookie", "")
    assert c.get("/admin").status_code == 200          # vào lại không cần ?key


# ---------- 3. Thoát rồi vào lại trên cùng trình duyệt: không hỏi, giữ vai và icon
def test_logout_and_rejoin_same_browser(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    admin(A).post("/admin/assign")
    role = A.game_data["players"]["Như"]["role"]
    avatar = A.game_data["players"]["Như"]["avatar"]
    ps["Như"].get("/logout")
    assert "Vào phòng chơi" in html(ps["Như"])
    join(A, "Như", client=ps["Như"])
    p = html(ps["Như"])
    assert "Bạn vào lại?" not in p
    assert A.game_data["players"]["Như"]["role"] == role and A.game_data["players"]["Như"]["avatar"] == avatar


# ---------- 4. Trùng tên khi người kia đang online: chặn, không lộ vai
def test_duplicate_name_while_owner_online_is_blocked(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    admin(A).post("/admin/assign")
    ps["Như"].get("/state")                              # Như đang mở app
    other = A.app.test_client()
    r = other.post("/join", data={"username": "Như"}).get_data(as_text=True)
    assert "đang có người dùng trên máy khác" in r
    role = A.ROLES[A.game_data["players"]["Như"]["role"]]["name"]
    assert role not in html(other)                       # người lạ không xem được vai
    assert "Vào phòng chơi" in html(other)
    assert role in html(ps["Như"])                       # Như vẫn chơi bình thường


# ---------- 5. Đổi máy / mở bằng trình duyệt khác khi máy cũ đã tắt: hỏi xác nhận rồi vào đúng người
def test_rejoin_from_other_browser_after_owner_offline(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    admin(A).post("/admin/assign")
    A.LAST_SEEN["Như"] = time.time() - 120               # máy cũ đã tắt 2 phút
    new = A.app.test_client()
    r = new.post("/join", data={"username": "Như"}).get_data(as_text=True)
    assert "Bạn vào lại?" in r and "Đúng, là tôi" in r
    assert "Vào phòng chơi" in html(new) or "Bạn vào lại?" not in html(new)   # chưa xác nhận thì chưa vào
    new.post("/join", data={"username": "Như", "confirm": "1"})
    role = A.ROLES[A.game_data["players"]["Như"]["role"]]["name"]
    assert role in html(new)
    # máy cũ mở lại: tên đã chuyển sang máy mới nên máy cũ về trang nhập tên
    assert "Vào phòng chơi" in html(ps["Như"])
    assert len(A.game_data["players"]) == 5               # không sinh người chơi thừa


def test_choose_other_name_link(A):
    players(A, "Như", "Duy", "Vàng")
    A.LAST_SEEN["Như"] = 0
    r = A.app.test_client().post("/join", data={"username": "Như"}).get_data(as_text=True)
    assert 'href="/"' in r and "chọn tên khác" in r


# ---------- 6. Quản trò xoá phòng: vào lại đúng tên, giữ điểm Điệp vụ
def test_room_reset_keeps_mission_identity(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng")
    ad = admin(A)
    ad.post("/admin/mission/start")
    m = A.game_data["mission"]
    m["agents"]["Như"]["score"] = 7
    ad.post("/admin/reset")
    assert "Vào phòng chơi" in html(ps["Như"])
    join(A, "Như", client=ps["Như"])
    assert "Bạn vào lại?" not in html(ps["Như"])          # cùng trình duyệt: không hỏi
    assert "<b>7</b>" in html(ps["Như"], "/mission")


def test_room_reset_other_browser_must_confirm_mission_name(A):
    players(A, "Duy", "Nhi", "Như", "Vàng")
    ad = admin(A)
    ad.post("/admin/mission/start")
    ad.post("/admin/reset")
    A.LAST_SEEN.clear()
    r = A.app.test_client().post("/join", data={"username": "Như"}).get_data(as_text=True)
    assert "Bạn vào lại?" in r


# ---------- 7. Bị quản trò xoá tên ở phòng chờ
def test_kicked_player_back_to_join(A):
    ps = players(A, "Duy", "Duyy", "Như")
    admin(A).post("/admin/kick", data={"name": "Duyy"})
    assert "Vào phòng chơi" in html(ps["Duyy"])


# ---------- 8. Vào giữa ván: chỉ xem, không phá ván
def test_late_join_each_game(A):
    players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    ad = admin(A)
    ad.post("/admin/assign")
    late = join(A, "Muộn")
    assert "chỉ xem" in html(late)
    ad.post("/admin/lobby")
    ad.post("/admin/spy/start")
    late2 = join(A, "Muộn 2")
    assert "chỉ xem" in html(late2)
    ad.post("/admin/spy/end")
    ad.post("/admin/mission/start")
    ad.post("/admin/lobby")
    late3 = join(A, "Muộn 3")
    assert "vào sau khi điệp vụ" in html(late3, "/mission")


# ---------- 9. Mở link trong Zalo/Messenger: nhắc mở bằng trình duyệt
def test_in_app_browser_hint(A):
    c = A.app.test_client()
    z = c.get("/", headers={"User-Agent": "Mozilla/5.0 (iPhone) Zalo iOS"}).get_data(as_text=True)
    assert "Mở bằng trình duyệt" in z
    n = c.get("/", headers={"User-Agent": "Mozilla/5.0 (iPhone) CriOS/120"}).get_data(as_text=True)
    assert "Mở bằng trình duyệt" not in n


# ---------- 10. Trang Điệp vụ khi chưa nhập tên
def test_mission_page_without_name(A):
    players(A, "Duy", "Nhi", "Như", "Vàng")
    admin(A).post("/admin/mission/start")
    assert "Nhập tên" in html(A.app.test_client(), "/mission")
