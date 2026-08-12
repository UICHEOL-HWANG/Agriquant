// docs/app.js — data.json 을 읽어 화면을 채우고 ECharts 로 그린다.
//
// 이 파일은 계산을 하지 않는다. 숫자는 전부 site/build.py 가 만든
// data.json 에 이미 들어 있고, 여기서는 배치와 그리기만 한다.
// 브라우저에서 다시 계산하면 사이트가 보여주는 값과 BigQuery 에 적재된
// 값이 갈라질 수 있어서다.

const PCT = (v, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d) + "%");
const NUM = (v) => (v === null || v === undefined ? "—" : v.toLocaleString("ko-KR"));

// 다크모드에 맞춰 축·글자 색을 바꾼다. 차트가 배경에서 뜨지 않게.
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const MUTED = dark ? "#9a9a94" : "#6b6b66";
const LINE = dark ? "#2c2e33" : "#e2e2dc";
const UP = dark ? "#4caf80" : "#1f7a4d";
const DOWN = dark ? "#d9714f" : "#a8442a";
const ACCENT = dark ? "#6ea8dc" : "#2a5d8f";

const BASE = {
  textStyle: { fontFamily: '-apple-system, "Apple SD Gothic Neo", sans-serif' },
  grid: { left: 48, right: 20, top: 34, bottom: 40 },
  tooltip: { trigger: "axis", confine: true },
};
const AXIS = {
  axisLine: { lineStyle: { color: LINE } },
  axisLabel: { color: MUTED, fontSize: 11 },
  splitLine: { lineStyle: { color: LINE, type: "dashed" } },
};

const charts = [];
function draw(id, option) {
  const el = document.getElementById(id);
  if (!el) return;
  const c = echarts.init(el, null, { renderer: "canvas" });
  c.setOption(Object.assign({}, BASE, option));
  charts.push(c);
}
addEventListener("resize", () => charts.forEach((c) => c.resize()));

// ── 화면 채우기 ────────────────────────────────────────────────
function header(d) {
  const f = d.mart.freshness_days;
  const b = document.getElementById("freshness");
  b.textContent = f <= 1 ? `데이터 최신 (${d.mart.last_date})`
                         : `데이터 ${f}일 지연 (${d.mart.last_date})`;
  b.className = "badge " + (f <= 4 ? "ok" : "warn");
  document.getElementById("generated").textContent =
    "갱신 " + d.generated_at.slice(0, 16).replace("T", " ");
  document.getElementById("mart-info").textContent =
    `마트 ${NUM(d.mart.rows)}행 · ${d.mart.items_total}품목 중 ` +
    `운영 ${d.mart.items_operating} · 창고 후보 ${d.mart.items_storable}`;
}

function signals(d) {
  // 행동할 것 = 확신했고 저장이 되는 품목. 저장 10일 미만은 미룰 수 없다.
  const act = d.signals.filter((s) => s.signal !== "판단 보류" && s.storable);
  const hold = d.signals.filter((s) => s.signal === "판단 보류" || !s.storable);

  document.getElementById("action-cards").innerHTML = act.length
    ? act.map((s) => {
        const up = s.signal === "미룸";
        return `<div class="card ${up ? "up" : "down"}">
          <div class="name">${s.item}</div>
          <div class="sig ${up ? "up" : "down"}">${s.signal}</div>
          <div class="prob">${PCT(s.prob_up * 100)}</div>
          <div class="foot">저장 ${s.storage_days}일 · ${NUM(s.price)}원/kg</div>
        </div>`;
      }).join("")
    : `<p class="note">오늘은 확신하는 신호가 없습니다. 평소대로 하면 됩니다.</p>`;

  document.getElementById("hold-count").textContent = `${hold.length}개`;
  document.getElementById("hold-list").innerHTML = hold
    .map((s) => `<span>${s.item} ${PCT(s.prob_up * 100)}</span>`).join("");
}

