"""Khám răng cá sấu / Đâm hải tặc: luật, lượt, bẫy giấu kín, chống bấm lặp."""
import re
from collections import Counter

from conftest import admin, join, html, players


def start(A, skin="croc", mode="turn", n=4):
    ps = players(A, *[f"P{i}" for i in range(n)])
    ad = admin(A)
    ad.post("/admin/trap/start", data={"skin": skin, "mode": mode, "gid": A.game_data["game_id"]})
    return ad, ps


def safe_slot(t):
    return next(i for i in range(A_SLOTS[t["skin"]]) if i != t["trap"] and i not in t["pressed"])


A_SLOTS = {"croc": 12, "pirate": 16}


def press(c, t, i):
    return c.post("/trap/press", data={"i": i, "round": t["round"]})


def test_start_both_skins(A):
    for skin, word in (("croc", "răng"), ("pirate", "lỗ")):
        ad, ps = start(A, skin)
        t = A.game_data["trap"]
        assert A.game_data["phase"] == "TRAP" and 0 <= t["trap"] < A_SLOTS[skin]
        assert word in html(ps["P0"])
        ad.post("/admin/lobby")
        A.game_data["players"] = {}


def test_trap_position_never_in_page(A):
    ad, ps = start(A, "pirate")
    t = A.game_data["trap"]
    for c in list(ps.values()) + [ad]:
        page = html(c) if c is not ad else html(ad, "/admin")
        assert "data-trap" not in page and '"trap"' not in page
    # nút của ô bẫy trông y hệt ô khác
    page = html(ps[t["order"][0]])
    buttons = re.findall(r'<button type="submit" class="hole[^"]*" aria-label="Lỗ (\d+)"', page)
    assert len(buttons) == 16


