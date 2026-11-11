import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title='Fear-RSI v1.5 | 통합형 퀀트', page_icon='📈', layout='wide')
st.title('📈 Fear-RSI 퀀트 대시보드 v1.5')
st.caption('업비트 BTC·ETH | D1 단기·D2 스윙·D3 추세 | MFE·MAE · 진입 신호 선행 성과 분석 | 연구용, 자동 주문 없음')
CONFIG = {
    'D1 · 단기 반등': {'signal':'D1','stop':2.5,'take':4.5,'hours':24,'trail':2.0},
    'D2 · 스윙': {'signal':'D2','stop':6.0,'take':12.0,'hours':336,'trail':5.0},
    'D3 · 추세 전환': {'signal':'D3','stop':10.0,'take':25.0,'hours':1344,'trail':8.0},
}
KST='Asia/Seoul'

@st.cache_data(ttl=900,show_spinner=False)
def get_candles(market,days):
    session=requests.Session();session.headers['User-Agent']='FearRSIResearch/1.5'
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


# v1.5: Event study using only features available at the signal close.
# Future returns are labels for historical evaluation, NEVER input features for scoring.
HORIZONS = {'1일':6, '3일':18, '7일':42, '14일':84}

def entry_opportunity_score(frame):
    # Predeclared, interpretable hypothesis, not optimized on holdout data.
    # Score is intentionally not a probability or a forecast of return.
    x=frame
    score=pd.Series(0.0,index=x.index)
    score+=np.select([x.rsi.between(30,42),x.rsi.between(42,52),x.rsi.between(20,30)],[25,17,12],default=0)
    score+=np.select([x.vol_ratio>=1.5,x.vol_ratio>=1.1,x.vol_ratio>=.8],[20,14,7],default=0)
    score+=np.select([x.fear<=20,x.fear<=35,x.fear<=55],[20,15,7],default=0)
    score+=np.where(x.close>x.ema20,15,0)
    score+=np.where(x.ema20>x.ema50,10,0)
    score+=np.where(x.regime.isin(['강한 상승','횡보/전환']),10,0)
    return score.clip(0,100)

def build_entry_study(frame, strategy, train_end, test_start, cooldown_bars=0):
    # The signal is known at candle close i; buy at candle i+1 open.
    # Exclude signals whose complete horizon is not contained in its partition.
    rows=[];last_idx=-10**9
    idx=np.flatnonzero(frame[strategy].fillna(False).to_numpy(dtype=bool))
    for i in idx:
        if i-last_idx<=cooldown_bars:continue
        if i+1+max(HORIZONS.values())>=len(frame):continue
        entry_time=frame.index[i+1]
        end_time=frame.index[i+1+max(HORIZONS.values())]
        if entry_time<=train_end and end_time<=train_end:segment='개발'
        elif entry_time>=test_start and end_time<=frame.index[-1]:segment='검증'
        else:continue
        entry=float(frame.open.iloc[i+1])
        if not np.isfinite(entry) or entry<=0:continue
        feat=frame.iloc[i]
        r={'구간':segment,'신호시각':frame.index[i], '진입시각':entry_time,
           '진입가':entry,'RSI':float(feat.rsi),'공포':float(feat.fear) if pd.notna(feat.fear) else np.nan,
           '거래량배율':float(feat.vol_ratio),'EMA20상회':bool(feat.close>feat.ema20),
           '국면':str(feat.regime),'점수':float(feat.opportunity_score)}
        for label,bars in HORIZONS.items():
            last=i+1+bars
            r[f'{label} 종가수익률(%)']=100*(float(frame.close.iloc[last])/entry-1)
            # Exclude entry bar intrabar highs/lows: order within entry bar is unknown.
            sub=frame.iloc[i+2:last+1]
            r[f'{label} MFE(%)']=100*(float(sub.high.max())/entry-1) if len(sub) else np.nan
            r[f'{label} MAE(%)']=100*(float(sub.low.min())/entry-1) if len(sub) else np.nan
        rows.append(r);last_idx=i
    return pd.DataFrame(rows)

def study_summary(events, fee_pct, slip_pct):
    rows=[]
    for label in HORIZONS:
        col=f'{label} 종가수익률(%)'
        # Entry slip and fee, exit slip and fee. Fee/slip approximate two-sided drag.
        gross=events[col].dropna()
        net=(1+gross/100)*((1-slip_pct/100)*(1-fee_pct/100))/((1+slip_pct/100)*(1+fee_pct/100))-1
        net*=100
        rows.append({'보유 기간':label,'표본':len(net), '평균 순수익률(%)':net.mean() if len(net) else np.nan,
                     '중앙값(%)':net.median() if len(net) else np.nan,
                     '수익 확률(%)':100*(net>0).mean() if len(net) else np.nan,
                     '평균 MFE(%)':events[f'{label} MFE(%)'].mean() if len(net) else np.nan,
                     '평균 MAE(%)':events[f'{label} MAE(%)'].mean() if len(net) else np.nan})
    return pd.DataFrame(rows)

