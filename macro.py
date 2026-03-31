"""
플레이33 방탈출 예약 매크로
================================
미래 날짜도 OK! 오픈 시간(22:00)을 자동 계산해서 대기 후 즉시 예약.

사용법:
    pip3 install playwright && python3 -m playwright install chromium
    python3 macro.py

    # 한줄 명령 (미래 날짜 → 자동 대기):
    python3 macro.py --branch 대전점 --theme 자각몽 --date 2026-04-11 \
        --times "11:00,12:10" --name 홍길동 --phone 010-1234-5678 --people 2

예약 오픈 규칙:
    - 7일 전부터 예약 가능
    - 건대점/홍대점: 오후 8시 오픈
    - 대전점: 오전 10시 오픈
    - 예: 대전점 4월 11일 → 4월 4일 10:00 오픈
    - 예: 건대점 4월 11일 → 4월 4일 20:00 오픈
"""

import argparse
import time
import sys
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# ── 지점 매핑 ──
BRANCHES = {
    "건대점": "1",
    "홍대점": "4",
    "대전점": "5",
}

BASE_URL = "https://play33.kr"

# ── 예약 오픈 규칙 (사이트 JS에서 확인) ──
RESERVATION_RANGE_DAYS = 7

# 지점별 오픈 시간
BRANCH_OPEN_TIMES = {
    "1": "20:00:00",  # 건대점: 오후 8시
    "4": "20:00:00",  # 홍대점: 건대점과 동일 (확인 필요)
    "5": "10:00:00",  # 대전점: 오전 10시
}


def log(msg):
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"  [{now}] {msg}")


def calc_open_datetime(target_date_str, branch_id):
    """타겟 날짜의 예약 오픈 일시를 계산.
    건대점: 7일 전 20:00, 대전점: 7일 전 10:00"""
    target = datetime.strptime(target_date_str, "%Y-%m-%d")
    open_date = target - timedelta(days=RESERVATION_RANGE_DAYS)
    open_time = BRANCH_OPEN_TIMES.get(branch_id, "20:00:00")
    open_dt = datetime.strptime(
        f"{open_date.strftime('%Y-%m-%d')} {open_time}",
        "%Y-%m-%d %H:%M:%S",
    )
    return open_dt


