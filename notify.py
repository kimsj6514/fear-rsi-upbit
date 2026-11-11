"""Hourly Telegram notifier with deduplication and signal-entry history."""
import csv
import json
import os
from pathlib import Path
import requests
from signals import MARKETS, analyze, fetch_fear

STATE = Path('alert_state.json')
HISTORY = Path('signal_history.csv')
FIELDS = ['detected_at_kst', 'market', 'signal', 'price_krw', 'fear', 'rsi_1h', 'rsi_4h', 'rsi_1d', 'drop_from_30d_high_pct', 'daily_trend', 'upbit_url']


def send(token, chat_id, message):
    response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': True}, timeout=15)
    response.raise_for_status()
    if not response.json().get('ok'):
        raise RuntimeError('텔레그램 응답 오류')


def main():
    token, chat_id = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        raise RuntimeError('GitHub Secrets에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정하세요')
    previous = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    updated = dict(previous)
    fear, _ = fetch_fear()
    failures = []
    for market in MARKETS:
        try:
            result = analyze(market, fear)
            label = result['label']
            current = label if ('매수' in label or '매도' in label) else '없음'
            before = previous.get(market, '없음')
            if current != '없음' and current != before:
                detail = '\n'.join(f"{frame}: {info['direction']} · RSI {info['rsi']} · 확정봉 {info['time']}" for frame, info in result['frames'].items())
                msg = (f"📈 Fear-RSI | {market}\n{'🟢' if '매수' in current else '🔴'} {label}\n{result['reason']}\n"
                       f"참고 가격(1시간 확정봉): {result['price']:,.0f}원\n"
                       f"30일 고점 대비: {result['drop30']:+.1f}%\n일봉 방향: {result['day_trend']}\n"
                       f"공포탐욕: {fear}\n{detail}\n📊 {result['chart_url']}\n참고 신호이며 자동 주문하지 않습니다.")
                send(token, chat_id, msg)
                # Log only after a confirmed successful send, to avoid phantom entries.
                from datetime import datetime
                from zoneinfo import ZoneInfo
                row = [datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S'), market, current,
                       result['price'], fear, result['frames']['1시간']['rsi'], result['frames']['4시간']['rsi'],
                       result['frames']['1일']['rsi'], round(result['drop30'], 2), result['day_trend'], result['chart_url']]
                with HISTORY.open('a', newline='', encoding='utf-8-sig') as file:
                    writer = csv.writer(file)
                    if file.tell() == 0:
                        writer.writerow(FIELDS)
                    writer.writerow(row)
                print('Sent:', market, current)
            updated[market] = current
            # Save immediately so a failure in another market does not cause repeat sends on retry.
            STATE.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except Exception as exc:
            failures.append(f'{market}: {exc}')
    if failures:
        raise RuntimeError('일부 종목 확인 실패: ' + '; '.join(failures))


if __name__ == '__main__':
    main()
