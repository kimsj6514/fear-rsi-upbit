import time
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

st.set_page_config(page_title='Fear-RSI | 업비트 퀀트', page_icon='📈', layout='wide')
st.markdown('''<style>.block-container{padding-top:1.5rem;max-width:1250px}div[data-testid="stMetric"]{background:rgba(128,128,128,.07);padding:14px;border-radius:12px}</style>''',unsafe_allow_html=True)
KST=timezone(timedelta(hours=9))
SESSION=requests.Session()
SESSION.headers.update({'User-Agent':'FearRSIResearchDashboard/1.0'})

@st.cache_data(ttl=900,show_spinner=False)
def candles(market, days):
    count=int(days*6+1250)
    all_rows=[]; to=None
    while len(all_rows)<count:
        params={'market':market,'count':min(200,count-len(all_rows))}
        if to:params['to']=to
        for attempt in range(4):
            try:
                r=SESSION.get('https://api.upbit.com/v1/candles/minutes/240',params=params,timeout=20)
                if r.status_code==429:
                    time.sleep(1.5*(attempt+1));continue
                r.raise_for_status(); batch=r.json();break
            except (requests.RequestException,ValueError):
                if attempt==3:raise
                time.sleep(1+attempt)
        if not batch:break
        all_rows.extend(batch)
        oldest=pd.Timestamp(batch[-1]['candle_date_time_utc'],tz='UTC')
        to=oldest.strftime('%Y-%m-%dT%H:%M:%S')+'Z'
        time.sleep(.13)
    df=pd.DataFrame(all_rows)
    if df.empty:raise ValueError('캔들 데이터가 없습니다.')
    df['ts']=pd.to_datetime(df['candle_date_time_utc'],utc=True)
    df=df.sort_values('ts').drop_duplicates('ts').set_index('ts')
    df=df.rename(columns={'opening_price':'open','high_price':'high','low_price':'low','trade_price':'close','candle_acc_trade_volume':'volume'})
    df=df[['open','high','low','close','volume']].astype(float)
    # 4시간봉은 확정 시점에만 신호로 사용
    df=df[df.index+pd.Timedelta(hours=4)<=pd.Timestamp.now(tz='UTC')]
    return df

@st.cache_data(ttl=3600,show_spinner=False)
def fear_history():
    r=SESSION.get('https://api.alternative.me/fng/',params={'limit':0,'format':'json'},timeout=30)
    r.raise_for_status(); raw=r.json()['data']
    f=pd.DataFrame(raw)
    f['date']=pd.to_datetime(f['timestamp'].astype(int),unit='s',utc=True).dt.floor('D')
    f['fear']=pd.to_numeric(f['value'],errors='coerce')
    return f[['date','fear']].dropna().drop_duplicates('date').sort_values('date')

def rsi_wilder(series,period=14):
    delta=series.diff(); up=delta.clip(lower=0);down=-delta.clip(upper=0)
    avg_up=up.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    avg_down=down.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    rs=avg_up/avg_down.replace(0,np.nan)
    result=100-100/(1+rs)
    result= result.mask((avg_down==0)&(avg_up>0),100).mask((avg_down==0)&(avg_up==0),50)
    return result

def prepare(df,fear):
    d=df.copy();d['rsi']=rsi_wilder(d.close)
    # Conservative: only use the previous UTC day's published index; avoids same-day look-ahead.
    d['utc_day']=d.index.floor('D')-pd.Timedelta(days=1)
    f=fear.set_index('date')['fear']
    d['fear']=d['utc_day'].map(f)
    # Daily close defined at UTC midnight. Shift one full day to ensure daily candle is complete.
    daily=d['close'].resample('1D').last().dropna()
    sma=daily.rolling(200,min_periods=200).mean()
    prev_sma=sma.shift(1)
    d['sma200']=d.index.floor('D').map(prev_sma)
    d['trend_ok']=d['close']>d['sma200']
    d['cross_up']=(d.rsi>=30)&(d.rsi.shift(1)<30)
    d['buy_signal']=d.cross_up & (d.fear<=35) & d.trend_ok
    return d

