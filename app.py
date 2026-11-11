import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title='Fear-RSI v1.4 | 통합형 퀀트', page_icon='📈', layout='wide')
st.title('📈 Fear-RSI 퀀트 대시보드 v1.4')
st.caption('업비트 BTC·ETH | D1 단기·D2 스윙·D3 추세 | MFE·MAE 및 청산 방식 비교 | 연구용, 자동 주문 없음')
CONFIG = {
    'D1 · 단기 반등': {'signal':'D1','stop':2.5,'take':4.5,'hours':24,'trail':2.0},
    'D2 · 스윙': {'signal':'D2','stop':6.0,'take':12.0,'hours':336,'trail':5.0},
    'D3 · 추세 전환': {'signal':'D3','stop':10.0,'take':25.0,'hours':1344,'trail':8.0},
}
KST='Asia/Seoul'

@st.cache_data(ttl=900,show_spinner=False)
def get_candles(market,days):
    session=requests.Session();session.headers['User-Agent']='FearRSIResearch/1.4'
    target=int((days+235)*6);rows=[];cursor=None
    while len(rows)<target:
        params={'market':market,'count':min(200,target-len(rows))}
        if cursor:params['to']=cursor
        for attempt in range(5):
            try:
                r=session.get('https://api.upbit.com/v1/candles/minutes/240',params=params,timeout=25)
                if r.status_code==429:time.sleep(1.5*(attempt+1));continue
                r.raise_for_status();batch=r.json();break
            except (requests.RequestException,ValueError):
                if attempt==4:raise
                time.sleep(1+attempt)
        if not batch:break
        rows.extend(batch)
        cursor=pd.Timestamp(batch[-1]['candle_date_time_utc'],tz='UTC').strftime('%Y-%m-%dT%H:%M:%SZ')
        time.sleep(.13)
    if not rows:raise ValueError('업비트 캔들 수집 실패')
    d=pd.DataFrame(rows)
    d['ts']=pd.to_datetime(d['candle_date_time_utc'],utc=True)
    d=d.sort_values('ts').drop_duplicates('ts').set_index('ts')
    d=d.rename(columns={'opening_price':'open','high_price':'high','low_price':'low','trade_price':'close','candle_acc_trade_volume':'volume'})
    d=d[['open','high','low','close','volume']].astype(float)
    d=d[d.index+pd.Timedelta(hours=4)<=pd.Timestamp.now(tz='UTC')]
    return d

@st.cache_data(ttl=3600,show_spinner=False)
def get_fear():
    r=requests.get('https://api.alternative.me/fng/',params={'limit':0,'format':'json'},timeout=30)
    r.raise_for_status();f=pd.DataFrame(r.json()['data'])
    f['date']=pd.to_datetime(f.timestamp.astype(int),unit='s',utc=True).dt.floor('D')
    f['fear']=pd.to_numeric(f.value,errors='coerce')
    return f.dropna(subset=['fear']).drop_duplicates('date').set_index('date')['fear']

def rsi(series,n=14):
    delta=series.diff();up=delta.clip(lower=0);down=-delta.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ad=down.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    value=100-100/(1+au/ad.replace(0,np.nan))
    return value.mask((ad==0)&(au>0),100).mask((ad==0)&(au==0),50)