function scoreTable(d) {
  const b = d.backtest, l = d.live;
  document.getElementById("bt-acc").textContent = PCT(b.accuracy, 2);
  document.getElementById("bt-cacc").textContent = PCT(b.confident_accuracy, 2);
  document.getElementById("bt-n").textContent = NUM(b.n) + "건";

  document.getElementById("lv-acc").textContent = PCT(l.accuracy, 2);
  document.getElementById("lv-cacc").textContent = PCT(l.confident_accuracy, 2);
  document.getElementById("lv-n").textContent = NUM(l.scored) + "건";

  const note = document.getElementById("score-note");
  if (!l.scored) {
    note.innerHTML =
      `실전 열은 아직 비어 있습니다. 예측 <strong>${NUM(l.predictions)}건</strong>을 ` +
      `적재했고, 7거래일이 지나야 채점됩니다. ` +
      `<strong>비어 있는 것이 이 표의 요점입니다</strong> — ` +
      `백테스트만 자랑하지 않고 결과를 알기 전에 기록해 두었습니다.`;
  } else {
    note.innerHTML =
      `찍기 기준선은 ${PCT(b.chance, 1)} 입니다. ` +
      `실전 ${NUM(l.scored)}건이 채점됐고 예측 ${NUM(l.predictions)}건이 쌓였습니다.`;
  }
}

// ── 차트 ───────────────────────────────────────────────────────
function chartMonth(d) {
  const m = d.by_month;
  draw("chart-month", {
    legend: { data: ["확신한 날", "그중 미룸 비중"], top: 0,
              textStyle: { color: MUTED, fontSize: 11 } },
    xAxis: Object.assign({ type: "category",
      data: m.map((x) => x.month + "월") }, AXIS),
    yAxis: [
      Object.assign({ type: "value", name: "일수",
        nameTextStyle: { color: MUTED, fontSize: 10 } }, AXIS),
      Object.assign({ type: "value", name: "미룸 %", max: 100,
        nameTextStyle: { color: MUTED, fontSize: 10 } }, AXIS),
    ],
    series: [
      { name: "확신한 날", type: "bar", data: m.map((x) => x.n),
        itemStyle: { color: ACCENT, borderRadius: [3, 3, 0, 0] },
        animationDelay: (i) => i * 40 },
      { name: "그중 미룸 비중", type: "line", yAxisIndex: 1, smooth: true,
        data: m.map((x) => x.delay_ratio),
        lineStyle: { color: UP, width: 2.5 }, itemStyle: { color: UP },
        symbolSize: 6 },
    ],
  });
}

