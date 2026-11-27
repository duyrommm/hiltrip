"""Điệp vụ Vũng Tàu."""
from conftest import admin, join, html, players

NAMES = ["Duy", "Hà", "Tài", "chị Nhi", "Như", "Vàng", "Duy Đỗ", "chị Linh", "Huy", "Phụng", "anh Hai", "Minh", "Thảo", "Lan", "Bình"]


def test_start_targets_and_admin_hides_secrets(A):
    ps = players(A, *NAMES)
    ad = admin(A)
    ad.post("/admin/mission/start")
    m = A.game_data["mission"]
    spies = [n for n, a in m["agents"].items() if a["role"] == "spy"]
    assert len(spies) == 3
    assert all(t["owner"] != t["target"] for t in m["tasks"])
    page = html(ad, "/admin/mission")
    for t in m["tasks"]:
        assert t["text"].split("{t}")[-1][:20] not in page or len(t["text"].split("{t}")[-1]) < 8
    assert "Đồng bọn" in html(ps[spies[0]], "/mission")


def test_done_catch_wrong_catch_and_intel(A):
    ps = players(A, *NAMES[:6])
    ad = admin(A)
    ad.post("/admin/mission/start")
    m = A.game_data["mission"]
    t = [t for t in m["tasks"] if t["kind"] == "mission"][0]
    ps[t["owner"]].post("/mission/done", data={"id": t["id"], "note": "đã làm"})
    assert t["status"] == "done" and m["agents"][t["owner"]]["score"] == 2
    ad.post("/admin/mission/next", data={"stage": "0"})
    x = [t for t in m["tasks"] if t["stage"] == 1 and t["kind"] == "mission"][0]
    o, tg = x["owner"], x["target"]
    ps[tg].post("/mission/catch", data={"who": o})
    assert x["status"] == "caught" and o in m["agents"][tg]["intel"]
    assert "Manh mối" in html(ps[tg], "/mission")
    # chọn một người chắc chắn KHÔNG có nhiệm vụ đang nhắm vào tg (kiểm tra trước khi bắt)
    wrong = [n for n in ps if n not in (tg, o) and not any(
        t["owner"] == n and t["target"] == tg and t["status"] == "open" for t in m["tasks"])][0]
    s = m["agents"][tg]["score"]
    ps[tg].post("/mission/catch", data={"who": wrong})
    assert m["agents"][tg]["score"] == s - 1


def test_double_tap_next_stage_opens_one_stage(A):
    players(A, *NAMES[:5])
    ad = admin(A)
    ad.post("/admin/mission/start")
    for _ in range(3):
        ad.post("/admin/mission/next", data={"stage": "0"})
    assert A.game_data["mission"]["stage"] == 1


def test_vote_reveal_and_late_joiner(A):
    ps = players(A, *NAMES)
    ad = admin(A)
    ad.post("/admin/mission/start")
    late = join(A, "Muộn")
    ad.post("/admin/mission/next", data={"stage": "0"})
    assert "Muộn" in A.game_data["mission"]["agents"]
    ad.post("/admin/mission/vote")
    spies = [n for n, a in A.game_data["mission"]["agents"].items() if a["role"] == "spy"]
    for n, c in ps.items():
        c.post("/mission/vote", data={"who": spies[0] if n != spies[0] else spies[1]})
    ad.post("/admin/mission/reveal")
    m = A.game_data["mission"]
    assert m["phase"] == "REVEAL" and spies[0] in m["caught"]
    assert "Bảng điểm" in html(late, "/mission")


def test_no_market_or_grill_only_missions(A):
    bad = ("nướng giùm", "lật giùm", "trả giá", "hỏi giá")
    for k in ("cho", "tiec"):
        for t in A.MISSIONS[k]:
            assert not any(b in t for b in bad), t


def test_demo_mode_cleanup(A):
    ad = admin(A)
    join(A, "Duy", client=ad)                      # quản trò tự nhập tên trên chính trình duyệt của mình
    ad.post("/admin/mission/demo", data={"role": "spy"})
    m = A.game_data["mission"]
    assert m["agents"]["Duy"]["role"] == "spy" and len(m["agents"]) == 6
    ad.post("/admin/mission/demo_act")
    ad.post("/admin/mission/reset")
    assert not any(b in A.game_data["players"] for b in A.BOT_NAMES)


def test_drivers_skip_road_stage_and_are_never_targets(A):
    ps = players(A, *NAMES[:8])
    ad = admin(A)
    drivers = ["Tài", "Vàng"]
    for _ in range(15):                                   # ngẫu nhiên nhiều lần
        A.game_data["mission"] = None
        ad.post("/admin/mission/start", data={"driver": drivers})
        m = A.game_data["mission"]
        assert m["drivers"] == drivers
        road = [t for t in m["tasks"] if t["stage"] == 0]
        assert road and all(t["owner"] not in drivers for t in road), "tài xế không nhận nhiệm vụ khi đang lái"
        assert all(t["target"] not in drivers for t in road), "không ai được dụ tài xế cầm điện thoại"
        assert {t["owner"] for t in road if t["kind"] == "mission"} == set(NAMES[:8]) - set(drivers)
    page = html(ps["Tài"], "/mission")
    assert "Bạn đang lái xe" in page and "Bạn đã xong hết nhiệm vụ" not in page
    assert "Bạn đang lái xe" not in html(ps["Duy"], "/mission")
    ad.post("/admin/mission/next", data={"stage": "0"})       # sang Cà phê Umi+: tài xế chơi bình thường
    stage1 = [t for t in m["tasks"] if t["stage"] == 1 and t["kind"] == "mission"]
    assert {t["owner"] for t in stage1} == set(NAMES[:8])
    assert "Bạn đang lái xe" not in html(ps["Tài"], "/mission")


def test_admin_page_lists_players_to_mark_as_drivers(A):
    players(A, *NAMES[:5])
    ad = admin(A)
    page = html(ad, "/admin/mission")
    assert "Ai đang lái xe?" in page and page.count('name="driver"') == 5


def test_start_without_drivers_unchanged_and_unknown_driver_ignored(A):
    players(A, *NAMES[:6])
    ad = admin(A)
    ad.post("/admin/mission/start", data={"driver": ["Không có tên này"]})
    m = A.game_data["mission"]
    assert m["drivers"] == []
    assert len([t for t in m["tasks"] if t["stage"] == 0 and t["kind"] == "mission"]) == 6


def test_too_many_drivers_leaves_road_stage_empty_but_game_continues(A):
    players(A, *NAMES[:5])
    ad = admin(A)
    ad.post("/admin/mission/start", data={"driver": NAMES[:4]})     # chỉ còn 1 người không lái
    m = A.game_data["mission"]
    assert not [t for t in m["tasks"] if t["stage"] == 0]
    ad.post("/admin/mission/next", data={"stage": "0"})
    assert len([t for t in m["tasks"] if t["stage"] == 1 and t["kind"] == "mission"]) == 5
