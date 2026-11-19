"""Kiểm tra trên trình duyệt thật, giả lập iPhone. Chạy:  python tests/e2e_mobile.py
Cần: pip install -r requirements-e2e.txt && python -m playwright install chromium (Python 64-bit)"""
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("E2E_PORT", "5099"))
B = f"http://localhost:{PORT}"
IPHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/120.0 Mobile/15E148 Safari/604.1")
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("  ĐẠT  " if ok else "  LỖI  ") + name + (f"  ({detail})" if detail and not ok else ""))


def state(page):
    return page.evaluate("fetch('/state').then(r=>r.json()).then(j=>j.v)")


def picker_checks(br, phone):
    """Trang Random."""
    import json
    _, pg = phone()
    pg.goto(B + "/picker")
    items = pg.evaluate("document.querySelectorAll('.chip').length")

    # P1. chạm thật vào nút Quay: vòng quay chạy, tên công bố đúng ô dưới mũi tên
    pg.tap("#spin")
    pg.wait_for_timeout(4800)
    name = pg.locator("#wres").get_attribute("data-name")
    check("Random: chạm Quay! thì quay, kết quả đúng ô dưới mũi tên", bool(name) and name == pg.evaluate("__wheelAt()"), name)

    # P2. quay liên tiếp + bỏ người đã trúng (tắt hiệu ứng cho nhanh)
    ctx = br.new_context(**IPHONE, reduced_motion="reduce")
    q = ctx.new_page()
    q.goto(B + "/picker")
    q.evaluate("localStorage.setItem('hiltrip_picker_v1', JSON.stringify({items:['A','B','C','D','E','F','G']}))")
    q.reload()
    q.check("#wremove")
    ok, seen = True, []
    for i in range(6):
        q.tap("#spin")
        q.wait_for_timeout(1100)
        n = q.locator("#wres").get_attribute("data-name")
        left = q.evaluate("document.querySelectorAll('.chip').length")
        ok = ok and n not in seen and left == 7 - (i + 1)
        seen.append(n)
    check("Random: quay 6 lần, bỏ người đã trúng, không ai trúng 2 lần", ok, seen)

    # P3. thuật toán chia team, thử mọi số người 2..16
    bad = q.evaluate("""() => { const errs = [];
      for (let n = 2; n <= 16; n++) { const L = Array.from({length:n}, (_, i) => 'p' + i);
        for (const size of [2,3,4,5]) { if (n < size * 2) continue; const r = __splitTeams(L, size, 0);
          const all = r.teams.flat().concat(r.bench);
          if (r.teams.length !== Math.floor(n/size) || r.teams.some(t => t.length !== size) || r.bench.length !== n % size
              || all.length !== n || new Set(all).size !== n) errs.push('size ' + n + '/' + size); }
        for (const c of [2,3,4]) { if (n < c) continue; const r = __splitTeams(L, 0, c); const all = r.teams.flat();
          const sz = r.teams.map(t => t.length);
          if (r.teams.length !== c || Math.max(...sz) - Math.min(...sz) > 1 || all.length !== n || new Set(all).size !== n || r.bench.length) errs.push('count ' + n + '/' + c); } }
      return errs; }""")
    check("Random: chia team đúng cho mọi số người 2–16 (2vs2..5vs5, 2–4 đội)", not bad, bad[:5])

    # P4. bấm chia đội trên giao diện; thiếu người thì báo rõ
    pg.click("button[data-tab=team]")
    pg.click("button[data-size='5']")                      # cần 10 người, phòng thử ít hơn
    warned = "Cần ít nhất 10 mục" in pg.content()
    size = 3 if items >= 6 else 2
    pg.click(f"button[data-size='{size}']")
    r = json.loads(pg.locator("#teams").get_attribute("data-teams"))
    shown = pg.locator("#teams .team").count()
    check(f"Random: {size} vs {size} hiện đúng số đội; thiếu người thì báo", warned and shown == len(r["teams"]) == items // size
          and all(len(t) == size for t in r["teams"]) and len(r["bench"]) == items % size)

    # P5. bốc thăm không trùng, không vượt số người
    pg.click("button[data-tab=draw]")
    for _ in range(20):
        pg.click("#dplus")
    pg.click("#drawbtn")
    pick = json.loads(pg.locator("#draws").get_attribute("data-pick"))
    check("Random: bốc thăm không trùng, tối đa bằng số người", len(pick) == items and len(set(pick)) == items)

    # P6. xúc xắc và đồng xu đúng miền giá trị
    pg.click("button[data-tab=dice]")
    pg.click("[data-dice='3']")
    okd = True
    for _ in range(3):
        pg.click("#rollbtn")
        pg.wait_for_timeout(800)
        v = json.loads(pg.locator("#dice").get_attribute("data-vals"))
        okd = okd and len(v) == 3 and all(1 <= x <= 6 for x in v) and str(sum(v)) in pg.locator("#dsum").inner_text()
    pg.click("button[data-tab=coin]")
    vals = set()
    for _ in range(4):
        pg.click("#flipbtn")
        pg.wait_for_timeout(900)
        vals.add(pg.locator("#coinres").get_attribute("data-v"))
    import re
    counts = [int(x) for x in re.findall(r"\d+", pg.locator("#coinhist").inner_text())]
    check("Random: xúc xắc 1–6, tổng đúng; đồng xu chỉ Ngửa/Sấp, đếm đủ 4 lần", okd and vals <= {"H", "T"} and sum(counts) == 4, (okd, vals, counts))

    # P7. có người mới vào phòng (trạng thái đổi) nhưng trang Random không bị làm mới mất kết quả
    pg.click("button[data-tab=wheel]")
    _, newbie = phone()
    newbie.goto(B)
    newbie.fill("input[name=username]", "Người mới")
    newbie.click("button:has-text('Vào phòng')")
    pg.wait_for_timeout(4500)
    check("Random: trạng thái phòng đổi, kết quả trên trang vẫn giữ", pg.locator("#wres").get_attribute("data-name") == name)

    # P8. danh sách thêm/bớt và còn sau khi tải lại
    pg.evaluate("document.getElementById('plist').open = true")
    pg.fill("#padd", "Lẩu")
    pg.click("#paddbtn")
    pg.reload()
    check("Random: thêm 'Lẩu' vào danh sách và còn sau khi tải lại", "Lẩu" in (pg.locator("#chips").text_content() or ""))

    # P9. trình duyệt chặn lưu trữ (chế độ riêng tư) vẫn chạy
    c3 = br.new_context(**IPHONE, reduced_motion="reduce")
    c3.add_init_script("Object.defineProperty(window, 'localStorage', {get(){ throw new Error('blocked'); }});")
    z = c3.new_page()
    errs = []
    z.on("pageerror", lambda e: errs.append(str(e)))
    z.goto(B + "/picker")
    z.tap("#spin")
    z.wait_for_timeout(1100)
    check("Random: trình duyệt chặn lưu trữ vẫn quay được", not errs and bool(z.locator("#wres").get_attribute("data-name")), errs)


