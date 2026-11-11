"""GitHub Actions hourly Telegram notifier. Stores state in repository file."""
import json
import os
from pathlib import Path
import requests
from signals import MARKETS, analyze, fetch_fear

STATE = Path('alert_state.json')


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        raise RuntimeError('GitHub Secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 설정 필요')
    previous = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    updated = dict(previous)
    fear, _ = fetch_fear()
    failures = []
    for market in MARKETS:
        try:
            result = analyze(market, fear)
            label = result['label']
            # Notify on entering/changing buy/sell state, not every hourly run.
            current = label if ('매수' in label or '매도' in label) else '없음'
            before = previous.get(market, '없음')
            if current != '없음' and current != before:
                detail = '\n'.join(f"{f}: {i['direction']} · RSI {i['rsi']} · 확정봉 {i['time']}" for f, i in result['frames'].items())
                msg = f"📈 Fear-RSI | {market}\n{'🟢' if '매수' in current else '🔴'} {label}\n{result['reason']}\n공포탐욕: {fear}\n{detail}\n참고 신호이며 자동 주문은 하지 않습니다."
                resp = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat_id, 'text': msg}, timeout=15)
                resp.raise_for_status()
                if not resp.json().get('ok'):
                    raise RuntimeError('텔레그램 전송 실패')
                print('Sent:', market, current)
            updated[market] = current
        except Exception as exc:
            failures.append(f'{market}: {exc}')
    # Write only after each successful market; failed markets retain old state.
    STATE.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failures:
        raise RuntimeError('일부 종목 확인 실패: ' + '; '.join(failures))


if __name__ == '__main__':
    main()
