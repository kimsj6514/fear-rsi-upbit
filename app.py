import datetime as dt
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='Fear-RSI 매매 신호', page_icon='📈', layout='wide')
KST = ZoneInfo('Asia/Seoul')
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Fear-RSI-Signal-Dashboard/2.0'})

@st.cache_data(ttl=180, show_spinner=False)
def fetch_candles(market: str):
    url = 'https://api.upbit.com/v1/candles/minutes/240'
    response = SESSION.get(url, params={'market': market, 'count': 200}, timeout=12)
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError('업비트 캔들 데이터가 비어 있습니다.')
    df = pd.DataFrame(rows)
    df['start_kst'] = pd.to_datetime(df['candle_date_time_kst'])
    df = df.sort_values('start_kst').reset_index(drop=True)
    df['close'] = pd.to_numeric(df['trade_price'])
    df['high'] = pd.to_numeric(df['high_price'])
    df['low'] = pd.to_numeric(df['low_price'])
    df['volume'] = pd.to_numeric(df['candle_acc_trade_volume'])
    # API returns an in-progress candle; only use fully closed 4-hour candles.
    now = pd.Timestamp.now(tz=KST).tz_localize(None)
    df = df[df['start_kst'] + pd.Timedelta(hours=4) <= now].copy()
    if len(df) < 50:
        raise ValueError('확정된 4시간봉이 부족합니다.')
    return df

@st.cache_data(ttl=900, show_spinner=False)
def fetch_fear():
    response = SESSION.get('https://api.alternative.me/fng/', params={'limit': 1, 'format': 'json'}, timeout=12)
    response.raise_for_status()
    data = response.json()['data'][0]
    return int(data['value']), dt.datetime.fromtimestamp(int(data['timestamp']), tz=KST)


def rsi_wilder(series: pd.Series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, float('nan'))
    out = 100 - 100 / (1 + rs)
    out = out.mask((avg_down == 0) & (avg_up > 0), 100)
    out = out.mask((avg_up == 0) & (avg_down > 0), 0)
    return out


def signal_for(df: pd.DataFrame, fear: int):
    r = rsi_wilder(df['close'])
    last, prev = float(r.iloc[-1]), float(r.iloc[-2])
    close = float(df['close'].iloc[-1])
    prev_close = float(df['close'].iloc[-2])
    # Simple, intentionally non-predictive heuristic: low RSI + fear + RSI uptick;
    # high RSI + greed + RSI downtick. Signals are advisory only.
    buy = (fear <= 35 and last <= 40 and last > prev)
    sell = (fear >= 65 and last >= 60 and last < prev)
    if buy:
        label, icon, explanation = '매수 후보', '🟢', '시장 공포가 높고 RSI가 낮은 구간에서 RSI가 반등했습니다.'
    elif sell:
        label, icon, explanation = '매도 후보', '🔴', '시장 탐욕이 높고 RSI가 높은 구간에서 RSI가 하락했습니다.'
    else:
        label, icon = '신호 없음', '⚪'
        if fear <= 35 and last <= 40:
            explanation = '공포·낮은 RSI 조건이지만 RSI 반등이 아직 확인되지 않았습니다.'
        elif fear >= 65 and last >= 60:
            explanation = '탐욕·높은 RSI 조건이지만 RSI 하락 전환이 아직 확인되지 않았습니다.'
        else:
            explanation = '매수·매도 조건이 모두 충족되지 않아 관망 신호입니다.'
    return dict(label=label, icon=icon, explanation=explanation, rsi=last,
                price=close, change=(close / prev_close - 1) * 100,
                time=df['start_kst'].iloc[-1], rsi_series=r)

st.title('📈 Fear-RSI 매매 신호')
st.caption('저점 매수 · 고점 매도 후보만 간단하게 확인 | 업비트 4시간봉 + 시장 공포탐욕지수 | 자동 주문 없음')
with st.sidebar:
    st.header('설정')
    selected = st.multiselect('관심 종목', ['KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-ADA', 'KRW-DOGE'], default=['KRW-BTC', 'KRW-ETH'])
    st.caption('매수: 공포 ≤35, RSI ≤40에서 반등 / 매도: 탐욕 ≥65, RSI ≥60에서 하락 전환')
    if st.button('🔄 최신 데이터 새로고침', use_container_width=True):
        fetch_candles.clear()
        fetch_fear.clear()
        st.rerun()

if not selected:
    st.info('왼쪽에서 관심 종목을 선택해 주세요.')
    st.stop()

try:
    fear, fear_time = fetch_fear()
except Exception as exc:
    st.error(f'공포탐욕지수를 불러오지 못했습니다: {exc}. 신호를 계산하지 않습니다.')
    st.stop()

c1, c2 = st.columns(2)
c1.metric('시장 공포탐욕지수 (0~100)', fear)
c2.write(f'**공포탐욕 기준일:** {fear_time:%Y-%m-%d %H:%M} KST')
st.caption('공포탐욕지수는 전체 암호화폐 시장의 일간 지표입니다. 업비트 개별 코인 지표가 아닙니다.')

results = []
for market in selected:
    try:
        df = fetch_candles(market)
        info = signal_for(df, fear)
        results.append((market, df, info))
    except Exception as exc:
        st.warning(f'{market}: 시세 조회 실패 ({exc})')

if not results:
    st.error('조회 가능한 종목이 없습니다. 잠시 후 새로고침해 주세요.')
    st.stop()

st.subheader('지금 매수·매도 신호')
for market, df, info in results:
    with st.container(border=True):
        a, b = st.columns([2, 1])
        with a:
            st.markdown(f"### {market}  ·  {info['icon']} {info['label']}")
            st.write(info['explanation'])
            st.caption(f"마지막 확정 4시간봉 시작: {info['time']:%Y-%m-%d %H:%M} KST")
        with b:
            st.metric('확정봉 종가', f"{info['price']:,.0f}원", f"{info['change']:+.2f}% (직전 봉 대비)")
            st.metric('RSI (14)', f"{info['rsi']:.1f}")
        with st.expander('최근 가격과 RSI 보기'):
            chart = df[['start_kst', 'close']].tail(60).set_index('start_kst')
            st.line_chart(chart, y='close', height=200)
            rsi_chart = pd.DataFrame({'RSI(14)': info['rsi_series'].tail(60).values}, index=df['start_kst'].tail(60))
            st.line_chart(rsi_chart, height=160)

st.info('신호는 확정된 4시간봉과 가장 최근 공개된 일간 공포탐욕지수로 계산합니다. 실시간 체결가와 다를 수 있습니다. 신호는 수익을 보장하지 않으며 매수·매도 결정은 직접 하세요.')
st.caption('데이터: 업비트 공개 캔들 API / Alternative.me Crypto Fear & Greed Index. 로그인·API 키·자동매매 기능 없음.')
