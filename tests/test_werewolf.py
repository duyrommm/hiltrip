"""Ma Sói: chia vai, thắng thua, biểu quyết, các vai đặc biệt, chống bấm lặp."""
from collections import Counter

from conftest import admin, join, html, players, set_roles

QUIET = "Hãy im lặng giúp trò chơi vui hơn"


def start(A, n, roles):
    names = [f"P{i}" for i in range(n)]
    ps = players(A, *names)
    ad = admin(A)
    ad.post("/admin/assign")
    mapping = {nm: "Villager" for nm in names}
    mapping.update(roles)
    set_roles(A, mapping)
    return ad, ps


def test_role_distribution(A):
    expect = {3: {"Wolf": 1}, 4: {"Wolf": 1, "Seer": 1}, 6: {"Wolf": 1, "Seer": 1, "Hunter": 1},
              8: {"Wolf": 1, "Seer": 1, "Guard": 1, "Witch": 1},
              10: {"Wolf": 2, "Seer": 1, "Guard": 1, "Witch": 1, "Hunter": 1},
              12: {"Wolf": 2, "Seer": 1, "Guard": 1, "Witch": 1, "Hunter": 1, "Idiot": 1}}
    for n, special in expect.items():
        A.game_data["players"] = {}
        players(A, *[f"P{i}" for i in range(n)])
        admin(A).post("/admin/assign")
        got = Counter(i["role"] for i in A.game_data["players"].values())
        for r, k in special.items():
            assert got[r] == k, (n, got)
        assert sum(got.values()) == n


def test_wolf_wins_on_parity(A):
    ad, ps = start(A, 3, {"P0": "Wolf"})
    ps["P0"].post("/action", data={"target": "P1"})
    ad.post("/admin/next_phase/DAY")
    assert A.game_data["phase"] == "END" and A.game_data["winner"] == "Wolf"
    assert "Sói thắng" in html(ps["P2"])


def test_lynch_reveals_role_and_village_wins(A):
    ad, ps = start(A, 5, {"P0": "Wolf"})
    ad.post("/admin/next_phase/DAY")
    ad.post("/admin/open_vote")
    for n in ["P1", "P2", "P3", "P4"]:
        ps[n].post("/vote", data={"target": "P0"})
    ad.post("/admin/close_vote")
    assert "P0 là 🐺 Ma Sói" in A.game_data["vote_result"]
    assert A.game_data["winner"] == "Village"


def test_tie_vote_nobody_dies(A):
    ad, ps = start(A, 5, {"P0": "Wolf"})
    ad.post("/admin/next_phase/DAY")
    ad.post("/admin/open_vote")
    ps["P1"].post("/vote", data={"target": "P2"})
    ps["P2"].post("/vote", data={"target": "P1"})
    ad.post("/admin/close_vote")
    assert "Hoà" in A.game_data["vote_result"] and all(i["alive"] for i in A.game_data["players"].values())


def test_only_wolf_can_bite_and_dead_cannot_vote(A):
    ad, ps = start(A, 5, {"P0": "Wolf"})
    ps["P1"].post("/action", data={"target": "P2"})
    assert not A.game_data["players"]["P1"]["acted"]
    ps["P0"].post("/action", data={"target": "P1"})
    ad.post("/admin/next_phase/DAY")
    assert QUIET in html(ps["P1"])
    ad.post("/admin/open_vote")
    ps["P1"].post("/vote", data={"target": "P0"})
    assert A.game_data["players"]["P1"]["vote"] is None


def test_seer_and_guard(A):
    ad, ps = start(A, 7, {"P0": "Wolf", "P1": "Seer", "P2": "Guard"})
    ps["P1"].post("/action", data={"target": "P0"})
    assert "P0 là Sói" in html(ps["P1"])
    ps["P2"].post("/action", data={"target": "P3"})
    ps["P0"].post("/action", data={"target": "P3"})
    ad.post("/admin/next_phase/DAY")
    assert A.game_data["players"]["P3"]["alive"]                      # được bảo vệ
    ad.post("/admin/next_night")
    ps["P2"].post("/action", data={"target": "P3"})
    assert not A.game_data["players"]["P2"]["acted"]                  # không bảo vệ 2 đêm liền


def test_witch_save_then_poison(A):
    ad, ps = start(A, 7, {"P0": "Wolf", "P1": "Witch"})
    assert "Chờ Sói chọn xong" in html(ps["P1"])
    ps["P0"].post("/action", data={"target": "P2"})
    ps["P1"].post("/witch", data={"save": "1"})
    ad.post("/admin/next_phase/DAY")
    assert A.game_data["players"]["P2"]["alive"] and not A.game_data["players"]["P1"]["heal"]
    ad.post("/admin/next_night")
    ps["P0"].post("/action", data={"target": "P3"})
    ps["P1"].post("/witch", data={"kill": "P0"})
    ad.post("/admin/next_phase/DAY")
    assert A.game_data["winner"] == "Village"


def test_hunter_shoots_after_lynch(A):
    ad, ps = start(A, 6, {"P0": "Wolf", "P1": "Hunter"})
    ad.post("/admin/next_phase/DAY")
    ad.post("/admin/open_vote")
    for n in ["P0", "P2", "P3", "P4", "P5"]:
        ps[n].post("/vote", data={"target": "P1"})
    ad.post("/admin/close_vote")
    assert A.game_data["phase"] == "HUNTER"
    ps["P2"].post("/shoot", data={"target": "P0"})                    # người khác không bắn hộ
    assert A.game_data["phase"] == "HUNTER"
    ps["P1"].post("/shoot", data={"target": "P0"})
    assert A.game_data["winner"] == "Village"


def test_hunter_can_skip(A):
    ad, ps = start(A, 6, {"P0": "Wolf", "P1": "Hunter"})
    ps["P0"].post("/action", data={"target": "P1"})
    ad.post("/admin/next_phase/DAY")
    ps["P1"].post("/shoot", data={"target": "__none__"})
    assert A.game_data["phase"] == "DAY" and "không bắn ai" in A.game_data["night_result"]


def test_idiot_survives_first_lynch(A):
    ad, ps = start(A, 11, {"P0": "Wolf", "P1": "Idiot"})
    for _ in range(2):
        ad.post("/admin/next_phase/DAY")
        ad.post("/admin/open_vote")
        if A.game_data["players"]["P1"].get("revealed"):
            ps["P1"].post("/vote", data={"target": "P0"})
            assert A.game_data["players"]["P1"]["vote"] is None       # đã lộ: không được bầu
        for n in [f"P{i}" for i in range(2, 11)]:
            ps[n].post("/vote", data={"target": "P1"})
        ad.post("/admin/close_vote")
        if A.game_data["players"]["P1"]["alive"]:
            assert "Thằng Ngốc" in A.game_data["vote_result"]
            ad.post("/admin/next_night")
    assert not A.game_data["players"]["P1"]["alive"]


def test_double_tap_assign_makes_one_game(A):
    players(A, "A1", "A2", "A3", "A4")
    ad = admin(A)
    g = A.game_data["game_id"]
    for _ in range(3):
        ad.post("/admin/assign", data={"gid": g})
    assert A.game_data["game_id"] == g + 1
