"""Shared, advisory Fear-RSI signals. Uses only completed Upbit candles."""
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import requests

KST = ZoneInfo('Asia/Seoul')
MARKETS = ['KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-DOGE']
FRAMES = {'1시간': ('minutes/60', pd.Timedelta(hours=1)), '4시간': ('minutes/240', pd.Timedelta(hours=4)), '1일': ('days', pd.Timedelta(days=1))}
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Fear-RSI-Simple/2.1'})


def fetch_candles(market, frame):
    endpoint, duration = FRAMES[frame]
    resp = SESSION.get(f'https://api.upbit.com/v1/candles/{endpoint}', params={'market': market, 'count': 200}, timeout=15)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError('캔들 데이터가 없습니다')
    df = pd.DataFrame(rows)
    df['start'] = pd.to_datetime(df['candle_date_time_kst'])
    df['close'] = pd.to_numeric(df['trade_price'])
    df = df.sort_values('start').reset_index(drop=True)
    now = pd.Timestamp.now(tz=KST).tz_localize(None)
    # Daily candles begin at 09:00 KST; other intervals begin at their exchange timestamps.
    df = df.loc[df['start'] + duration <= now].copy()
    if len(df) < 30:
        raise ValueError('확정 캔들이 부족합니다')
    return df


def fetch_fear():
    resp = SESSION.get('https://api.alternative.me/fng/', params={'limit': 1, 'format': 'json'}, timeout=15)
    resp.raise_for_status()
    row = resp.json()['data'][0]
    return int(row['value']), dt.datetime.fromtimestamp(int(row['timestamp']), tz=KST)


def rsi_wilder(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    result = 100 - 100 / (1 + rs)
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    return result.mask((avg_gain == 0) & (avg_loss == 0), 50)


def frame_signal(df, fear):
    rsi = rsi_wilder(df['close'])
    last, prev = float(rsi.iloc[-1]), float(rsi.iloc[-2])
    direction = '매수' if fear <= 35 and last <= 40 and last > prev else ('매도' if fear >= 65 and last >= 60 and last < prev else '없음')
    return {'direction': direction, 'rsi': round(last, 1), 'price': float(df['close'].iloc[-1]), 'time': df['start'].iloc[-1].strftime('%Y-%m-%d %H:%M'), 'rsi_series': rsi}


def overall(frames):
    # Strong requires BOTH 1-hour and 4-hour alignment; daily confirmation is a bonus.
    one, four, day = (frames[f]['direction'] for f in ('1시간', '4시간', '1일'))
    if one == four and one != '없음':
        return f'강한 {one} 신호', f'1시간봉·4시간봉 동시 {one}' + (' · 일봉도 일치' if day == one else '')
    active = [(f, frames[f]['direction']) for f in ('1시간', '4시간', '1일') if frames[f]['direction'] != '없음']
    if active:
        # Conflicting buy and sell signals must not be presented as a clear direction.
        directions = {d for _, d in active}
        if len(directions) > 1:
            return '신호 엇갈림', '시간봉별 매수·매도 방향이 달라 관찰 필요'
        direction = active[0][1]
        return f'{direction} 후보', ' · '.join(f'{f} {d}' for f, d in active)
    return '신호 없음', '세 시간봉 모두 매수·매도 조건 미충족'


def analyze(market, fear, fetch=fetch_candles):
    frames = {}
    for frame in FRAMES:
        frames[frame] = frame_signal(fetch(market, frame), fear)
    label, reason = overall(frames)
    return {'market': market, 'label': label, 'reason': reason, 'frames': frames}
