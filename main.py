# -*- coding: utf-8 -*-
"""
어제의 박스오피스 - Streamlit 앱
--------------------------------
영화진흥위원회(KOBIS) 공식 Open API를 사용해서
'어제' 하루 동안의 일별 박스오피스 순위를 보여주는 앱입니다.

배포: Streamlit Cloud
인증키: Streamlit Cloud의 Secrets 설정(.streamlit/secrets.toml 또는 웹 UI)에서
       KOBIS_KEY 라는 이름으로 등록해서 사용합니다. 코드에는 절대 쓰지 않습니다.

예시 secrets.toml 내용 (Streamlit Cloud > Settings > Secrets 에 붙여넣기):
    KOBIS_KEY = "여기에_발급받은_인증키"
"""

import requests
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------------

st.set_page_config(
    page_title="어제의 박스오피스",
    page_icon="🎬",
    layout="wide",
)

KOBIS_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

# 배포 서버의 시계는 한국 시간(KST)이 아닐 수 있으므로,
# UTC 기준 현재 시각을 구한 뒤 +9시간을 직접 더해서 '한국 기준 지금'을 계산합니다.
KST = timezone(timedelta(hours=9))


def get_yesterday_kst() -> str:
    """한국 시간(KST) 기준으로 '어제' 날짜를 yyyymmdd 문자열로 반환합니다.

    오늘 날짜의 박스오피스는 KOBIS 쪽 집계가 아직 끝나지 않은 경우가 많아서
    항상 '어제' 데이터를 조회합니다.
    """
    now_kst = datetime.now(KST)
    yesterday_kst = now_kst - timedelta(days=1)
    return yesterday_kst.strftime("%Y%m%d")


def format_date_for_display(yyyymmdd: str) -> str:
    """yyyymmdd 문자열을 'YYYY년 MM월 DD일' 형태로 예쁘게 바꿔줍니다."""
    dt = datetime.strptime(yyyymmdd, "%Y%m%d")
    return dt.strftime("%Y년 %m월 %d일")