def backtest(d,capital,allocation,fee_pct,slip_pct):
    # Decisions at completed candle close; entry at following open; stop/take checked intrabar.
    cash=float(capital);qty=0.;entry=0.;entry_ts=None;entry_cost=0.;trades=[];curve=[]
    fee=fee_pct/100; slip=slip_pct/100
    pending_buy=False;pending_sell=False
    for i in range(1,len(d)):
        bar=d.iloc[i];prev=d.iloc[i-1];ts=d.index[i]
        if qty>0 and pending_sell:
            px=float(bar.open)*(1-slip);proceeds=qty*px*(1-fee)
            cash+=proceeds;trades.append({'진입':entry_ts,'청산':ts,'진입가':entry,'청산가':px,'순손익':proceeds-entry_cost,'수익률(%)':(proceeds/entry_cost-1)*100,'사유':'RSI/시간 청산'})
            qty=0.;pending_sell=False
        if qty==0 and pending_buy and pd.notna(bar.open):
            spend=cash*allocation/100
            if spend>=5000:
                entry=float(bar.open)*(1+slip);qty=spend/(entry*(1+fee));entry_cost=spend;cash-=spend;entry_ts=ts
            pending_buy=False
        if qty>0:
            stop=entry*.975;take=entry*1.05
            reason=None;px=None
            if bar.low<=stop:
                px=stop*(1-slip);reason='손절 (보수적 우선)'
            elif bar.high>=take:
                px=take*(1-slip);reason='익절'
            if reason:
                proceeds=qty*px*(1-fee);cash+=proceeds
                trades.append({'진입':entry_ts,'청산':ts,'진입가':entry,'청산가':px,'순손익':proceeds-entry_cost,'수익률(%)':(proceeds/entry_cost-1)*100,'사유':reason})
                qty=0.;pending_sell=False
            else:
                if pd.notna(bar.rsi) and (bar.rsi>=70 or ts-entry_ts>=pd.Timedelta(hours=120)):
                    pending_sell=True
        if qty==0 and bool(bar.buy_signal):pending_buy=True
        curve.append((ts,cash+qty*float(bar.close)))
    equity=pd.Series(dict(curve),name='equity')
    t=pd.DataFrame(trades)
    final=float(equity.iloc[-1]) if len(equity) else capital
    dd=(equity/equity.cummax()-1).min()*100 if len(equity) else 0
    return {'equity':equity,'trades':t,'final':final,'return':(final/capital-1)*100,'mdd':dd,'wins':(t['순손익']>0).mean()*100 if len(t) else np.nan}

st.title('📈 Fear-RSI 퀀트 대시보드')
st.caption('업비트 KRW 현물 · BTC/ETH · 4시간봉 RSI(14) + Crypto Fear & Greed · 투자 연구용')
with st.sidebar:
    st.header('전략 설정')
    market=st.selectbox('분석 종목',['KRW-BTC','KRW-ETH'])
    days=st.select_slider('분석 기간 (일)',options=[180,365,540,720],value=365)
    capital=st.number_input('가상 투자금 (원)',min_value=10000,max_value=100000000,step=100000,value=500000)
    allocation=st.slider('진입 시 현금 투입 비율 (%)',10,100,25,5)
    fee=st.number_input('편도 수수료 (%)',0.,1.,0.05,0.01)
    slip=st.number_input('편도 슬리피지 (%)',0.,2.,0.10,0.05)
    st.caption('기본값은 검증된 최적값이 아닙니다. 실거래 전 실제 수수료를 확인하세요.')
    if st.button('데이터 새로고침'):
        candles.clear();fear_history.clear();st.rerun()

try:
    with st.spinner('업비트 캔들 및 공포탐욕지수 수집 중… (최초 실행 시 시간이 걸립니다)'):
        raw=candles(market,days);fear=fear_history();d=prepare(raw,fear)
except Exception as exc:
    st.error(f'데이터를 불러오지 못했습니다: {exc}')
    st.info('네트워크 연결 및 업비트/Alternative.me API 접근 가능 여부를 확인하고 새로고침하세요.')
    st.stop()

# Exclude warmup from displayed performance; retain all data for indicators.
cutoff=d.index.max()-pd.Timedelta(days=days)
analysis=d[d.index>=cutoff].copy()
result=backtest(analysis,capital,allocation,fee,slip)
latest=d.iloc[-1]
latest_signal='매수 후보' if bool(latest.buy_signal) else '관망 (조건 미충족)'
cols=st.columns(5)
cols[0].metric('최근 종가',f'{latest.close:,.0f}원')
cols[1].metric('RSI(14)',f'{latest.rsi:.1f}' if pd.notna(latest.rsi) else 'N/A')
cols[2].metric('공포탐욕 (전일)',f'{latest.fear:.0f}' if pd.notna(latest.fear) else 'N/A')
cols[3].metric('백테스트 수익률',f'{result["return"]:+.2f}%')
cols[4].metric('최대 낙폭 MDD',f'{result["mdd"]:.2f}%')
st.info(f'**현재 판정: {latest_signal}** · 마지막 확정봉 시작(한국시간): {d.index[-1].tz_convert(KST):%Y-%m-%d %H:%M} · 조건: 전일 공포≤35, RSI 30 상향 돌파, 200일선 위')
st.warning('실시간 주문 신호가 아닙니다. 업비트 캔들/지수 갱신 지연이 있을 수 있습니다. 4시간봉 확정 후 다음 봉 시가 진입을 가정합니다.')

