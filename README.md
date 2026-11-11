# VOGUE Market Research Agent

공통 저장소 레이아웃과 HTTP 책임 경계는 [`docs/repository-boundaries.md`](docs/repository-boundaries.md)를 참고하세요.

VOGUE의 시장성 조사 단계에서 사용할 수 있는 계약 중립적 Python 도메인 코어입니다. 현재 구현은 승인된 시장 자료원이 정해지지 않은 상태를 반영해 외부 API, 크롤러, 모델, 그래프 저장소를 포함하지 않습니다.

현재 저장소에는 승인된 실제 판매 원장·시장 자료 어댑터가 없어 운영 분석 시스템으로 동작하지 않습니다. 판매 관측 모델과 결과 정규화 경계는 Project_07 책임으로 구현하되, 자료원이 미구성된 경우 `no_results`와 제한 사유를 전달합니다. Kaggle/H&M 공개 과거 데이터와 합성 fixture는 운영 데이터로 사용하지 않습니다.

Project_06의 책임 정리 과정에서 H&M 과거자료 처리·품질·집계 adapter가 이 저장소로 옮겨졌습니다. 2026-11-09에는 H&M Kaggle 파일도 Git 제외 로컬 데이터 경로로 이동했습니다. 이 이동은 이용 권한을 승인하지 않습니다. `.env`와 SQLite 데이터는 옮기지 않았습니다. 판매와 시장 분석 코드는 각각 `sources/sales/`와 `sources/market/`에 둡니다. 상품기획 초안/시나리오 생성 데모 코드는 Project_06의 `_incoming/project_07-product-planning/`으로 반환했습니다. H&M adapter는 historical research용이며 승인된 사내 sales connector가 아닙니다. 테스트는 임시 합성 fixture를 사용하며 실제 Kaggle 원본 CSV를 요구하지 않습니다.

## 현재 동작

- 이 저장소 안에서만 쓰는 `MarketResearchResult` 로컬 응답 envelope와 Evidence 참조 모델을 제공합니다. 타 저장소와 공통 계약으로 합의된 상태는 아닙니다.
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
- `vogue-orchestration-request/0.1-proposal` 입력 어댑터는 통합 오케스트레이터가 해석한 구조화 요청을 검증합니다. 이 경로는 raw query를 다시 파싱하지 않고, Trend/Inventory 결과 payload가 섞여 들어오면 거부합니다. 새 입력 계약은 아직 공통 합의 전이며 stable result 1.0은 변경하지 않습니다.
- `PlanningRequest`, `TrendResult`, `InventoryProductResult`를 받아 조사하되, Project_06 상품 기획 결과는 입력으로 받지 않습니다. VOGUE 상위 흐름은 Project_07 결과가 Project_06보다 먼저 전달되는 순차 처리입니다.
- `CompositeMarketResearchSource`는 호출자가 제공한 source adapter들의 결과를 결합하고, 일부 source 실패 시 가용 결과와 오류를 함께 보존합니다. 현재 승인된 source adapter는 연결되어 있지 않습니다.
- H&M historical adapter는 카테고리 매핑 후 `purchase_rows`를 주 단위 관측으로 보존합니다. 이 값은 역사적 거래 행 수이며 현재 판매나 수요를 뜻하지 않습니다.
- `ObservationComparabilityValidator` / `compare_sales_to_market`는 판매·시장 관측의 지역, 채널, 기간, 카테고리, 단위, 정의, 매핑 및 권한을 확인합니다. 이는 비교 가능성만 판정하고 비율·시장점유율·마진을 계산하지 않습니다. 미확인 범위, 권한 미승인, H&M `purchase_rows`는 비교를 차단합니다.
- 숫자 분석은 구조화된 승인 관측값에 한해 결정론적으로 수행합니다. 시장 시계열 변화 계산은 같은 지표·측정 유형·단위·지역·채널·분류·모집단·매핑·주기·정의이고 기간이 순차일 때만 가능합니다. 계산에는 입력 Observation/Evidence ID와 공식 버전을 기록합니다. 미승인 산식의 원가마진은 계산하지 않습니다.
- `MarketEvidenceRetriever`는 주입 가능한 경로로 MarketResearchAgent에 연결되어 원문 query와 구조화 scope를 전달하고 승인된 결과만 Evidence로 보존합니다. 기본 실행에는 retriever, 벡터 저장소, 임베딩·생성 모델이 설정되어 있지 않습니다. 검색 근거를 바탕으로 서술형 시장 주장을 생성하는 기능도 구현하지 않았습니다.
- 판매-시장 비교 가능성은 카테고리 내 observation pair별로 차원별 일치/불일치와 `not_comparable`을 기록하며 산술·점유율을 계산하지 않습니다. 비교 행렬은 최대 5,000쌍이며 초과 시 제한을 기록합니다.