def indicators(raw,fear):
    d=raw.copy();d['rsi']=rsi(d.close)
    d['fear']=(d.index.floor('D')-pd.Timedelta(days=1)).map(fear)
    daily=d.close.resample('1D').last().dropna()
    d['sma200']=d.index.floor('D').map(daily.rolling(200,min_periods=200).mean().shift(1))
    d['sma50']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(1))
    d['sma50_prev']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(8))
    d['ema20']=d.close.ewm(span=20,adjust=False).mean()
    d['ema50']=d.close.ewm(span=50,adjust=False).mean()
    d['vol_ratio']=d.volume/d.volume.rolling(30,min_periods=30).mean().replace(0,np.nan)
    d['high_prev']=d.high.rolling(30,min_periods=30).max().shift(1)
    d['ret7d']=d.close/d.close.shift(42)-1
    slope=d.sma50/d.sma50_prev-1
    conditions=[(d.close>d.sma200)&(slope>.01),(d.close>d.sma200)&(slope<=.01),
                (d.close<=d.sma200)&(slope<-.025)&(d.ret7d<-.04),
                (d.close<=d.sma200)&(slope<-.005)]
    d['regime']=np.select(conditions,['강한 상승','상승 후 조정','강한 하락','하락 속 소강'],default='횡보/전환')
    d['D1']=(d.rsi.shift(1)<32)&(d.rsi>=32)&(d.close>d.open)&(d.fear<=55)
    d['D2']=(d.rsi.shift(1)<42)&(d.rsi>=42)&(d.close>d.ema20)&(d.vol_ratio>=.8)&(d.fear<=65)
    d['D3']=(d.close>d.high_prev)&(d.ema20>d.ema50)&(d.rsi.between(48,75))&(d.vol_ratio>=1.05)
    d['A']=(d.rsi>=30)&(d.rsi.shift(1)<30)
    d['B']=d.A&(d.fear<=35)
    d['C']=d.B&(d.close>d.sma200)
    return d

TRADE_COLS=['entry','exit','pnl','return_pct','reason','regime','hours','mfe_pct','mae_pct','capture_pct']

def simulate(d,signal,capital,allocation,fee_pct,slip_pct,stop_pct,take_pct,hold_hours,mode='고정 익절',trail_pct=3.0):
    # Close-of-bar signals; next-bar-open entries. Exit checks use the next bar's OHLC.
    # On each bar, stops are checked BEFORE updating the high-water mark, avoiding
    # unknowable intrabar high/low ordering and optimistic same-bar trailing fills.
    cash=float(capital);qty=0.;entry=0.;cost=0.;entry_ts=None;entry_regime=''
    pending=False;pending_exit=None;peak=0.;max_high=0.;min_low=np.inf
    trades=[];curve=[];bars=0;fee=fee_pct/100;slip=slip_pct/100

    def close_trade(ts,px,reason):
        nonlocal cash,qty,pending_exit
        proceeds=qty*px*(1-fee)
        mfe=(max_high/entry-1)*100
        mae=(min_low/entry-1)*100
        net=(proceeds/cost-1)*100
        capture=(net/mfe*100) if mfe>0 else np.nan
        trades.append({'entry':entry_ts,'exit':ts,'pnl':proceeds-cost,'return_pct':net,
                       'reason':reason,'regime':entry_regime,'hours':(ts-entry_ts).total_seconds()/3600,
                       'mfe_pct':mfe,'mae_pct':mae,'capture_pct':capture})
        cash+=proceeds;qty=0.;pending_exit=None

    for i in range(1,len(d)):
        b=d.iloc[i];ts=d.index[i]
        if qty>0 and pending_exit:
            # Exit at the next opening price. The opening price is part of the
            # holding period, and therefore included in excursion diagnostics.
            max_high=max(max_high,float(b.open));min_low=min(min_low,float(b.open))
            close_trade(ts,float(b.open)*(1-slip),pending_exit)
        if qty==0 and pending:
            spend=cash*allocation/100
            if spend>=5000 and pd.notna(b.open):
                entry=float(b.open)*(1+slip);cost=spend;qty=spend/(entry*(1+fee))
                cash-=spend;entry_ts=ts;entry_regime=str(d.iloc[i-1].regime)
                peak=entry;max_high=entry;min_low=entry
            pending=False
        if qty>0:
            bars+=1
            stop_px=entry*(1-stop_pct/100)
            # Trailing stop is based ONLY on highs observed before this bar.
            if mode in ('트레일링','부분 익절 + 트레일링'):
                stop_px=max(stop_px,peak*(1-trail_pct/100))
            take_px=entry*(1+take_pct/100)
            low=float(b.low);high=float(b.high);op=float(b.open)
            # MFE/MAE are observed extremes up to exit; when an intrabar stop
            # fires, the remainder of that bar is NOT included in diagnostics.
            reason=None;px=None
            if low<=stop_px:
                px=min(stop_px,op)*(1-slip);reason='손절/트레일링'
                min_low=min(min_low,px);max_high=max(max_high,op)
            elif mode=='고정 익절' and high>=take_px:
                px=max(take_px,op)*(1-slip);reason='익절'
                max_high=max(max_high,px);min_low=min(min_low,op)
            if reason:
                close_trade(ts,px,reason)
            else:
                max_high=max(max_high,high);min_low=min(min_low,low)
                peak=max(peak,high)
                if mode=='모멘텀 청산' and pd.notna(b.rsi) and pd.notna(d.iloc[i-1].rsi):
                    if b.close<b.ema20 and b.rsi<d.iloc[i-1].rsi:
                        pending_exit='모멘텀 약화'
                if not pending_exit and ts-entry_ts>=pd.Timedelta(hours=hold_hours):
                    pending_exit='보유기간 종료'
        if qty==0 and not pending and bool(b[signal]):pending=True
        curve.append((ts,cash+qty*float(b.close)))
    t=pd.DataFrame(trades,columns=TRADE_COLS)
    equity=pd.Series(dict(curve),dtype=float)
    final=float(equity.iloc[-1]) if len(equity) else float(capital)
    mdd=float((equity/equity.cummax()-1).min()*100) if len(equity) else 0.
    return {'trades':t,'equity':equity,'return_pct':(final/capital-1)*100,'mdd':mdd,
            'exposure':100*bars/max(1,len(d)-1),'open':qty>0}

