"""Truy Tìm Gián Điệp."""
from collections import Counter

from conftest import admin, html, players


def roster_roles(A):
    return Counter(i["role"] for i in A.game_data["spy"]["roster"].values())


def vote_out(A, ad, ps, target):
    ad.post("/admin/spy/open_vote")
    for n, i in A.game_data["spy"]["roster"].items():
        if i["alive"] and n != target:
            ps[n].post("/spy/vote", data={"target": target})
    ad.post("/admin/spy/close_vote")


def test_role_counts_and_speaking_order(A):
    for n, (spy, white) in {4: (1, 0), 7: (1, 1), 10: (2, 1), 13: (3, 1)}.items():
        A.game_data["players"] = {}
        players(A, *[f"P{i}" for i in range(n)])
        admin(A).post("/admin/spy/start")
        r = roster_roles(A)
        assert r["spy"] == spy and r["white"] == white
        sp = A.game_data["spy"]
        assert sp["roster"][sp["order"][0]]["role"] != "white"            # Mũ Trắng không nói đầu


def test_spy_does_not_know_and_civ_wins(A):
    ps = players(A, *[f"P{i}" for i in range(6)])
    ad = admin(A)
    ad.post("/admin/spy/start", data={"civ": "Cà phê", "spy": "Trà sữa"})
    sp = A.game_data["spy"]
    s = [n for n, i in sp["roster"].items() if i["role"] == "spy"][0]
    c = [n for n, i in sp["roster"].items() if i["role"] == "civ"]
    assert "Trà sữa" in html(ps[s]) and "Cà phê" not in html(ps[s])
    vote_out(A, ad, ps, s)
    assert sp["winner"] == "civ" and "Dân thắng" in html(ps[c[0]])


def test_white_guess_ignores_accents(A):
    ps = players(A, *[f"P{i}" for i in range(8)])
    ad = admin(A)
    ad.post("/admin/spy/start", data={"civ": "Bánh xèo", "spy": "Bánh khọt"})
    w = [n for n, i in A.game_data["spy"]["roster"].items() if i["role"] == "white"][0]
    vote_out(A, ad, ps, w)
    other = [n for n in ps if n != w][0]
    ps[other].post("/spy/guess", data={"guess": "banh xeo"})
    assert A.game_data["spy"]["phase"] == "GUESS"
    ps[w].post("/spy/guess", data={"guess": "BANH XEO"})
    assert A.game_data["spy"]["winner"] == "white"


def test_tie_and_manual_end_and_quiet_note(A):
    ps = players(A, *[f"P{i}" for i in range(6)])
    ad = admin(A)
    ad.post("/admin/spy/start")
    names = list(A.game_data["spy"]["roster"])
    ad.post("/admin/spy/open_vote")
    v = html(ps[names[0]])
    assert '<option value="" disabled selected>Chọn người…</option>' in v       # không chọn sẵn ai
    ps[names[0]].post("/spy/vote", data={"target": names[1]})
    ps[names[1]].post("/spy/vote", data={"target": names[0]})
    ad.post("/admin/spy/close_vote")
    assert "Hoà" in A.game_data["spy"]["result"]
    civ = [n for n, i in A.game_data["spy"]["roster"].items() if i["role"] == "civ"][0]
    vote_out(A, ad, ps, civ)
    if A.game_data["spy"]["phase"] != "END":
        assert "Hãy im lặng giúp trò chơi vui hơn" in html(ps[civ])
        ad.post("/admin/spy/end")
    e = html(ps[names[2]])
    assert "Mũ Trắng thắng" not in e or A.game_data["spy"]["winner"] == "white"


def test_double_tap_start_keeps_same_words(A):
    ps = players(A, *[f"P{i}" for i in range(5)])
    ad = admin(A)
    g = A.game_data["game_id"]
    ad.post("/admin/spy/start", data={"gid": g})
    words = (A.game_data["spy"]["civ_word"], A.game_data["spy"]["spy_word"])
    for _ in range(3):
        ad.post("/admin/spy/start", data={"gid": g})
    assert (A.game_data["spy"]["civ_word"], A.game_data["spy"]["spy_word"]) == words
    assert A.game_data["game_id"] == g + 1


def test_word_pairs_clean(A):
    seen = set()
    for a, b in A.WORD_PAIRS:
        k = frozenset((A.norm(a), A.norm(b)))
        assert len(k) == 2 and k not in seen, (a, b)
        seen.add(k)
