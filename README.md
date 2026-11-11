# Fear-RSI 매매 신호 — 심플 버전

목적: 업비트 관심 종목의 **매수 후보 / 매도 후보 / 신호 없음**만 보여줍니다. 자동 주문, 백테스트, 수익률 예측 기능은 없습니다.

## 규칙
- 매수 후보: 시장 공포탐욕지수 35 이하 + 확정 4시간봉 RSI(14) 40 이하 + RSI가 직전 봉보다 상승
- 매도 후보: 시장 공포탐욕지수 65 이상 + RSI(14) 60 이상 + RSI가 직전 봉보다 하락
- 그 외: 신호 없음

시장 공포탐욕지수는 전체 암호화폐 시장의 일간 지표로 코인별 지표가 아닙니다. 매수·매도 후보는 수익 보장이 아닙니다. 확정봉만 사용하고 진행 중인 봉은 제외합니다.

## 배포
GitHub 저장소 루트에 `app.py`, `requirements.txt`, `README.md` 업로드 후 Streamlit Community Cloud에서 `app.py`를 실행합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```
