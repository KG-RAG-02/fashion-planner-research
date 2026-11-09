# VOGUE Market Research Agent

VOGUE의 시장성 조사 단계에서 사용할 수 있는 계약 중립적 Python 도메인 코어입니다. 현재 구현은 승인된 시장 자료원이 정해지지 않은 상태를 반영해 외부 API, 크롤러, 모델, 그래프 저장소를 포함하지 않습니다.

현재 저장소에는 승인된 실제 판매 원장·시장 자료 어댑터가 없어 운영 분석 시스템으로 동작하지 않습니다. 판매 관측 모델과 결과 정규화 경계는 Project_07 책임으로 구현하되, 자료원이 미구성된 경우 `no_results`와 제한 사유를 전달합니다. Kaggle/H&M 공개 과거 데이터와 합성 fixture는 운영 데이터로 사용하지 않습니다.

Project_06의 책임 정리 과정에서 H&M 과거자료 처리·품질·집계 adapter, 판매 domain model 및 합성 판매/운영 시나리오 예제와 관련 테스트가 이 저장소로 옮겨졌습니다. Project_06은 새상품 기획 전용입니다. Project_07의 합성 merchandising 시나리오는 이관된 소프트웨어 검증/역사 데모이며, 현재 Market Research Agent의 승인 자료원이나 운영 분석 기능을 뜻하지 않습니다. 이동한 H&M adapter 역시 local historical research용이며 승인된 사내 sales connector가 아닙니다. 테스트는 임시 fixture를 만들며 원본 CSV나 Project_06의 로컬 `data/`를 필요로 하지 않습니다. 실제 원본·처리 데이터, `.env`, SQLite 데이터는 복사·이동하지 않았습니다.

## 현재 동작

- `MarketResearchResult` 공통 응답 envelope와 Evidence 참조 모델을 제공합니다.
- 조사 출처는 `MarketResearchSource` 인터페이스로 연결하도록 분리했습니다.
- 출처가 설정되지 않으면 `no_results`와 사유를 반환합니다. 이를 시장값 0으로 표현하지 않습니다.
- 권한이 `authorized`로 확인되지 않은 Evidence와 이를 근거로 삼은 finding은 출력하지 않습니다.
- Evidence ID와 출처 ID·유형·이름 또는 finding ID·주제·주장이 비거나 공백이면 해당 항목을 제외하고 오류를 남깁니다.
- 판매·시장 수치의 기간/as-of, 지역, 채널, 상품 매핑, 단위, 통화, 지표 정의, 계산 참조는 Evidence 메타데이터 확장 제안에 담을 수 있습니다. 이 확장은 Project_06과 합의 전이며 wire contract가 아닙니다.
- 자료 결측, 정의/매핑 불일치, 권한 제한은 원인 코드·메시지·필드·Evidence 참조를 가진 `data_limitations` 제안 모델로 보존할 수 있습니다. `data_limitations` 또한 미합의 확장입니다.
- 판매 관측을 위한 `SalesObservation` 및 `SalesDataResult` 도메인 모델을 소유합니다. 각 관측은 지표명·값·단위·상품/기간/채널 범위와 Evidence ID를 보존합니다. 이 모델은 제안 계약이며 Project_06과 공유 wire contract로 승인된 상태는 아닙니다.
- `fact` 유형 finding은 Evidence ID를 반드시 가져야 합니다. 참조가 누락되거나 유효하지 않으면 해당 finding을 제거하고 오류를 남깁니다.
- provider 결과가 부분적이어도 유효한 finding과 Evidence를 보존하고 `partial` 상태를 반환합니다.
- 공통 입력의 상태·제한사항·미확보 필드는 가능한 한 결과의 제한사항에 기록하며, 입력 원문 객체는 source adapter에 그대로 전달합니다.
- 입력 envelope에 `request_id`가 있으면 실행 요청 ID와 일치하는지 확인하고, 불일치 입력은 결합하지 않습니다.
- `PlanningRequest`, `TrendResult`, `InventoryProductResult`를 받아 조사하되, Project_06 상품 기획 결과는 입력으로 받지 않습니다. VOGUE 상위 흐름은 Project_07 결과가 Project_06보다 먼저 전달되는 순차 처리입니다.
- `CompositeMarketResearchSource`는 호출자가 제공한 source adapter들의 결과를 결합하고, 일부 source 실패 시 가용 결과와 오류를 함께 보존합니다. 현재 승인된 source adapter는 연결되어 있지 않습니다.

이 패키지는 요청·트렌드·재고 입력을 opaque dictionary로 전달합니다. 실제 공통 입력 스키마가 다른 저장소와 합의되면 해당 계약 어댑터를 추가해야 합니다. `contracts/schemas/market-research-result.schema.json`은 현재 로컬 1.0 구현을 설명하며, `contracts/schemas/proposals/`의 1.1은 새 판매·시장 경계를 검토하기 위한 제안입니다. 어느 파일도 다른 에이전트 저장소와 합의된 wire contract를 뜻하지 않습니다.

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

## 시장성 조사 결과 생성

