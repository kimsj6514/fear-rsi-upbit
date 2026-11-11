import time
from datetime import timezone, timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title='Fear-RSI v1.2 | 업비트 퀀트',page_icon='📈',layout='wide')
st.markdown('<style>.block-container{padding-top:1.3rem;max-width:1250px}div[data-testid="stMetric"]{background:rgba(128,128,128,.07);padding:12px;border-radius:10px}</style>',unsafe_allow_html=True)
KST=timezone(timedelta(hours=9))
SESSION=requests.Session(); SESSION.headers.update({'User-Agent':'FearRSIResearchDashboard/1.2'})

@st.cache_data(ttl=900,show_spinner=False)
def candles(market,days):
    count=int(days*6+1250); rows=[]; cursor=None
    while len(rows)<count:
        params={'market':market,'count':min(200,count-len(rows))}
        if cursor: params['to']=cursor
        for attempt in range(4):
            try:
                response=SESSION.get('https://api.upbit.com/v1/candles/minutes/240',params=params,timeout=20)
                if response.status_code==429:
                    time.sleep(1.5*(attempt+1));continue
                response.raise_for_status();batch=response.json();break
            except (requests.RequestException,ValueError):
                if attempt==3:raise
                time.sleep(1+attempt)
        if not batch:break
        rows.extend(batch)
        oldest=pd.Timestamp(batch[-1]['candle_date_time_utc'],tz='UTC')
        cursor=oldest.strftime('%Y-%m-%dT%H:%M:%S')+'Z'
        time.sleep(.13)
    if not rows:raise ValueError('캔들 데이터가 없습니다.')
    d=pd.DataFrame(rows)
    d['ts']=pd.to_datetime(d['candle_date_time_utc'],utc=True)
    d=d.sort_values('ts').drop_duplicates('ts').set_index('ts')
    d=d.rename(columns={'opening_price':'open','high_price':'high','low_price':'low','trade_price':'close','candle_acc_trade_volume':'volume'})
    d=d[['open','high','low','close','volume']].astype(float)
    d=d[d.index+pd.Timedelta(hours=4)<=pd.Timestamp.now(tz='UTC')]
    return d

@st.cache_data(ttl=3600,show_spinner=False)
def fear_history():
    response=SESSION.get('https://api.alternative.me/fng/',params={'limit':0,'format':'json'},timeout=30)
    response.raise_for_status();f=pd.DataFrame(response.json()['data'])
    f['date']=pd.to_datetime(f['timestamp'].astype(int),unit='s',utc=True).dt.floor('D')
    f['fear']=pd.to_numeric(f['value'],errors='coerce')
    return f[['date','fear']].dropna().drop_duplicates('date').sort_values('date')

def rsi_wilder(series,period=14):
    delta=series.diff();up=delta.clip(lower=0);down=-delta.clip(upper=0)
    au=up.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    ad=down.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
    out=100-100/(1+au/ad.replace(0,np.nan))
    return out.mask((ad==0)&(au>0),100).mask((ad==0)&(au==0),50)

def prepare(raw,fear,rsi_entry,fear_limit):
    d=raw.copy();d['rsi']=rsi_wilder(d.close)
    d['fear']=(d.index.floor('D')-pd.Timedelta(days=1)).map(fear.set_index('date')['fear'])
    daily=d.close.resample('1D').last().dropna()
    d['sma200']=d.index.floor('D').map(daily.rolling(200,min_periods=200).mean().shift(1))
    d['trend_ok']=d.close>d.sma200
    d['cross_up']=(d.rsi>=rsi_entry)&(d.rsi.shift(1)<rsi_entry)
    d['signal_a']=d.cross_up
    d['signal_b']=d.cross_up & (d.fear<=fear_limit)
    d['signal_c']=d.signal_b & d.trend_ok
    return d