이 패키지는 요청·트렌드·재고 입력을 opaque dictionary로 source adapter에 그대로 전달합니다. trend/product-inventory 결과에는 현재 요청과 일치하는 `request_id`와 지원 상태가 있어야 하며, 불일치·누락·잘못된 형식은 source 호출 전에 거부합니다. producer `schema_version`은 이 저장소의 로컬 결과 버전과 비교하지 않습니다. 입력 원문 review snapshot은 명시적 1.2/1.3 proposal에만 포함되고 1.0 serializer에서는 제외됩니다. 실제 공통 입력 스키마가 다른 저장소와 합의되면 별도 계약 어댑터를 추가해야 합니다. `contracts/schemas/market-research-result.schema.json`은 현재 로컬 1.0 구현을 설명하며, `contracts/schemas/proposals/`의 1.1·1.2·1.3은 새 판매·시장 경계를 검토하기 위한 제안입니다. 어느 파일도 다른 에이전트 저장소와 합의된 wire contract를 뜻하지 않습니다. 상세 동작은 [`docs/design/upstream-context-preservation.md`](docs/design/upstream-context-preservation.md)를 참고하세요.

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

CLI는 `PlanningRequest`, `TrendResult`, `InventoryProductResult` 입력 JSON을 검증합니다. 현재 허용한 운영 자료는 권한이 확인된 H&M Kaggle 과거 판매 adapter뿐이며 합성 fixture는 demo/test 전용입니다. 외부 시장 자료원은 이 작업 경로에 연결하지 않습니다.

```bash
PYTHONPATH=src python3 -m market_research_agent.cli \\
  --inputs-json examples/fixtures/market-research-input.example.json \\
  --metrics "purchase_rows" \\
  --hm-processed-dir data/processed/hm-personalized-fashion-recommendations \\
  --hm-category-map "Garment Upper body=Outerwear" \\
  --hm-mapping-version "vogue-taxonomy-2026-11-01" \\
  --proposal-v1-2
```

입력 예시 파일은 이 저장소의 `examples/fixtures/market-research-input.example.json`을 참고하세요. 모든 상위 결과의 `request_id`가 일치해야 합니다. H&M 어댑터는 권한 확인, 명시적 category mapping, mapping version이 없으면 데이터를 읽지 않습니다. `--hm-usage-authorized`는 승인 절차가 아니며 외부에서 확인한 사용 권한을 호출자가 선언하는 플래그입니다. CLI 기본 요청에 source가 연결되지 않으면 `no_results`를 반환합니다. `--proposal-v1-1`, `--proposal-v1-2`, `--proposal-v1-3`은 명시적인 미합의 계약 제안입니다.

현재 작업 범위는 로컬 H&M Kaggle 과거 판매자료와 저장소의 합성 fixture로 제한됩니다. 일반 CLI/HTTP 실행은 승인 확인 및 명시적 category mapping이 있는 H&M historical adapter만 선택할 수 있으며, 합성 fixture는 별도의 demo/test 경로에서만 사용합니다. 시장 CSV adapter 코드는 남아 있지만 이 작업 범위의 CLI/HTTP source로 연결하지 않습니다. H&M 필수 설정과 합성 데이터 경계는 [`docs/design/sales-search-quality-hm-synthetic.md`](docs/design/sales-search-quality-hm-synthetic.md)를 참고하세요.