def wait_until(target_dt):
    """지정 datetime까지 대기"""
    while True:
        remaining = (target_dt - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        if remaining > 60:
            log(f"오픈까지 {remaining / 60:.1f}분 ({target_dt.strftime('%m/%d %H:%M:%S')})")
            time.sleep(min(remaining - 10, 30))
        elif remaining > 10:
            log(f"오픈까지 {remaining:.0f}초...")
            time.sleep(min(remaining - 3, 5))
        elif remaining > 0.05:
            time.sleep(0.01)
    log("오픈!")


def pick(prompt, options, label_fn):
    """목록에서 하나를 선택"""
    print(f"\n  {prompt}")
    for i, opt in enumerate(options):
        print(f"    {i + 1}) {label_fn(opt)}")
    while True:
        try:
            idx = int(input(f"\n  번호 선택 (1-{len(options)}): ").strip()) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except (ValueError, IndexError):
            pass
        print("  잘못된 입력!")


def pick_multi(prompt, options, label_fn):
    """목록에서 여러 개 선택 (쉼표 구분)"""
    print(f"\n  {prompt}")
    for i, opt in enumerate(options):
        print(f"    {i + 1}) {label_fn(opt)}")
    while True:
        try:
            raw = input(f"\n  번호 선택 (쉼표 구분, 예: 1,3): ").strip()
            indices = [int(x.strip()) - 1 for x in raw.split(",")]
            if all(0 <= idx < len(options) for idx in indices):
                return [options[idx] for idx in indices]
        except (ValueError, IndexError):
            pass
        print("  잘못된 입력!")


def create_browser():
    """Playwright 브라우저 + 페이지 생성"""
    p = sync_playwright().start()
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="ko-KR",
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false, configurable: true
        });
    """)
    page = context.new_page()
    return p, browser, page


def load_reservation_page(page, branch_id, date, fast=False):
    """예약 페이지 로드 → 테마 목록 + 시간 버튼 파싱"""
    url = f"{BASE_URL}/reservation?branch={branch_id}&date={date}"

    # 이전 네비게이션이 진행 중일 수 있으므로 안정화 대기
    try:
        page.wait_for_load_state("load", timeout=3000)
    except Exception:
        pass

    if fast:
        for attempt in range(3):
            try:
                page.goto(url, wait_until="commit")
                page.wait_for_selector(".eveReservationButton", timeout=5000)
                break
            except Exception as e:
                if attempt < 2:
                    log(f"  페이지 로드 재시도 ({attempt + 1}/3): {e}")
                    page.wait_for_timeout(500)
                else:
                    raise
    else:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=15000)

    # 테마 목록
    themes = page.evaluate("""
    () => {
        const sel = document.querySelector('select[name="theme"]');
        if (!sel) return [];
        return Array.from(sel.options)
            .filter(o => o.value)
            .map(o => ({ id: o.value, name: o.textContent.trim() }));
    }
    """)

    # 시간 버튼 (버튼 인덱스 포함)
    time_data = page.evaluate("""
    () => {
        const buttons = document.querySelectorAll('.eveReservationButton');
        const result = [];
        buttons.forEach((btn, idx) => {
            const jsonMatch = btn.textContent.match(/\\{.*\\}/);
            if (jsonMatch) {
                try {
                    const data = JSON.parse(jsonMatch[0]);
                    result.push({
                        ...data,
                        disabled: btn.disabled,
                        btnIndex: idx,
                    });
                } catch(e) {}
            }
        });
        return result;
    }
    """)

    return themes, time_data


def load_themes_only(page, branch_id):
    """오늘 날짜로 테마 목록만 조회 (미래 날짜 예약 시 테마 ID 확인용)"""
    today = datetime.now().strftime("%Y-%m-%d")
    themes, _ = load_reservation_page(page, branch_id, today)
    return themes


def book_single(page, branch_id, theme_id, date, time_str, name, phone, people, btn_index):
    """단일 예약 수행 (버튼 클릭 + 폼 입력 방식, CSRF/세션 안전지향)"""
    log(f"예약: {time_str}")
    t0 = time.time()

    # STEP 1: 시간 버튼 클릭으로 예약 페이지 진입 (기본 방식)
    # evaluate()는 내비게이션을 추적하지 않으므로 expect_navigation과 함께 사용
    try:
        with page.expect_navigation(timeout=8000, wait_until="domcontentloaded"):
            clicked = page.evaluate(f"""
            () => {{
                const buttons = document.querySelectorAll('.eveReservationButton');
                const target = buttons[{btn_index}];
                if (target) {{
                    target.click();
                    return true;
                }}
                return false;
            }}
            """)
    except Exception as nav_err:
        log(f"  [경고] 내비게이션 대기 중 오류: {nav_err}")
        clicked = False

    if not clicked:
        log("  [경고] 버튼 클릭 실패: 폼 직접 제출로 대체 시도")
        try:
            with page.expect_navigation(timeout=8000, wait_until="domcontentloaded"):
                page.evaluate(f"""
                () => {{
                    const form = document.getElementById('eveSubmitForm');
                    if (form) {{
                        form.querySelector('input[name="branch"]').value = '{branch_id}';
                        form.querySelector('input[name="theme"]').value = '{theme_id}';
                        form.querySelector('input[name="date"]').value = '{date}';
                        form.querySelector('input[name="time"]').value = '{time_str}';
                        form.submit();
                    }}
                }}
                """)
        except Exception as nav_err:
            log(f"  [경고] 폼 제출 내비게이션 오류: {nav_err}")

    page.wait_for_selector("input[name='name']", timeout=8000)

    # CSRF 토큰 존재 확인 (실제로 이름입력 페이지에 들어왔으면 보통 토큰이 있다)
    page.wait_for_timeout(150)
    csrf_token = page.evaluate("() => document.querySelector('input[name=_token]')?.value || ''")
    if csrf_token:
        log("  CSRF 토큰 확인")
    else:
        log("  [주의] CSRF 토큰 미확인 (419 가능성) ")

    ms1 = f"{(time.time() - t0) * 1000:.0f}ms"
    log(f"  폼 진입 ({ms1})")

    # STEP 2: 모든 필드 입력 + 동의 체크 + submit
    try:
        page.fill("input[name='name']", name)
        page.fill("input[name='phone']", phone)
    except Exception as e:
        log(f"  입력 필드 채우기 실패: {e}")

    # 인원 선택
    try:
        if page.query_selector('#evePeople'):
            page.select_option('#evePeople', str(people))
        elif page.query_selector("select[name='people']"):
            page.select_option("select[name='people']", str(people))
    except Exception as e:
        log(f"  인원 선택 실패: {e}")

    # 개인정보 처리방침 동의 체크 (다양한 셀렉터 시도)
    try:
        checked = page.evaluate("""
        () => {
            // 1) name 기반 셀렉터 (policy, agree, privacy 등)
            const nameSelectors = [
                'input[name="policy"]',
                'input[name="agree"]',
                'input[name="privacy"]',
                'input[name="agreement"]',
                'input[name="personal"]',
                'input[name="check"]',
            ];
            for (const sel of nameSelectors) {
                const el = document.querySelector(sel);
                if (el && el.type === 'checkbox') {
                    el.checked = true;
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return `name: ${sel}`;
                }
            }

            // 2) 개인정보/동의 텍스트 근처의 체크박스
            const allCheckboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of allCheckboxes) {
                const parent = cb.closest('label, div, li, p, span');
                const text = parent ? parent.textContent : '';
                if (text.includes('개인정보') || text.includes('동의') || text.includes('약관') || text.includes('처리방침')) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                    return `text: ${text.trim().substring(0, 30)}`;
                }
            }

            // 3) 페이지에 체크박스가 하나뿐이면 그걸 체크
            if (allCheckboxes.length === 1) {
                allCheckboxes[0].checked = true;
                allCheckboxes[0].dispatchEvent(new Event('change', { bubbles: true }));
                return 'only-checkbox';
            }

            // 4) 모든 체크박스를 체크 (체크 안 된 것만)
            if (allCheckboxes.length > 0) {
                allCheckboxes.forEach(cb => {
                    if (!cb.checked) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                });
                return `all: ${allCheckboxes.length}개`;
            }

            return null;
        }
        """)
        if checked:
            log(f"  개인정보 동의 체크 완료 ({checked})")
        else:
            log("  [주의] 체크박스를 찾지 못함")
    except Exception as e:
        log(f"  동의 체크 실패: {e}")

    # 예약하기 버튼 클릭 (다양한 셀렉터 + 텍스트 매칭)
    try:
        clicked_btn = page.evaluate("""
        () => {
            // 1) submit 타입 버튼
            const submitBtn = document.querySelector("button[type='submit'], input[type='submit']");
            if (submitBtn) {
                submitBtn.click();
                return 'submit-type';
            }

            // 2) "예약" 텍스트가 포함된 버튼/링크
            const allButtons = document.querySelectorAll('button, a.btn, a[class*="btn"], input[type="button"]');
            for (const btn of allButtons) {
                const text = btn.textContent || btn.value || '';
                if (text.includes('예약') || text.includes('신청') || text.includes('확인') || text.includes('제출')) {
                    btn.click();
                    return `text: ${text.trim().substring(0, 20)}`;
                }
            }

            // 3) form.submit() fallback
            const form = document.querySelector('form[action*="reservation"]')
                      || document.querySelector('form[action*="create"]')
                      || document.querySelector('form');
            if (form) {
                form.submit();
                return 'form-submit';
            }

            return null;
        }
        """)
        if clicked_btn:
            log(f"  예약 버튼 클릭 ({clicked_btn})")
        else:
            log("  [경고] 예약 버튼을 찾지 못함")
    except Exception as e:
        log(f"  제출 시도 실패: {e}")


    # 결과 페이지 대기
    page.wait_for_load_state("commit", timeout=5000)

    total = f"{(time.time() - t0) * 1000:.0f}ms"
    current_url = page.url
    log(f"  제출 완료 ({total})")

    # 결과 확인
    try:
        page.wait_for_load_state("domcontentloaded", timeout=3000)
        result_text = page.text_content("body")
        lc_text = (result_text or "").lower()
        if "완료" in result_text or "confirm" in current_url.lower():
            log(f"  예약 성공! ({total})")
            return True
        elif "실패" in result_text or "이미" in result_text:
            log(f"  예약 실패 ({total})")
            return False
        elif "419" in result_text or "page expired" in lc_text:
            log(f"  419 Page Expired 감지 ({total})")
            return None
    except Exception:
        pass

    log(f"  결과 불확실 ({total}) - 브라우저 확인")
    return None


def is_date_bookable_now(target_date_str, branch_id):
    """해당 날짜가 지금 바로 예약 가능한지 (오픈 범위 내인지)"""
    open_dt = calc_open_datetime(target_date_str, branch_id)
    return datetime.now() >= open_dt


def run_booking(page, branch_id, theme_id, date, time_strs, name, phone, people):
    """시간 목록에 대해 순차 예약 수행."""
    # 속도 우선으로 페이지 로드
    _, time_data = load_reservation_page(page, branch_id, date, fast=True)

    # 테마에 해당하는 시간 매핑
    theme_time_map = {}
    for td in time_data:
        if str(td.get("theme")) == str(theme_id):
            theme_time_map[td["time"]] = td

    # 예약 가능 여부 체크
    for t in time_strs:
        if t not in theme_time_map:
            log(f"  {t} - 존재하지 않는 시간")
        elif theme_time_map[t]["disabled"]:
            log(f"  {t} - 매진")
        else:
            log(f"  {t} - 예약 가능")

    bookable = [t for t in time_strs if t in theme_time_map and not theme_time_map[t]["disabled"]]
    if not bookable:
        print("\n  예약 가능한 시간이 없습니다!")
        return []

    results = []
    for i, time_str in enumerate(bookable):
        if i > 0:
            log("예약 페이지로 복귀...")
            _, refreshed = load_reservation_page(page, branch_id, date, fast=True)
            for td in refreshed:
                if str(td.get("theme")) == str(theme_id) and td["time"] == time_str:
                    theme_time_map[time_str] = td
                    break

        btn_index = theme_time_map[time_str]["btnIndex"]

        result = None
        attempts = 0
        while attempts < 2 and result is not True and result is not False:
            result = book_single(
                page, branch_id, theme_id,
                date, time_str, name, phone, people,
                btn_index,
            )
            if result is True or result is False:
                break

            attempts += 1
            log(f"  재시도 {attempts}/2 (419 or 불확실)")
            _, refreshed = load_reservation_page(page, branch_id, date, fast=True)
            for td in refreshed:
                if str(td.get("theme")) == str(theme_id) and td["time"] == time_str:
                    theme_time_map[time_str] = td
                    btn_index = td["btnIndex"]
                    break

        results.append((time_str, result))

    return results


def print_results(results):
    """예약 결과 출력"""
    print(f"""
  ══════════════════════════════════════════
  예약 결과:
