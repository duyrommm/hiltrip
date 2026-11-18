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