핵심 설정 환경변수는 `VOGUE_HM_PROCESSED_DIR`(처리 데이터 경로)와 `VOGUE_HM_MAPPING_VERSION`(분류 매핑 버전)입니다. 기존 `VOGUE_HM_ANALYSIS_PATH`는 historical prototype 경로 설정입니다. 이 패키지는 `.env`를 자동 로드하지 않으며, 필요한 값을 셸 또는 실행환경에서 설정해야 합니다. `.env`는 Git 제외 상태를 유지하십시오.

## 합성 RAG 평가 자료

RAG 검색의 query 전달, 메타데이터 범위 필터, Evidence citation, 권한 차단 및 검색 순위를 개발 환경에서 평가하려면 전용 합성 fixture generator를 사용합니다. 이 fixture는 실제 통계·판매·시장 근거가 아니며 사업 의사결정에 사용할 수 없습니다. 운영 retriever, source manifest 또는 기본 workflow에서 자동 선택되지 않고, 외부 임베딩·모델·저장소에 연결되지 않습니다. 테스트 전용 permission 값 `authorized_for_synthetic_testing_only`는 운영의 `authorized`와 별개입니다.

판매·시장 분석 결과 전달 형식과 차트를 살펴보려면 [합성 분석 demo](docs/design/synthetic-analysis-demo.md)를 참고하세요. `python3 scripts/synthetic_market_analysis.py --seed 20261110`은 합성 시장 시계열, 로컬 proposal JSON, SVG 차트를 생성합니다. H&M 판매값은 Kaggle 사용 권한이 확인되지 않아 읽지 않으며 결과에서 `no_results`로 표시됩니다. 따라서 이 demo는 두 데이터의 직접 비교를 `not_comparable`로 반환합니다.

보다 구체적인 시장조사 절차용 합성 시나리오는 [synthetic market scenario 안내](docs/design/synthetic-market-scenario.md)를 참고하세요. `python3 scripts/synthetic_market_scenario.py --config examples/synthetic_market_scenario/scenario-config.json`은 월별·카테고리별·판매매체별 fixture, 합성 조사 문서와 Evidence, 품질 시나리오, 분석 proposal과 시각화를 생성합니다. 모든 값은 명시된 가정으로 만든 비운영 합성값입니다.

구조화 요청으로 합성 관측 또는 설명 문서를 조회하는 별도 RAG proposal은 아래처럼 명시적 테스트 모드에서 실행합니다. 이 경로는 canonical request만 받고 HTTP·운영 retriever와 분리되어 있습니다. 응답의 수치는 합성 시나리오 안에서만 해석하세요.

```bash
python3 scripts/run_synthetic_rag_analysis.py \
  --mode synthetic_test \
  --request examples/synthetic_rag_analysis/women-knitwear-2028-03.request.json

python3 scripts/evaluate_synthetic_rag_analysis.py
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
```

새 평가셋 `tests/fixtures/synthetic_rag_analysis/v1/`은 기존 retrieval baseline과 별도입니다. 현재 7개 사례 결과는 구조화 fixture 경로에 한정되며 자유형 검색이나 실제 시장 정확도를 검증하지 않습니다. 설계와 남은 제한은 [합성 fixture RAG 설계 및 구현 상태](docs/design/synthetic-fixture-rag-analysis-design.md)를 참고하세요.

공통 v0.1에서 제외한 차트 artifact 후속 확장안은 [시각화·handoff 설계 제안](docs/design/visual-analysis-orchestrator-handoff.proposal.md)과 [합성 artifact handoff fixture](examples/fixtures/synthetic_chart_handoff/handoff.proposal.json)에 있습니다. 이는 `vogue-handoff/0.2-proposal` 검토용이며 `fixture://` 경로는 실제 전송 주소가 아닙니다.

향후 생성 모델의 판매·시장 분석 프롬프트 원본은 역할별 [`prompts/`](prompts/README.md)에서 관리합니다. 보안 원칙·평가 기준은 [프롬프트 제안 문서](docs/design/market-analysis-system-prompt.proposal.md), 합성 공격·정상 평가 사례는 `tests/fixtures/market_analysis_prompt_injection/cases.json`에 있습니다. 현재 런타임에는 모델이나 이 프롬프트가 연결되어 있지 않습니다. 검토용 렌더링은 `python3 scripts/render_market_analysis_prompt.py --prompt sales_analysis --version v1` 또는 `--prompt market_analysis --version v1`로 생성합니다.