def backtest(d,capital,allocation,fee_pct,slip_pct,signal_col,stop_pct,take_pct,hold_hours):
    cash=float(capital);qty=0.;entry=0.;entry_ts=None;entry_cost=0.;trades=[];curve=[]
    fee=fee_pct/100;slip=slip_pct/100
    pending_buy=False;pending_sell=False;pending_reason=None;bars_in_position=0;position_bars=0
    for i in range(1,len(d)):
        bar=d.iloc[i];ts=d.index[i]
        if qty>0 and pending_sell:
            px=float(bar.open)*(1-slip);proceeds=qty*px*(1-fee)
            trades.append({'진입시각':entry_ts,'청산시각':ts,'진입가':entry,'청산가':px,'투입금':entry_cost,'순손익(원)':proceeds-entry_cost,'수익률(%)':(proceeds/entry_cost-1)*100,'청산사유':pending_reason,'보유시간(h)':(ts-entry_ts).total_seconds()/3600})
            cash+=proceeds;qty=0.;pending_sell=False;pending_reason=None
        if qty==0 and pending_buy:
            spend=cash*allocation/100
            if spend>=5000:
                entry=float(bar.open)*(1+slip);qty=spend/(entry*(1+fee));entry_cost=spend;cash-=spend;entry_ts=ts
            pending_buy=False
        if qty>0:
            position_bars+=1
            stop=entry*(1-stop_pct/100);take=entry*(1+take_pct/100)
            reason=None;px=None
            # Conservative stop-first model; an open gap beyond stop fills at the worse opening price.
            if float(bar.low)<=stop:
                px=min(stop,float(bar.open))*(1-slip);reason='손절'
            elif float(bar.high)>=take:
                px=max(take,float(bar.open))*(1-slip);reason='익절'
            if reason:
                proceeds=qty*px*(1-fee)
                trades.append({'진입시각':entry_ts,'청산시각':ts,'진입가':entry,'청산가':px,'투입금':entry_cost,'순손익(원)':proceeds-entry_cost,'수익률(%)':(proceeds/entry_cost-1)*100,'청산사유':reason,'보유시간(h)':(ts-entry_ts).total_seconds()/3600})
                cash+=proceeds;qty=0.;pending_sell=False;pending_reason=None
            elif pd.notna(bar.rsi) and bar.rsi>=70:
                pending_sell=True;pending_reason='RSI 70 청산'
            elif ts-entry_ts>=pd.Timedelta(hours=hold_hours):
                pending_sell=True;pending_reason='시간 만료'
        if qty==0 and not pending_buy and bool(bar[signal_col]):pending_buy=True
        curve.append((ts,cash+qty*float(bar.close)))
    equity=pd.Series(dict(curve),name='평가금액')
    t=pd.DataFrame(trades)
    final=float(equity.iloc[-1]) if len(equity) else capital
    dd=float((equity/equity.cummax()-1).min()*100) if len(equity) else 0.
    return {'equity':equity,'trades':t,'final':final,'return':(final/capital-1)*100,'mdd':dd,
            'wins':float((t['순손익(원)']>0).mean()*100) if len(t) else np.nan,
            'open_position':qty>0,'open_value':qty*float(d.close.iloc[-1]) if qty>0 else 0.,
            'exposure':position_bars/max(1,len(d)-1)*100}

st.title('📈 Fear-RSI 퀀트 대시보드 v1.2')
st.caption('업비트 KRW 현물 · 4시간봉 · 거래별 손실 원인 분석 · 투자 연구용 (자동 주문 없음)')
with st.sidebar:
    st.header('백테스트 설정')
    market=st.selectbox('종목',['KRW-BTC','KRW-ETH'])
    days=st.select_slider('분석 기간 (일)',options=[180,365,540,720],value=365)
    capital=st.number_input('초기 가상 투자금 (원)',min_value=10000,max_value=100000000,value=500000,step=100000)
    allocation=st.slider('진입 시 현금 투입 비율 (%)',10,100,25,5)
    fee=st.number_input('편도 수수료 (%)',0.,1.,0.05,0.01)
    slip=st.number_input('편도 슬리피지 (%)',0.,2.,0.10,0.05)
    with st.expander('실험용 조건 변경 (기본값 유지 권장)'):
        rsi_entry=st.slider('RSI 상향 돌파 기준',20,45,30)
        fear_limit=st.slider('공포탐욕 상한',15,60,35)
        stop_pct=st.slider('손절폭 (%)',1.0,8.0,2.5,0.5)
        take_pct=st.slider('익절폭 (%)',2.0,15.0,5.0,0.5)
        hold_hours=st.slider('최대 보유시간 (h)',24,240,120,12)
    if st.button('API 데이터 새로고침'):
        candles.clear();fear_history.clear();st.rerun()
