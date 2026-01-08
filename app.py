import time
import hmac
import hashlib
import base64
import requests
import json
import os
import random
import pandas as pd

from io import BytesIO
from datetime import datetime
from bs4 import BeautifulSoup

from flask import (
    Flask,
    request,
    render_template_string,
    send_file,
    redirect,
    session,
    url_for,
)

# ==========================
# 네이버 검색광고 API 설정
# ==========================
BASE_URL = "https://api.naver.com"

API_KEY = "01000000000ea500e2d816aa0a9bc44418f20e0f55571f42f79ae469d57353f9337dd3f592"
SECRET_KEY = "AQAAAAAOpQDi2BaqCpvERBjyDg9Vw0VyAu/CjIVNHsmmqld7Ag=="
CUSTOMER_ID = 4174381

# ==========================
# 회사 정보 (리포트 하단 표)
# ==========================
COMPANY_INFO = {
    "회사명": "제이앤티솔루션 (J&T Solution)",
    "담당자": "김태민 이사",
    "연락처": "010-7140-1306",
    "비고": "본 리포트는 네이버 검색 데이터 기반으로 자동 생성된 키워드 분석 자료입니다.",
}

# ==========================
# 계정 관리 (accounts.json)
# ==========================
ACCOUNTS_FILE = "accounts.json"
ACCOUNTS = {}


def load_accounts():
    global ACCOUNTS
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            ACCOUNTS = json.load(f)
    except Exception:
        ACCOUNTS = {}


def save_accounts():
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(ACCOUNTS, f, ensure_ascii=False, indent=2)


load_accounts()

# 유저별 마지막 엑셀 저장
LAST_EXCEL = {}  # { user_id: {"bytes": b"...", "filename": "..." } }

# ==========================
# 업종 템플릿 로딩 세팅
# ==========================
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "static", "templates")


def load_industry_template(industry_code: str):
    """
    업종 코드(driving, education, hospital, realestate, beauty, food, onlineshop, aquarium, interior 등)에 따라
    static/templates/{code}.json 을 로딩한다.
    업종 코드가 비어 있으면(예: admin) 기본 템플릿을 사용한다.
    """
    code_raw = (industry_code or "").strip()

    # 기본 템플릿 (업종 공통)
    default = {
        "industry": "",
        "report_title": "J&T Solution 키워드 리포트",
        "good_keyword_rule": "검색량 100 이상 & 경쟁도 0.8 이하 = 좋은 키워드",
        "summary_format": (
            "총 {total_keywords}개 키워드 중 {passed_keywords}개가 조건을 통과했습니다. "
            "평균 검색량 {avg_search}회, 평균 경쟁도 {avg_comp}입니다."
        ),
        "recommended_title_patterns": [
            "{지역} {키워드} 완벽 정리 가이드",
            "{지역}에서 {키워드} 준비하려면?",
            "{키워드} 할 때 꼭 알아야 할 3가지"
        ],
    }

    # 업종 코드가 없으면(예: admin) 기본 템플릿 사용
    if not code_raw:
        return default

    # 업종 코드가 있으면 해당 json 로딩
    path = os.path.join(TEMPLATE_DIR, f"{code_raw}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = default.copy()
        merged.update(data)
        return merged
    except Exception:
        # 파일이 없거나 깨지면 기본 템플릿
        return default


# ==========================
# Flask 기본 설정
# ==========================
app = Flask(__name__)
app.secret_key = "JNT_login_secret_2025"


# ==========================
# 네이버 API 서명
# ==========================
class Signature:
    @staticmethod
    def generate(ts, method, uri, secret_key):
        msg = f"{ts}.{method}.{uri}"
        dig = hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(dig).decode()


def get_headers(method, uri):
    ts = str(round(time.time() * 1000))
    sig = Signature.generate(ts, method, uri, SECRET_KEY)
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": API_KEY,
        "X-Customer": str(CUSTOMER_ID),
        "X-Signature": sig,
    }


# ==========================
# 유틸 함수
# ==========================
def fetch_keyword_stats(base_keyword):
    """네이버 검색광고 키워드 도구 호출"""
    uri = "/keywordstool"
    headers = get_headers("GET", uri)
    params = {"hintKeywords": base_keyword, "showDetail": "1"}
    res = requests.get(BASE_URL + uri, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    return res.json().get("keywordList", [])


def check_blog_duplication(full_text):
    """
    원고를 받아 문장을 추출하고, 네이버에 검색하여 중복 여부를 확인
    """
    # 1. 문장 분리 (줄바꿈 및 마침표 기준)
    # 너무 짧은 문장(15자 미만)은 검사 의미가 없으므로 제외
    sentences = [
        s.strip()
        for s in full_text.replace("\n", ".").split(".")
        if len(s.strip()) > 15
    ]

    if not sentences:
        return None, "검사할 수 있는 긴 문장이 없습니다. (15자 이상)"

    # 2. 랜덤으로 3~5개 문장 추출
    check_count = min(5, len(sentences))
    target_sentences = random.sample(sentences, check_count)

    results = []

    # 네이버 봇 차단 방지용 헤더
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
    }

    base_search_url = "https://search.naver.com/search.naver"

    for sent in target_sentences:
        # 3. 정확도 검사를 위해 따옴표("")로 감싸서 검색 (Exact Match)
        query = f'"{sent}"'
        params = {"query": query, "where": "view"}  # where=view (블로그/카페 탭)

        try:
            resp = requests.get(base_search_url, headers=headers, params=params, timeout=5)
            soup = BeautifulSoup(resp.text, "html.parser")

            # 4. 결과 분석
            # '검색결과가 없습니다' 문구가 뜨거나, 리스트가 비어있으면 안전
            # 네이버 뷰탭 클래스 구조에 따라 다르지만, 보통 결과 없으면 특정 문구가 뜸
            text_body = soup.text

            if "검색결과가 없습니다" in text_body:
                status = "안전 (Unique)"
                is_safe = True
            else:
                # 결과가 있으면 중복 의심
                status = "중복 발견 (Dangerous)"
                is_safe = False

            results.append({
                "sentence": sent,
                "status": status,
                "is_safe": is_safe
            })

            # 과도한 요청 방지
            time.sleep(0.5)

        except Exception as e:
            results.append({
                "sentence": sent,
                "status": "검사 실패 (Error)",
                "is_safe": False
            })

    return results, None