def test_turn_order_enforced(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    cur, other = t["order"][0], t["order"][1]
    press(ps[other], t, safe_slot(t))
    assert t["pressed"] == []                              # chưa tới lượt: không bấm được
    assert "Đến lượt bạn" in html(ps[cur]) and "Lượt của" in html(ps[other])
    press(ps[cur], t, safe_slot(t))
    assert len(t["pressed"]) == 1 and t["order"][t["turn"]] == other


def test_same_slot_and_old_round_ignored(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    i = safe_slot(t)
    cur = t["order"][0]
    press(ps[cur], t, i)
    nxt = t["order"][1]
    press(ps[nxt], t, i)                                   # ô đã bấm
    assert t["pressed"] == [i] and t["turn"] == 1
    ps[nxt].post("/trap/press", data={"i": safe_slot(t), "round": t["round"] - 1})   # ván cũ
    assert t["turn"] == 1
    ps[nxt].post("/trap/press", data={"i": 99, "round": t["round"]})                  # ô không tồn tại
    assert t["turn"] == 1


def test_boom_loser_penalty_and_locked(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    press(ps[t["order"][0]], t, safe_slot(t))
    loser = t["order"][1]
    press(ps[loser], t, t["trap"])
    assert t["phase"] == "BOOM" and t["loser"] == loser and t["losses"][loser] == 1
    assert t["penalty"] in A.TOD_PENALTY
    for c in ps.values():
        p = html(c)
        assert "CẮN!" in p and f"Hình phạt cho {loser}" in p
    before = list(t["pressed"])
    press(ps[t["order"][2]], t, safe_slot(t))
    assert t["pressed"] == before                          # đã sập bẫy: không bấm thêm được


def test_new_round_once_loser_starts(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    loser = t["order"][0]
    press(ps[loser], t, t["trap"])
    r = t["round"]
    for _ in range(3):
        ad.post("/admin/trap/again", data={"round": r})    # bấm lặp
    assert t["round"] == r + 1 and t["phase"] == "PLAY" and t["pressed"] == []
    assert t["order"][0] == loser and t["losses"][loser] == 1


def test_free_mode_anyone_presses(A):
    ad, ps = start(A, mode="free")
    t = A.game_data["trap"]
    press(ps["P3"], t, safe_slot(t))
    press(ps["P1"], t, safe_slot(t))
    assert len(t["pressed"]) == 2
    press(ps["P2"], t, t["trap"])
    assert t["loser"] == "P2"
    outsider = A.app.test_client()
    outsider.post("/trap/press", data={"i": 0, "round": t["round"]})   # chưa nhập tên: không bấm được
    assert t["loser"] == "P2"


def test_admin_skip_absent_player_once(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    turn = t["turn"]
    for _ in range(3):
        ad.post("/admin/trap/skip", data={"round": t["round"], "turn": turn})
    assert t["turn"] == turn + 1


def test_late_joiner_next_round(A):
    ad, ps = start(A)
    t = A.game_data["trap"]
    late = join(A, "Muộn")
    press(late, t, safe_slot(t))
    assert t["pressed"] == []
    press(ps[t["order"][0]], t, t["trap"])
    ad.post("/admin/trap/again", data={"round": t["round"]})
    assert "Muộn" in t["order"]


def test_game_always_ends_and_trap_uniform(A):
    players(A, "A1", "A2", "A3")
    ad = admin(A)
    ad.post("/admin/trap/start", data={"skin": "croc", "mode": "free"})
    t = A.game_data["trap"]
    hits = Counter()
    for _ in range(1200):
        A.trap_new_round(t)
        hits[t["trap"]] += 1
    assert set(hits) == set(range(12)) and min(hits.values()) > 50          # bẫy rải đều 12 răng
    A.trap_new_round(t)
    c = A.app.test_client()
    c.post("/join", data={"username": "Z"})
    for i in range(12):
        c.post("/trap/press", data={"i": i, "round": t["round"]})
        if t["phase"] == "BOOM":
            break
    assert t["phase"] == "BOOM"


def test_fruit_avatars(A):
    assert len(A.AVATARS) == 40 and len(set(A.AVATARS)) == 40
    for fruit in ("🍎", "🍉", "🍓", "🫐", "🥭"):
        assert fruit in A.AVATARS
    c = join(A, "Duy", avatar="🍉")
    assert A.game_data["players"]["Duy"]["avatar"] == "🍉"
    assert 'value="🍉" disabled' in html(A.app.test_client())


def test_delayed_new_round_tap_does_not_skip_result(A):
    """Bấm 'Ván mới' bị mạng chậm, tới nơi khi ván sau đã sập bẫy: không được xoá kết quả ván đó."""
    ad, ps = start(A)
    t = A.game_data["trap"]
    press(ps[t["order"][0]], t, t["trap"])
    old = t["round"]
    ad.post("/admin/trap/again", data={"round": old})
    press(ps[t["order"][0]], t, t["trap"])                 # ván mới sập bẫy ngay
    loser = t["loser"]
    ad.post("/admin/trap/again", data={"round": old})      # cú bấm cũ tới muộn
    assert t["round"] == old + 1 and t["phase"] == "BOOM" and t["loser"] == loser


def test_penalty_modes_chosen_at_start(A):
    players(A, "A1", "A2", "A3")
    ad = admin(A)
    cases = [({"pen": "beer"}, lambda p: p == A.TRAP_BEER),
             ({"pen": "custom", "pen_text": "Hát một bài do cả phòng chọn"}, lambda p: p == "Hát một bài do cả phòng chọn"),
             ({"pen": "custom", "pen_text": "  "}, lambda p: p in A.TOD_PENALTY),       # bỏ trống thì về thử thách
             ({"pen": "challenge"}, lambda p: p in A.TOD_PENALTY),
             ({}, lambda p: p in A.TOD_PENALTY)]
    for extra, ok in cases:
        ad.post("/admin/lobby")
        ad.post("/admin/trap/start", data=dict({"skin": "croc", "mode": "free", "gid": A.game_data["game_id"]}, **extra))
        t = A.game_data["trap"]
        for r in range(2):                                  # áp cho mọi ván
            ad.post("/trap/press", data={"i": t["trap"], "round": t["round"]})
            assert ok(t["penalty"]), (extra, t["penalty"])
            page = html(ad, "/admin")
            assert "Hình phạt cho" in page
            ad.post("/admin/trap/again", data={"round": t["round"]})
    beer_page = None
    ad.post("/admin/lobby")
    ad.post("/admin/trap/start", data={"skin": "pirate", "mode": "free", "pen": "beer", "gid": A.game_data["game_id"]})
    assert "uống bia" in html(ad, "/admin")
