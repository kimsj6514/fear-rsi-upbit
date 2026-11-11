import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title='Fear-RSI v1.6',page_icon='📈',layout='wide')
st.title('📈 Fear-RSI 퀀트 대시보드 v1.6')
st.caption('업비트 BTC·ETH | 전체 시장 4시간봉 기회 연구 | D1/D2/D3 기준 비교 | 자동 주문 없음')
KST='Asia/Seoul'
HORIZONS={'1일':6,'3일':18,'7일':42,'14일':84}
CONFIG={'D1 · 단기 반등':('D1',2.5,4.5,24),'D2 · 스윙':('D2',6,12,336),'D3 · 추세 전환':('D3',10,25,1344)}

@st.cache_data(ttl=900,show_spinner=False)
def candles(market,days):
    session=requests.Session();session.headers.update({'User-Agent':'FearRSIResearch/1.6'})
    target=(days+240)*6;rows=[];cursor=None
    while len(rows)<target:
        params={'market':market,'count':min(200,target-len(rows))}
        if cursor:params['to']=cursor
        batch=None
        for k in range(5):
            try:
                resp=session.get('https://api.upbit.com/v1/candles/minutes/240',params=params,timeout=25)
                if resp.status_code==429:time.sleep(1.5*(k+1));continue
                resp.raise_for_status();batch=resp.json();break
            except (requests.RequestException,ValueError):
                if k==4:raise
                time.sleep(k+1)
        if not batch:break
        rows.extend(batch)
        cursor=pd.Timestamp(batch[-1]['candle_date_time_utc'],tz='UTC').strftime('%Y-%m-%dT%H:%M:%SZ')
        time.sleep(.13)
    if not rows:raise ValueError(f'{market} 캔들 없음')
    d=pd.DataFrame(rows);d['ts']=pd.to_datetime(d.candle_date_time_utc,utc=True)
    d=d.sort_values('ts').drop_duplicates('ts').set_index('ts')
    d=d.rename(columns={'opening_price':'open','high_price':'high','low_price':'low','trade_price':'close','candle_acc_trade_volume':'volume'})
    d=d[['open','high','low','close','volume']].astype(float)
    return d[d.index+pd.Timedelta(hours=4)<=pd.Timestamp.now(tz='UTC')]

@st.cache_data(ttl=3600,show_spinner=False)
def fear_data():
    r=requests.get('https://api.alternative.me/fng/',params={'limit':0,'format':'json'},timeout=30);r.raise_for_status()
    d=pd.DataFrame(r.json()['data']);d['date']=pd.to_datetime(d.timestamp.astype(int),unit='s',utc=True).dt.floor('D')
    d['fear']=pd.to_numeric(d.value,errors='coerce')
    return d.dropna(subset=['fear']).drop_duplicates('date').set_index('date').fear

def rsi(s,n=14):
    delta=s.diff();up=delta.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    down=(-delta.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    v=100-100/(1+up/down.replace(0,np.nan))
    return v.mask((down==0)&(up>0),100).mask((down==0)&(up==0),50)

def prepare(raw,fear):
    d=raw.copy();d['rsi']=rsi(d.close)
    d['fear']=(d.index.floor('D')-pd.Timedelta(days=1)).map(fear)
    daily=d.close.resample('1D').last().dropna()
    d['sma200']=d.index.floor('D').map(daily.rolling(200,min_periods=200).mean().shift(1))
    d['sma50']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(1))
    d['sma50_old']=d.index.floor('D').map(daily.rolling(50,min_periods=50).mean().shift(8))
    d['ema20']=d.close.ewm(span=20,adjust=False).mean();d['ema50']=d.close.ewm(span=50,adjust=False).mean()
    d['vol_ratio']=d.volume/d.volume.rolling(30,min_periods=30).mean().replace(0,np.nan)
    d['high_prev']=d.high.rolling(30,min_periods=30).max().shift(1)
    d['ret7']=d.close/d.close.shift(42)-1
    slope=d.sma50/d.sma50_old-1
    cond=[(d.close>d.sma200)&(slope>.01),(d.close>d.sma200)&(slope<=.01),
          (d.close<=d.sma200)&(slope<-.025)&(d.ret7<-.04),
          (d.close<=d.sma200)&(slope<-.005)]
    d['regime']=np.select(cond,['강한 상승','상승 후 조정','강한 하락','하락 속 소강'],default='횡보/전환')
    d['D1']=(d.rsi.shift(1)<32)&(d.rsi>=32)&(d.close>d.open)&(d.fear<=55)
    d['D2']=(d.rsi.shift(1)<42)&(d.rsi>=42)&(d.close>d.ema20)&(d.vol_ratio>=.8)&(d.fear<=65)
    d['D3']=(d.close>d.high_prev)&(d.ema20>d.ema50)&d.rsi.between(48,75)&(d.vol_ratio>=1.05)
    d['score']=(np.select([d.rsi.between(30,42),d.rsi.between(42,52),d.rsi.between(20,30)],[25,17,12],0)
        +np.select([d.vol_ratio>=1.5,d.vol_ratio>=1.1,d.vol_ratio>=.8],[20,14,7],0)
        +np.select([d.fear<=20,d.fear<=35,d.fear<=55],[20,15,7],0)
        +np.where(d.close>d.ema20,15,0)+np.where(d.ema20>d.ema50,10,0)
        +np.where(d.regime.isin(['강한 상승','횡보/전환']),10,0)).clip(0,100)
    return d