function chartCum(d) {
  const c = d.cumulative;
  const bt = d.backtest.accuracy;
  const mark = {
    silent: true, symbol: "none",
    label: { formatter: `백테스트 ${bt.toFixed(2)}%`, position: "insideEndTop",
             color: MUTED, fontSize: 11 },
    lineStyle: { color: MUTED, type: "dashed" },
    data: [{ yAxis: bt }],
  };

  if (!c.length) {
    // 채점 전. 빈 축과 기준선만 두고 안내를 얹는다.
    // 비어 있는 것 자체가 메시지라 차트를 숨기지 않는다.
    draw("chart-cum", {
      tooltip: { show: false },
      xAxis: Object.assign({ type: "category", data: [] }, AXIS),
      yAxis: Object.assign({ type: "value", min: 40, max: 85,
        axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
      graphic: {
        type: "text", left: "center", top: "middle",
        style: { text: "채점 대기 중\n7거래일이 지나면 여기에 점이 찍힙니다",
                 fill: MUTED, fontSize: 13, align: "center", lineHeight: 22 },
      },
      series: [{ type: "line", data: [], markLine: mark }],
    });
    return;
  }

  draw("chart-cum", {
    tooltip: { trigger: "axis", confine: true,
      formatter: (p) => {
        const i = p[0].dataIndex;
        return `${c[i].date}<br/>누적 적중률 <b>${c[i].accuracy}%</b><br/>표본 ${c[i].n}건`;
      } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    grid: { left: 48, right: 20, top: 34, bottom: 56 },
    xAxis: Object.assign({ type: "category", data: c.map((x) => x.date) }, AXIS),
    yAxis: Object.assign({ type: "value", min: 40, max: 85,
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    series: [{
      type: "line", smooth: true, data: c.map((x) => x.accuracy),
      lineStyle: { color: ACCENT, width: 2.5 }, itemStyle: { color: ACCENT },
      areaStyle: { color: ACCENT, opacity: 0.1 },
      markLine: mark,
    }],
  });
}

function chartConf(d) {
  const k = d.by_confidence;
  const thr = d.backtest.threshold;
  draw("chart-conf", {
    legend: { data: ["적중률", "행동하는 날 비중"], top: 0,
              textStyle: { color: MUTED, fontSize: 11 } },
    xAxis: Object.assign({ type: "category",
      data: k.map((x) => x.threshold.toFixed(2)), name: "확신도 임계값",
      nameLocation: "middle", nameGap: 26,
      nameTextStyle: { color: MUTED, fontSize: 11 } }, AXIS),
    yAxis: Object.assign({ type: "value",
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    series: [
      { name: "적중률", type: "line", smooth: true,
        data: k.map((x) => x.accuracy),
        lineStyle: { color: UP, width: 2.5 }, itemStyle: { color: UP },
        markLine: {
          silent: true, symbol: "none",
          label: { formatter: "운영 지점", color: MUTED, fontSize: 11 },
          lineStyle: { color: DOWN, type: "dashed" },
          data: [{ xAxis: thr.toFixed(2) }],
        } },
      { name: "행동하는 날 비중", type: "bar",
        data: k.map((x) => x.coverage),
        itemStyle: { color: ACCENT, opacity: 0.35, borderRadius: [3, 3, 0, 0] },
        animationDelay: (i) => i * 50 },
    ],
  });
}

function chartFold(d) {
  const f = d.by_fold;
  const acc = f.map((x) => x.accuracy);
  const lo = Math.min(...acc), hi = Math.max(...acc);
  draw("chart-fold", {
    tooltip: { trigger: "axis", confine: true,
      formatter: (p) => {
        const i = p[0].dataIndex;
        return `${f[i].fold}<br/>적중률 <b>${f[i].accuracy}%</b><br/>표본 ${NUM(f[i].n)}건`;
      } },
    xAxis: Object.assign({ type: "category", data: f.map((x) => x.fold) }, AXIS),
    yAxis: Object.assign({ type: "value", min: 50, max: 72,
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    series: [{
      type: "bar", data: acc,
      itemStyle: {
        borderRadius: [3, 3, 0, 0],
        color: (p) => (p.value === lo || p.value === hi ? DOWN : ACCENT),
      },
      markLine: {
        silent: true, symbol: "none",
        label: { formatter: `편차 ${(hi - lo).toFixed(1)}%p`,
                 color: MUTED, fontSize: 11 },
        lineStyle: { color: MUTED, type: "dashed" },
        data: [[{ xAxis: 0, yAxis: lo }, { xAxis: f.length - 1, yAxis: lo }],
               [{ xAxis: 0, yAxis: hi }, { xAxis: f.length - 1, yAxis: hi }]],
      },
      animationDelay: (i) => i * 60,
    }],
  });
}

// ── 시작 ───────────────────────────────────────────────────────
fetch("data.json", { cache: "no-cache" })
  .then((r) => {
    if (!r.ok) throw new Error(`data.json ${r.status}`);
    return r.json();
  })
  .then((d) => {
    header(d);
    signals(d);
    scoreTable(d);
    chartMonth(d);
    chartCum(d);
    chartConf(d);
    chartFold(d);
  })
  .catch((e) => {
    const b = document.getElementById("freshness");
    b.textContent = "데이터를 불러오지 못했습니다";
    b.className = "badge warn";
    console.error(e);
  });
