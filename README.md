# Fear-RSI v2.1 — 심플 매수·매도 신호 + 텔레그램

- 업비트 1시간 / 4시간 / 1일 **확정봉** RSI(14)
- 공포탐욕지수(Alternative.me, 일간)
- 매수 후보: 공포 ≤35, RSI ≤40, 직전 RSI보다 상승
- 매도 후보: 탐욕 ≥65, RSI ≥60, 직전 RSI보다 하락
- **강한 매수/매도**: 1시간봉과 4시간봉에서 같은 방향의 신호가 동시에 나올 때. 1일봉까지 일치하면 표시
- 자동 주문 없음. 예측이나 수익 보장 아님.

## 배포

기존 GitHub 리포지토리 `main`에 ZIP 안의 모든 파일과 `.github/workflows/telegram-alert.yml`을 같은 구조로 업로드하세요. 기존 Streamlit 주소는 그대로입니다. `app.py`만 올리면 알림은 작동하지 않습니다.

## 텔레그램 설정

1. 텔레그램에서 `@BotFather`를 열고 `/newbot`으로 봇을 만듭니다. 발급받은 **토큰은 공개하거나 코드에 붙여 넣지 마세요.**
2. 만든 봇과 대화를 열어 `/start`를 보냅니다.
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `message.chat.id` 값을 확인합니다. (주소에 들어간 토큰은 타인과 공유하지 마세요.)
4. GitHub 리포지토리 → **Settings → Secrets and variables → Actions → New repository secret** 에 다음 두 개를 등록합니다.
   - `TELEGRAM_BOT_TOKEN` : 봇 토큰
   - `TELEGRAM_CHAT_ID` : 숫자 chat ID
5. GitHub → **Actions → Fear-RSI Telegram alerts → Run workflow** 로 첫 실행을 시험합니다. 첫 실행에서 매수·매도 후보가 없으면 알림은 오지 않습니다.
6. 이후 매시간 12분(UTC 기준)에 자동 점검합니다. GitHub Actions 스케줄은 지연되거나 실행이 누락될 수 있어 정확한 시각 보장은 없습니다.

**중복 알림:** 매수/매도 후보가 새로 나타나거나 '매수 후보'→'강한 매수 신호'처럼 단계가 바뀔 때만 알립니다. 신호가 사라졌다 다시 나타나면 재알림합니다. 상태는 `alert_state.json`에 기록됩니다. 매시간 전체 종목 4개를 확인합니다.

**주의:** GitHub Actions의 워크플로 파일은 `.github/workflows/` 경로에 정확히 넣어야 합니다. GitHub 설정에서 Actions의 **Workflow permissions → Read and write permissions**가 필요할 수 있습니다. 장기간 활동이 없는 공개 저장소의 예약 작업은 자동 비활성화될 수 있습니다. 무료 사용 한도와 실행 정책을 확인하세요.
