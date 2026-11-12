(() => {
  "use strict";

  const form = document.getElementById("simulation-form");
  const intent = document.getElementById("intent");
  const metric = document.getElementById("metric");
  const metricField = document.getElementById("metric-field");
  const requestState = document.getElementById("request-state");
  const resultPanel = document.getElementById("result");
  const resultContent = document.getElementById("result-content");
  const apiPath = "/v1/agents/sales-market-agent/runs";
  let lastRequest = null;

  const metricOptions = {
    market_observation: [["scenario_market_value", "합성 시나리오 가치 (KRW_million)"]],
    sales_observation: [["scenario_units_sold", "합성 판매 유사 수량 (item_units)"]],
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

  function buildRequest() {
    const periodStart = document.getElementById("period-start").value;
    const periodEnd = document.getElementById("period-end").value;
    if (!periodStart || !periodEnd || periodStart > periodEnd) {
      throw new Error("기간 시작 월과 종료 월을 확인해 주세요.");
    }
    const selectedIntent = intent.value;
    const requestId = `sim-${crypto.randomUUID()}`;
    const category = document.getElementById("category").value;
    const gender = document.getElementById("gender").value;
    const season = document.getElementById("season").value;
    const context = { period_start: periodStart, period_end: periodEnd };
    if (category) context.category = { value: category, source: "explicit_ui_scope" };
    if (gender) context.gender = gender;
    if (season) context.season = season;
    const query = document.getElementById("raw-query").value.trim();
    return {
      contract_version: "vogue-handoff/0.1",
      request_id: requestId,
      input: {
        request_id: requestId,
        planning_request: { schema_version: "1.0", request_id: requestId, query, context },
        trend_result: null,
        inventory_product_result: null,
        requested_category: category || null,
        requested_metrics: metricOptions[selectedIntent].length ? [metric.value] : [],
        requested_geography: document.getElementById("geography").value || null,
        requested_channel: document.getElementById("channel").value || null,
        requested_query: query,
        requested_period: `${periodStart} ${periodEnd}`,
      },
    };
  }

  function render(payload) {
    const result = payload.result || {};
    const status = result.status || payload.status || "error";
    resultContent.replaceChildren();
    resultPanel.hidden = false;
    const statusText = status === "no_results"
      ? "요청 범위에 맞는 합성 fixture 자료가 없습니다. 값 0을 뜻하지 않습니다."
      : status === "error"
        ? `오류: ${result.errors?.map(item => item.message).join("; ") || payload.error?.message || "요청을 처리하지 못했습니다."}`
        : `상태: ${status} · 계약: ${payload.contract_version || "vogue-handoff/0.1"}`;
    resultContent.append(el("div", statusText, `status-banner${status === "error" ? " error" : ""}`));
    resultContent.append(el("p", "합성 데이터입니다. 실제 시장 수치·수요·트렌드·회사 실적을 나타내지 않습니다.", "simulation-note"));
    resultContent.append(el("h3", "요청 원문"));
    resultContent.append(el("p", lastRequest?.input?.planning_request?.query || "(없음)"));
    for (const finding of result.findings || []) {
      const card = el("article", undefined, "domain-card");
      card.append(el("h3", finding.topic || "합성 분석 결과"));
      card.append(el("p", finding.statement));
      card.append(el("p", `유형: ${finding.kind} · Evidence ID: ${finding.evidence_ids?.join(", ") || "v0.1 응답에서 제공되지 않음"}`));
      resultContent.append(card);
    }
    if (result.limitations?.length) {
      const card = el("section", undefined, "domain-card");
      card.append(el("h3", "분석 제한"));
      for (const limitation of result.limitations) card.append(el("p", limitation));
      resultContent.append(card);
    }
    if (!(result.findings || []).length && status !== "error") {
      resultContent.append(el("p", "현재 요청 범위로 반환할 결과가 없습니다."));
    }
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
      lastRequest = request;
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
