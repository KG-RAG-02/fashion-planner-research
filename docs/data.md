# 데이터

이 레포의 MIT 라이선스는 **코드에만** 적용됩니다. 아래 데이터셋에 대한 권리는 주지 않습니다. 레포에는 실제 데이터가 없고, 테스트 픽스처는 모두 테스트 안에서 만드는 합성 데이터입니다.

데이터 없이도 서비스는 실행됩니다. 아무것도 설정하지 않으면 리뷰는 `no_results`를 돌려주고, 자료원마다 빠진 이유를 `result.missing_data`에 적습니다.

## 자료원 한눈에 보기

| 자료원 | 쓰는 곳 | 이용 조건 | 이 레포에 있는가 | 얻는 방법 |
|---|---|---|---|---|
| H&M Personalized Fashion Recommendations (Kaggle 대회) | 과거 판매가 통계, 고객 연령대 | Kaggle 대회 규칙: 비상업 용도만 허용, 재배포 금지 | 없음. 원본 파일과 행 단위 파생 파일은 커밋하지 않습니다 | Kaggle에서 내려받아 두 파일을 Parquet으로 바꾸고 자기 버킷에 올립니다 |
| 원단 판매 목록 CSV | 원단 가격 참고값 | 직접 준비합니다. 가격을 가져온 사이트의 이용 조건을 따릅니다 | 없음 | 아래 스키마로 만듭니다 |
| 집계 스냅샷 | 설정했을 때 요청 처리용 자료원 | 위 두 자료에서 만든 파생물이며 H&M 조건을 그대로 따릅니다. 비공개로 둡니다 | 없음 | 스냅샷 빌더로 만듭니다 |
| USD/KRW 기준 환율 | 과거 가격 참고값의 원화 환산 | [Frankfurter](https://frankfurter.dev) 공개 API를 요청 때 조회합니다 | `runtime_settings.py`의 날짜가 적힌 대체 환율 | 할 일 없음 |
| 의류 업종 매출총이익률 | 마진 시나리오 | 공개 수치: NYU Stern(Damodaran), *Margins by Sector (US)*, 2026년 1월, 의류 56.88 % | `domain/apparel_costing.py`의 상수 | 할 일 없음 |

## H&M Kaggle 데이터셋

- 데이터: <https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/data>
- 규칙: <https://www.kaggle.com/competitions/h-and-m-personalized-fashion-recommendations/rules>. 내려받기 전에 읽으세요. 비상업 연구에만 쓰고, 공개 버킷이나 공개 스냅샷을 통한 배포를 포함해 재배포하지 마세요.
- 서비스는 비상업 H&M 연구를 켰을 때만(`RESEARCH_HM_NONCOMMERCIAL_RESEARCH=true`) H&M 자료를 읽습니다. [설정](../README.md#설정)을 보세요.

### 파일과 열

| 객체 | 형식 | 서비스가 읽는 열 |
|---|---|---|
| `customers.csv` | 머리글이 있는 CSV. Kaggle 원본 열 `customer_id, FN, Active, club_member_status, fashion_news_frequency, age, postal_code`를 그대로 둡니다 | `customer_id`, `age` |
| `articles.parquet` | `articles.csv`를 바꾼 Parquet | `article_id`, `product_type_name`, `product_group_name`, `index_group_name` |
| `transactions_train.parquet` | `transactions_train.csv`를 바꾼 Parquet | `t_dat`, `customer_id`, `article_id`, `price`, `sales_channel_id` |

Kaggle은 `articles.csv`와 `transactions_train.csv`를 CSV로 제공합니다. `r2` extra가 설치하는 DuckDB로 한 번 바꿉니다. 앞자리 0이 맞도록 두 파일 모두 `article_id`를 문자열로 둡니다.

```python
import duckdb

duckdb.sql("""COPY (SELECT * FROM read_csv('articles.csv', types={'article_id': 'VARCHAR'}))
              TO 'articles.parquet' (FORMAT parquet)""")
duckdb.sql("""COPY (SELECT * FROM read_csv('transactions_train.csv',
                    types={'article_id': 'VARCHAR', 'customer_id': 'VARCHAR'}))
              TO 'transactions_train.parquet' (FORMAT parquet)""")
```

세 객체를 **비공개** 버킷에 올립니다. 객체 키는 `src/fashion_planner_research/resources/configs/r2-source-manifest.json`에서 정합니다. 키가 다르면 자기 매니페스트 파일을 만들고 `RESEARCH_R2_SOURCE_MANIFEST`로 가리킵니다.

### 해석 방법

- **현재가 아니라 과거입니다.** 거래는 2018–2020년 자료입니다. 그때의 H&M 구매자를 보여 줄 뿐, 여러분의 고객이나 지금의 수요가 아닙니다.
- **가격 배율은 가정입니다.** Kaggle은 정규화한 `price`를 공개합니다. 서비스는 여기에 590을 곱해 USD로 표시합니다. 이 배율은 연구 가정이며 데이터셋의 통화 정보가 아닙니다.
- **성별은 상품 세그먼트입니다.** `women`/`men` 필터는 구매한 상품의 `index_group_name`(`Ladieswear`/`Menswear`)을 씁니다. 고객 표에는 성별이 없습니다.
- **시즌이 없습니다.** 데이터셋에 시즌 필드가 없어서 `season`으로 거르지 않습니다.
- **카테고리 매핑.** `hm-category-map.json`은 H&M `product_type_name`을 먼저, `product_group_name`을 다음으로 리뷰 카테고리에 매핑하고, `스커트` 같은 한국어 카테고리 이름도 매핑합니다(`requested_category_aliases`). 표에 없는 값은 매핑하지 않습니다.
- **개인정보.** 쿼리 밖으로 나가는 것은 집계뿐입니다. 연령대별 고유 고객 수와 가격 통계만 나갑니다. 한 프로필의 연령대 중 하나라도 10명 미만이면 그 프로필의 연령대 수치 전부, 분모, 타깃 연령대 수치를 숨깁니다. 고객 ID와 행은 응답에도 스냅샷에도 들어가지 않습니다.

### 집계 예시

원래 프로젝트가 로컬 연구 실행으로 얻은 집계입니다. 카테고리 `Outerwear`, 상품 세그먼트 `Ladieswear`, 거래 기간 2018-09-20~2020-09-22입니다. H&M 과거 연구 통계이며 어떤 브랜드의 고객 프로필이나 현재 수요 추정이 아닙니다.

| 구분 | 고유 고객 수 |
|---|---:|
| 전체 | 323,345 |
| 나이를 아는 고객 | 321,550 |
| 20세 미만 | 7,314 |
| 20–29세 | 134,560 |
| 30–39세 | 57,499 |
| 40–49세 | 44,869 |
| 50세 이상 | 77,308 |
| 나이 모름 | 1,795 |

미리 정한 세 연령대(16–19, 20–39, 40 이상) 중 가장 큰 20–39세가 타깃 연령대로 뽑혔습니다. 192,059명으로, 나이를 아는 고객의 59.7291 %입니다.

## 원단 판매 목록 CSV(직접 준비)

원단 참고값은 공개 원단 판매 목록으로 직접 만드는 CSV입니다. 원래 프로젝트의 CSV는 한 판매처에서 수집한 것이라 **배포하지 않습니다**. 서비스는 매니페스트 키 `fabric_prices`의 객체를 읽습니다.

요구 사항:

- UTF-8(BOM 허용), 최대 1,000,000바이트
- 아래 24개 열 이름이 모두 있어야 합니다. 표에 따로 적지 않은 열은 값이 비어도 됩니다.
- 선택: 파일의 SHA-256을 객체 메타데이터 `sha256`에 넣으면, 바이트가 맞지 않는 파일을 서비스가 거부합니다.

| 열 | 필수 값 | 뜻 |
|---|---|---|
| `record_id` | 예 | 판매 목록의 고유 ID. 없으면 그 행을 건너뜁니다 |
| `product_name` | | 판매 제목. 요청 소재를 여기서 찾습니다 |
| `category` | | 원단 분류. 결과를 이 값으로 묶고, 소재를 여기서도 찾습니다 |
| `composition` | | 혼용률. 예: `면 100%`. 소재를 여기서도 찾습니다 |
| `construction_or_finish` | | 조직이나 가공. 소재를 여기서도 찾습니다 |
| `weight_g_per_cut` | | 판매 단위당 무게 |
| `width_cm` | | 폭 |
| `color` | | 색 |
| `original_unit` | | 판매 단위 원문. 예: `1 yd`, `90 cm`, `1마`. 원화 미터당 단가 추정에 씁니다 |
| `price_amount` | 예, 0보다 큼 | 판매가. 양수가 아니면 그 행을 건너뜁니다 |
| `currency` | | 통화 코드. 미터당 추정은 `KRW`일 때만 합니다 |
| `supplier` | | 판매처 |
| `shipping_terms` | | 배송 조건 원문 |
| `source_url` | 예 | 판매 페이지 URL. 없으면 그 행을 건너뜁니다 |
| `observed_at` | | 가격을 본 날짜(`YYYY-MM-DD`) |
| `price_history_status` | | 예: `single_observation` |
| `source_page_type` | | 예: `retailer_product_listing` |
| `estimated_sales_unit` | | `original_unit`이 불분명할 때 읽어 낸 판매 단위 |
| `estimated_length_m` | | 판매 길이(m). 단위 문자열보다 먼저 씁니다(0 < 값 ≤ 1000) |
| `unit_price_estimation_status` | | `observed`나 `verified`면 `unit_price_krw_per_meter`를 관측값으로, 그 밖에는 추정값으로 봅니다 |
| `unit_price_estimation_method` | | 미터당 값을 얻은 방법 |
| `unit_price_estimation_confidence` | | 예: `high`, `low` |
| `unit_price_estimation_evidence_urls` | | 미터당 값의 출처 |
| `unit_price_krw_per_meter` | | 원화 미터당 단가가 있으면 넣습니다 |

소재 찾기는 요청 소재(내장 한영 별칭 포함, 예: `wool` ↔ `울`)를 정규화한 뒤 `product_name`, `category`, `composition`, `construction_or_finish`에서 부분 문자열로 찾는 방식입니다. 판매가와 미터당 값은 공개 판매가 참고값이며, 응답은 이를 검증된 조달 원가로 표시하지 않습니다.

합성 예시 한 행:

```csv
record_id,product_name,category,composition,construction_or_finish,weight_g_per_cut,width_cm,color,original_unit,price_amount,currency,supplier,shipping_terms,source_url,observed_at,price_history_status,source_page_type,estimated_sales_unit,estimated_length_m,unit_price_estimation_status,unit_price_estimation_method,unit_price_estimation_confidence,unit_price_estimation_evidence_urls,unit_price_krw_per_meter
example-wool-001,예시 울 혼방 코트지,울 혼방 직물,울 60% 폴리에스테르 40%,능직,,150,차콜,1 yd,12000,KRW,예시 원단,,https://example.com/listing/1,2026-01-15,single_observation,retailer_product_listing,,,,,,
```

## 집계 스냅샷

요청마다 H&M 원본 객체를 훑으면 느리고 메모리를 많이 씁니다. 대신 오프라인에서 JSON 스냅샷 하나를 만들고 서비스가 그것을 가리키게 합니다.

```sh
export RESEARCH_HM_NONCOMMERCIAL_RESEARCH=true          # R2_* 자격 증명도 필요
python -m fashion_planner_research.sources.sales.hm_kaggle.hm_research_aggregate            # 시험 실행
RESEARCH_AGGREGATE_UPLOAD_ENABLED=true \
python -m fashion_planner_research.sources.sales.hm_kaggle.hm_research_aggregate --upload   # 올리기
```

빌더가 하는 일:

1. H&M 객체 세 개의 크기와 버전을 확인하고, 원단 CSV(최대 1 MB)를 읽어 SHA-256과 열 구성을 검사합니다.
2. DuckDB `httpfs`로 H&M 객체에서 연령대 쿼리와 가격 쿼리를 연결을 나눠 실행합니다.
3. 실행하는 동안 자료원 객체가 바뀌지 않았는지 다시 확인합니다.
4. 내용의 SHA-256을 이름으로 한 JSON 객체 하나를 `derived/research-aggregate-snapshots/` 아래에 씁니다(`RESEARCH_AGGREGATE_SNAPSHOT_PREFIX`로 prefix를 바꿀 수 있습니다). 그 키에 다른 객체가 있으면 덮어쓰지 않습니다.

출력된 키를 `RESEARCH_AGGREGATE_SNAPSHOT_KEY`에 넣고 서비스를 다시 시작합니다. 스냅샷에는 형식(`schema: research-aggregate-snapshot`, `schema_version: 1.0.0`), 원본 객체 버전, 카테고리 매핑의 SHA-256(`mapping_sha256`), 집계 SQL의 SHA-256(`aggregation_sql_sha256`), DuckDB 버전이 들어 있습니다. `hm-category-map.json`을 바꾸면 서비스가 옛 스냅샷을 거부하므로 새로 만들어야 합니다. 빌드용 자격 증명에는 자료원 읽기 권한과 파생 prefix 쓰기 권한만 주세요. 서비스 자체는 읽기만 합니다.

단계별 그림은 [snapshot-build](diagrams/snapshot-build.svg)에 있습니다.

## 한계

- **원래 프로젝트의 비공개 스냅샷은 읽을 수 없습니다.** 공개판에서 스냅샷 형식(`research-aggregate-snapshot` 1.0.0)과 카테고리 매핑의 식별(`hm-product-taxonomy`, `mapping_sha256`)이 바뀌었기 때문입니다. 의도한 비호환이며, 실데이터를 쓰려면 위 절차로 새로 만들어야 합니다.
- 위 집계 예시는 원래 프로젝트의 실행 결과입니다. 같은 수치를 얻으려면 같은 Kaggle 데이터와 같은 카테고리 매핑이 필요합니다.
- H&M 자료는 한 소매업체의 과거 거래입니다. 가격 배율(×590 USD)은 가정이고, 성별은 상품 세그먼트이며, 시즌 정보는 없습니다.
- 원단 판매 목록은 공개 판매가 참고값입니다. 미터당 값은 판매 단위에서 추정한 값일 수 있고, 조달 원가가 아닙니다.
- 환율 API에 닿지 않으면 날짜가 적힌 대체 환율을 씁니다. 응답의 `usd_krw_conversion.fallback_used`로 알 수 있습니다.

## Git에 넣지 않는 것

H&M 원본과 변환 파일, 스냅샷, 원단 CSV, `.env` 파일, R2 키. `.gitignore`가 `data/` 작업 폴더와 `.env*`(`.env.example` 제외)를 막습니다.