def all_bar_events(d,market,days,fee,slip):
    # Event timestamp = confirmed bar close; hypothetical entry = next bar open.
    # Label end = entry open + exactly N completed 4h candles; no partial horizon.
    # Dataset split is by signal timestamp, and each label must finish inside split.
    a=d[d.index>=d.index.max()-pd.Timedelta(days=days)].copy()
    split=int(len(a)*.7);boundary=a.index[split]
    # Date-scoped 70/30 boundaries are shared across BTC/ETH approximately, not exact.
    out=[];n=len(a)
    for i in range(1,n-1):
        row=a.iloc[i];entry_i=i+1
        if pd.isna(row.rsi) or pd.isna(row.vol_ratio) or pd.isna(row.fear):continue
        entry=float(a.open.iloc[entry_i]);
        if not np.isfinite(entry) or entry<=0:continue
        rec={'종목':market,'신호시각':a.index[i],'진입시각':a.index[entry_i],
             '구간':'개발' if i<split else '검증',
             'RSI':float(row.rsi),'공포':float(row.fear),'거래량배율':float(row.vol_ratio),
             '국면':str(row.regime),'점수':float(row.score),'D1':bool(row.D1),'D2':bool(row.D2),'D3':bool(row.D3)}
        for label,h in HORIZONS.items():
            end=entry_i+h
            # Do not let the 70/30 label cross partition boundary.
            if (i<split and end>=split) or end>=n:
                rec[f'{label} 순수익률(%)']=np.nan
                rec[f'{label} MFE(%)']=np.nan;rec[f'{label} MAE(%)']=np.nan
                continue
            gross=float(a.close.iloc[end])/entry
            net=100*(gross*(1-slip/100)*(1-fee/100)/((1+slip/100)*(1+fee/100))-1)
            # Entry bar excluded: unknown order of open/high/low; horizon bars only.
            path=a.iloc[entry_i+1:end+1]
            rec[f'{label} 순수익률(%)']=net
            rec[f'{label} MFE(%)']=100*(float(path.high.max())/entry-1)
            rec[f'{label} MAE(%)']=100*(float(path.low.min())/entry-1)
        out.append(rec)
    return pd.DataFrame(out)

def summarize(events,horizon):
    col=f'{horizon} 순수익률(%)';s=events[col].dropna()
    return {'표본':len(s),'평균 순수익(%)':round(s.mean(),2) if len(s) else np.nan,
            '중앙값(%)':round(s.median(),2) if len(s) else np.nan,
            '수익 비율(%)':round(100*(s>0).mean(),1) if len(s) else np.nan,
            '하위 10% 수익(%)':round(s.quantile(.1),2) if len(s) else np.nan,
            '평균 MFE(%)':round(events.loc[s.index,f'{horizon} MFE(%)'].mean(),2) if len(s) else np.nan,
            '평균 MAE(%)':round(events.loc[s.index,f'{horizon} MAE(%)'].mean(),2) if len(s) else np.nan}

