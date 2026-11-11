import streamlit as st
from signals import MARKETS, analyze, fetch_candles, fetch_fear

st.set_page_config(page_title='Fear-RSI 신호', page_icon='📈', layout='wide')
st.title('📈 Fear-RSI 매수·매도 신호')
st.caption('BTC · ETH · XRP · DOGE | 1시간·4시간·1일 확정봉 | 자동 주문 없음')

@st.cache_data(ttl=180, show_spinner=False)
def candles(market, frame):
    return fetch_candles(market, frame)

@st.cache_data(ttl=900, show_spinner=False)
def fear_index():
    return fetch_fear()

with st.sidebar:
    selected = st.multiselect('관심 종목', MARKETS, default=MARKETS)
    if st.button('🔄 새로고침', use_container_width=True):
        candles.clear()
        fear_index.clear()
        st.rerun()
    st.caption('매수: 공포 ≤35 + RSI ≤40 + RSI 상승')
    st.caption('매도: 탐욕 ≥65 + RSI ≥60 + RSI 하락')
    st.caption('강한 신호: 1시간봉과 4시간봉 같은 방향. 30일 하락률과 일봉 추세는 참고 정보입니다.')

if not selected:
    st.info('종목을 선택해 주세요.')
    st.stop()
try:
    fear, fear_time = fear_index()
except Exception as exc:
    st.error(f'공포탐욕지수 조회 실패: {exc}')
    st.stop()
st.metric('시장 공포탐욕지수', fear)
st.caption(f'공포탐욕 기준: {fear_time:%Y-%m-%d %H:%M} KST · 시장 전체 일간 지표')
for market in selected:
    try:
        result = analyze(market, fear, fetch=candles)
    except Exception as exc:
        st.warning(f'{market} 조회 실패: {exc}')
        continue
    label = result['label']
    symbol = '🟢' if '매수' in label else ('🔴' if '매도' in label else '⚪')
    with st.container(border=True):
        st.subheader(f'{symbol} {market} · {label}')
        st.write(result['reason'])
        a, b, c = st.columns(3)
        a.metric('최근 1시간 확정봉 종가', f"{result['price']:,.0f}원")
        b.metric('30일 고점 대비', f"{result['drop30']:+.1f}%")
        c.metric('일봉 방향', result['day_trend'])
        st.caption(f"30일 최고가 {result['high30']:,.0f}원 · 일봉 방향은 최근 확정 일봉 종가 2개 비교 · 현재 체결가는 아님")
        st.link_button('📊 업비트 차트 열기', result['chart_url'])
        columns = st.columns(3)
        for col, (frame, info) in zip(columns, result['frames'].items()):
            with col:
                st.markdown(f'**{frame}**')
                st.write(('🟢 ' if info['direction'] == '매수' else '🔴 ' if info['direction'] == '매도' else '⚪ ') + (info['direction'] + ' 후보' if info['direction'] != '없음' else '신호 없음'))
                st.metric('RSI(14)', f"{info['rsi']:.1f}")
                st.caption(f"확정봉 종가 {info['price']:,.0f}원 · 시작 {info['time']} KST")
        with st.expander('시간봉별 최근 가격 보기'):
            frame = st.selectbox('차트 시간봉', list(result['frames']), key=f'chart-{market}')
            st.line_chart(candles(market, frame).set_index('start')['close'].tail(80))
st.info('매수·매도는 참고 신호입니다. 30일 고점 대비 하락률이 크더라도 저점이나 반등을 보장하지 않습니다.')
st.caption('업비트 공개 API · Alternative.me | 텔레그램 신호 발생 가격은 GitHub 저장소의 signal_history.csv에 기록')