VOGUE 공통 HTTP handoff v0.1은 [계약 및 Project_07 구현 문서](docs/design/http-json-handoff.proposal.md)에 정리되어 있습니다. Project_07에는 loopback 개발 서버가 있으며, 운영 배포는 별도입니다. `/runs`는 `VOGUE_AGENT_SERVICE_BEARER_TOKEN` Bearer 인증을 요구합니다. 승인된 source를 주입하지 않으면 `no_results`를 반환하고, runtime chart artifact는 생성하지 않습니다.

로컬 HTTP 요청/응답 흐름은 다음과 같이 실행할 수 있습니다.

```bash
PYTHONPATH=src python3 -m market_research_agent.http_server --host 127.0.0.1 --port 8766
```

Canonical endpoint는 `POST http://127.0.0.1:8766/v1/agents/sales-market-agent/runs`입니다. 이전 `/v1/agents/market-research-agent/runs` 경로는 deprecated alias로 유지됩니다. 기본 서버는 loopback에만 바인딩하고 `/runs`에서 `VOGUE_AGENT_SERVICE_BEARER_TOKEN`을 검증합니다. 외부 공개·운영 배포에는 HTTPS/TLS가 추가로 필요합니다. 정상 응답은 공통 envelope v0.1과 안정된 producer payload 1.0을 사용합니다. 차트 artifact는 별도 후속 계약입니다.

구조화된 요청만 전달하는 입력 경로는 `POST /v1/agents/sales-market-agent/structured-runs`입니다. 이 경로는 proposal schema [`vogue-orchestration-request-0.1-proposal.schema.json`](contracts/schemas/proposals/vogue-orchestration-request-0.1-proposal.schema.json)을 검증하고 기존 응답 envelope/result 1.0을 반환합니다. 입력 proposal은 타 저장소와 합의되지 않았으므로 통합 오케스트레이터 연결 전에 계약 owner 검토가 필요합니다. 안정된 `/runs` 경로의 입력 계약은 바뀌지 않았습니다.

다른 터미널에서 합성 입력 fixture를 보낼 수 있습니다.

```bash
curl -sS -X POST http://127.0.0.1:8766/v1/agents/sales-market-agent/runs \
  -H 'Content-Type: application/json' \
  --data-binary @examples/fixtures/http_json_handoff/project-07-run.request.synthetic.json
```

기본 실행은 승인된 자료원을 주입하지 않으므로 응답 상태는 `no_results`이며, 숫자 분석 결과를 만들지 않습니다. HTTP `result.schema_version`은 `1.0`입니다. 로컬 분석 확장 serializer 1.1–1.3은 계속 사용할 수 있지만 공통 HTTP wire result로 채택되지는 않았습니다.

생성된 fixture로 제한된 질의 분석 응답을 확인하려면 `--synthetic-demo`를 명시해 실행합니다. 이 모드는 실제 source와 H&M Kaggle을 사용하지 않으며, findings와 limitations에 합성 테스트임을 표시합니다. 결과는 `partial`이고 합성 수치는 사업 근거가 아닙니다. 안정된 HTTP 1.0에는 구조화된 sales/market 블록과 chart artifact가 없으므로 이 모드는 명시적으로 표시된 proposal finding만 반환합니다.

```bash
PYTHONPATH=src python3 -m market_research_agent.http_server --host 127.0.0.1 --port 8766 --synthetic-demo
```

시연 데이터 차원, 요청 지원 범위, 제한은 [합성 질의 커버리지 문서](docs/design/synthetic-query-coverage.md)를 참고하세요.

```bash
curl -sS -X POST http://127.0.0.1:8766/v1/agents/sales-market-agent/runs \
  -H 'Content-Type: application/json' \
  --data-binary @examples/fixtures/http_json_handoff/project-07-synthetic-demo.request.json
```

