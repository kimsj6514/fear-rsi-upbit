import streamlit as st
from signals import MARKETS, analyze, fetch_candles, fetch_fear

st.set_page_config(page_title='Fear-RSI 신호', page_icon='📈', layout='wide')
st.title('📈 Fear-RSI 매수·매도 신호')
st.caption('업비트 1시간봉 · 4시간봉 · 1일봉 | 확정봉 기준 | 자동 주문 없음')

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
    st.caption('매수: 공포 ≤35 + RSI ≤40 + RSI 상승\n\n매도: 탐욕 ≥65 + RSI ≥60 + RSI 하락')
    st.caption('강한 신호: 1시간봉과 4시간봉 방향 일치. 일봉 일치 시 추가 표시.')

if not selected:
    st.info('종목을 선택해 주세요.')
    st.stop()
try:
    fear, fear_time = fear_index()
except Exception as exc:
    st.error(f'공포탐욕지수를 불러올 수 없어 신호 계산을 중단합니다: {exc}')
    st.stop()
st.metric('시장 공포탐욕지수', fear)
st.caption(f'공포탐욕 기준일: {fear_time:%Y-%m-%d %H:%M} KST · 암호화폐 시장 전체의 일간 지표')
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
        columns = st.columns(3)
        for col, (frame, info) in zip(columns, result['frames'].items()):
            with col:
                st.markdown(f'**{frame}**')
                st.write(('🟢 ' if info['direction'] == '매수' else '🔴 ' if info['direction'] == '매도' else '⚪ ') + (info['direction'] + ' 후보' if info['direction'] != '없음' else '신호 없음'))
                st.metric('RSI(14)', f"{info['rsi']:.1f}")
                st.caption(f"확정봉 종가 {info['price']:,.0f}원")
                st.caption(f"봉 시작 {info['time']} KST")
        with st.expander('시간봉별 최근 가격 보기'):
            frame = st.selectbox('차트 시간봉', list(result['frames']), key=f'chart-{market}')
            data = candles(market, frame)
            st.line_chart(data.set_index('start')['close'].tail(80))
st.info('신호는 확정봉으로 계산하므로 현재 체결가보다 늦을 수 있습니다. 정확한 저점·고점이나 수익을 보장하지 않습니다.')
st.caption('데이터: 업비트 공개 API · Alternative.me 공포탐욕지수 | 자동매매 기능 없음')