def trap_checks(br, phone, adm):
    """Khám răng cá sấu / Đâm hải tặc trên 3 điện thoại."""
    post = lambda path, data=None: adm.evaluate(
        "([p, d]) => fetch(p, {method:'POST', body:new URLSearchParams(d || {})}).then(r => r.status)", [path, data])
    post("/admin/reset")
    phones = {}
    for n, e in [("Ổi", "🍐"), ("Mít", "🥭"), ("Dừa", "🥥")]:
        ctx, pg = phone()
        pg.add_init_script("window.__vib = 0; navigator.vibrate = function(){ window.__vib++; return true; };")
        pg.goto(B)
        pg.click(f"label[title='{e}']")
        pg.fill("input[name=username]", n)
        pg.click("button:has-text('Vào phòng')")
        phones[n] = pg
    gid = adm.evaluate("fetch('/admin').then(r => r.text()).then(t => t.match(/name=\"gid\" value=\"(\\d+)\"/)[1])")
    post("/admin/trap/start", {"skin": "croc", "mode": "turn", "gid": gid})
    for pg in phones.values():
        pg.goto(B)

    def who_turn():
        for n, pg in phones.items():
            pg.reload()
            if "Đến lượt bạn" in pg.content():
                return n
        return None

    cur = who_turn()
    others = [n for n in phones if n != cur]
    dis = phones[others[0]].locator(".tooth[disabled]").count()
    check("Cá sấu: chưa tới lượt thì không bấm được răng nào", dis == 12, dis)

    # răng hàng trên ở giữa nằm ngay dưới hình cá sấu: chạm thật phải ăn
    v0 = state(phones[cur])
    tooth = phones[cur].locator(".teeth.top .tooth").nth(2)
    bb = tooth.bounding_box()
    edge = {"x": bb["width"] / 2, "y": 4}                    # sát mép trên: chỗ hình cá sấu đè lên
    hit = phones[cur].evaluate("([x,y]) => { const e = document.elementFromPoint(x,y); return e ? (e.closest('.tooth') ? 'tooth' : e.className || e.tagName) : null }",
                               [bb["x"] + edge["x"], bb["y"] + edge["y"]])
    check("Cá sấu: điểm chạm ở mép răng thuộc về răng, không bị hình che", hit == "tooth", hit)
    phones[cur].touchscreen.tap(bb["x"] + edge["x"], bb["y"] + edge["y"])
    phones[cur].touchscreen.tap(bb["x"] + edge["x"], bb["y"] + edge["y"])   # chạm dồn lần 2
    phones[cur].wait_for_timeout(1500)
    dv = state(phones[cur]) - v0
    check("Cá sấu: chạm răng nằm dưới hình cá sấu vẫn ăn, chạm dồn chỉ tính 1", dv == 1, dv)

    watcher = phones[others[1]]
    watcher.wait_for_timeout(2600)
    synced = watcher.locator(".tooth.down").count() == 1 or "CẮN!" in watcher.content()
    check("Cá sấu: máy khác thấy răng vừa ấn sau vài giây (đồng bộ)", synced)

    # ép sập bẫy: quản trò ấn hộ lần lượt các răng
    for i in range(12):
        st = adm.evaluate("fetch('/admin').then(r => r.text())")
        if "Ván mới" in st:
            break
        rnd = adm.evaluate("fetch('/admin').then(r => r.text()).then(t => (t.match(/name=\"round\" value=\"(\\d+)\"/) || [])[1])")
        post("/trap/press", {"i": str(i), "round": rnd})
    watcher.wait_for_timeout(3000)
    vw = watcher.evaluate("[innerWidth, document.documentElement.scrollWidth]")
    check("Cá sấu: khi cắn màn hình không bị rộng ra / thu nhỏ", vw[0] <= 390 and vw[1] <= 390, vw)
    check("Cá sấu: sập bẫy thì mọi máy hiện CẮN! và rung", "CẮN!" in watcher.content() and watcher.evaluate("window.__vib") > 0,
          watcher.evaluate("window.__vib"))

    # hải tặc
    post("/admin/lobby")
    gid = adm.evaluate("fetch('/admin').then(r => r.text()).then(t => t.match(/name=\"gid\" value=\"(\\d+)\"/)[1])")
    post("/admin/trap/start", {"skin": "pirate", "mode": "free", "gid": gid})
    pg = phones[others[0]]
    pg.goto(B)
    v0 = state(pg)
    hb = pg.locator(".hole").nth(1).bounding_box()
    gap = pg.evaluate("() => { const h = document.querySelector('.pirate-head .hat').getBoundingClientRect(); const t = [...document.querySelectorAll('#app p, #app h1, #app h2')].filter(e => e.getBoundingClientRect().bottom <= h.bottom).pop(); return t ? Math.round(h.top - t.getBoundingClientRect().bottom) : 99 }")
    check("Hải tặc: mũ không đè lên chữ phía trên", gap >= 0, gap)
    hit = pg.evaluate("([x,y]) => { const e = document.elementFromPoint(x,y); return e ? (e.closest('.hole') ? 'hole' : e.className || e.tagName) : null }",
                      [hb["x"] + hb["width"] / 2, hb["y"] + 4])
    check("Hải tặc: điểm chạm ở mép lỗ thuộc về lỗ, không bị hình che", hit == "hole", hit)
    pg.touchscreen.tap(hb["x"] + hb["width"] / 2, hb["y"] + 4)                  # sát mép trên, dưới hình hải tặc
    pg.wait_for_timeout(1500)
    check("Hải tặc: chạm lỗ hàng trên (dưới hình hải tặc) vẫn ăn", state(pg) - v0 == 1)