with st.sidebar:
    st.header('연구 설정')
    days=st.select_slider('분석 기간(일)',options=[365,540,720,1080,1440],value=720)
    fee=st.number_input('편도 수수료(%)',0.,1.,0.05,0.01)
    slip=st.number_input('편도 슬리피지(%)',0.,2.,0.10,0.05)
    horizon=st.selectbox('비교 기간',list(HORIZONS),index=2)
    sample_every=st.selectbox('표본 간격(4시간봉)',[1,6,18,42],index=3,
        help='1=모든 봉, 42=7일 간격. 같은 구간에서 발생한 신호는 여전히 상관될 수 있습니다.')
    market_view=st.selectbox('화면 종목',['전체','KRW-BTC','KRW-ETH'])
    if st.button('API 캐시 초기화'):
        candles.clear();fear_data.clear();st.rerun()

try:
    with st.spinner('BTC·ETH 과거 캔들 수집 및 분석 중… 처음 실행은 몇 분 걸릴 수 있습니다.'):
        f=fear_data();frames={m:prepare(candles(m,days),f) for m in ['KRW-BTC','KRW-ETH']}
        all_events=pd.concat([all_bar_events(d,m,days,fee,slip) for m,d in frames.items()],ignore_index=True)
except Exception as exc:
    st.error(f'데이터 수집/분석 오류: {exc}');st.stop()
if all_events.empty:st.error('연구할 데이터가 없습니다.');st.stop()

st.info('v1.6은 매수 시점의 조건과 이후 가격 변화를 연구하는 도구입니다. 통계적 연관성은 매수 성공 확률이나 미래 수익 보장이 아닙니다.')
latest=[]
for m,d in frames.items():
    z=d.iloc[-1];latest.append({'종목':m,'확정봉 시작(KST)':d.index[-1].tz_convert(KST).strftime('%Y-%m-%d %H:%M'),
    '종가(원)':round(z.close),'RSI':round(z.rsi,1),'공포':z.fear,'국면':z.regime,'점수':round(z.score),
    'D1':bool(z.D1),'D2':bool(z.D2),'D3':bool(z.D3)})
st.subheader('📌 BTC·ETH 최신 확정봉')
st.dataframe(pd.DataFrame(latest),hide_index=True,use_container_width=True)

# Deterministic systematic sampling: anchor to each asset's full time index, not filtered signals.
events=all_events[all_events['종목'].eq(market_view)] if market_view!='전체' else all_events
sampled=pd.concat([g.iloc[::sample_every] for _,g in events.groupby('종목')],ignore_index=True)
valid=sampled.dropna(subset=[f'{horizon} 순수익률(%)'])
st.subheader('🔬 전체 시장 4시간봉 — 신호 없는 구간 포함')
st.caption(f'표본 간격 {sample_every}봉 · 종목별 시간순 70:30 분할 · {horizon} 수익률은 편도 수수료/슬리피지 차감. 4시간봉 중첩 표본은 독립이 아닙니다.')
rows=[]
for seg in ['개발','검증']:
    sub=valid[valid['구간']==seg];rows.append({'구간':seg,**summarize(sub,horizon)})
st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)

st.subheader('🎯 기존 D1/D2/D3 신호와 시장 전체 비교')
rows=[]
for seg in ['개발','검증']:
    base=events[(events['구간']==seg)&events[f'{horizon} 순수익률(%)'].notna()]
    rows.append({'구간':seg,'집단':'전체 4시간봉 (간격 적용)',**summarize(valid[valid['구간']==seg],horizon)})
    for name,(sig,*_) in CONFIG.items():
        sub=base[base[sig]]
        # 14-day cooldown based on time, so one prolonged setup doesn't dominate.
        sub=sub.sort_values('신호시각')
        kept=[];last=None
        for idx,row in sub.iterrows():
            if last is None or row['신호시각']-last>=pd.Timedelta(days=14):
                kept.append(idx);last=row['신호시각']
        rows.append({'구간':seg,'집단':name+' (14일 간격)',**summarize(sub.loc[kept],horizon)})
st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
st.caption('전체 시장 표본과 전략 신호 표본은 추출 방식이 달라 직접적인 인과 비교는 불가능합니다. 전략별 14일 중복 제거를 적용했습니다.')

