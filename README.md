# Fear-RSI v1.0 — 업비트 퀀트 웹 대시보드

업비트 KRW-BTC / KRW-ETH 4시간봉, RSI(14), Alternative.me 공포탐욕지수를 활용하는 **연구·모의매매용** Streamlit 대시보드입니다. 자동 주문, 거래소 계정 연동, API 키 입력은 없습니다.

## PC에서 실행

1. Python 3.10 이상 설치
2. 이 폴더에서 터미널 열기
3. `python -m pip install -r requirements.txt`
4. `python -m streamlit run app.py`
5. 자동으로 열린 브라우저 또는 `http://localhost:8501` 접속

Windows에서 `python` 명령이 없으면 `py -m pip ...`, `py -m streamlit ...`을 사용하세요.

## 휴대폰에서 보기

**같은 Wi-Fi:** PC에서 `python -m streamlit run app.py --server.address 0.0.0.0` 실행 후 PC의 내부 IP 주소(예: `192.168.x.x`)와 `:8501`을 휴대폰 브라우저에 입력합니다. PC 방화벽 설정이 필요할 수 있습니다. 공용 네트워크에 무분별하게 포트를 노출하지 마세요.

**어디서나:** 본 폴더의 `app.py`와 `requirements.txt`를 개인 GitHub 저장소에 올리고 https://share.streamlit.io/ 에서 저장소 및 `app.py`를 선택하여 배포할 수 있습니다. 배포 URL은 PC와 휴대폰 모두에서 접근할 수 있습니다. 무료 호스팅 정책/사용 제한은 변경될 수 있습니다.

## 데이터

- 업비트 공개 API: `https://api.upbit.com/v1/candles/minutes/240`
- Alternative.me: `https://api.alternative.me/fng/?limit=0&format=json`
- 최초 로딩은 여러 페이지를 가져오므로 시간이 걸릴 수 있습니다. 인터넷 연결이 필수입니다.
- 공포탐욕지수는 비트코인 중심의 시장 심리 지표입니다.

## 제한사항

- 코드 작성 환경에서는 외부 API 연결이 차단되어 **실제 API 응답과 최종 수익률을 실시간 검증하지 못했습니다**. 첫 실행 시 데이터 수집 여부를 확인하세요.
- 200일 이동평균은 4시간봉 종가를 UTC 일별로 리샘플링하여 구합니다. 4시간봉 누락이 있는 날의 품질 점검이 필요합니다.
- 백테스트에는 같은 봉 내 체결 순서, 유동성, 주문 지연, 급격한 갭 등 단순화가 있습니다.
- 전략 기본값은 최적화된 값이 아닙니다. 실거래 전에 워크포워드 검증과 모의매매를 수행하세요.