실제 자료원 기반 판매·시장 분석 에이전트로 발전시키기 위한 현재 갭, 자료원 후보, 권한·데이터 계약, 처리 구조와 단계별 acceptance criteria는 [운영 분석 에이전트 설계 제안](docs/design/operational-market-analysis-agent-design.md)에 정리되어 있습니다. 실제 자료 연결이나 권한 승인은 포함하지 않습니다.

기본 seed `20261109`는 문서 120개, chunk 359개, 검색 query 22개를 만듭니다. 생성량은 20–1000개 문서 범위에서 조절합니다. 생성 파일은 `tests/fixtures/synthetic_market_rag/` 아래에만 둘 수 있습니다.

```bash
PYTHONPATH=src:. python3 -m scripts.synthetic_market_rag.generate --seed 20261109 --documents 120
PYTHONPATH=src:. python3 -m scripts.synthetic_market_rag.evaluate --input-dir tests/fixtures/synthetic_market_rag --output tests/fixtures/synthetic_market_rag/evaluation-baseline.json
python3 -m unittest tests.test_synthetic_market_rag
```

평가기는 외부 의존성이 없는 lexical baseline입니다. test-only retriever probe가 실제 agent 주입 경로의 query 전달을 확인하지만, 합성 결과를 운영 `authorized` Evidence로 바꾸지 않고 workflow에는 hit을 반환하지 않습니다. Recall@k, Precision@k, MRR, nDCG@k, citation 정확도, hard-negative 배제율, 상태 일치율, 권한 위반·합성 hit 승격 건수와 workflow query 전달률은 fixture 동작 점검용입니다. 현재 실제 production RAG provider는 구성되어 있지 않으므로 점수는 운영 retriever 품질을 나타내지 않습니다. 시나리오 설명은 [`docs/design/synthetic-market-rag-evaluation.md`](docs/design/synthetic-market-rag-evaluation.md)에 있습니다.

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
    trend_result={"request_id": "req-123", "status": "ok", "findings": []},
    inventory_product_result={"request_id": "req-123", "status": "ok", "findings": []},
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

현재 판매·시장 검색 경로의 RAG 검토와 단계별 고도화 계획은 [`docs/design/sales-market-rag-analysis-roadmap.md`](docs/design/sales-market-rag-analysis-roadmap.md)에 정리되어 있습니다.

실제 자료를 받기 전 합성 fixture로 검색·분석 RAG를 개발하는 제안은 [`docs/design/synthetic-fixture-rag-analysis-design.md`](docs/design/synthetic-fixture-rag-analysis-design.md)에 정리되어 있습니다. 이 설계는 합성 결과를 실제 시장 근거로 취급하지 않습니다.

판매와 시장을 각각 어떤 지표/정형 계산으로 분석하고 RAG를 어디에 제한해서 쓸지에 대한 전문가 설계는 [`docs/design/sales-market-analysis-expert-design.md`](docs/design/sales-market-analysis-expert-design.md)를 참고하세요. 해당 내용은 구현/자료원 승인을 대신하지 않는 설계 제안입니다.

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
├── examples/fixtures/              # MarketResearchAgent 합성 입력
├── docs/reference/                 # Sales source pipeline references
├── src/market_research_agent/
│   ├── domain/                     # 도메인 계약과 모델
│   ├── sources/
│   │   ├── sales/hm_kaggle/        # H&M historical 판매 처리와 분석
│   │   ├── market/                 # 시장 CSV와 Evidence 검색 포트
│   │   └── shared/                 # 공통 source protocol/composition/검증
│   └── workflows/                  # 검증과 결과 조립
└── tests/                          # 자동화 테스트
```

구조는 `domain` / `sources` / `workflows` 책임을 따릅니다. 운영 자료원이 정해지지 않아 사내 DB/API connector, 로컬 UI, 서비스 서버는 추가하지 않았습니다. H&M 관측치와 시장 자료는 서로 다른 adapter 경로에서 유지합니다. 새상품 기획에 해당하는 합성 상품기획 데모는 Project_06 인계 폴더에 있습니다.