""")
    for time_str, result in results:
        status = "성공" if result else ("실패" if result is False else "확인필요")
        print(f"    {time_str}: {status}")
    print("  ══════════════════════════════════════════\n")


def setup_interactive():
    """대화형 모드"""
    print("""
  ╔═══════════════════════════════════════════╗
  ║     플레이33 예약 매크로                  ║
  ╚═══════════════════════════════════════════╝
""")

    # 1) 지점
    branch_list = list(BRANCHES.items())
    branch = pick("지점 선택:", branch_list, lambda x: x[0])
    branch_name, branch_id = branch
    print(f"  → {branch_name}")

    # 2) 날짜
    date = input("\n  예약 날짜 (예: 2026-04-11): ").strip()

    # 3) 브라우저 시작
    pw, browser, page = create_browser()

    # 4) 테마 조회 (미래 날짜면 오늘 날짜로 조회)
    is_future = not is_date_bookable_now(date, branch_id)
    if is_future:
        open_dt = calc_open_datetime(date, branch_id)
        log(f"미래 날짜! 오픈 예정: {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        log("오늘 날짜로 테마 목록을 먼저 조회합니다...")
        themes = load_themes_only(page, branch_id)
    else:
        log("테마/시간 조회 중...")
        themes, time_data = load_reservation_page(page, branch_id, date)

    if not themes:
        print("  테마가 없습니다!")
        browser.close()
        pw.stop()
        sys.exit(1)

    # 5) 테마 선택
    theme = pick("테마 선택:", themes, lambda x: x["name"])
    theme_id = theme["id"]
    theme_name = theme["name"]
    print(f"  → {theme_name}")

    if is_future:
        # 미래 날짜: 시간은 오픈 전에 알 수 없으므로 직접 입력
        print("\n  (미래 날짜라 시간표를 아직 조회할 수 없습니다)")
        print("  원하는 시간을 직접 입력하세요.")
        times_raw = input("  시간 (쉼표 구분, 예: 11:00,12:10): ").strip()
        chosen_time_strs = [t.strip() for t in times_raw.split(",")]
    else:
        # 현재 예약 가능: 시간 목록에서 선택
        theme_times = [t for t in time_data if str(t.get("theme")) == str(theme_id)]
        available = [t for t in theme_times if not t["disabled"]]
        sold_out = [t for t in theme_times if t["disabled"]]

        if sold_out:
            print(f"\n  (매진: {', '.join(t['time'] for t in sold_out)})")
        if not available:
            print("  예약 가능한 시간이 없습니다!")
            browser.close()
            pw.stop()
            sys.exit(1)

        chosen = pick_multi("시간 선택 (복수 가능):", available, lambda x: x["time"])
        chosen_time_strs = [t["time"] for t in chosen]

    print(f"  → {', '.join(chosen_time_strs)}")

    # 6) 개인정보
    name = input("\n  이름: ").strip()
    phone = input("  전화번호 (하이픈 포함, 예: 010-1234-5678): ").strip()
    people = input("  인원수: ").strip()

    # 7) 요약
    if is_future:
        open_dt = calc_open_datetime(date, branch_id)
        remaining = (open_dt - datetime.now()).total_seconds()
        wait_info = f"예 → {open_dt.strftime('%m/%d %H:%M')} (약 {remaining / 3600:.1f}시간 후)"
    else:
        wait_info = "아니오 (바로 예약)"

    print(f"""
  ══════════════════════════════════════════
  지점: {branch_name}
  테마: {theme_name}
  날짜: {date}
  시간: {', '.join(chosen_time_strs)}
  이름: {name}
  전화: {phone}
  인원: {people}명
  오픈대기: {wait_info}
  ══════════════════════════════════════════
