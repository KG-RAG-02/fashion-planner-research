# VOGUE Market Research Agent

VOGUE의 시장성 조사 단계에서 사용할 수 있는 계약 중립적 Python 도메인 코어입니다. 현재 구현은 승인된 시장 자료원이 정해지지 않은 상태를 반영해 외부 API, 크롤러, 모델, 그래프 저장소를 포함하지 않습니다.

## 현재 동작

- `MarketResearchResult` 공통 응답 envelope와 Evidence 참조 모델을 제공합니다.
- 조사 출처는 `MarketResearchSource` 인터페이스로 연결하도록 분리했습니다.
- 출처가 설정되지 않으면 `no_results`와 사유를 반환합니다. 이를 시장값 0으로 표현하지 않습니다.
- 권한이 `authorized`로 확인되지 않은 Evidence와 이를 근거로 삼은 finding은 출력하지 않습니다.
- Evidence ID와 출처 ID·유형·이름 또는 finding ID·주제·주장이 비거나 공백이면 해당 항목을 제외하고 오류를 남깁니다.
- `fact` 유형 finding은 Evidence ID를 반드시 가져야 합니다. 참조가 누락되거나 유효하지 않으면 해당 finding을 제거하고 오류를 남깁니다.
- provider 결과가 부분적이어도 유효한 finding과 Evidence를 보존하고 `partial` 상태를 반환합니다.
- 공통 입력의 상태·제한사항·미확보 필드는 가능한 한 결과의 제한사항에 기록하며, 입력 원문 객체는 source adapter에 그대로 전달합니다.
- 입력 envelope에 `request_id`가 있으면 실행 요청 ID와 일치하는지 확인하고, 불일치 입력은 결합하지 않습니다.
- 새상품 조사 결과는 시장 조사 입력에 포함하지 않습니다. 두 에이전트는 공통 입력만으로 독립 실행하고, 초안 생성 단계가 두 결과를 함께 받습니다.

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

Docker Desktop 또는 Docker Engine과 Compose를 설치한 뒤 프로젝트 루트에서 개발 컨테이너를 시작합니다.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f market-research-agent
```

기본 컨테이너는 Docker Desktop에서 실행 상태를 유지하는 개발용 대기 프로세스입니다. 현재 API 서버나 worker는 포함하지 않습니다. 컨테이너 안에서 합성 데모를 실행하려면 다음 명령을 사용합니다.

```bash
docker compose exec market-research-agent python examples/synthetic_demo.py
```

테스트는 별도 일회 실행 컨테이너로 실행합니다.

```bash
docker compose --profile test run --build --rm market-research-agent-tests
```

데모 자체만 실행하고 종료하려면 `docker compose --profile tools run --build --rm market-research-agent-demo`를 사용합니다. 컨테이너는 네트워크를 사용하지 않으며, Docker 이미지에는 `.env`, `.venv`, 원본·처리 데이터가 포함되지 않습니다. 중지할 때는 `docker compose down`을 실행합니다.

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

새상품 조사 에이전트와의 병렬 실행은 상위 workflow가 공통 입력을 사용해 각 에이전트를 독립 호출하도록 구현해야 합니다. 두 에이전트는 서로의 결과를 읽거나 전달하지 않습니다. 이 저장소는 transport나 orchestrator를 선택하지 않습니다. 초안 생성 단계가 `ProductConcept`와 `MarketResearchResult`를 함께 받은 뒤 두 결과의 관계를 판단하고 상태와 Evidence를 보존합니다.

## 미결정 사항

- 승인된 시장 조사 자료원, 라이선스·접근 권한, 조사 지표와 시간 범위
- 다섯 저장소에 공통으로 반영할 최종 `MarketResearchResult` 스키마·버전
- API/메시지 전달 방식, 인증, 저장·보존, retry/timeout과 배포 형태
- 시장 조사 품질 평가용 승인 데이터와 팀이 정할 합격 기준

현재 MVP 지침에 따라 판매량 분석은 구현하지 않으며 재고 스냅샷을 수요나 판매량의 대리 지표로 사용하지 않습니다. 출처·구현 경계의 검토 기록은 [`docs/design/notion-review.md`](docs/design/notion-review.md), 계층 구조와 의존 방향은 [`docs/design/project-structure.md`](docs/design/project-structure.md)에 있습니다.

Notion 합의에서 구현 순서와 미결정 사항까지 연결한 전체 방향은 [`docs/design/implementation-direction.md`](docs/design/implementation-direction.md)에 정리되어 있습니다.

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
