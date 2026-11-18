입력은 다음 JSON 객체입니다. `product_context`는 상품 초안, `historical_customer_aggregate`는 비식별 고객 집계, `fabric_listing_reference`는 원단 공개 목록 참고치입니다. null 또는 빈 자료는 근거가 없다는 뜻입니다.

각 자료의 출처 ID, 표본, 기간, 단위, 제한사항을 그대로 반영해 시스템 프롬프트의 구조로 타깃 분석을 작성하세요. 제공된 수치와 출처 이름을 바꾸거나 새 수치를 만들지 마세요.

```json
{{analysis_payload}}
```