""")

    if input("  이대로 진행? (y/n): ").strip().lower() != "y":
        browser.close()
        pw.stop()
        return

    # 8) 오픈 대기 (미래 날짜)
    if is_future:
        open_dt = calc_open_datetime(date, branch_id)
        remaining = (open_dt - datetime.now()).total_seconds()
        if remaining > 0:
            # 대기 중엔 브라우저 끄기 (죽는 문제 방지)
            browser.close()
            pw.stop()
            log(f"오픈 대기 시작: {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            log("브라우저는 오픈 직전에 새로 띄웁니다.")
            wait_until(open_dt)

            # 오픈! 브라우저 새로 생성
            log("브라우저 시작...")
            pw, browser, page = create_browser()
        else:
            log("이미 오픈된 날짜입니다!")

    # 9) 예약 실행!
    log("매크로 발사!")
    results = run_booking(page, branch_id, theme_id, date, chosen_time_strs, name, phone, people)

    print_results(results)
    input("  >>> Enter로 브라우저 닫기...")
    browser.close()
    pw.stop()


def setup_cli(args):
    """CLI 모드"""
    branch_id = BRANCHES.get(args.branch)
    if not branch_id:
        print(f"  알 수 없는 지점: {args.branch}")
        print(f"  가능한 지점: {', '.join(BRANCHES.keys())}")
        sys.exit(1)

    times = [t.strip() for t in args.times.split(",")]
    is_future = not is_date_bookable_now(args.date, branch_id)

    if is_future:
        open_dt = calc_open_datetime(args.date, branch_id)
        remaining = (open_dt - datetime.now()).total_seconds()
        wait_info = f"{open_dt.strftime('%m/%d %H:%M')} (약 {max(remaining / 3600, 0):.1f}시간 후)"
    else:
        wait_info = "바로 예약"

    print(f"""
  ╔═══════════════════════════════════════════╗
  ║     플레이33 예약 매크로 (CLI 모드)       ║
  ╚═══════════════════════════════════════════╝

  지점: {args.branch}
  테마: {args.theme}
  날짜: {args.date}
  시간: {', '.join(times)}
  이름: {args.name}
  전화: {args.phone}
  인원: {args.people}명
  오픈: {wait_info}