st.subheader('🧪 v1.5 · 매수 시점별 미래 성과 연구')
st.caption('진입 시점에 이미 알려진 RSI·거래량·공포·추세·시장 국면을 기록하고, 그 이후 1·3·7·14일 성과를 평가합니다. 미래 성과는 점수 계산에 사용하지 않습니다.')
research_strategy=st.selectbox('진입 신호 연구 대상',list(CONFIG),index=1,key='v15_strategy')
nonoverlap=st.checkbox('14일 이내 중복 신호 제외 (표본 독립성 보조)',value=True)
research_frame=a.copy()
research_frame['opportunity_score']=entry_opportunity_score(research_frame)
# Use full indicator warmup, but enforce horizon completion entirely inside each segment.
research=build_entry_study(research_frame,CONFIG[research_strategy]['signal'],train.index[-1],test.index[0],84 if nonoverlap else 0)
if research.empty:
    st.info('이 설정에서 14일 미래 가격까지 확보된 진입 신호가 없습니다. 분석 기간을 늘리거나 중복 제외를 해제해 보세요.')
else:
    for segment in ['개발','검증']:
        sample=research[research['구간']==segment].copy()
        st.markdown(f'**{segment} 구간 · 완전 관측된 신호 {len(sample)}개**')
        if sample.empty:
            st.info('이 구간은 완전한 14일 관측값이 없습니다.')
            continue
        st.dataframe(study_summary(sample,fee,slip).round(2),hide_index=True,use_container_width=True)
        if len(sample)<20:
            st.warning(f'{segment} 표본이 {len(sample)}개뿐이어서 평균이나 승률이 불안정합니다. 이 수치로 매수 규칙을 확정하면 안 됩니다.')
        if segment=='검증':
            sample['점수 구간']=pd.cut(sample['점수'],bins=[-1,39,59,79,100],labels=['0~39','40~59','60~79','80~100'])
            score_tbl=sample.groupby('점수 구간',observed=True).agg(
                표본=('점수','size'),평균점수=('점수','mean'),
                **{'7일 평균수익(%)':('7일 종가수익률(%)','mean'),
                   '7일 상승비율(%)':('7일 종가수익률(%)',lambda s:100*(s>0).mean()),
                   '14일 평균수익(%)':('14일 종가수익률(%)','mean')}).reset_index()
            st.markdown('**검증 구간 · 진입 점수별 결과 (종가 변화, 거래비용 차감 전)**')
            st.dataframe(score_tbl.round(2),hide_index=True,use_container_width=True)
            if len(sample)>=5:
                plot=go.Figure()
                plot.add_trace(go.Scatter(x=sample['점수'],y=sample['7일 종가수익률(%)'],mode='markers',
                                          text=sample['국면'],name='7일 수익률'))
                plot.update_layout(height=300,xaxis_title='진입 당시 점수 (0~100)',yaxis_title='7일 가격 변화 (%)',
                                   margin=dict(l=10,r=10,t=20,b=30))
                st.plotly_chart(plot,use_container_width=True)
            st.markdown('**검증 구간 · 국면별 7일 성과**')
            by_reg=sample.groupby('국면',as_index=False).agg(표본=('국면','size'),
                평균7일변화=('7일 종가수익률(%)','mean'),상승비율=('7일 종가수익률(%)',lambda s:100*(s>0).mean()))
            st.dataframe(by_reg.round(2),hide_index=True,use_container_width=True)
    st.markdown('**진입 당시 조건 및 사후 성과 상세 기록**')
    show=research.copy()
    for c in ['신호시각','진입시각']:
        show[c]=show[c].dt.tz_convert(KST).dt.strftime('%Y-%m-%d %H:%M')
    st.dataframe(show.round(2),hide_index=True,use_container_width=True)
    st.download_button('v1.5 진입 신호 연구 CSV 다운로드',show.to_csv(index=False).encode('utf-8-sig'),
                       file_name=f'{market}_{CONFIG[research_strategy]["signal"]}_v1_5_entry_study.csv',mime='text/csv')

st.subheader('🔎 현재 진입 기회 점수 · 설명용')
now_score=float(research_frame.opportunity_score.iloc[-1])
st.metric('현재 확정봉 기준 점수',f'{now_score:.0f}/100')
st.caption('점수는 사전에 정한 휴리스틱이며 미래 상승 확률이나 예상 수익률이 아닙니다. 점수가 높아도 진입 신호가 없으면 거래 후보가 아닙니다.')
st.caption('주의: 공포탐욕지수의 실제 게시 시각, 업비트 캔들 결측, 거래소 체결 지연은 별도 검증이 필요합니다. 동일 신호가 여러 보유기간에서 중복되므로 각 기간 성과는 독립적인 표본이 아닙니다. 검증 결과를 보고 가중치를 바꾸면 새 검증 데이터가 필요합니다.')
