# VOGUE Sales Market Review Agent

공통 저장소 레이아웃과 HTTP 책임 경계는 [`docs/repository-boundaries.md`](docs/repository-boundaries.md)를 참고하세요.

Project_07은 P06 review bridge가 보낸 최소 상품 프로필을 바탕으로 시장 검토를 수행하고 `SalesMarketReview`를 반환합니다. P07은 P06의 `PlanningDraft` 전체를 받지 않습니다. 초안 생성과 MD의 최종 판단은 각각 P06 및 오케스트레이터/UI 책임입니다.

실시간 사내 판매 원장과 실제 원단 원가 DB는 연결되어 있지 않습니다. H&M Kaggle 과거 자료는 비상업 연구 전용으로 분리되며 실제 시장 실적으로 제공하지 않습니다.

H&M 자료 adapter는 사후 검토에서 비상업적 historical reference 용도로만 사용할 수 있습니다. 이 저장소는 초안을 만들거나 변경하지 않습니다.

## 현재 동작

- 유일한 분석 HTTP 경로는 `POST /v1/agents/sales-market-agent/reviews`입니다. P06 bridge가 전달하는 최소 상품 payload와 `request_id`를 검증합니다.
- 응답은 Evidence, 누락 자료, 제한사항, 업계 마진 benchmark 기반 가격 구간 분석, 영역별 근거 충족 상태를 담은 `SalesMarketReview` proposal입니다.
- 최종 채택·수정·보류 결정은 항상 MD가 합니다.
- 과거 H&M 자료는 비상업 연구 데이터로, 원단 목록 가격은 공급자 검증 원가가 아닌 참고치로 표시합니다. 결측은 0으로 바꾸지 않습니다.
- 레거시 `/runs`, `/structured-runs`, 조사 CLI 및 HTTP 합성 시뮬레이션 경로는 제거했습니다.

## 빠른 실행

Python 3.11 이상, 외부 패키지 없이 실행할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

R2 source와 타겟 LLM 기능은 선택 의존성과 환경 설정이 필요합니다.

H&M Kaggle customer/price aggregates run only when `VOGUE_HM_RESEARCH_NONCOMMERCIAL=true` and remain non-commercial historical references. Fabric listing observations are not verified production costs. Detailed limitations are documented in [`docs/design/post-planning-sales-market-review.md`](docs/design/post-planning-sales-market-review.md).

## 사후 검토 HTTP 서비스

로컬 서버는 loopback에만 바인딩하며 review endpoint에서 `VOGUE_AGENT_SERVICE_BEARER_TOKEN` Bearer 인증을 요구합니다.

```bash
PYTHONPATH=src python3 -m market_research_agent.http_server --host 127.0.0.1 --port 8766
```

상품 리뷰 proposal은 `POST /v1/agents/sales-market-agent/reviews`입니다. 최소 요청 예시는 [`sales-market-review-0.2-proposal.request.json`](contracts/examples/sales-market-review-0.2-proposal.request.json), schema는 [`sales-market-review-0.2-proposal.schema.json`](contracts/schemas/proposals/sales-market-review-0.2-proposal.schema.json)입니다. 이 경로는 `vogue-sales-market-review/0.2-proposal` envelope와 Bearer 인증을 사용합니다. 요청에는 필수 `category`와 선택 `product_type`, `gender`, `season`, `material`만 포함됩니다. P06 bridge가 P06 draft에서 이 필드만 추려 전달하며 P07은 draft 전체를 받지 않습니다. R2 H&M Kaggle의 과거 거래가격과 연령 집계는 비상업 연구 설정에서만 제공합니다. 호출 주체는 P06 review bridge이며 응답은 동기 방식입니다.

P07 `/reviews` 입력은 상품 원가 시트, 제안가격/통화, 이미지 참조, P06 evidence 메타데이터를 받지 않습니다. 원가·가격 관련 분석은 연결 자료와 P07 내부 분석 기준으로 수행되며, P06 bridge는 데이터 전달과 P06 전용 검증을 담당합니다. P07은 그 bridge 책임을 중복하지 않습니다.