def main():
    env = dict(os.environ, PORT=str(PORT), ADMIN_KEY="k")
    env.pop("DATABASE_URL", None)
    srv = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(B + "/state", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()

            def phone():
                ctx = br.new_context(**IPHONE)
                pg = ctx.new_page()
                pg.on("dialog", lambda d: d.dismiss())        # như Chrome iOS / Zalo chặn hộp thoại
                return ctx, pg

            actx, adm = phone()
            adm.goto(B + "/admin?key=k")
            pages = {}
            for n, e in [("Duy", "🦀"), ("Nhi", "🐙"), ("Như", "🐬"), ("Vàng", "🐢"), ("Huy", "🦈")]:
                ctx, pg = phone()
                pg.goto(B)
                pg.click(f"label[title='{e}']")
                pg.fill("input[name=username]", n)
                pg.click("button:has-text('Vào phòng')")
                pages[n] = (ctx, pg)

            # 1. cookie phiên giữ ≥ 6 ngày (tắt trình duyệt mở lại vẫn còn)
            ck = [c for c in pages["Như"][0].cookies() if c["name"] == "session"][0]
            check("Cookie phiên giữ lâu ngày", ck["expires"] > time.time() + 6 * 86400)

            # 2. bấm dồn 3 lần lúc mạng chậm chỉ tạo 1 ván
            adm.goto(B + "/admin")
            adm.route("**/admin/spy/start", lambda r: (adm.wait_for_timeout(1500), r.continue_()))
            v0 = state(adm)
            for _ in range(3):
                try:
                    adm.locator("button:has-text('Chơi Truy Tìm Gián Điệp')").click(timeout=700, force=True)
                except Exception:
                    pass
            adm.wait_for_timeout(2500)
            adm.unroute("**/admin/spy/start")
            check("Bấm dồn 3 lần khi mạng chậm = 1 ván", state(adm) - v0 == 1, f"{state(adm) - v0} lần")

            # 3. tải lại trang vẫn đúng từ khoá
            pg = pages["Như"][1]
            pg.goto(B)
            pg.click(".card-role")
            w1 = pg.locator(".card-role .rname").inner_text()
            pg.reload()
            pg.click(".card-role")
            check("Tải lại trang vẫn đúng từ khoá", pg.locator(".card-role .rname").inner_text() == w1)

            # 4. nút Kết thúc khi trình duyệt chặn hộp thoại: chạm 2 lần
            adm.goto(B + "/admin")
            v0 = state(adm)
            adm.click("button:has-text('Kết thúc ván')")
            adm.wait_for_timeout(400)
            one = state(adm) == v0
            adm.click("button.armed")
            adm.wait_for_timeout(1200)
            check("Kết thúc ván: chạm 1 lần chưa chạy, chạm 2 lần chạy", one and state(adm) != v0)

            # 5. trùng tên khi người kia đang online: bị chặn
            ctx2, other = phone()
            other.goto(B)
            other.fill("input[name=username]", "Như")
            other.click("button:has-text('Vào phòng')")
            check("Trùng tên người đang online bị chặn", "đang có người dùng" in other.content())

            # 6. máy cũ tắt hẳn, mở trên trình duyệt khác: hỏi xác nhận rồi vào đúng người; máy cũ bị đưa ra
            old_ctx, old = pages["Như"]
            old.goto("about:blank")                              # máy cũ không còn mở app
            time.sleep(26)
            other.goto(B)
            other.fill("input[name=username]", "Như")
            other.click("button:has-text('Vào phòng')")
            asked = "Bạn vào lại?" in other.content()
            other.click("button:has-text('Đúng, là tôi')")
            other.wait_for_timeout(500)
            inside = "Vào phòng chơi" not in other.content()
            check("Đổi trình duyệt khi máy cũ đã tắt: hỏi rồi vào lại", asked and inside)
            old.goto(B)
            check("Máy cũ mở lại thì về trang nhập tên", "Vào phòng chơi" in old.content())

            picker_checks(br, phone)
            trap_checks(br, phone, adm)
            br.close()
    finally:
        srv.terminate()
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} đạt")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