""")

    pw, browser, page = create_browser()

    # 테마 검증 (오늘 날짜로)
    log("테마 목록 조회 중...")
    themes = load_themes_only(page, branch_id)

    theme_id = None
    theme_name = None
    for t in themes:
        if args.theme in t["name"]:
            theme_id = t["id"]
            theme_name = t["name"]
            break

    if not theme_id:
        print(f"  테마를 찾을 수 없습니다: {args.theme}")
        print(f"  가능한 테마: {', '.join(t['name'] for t in themes)}")
        browser.close()
        pw.stop()
        sys.exit(1)

    log(f"테마 매칭: {theme_name} (ID: {theme_id})")

    # 오픈 대기 (미래 날짜)
    if is_future:
        open_dt = calc_open_datetime(args.date, branch_id)
        remaining = (open_dt - datetime.now()).total_seconds()
        if remaining > 0:
            # 대기 중엔 브라우저 끄기 (죽는 문제 방지)
            browser.close()
            pw.stop()
            log(f"오픈 대기: {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            log("브라우저는 오픈 직전에 새로 띄웁니다.")
            wait_until(open_dt)

            # 오픈! 브라우저 새로 생성
            log("브라우저 시작...")
            pw, browser, page = create_browser()
    else:
        if not args.auto:
            input("\n  >>> Enter 누르면 매크로 발사!\n")

    # 예약 실행!
    log("매크로 발사!")
    results = run_booking(page, branch_id, theme_id, args.date, times, args.name, args.phone, args.people)

    print_results(results)

    if not args.auto:
        input("  >>> Enter로 브라우저 닫기...")
    browser.close()
    pw.stop()


def main():
    parser = argparse.ArgumentParser(
        description="플레이33 방탈출 예약 매크로",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 대화형 모드
  python3 macro.py

  # CLI 모드 - 지금 예약 가능한 날짜
  python3 macro.py --branch 대전점 --theme 자각몽 --date 2026-03-28 \\
      --times "11:00,12:10" --name 홍길동 --phone 010-1234-5678 --people 2

  # CLI 모드 - 미래 날짜 (오픈 시간에 자동 대기 후 예약)
  python3 macro.py --branch 대전점 --theme 자각몽 --date 2026-04-11 \\
      --times "11:00,12:10" --name 홍길동 --phone 010-1234-5678 --people 2

  # 완전 자동 (확인 프롬프트 없음)
  python3 macro.py --branch 대전점 --theme 자각몽 --date 2026-04-11 \\
      --times "11:00,12:10" --name 홍길동 --phone 010-1234-5678 --people 2 --auto
        """,
    )
    parser.add_argument("--branch", help="지점 (건대점/홍대점/대전점)")
    parser.add_argument("--theme", help="테마 이름 (부분 일치, 예: 자각몽)")
    parser.add_argument("--date", help="예약 날짜 (예: 2026-04-11)")
    parser.add_argument("--times", help="예약 시간 (쉼표 구분, 예: 11:00,12:10)")
    parser.add_argument("--name", help="예약자 이름")
    parser.add_argument("--phone", help="전화번호 (하이픈 포함)")
    parser.add_argument("--people", help="인원수", default="2")
    parser.add_argument("--auto", action="store_true", help="확인 없이 자동 실행")

    args = parser.parse_args()

    if args.branch and args.theme and args.date and args.times and args.name and args.phone:
        setup_cli(args)
    else:
        setup_interactive()


if __name__ == "__main__":
    main()