def to_int(v):
    try:
        if isinstance(v, str):
            v = v.replace("<", "").strip()
        return int(v)
    except Exception:
        return 0


def to_float(v):
    try:
        return float(v)
    except Exception:
        return None


def parse_competition(v):
    """경쟁도 텍스트(낮음/중간/높음) → 숫자 스코어"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("낮음", "하", "low", "LOW"):
        return 0.3
    if s in ("중간", "중", "mid", "MID", "medium", "Medium"):
        return 0.6
    if s in ("높음", "상", "high", "HIGH"):
        return 0.9
    try:
        return float(s)
    except Exception:
        return None


# ==========================
# 로그인 / 로그아웃
# ==========================
LOGIN_HTML = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>로그인 - J&T Solution</title>
  <style>
    :root{
      --bg1:#0b1220;
      --bg2:#111827;
      --card:#0f172a;
      --muted:#94a3b8;
      --line:#1f2937;
      --text:#e5e7eb;
      --white:#ffffff;
      --accent:#22c55e;
      --accent2:#38bdf8;
      --danger:#fb7185;
    }
    *{box-sizing:border-box;}
    body{
      margin:0;
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      color:var(--text);
      min-height:100vh;
      display:flex;
      align-items:center;
      justify-content:center;
      padding:24px;
      background:
        radial-gradient(1200px 600px at 20% 10%, rgba(56,189,248,0.20), transparent 55%),
        radial-gradient(900px 500px at 80% 30%, rgba(34,197,94,0.16), transparent 55%),
        linear-gradient(180deg, var(--bg1), var(--bg2));
    }
    .shell{
      width:100%;
      max-width:980px;
      display:grid;
      grid-template-columns: 1.1fr 0.9fr;
      gap:18px;
      align-items:stretch;
    }
    .hero{
      border:1px solid rgba(148,163,184,0.12);
      background:linear-gradient(180deg, rgba(15,23,42,0.70), rgba(2,6,23,0.65));
      border-radius:18px;
      padding:28px;
      overflow:hidden;
      position:relative;
      box-shadow:0 20px 60px rgba(0,0,0,0.35);
    }
    .hero:before{
      content:"";
      position:absolute;
      inset:-2px;
      background: radial-gradient(600px 250px at 30% 0%, rgba(56,189,248,0.16), transparent 60%),
                  radial-gradient(520px 220px at 70% 20%, rgba(34,197,94,0.12), transparent 55%);
      pointer-events:none;
    }
    .brand{
      display:flex;
      align-items:center;
      gap:12px;
      position:relative;
    }
    .brand img{
      width:40px;height:40px;
      border-radius:10px;
      background:rgba(255,255,255,0.08);
      border:1px solid rgba(148,163,184,0.14);
      padding:6px;
    }
    .brand .t1{font-weight:800; font-size:18px; letter-spacing:-0.02em;}
    .brand .t2{font-size:12px; color:var(--muted); margin-top:2px;}
    .hero h1{
      position:relative;
      margin:22px 0 8px;
      font-size:26px;
      line-height:1.25;
      letter-spacing:-0.03em;
    }
    .hero p{
      position:relative;
      margin:0;
      color:var(--muted);
      font-size:13px;
      line-height:1.55;
      max-width:46ch;
    }
    .chips{
      position:relative;
      margin-top:18px;
      display:flex;
      flex-wrap:wrap;
      gap:8px;
    }
    .chip{
      font-size:11px;
      color:#cbd5e1;
      border:1px solid rgba(148,163,184,0.16);
      background:rgba(2,6,23,0.35);
      padding:6px 10px;
      border-radius:999px;
    }
    .card{
      border:1px solid rgba(148,163,184,0.12);
      background:rgba(2,6,23,0.55);
      border-radius:18px;
      padding:22px;
      box-shadow:0 20px 60px rgba(0,0,0,0.35);
    }
    .card h2{
      margin:0 0 6px;
      font-size:16px;
      letter-spacing:-0.02em;
    }
    .card .sub{
      margin:0 0 14px;
      font-size:12px;
      color:var(--muted);
      line-height:1.5;
    }
    .field{
      margin-top:10px;
    }
    .label{
      display:block;
      font-size:11px;
      color:#cbd5e1;
      margin-bottom:6px;
    }
    input{
      width:100%;
      padding:11px 12px;
      border-radius:12px;
      border:1px solid rgba(148,163,184,0.18);
      background:rgba(15,23,42,0.65);
      color:var(--text);
      font-size:13px;
      outline:none;
    }
    input:focus{
      border-color: rgba(56,189,248,0.55);
      box-shadow: 0 0 0 3px rgba(56,189,248,0.14);
    }
    .btn{
      width:100%;
      margin-top:14px;
      padding:11px 12px;
      border:none;
      border-radius:12px;
      font-size:13px;
      font-weight:700;
      cursor:pointer;
      color:#052e1d;
      background: linear-gradient(90deg, var(--accent), #86efac);
    }
    .btn:hover{filter:brightness(0.98);}
    .hint{
      margin-top:10px;
      font-size:11px;
      color:var(--muted);
      line-height:1.45;
    }
    .msg{
      margin-top:12px;
      padding:10px 12px;
      border-radius:12px;
      background: rgba(251,113,133,0.10);
      border:1px solid rgba(251,113,133,0.25);
      color:#fecdd3;
      font-size:12px;
    }
    .footer{
      margin-top:14px;
      font-size:11px;
      color:rgba(148,163,184,0.85);
      text-align:center;
    }
    @media (max-width: 860px){
      .shell{grid-template-columns:1fr; max-width:520px;}
      .hero{display:none;}
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div class="brand">
        <img src="{{ url_for('static', filename='logo.png') }}" onerror="this.style.display='none'">
        <div>
          <div class="t1">J&T Solution</div>
          <div class="t2">Naver Keyword Report System</div>
        </div>
      </div>

      <h1>키워드 리포트</h1>
      <p>
        네이버 검색 데이터를 기반으로 키워드 규모·경쟁도를 빠르게 확인하고,<br>
        리포트 엑셀로 정리할 수 있습니다.
      </p>

      <div class="chips">
        <div class="chip">키워드 분석</div>
        <div class="chip">경쟁도 시각화</div>
        <div class="chip">엑셀 리포트</div>
        <div class="chip">프리셋 저장</div>
      </div>
    </div>

    <div class="card">
      <h2>계정 로그인</h2>
      <p class="sub">발급받은 아이디/비밀번호로 접속하세요.</p>

      <form method="post" autocomplete="on">
        <div class="field">
          <span class="label">아이디</span>
          <input name="username" placeholder="아이디" autocomplete="username" autofocus>
        </div>

        <div class="field">
          <span class="label">비밀번호</span>
          <input name="password" type="password" placeholder="비밀번호" autocomplete="current-password">
        </div>

        <button class="btn" type="submit">로그인</button>
      </form>

      <div class="hint">
        문의: 제이앤티솔루션 · 김태민 이사 · 010-7140-1306<br>
        * 계정 분실 시 담당자에게 연락 주세요.
      </div>

      {% if msg %}
      <div class="msg">{{msg}}</div>
      {% endif %}

      <div class="footer">© {{ datetime.utcnow().year }} J&T Solution</div>
    </div>
  </div>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uid = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()
        user = ACCOUNTS.get(uid)
        if user and user["password"] == pw:
            session["user"] = uid
            session["name"] = user.get("name", uid)
            return redirect("/")
        return render_template_string(LOGIN_HTML, msg="아이디 또는 비밀번호가 올바르지 않습니다.", datetime=datetime)

    return render_template_string(LOGIN_HTML, msg=None, datetime=datetime)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ==========================
# 프리셋 (계정별)
# ==========================
def preset_file():
    return f"presets_{session['user']}.json"


def load_presets():
    try:
        with open(preset_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_presets(data):
    with open(preset_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==========================
# 메인 페이지 템플릿
# ==========================
MAIN_HTML = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>J&T Solution - 마케팅 인텔리전스</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --primary: #1e293b;
      --accent: #3b82f6;
      --bg: #f3f4f6;
      --card-bg: #ffffff;
      --text-main: #111827;
      --text-sub: #6b7280;
      --border: #e5e7eb;
      --danger: #ef4444;
      --success: #22c55e;
    }
    body {
      font-family: 'Noto Sans KR', -apple-system, sans-serif;
      background-color: var(--bg);
      color: var(--text-main);
      margin: 0;
      padding: 0;
      line-height: 1.5;
    }
    .container {
      max-width: 1100px;
      margin: 0 auto;
      padding: 30px 20px;
    }
    /* 헤더 */
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 30px;
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand img { height: 48px; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
    .brand-text h1 { margin: 0; font-size: 20px; font-weight: 700; color: var(--primary); }
    .brand-text p { margin: 0; font-size: 13px; color: var(--text-sub); }
    .user-menu { font-size: 14px; text-align: right; }
    .user-menu a { color: var(--text-sub); text-decoration: none; margin-left: 10px; font-weight: 500; }
    .user-menu a:hover { color: var(--accent); }

    /* 카드 */
    .card {
      background: var(--card-bg);
      border-radius: 16px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
      padding: 24px;
      margin-bottom: 24px;
      border: 1px solid var(--border);
    }
    .card-title {
      font-size: 17px;
      font-weight: 700;
      color: var(--primary);
      margin: 0 0 16px 0;
      padding-bottom: 12px;
      border-bottom: 2px solid #f1f5f9;
    }

    /* 폼 요소 */
    label { display: block; font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 6px; }
    input, select, textarea {
      width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border);
      font-size: 14px; background: #f9fafb; transition: all 0.2s; box-sizing: border-box; font-family: inherit;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: var(--accent); background: #fff; box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }

    /* 버튼 */
    .btn {
      display: inline-flex; align-items: center; justify-content: center;
      padding: 12px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; border: none;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.9; }
    .btn-primary { background: var(--primary); color: white; width: 100%; }
    .btn-outline { background: white; border: 1px solid var(--border); color: var(--text-main); padding: 8px 12px; font-size: 12px; }
    .btn-danger { color: var(--danger); border-color: #fecaca; background: #fef2f2; }

    /* 프리셋 */
    .preset-area { background: #f8fafc; border-radius: 12px; padding: 16px; margin-top: 20px; border: 1px solid var(--border); }
    .preset-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .preset-header span { font-size: 13px; font-weight: 600; color: var(--text-sub); }

    /* 알림창 */
    .alert { padding: 14px; border-radius: 8px; margin-top: 20px; font-size: 14px; background: #eff6ff; color: #1e40af; border: 1px solid #dbeafe; }
    .alert a { color: #2563eb; font-weight: 600; }

    /* 테이블 */
    .table-container { overflow-x: auto; margin-top: 10px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 600px; }
    th { background: #f8fafc; color: var(--text-sub); font-weight: 600; text-align: center; padding: 10px; border-bottom: 2px solid var(--border); }
    td { padding: 10px; border-bottom: 1px solid var(--border); text-align: center; color: var(--text-main); }

    /* ⭐ [수정] 차트 그리드 시스템 (깨짐 방지) */
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); /* 화면 좁으면 자동으로 밑으로 떨어짐 */
      gap: 20px;
    }
    .chart-box {
      background: #f8fafc;
      border-radius: 12px;
      padding: 16px;
      border: 1px solid var(--border);
      min-width: 0; /* ⭐ 핵심: Flex/Grid 자식 요소가 넘치지 않게 함 */
    }
    canvas {
      width: 100% !important;
      height: auto !important;
      max-height: 300px; /* 높이 제한 */
    }

    /* 추천 리스트 */
    .idea-list { list-style: none; padding: 0; margin: 0; }
    .idea-list li { background: #f8fafc; margin-bottom: 6px; padding: 8px 12px; border-radius: 6px; font-size: 13px; color: var(--text-main); }
    .idea-list li:before { content: "💡"; margin-right: 8px; }

    /* 검사 결과 */
    .check-result-item { padding: 12px; border-radius: 8px; margin-bottom: 8px; font-size: 13px; border-left: 4px solid transparent; }
    .check-safe { background: #f0fdf4; border-color: var(--success); }
    .check-danger { background: #fef2f2; border-color: var(--danger); }
    .tag { font-weight: 700; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 6px; }
    .tag-safe { background: #dcfce7; color: #166534; }
    .tag-danger { background: #fee2e2; color: #991b1b; }
  </style>
</head>
<body>

<div class="container">
  <header class="header">
    <div class="brand">
      <img src="{{ url_for('static', filename='logo.png') }}" onerror="this.src='https://via.placeholder.com/48?text=JNT'">
      <div class="brand-text">
        <h1>J&T Intelligence</h1>
        <p>{{ industry_name }} 키워드 분석 시스템</p>
      </div>
    </div>
    <div class="user-menu">
      <span>안녕하세요, <strong>{{ session['name'] }}</strong>님</span><br>
      {% if session['user'] == 'admin' %} <a href="{{ url_for('manage_accounts') }}">⚙️ 관리자</a> {% endif %}
      <a href="{{ url_for('logout') }}">로그아웃</a>
    </div>
  </header>

  <form method="post">
    <div class="card">
      <div class="card-title">🔍 키워드 분석 설정</div>
      <label>기준 키워드 입력</label>
      <textarea name="keywords" rows="2" placeholder="예: 강남맛집, 홍대카페">{{keywords}}</textarea>

      <div class="form-grid">
        <div><label>최소 검색수</label><input type="number" name="min_total" value="{{min_total or ''}}" placeholder="예: 100"></div>
        <div><label>최대 경쟁도</label><input name="max_comp" value="{{max_comp or ''}}" placeholder="예: 0.8"></div>
        <div>
          <label>정렬 기준</label>
          <select name="sort_by">
            <option value="total" {% if sort_by == 'total' %}selected{% endif %}>검색수 높은순</option>
            <option value="comp" {% if sort_by == 'comp' %}selected{% endif %}>경쟁도 낮은순</option>
          </select>
        </div>
      </div>
      <button name="action" value="generate" class="btn btn-primary">🚀 데이터 분석 시작</button>

      <div class="preset-area">
        <div class="preset-header">
          <span>📂 저장된 프리셋</span>
          <div style="display:flex; gap:6px;">
            <select name="preset" style="padding: 6px;"><option value="">-- 선택 --</option>{% for n in presets %}<option value="{{n}}" {% if n == selected %}selected{% endif %}>{{n}}</option>{% endfor %}</select>
            <button type="submit" name="action" value="load" class="btn btn-outline">열기</button>
            <button type="submit" name="action" value="delete_preset" class="btn btn-outline btn-danger" onclick="return confirm('삭제?');">삭제</button>
          </div>
        </div>
        <div style="display:flex; gap:8px;">
          <input name="newname" placeholder="새 프리셋 이름" style="flex:1;">
          <button type="submit" name="action" value="save" class="btn btn-outline" style="background:#1e293b; color:white;">저장</button>
        </div>
      </div>
    </div>
  </form>

  {% if msg %}
  <div class="alert">
    {{msg|safe}}
    {% if downloadable %} <br><a href="{{ url_for('download') }}" style="margin-top:8px; display:inline-block;">📥 엑셀 다운로드</a> {% endif %}
  </div>
  {% endif %}

  {% if chart_available or summary_table %}
  <div class="card">
    <div class="card-title">📊 분석 결과</div>

    {% if summary_table %}
    <div class="table-container" style="margin-bottom:30px;">
      <table>
        <thead><tr><th>기준 키워드</th><th>수집 수</th><th>평균 검색량</th><th>평균 경쟁도</th><th style="color:var(--accent);">조건 통과</th></tr></thead>
        <tbody>
          {% for row in summary_table %}
          <tr><td>{{row["기준 키워드"]}}</td><td>{{row["수집 키워드 수"]}}</td><td>{{row["평균 검색량"]}}</td><td>{{row["평균 경쟁도"]}}</td><td style="color:var(--accent); font-weight:bold;">{{row["조건 통과"]}}</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% if chart_available %}
    <div class="chart-grid">
      <div class="chart-box">
        <h4 style="font-size:13px; text-align:center; margin-bottom:10px;">검색량 Top 20</h4>
        <div style="position: relative; height:250px; width:100%;">
            <canvas id="volumeChart"></canvas>
        </div>
      </div>
      <div class="chart-box">
        <h4 style="font-size:13px; text-align:center; margin-bottom:10px;">경쟁도 분석</h4>
        <div style="position: relative; height:250px; width:100%;">
            <canvas id="compChart"></canvas>
        </div>
      </div>
    </div>
    {% endif %}
  </div>

  <div class="chart-grid" style="align-items:start;">
    {% if recommended_groups %}
    <div class="card">
      <div class="card-title">🧠 키워드 조합 추천</div>
      {% for group in recommended_groups %}
        <h5 style="margin:10px 0 6px; font-size:13px; color:var(--text-sub);">[{{ group.base }}]</h5>
        <ul class="idea-list">{% for phrase in group.phrases %}<li>{{ phrase }}</li>{% endfor %}</ul>
      {% endfor %}
    </div>
    {% endif %}
    {% if blog_title_groups %}
    <div class="card">
      <div class="card-title">✏️ 블로그 제목 제안</div>
      {% for g in blog_title_groups %}
        <h5 style="margin:10px 0 6px; font-size:13px; color:var(--text-sub);">[{{ g.base }}]</h5>
        <ul class="idea-list">{% for t in g.titles %}<li style="background:#f0fdf4;">{{ t }}</li>{% endfor %}</ul>
      {% endfor %}
    </div>
    {% endif %}
  </div>
  {% endif %}

  <div class="card" style="border-top: 4px solid var(--primary);">
    <div class="card-title" style="border-bottom:none;">📝 원고 중복(유사문서) 사전 점검</div>
    <form method="post">
      <textarea name="blog_content" rows="6" placeholder="작성한 블로그 글 붙여넣기..." 
                onfocus="this.style.borderColor='#3b82f6'" onblur="this.style.borderColor='#e5e7eb'">{{ blog_content or '' }}</textarea>
      <button name="action" value="check_duplication" class="btn btn-primary" style="margin-top:12px;">🔍 중복 정밀 검사</button>
    </form>
    {% if dup_results %}
      <div style="margin-top:20px;">
        <h4 style="font-size:13px; font-weight:600; margin-bottom:12px;">검사 결과</h4>
        {% for res in dup_results %}
        <div class="check-result-item {{ 'check-safe' if res.is_safe else 'check-danger' }}">
          <span class="tag {{ 'tag-safe' if res.is_safe else 'tag-danger' }}">{{ 'SAFE' if res.is_safe else 'WARNING' }}</span>
          "{{ res.sentence }}"
        </div>
        {% endfor %}
      </div>
    {% endif %}
  </div>

  <div style="text-align:center; font-size:12px; color:#9ca3af; margin-top:40px;">© 2026 J&T Solution Intelligence.</div>
</div>

{% if chart_available %}
<script>
  // Chart.js 반응형 설정 (유지보수 용이)
  const commonOptions = {
    responsive: true,
    maintainAspectRatio: false, // 부모 div 크기에 맞춤
    plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: {size: 11} } } },
    scales: { x: { ticks: { display: false } } }
  };
  const kwLabels = {{ chart_labels|tojson }};
  const pcData = {{ chart_pc|tojson }};
  const moData = {{ chart_mo|tojson }};
  const compData = {{ chart_comp|tojson }};

  new Chart(document.getElementById('volumeChart'), {
    type: 'bar',
    data: { labels: kwLabels, datasets: [{ label: 'PC', data: pcData, backgroundColor: '#94a3b8' }, { label: 'Mobile', data: moData, backgroundColor: '#3b82f6' }] },
    options: commonOptions
  });

  const compColors = compData.map(v => (v<=0.3 ? '#22c55e' : v<=0.8 ? '#f59e0b' : '#ef4444'));
  new Chart(document.getElementById('compChart'), {
    type: 'bar',
    data: { labels: kwLabels, datasets: [{ label: '경쟁도', data: compData, backgroundColor: compColors, borderRadius: 4 }] },
    options: { ...commonOptions, scales: { y: { beginAtZero: true, max: 1.0 } } }
  });
</script>
{% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def index():
    if "user" not in session:
        return redirect("/login")

    presets = load_presets()
    msg = None
    downloadable = False
    keywords = ""
    min_total = 0
    max_comp_str = ""
    selected = ""
    sort_by = "total"
    blog_content = ""
    dup_results = None

    # 그래프용 기본값
    chart_available = False
    chart_labels, chart_pc, chart_mo, chart_comp = [], [], [], []
    chart_count = 0

    # 기준 키워드별 요약 테이블 + 추천 키워드 조합 + 블로그 제목 그룹
    summary_table = []
    recommended_groups = []
    blog_title_groups = []

    # 로그인한 계정의 지역명/업종
    user_info = ACCOUNTS.get(session["user"], {})
    region = user_info.get("region", "") or ""
    region = region.strip()
    industry_code = user_info.get("industry", "driving")

    # 업종 템플릿 로드
    tpl = load_industry_template(industry_code)
    report_title = tpl.get("report_title", "J&T Solution 키워드 리포트")
    industry_name = tpl.get("industry", "키워드 리포트")
    good_keyword_rule = tpl.get(
        "good_keyword_rule", "검색량 100 이상 & 경쟁도 0.8 이하 = 좋은 키워드"
    )
    summary_format = tpl.get(
        "summary_format",
        "총 {total_keywords}개 키워드 중 {passed_keywords}개가 조건을 통과했습니다. "
        "평균 검색량 {avg_search}회, 평균 경쟁도 {avg_comp}입니다.",
    )
    title_patterns = tpl.get(
        "recommended_title_patterns",
        [
            "{지역} {키워드} 완벽 정리 가이드",
            "{지역}에서 {키워드} 준비하려면?",
            "{키워드} 할 때 꼭 알아야 할 3가지",
        ],
    )

    if request.method == "POST":
        action = request.form.get("action")
        keywords = request.form.get("keywords", "")
        min_total = int(request.form.get("min_total") or 0)
        max_comp_str = request.form.get("max_comp", "").strip()
        max_comp_val = to_float(max_comp_str) if max_comp_str else None
        selected = request.form.get("preset", "")
        sort_by = request.form.get("sort_by", "total")

        if action == "load":
            if selected and selected in presets:
                keywords = presets[selected]
                msg = f"프리셋 '{selected}'을(를) 불러왔습니다."
            else:
                msg = "불러올 프리셋을 선택해 주세요."

        elif action == "save":
            newname = request.form.get("newname", "").strip()
            if not newname:
                msg = "프리셋 이름을 입력해 주세요."
            elif not keywords:
                msg = "현재 키워드가 비어 있어 저장할 수 없습니다."
            else:
                presets[newname] = keywords
                save_presets(presets)
                msg = f"프리셋 '{newname}'이(가) 저장되었습니다."

        elif action == "delete_preset":
            target = request.form.get("preset", "").strip()
            if not target:
                msg = "삭제할 프리셋을 먼저 선택해 주세요."
            elif target not in presets:
                msg = "해당 프리셋을 찾을 수 없습니다."
            else:
                presets.pop(target)
                save_presets(presets)
                if selected == target:
                    selected = ""
                    keywords = ""
                msg = f"프리셋 '{target}'이(가) 삭제되었습니다."

        elif action == "generate":
            base_keywords = [k.strip() for k in keywords.split(",") if k.strip()]
            if not base_keywords:
                msg = "기준 키워드를 하나 이상 입력해 주세요."
            else:
                all_rows = []

                # 기준 키워드별 수집
                for base in base_keywords:
                    items = fetch_keyword_stats(base)
                    for item in items:
                        rel = item.get("relKeyword")
                        if not rel:
                            continue
                        pc = to_int(item.get("monthlyPcQcCnt"))
                        mo = to_int(item.get("monthlyMobileQcCnt"))
                        total = pc + mo
                        comp_text = item.get("compIdx")
                        comp_score = parse_competition(comp_text)

                        all_rows.append(
                            {
                                "키워드": rel,
                                "PC 검색수": pc,
                                "모바일 검색수": mo,
                                "총 검색수": total,
                                "평균 노출 광고수": item.get("plAvgDepth"),
                                "경쟁도": comp_score,
                                "경쟁도(텍스트)": comp_text,
                                "기준 키워드 출처": base,
                            }
                        )
                    time.sleep(0.3)

                if not all_rows:
                    msg = "수집된 키워드가 없습니다."
                else:
                    df_all = pd.DataFrame(all_rows)

                    # 필터 적용
                    df_filtered = df_all[df_all["총 검색수"] >= min_total]
                    if max_comp_val is not None:
                        df_filtered = df_filtered[
                            (df_filtered["경쟁도"].notna())
                            & (df_filtered["경쟁도"] <= max_comp_val)
                        ]

                    # 정렬 적용 (필터된 데이터에 대해서)
                    if not df_filtered.empty:
                        if sort_by == "comp":
                            df_filtered = df_filtered.sort_values(
                                "경쟁도", ascending=True
                            )
                        else:
                            df_filtered = df_filtered.sort_values(
                                "총 검색수", ascending=False
                            )

                    # 기준 키워드별 요약 테이블
                    for base in base_keywords:
                        sub = df_all[df_all["기준 키워드 출처"] == base]
                        if sub.empty:
                            continue
                        avg_total = int(sub["총 검색수"].mean())
                        avg_comp = round(sub["경쟁도"].mean(), 2)
                        if max_comp_val is not None:
                            sub_pass = sub[
                                (sub["총 검색수"] >= min_total)
                                & (sub["경쟁도"].notna())
                                & (sub["경쟁도"] <= max_comp_val)
                            ]
                        else:
                            sub_pass = sub[sub["총 검색수"] >= min_total]

                        summary_table.append(
                            {
                                "기준 키워드": base,
                                "수집 키워드 수": len(sub),
                                "평균 검색량": avg_total,
                                "평균 경쟁도": avg_comp,
                                "조건 통과": len(sub_pass),
                            }
                        )

                    # 그래프용 데이터 (전체 기준 상위 20개)
                    top_df = df_all.sort_values("총 검색수", ascending=False).head(20)
                    chart_labels = top_df["키워드"].tolist()
                    chart_pc = top_df["PC 검색수"].tolist()
                    chart_mo = top_df["모바일 검색수"].tolist()
                    chart_comp = top_df["경쟁도"].fillna(0).tolist()
                    chart_count = len(top_df)
                    chart_available = chart_count > 0

                    # ==========================
                    # 요약문 + 예상 광고비 / 유입 규모 (러프 추정)
                    # ==========================
                    if not df_filtered.empty:
                        avg_total_all = int(df_filtered["총 검색수"].mean())
                        avg_comp_all = round(df_filtered["경쟁도"].mean(), 2)

                        # 템플릿 기반 요약문
                        summary_core = summary_format.format(
                            total_keywords=len(df_all),
                            passed_keywords=len(df_filtered),
                            avg_search=avg_total_all,
                            avg_comp=avg_comp_all,
                        )
                        summary_msg = (
                            f"리포트 생성 완료. {summary_core} ({good_keyword_rule})<br>"
                        )

                        # 🔹 여기서부터는 '추측입니다' 영역 (러프 추정)
                        total_search_sum = int(df_filtered["총 검색수"].sum())

                        # 예시 가정 (추측입니다):
                        # - 예상 클릭율: 1% ~ 3%
                        # - 클릭당 비용: 500원 ~ 1,500원
                        est_clicks_low = int(total_search_sum * 0.01)
                        est_clicks_high = int(total_search_sum * 0.03)

                        cpc_low = 500
                        cpc_high = 1500

                        est_budget_low = est_clicks_low * cpc_low
                        est_budget_high = est_clicks_high * cpc_high

                        estimate_msg = (
                            "<br>※ 아래 수치는 네이버 검색량을 기준의 예상 광고비용이며, "
                            "실제 광고 집행 결과와는 다를 수 있습니다.<br>"
                            f"- 월 예상 클릭수: 약 {est_clicks_low:,} ~ {est_clicks_high:,}회<br>"
                            f"- 월 예상 광고비: 약 {est_budget_low:,.0f}원 ~ "
                            f"{est_budget_high:,.0f}원 수준"
                        )

                        full_msg = summary_msg + estimate_msg
                    else:
                        full_msg = "조건에 맞는 키워드가 없습니다."

                    # 🔹 추천 키워드 조합 (웹용) - 기준 키워드별로 생성
                    if not df_filtered.empty:
                        for base in base_keywords:
                            sub = df_filtered[df_filtered["기준 키워드 출처"] == base]
                            if sub.empty:
                                continue

                            sub_sorted = sub.sort_values(
                                "총 검색수", ascending=False
                            ).head(10)

                            phrases = []
                            for _, row in sub_sorted.head(3).iterrows():
                                kw = row["키워드"]
                                if base and base not in kw:
                                    phrase = f"{base} {kw}"
                                else:
                                    phrase = kw
                                phrases.append(phrase)

                            if phrases:
                                recommended_groups.append(
                                    {
                                        "base": base,
                                        "phrases": phrases,
                                    }
                                )

                    # 🔹 블로그 제목 자동 제안 (업종 템플릿 + 지역 포함)
                    region_placeholder = region if region else ""
                    for group in recommended_groups:
                        base = group["base"]
                        phrases = group["phrases"]
                        if not phrases:
                            continue
                        main_kw = phrases[0]

                        titles = []
                        for pat in title_patterns[:5]:
                            t = pat.replace("{지역}", region_placeholder).replace(
                                "{키워드}", main_kw
                            )
                            # 지역이 없을 때 생길 수 있는 이중 공백 정리
                            t = " ".join(t.split())
                            titles.append(t)

                        blog_title_groups.append(
                            {
                                "base": base,
                                "titles": titles,
                            }
                        )

                    # 엑셀 저장 (전체, 필터, 회사정보)
                    info_rows = [
                        {"항목": k, "내용": v} for k, v in COMPANY_INFO.items()
                    ]
                    df_info = pd.DataFrame(info_rows)

                    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
                    fname = f"JNT_Keyword_Report_{session['user']}_{ts}.xlsx"

                    out = BytesIO()
                    with pd.ExcelWriter(out, engine="openpyxl") as w:
                        df_all.to_excel(w, sheet_name="전체 키워드", index=False)
                        df_filtered.to_excel(w, sheet_name="필터 적용", index=False)
                        df_info.to_excel(
                            w,
                            sheet_name="전체 키워드",
                            startrow=len(df_all) + 2,
                            index=False,
                        )
                        df_info.to_excel(
                            w,
                            sheet_name="필터 적용",
                            startrow=len(df_filtered) + 2,
                            index=False,
                        )
                    out.seek(0)

                    LAST_EXCEL[session["user"]] = {
                        "bytes": out.read(),
                        "filename": fname,
                    }

                    downloadable = True
                    msg = full_msg

        elif action == "check_duplication":
            blog_content = request.form.get("blog_content", "").strip()
            if not blog_content:
                msg = "검사할 원고 내용을 입력해주세요."
            else:
                # Step 2에서 만든 함수 호출
                results, error_msg = check_blog_duplication(blog_content)
                if error_msg:
                    msg = error_msg
                else:
                    dup_results = results
                    msg = "중복 검사가 완료되었습니다. 아래 결과를 확인하세요."

        else:
            msg = "알 수 없는 동작입니다."

    # GET 또는 POST 이후 렌더링
    return render_template_string(
        MAIN_HTML,
        presets=presets,
        selected=selected,
        keywords=keywords,
        min_total=min_total,
        max_comp=max_comp_str,
        msg=msg,
        downloadable=downloadable,
        sort_by=sort_by,
        chart_available=chart_available,
        chart_labels=chart_labels,
        chart_pc=chart_pc,
        chart_mo=chart_mo,
        chart_comp=chart_comp,
        chart_count=chart_count,
        summary_table=summary_table,
        recommended_groups=recommended_groups,
        blog_title_groups=blog_title_groups,
        report_title=report_title,
        industry_name=industry_name,
        blog_content=blog_content,
        dup_results=dup_results,
    )


# ==========================
# 계정 관리 (관리자 전용)
# ==========================
ADMIN_HTML = """
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>계정 관리 - J&T Solution</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f5f7;max-width:900px;margin:40px auto;padding:0 16px;}
.card{background:white;padding:24px;border-radius:14px;box-shadow:0 6px 18px rgba(0,0,0,0.05);}
h1{font-size:20px;margin-bottom:12px;}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;}
th,td{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;}
th{background:#f9fafb;}
form.inline{display:inline;}
input,select{padding:6px;border-radius:6px;border:1px solid #d1d5db;font-size:13px;margin-right:6px;}
button{padding:6px 10px;border:none;border-radius:6px;font-size:13px;cursor:pointer;}
.btn-del{background:#fee2e2;color:#b91c1c;}
.btn-back{background:#e5e7eb;color:#111827;margin-bottom:10px;}
.btn-add{background:#111827;color:white;margin-top:8px;}
.msg{margin-top:12px;font-size:13px;color:#2563eb;}
</style></head><body>
<div class="card">
  <form method="get" action="{{ url_for('index') }}">
    <button class="btn-back" type="submit">← 리포트 화면으로</button>
  </form>
  <h1>계정 관리 (관리자 전용)</h1>

  <h2 style="font-size:14px;margin-top:10px;">현재 계정 목록</h2>
  <table>
    <tr>
      <th>아이디</th>
      <th>이름</th>
      <th>지역</th>
      <th>업종코드</th>
      <th>비고</th>
      <th>삭제</th>
    </tr>
    {% for uid, info in accounts.items() %}
      <tr>
        <td>{{uid}}</td>
        <td>{{info.name}}</td>
        <td>{{info.region or '-'}}</td>
        <td>{{info.industry or '-'}}</td>
        <td>{% if uid == 'admin' %}관리자 계정{% else %}-{% endif %}</td>
        <td>
          {% if uid != 'admin' %}
          <form method="post" class="inline">
            <input type="hidden" name="action" value="delete">
            <input type="hidden" name="del_uid" value="{{uid}}">
            <button class="btn-del" type="submit">삭제</button>
          </form>
          {% else %}
          -
          {% endif %}
        </td>
      </tr>
    {% endfor %}
  </table>

  <h2 style="font-size:14px;margin-top:18px;">새 계정 추가</h2>
  <form method="post">
    <input type="hidden" name="action" value="add">
    <div style="margin-top:6px;">
      <input name="new_uid" placeholder="아이디 (영문/숫자 권장)">
    </div>
    <div style="margin-top:6px;">
      <input name="new_pw" placeholder="비밀번호">
    </div>
    <div style="margin-top:6px;">
      <input name="new_name" placeholder="표시 이름 (예: 업체명)">
    </div>
    <div style="margin-top:6px;">
      <input name="new_region" placeholder="지역 (예: 서울, 경기, 경북, 전북)">
    </div>
    <div style="margin-top:6px;">
      <select name="new_industry">
        <option value="driving">운전면허학원 (driving)</option>
        <option value="education">교육 (education)</option>
        <option value="hospital">병원 (hospital)</option>
        <option value="realestate">부동산 (realestate)</option>
        <option value="beauty">뷰티 (beauty)</option>
        <option value="food">음식 (food)</option>
        <option value="onlineshop">온라인 잡화 (onlineshop)</option>
        <option value="aquarium">관상어/물생활 (aquarium)</option>
        <option value="interior">인테리어 (interior)</option>
      </select>
    </div>
    <button class="btn-add" type="submit">계정 추가</button>
  </form>

  {% if msg %}
  <div class="msg">{{msg}}</div>
  {% endif %}
</div>
</body></html>
"""


@app.route("/admin/accounts", methods=["GET", "POST"])
def manage_accounts():
    if "user" not in session or session["user"] != "admin":
        return redirect("/login")

    msg = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            del_uid = request.form.get("del_uid", "").strip()
            if del_uid and del_uid in ACCOUNTS and del_uid != "admin":
                ACCOUNTS.pop(del_uid)
                save_accounts()
                msg = f"계정 '{del_uid}'이(가) 삭제되었습니다."
            else:
                msg = "삭제할 수 없는 계정입니다."
        elif action == "add":
            new_uid = request.form.get("new_uid", "").strip()
            new_pw = request.form.get("new_pw", "").strip()
            new_name = request.form.get("new_name", "").strip()
            new_region = request.form.get("new_region", "").strip()
            new_industry = request.form.get("new_industry", "").strip() or "driving"
            if not new_uid or not new_pw or not new_name:
                msg = "아이디, 비밀번호, 이름을 모두 입력해 주세요."
            elif new_uid in ACCOUNTS:
                msg = "이미 존재하는 아이디입니다."
            else:
                ACCOUNTS[new_uid] = {
                    "password": new_pw,
                    "name": new_name,
                    "region": new_region,
                    "industry": new_industry,
                }
                save_accounts()
                msg = f"계정 '{new_uid}'이(가) 추가되었습니다."
        else:
            msg = "알 수 없는 동작입니다."

    # view용 객체로 변환 (info.name, info.region, info.industry 등 접근 가능하게)
    accounts_for_view = {}
    for uid, info in ACCOUNTS.items():
        obj = type("obj", (), {})()
        obj.name = info.get("name", "")
        obj.region = info.get("region", "")
        obj.industry = info.get("industry", "")
        accounts_for_view[uid] = obj

    return render_template_string(
        ADMIN_HTML,
        accounts=accounts_for_view,
        msg=msg,
    )


# ==========================
# 엑셀 다운로드
# ==========================
@app.route("/download")
def download():
    if "user" not in session:
        return redirect("/login")

    uid = session["user"]
    if uid not in LAST_EXCEL:
        return "리포트를 먼저 생성하세요.", 400

    blob = LAST_EXCEL[uid]
    return send_file(
        BytesIO(blob["bytes"]),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=blob["filename"],
    )


# ==========================
# 앱 실행
# ==========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=True)