# 변경 기록

이 파일은 [Keep a Changelog 1.1](https://keepachangelog.com/ko/1.1.0/) 형식을 따르고, 버전은 [유의적 버전](https://semver.org/lang/ko/)을 따릅니다.

## [1.0.0] - 2026-11-18

공개 첫 판입니다. 팀 내부에서 쓰던 판매·시장 검토 서비스를 정리해 공개했습니다.

### 추가

- 네트워크·저장소·LLM 없이 결정적인 리뷰를 내는 합성 모드(`RESEARCH_REVIEW_SOURCE=synthetic`). 결과에는 `synthetic_test_data`를 표시합니다.
- 타깃 가설 응답에 사용한 시스템 프롬프트의 `prompt_sha256`
- 스냅샷의 `aggregation_sql_sha256`, `duckdb_version`과 스냅샷 prefix를 바꾸는 `RESEARCH_AGGREGATE_SNAPSHOT_PREFIX`
- 문서: 아키텍처, 데이터(원단 CSV 스키마, H&M 변환 방법), 문제 해결 16건, 설계 결정 6건, 다이어그램 2종과 근거 표
- 공개 표준 파일(LICENSE, AUTHORS, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY), 이슈·PR 템플릿, 오프라인 단위 테스트 CI

### 바뀜

- 패키지·배포·실행 이름을 `fashion-planner-research`(import `fashion_planner_research`)로 통일했습니다.
- 계약 이름은 `research-handoff`, 경로는 `POST /v1/agents/research/reviews`, `agent_id`는 `research`, `schema_version`은 `1.0.0`입니다.
- 환경변수는 서비스 전용이면 `RESEARCH_`, 공용 토큰은 `FASHION_PLANNER_SERVICE_TOKEN`입니다.
- H&M 연구(`RESEARCH_HM_NONCOMMERCIAL_RESEARCH`)와 LLM 전송(`RESEARCH_TARGET_LLM_TRANSFER_AUTHORIZED`)은 기본으로 꺼져 있고, 명시적으로 켜야 합니다.
- 카테고리 매핑과 스냅샷 형식은 이름에서 버전을 빼고 내용 해시(`mapping_sha256`)로 식별합니다. 이전 형식의 스냅샷은 읽지 않습니다.
- 설정·프롬프트는 패키지 `resources/` 한 곳에만 둡니다.
- 실행 이미지는 패키지만 담고, 테스트는 별도 빌드 단계에서 돌립니다.
- `jsonschema`는 테스트 의존성(`test` extra)으로 옮겼습니다. 필수 런타임 의존성은 없습니다.
- 공개 전 히스토리 정리 때 커밋 메시지의 옛 이름(VOGUE, P06~P08, 옛 레포·패키지 이름)을 현재 이름이나 중립 표현으로 바꿨습니다. 과거 커밋의 코드와 메시지 속 이름이 다를 수 있습니다.

### 제거

- 리뷰 경로에서 쓰지 않던 옛 모듈(CLI, 합성 RAG, 로컬 CSV 자료원, H&M 전처리 파이프라인 등)과 그 테스트·스크립트
- 수집한 원단 판매 목록 CSV와 팀 내부 설계 문서
- 비공개 버킷 이름과 객체 키