with st.sidebar:
    st.header('백테스트 설정')
    market=st.selectbox('종목',['KRW-BTC','KRW-ETH'])
    days=st.select_slider('분석 기간 (일)',options=[365,540,720],value=720)
    capital=st.number_input('초기 가상 투자금 (원)',min_value=10000,max_value=100000000,value=500000,step=100000)
    allocation=st.slider('진입 시 현금 사용 비율 (%)',10,100,25,5)
    fee=st.number_input('편도 수수료 (%)',0.,1.,0.05,0.01)
    slip=st.number_input('편도 슬리피지 (%)',0.,2.,0.10,0.05)
    trail_mult=st.slider('트레일링 폭 배율 (실험)',0.5,2.0,1.0,0.1)
    if st.button('API 데이터 새로고침'):
        get_candles.clear();get_fear.clear();st.rerun()

try:
    with st.spinner('업비트 4시간봉과 공포탐욕지수 수집 중…'):
        d=indicators(get_candles(market,days),get_fear())
except Exception as exc:
    st.error(f'데이터 수집 오류: {exc}');st.stop()
a=d[d.index>=d.index.max()-pd.Timedelta(days=days)].copy()
if len(a)<200:st.error('데이터 부족');st.stop()
split=int(len(a)*.7);train=a.iloc[:split];test=a.iloc[split:]
latest=a.iloc[-1]
st.info(f'마지막 확정 4시간봉 시작: {a.index[-1].tz_convert(KST):%Y-%m-%d %H:%M} KST | {market} {latest.close:,.0f}원 | RSI {latest.rsi:.1f} | 전일 공포 {latest.fear if pd.notna(latest.fear) else "N/A"} | 국면 **{latest.regime}**')
st.warning('v1.4는 청산 방식을 과거 검증 구간에서 비교하는 탐색 도구입니다. 검증 구간에서 가장 좋은 방식을 고르는 것은 독립적인 성과 검증이 아닙니다.')