비상업 H&M 연구 모드에서 고객 자료는 개인정보 행을 노출하지 않고 카테고리별 연령대로 집계합니다. 타겟층 가설은 별도 LLM 전송 설정이 켜진 경우에만 생성합니다. 자세한 내용은 [customer age and target analysis 설계](docs/design/customer-age-target-analysis.md)를 참고하세요.

상품 리뷰 응답에는 H&M 가격 통계와 원단 공개 listing 참고값을 영역별로 반환합니다. H&M의 정규화 평균에 ×590을 적용해 USD 참고값으로 해석한 뒤, Frankfurter USD/KRW 기준환율과 Python 환산 함수로 원화 환산 참고값을 만듭니다. Kaggle이 ×590 USD 해석이나 통화를 공식 보증하지 않으므로 응답에 가정과 환율 기준일을 기록합니다. 상품 종류는 versioned mapping으로 H&M의 `product_type_name`이 속한 category에 매핑해 집계하며, 해당 자료에는 시즌 필드가 없어 시즌은 필터링되지 않습니다. 제안가와 원가 시트는 API 입력에 포함되지 않습니다. H&M P25–P75 구간은 업계 총마진 benchmark를 적용한 scenario로 `margin_price_range_analysis`에 반환하며, 상품별 실제 마진은 아닙니다. 텍스트 LLM 타겟 가설은 상품 정보의 외부 전송을 명시적으로 허용한 경우에만 `VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=true`로 활성화됩니다. 연령대 근거는 H&M 비상업 연구 모드가 켜져 있고 집계 자료가 제공될 때만 포함됩니다.

## Docker

Docker Desktop 또는 Docker Engine과 Compose를 설치한 뒤 프로젝트 루트에서 개발 컨테이너를 시작합니다.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f sales-market-review-agent
```

기본 컨테이너는 Docker Desktop에서 실행 상태를 유지하는 개발용 대기 프로세스입니다.

테스트는 별도 일회 실행 컨테이너로 실행합니다.

```bash
docker compose --profile test run --build --rm sales-market-review-agent-tests
```

Docker 이미지에는 `.env`, `.venv`, 원본·처리 데이터가 포함되지 않습니다. 중지할 때는 `docker compose down`을 실행합니다.

## 사용 예시

Review 입력 예시는 [sales-market-review 0.2 proposal request](contracts/examples/sales-market-review-0.2-proposal.request.json)를 참고하세요. 최소 입력은 `{"category":"Outerwear"}`이며, 선택 필드는 생략하거나 `null`로 보낼 수 있습니다. API는 초안을 수정하지 않고 검토 결과만 반환합니다.

## 남은 통합 항목

- `PlanningDraft`와 `SalesMarketReview` 공통 schema/version 승인
- P06 review bridge의 `/reviews` 호출 연결과 timeout/error 전달 정책
- 상업 이용 가능한 판매·원단 원가 자료원의 owner·권한·단위 확인

실제 자료를 받기 전 합성 fixture로 검색·분석 RAG를 개발하는 제안은 [`docs/design/synthetic-fixture-rag-analysis-design.md`](docs/design/synthetic-fixture-rag-analysis-design.md)에 정리되어 있습니다. 이 설계는 합성 결과를 실제 시장 근거로 취급하지 않습니다.

판매와 시장을 각각 어떤 지표/정형 계산으로 분석하고 RAG를 어디에 제한해서 쓸지에 대한 전문가 설계는 [`docs/design/sales-market-analysis-expert-design.md`](docs/design/sales-market-analysis-expert-design.md)를 참고하세요. 해당 내용은 구현/자료원 승인을 대신하지 않는 설계 제안입니다.

Notion 합의에서 구현 순서와 미결정 사항까지 연결한 전체 방향은 [`docs/design/implementation-direction.md`](docs/design/implementation-direction.md)에 정리되어 있습니다.

## 폴더 구조

```text
repository-root/
├── contracts/schemas/             # Active P07 review request/response/error schemas
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

활성 서버는 loopback 개발용이며 `POST /v1/agents/sales-market-agent/reviews`만 처리합니다. 시장 연구용 `/runs`와 합성 HTTP 시뮬레이션 경로는 제거했습니다.
