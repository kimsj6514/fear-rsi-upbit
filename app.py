import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title='Fear-RSI v1.3 | 통합형 퀀트',page_icon='📈',layout='wide')
KST='Asia/Seoul'
st.title('📈 Fear-RSI 퀀트 대시보드 v1.3')
st.caption('BTC·ETH | 단기 반등·스윙·추세 전환 | 시장 국면별 기대수익 분석 | 연구용 · 자동 주문 없음')

@st.cache_data(ttl=900,show_spinner=False)
def get_candles(market,days):
    s=requests.Session();s.headers['User-Agent']='FearRSI/1.3 research'
    target=int((days+235)*6);rows=[];cursor=None
    while len(rows)<target:
        p={'market':market,'count':min(200,target-len(rows))}
        if cursor:p['to']=cursor
        for attempt in range(5):
            try:
                r=s.get('https://api.upbit.com/v1/candles/minutes/240',params=p,timeout=25)
                if r.status_code==429:time.sleep(1.5*(attempt+1));continue
                r.raise_for_status();batch=r.json();break
            except (requests.RequestException,ValueError):
                if attempt==4:raise
                time.sleep(1+attempt)
        if not batch:break
        rows.extend(batch)
        cursor=pd.Timestamp(batch[-1]['candle_date_time_utc'],tz='UTC').strftime('%Y-%m-%dT%H:%M:%SZ')
        time.sleep(.13)
    if not rows:raise ValueError('업비트 캔들을 가져오지 못했습니다.')
    d=pd.DataFrame(rows)
    d['ts']=pd.to_datetime(d['candle_date_time_utc'],utc=True)
    d=d.sort_values('ts').drop_duplicates('ts').set_index('ts')
    d=d.rename(columns={'opening_price':'open','high_price':'high','low_price':'low','trade_price':'close','candle_acc_trade_volume':'volume'})
    d=d[['open','high','low','close','volume']].astype(float)
    # Drop incomplete current 4h candle.
    d=d[d.index+pd.Timedelta(hours=4)<=pd.Timestamp.now(tz='UTC')]
    return d

@st.cache_data(ttl=3600,show_spinner=False)
def get_fear():
    r=requests.get('https://api.alternative.me/fng/',params={'limit':0,'format':'json'},timeout=30)
    r.raise_for_status();f=pd.DataFrame(r.json()['data'])
    f['date']=pd.to_datetime(f.timestamp.astype(int),unit='s',utc=True).dt.floor('D')
    f['fear']=pd.to_numeric(f.value,errors='coerce')
    return f.dropna(subset=['fear']).drop_duplicates('date').set_index('date')['fear']

def rsi(s,n=14):
    delta=s.diff();up=delta.clip(lower=0);dn=-delta.clip(upper=0)
    u=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    d=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    x=100-100/(1+u/d.replace(0,np.nan))
    return x.mask((d==0)&(u>0),100).mask((d==0)&(u==0),50)