st.subheader('📊 조건별 수익 기회 — 개발/검증 분리')
feature=st.selectbox('분석 조건',['시장 국면','RSI 구간','공포 구간','거래량 구간','진입 점수 구간'])
tbl=valid.copy()
tbl['RSI 구간']=pd.cut(tbl.RSI,[0,20,30,40,50,60,70,80,100],include_lowest=True)
tbl['공포 구간']=pd.cut(tbl['공포'],[-1,20,35,50,65,80,100])
tbl['거래량 구간']=pd.cut(tbl['거래량배율'],[-.01,.5,.8,1.1,1.5,2.5,np.inf])
tbl['진입 점수 구간']=pd.cut(tbl['점수'],[-1,20,40,60,80,100])
tbl['시장 국면']=tbl['국면']
summary=[]
for (seg,bucket),g in tbl.groupby(['구간',feature],observed=True):
    summary.append({'구간':seg,'조건':str(bucket),**summarize(g,horizon)})
result=pd.DataFrame(summary)
st.dataframe(result,hide_index=True,use_container_width=True)
if not result.empty:
    fig=px.bar(result,x='조건',y='평균 순수익(%)',color='구간',barmode='group',hover_data=['표본','수익 비율(%)','하위 10% 수익(%)'])
    fig.update_layout(height=380,xaxis_title='',yaxis_title='평균 순수익(%)')
    st.plotly_chart(fig,use_container_width=True)

st.subheader('📉 동일 시장 국면에서 BTC와 ETH 비교')
market_rows=[]
for (seg,m,reg),g in valid.groupby(['구간','종목','국면']):
    market_rows.append({'구간':seg,'종목':m,'국면':reg,**summarize(g,horizon)})
st.dataframe(pd.DataFrame(market_rows),hide_index=True,use_container_width=True)

st.subheader('📈 가격·RSI·매수 신호')
plot_market=market_view if market_view!='전체' else 'KRW-BTC'
d=frames[plot_market].tail(350)
fig=go.Figure();fig.add_trace(go.Scatter(x=d.index,y=d.close,name='종가'))
fig.add_trace(go.Scatter(x=d.index,y=d.sma200,name='SMA200'))
for name,(sig,*_) in CONFIG.items():
    pts=d[d[sig]];fig.add_trace(go.Scatter(x=pts.index,y=pts.close,mode='markers',name=name,marker={'size':8}))
fig.update_layout(height=400);st.plotly_chart(fig,use_container_width=True)
st.line_chart(d[['rsi']])

st.subheader('📁 분석 원본 CSV')
st.download_button('전체 진입 시점·사후 성과 CSV 다운로드',all_events.to_csv(index=False).encode('utf-8-sig'),
                   file_name='Fear_RSI_v1_6_all_bar_study.csv',mime='text/csv')
with st.expander('분석 해석 및 한계'):
    st.markdown("""- **전체 시장 기준**: 매수 신호가 없는 4시간봉도 다음 봉 시가에 가상 진입해 미래 종가 수익률을 평가합니다. 모든 봉에 실제로 투자했다는 뜻이 아닙니다.
- **데이터 분리**: 앞 70% 개발, 뒤 30% 검증. 각 기간의 미래 수익률이 경계를 넘으면 해당 기간 결과를 결측 처리합니다. 시장 국면과 점수는 당시 봉까지의 정보만 사용합니다.
- **비용**: 진입·청산 각각 고정 수수료와 슬리피지를 적용한 단순 모형입니다. 실제 호가·유동성·주문 체결과 다릅니다.
- **표본 상관**: 4시간봉 간격의 미래 7일 수익률은 심하게 겹칩니다. 간격 42봉은 중첩을 줄일 뿐 통계적 독립성을 보장하지 않습니다.
- **시장 국면**: SMA200은 전일 이전까지의 일봉 평균, 나머지 지표는 확정된 현재 봉 정보만 사용합니다. 공포지수는 전일 UTC 날짜 값을 사용하지만 실제 게시 시각 검증은 필요합니다.
- **장기 데이터**: 업비트 API의 과거 4시간봉을 요청합니다. 오래된 데이터의 누락·불규칙한 시간 간격은 별도 점검해야 합니다.
- **검증 결과 선택 편향**: 검증 구간에서 가장 높은 조건을 선택하면 더 이상 독립 검증이 아닙니다. 다음 시점의 신규 데이터로 재검증해야 합니다.
- **자동 주문 없음**. 본 화면은 연구용이며 투자 권유가 아닙니다.""")
