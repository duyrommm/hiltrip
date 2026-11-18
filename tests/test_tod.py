"""Thật hay Thách."""
from collections import Counter

from conftest import admin, html, players


def test_full_round_and_fair_spins(A):
    ps = players(A, "Duy", "Nhi", "Như", "Vàng", "Huy")
    ad = admin(A)
    ad.post("/admin/tod/start")
    t = A.game_data["tod"]
    picked = []
    for r in range(10):
        ps["Như"].post("/tod/spin")
        ps["Như"].post("/tod/spin")                       # bấm lặp không quay thêm
        assert t["spin_id"] == r + 1
        p = t["picked"]
        picked.append(p)
        other = [n for n in ps if n != p][0]
        ps[other].post("/tod/choose", data={"kind": "dare"})
        assert t["phase"] == "CHOOSE"                     # người khác không chọn hộ
        ps[p].post("/tod/choose", data={"kind": "truth" if r % 2 else "dare"})
        old = t["card"]
        ps[p].post("/tod/swap")
        assert t["card"] != old and t["swapped"]
        if r == 3:
            ps[p].post("/tod/skip")
            assert t["phase"] == "PENALTY" and t["penalty"] in A.TOD_PENALTY
        ps[p].post("/tod/done")
        assert t["phase"] == "SPIN"
    c = Counter(picked)
    assert max(c.values()) - min(c.values()) <= 1
    assert all(picked[i] != picked[i + 1] for i in range(len(picked) - 1))


def test_wheel_lands_on_picked_player(A):
    players(A, "Duy", "Nhi", "Như", "Vàng", "Huy", "Lan", "Bình")
    ad = admin(A)
    ad.post("/admin/tod/start")
    t = A.game_data["tod"]
    for who in ["Vàng", "Bình", "Duy"]:
        ad.post("/admin/tod/spin", data={"who": who})
        seg = 360 / len(t["wheel"])
        i = t["wheel"].index(who)
        pointer = (-t["deg"]) % 360
        assert i * seg <= pointer <= (i + 1) * seg
        ad.post("/tod/choose", data={"kind": "truth"})
        ad.post("/tod/done")


def test_custom_question_first_and_end_awards(A):
    ps = players(A, "Duy", "Nhi", "Như")
    ad = admin(A)
    ad.post("/admin/tod/start")
    ad.post("/admin/tod/custom", data={"kind": "dare", "text": "Kể vụ lạc đường đi chợ"})
    ps["Duy"].post("/tod/spin")
    p = A.game_data["tod"]["picked"]
    ps[p].post("/tod/choose", data={"kind": "dare"})
    assert A.game_data["tod"]["card"] == "Kể vụ lạc đường đi chợ"
    ps[p].post("/tod/done")
    ad.post("/admin/tod/end")
    assert "Bảng vàng" in html(ps["Nhi"]) and "Dũng cảm nhất" in html(ps["Nhi"])


def test_double_tap_start(A):
    players(A, "Duy", "Nhi")
    ad = admin(A)
    g = A.game_data["game_id"]
    for _ in range(3):
        ad.post("/admin/tod/start", data={"gid": g})
    assert A.game_data["game_id"] == g + 1
