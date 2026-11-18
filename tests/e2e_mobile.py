"""Kiểm tra trên trình duyệt thật, giả lập iPhone. Chạy:  python tests/e2e_mobile.py
Cần: pip install playwright && python -m playwright install chromium"""
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

            br.close()
    finally:
        srv.terminate()
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} đạt")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
