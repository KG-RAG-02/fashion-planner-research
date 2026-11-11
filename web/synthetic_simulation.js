(() => {
  "use strict";

  const form = document.getElementById("simulation-form");
  const intent = document.getElementById("intent");
  const metric = document.getElementById("metric");
  const metricField = document.getElementById("metric-field");
  const requestState = document.getElementById("request-state");
  const resultPanel = document.getElementById("result");
  const resultContent = document.getElementById("result-content");
  const apiPath = "/v1/simulations/synthetic-rag/runs";

  const metricOptions = {
    market_observation: [["scenario_market_value", "합성 시나리오 가치 (KRW_million)"]],
    sales_observation: [["scenario_units_sold", "합성 판매 유사 수량 (item_units)"]],
    definition_lookup: [],
  };

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function setMetrics() {
    const options = metricOptions[intent.value] || [];
    metric.replaceChildren();
    for (const [value, label] of options) {
      const option = el("option", label);
      option.value = value;
      metric.append(option);
    }
    metricField.hidden = options.length === 0;
    metric.required = options.length > 0;
  }

  function scopedValue(id) {
    const value = document.getElementById(id).value;
    return value ? { value, certainty: "explicit", source: "synthetic_simulation_ui" } : null;
  }

  function monthBoundary(value, isEnd) {
    const [year, month] = value.split("-").map(Number);
    const day = isEnd ? new Date(year, month, 0).getDate() : 1;
    return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }

  function buildRequest() {
    const now = new Date().toISOString();
    const periodStart = document.getElementById("period-start").value;
    const periodEnd = document.getElementById("period-end").value;
    if (!periodStart || !periodEnd || periodStart > periodEnd) {
      throw new Error("기간 시작 월과 종료 월을 확인해 주세요.");
    }
    const selectedIntent = intent.value;
    return {
      contract_version: "vogue-orchestration-request/0.1-proposal",
      request_id: `sim-${crypto.randomUUID()}`,
      created_at: now,
      locale: "ko-KR",
      timezone: "Asia/Seoul",
      raw_query: document.getElementById("raw-query").value.trim(),
      query_parser: { name: "synthetic-simulation-ui", version: "1.0", reference_time: now },
      intent: {
        primary: selectedIntent,
        requested_action: selectedIntent === "definition_lookup" ? "explain" : "analyze",
        confidence: 1,
      },
      scope: {
        category: scopedValue("category"),
        gender: scopedValue("gender"),
        season: scopedValue("season"),
        period: {
          start: monthBoundary(periodStart, false),
          end: monthBoundary(periodEnd, true),
          certainty: "resolved",
          rule_version: "simulation-ui/1.0",
          source: "synthetic_simulation_ui",
        },
        geography: scopedValue("geography"),
        channel: scopedValue("channel"),
        metrics: metricOptions[selectedIntent].length ? [metric.value] : [],
        ambiguities: [],
      },
      requested_agents: ["sales_market"],
    };
  }

  function pretty(value) {
    const details = el("details");
    details.append(el("summary", "출처·Evidence 세부정보"));
    details.append(el("pre", JSON.stringify(value, null, 2)));
    return details;
  }

  function renderScope(payload) {
    const card = el("section", undefined, "scope-card");
    card.append(el("h3", "요청 범위 해석"));
    card.append(el("p", `원문: ${payload.requested_scope.raw_query || "(없음)"}`));
    card.append(el("p", `의도: ${payload.requested_scope.intent} · 지표: ${payload.requested_scope.metrics.join(", ") || "미지정"}`));
    card.append(el("p", `명시 범위: ${JSON.stringify(payload.requested_scope.explicit)}`));
    card.append(el("p", `추론 범위: ${JSON.stringify(payload.requested_scope.inferred)}`));
    card.append(el("p", `모호성: ${payload.requested_scope.ambiguities.join(", ") || "없음"}`));
    return card;
  }

  function renderDomain(name, block, sales) {
    const card = el("section", undefined, `domain-card${sales ? " sales" : ""}`);
    card.append(el("h3", sales ? "판매 유사 시나리오" : "시장 유사 시나리오"));
    card.append(el("p", "합성 fixture · 실제 판매·시장 관측이나 실적이 아님", "simulation-note"));
    card.append(el("p", `상태: ${block.status} · 지표: ${block.metric_name || "미조회"} · 단위: ${block.unit || "없음"}`));
    if (block.status === "not_queried") {
      card.append(el("p", "이 요청에서 조회하지 않은 자료 영역입니다."));
      return card;
    }
    if (!block.observations || block.observations.length === 0) {
      card.append(el("p", block.status === "no_results" ? "범위에 맞는 관측 행이 없습니다. 이는 값 0을 의미하지 않습니다." : "반환된 관측 행이 없습니다."));
      return card;
    }
    card.append(el("h4", "차원별 월 합계 · 합성 시나리오"));
    const aggregateWrap = el("div", undefined, "table-wrap");
    const aggregateTable = el("table");
    const aggregateHead = el("thead");
    const aggregateHeader = el("tr");
    for (const title of ["기간", "유지한 범위 차원", "합계", "Observation ID", "Evidence ID"]) aggregateHeader.append(el("th", title));
    aggregateHead.append(aggregateHeader);
    aggregateTable.append(aggregateHead);
    const aggregateBody = el("tbody");
    for (const aggregate of block.monthly_aggregates || []) {
      const row = el("tr");
      row.append(el("td", aggregate.period));
      row.append(el("td", JSON.stringify(aggregate.dimensions)));
      row.append(el("td", `${aggregate.value} ${block.unit}`));
      row.append(el("td", aggregate.observation_ids.join(", ")));
      row.append(el("td", aggregate.evidence_ids.join(", ")));
      aggregateBody.append(row);
    }
    aggregateTable.append(aggregateBody);
    aggregateWrap.append(aggregateTable);
    card.append(aggregateWrap);
    card.append(el("h4", "원천 관측 행"));
    const wrapper = el("div", undefined, "table-wrap");
    const table = el("table");
    const head = el("thead");
    const headRow = el("tr");
    for (const title of ["기간", "범위", "값", "모집단", "Observation ID", "Evidence ID"]) headRow.append(el("th", title));
    head.append(headRow);
    table.append(head);
    const body = el("tbody");
    for (const observation of block.observations) {
      const row = el("tr");
      row.append(el("td", `${observation.period_start} – ${observation.period_end}`));
      const dims = [observation.geography, observation.channel, observation.gender, observation.category,
        observation.season, observation.attributes?.color].filter(Boolean).join(" · ");
      row.append(el("td", dims || "범위 미제공"));
      row.append(el("td", `${observation.value} ${observation.unit}`));
      row.append(el("td", observation.population || "미제공"));
      row.append(el("td", observation.observation_id));
      row.append(el("td", observation.evidence_ids.join(", ")));
      body.append(row);
    }
    table.append(body);
    wrapper.append(table);
    card.append(wrapper);
    card.append(pretty(block.observations.map(item => ({ observation_id: item.observation_id, source_id: item.source_id, evidence: item.evidence }))));
    card.append(pretty({ source_id: block.source_id, manifest_id: block.manifest_id, limitations: block.limitations }));
    return card;
  }

  function render(payload) {
    resultContent.replaceChildren();
    resultPanel.hidden = false;
    const statusText = payload.status === "no_results"
      ? `일치 자료 없음: ${payload.reason || "요청 범위의 관측값을 찾지 못했습니다."}`
      : payload.status === "error"
        ? `오류: ${payload.message || payload.reason || "요청을 처리하지 못했습니다."}`
        : `상태: ${payload.status} · 시뮬레이션 모드: ${payload.simulation_mode || "synthetic_simulation"}`;
    resultContent.append(el("div", statusText, `status-banner${payload.status === "error" ? " error" : ""}`));
    resultContent.append(el("p", "합성 데이터입니다. 실제 시장 수치·수요·트렌드·회사 실적을 나타내지 않습니다.", "simulation-note"));
    if (payload.requested_scope) resultContent.append(renderScope(payload));
    if (payload.analysis?.summary) resultContent.append(el("p", payload.analysis.summary));
    resultContent.append(renderDomain("market_like", payload.market_like || { status: "not_queried", observations: [] }, false));
    resultContent.append(renderDomain("sales_like", payload.sales_like || { status: "not_queried", observations: [] }, true));
    if (payload.retrieval?.document_hits?.length) {
      const documents = el("section", undefined, "document-card");
      documents.append(el("h3", "설명 문서 검색 결과 · 합성 문서"));
      documents.append(pretty(payload.retrieval.document_hits));
      resultContent.append(documents);
    }
    if (payload.limitations?.length) resultContent.append(pretty({ limitations: payload.limitations }));
  }

  intent.addEventListener("change", setMetrics);
  setMetrics();

  form.addEventListener("submit", async event => {
    event.preventDefault();
    requestState.textContent = "요청을 처리하고 있습니다…";
    resultPanel.hidden = true;
    const button = form.querySelector("button[type='submit']");
    button.disabled = true;
    const tokenInput = document.getElementById("bearer-token");
    const token = tokenInput.value;
    try {
      const request = buildRequest();
      const response = await fetch(apiPath, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(request),
        cache: "no-store",
        credentials: "same-origin",
      });
      const payload = await response.json();
      if (!response.ok) {
        render(payload);
        requestState.textContent = response.status === 401 ? "인증 실패" : "요청 실패";
        return;
      }
      render(payload);
      requestState.textContent = "요청 완료";
    } catch (error) {
      render({ status: "error", message: error instanceof Error ? error.message : "요청을 처리하지 못했습니다.",
        synthetic: true, data_nature: "synthetic_test", permission_status: "authorized_for_synthetic_testing_only", operational_eligible: false,
        limitations: ["합성 데이터 시뮬레이션 요청에서 발생한 오류입니다."] });
      requestState.textContent = "요청 실패";
    } finally {
      tokenInput.value = "";
      button.disabled = false;
    }
  });
})();