st.subheader('가격 · 매수 신호 · RSI')
view=analysis.tail(300)
fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.7,.3],vertical_spacing=.05)
fig.add_trace(go.Candlestick(x=view.index,open=view.open,high=view.high,low=view.low,close=view.close,name='4H 가격'),row=1,col=1)
fig.add_trace(go.Scatter(x=view.index,y=view.sma200,name='전일 기준 일봉 SMA200',line=dict(color='#e6a23c',width=1.5)),row=1,col=1)
buys=view[view.buy_signal]
fig.add_trace(go.Scatter(x=buys.index,y=buys.low,mode='markers',marker=dict(symbol='triangle-up',size=12,color='#17a67a'),name='매수 후보'),row=1,col=1)
fig.add_trace(go.Scatter(x=view.index,y=view.rsi,name='RSI(14)',line=dict(color='#7797f8')),row=2,col=1)
for v in (30,70):fig.add_hline(y=v,line_dash='dash',line_color='gray',row=2,col=1)
fig.update_layout(height=620,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=15,b=5),legend=dict(orientation='h'),template='plotly_dark')
st.plotly_chart(fig,use_container_width=True)

st.subheader('공포탐욕지수 (전일 확정값 사용)')
fchart=analysis[['fear']].dropna().iloc[::6]
st.line_chart(fchart)

st.subheader('백테스트 성과')
c1,c2,c3,c4=st.columns(4)
t=result['trades'];c1.metric('완료 거래',f'{len(t)}회');c2.metric('승률',f'{result["wins"]:.1f}%' if len(t) else 'N/A');c3.metric('최종 평가금액',f'{result["final"]:,.0f}원');c4.metric('단순 보유 수익률',f'{(analysis.close.iloc[-1]/analysis.close.iloc[0]-1)*100:+.2f}%')
st.line_chart(result['equity'])
st.caption('미청산 포지션은 마지막 종가로 평가합니다. 백테스트는 시장가 슬리피지·수수료 가정을 반영하나 급변 시 실제 체결을 보장하지 않습니다.')
if len(t):
    st.dataframe(t.sort_values('청산',ascending=False),use_container_width=True,hide_index=True)
    st.download_button('거래 기록 CSV 다운로드',t.to_csv(index=False).encode('utf-8-sig'),file_name=f'{market}_fear_rsi_trades.csv',mime='text/csv')
else:st.info('설정한 기간에 완료된 거래가 없습니다. 전략이 적합하지 않을 수도 있으므로 신호를 억지로 만들지 마세요.')
with st.expander('백테스트 가정 및 한계 (필독)'):
    st.markdown('''- **매수:** 확정된 4시간봉에서 RSI 30 상향 돌파, 전일 공포탐욕 ≤35, 전일 확정 일봉 SMA200 위. 다음 4시간봉 시가 진입.\n- **매도:** -2.5% 손절 / +5% 익절 / RSI≥70 / 120시간 보유. RSI·시간 조건은 다음 봉 시가 청산.\n- **동일 봉 손절·익절 동시 충족:** 손절 우선. 갭과 슬리피지 모델은 단순화되어 있음.\n- **공포탐욕지수:** 당일 지수는 사용하지 않고 전일 UTC 일자 값만 사용. 실제 발표 시각에 따른 엄밀한 가용성은 추가 검증 필요.\n- **일봉 추세:** 전일 확정된 일봉으로 계산한 SMA200 사용.\n- **수익률:** 현금 잔액과 미청산 평가금 포함. 단순 보유와 비교하되 동일 노출 비율 비교는 아님.\n- **주의:** 과거 성과는 미래 수익을 보장하지 않음. 거래소 API 결측·서버 시각·수수료·실제 체결을 별도 검증해야 함.\n- **검증:** 초기 결과로 매개변수를 반복 최적화하지 말고 개발/검증/미사용 테스트 기간을 분리하세요.''')
