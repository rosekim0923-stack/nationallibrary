"""
중앙대 문헌정보학과 학부공지 새 글 알림 봇.

게시판 페이지를 읽어 글 링크 목록을 저장해 두고, 다음 실행 때
처음 보는 링크가 생기면 ntfy(휴대폰 푸시)로 알려줍니다.
사이트 HTML 구조를 몰라도 동작하도록 '링크 비교' 방식으로 만들었습니다.

환경변수
  NOTICE_URL     : 학부공지 게시판 주소 (필수)
  NTFY_TOPIC     : ntfy 토픽 이름 (필수, 남이 추측 못 하게 길게)
  LINK_SELECTOR  : (선택) 글 목록 영역만 고르는 CSS 선택자. 예) "table tbody"
"""
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse, parse_qsl

import requests
from bs4 import BeautifulSoup

STATE_FILE = "seen.json"
KST = timezone(timedelta(hours=9))

NOTICE_URL = (os.environ.get("NOTICE_URL", "").strip()
              or "https://lis.cau.ac.kr/02_notice/notice_01a.php")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
LINK_SELECTOR = os.environ.get("LINK_SELECTOR", "").strip()

# 글 상세 페이지로 보이는 링크 패턴 (대부분의 대학 게시판이 이 중 하나를 씀)
POST_HINT = re.compile(
    r"(view|read|artcl|detail|idx=|seq=|no=|uid=|wr_id=|articleNo=|bbsidx|boardseq)",
    re.I,
)
# 메뉴·페이지 이동 등 제외할 링크
SKIP_HINT = re.compile(r"(login|logout|sitemap|#$|mailto:|tel:)", re.I)


def fetch(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text


def looks_like_post(url: str, base_url: str) -> bool:
    """같은 게시판 폴더 안에서, 페이지 번호 말고 다른 숫자 파라미터가 붙은 링크 = 글 상세."""
    u, b = urlparse(url), urlparse(base_url)
    if u.netloc != b.netloc:
        return False
    if not u.path.startswith(b.path.rsplit("/", 1)[0] + "/"):
        return False
    params = [k for k, v in parse_qsl(u.query) if v.isdigit() and k.lower() not in ("page", "pg", "p")]
    return bool(params)


def extract_posts(html: str, base_url: str) -> dict:
    """{고유키: {"title":..., "url":...}} 형태로 글 목록을 뽑는다."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(LINK_SELECTOR) if LINK_SELECTOR else soup
    if root is None:
        raise SystemExit(f"LINK_SELECTOR '{LINK_SELECTOR}' 에 맞는 요소가 없습니다.")

    posts = {}
    for a in root.find_all("a"):
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 4:
            continue
        href = (a.get("href") or "").strip()
        onclick = (a.get("onclick") or "").strip()

        if href and not href.lower().startswith("javascript"):
            url = urljoin(base_url, href)
            key = url
        else:
            # javascript:goView('1234') 같은 형태 → 숫자 ID를 키로 사용
            js = href + " " + onclick
            ids = re.findall(r"\d{2,}", js)
            if not ids:
                continue
            key = "js:" + "-".join(ids)
            url = base_url

        if SKIP_HINT.search(key):
            continue
        if not LINK_SELECTOR and not (
            POST_HINT.search(key) or key.startswith("js:") or looks_like_post(key, base_url)
        ):
            continue
        posts.setdefault(key, {"title": title, "url": url})
    return posts


def notify(title: str, url: str) -> None:
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=title.encode("utf-8"),
        headers={
            "Title": "=?UTF-8?B?" + base64.b64encode("문헌정보학과 새 공지".encode()).decode() + "?=",
            "Click": url,
            "Tags": "books",
        },
        timeout=20,
    )


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def main() -> None:
    if not NOTICE_URL or not NTFY_TOPIC:
        sys.exit("NOTICE_URL 과 NTFY_TOPIC 을 설정해 주세요.")

    posts = extract_posts(fetch(NOTICE_URL), NOTICE_URL)
    if not posts:
        sys.exit("글 링크를 하나도 못 찾았습니다. LINK_SELECTOR 를 지정해 보세요.")

    state = load_state()
    seen = state.get("seen", {})
    now = datetime.now(KST)

    if not seen:
        print(f"첫 실행: 기존 글 {len(posts)}개를 저장만 하고 알림은 보내지 않습니다.")
        notify(f"알림 설정 완료! 현재 공지 {len(posts)}개를 기억했어요.", NOTICE_URL)
    else:
        new = [(k, v) for k, v in posts.items() if k not in seen]
        for _, p in reversed(new):  # 오래된 것부터 알림
            print("새 공지:", p["title"])
            notify(p["title"], p["url"])
        if not new:
            print("새 공지 없음")

    seen.update({k: v["title"] for k, v in posts.items()})
    state["seen"] = seen

    # GitHub는 60일간 커밋이 없으면 예약 실행을 멈추므로, 한 달에 한 번 기록을 남긴다
    last = state.get("heartbeat")
    if not last or (now - datetime.fromisoformat(last)).days >= 30:
        state["heartbeat"] = now.isoformat(timespec="seconds")

    save_state(state)


if __name__ == "__main__":
    main()