CLI는 `PlanningRequest`, `TrendResult`, `InventoryProductResult` 입력 JSON을 검증하고 판매·시장 조사 결과만 반환합니다. Project_07은 새상품 콘셉트나 기획안을 생성하지 않습니다. 시장 자료원은 현재 미연결 상태이므로 기본 실행은 시장값을 만들지 않고 `no_results`를 반환합니다.

```bash
PYTHONPATH=src python3 -m market_research_agent.cli \\
  --inputs-json examples/fixtures/market-research-input.example.json \\
  --metrics "category_sales,market_size,price_band" \\
  --hm-processed-dir data/processed/hm-personalized-fashion-recommendations \\
  --hm-category-map "Garment Upper body=Outerwear" \\
  --hm-mapping-version "vogue-taxonomy-2026-11-01" \\
  --proposal-v1-2
```

입력 예시 파일은 이 저장소의 `examples/fixtures/market-research-input.example.json`을 참고하세요. 모든 상위 결과의 `request_id`가 일치해야 합니다. H&M 어댑터는 승인 여부가 별도 확인되지 않으면 수치 관측을 출력하지 않습니다. 실제 데이터 사용 승인을 받은 뒤에만 `--hm-usage-authorized`를 지정하십시오. 이 플래그는 승인 절차가 아니며, 호출자가 외부에서 확인한 승인을 선언합니다. 기본 1.0 직렬화는 기존 결과 스키마를 유지합니다. `--proposal-v1-1`은 이전 미합의 판매 데이터 확장, `--proposal-v1-2`는 판매·시장·원가/마진 영역이 분리된 최신 proposal을 출력합니다. 둘 다 공통 wire contract를 뜻하지 않습니다.

승인된 시장자료 파일은 `--market-observations-csv`와 `--market-manifest`로 함께 전달할 수 있습니다. manifest의 `permission_status`가 `authorized`가 아니면 관측을 내보내지 않습니다. 입력의 원문 `planning_request.query`는 각 source adapter로 전달되고 1.2 proposal의 `requested_scope.query`에도 보존됩니다. CSV `search_text` 열이 있으면 원문 쿼리로 literal 검색하며, 열이 없으면 검색이 적용되지 않았다는 제한을 `partial` 상태로 표시합니다. 이 로컬 CSV 어댑터는 외부 API를 호출하지 않으며, KOSIS/Euromonitor/WGSN에 자동 연결되지 않습니다. CSV 열 정의, manifest 예시, H&M 처리 파일 준비와 두 어댑터를 한 번에 실행하는 방법은 [`docs/design/local-market-data-connection.md`](docs/design/local-market-data-connection.md)를 참고하세요. API 자격 증명은 CLI 인수나 데이터 manifest에 넣지 마세요.

핵심 설정 환경변수는 `VOGUE_HM_PROCESSED_DIR`(처리 데이터 경로)와 `VOGUE_HM_MAPPING_VERSION`(분류 매핑 버전)입니다. 기존 `VOGUE_HM_ANALYSIS_PATH`는 historical prototype 경로 설정입니다. 이 패키지는 `.env`를 자동 로드하지 않으며, 필요한 값을 셸 또는 실행환경에서 설정해야 합니다. `.env`는 Git 제외 상태를 유지하십시오.

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

상위 workflow는 Project_01/02 입력을 받은 뒤 Project_07 조사를 완료하고, 상태와 Evidence를 포함한 결과를 Project_06 기획안 에이전트로 전달합니다. 이 저장소는 상위 orchestrator나 transport를 선택하지 않습니다.

## 미결정 사항

- 승인된 시장 조사 자료원, 라이선스·접근 권한, 조사 지표와 시간 범위
- 다섯 저장소에 공통으로 반영할 최종 `MarketResearchResult` 스키마·버전
- API/메시지 전달 방식, 인증, 저장·보존, retry/timeout과 배포 형태
- 시장 조사 품질 평가용 승인 데이터와 팀이 정할 합격 기준

승인된 source adapter가 연결되기 전에는 판매량이나 시장 수치를 반환하지 않으며, 재고 스냅샷을 수요나 판매량의 대리 지표로 사용하지 않습니다. 출처·구현 경계의 검토 기록은 [`docs/design/notion-review.md`](docs/design/notion-review.md), 계층 구조와 의존 방향은 [`docs/design/project-structure.md`](docs/design/project-structure.md)에 있습니다.

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
├── docs/reference/                 # 이관된 historical sales pipeline/fixture 설명
├── src/market_research_agent/
│   ├── domain/                     # 도메인 계약과 모델
│   ├── sources/                    # 출처 인터페이스와 어댑터
│   └── workflows/                  # 검증과 결과 조립
└── tests/                          # 자동화 테스트
```

구조는 project_06의 `domain` / `sources` / `workflows` 분리를 따릅니다. 운영 자료원이 정해지지 않아 사내 DB/API connector, 로컬 UI, 서비스 서버는 추가하지 않았습니다. H&M 과거자료 adapter와 합성 예시는 별도 historical/test 용도로 분리되어 있습니다.
