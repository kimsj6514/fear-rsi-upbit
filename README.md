# Fear-RSI v2.3 — 4종목 심플 신호 + 텔레그램

BTC, ETH, XRP, DOGE의 업비트 원화마켓을 1시간·4시간·일봉 **확정봉** 기준으로 분석합니다. 자동매매는 하지 않습니다.

## 기존 신호 조건 (변경 없음)
- 매수: 공포탐욕 ≤35, RSI(14) ≤40, RSI가 직전 확정봉보다 상승
- 매도: 공포탐욕 ≥65, RSI(14) ≥60, RSI가 직전 확정봉보다 하락
- 강한 신호: 1시간봉과 4시간봉 같은 방향 동시 충족. 상반된 방향이면 `신호 엇갈림`.

## 추가 기능 5가지
1. 최근 **30개 확정 일봉** 최고가(고가) 대비 최신 1시간 확정봉 종가의 하락률 표시.
2. `alert_state.json`으로 중복 알림 방지. 새로운 신호/신호 등급 변경/해제 후 재발생 시 알림.
3. 최근 2개 확정 일봉 종가 비교로 일봉 방향(상승/하락/보합) 표시. 신호 조건에는 영향을 주지 않음.
4. 텔레그램 메시지와 대시보드에 업비트 종목 차트 링크.
5. **새 알림 전송 성공 시** `signal_history.csv`에 발생 시각, 당시 참고 가격(1시간 확정봉 종가), 신호, RSI, 공포탐욕, 하락률을 기록. 실제 매수가/체결가가 아님.

## 설치

ZIP 내부의 `app.py`, `signals.py`, `notify.py`, `requirements.txt`, `README.md`를 기존 GitHub 저장소 `main` 루트에 교체하고, `.github/workflows/telegram-alert.yml`도 같은 경로에 교체하세요. 기존 Streamlit 주소와 Telegram Secrets(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)는 그대로 사용합니다.

GitHub Actions → **Fear-RSI Telegram alerts** → **Run workflow**로 실행 확인. 신호가 새로 발생하지 않으면 알림이 오지 않는 것이 정상입니다. 작업 실행 결과는 Actions 로그에서 확인합니다.

기존 `alert_state.json`은 **삭제하거나 덮어쓰지 마세요**. 기존 알림 중복 방지 상태를 이어받습니다. `signal_history.csv`는 자동 생성됩니다. 공개 저장소에 저장하면 신호 기록도 공개되므로 비공개 기록이 필요하면 저장 방식 변경이 필요합니다.

예약 실행은 UTC 매시 12분이며 GitHub Actions 정책상 지연/누락될 수 있습니다. GitHub Actions → Settings → Actions → General → Workflow permissions에 쓰기 권한이 필요할 수 있습니다.

**주의:** 30일 고점 대비 하락률은 가격이 저렴한지에 대한 참고치일 뿐, 저점·반등·수익을 보장하지 않습니다. Alternative.me 공포탐욕은 시장 전체 일간 지표입니다.
