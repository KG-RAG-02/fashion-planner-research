# VOGUE Market Research Agent

VOGUE의 시장성 조사 단계에서 사용할 수 있는 계약 중립적 Python 도메인 코어입니다. 현재 구현은 승인된 시장 자료원이 정해지지 않은 상태를 반영해 외부 API, 크롤러, 모델, 그래프 저장소를 포함하지 않습니다.

## 현재 동작

- `MarketResearchResult` 공통 응답 envelope와 Evidence 참조 모델을 제공합니다.
- 조사 출처는 `MarketResearchSource` 인터페이스로 연결하도록 분리했습니다.
- 출처가 설정되지 않으면 `no_results`와 사유를 반환합니다. 이를 시장값 0으로 표현하지 않습니다.
- 권한이 `authorized`로 확인되지 않은 Evidence와 이를 근거로 삼은 finding은 출력하지 않습니다.
- `fact` 유형 finding은 Evidence ID를 반드시 가져야 합니다. 참조가 누락되거나 유효하지 않으면 해당 finding을 제거하고 오류를 남깁니다.
- provider 결과가 부분적이어도 유효한 finding과 Evidence를 보존하고 `partial` 상태를 반환합니다.
- `product_concept`는 후속 교차 연결을 위한 선택 입력입니다. 시장 조사는 이 결과가 없어도 실행할 수 있습니다.

이 패키지는 요청·트렌드·재고 입력을 opaque dictionary로 전달합니다. 실제 공통 입력 스키마가 다른 저장소와 합의되면 해당 계약 어댑터를 추가해야 합니다. 이 저장소의 JSON Schema는 구현 초안이며, 다른 에이전트 저장소와 동기화된 wire contract로 간주하지 않습니다.

## 빠른 실행

Python 3.11 이상, 외부 패키지 없이 실행할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

합성 예시는 다음 명령으로 실행합니다.

```bash
PYTHONPATH=src python3 examples/synthetic_demo.py
```

## Docker

Docker Desktop 또는 Docker Engine과 Compose를 설치한 뒤 프로젝트 루트에서 실행합니다.

```bash
docker compose run --build --rm market-research-agent-demo
docker compose --profile test run --build --rm market-research-agent-tests
```

두 서비스는 합성 예시와 테스트를 실행한 뒤 종료하는 one-shot 컨테이너입니다. 현재 HTTP 서버나 승인된 외부 자료원이 없어 포트나 비밀키를 설정하지 않으며, 실행 컨테이너는 네트워크를 사용하지 않습니다. Docker 이미지에는 `.env`, `.venv`, 원본·처리 데이터가 포함되지 않습니다.

## Docker 실행

Docker Desktop 또는 Docker Engine과 Compose가 설치되어 있으면 합성 데모를 컨테이너에서 실행합니다.

```bash
docker compose run --build --rm market-research-agent-demo
```

컨테이너 안에서 테스트를 실행할 수 있습니다.

```bash
docker compose --profile test run --build --rm market-research-agent-tests
```

현재 구성은 데모와 테스트용 one-shot 컨테이너입니다. HTTP 서버나 승인된 외부 데이터 공급자가 아직 없으므로 포트와 API 키를 설정하지 않습니다. 컨테이너 네트워크도 비활성화해 합성 예시가 외부에 접속하지 않도록 했습니다. 향후 승인된 provider를 연결할 때는 별도 실행 설정과 필요한 네트워크 범위를 검토해야 합니다.

## 사용 예시

```python
from market_research_agent import MarketResearchAgent, MarketResearchContext

context = MarketResearchContext(
    request_id="req-123",
    planning_request={"query": "2027SS 한국 여성 니트"},
    trend_result={"status": "ok", "findings": []},
    inventory_product_result={"status": "ok", "findings": []},
)

# 승인된 source adapter를 넘기기 전에는 안전하게 no_results를 반환합니다.
result = MarketResearchAgent().run(context)
print(result.to_dict())
```

새상품 조사 에이전트와의 병렬 실행은 상위 workflow가 각 에이전트를 독립 호출해 구현해야 합니다. 이 저장소는 transport나 orchestrator를 선택하지 않습니다. 초안 생성 단계는 `ProductConcept`와 `MarketResearchResult`를 함께 받은 뒤 결과별 상태와 Evidence를 보존해야 합니다.

## 미결정 사항

- 승인된 시장 조사 자료원, 라이선스·접근 권한, 조사 지표와 시간 범위
- 다섯 저장소에 공통으로 반영할 최종 `MarketResearchResult` 스키마·버전
- API/메시지 전달 방식, 인증, 저장·보존, retry/timeout과 배포 형태
- 시장 조사 품질 평가용 승인 데이터와 팀이 정할 합격 기준

현재 MVP 지침에 따라 판매량 분석은 구현하지 않으며 재고 스냅샷을 수요나 판매량의 대리 지표로 사용하지 않습니다. 출처·구현 경계의 검토 기록은 [`docs/design/notion-review.md`](docs/design/notion-review.md), 계층 구조와 의존 방향은 [`docs/design/project-structure.md`](docs/design/project-structure.md)에 있습니다.

## 폴더 구조

```text
repository-root/
├── contracts/schemas/             # MarketResearchResult 스키마 초안
├── data/                           # Git 제외 런타임 데이터 위치
├── Dockerfile                      # non-root Python runtime image
├── compose.yaml                    # 합성 데모 및 테스트 서비스
├── docs/decisions/                 # 구조·구현 결정 기록
├── docs/design/                    # 요구사항과 설계 기록
├── examples/fixtures/              # 합성 데모 입력
├── src/market_research_agent/
│   ├── domain/                     # 도메인 계약과 모델
│   ├── sources/                    # 출처 인터페이스와 어댑터
│   └── workflows/                  # 검증과 결과 조립
└── tests/                          # 자동화 테스트
```

구조는 project_06의 `domain` / `sources` / `workflows` 분리를 따릅니다. 실제 자료원이 정해지기 전이므로 provider 전용 코드, 로컬 UI, 서비스 서버는 추가하지 않았습니다.