# ------------------------------------------------------------------
# API 호출 (같은 날짜는 1시간 동안 캐시해서 재호출하지 않음)
# ------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_box_office(target_dt: str, api_key: str):
    """KOBIS API에서 일별 박스오피스를 가져옵니다.

    반환값: (성공 여부, 결과 데이터 또는 에러 메시지)
    - 성공하면 (True, DataFrame) 을 반환합니다.
    - 실패하면 (False, "사용자에게 보여줄 한국어 안내 문구") 를 반환합니다.

    st.cache_data 덕분에 같은 target_dt로 다시 요청해도
    1시간(3600초) 안에는 실제 API를 다시 부르지 않고 캐시된 결과를 씁니다.
    """
    params = {
        "key": api_key,
        "targetDt": target_dt,
    }

    # 1) 네트워크 요청 자체가 실패하는 경우 (타임아웃, 연결 오류 등)
    try:
        response = requests.get(KOBIS_URL, params=params, timeout=10)
    except requests.exceptions.RequestException:
        return False, (
            "KOBIS 서버에 연결하지 못했습니다. "
            "인터넷 연결 상태를 확인하거나 잠시 후 다시 시도해 주세요."
        )

    # 2) HTTP 상태코드가 200이 아닌 경우 (문서상 흔치는 않지만 방어적으로 체크)
    if response.status_code != 200:
        return False, (
            f"KOBIS 서버가 오류 상태코드({response.status_code})를 반환했습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    # 3) 응답이 JSON 형식이 아닌 경우
    try:
        data = response.json()
    except ValueError:
        return False, (
            "KOBIS 서버 응답을 이해할 수 없습니다(JSON 형식이 아님). "
            "요청 주소나 파라미터가 올바른지 확인해 주세요."
        )

    # 4) 인증키가 틀렸을 때 등 - 상태코드는 200이지만 faultInfo 상자가 오는 경우
    if "faultInfo" in data:
        fault = data["faultInfo"]
        fault_msg = fault.get("message", "알 수 없는 오류")
        return False, (
            f"KOBIS API가 오류를 반환했습니다: {fault_msg}\n\n"
            "Streamlit Cloud의 Secrets에 등록한 KOBIS_KEY 값이 정확한지, "
            "그리고 인증키가 아직 유효한지 확인해 주세요."
        )

    # 5) 정상 구조인지 확인 (boxOfficeResult > dailyBoxOfficeList)
    box_office_result = data.get("boxOfficeResult")
    if not box_office_result:
        return False, (
            "응답에서 boxOfficeResult 항목을 찾을 수 없습니다. "
            "KOBIS API 응답 구조가 바뀌었을 수 있으니 공식 문서를 다시 확인해 주세요."
        )

    movie_list = box_office_result.get("dailyBoxOfficeList")
    if not movie_list:
        return False, (
            f"{format_date_for_display(target_dt)}의 박스오피스 데이터가 비어 있습니다. "
            "조회 날짜가 너무 이르거나(그날 데이터가 아직 없음), "
            "targetDt 값이 올바른지 확인해 주세요."
        )

    df = pd.DataFrame(movie_list)

    # 문서에 나온 대로 숫자 항목이 전부 문자열로 오기 때문에,
    # 정렬과 그래프에 쓸 수 있도록 숫자형(int)으로 변환합니다.
    numeric_cols = ["rank", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 혹시 숫자 변환에 실패한(NaN) 행이 있으면 순위 계산에 방해되므로 제거
    df = df.dropna(subset=["rank"])
    df["rank"] = df["rank"].astype(int)
    df = df.sort_values("rank").reset_index(drop=True)

    return True, df


# ------------------------------------------------------------------
# 화면 그리기
# ------------------------------------------------------------------

def render_top_movie_cards(top_movie: pd.Series):
    """1위 영화를 큰 지표 카드 3장으로 보여줍니다."""
    st.subheader(f"🏆 1위: {top_movie['movieNm']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("오늘의 관객수", f"{int(top_movie['audiCnt']):,}명")
    col2.metric("누적 관객수", f"{int(top_movie['audiAcc']):,}명")
    col3.metric("스크린 수", f"{int(top_movie['scrnCnt']):,}개")


def render_top5_chart(df: pd.DataFrame):
    """관객수 기준 상위 5편을 막대그래프로 보여줍니다."""
    top5 = df.sort_values("audiCnt", ascending=False).head(5)

    fig = go.Figure(
        data=[
            go.Bar(
                x=top5["movieNm"],
                y=top5["audiCnt"],
                marker_color="#E07A5F",  # 따뜻한 톤(테라코타)
                text=top5["audiCnt"].map(lambda v: f"{v:,}명"),
                textposition="outside",
            )
        ]
    )
    fig.update_layout(
        title="관객수 상위 5편",
        xaxis_title="영화명",
        yaxis_title="관객수(명)",
        plot_bgcolor="#FFF8F0",
        paper_bgcolor="#FFF8F0",
        font_color="#4A3B32",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_table(df: pd.DataFrame):
    """전체 순위표를 보여줍니다."""
    table_df = df[["rank", "movieNm", "openDt", "audiCnt", "audiAcc", "scrnCnt"]].copy()
    table_df.columns = ["순위", "영화명", "개봉일", "관객수", "누적관객수", "스크린수"]

    # 보기 좋게 천 단위 콤마를 넣은 문자열로 변환 (표 전용, 그래프에는 숫자 원본 사용)
    for col in ["관객수", "누적관객수", "스크린수"]:
        table_df[col] = table_df[col].map(lambda v: f"{v:,}")

    st.dataframe(table_df, use_container_width=True, hide_index=True)


def main():
    st.title("🎬 어제의 박스오피스")

    target_dt = get_yesterday_kst()
    st.caption(f"조회 기준일: {format_date_for_display(target_dt)} (한국 시간 기준 '어제')")

    # Streamlit Cloud의 Secrets에서 인증키를 불러옵니다.
    api_key = st.secrets.get("KOBIS_KEY")
    if not api_key:
        st.error(
            "KOBIS_KEY가 설정되어 있지 않습니다.\n\n"
            "Streamlit Cloud > 앱 설정 > Secrets 에서 다음과 같이 등록해 주세요:\n\n"
            'KOBIS_KEY = "발급받은_인증키"'
        )
        st.stop()

    with st.spinner("박스오피스 정보를 불러오는 중입니다..."):
        success, result = fetch_box_office(target_dt, api_key)

    if not success:
        # 빈 화면 대신, 무엇을 확인해야 하는지 안내합니다.
        st.error(result)
        st.stop()

    df = result

    top_movie = df.iloc[0]
    render_top_movie_cards(top_movie)

    st.divider()
    render_top5_chart(df)

    st.divider()
    st.subheader("전체 순위표")
    render_table(df)


if __name__ == "__main__":
    main()
