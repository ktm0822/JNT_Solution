import time
import hmac
import hashlib
import base64
import requests
import json
import pandas as pd
from io import BytesIO
from datetime import datetime
import os

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
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>로그인 - J&T Solution</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f5f7;display:flex;align-items:center;justify-content:center;height:100vh;}
.card{background:white;padding:40px 50px;border-radius:14px;box-shadow:0 8px 20px rgba(0,0,0,0.08);width:360px;}
h1{text-align:center;font-size:22px;margin-bottom:20px;}
input{width:100%;padding:10px;margin-top:8px;border:1px solid #ccc;border-radius:8px;}
button{width:100%;margin-top:16px;padding:10px;border:none;border-radius:8px;background:#111827;color:#fff;font-weight:600;cursor:pointer;}
.msg{text-align:center;margin-top:10px;color:#e11d48;font-size:13px;}
</style></head><body>
<div class="card">
  <h1>J&T Solution<br>키워드 리포트 로그인</h1>
  <form method="post">
    <input name="username" placeholder="아이디 입력">
    <input name="password" type="password" placeholder="비밀번호 입력">
    <button type="submit">로그인</button>
  </form>
  {% if msg %}<div class="msg">{{msg}}</div>{% endif %}
</div></body></html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uid = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()
        user = ACCOUNTS.get(uid)
        if user and user["password"] == pw:
            session["user"] = uid
            session["name"] = user["name"]
            return redirect("/")
        return render_template_string(
            LOGIN_HTML, msg="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    return render_template_string(LOGIN_HTML, msg=None)


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
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>J&T Solution - 키워드 리포트</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f5f7;max-width:960px;margin:40px auto;padding:0 16px;}
.card{background:white;padding:24px;border-radius:14px;box-shadow:0 6px 18px rgba(0,0,0,0.05);}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:16px;}
.logo img{height:40px;}
.sub{font-size:12px;color:#888;}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
a.logout,a.admin-link{font-size:13px;text-decoration:none;margin-left:8px;}
a.logout{color:#e11d48;}
a.admin-link{color:#2563eb;}
label{display:block;margin-top:12px;font-size:13px;font-weight:600;}
textarea,input,select,button{width:100%;padding:8px;margin-top:4px;border-radius:8px;border:1px solid #d1d5db;font-size:13px;box-sizing:border-box;}
button{margin-top:10px;background:#111827;color:white;border:none;border-radius:8px;font-weight:600;cursor:pointer;}
.msg{margin-top:16px;padding:10px;background:#f3f4f6;border-radius:8px;font-size:13px;}
.chart-section{margin-top:24px;}
.chart-section h3{font-size:14px;margin-bottom:8px;}
canvas{background:#f9fafb;border-radius:8px;padding:8px;}
.summary-table{margin-top:16px;font-size:13px;border-collapse:collapse;width:100%;}
.summary-table th,.summary-table td{border:1px solid #e5e7eb;padding:6px 8px;text-align:center;}
.summary-table th{background:#f9fafb;}
.recommend-list{margin-top:4px;font-size:13px;padding-left:18px;}
.recommend-list li{margin-bottom:2px;}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body>
<div class="card">
  <div class="topbar">
    <div>👤 로그인: {{session['name']}}</div>
    <div>
      {% if session['user'] == 'admin' %}
      <a href="{{ url_for('manage_accounts') }}" class="admin-link">계정 관리</a>
      {% endif %}
      <a href="{{ url_for('logout') }}" class="logout">로그아웃</a>
    </div>
  </div>

  <div class="logo">
    <img src="{{ url_for('static', filename='logo.png') }}" onerror="this.style.display='none'">
    <div>
      <div><strong>J&T Solution 키워드 리포트</strong></div>
      <div class="sub">네이버 검색 데이터를 기반으로 한 실전형 키워드 분석 도구</div>
    </div>
  </div>

  <form method="post">
    <label>프리셋 선택</label>
    <select name="preset">
      <option value="">-- 선택 --</option>
      {% for n in presets %}
        <option value="{{n}}" {% if n == selected %}selected{% endif %}>{{n}}</option>
      {% endfor %}
    </select>
    <button name="action" value="load">불러오기</button>
    <button name="action" value="delete_preset"
            onclick="return confirm('선택한 프리셋을 삭제할까요?');">
      삭제
    </button>

    <label>새 프리셋 이름</label>
    <input name="newname" placeholder="예: 강릉 ○○학원 기본세트">
    <button name="action" value="save">프리셋 저장</button>

    <label>기준 키워드 (쉼표로 구분)</label>
    <textarea name="keywords" rows="3">{{keywords}}</textarea>

    <label>최소 총 검색수</label>
    <input type="number" name="min_total" value="{{min_total or ''}}">

    <label>최대 경쟁도</label>
    <input name="max_comp" value="{{max_comp or ''}}" placeholder="예: 0.8 (없으면 공백)">

    <label>정렬 기준</label>
    <select name="sort_by">
      <option value="total" {% if sort_by == 'total' %}selected{% endif %}>총 검색수순</option>
      <option value="comp" {% if sort_by == 'comp' %}selected{% endif %}>경쟁도 낮은순</option>
    </select>

    <button name="action" value="generate">리포트 생성</button>
  </form>

  {% if msg %}
  <div class="msg">
    {{msg}}
    {% if downloadable %}
    <br><a href="{{ url_for('download') }}">📥 엑셀 다운로드</a>
    {% endif %}
  </div>
  {% endif %}

  {% if summary_table %}
  <h3 style="margin-top:18px;font-size:14px;">📋 기준 키워드별 요약</h3>
  <table class="summary-table">
    <tr>
      <th>기준 키워드</th>
      <th>수집 키워드 수</th>
      <th>평균 검색량</th>
      <th>평균 경쟁도</th>
      <th>조건 통과</th>
    </tr>
    {% for row in summary_table %}
    <tr>
      <td>{{row["기준 키워드"]}}</td>
      <td>{{row["수집 키워드 수"]}}</td>
      <td>{{row["평균 검색량"]}}</td>
      <td>{{row["평균 경쟁도"]}}</td>
      <td>{{row["조건 통과"]}}</td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}

  {% if chart_available %}
  <div class="chart-section">
    <h3>📊 총 검색수 기준 상위 {{ chart_count }}개 키워드 (PC / 모바일)</h3>
    <canvas id="volumeChart" height="130"></canvas>
  </div>

  <div class="chart-section">
    <h3>📈 상위 키워드 경쟁도 분석</h3>
    <canvas id="compChart" height="130"></canvas>
  </div>
  {% endif %}

  {% if recommended_groups %}
  <div class="chart-section">
    <h3>🧠 추천 키워드 조합 (블로그/콘텐츠 활용)</h3>

    {% for group in recommended_groups %}
      <h4 style="font-size:13px;margin-top:8px;">[{{ group.base }}]</h4>
      <ul class="recommend-list">
        {% for phrase in group.phrases %}
          <li>{{ phrase }}</li>
        {% endfor %}
      </ul>
    {% endfor %}
  </div>
  {% endif %}
</div>

{% if chart_available %}
<script>
  const kwLabels = {{ chart_labels|tojson }};
  const pcData   = {{ chart_pc|tojson }};
  const moData   = {{ chart_mo|tojson }};
  const compData = {{ chart_comp|tojson }};

  // 검색량 차트
  const ctx1 = document.getElementById('volumeChart').getContext('2d');
  new Chart(ctx1, {
    type: 'bar',
    data: {
      labels: kwLabels,
      datasets: [
        { label: 'PC 검색수', data: pcData },
        { label: '모바일 검색수', data: moData }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top' },
        tooltip: { mode: 'index', intersect: false }
      },
      scales: {
        x: { ticks: { maxRotation: 60, minRotation: 40 }},
        y: { beginAtZero: true }
      }
    }
  });

  // 경쟁도 차트 - 좋은 키워드 색상 표시
  const compColors = compData.map((v, i) => {
    const total = pcData[i] + moData[i];
    if (total >= 100 && v <= 0.8) {
      return 'rgba(34, 197, 94, 0.9)';   // 초록 = 좋은 키워드
    } else if (v <= 0.9) {
      return 'rgba(245, 158, 11, 0.9)';  // 주황 = 중간
    } else {
      return 'rgba(239, 68, 68, 0.9)';   // 빨강 = 경쟁 높음
    }
  });

  const ctx2 = document.getElementById('compChart').getContext('2d');
  new Chart(ctx2, {
    type: 'bar',
    data: {
      labels: kwLabels,
      datasets: [
        {
          label: '경쟁도',
          data: compData,
          backgroundColor: compColors
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: true },
        tooltip: {
          callbacks: {
            label: function(context) {
              return '경쟁도: ' + context.parsed.y.toFixed(2);
            }
          }
        }
      },
      scales: {
        x: { ticks: { maxRotation: 60, minRotation: 40 }},
        y: { beginAtZero: true, max: 1.0 }
      }
    }
  });
</script>
{% endif %}
</body></html>
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

    # 그래프용 기본값
    chart_available = False
    chart_labels, chart_pc, chart_mo, chart_comp = [], [], [], []
    chart_count = 0

    # 기준 키워드별 요약 테이블 + 추천 키워드 조합
    summary_table = []
    recommended_groups = []

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

                    # 요약문
                    if not df_filtered.empty:
                        avg_total_all = int(df_filtered["총 검색수"].mean())
                        avg_comp_all = round(df_filtered["경쟁도"].mean(), 2)
                        summary_msg = (
                            f"총 {len(df_all)}개 키워드 중 "
                            f"{len(df_filtered)}개가 조건을 통과했습니다. "
                            f"평균 검색량 {avg_total_all:,}회, 평균 경쟁도 {avg_comp_all}입니다. "
                            f"(검색량 100 이상 & 경쟁도 0.8 이하 = 좋은 키워드)"
                        )
                    else:
                        summary_msg = "조건에 맞는 키워드가 없습니다."

                    # 🔹 추천 키워드 조합 (웹용) - 기준 키워드별로 생성
                    recommended_groups = []
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
                    msg = f"리포트 생성 완료. {summary_msg}"

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
    )


# ==========================
# 계정 관리 (관리자 전용)
# ==========================
ADMIN_HTML = """
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>계정 관리 - J&T Solution</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f5f7;max-width:800px;margin:40px auto;padding:0 16px;}
.card{background:white;padding:24px;border-radius:14px;box-shadow:0 6px 18px rgba(0,0,0,0.05);}
h1{font-size:20px;margin-bottom:12px;}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px;}
th,td{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;}
th{background:#f9fafb;}
form.inline{display:inline;}
input{padding:6px;border-radius:6px;border:1px solid #d1d5db;font-size:13px;margin-right:6px;}
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
    <tr><th>아이디</th><th>이름</th><th>비고</th><th>삭제</th></tr>
    {% for uid, info in accounts.items() %}
      <tr>
        <td>{{uid}}</td>
        <td>{{info.name}}</td>
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
      <input name="new_name" placeholder="표시 이름 (예: 강북제일자동차운전전문학원)">
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
            if not new_uid or not new_pw or not new_name:
                msg = "아이디, 비밀번호, 이름을 모두 입력해 주세요."
            elif new_uid in ACCOUNTS:
                msg = "이미 존재하는 아이디입니다."
            else:
                ACCOUNTS[new_uid] = {"password": new_pw, "name": new_name}
                save_accounts()
                msg = f"계정 '{new_uid}'이(가) 추가되었습니다."
        else:
            msg = "알 수 없는 동작입니다."

    accounts_for_view = {uid: type("obj", (), info) for uid, info in ACCOUNTS.items()}

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