st.subheader('🎯 D1/D2/D3 기준 전략 · 검증 구간')
st.caption(f'개발 {train.index[0].date()}~{train.index[-1].date()} / 검증 {test.index[0].date()}~{test.index[-1].date()} · 시간순 70:30')
rows=[]
for name,cfg in CONFIG.items():
    r=simulate(test,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'])
    t=r['trades']
    rows.append({'전략':name,'완료 거래':len(t),'거래당 평균수익 (%)':round(t.return_pct.mean(),2) if len(t) else None,
                 '자산 수익 (%)':round(r['return_pct'],2),'MDD (%)':round(r['mdd'],2),
                 '시장 노출 (%)':round(r['exposure'],1)})
st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)

st.subheader('🔍 MFE·MAE — 진입 이후 얼마나 움직였나?')
selected=st.selectbox('상세 분석 전략',list(CONFIG),index=1)
cfg=CONFIG[selected]
base=simulate(test,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'])
t=base['trades']
if t.empty:
    st.info('검증 기간에 완료 거래가 없습니다.')
else:
    st.caption('MFE: 진입 후 청산 전 관측된 최대 유리 가격변동(%). MAE: 최대 불리 가격변동(%). 거래비용을 제외한 가격 기준이며, 봉 내부 체결 순서는 알 수 없습니다.')
    c1,c2,c3,c4=st.columns(4)
    c1.metric('완료 거래',f'{len(t)}회')
    c2.metric('평균 MFE',f'{t.mfe_pct.mean():+.2f}%')
    c3.metric('평균 MAE',f'{t.mae_pct.mean():+.2f}%')
    c4.metric('평균 실현수익',f'{t.return_pct.mean():+.2f}%')
    chart=go.Figure()
    chart.add_trace(go.Bar(x=list(range(1,len(t)+1)),y=t.mfe_pct,name='MFE(%)'))
    chart.add_trace(go.Bar(x=list(range(1,len(t)+1)),y=t.mae_pct,name='MAE(%)'))
    chart.add_trace(go.Scatter(x=list(range(1,len(t)+1)),y=t.return_pct,mode='lines+markers',name='실현수익(%)'))
    chart.update_layout(height=370,barmode='relative',xaxis_title='거래 순번',yaxis_title='%',margin=dict(l=10,r=10,t=20,b=20))
    st.plotly_chart(chart,use_container_width=True)
    shown=t.copy()
    for col in ['entry','exit']:
        shown[col]=shown[col].dt.tz_convert(KST).dt.strftime('%Y-%m-%d %H:%M')
    shown=shown.rename(columns={'entry':'진입(KST)','exit':'청산(KST)','pnl':'순손익(원)','return_pct':'실현수익(%)',
                                'reason':'청산사유','regime':'진입국면','hours':'보유시간(h)',
                                'mfe_pct':'MFE(%)','mae_pct':'MAE(%)','capture_pct':'수익포착률(%)'})
    st.dataframe(shown.round(2),hide_index=True,use_container_width=True)
    st.download_button('MFE·MAE 거래내역 CSV 다운로드',shown.to_csv(index=False).encode('utf-8-sig'),file_name=f'{market}_{cfg["signal"]}_v1_4_mfe_mae.csv',mime='text/csv')

st.subheader('⚖️ 청산 방식 비교 — 같은 진입 신호')
st.caption('모든 방식은 같은 D전략 진입 신호를 사용하지만, 청산 시점이 달라지면 다음 진입 가능 시점도 달라져 실제 거래 집합은 달라질 수 있습니다.')
modes=['고정 익절','트레일링','모멘텀 청산']
comparison=[];curves={}
for mode in modes:
    r=simulate(test,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'],mode,cfg['trail']*trail_mult)
    tr=r['trades'];curves[mode]=r['equity']
    comparison.append({'청산 방식':mode,'완료 거래':len(tr),
                       '거래당 평균수익 (%)':round(tr.return_pct.mean(),2) if len(tr) else None,
                       '승률 (%)':round(100*(tr.pnl>0).mean(),1) if len(tr) else None,
                       '검증 자산수익 (%)':round(r['return_pct'],2),'검증 MDD (%)':round(r['mdd'],2),
                       '시장 노출 (%)':round(r['exposure'],1)})
st.dataframe(pd.DataFrame(comparison),hide_index=True,use_container_width=True)
st.line_chart(pd.DataFrame(curves))
st.info('부분 익절 + 잔여 트레일링은 포지션을 분할하고 수수료·평가액을 별도로 관리해야 하므로 이 버전에는 넣지 않았습니다. 잘못된 결과를 보여주지 않기 위한 의도적인 제외입니다.')

st.subheader('📌 현재 D1/D2/D3 신호')
st.dataframe(pd.DataFrame([{'전략':name,'현재 신호':'후보 발생' if bool(latest[cfg['signal']]) else '조건 미충족',
                            '기준 손절 (%)':cfg['stop'],'기준 익절 (%)':cfg['take'],'보유 상한 (h)':cfg['hours']}
                           for name,cfg in CONFIG.items()]),hide_index=True,use_container_width=True)

st.subheader('📉 가격 및 매수 후보')
view=a.tail(350);fig=go.Figure()
fig.add_trace(go.Scatter(x=view.index,y=view.close,name='종가'))
fig.add_trace(go.Scatter(x=view.index,y=view.sma200,name='SMA200'))
for name,cfg in CONFIG.items():
    p=view[view[cfg['signal']]]
    fig.add_trace(go.Scatter(x=p.index,y=p.close,mode='markers',name=name,marker={'size':8}))
fig.update_layout(height=410,margin=dict(l=5,r=5,t=15,b=20))
st.plotly_chart(fig,use_container_width=True)
st.line_chart(view[['rsi']])

with st.expander('기존 A/B/C 기준 전략'):
    rows=[]
    for name,sig in [('A · RSI','A'),('B · RSI+공포','B'),('C · RSI+공포+추세','C')]:
        r=simulate(a,sig,capital,allocation,fee,slip,2.5,5,120)
        rows.append({'전략':name,'완료 거래':len(r['trades']),'전체 수익률 (%)':round(r['return_pct'],2),'MDD (%)':round(r['mdd'],2)})
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
with st.expander('백테스트 한계 및 주의'):
    st.markdown('''- **신호**: 확정 4시간봉 종료 시 판정하고 다음 봉 시가 진입. 전일 UTC 공포탐욕지수 사용. 실제 지수 발표 시각의 가용성은 별도 검증 필요.
- **MFE/MAE**: 진입 시가 이후 청산까지의 OHLC 극단값을 사용합니다. 봉 내부 순서가 불명확하며, 봉 내부 손절/익절 이후의 고가·저가는 사용하지 않는 보수적인 근사입니다. 실제 호가와 체결 경로를 재현하지 않습니다.
- **트레일링**: 현재 봉의 고가를 보고 현재 봉의 저가에 과거로 소급하여 손절하지 않습니다. 이전 봉까지 확인된 최고가만 다음 봉의 트레일링 기준에 반영합니다. 급격한 갭은 불리한 시가로 체결합니다.
- **모멘텀 청산**: 확정봉의 종가가 EMA20 아래이고 RSI가 전 봉보다 낮으면 다음 봉 시가 청산. 손절은 항상 우선합니다.
- **비교**: 검증 구간의 성과를 보고 방식이나 배율을 선택하면 검증 데이터가 사실상 개발 데이터로 바뀝니다. 별도 미사용 구간 또는 워크포워드 재검증이 필요합니다.
- **수수료·슬리피지**: 편도 고정 비율 가정. API 누락, 캔들 시간 경계, 실제 주문 체결은 별도 검증되지 않았습니다.
- **자동 매매 없음**. 수익 보장이나 투자 권유가 아닙니다.''')