def indicators(raw,fear):
    d=raw.copy();d['rsi']=rsi(d.close)
    prev_day=d.index.floor('D')-pd.Timedelta(days=1)
    d['fear']=prev_day.map(fear)
    daily=d.close.resample('1D').last().dropna()
    # Use only prior completed UTC days; 200D filter is descriptive, not a hard buy veto.
    d['sma200']=d.index.floor('D').map(daily.rolling(200,min_periods=200).mean().shift(1))
    d['sma50']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(1))
    d['sma50_prev']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(8))
    d['ema20']=d.close.ewm(span=20,adjust=False).mean()
    d['ema50']=d.close.ewm(span=50,adjust=False).mean()
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    d['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    d['vol_ratio']=d.volume/d.volume.rolling(30,min_periods=30).mean().replace(0,np.nan)
    d['rsi_prev']=d.rsi.shift(1)
    d['low_prev']=d.low.rolling(18,min_periods=18).min().shift(1)
    d['high_prev']=d.high.rolling(30,min_periods=30).max().shift(1)
    d['ret7d']=d.close/d.close.shift(42)-1
    d['slope50']=d.sma50/d.sma50_prev-1
    # Regime classification based on known, completed data only.
    conditions=[(d.close>d.sma200)&(d.slope50>.01),(d.close>d.sma200)&(d.slope50<=.01),
                (d.close<=d.sma200)&(d.slope50<-.025)&(d.ret7d<-.04),
                (d.close<=d.sma200)&(d.slope50<-.005)]
    d['regime']=np.select(conditions,['강한 상승','상승 후 조정','강한 하락','하락 속 소강'],default='횡보/전환')
    # D1: 4H oversold recovery + price improvement; D2: swing recovery with volume;
    # D3: momentum breakout with medium-term confirmation, no 200D hard veto.
    d['D1']=(d.rsi.shift(1)<32)&(d.rsi>=32)&(d.close>d.open)&(d.fear<=55)
    d['D2']=(d.rsi.shift(1)<42)&(d.rsi>=42)&(d.close>d.ema20)&(d.vol_ratio>=.8)&(d.fear<=65)
    d['D3']=(d.close>d.high_prev)&(d.ema20>d.ema50)&(d.rsi.between(48,75))&(d.vol_ratio>=1.05)
    d['A']=(d.rsi>=30)&(d.rsi.shift(1)<30)
    d['B']=d.A&(d.fear<=35)
    d['C']=d.B&(d.close>d.sma200)
    return d

CONFIG={
    'D1 · 단기 반등':dict(signal='D1',stop=2.5,take=4.5,hours=24),
    'D2 · 스윙':dict(signal='D2',stop=6.,take=12.,hours=336),
    'D3 · 추세 전환':dict(signal='D3',stop=10.,take=25.,hours=1344),
}

def simulate(d,signal,capital,allocation,fee,slip,stop,take,hours):
    cash=float(capital);qty=0.;entry=0.;cost=0.;entry_ts=None;entry_regime='';pending=False;exit_next=None
    trades=[];curve=[];bars=0;fee/=100;slip/=100
    for i in range(1,len(d)):
        b=d.iloc[i];ts=d.index[i]
        if qty>0 and exit_next:
            px=float(b.open)*(1-slip);proceeds=qty*px*(1-fee)
            trades.append(dict(entry=entry_ts,exit=ts,pnl=proceeds-cost,return_pct=100*(proceeds/cost-1),reason=exit_next,regime=entry_regime,hours=(ts-entry_ts).total_seconds()/3600))
            cash+=proceeds;qty=0.;exit_next=None
        if qty==0 and pending:
            spend=cash*allocation/100
            if spend>=5000:
                entry=float(b.open)*(1+slip);cost=spend;qty=spend/(entry*(1+fee));cash-=spend;entry_ts=ts
                entry_regime=str(d.iloc[i-1].regime)
            pending=False
        if qty>0:
            bars+=1;stop_px=entry*(1-stop/100);take_px=entry*(1+take/100)
            reason=None;px=None
            if b.low<=stop_px:
                px=min(stop_px,float(b.open))*(1-slip);reason='손절'
            elif b.high>=take_px:
                px=max(take_px,float(b.open))*(1-slip);reason='익절'
            if reason:
                proceeds=qty*px*(1-fee)
                trades.append(dict(entry=entry_ts,exit=ts,pnl=proceeds-cost,return_pct=100*(proceeds/cost-1),reason=reason,regime=entry_regime,hours=(ts-entry_ts).total_seconds()/3600))
                cash+=proceeds;qty=0.;exit_next=None
            elif ts-entry_ts>=pd.Timedelta(hours=hours):exit_next='보유기간 종료'
        if qty==0 and not pending and bool(b[signal]):pending=True
        curve.append((ts,cash+qty*float(b.close)))
    t=pd.DataFrame(trades);eq=pd.Series(dict(curve),dtype=float)
    final=float(eq.iloc[-1]) if len(eq) else float(capital)
    mdd=float((eq/eq.cummax()-1).min()*100) if len(eq) else 0.
    return dict(trades=t,equity=eq,final=final,return_pct=(final/capital-1)*100,mdd=mdd,exposure=100*bars/max(1,len(d)-1),open=qty>0)

def trade_metrics(t):
    if t.empty:return dict(n=0,win=np.nan,expect=np.nan,median=np.nan,profit_factor=np.nan)
    profits=t.loc[t.pnl>0,'pnl'].sum();loss=-t.loc[t.pnl<0,'pnl'].sum()
    return dict(n=len(t),win=100*(t.pnl>0).mean(),expect=t.return_pct.mean(),median=t.return_pct.median(),profit_factor=profits/loss if loss>0 else np.nan)

with st.sidebar:
    st.header('분석 설정')
    market=st.selectbox('종목',['KRW-BTC','KRW-ETH'])
    days=st.select_slider('분석 기간 (일)',options=[365,540,720],value=720)
    capital=st.number_input('가상 투자금 (원)',min_value=10000,max_value=100000000,value=500000,step=100000)
    allocation=st.slider('진입 시 현금 사용 비율 (%)',10,100,25,5)
    fee=st.number_input('편도 수수료 (%)',0.,1.,0.05,0.01)
    slip=st.number_input('편도 슬리피지 (%)',0.,2.,0.10,0.05)
    st.caption('D1/D2/D3의 손절·익절·보유기간은 연구용 초기 가설이며 최적화된 값이 아닙니다.')
    if st.button('API 데이터 새로고침'):
        get_candles.clear();get_fear.clear();st.rerun()
try:
    with st.spinner('시장 데이터 수집 및 분석 중… 최초 실행은 1분 이상 걸릴 수 있습니다.'):
        raw=get_candles(market,days);fear=get_fear();d=indicators(raw,fear)
except Exception as e:
    st.error(f'데이터 수집 오류: {e}');st.stop()
cutoff=d.index.max()-pd.Timedelta(days=days)
a=d[d.index>=cutoff].copy()
if len(a)<200:st.error('유효한 4시간봉 데이터가 부족합니다.');st.stop()
latest=a.iloc[-1]
st.info(f'최근 확정봉: {a.index[-1].tz_convert(KST):%Y-%m-%d %H:%M} KST (시작 시각) | {market} {latest.close:,.0f}원 | RSI {latest.rsi:.1f} | 전일 공포 {latest.fear if pd.notna(latest.fear) else "N/A"} | 시장 국면: **{latest.regime}**')
st.warning('시장 국면과 점수는 휴리스틱 분류입니다. 실시간 체결 신호 또는 수익 보장이 아닙니다.')

st.subheader('🎯 통합형 D1/D2/D3 성과 비교')
# Chronological holdout: no parameter optimization on test segment.
split=int(len(a)*.7);train=a.iloc[:split].copy();test=a.iloc[split:].copy()
st.caption(f'개발 구간 {train.index[0].date()}~{train.index[-1].date()} / 검증 구간 {test.index[0].date()}~{test.index[-1].date()} (시간순 70:30). 초기 전략 규칙 고정, 검증 구간에서 재조정하지 않음.')
rows=[];results={}
for name,cfg in CONFIG.items():
    tr=simulate(train,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'])
    te=simulate(test,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'])
    full=simulate(a,cfg['signal'],capital,allocation,fee,slip,cfg['stop'],cfg['take'],cfg['hours'])
    results[name]=(tr,te,full)
    m=trade_metrics(te['trades'])
    rows.append({'전략':name,'검증 거래':m['n'],'검증 승률 (%)':round(m['win'],1) if m['n'] else None,
                 '검증 평균 거래수익 (%)':round(m['expect'],2) if m['n'] else None,
                 '검증 자산수익 (%)':round(te['return_pct'],2),'검증 MDD (%)':round(te['mdd'],2),
                 '검증 노출 (%)':round(te['exposure'],1),'전체 거래':len(full['trades'])})
summary=pd.DataFrame(rows)
st.dataframe(summary,hide_index=True,use_container_width=True)
st.line_chart(pd.DataFrame({name:res[1]['equity'] for name,res in results.items()}))
st.caption('검증 구간 자산곡선은 전략별 동일 초기자금으로 시작합니다. 미청산 포지션은 마지막 종가로 평가하지만 완료 거래 통계에서는 제외합니다.')

st.subheader('📌 현재 매수 후보')
current=[]
for name,cfg in CONFIG.items():
    active=bool(latest[cfg['signal']]);hist=results[name][1]['trades'];m=trade_metrics(hist)
    current.append({'전략':name,'현재 신호':'후보 발생' if active else '조건 미충족',
                    '검증 표본':m['n'],'검증 평균 거래수익 (%)':round(m['expect'],2) if m['n'] else None,
                    '검증 승률 (%)':round(m['win'],1) if m['n'] else None,'기본 보유 상한':f"{cfg['hours']}시간"})
st.dataframe(pd.DataFrame(current),hide_index=True,use_container_width=True)
valid=[(x['전략'],x['검증 평균 거래수익 (%)']) for x in current if x['현재 신호']=='후보 발생' and x['검증 표본']>=20 and x['검증 평균 거래수익 (%)'] is not None and x['검증 평균 거래수익 (%)']>0]
if valid:
    best=max(valid,key=lambda z:z[1]);st.success(f'검증 조건을 통과한 후보: {best[0]} (검증 평균 거래수익 {best[1]:+.2f}%). 과거 통계이며 실거래 권유는 아닙니다.')
else:
    st.info('현재 검증 기준을 통과한 매수 후보 없음. 신호 미발생, 검증 거래 20회 미만 또는 기대수익 0% 이하일 수 있습니다.')
st.caption('최소 20회는 임시 표본 기준이며 통계적 유의성을 보장하지 않습니다. 검증 성과를 보고 전략을 선택하는 과정 자체에도 선택 편향이 존재합니다.')

st.subheader('🧭 시장 국면별 성과')
selected=st.selectbox('분석 전략',list(CONFIG))
tr,te,full=results[selected]
if not te['trades'].empty:
    reg=te['trades'].groupby('regime',as_index=False).agg(거래수=('pnl','size'),평균수익률=('return_pct','mean'),합산손익=('pnl','sum'),승률=('pnl',lambda s:100*(s>0).mean()))
    st.dataframe(reg.round(2),hide_index=True,use_container_width=True)
else:st.info('검증 구간에 완료 거래가 없어 국면별 성과를 산출할 수 없습니다.')
st.caption('시장 국면은 매수 신호 발생 시점의 분류이며, 이후의 가격을 사용하지 않습니다.')

st.subheader('🔬 거래 손익 및 청산 사유')
if not te['trades'].empty:
    t=te['trades'].copy();reason=t.groupby('reason',as_index=False).agg(거래수=('pnl','size'),합산손익=('pnl','sum'),평균수익률=('return_pct','mean'))
    st.dataframe(reason.round(2),hide_index=True,use_container_width=True)
    chart=go.Figure(go.Bar(x=reason['reason'],y=reason['합산손익'],text=reason['거래수'].astype(str)+'회'))
    chart.update_layout(height=330,xaxis_title=None,yaxis_title='원',margin=dict(l=10,r=10,t=20,b=25))
    st.plotly_chart(chart,use_container_width=True)
    t['entry']=t.entry.dt.tz_convert(KST).dt.strftime('%Y-%m-%d %H:%M')
    t['exit']=t.exit.dt.tz_convert(KST).dt.strftime('%Y-%m-%d %H:%M')
    t=t.rename(columns={'entry':'진입(KST)','exit':'청산(KST)','pnl':'순손익(원)','return_pct':'거래수익률(%)','reason':'청산사유','regime':'진입국면','hours':'보유시간(h)'})
    st.dataframe(t.round(2),hide_index=True,use_container_width=True)
    st.download_button('검증 거래 내역 CSV 다운로드',t.to_csv(index=False).encode('utf-8-sig'),file_name=f'{market}_{CONFIG[selected]["signal"]}_v1_3.csv',mime='text/csv')
else:st.info('검증 구간 완료 거래 없음')

st.subheader('📉 가격 · RSI · 전략별 후보')
view=a.tail(350)
fig=go.Figure()
fig.add_trace(go.Scatter(x=view.index,y=view.close,name='종가',line=dict(color='#68b3ed')))
fig.add_trace(go.Scatter(x=view.index,y=view.sma200,name='전일 일봉 SMA200',line=dict(color='#e7a34e')))
for name,cfg in CONFIG.items():
    p=view[view[cfg['signal']]]
    fig.add_trace(go.Scatter(x=p.index,y=p.close,mode='markers',name=name,marker=dict(size=8)))
fig.update_layout(height=430,margin=dict(l=5,r=5,t=20,b=20),legend=dict(orientation='h'))
st.plotly_chart(fig,use_container_width=True)
st.line_chart(view[['rsi']])
st.caption('RSI는 4시간봉 기준. 시장 국면 분류는 일봉 50/200일선, 7일 가격 변화 등을 이용한 실험적 규칙입니다.')

with st.expander('기존 A/B/C 기준 전략 비교'):
    base=[]
    for name,col in [('A · RSI','A'),('B · RSI+공포','B'),('C · RSI+공포+추세','C')]:
        r=simulate(a,col,capital,allocation,fee,slip,2.5,5,120)
        base.append({'전략':name,'완료 거래':len(r['trades']),'전체 수익률 (%)':round(r['return_pct'],2),'MDD (%)':round(r['mdd'],2)})
    st.dataframe(pd.DataFrame(base),hide_index=True,use_container_width=True)
with st.expander('방법론과 주의사항'):
    st.markdown('''- D1/D2/D3의 조건과 손절·익절·보유기간은 **검증 전 가설**이며 최적화된 전략이 아닙니다.
- 신호는 **확정된 4시간봉 종가**에서 판정하고 **다음 봉 시가**에 매수합니다. 공포탐욕지수는 전일 UTC 값만 사용하며 실제 발표 시각의 가용성은 별도 검증이 필요합니다.
- 4시간봉 고가/저가가 손절·익절을 모두 건드리면 손절을 먼저 적용합니다. 시가 갭은 불리한 체결을 반영하고 수수료·슬리피지는 양방향 고정 비율로 계산합니다.
- 70:30 시간 분할은 **간단한 홀드아웃**입니다. 검증 구간에서 전략을 골라 재조정하면 과최적화 위험이 다시 생깁니다. 다수 기간 워크포워드, 별도 미사용 데이터, 종목별 독립 검증이 필요합니다.
- 진입 국면별 거래수는 적을 수 있습니다. 매수 신호는 장래 상승 확률의 보증이 아닙니다.
- 추세 전환 전략의 2~8주 보유는 720일 분석만으로 충분한 통계 검증이 어렵습니다. 추후 장기 일봉 데이터 검증이 필요합니다.
- 거래소 API 결측, 봉 간 간격, 거래량 이상치, 실제 주문 체결과 공포지수 게시 시각은 아직 별도 검증되지 않았습니다.
- **실거래 주문 기능 없음. 연구용 프로그램입니다.**''')