try:
    with st.spinner('업비트 및 공포탐욕지수 수집 중…'):
        raw=candles(market,days);fear=fear_history();d=prepare(raw,fear,rsi_entry,fear_limit)
except Exception as exc:
    st.error(f'데이터를 불러오지 못했습니다: {exc}');st.stop()
cutoff=d.index.max()-pd.Timedelta(days=days)
a=d[d.index>=cutoff].copy()
if len(a)<3:st.error('분석할 데이터가 부족합니다.');st.stop()
strategies={'A · RSI 단독':'signal_a','B · RSI + 공포':'signal_b','C · RSI + 공포 + 추세':'signal_c'}
results={name:backtest(a,capital,allocation,fee,slip,col,stop_pct,take_pct,hold_hours) for name,col in strategies.items()}
latest=d.iloc[-1]
st.info(f'마지막 확정 4시간봉 시작(KST): {d.index[-1].tz_convert(KST):%Y-%m-%d %H:%M} · 최근 종가 {latest.close:,.0f}원 · RSI {latest.rsi:.1f} · 전일 공포탐욕 {latest.fear if pd.notna(latest.fear) else "자료 없음"}')
st.warning('과거 시뮬레이션이며 실시간 매매 권유가 아닙니다. 거래소 캔들 누락, 지수 발표 시각, 체결 가격과 수수료는 별도 검증이 필요합니다.')

st.subheader('📊 전략 A/B/C 성과 비교')
summary=[]
for name,res in results.items():
    t=res['trades']
    summary.append({'전략':name,'완료 거래':len(t),'승률 (%)':round(res['wins'],2) if len(t) else None,'수익률 (%)':round(res['return'],2),'MDD (%)':round(res['mdd'],2),'시장 노출 (%)':round(res['exposure'],1),'최종 평가액 (원)':round(res['final'])})
st.dataframe(pd.DataFrame(summary),hide_index=True,use_container_width=True)
st.line_chart(pd.DataFrame({name:r['equity'] for name,r in results.items()}))
st.caption('시장 노출은 보유 중인 4시간봉의 비율입니다. 자본 투입 비중과는 다릅니다. 거래가 적은 전략의 수익률은 현금 대기 효과가 큽니다.')

st.subheader('🔬 거래 손실 원인 분석')
selected=st.selectbox('자세히 분석할 전략',list(strategies),index=2)
res=results[selected];t=res['trades'].copy()
if res['open_position']:
    st.info(f'미청산 포지션이 있습니다. 현재 평가액 약 {res["open_value"]:,.0f}원은 최종 자산에 포함되지만 완료 거래 통계에는 포함되지 않습니다.')
if t.empty:
    st.info('선택한 전략에는 완료 거래가 없습니다. 매수 조건 충족 여부를 아래에서 확인하세요.')
else:
    win=t[t['순손익(원)']>0];loss=t[t['순손익(원)']<=0]
    c1,c2,c3,c4=st.columns(4)
    c1.metric('완료 거래',f'{len(t)}회')
    c2.metric('승률',f'{res["wins"]:.1f}%')
    c3.metric('평균 수익 거래',f'{win["순손익(원)"].mean():+,.0f}원' if len(win) else '없음')
    c4.metric('평균 손실 거래',f'{loss["순손익(원)"].mean():+,.0f}원' if len(loss) else '없음')
    reason=t.groupby('청산사유',as_index=False).agg(거래횟수=('순손익(원)','size'),합산손익_원=('순손익(원)','sum'),평균손익_원=('순손익(원)','mean'))
    st.markdown('**청산 사유별 손익**')
    st.dataframe(reason.round(2),hide_index=True,use_container_width=True)
    st.bar_chart(reason.set_index('청산사유')['합산손익_원'])
    ordered=t.sort_values('청산시각').copy();ordered['누적실현손익(원)']=ordered['순손익(원)'].cumsum()
    st.markdown('**거래 종료 순서에 따른 누적 실현손익**')
    st.line_chart(ordered.set_index('청산시각')['누적실현손익(원)'])
    st.markdown('**거래별 손익**')
    st.bar_chart(ordered.set_index('청산시각')['순손익(원)'])
    display=t.sort_values('청산시각',ascending=False).copy()
    for col in ('진입시각','청산시각'):
        display[col]=display[col].dt.tz_convert(KST).dt.strftime('%Y-%m-%d %H:%M KST')
    st.dataframe(display.round(2),hide_index=True,use_container_width=True)
    st.download_button('선택 전략 거래 기록 CSV 다운로드',display.to_csv(index=False).encode('utf-8-sig'),file_name=f'{market}_{selected[0]}_trades_v1_2.csv',mime='text/csv')

st.subheader('🧭 진입 조건이 거래를 얼마나 줄였을까?')
count_a=int(a.signal_a.sum());count_b=int(a.signal_b.sum());count_c=int(a.signal_c.sum())
st.dataframe(pd.DataFrame({'단계':['RSI 상향 돌파','+ 전일 공포 조건','+ SMA200 추세 조건'], '조건 충족 봉 수':[count_a,count_b,count_c]}),hide_index=True,use_container_width=True)
st.caption('조건 충족 봉 수는 실제 진입 거래 수와 다릅니다. 이미 보유 중이거나 대기 주문이 있을 때는 추가 진입하지 않습니다.')
checks={'RSI 상향 돌파':bool(latest.cross_up),'전일 공포 조건':bool(pd.notna(latest.fear) and latest.fear<=fear_limit),'SMA200 위':bool(latest.trend_ok)}
st.write('최근 확정봉: '+' / '.join(('✅ ' if passed else '❌ ')+label for label,passed in checks.items()))

st.subheader('📉 가격 · RSI · 전략 C 매수 후보')
view=a.tail(300)
from plotly.subplots import make_subplots
fig=make_subplots(rows=2,cols=1,shared_xaxes=True,row_heights=[.7,.3],vertical_spacing=.05)
fig.add_trace(go.Candlestick(x=view.index,open=view.open,high=view.high,low=view.low,close=view.close,name='4시간봉'),row=1,col=1)
fig.add_trace(go.Scatter(x=view.index,y=view.sma200,name='SMA200',line=dict(color='#e6a23c')),row=1,col=1)
buys=view[view.signal_c]
fig.add_trace(go.Scatter(x=buys.index,y=buys.low,mode='markers',marker=dict(symbol='triangle-up',size=11,color='#17a67a'),name='전략 C 후보'),row=1,col=1)
fig.add_trace(go.Scatter(x=view.index,y=view.rsi,name='RSI(14)'),row=2,col=1)
fig.add_hline(y=rsi_entry,line_dash='dash',line_color='gray',row=2,col=1)
fig.add_hline(y=70,line_dash='dash',line_color='gray',row=2,col=1)
fig.update_layout(height=580,xaxis_rangeslider_visible=False,template='plotly_dark',margin=dict(l=5,r=5,t=15,b=5))
st.plotly_chart(fig,use_container_width=True)
st.subheader('공포탐욕지수 (전일 값)')
st.line_chart(a[['fear']].dropna().iloc[::6])

with st.expander('⚠️ 백테스트 가정과 한계'):
    st.markdown('''- RSI 상향 돌파와 전일 UTC 공포탐욕지수, 전일 확정 일봉 SMA200을 이용합니다. 4시간봉 종료 후 다음 봉 시가 진입을 가정합니다.
- 손절·익절은 봉 내부 고가/저가로 판정하며 동일 봉 동시 충족 시 손절을 우선합니다. 손절 갭 발생 시 더 불리한 시가를 적용합니다. RSI·시간 청산은 다음 봉 시가에 실행합니다.
- 거래 비용과 슬리피지는 입력한 고정값을 양방향에 적용합니다. 실제 호가·유동성·체결을 재현하지 않습니다.
- 전일 공포지수의 실제 게시 시각, API 결측 및 일봉 경계는 엄밀하게 검증되지 않았습니다.
- 거래별 누적 **실현**손익과 전체 자산 평가곡선은 다릅니다. 미청산 포지션은 종가 평가하며 거래별 통계에서는 제외합니다.
- 조건을 여러 번 조정해 같은 과거 구간에서 좋은 결과만 고르면 과최적화 위험이 큽니다. 학습·검증·미사용 테스트 기간을 분리하세요.
- 이 프로그램은 연구용이며 자동 주문, 실시간 체결 신호, 수익 보장을 제공하지 않습니다.''')
