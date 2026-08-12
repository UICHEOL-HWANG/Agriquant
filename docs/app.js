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
const FGLINE = dark ? "#e8e8e4" : "#1c1c1a";   // 히트맵 강조 테두리

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

  // 개요의 숫자도 data.json 에서 채운다. 하드코딩하면 데이터가 늘어
  // 백테스트 값이 움직일 때 개요만 옛 숫자로 남는다.
  const bt = d.backtest;   // 위의 b 는 배지 엘리먼트다. 이름을 겹치지 않게.
  document.getElementById("ov-acc").textContent = PCT(bt.accuracy, 1);
  document.getElementById("ov-chance").textContent = PCT(bt.chance, 1);
  document.getElementById("ov-cacc").textContent = PCT(bt.confident_accuracy, 1);
  document.getElementById("ov-cov").textContent = PCT(bt.coverage, 0);
  document.getElementById("ov-items").textContent = `${d.mart.items_operating}품목`;
  document.getElementById("ov-rows").textContent = `${NUM(d.mart.rows)}행`;
}

// 내부 용어를 사람 말로. 사이트에 처음 온 사람은 "미룸"이 뭔지 알 수 없다.
const ACTION = {
  "미룸": { label: "열흘 뒤에 파세요", why: "오를 것 같습니다", cls: "up" },
  "오늘 판매": { label: "오늘 파세요", why: "내릴 것 같습니다", cls: "down" },
};

function signals(d) {
  // 셋으로 나눈다. 예전엔 '애매함'과 '저장 불가'를 한 덩어리로 묶어서,
  // 확률 87% 인 미나리가 이유도 없이 사라져 보였다.
  const act = d.signals.filter((s) => ACTION[s.signal] && s.storable);
  const short = d.signals.filter((s) => ACTION[s.signal] && !s.storable);
  const hold = d.signals.filter((s) => !ACTION[s.signal]);

  document.getElementById("action-title").style.display = act.length ? "" : "none";
  document.getElementById("action-cards").innerHTML = act.length
    ? act.map((s) => {
        const a = ACTION[s.signal];
        return `<div class="card ${a.cls}">
          <div class="name">${s.item}</div>
          <div class="act ${a.cls}">${a.label}</div>
          <div class="why">${a.why} · 확률 <b>${PCT(s.prob_up * 100)}</b></div>
          <div class="foot">오늘 ${NUM(s.price)}원/kg · 저장 ${s.storage_days}일</div>
        </div>`;
      }).join("")
    : `<p class="note">오늘은 확실한 신호가 없습니다. <strong>평소대로</strong> 하면 됩니다.</p>`;

  const chip = (s, extra) =>
    `<span>${s.item} <b>${PCT(s.prob_up * 100)}</b>${extra}</span>`;

  document.getElementById("hold-count").textContent = `${hold.length}개`;
  document.getElementById("hold-list").innerHTML =
    hold.map((s) => chip(s, "")).join("") || `<span class="none">없음</span>`;

  document.getElementById("short-count").textContent = `${short.length}개`;
  document.getElementById("short-list").innerHTML =
    short.map((s) => chip(s, ` · 저장 ${s.storage_days}일`)).join("") ||
    `<span class="none">없음</span>`;
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
      `실제 운영 값은 아직 비어 있습니다. 예측 <strong>${NUM(l.predictions)}건</strong>을 ` +
      `기록해 두었고, 7거래일이 지나야 채점됩니다. ` +
      `<strong>비어 있는 것이 이 표의 요점입니다</strong> — ` +
      `과거 검증 수치만 내세우지 않고, 결과를 알기 전에 예측을 남겨 두었습니다.`;
  } else {
    note.innerHTML =
      `아무렇게나 찍었을 때의 기준선은 ${PCT(b.chance, 1)} 입니다. ` +
      `실제 운영에서 ${NUM(l.scored)}건이 채점됐고, ` +
      `예측 ${NUM(l.predictions)}건이 쌓였습니다.`;
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
    label: { formatter: `과거 검증 ${bt.toFixed(2)}%`, position: "insideEndTop",
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
    legend: { data: ["적중률", "신호 나온 날 비율"], top: 0,
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
      { name: "신호 나온 날 비율", type: "bar",
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

function chartPrice(d) {
  const p = d.prices;
  const names = Object.keys(p.series);
  // 16계열이라 색을 골고루 벌린다. 범례 토글로 보고 싶은 것만 켠다.
  const palette = names.map((_, i) =>
    `hsl(${Math.round((i * 360) / names.length)} 55% ${dark ? 62 : 42}%)`);
  draw("chart-price", {
    color: palette,
    tooltip: { trigger: "axis", confine: true,
      order: "valueDesc", axisPointer: { type: "line" } },
    legend: { type: "scroll", top: 0, textStyle: { color: MUTED, fontSize: 11 },
              inactiveColor: LINE },
    grid: { left: 56, right: 20, top: 56, bottom: 66 },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    xAxis: Object.assign({ type: "category", data: p.dates }, AXIS),
    yAxis: Object.assign({ type: "value", name: "원/kg",
      nameTextStyle: { color: MUTED, fontSize: 10 },
      axisLabel: { color: MUTED, fontSize: 11,
                   formatter: (v) => v.toLocaleString("ko-KR") } }, AXIS),
    series: names.map((n) => ({
      name: n, type: "line", data: p.series[n],
      smooth: true, showSymbol: false, lineStyle: { width: 1.6 },
      connectNulls: true, emphasis: { focus: "series" },
    })),
  });
}

function chartSeason(d) {
  const s = d.seasonal;
  const vals = s.cells.map((c) => c[2]);
  const lim = Math.max(Math.abs(Math.min(...vals)), Math.max(...vals));
  draw("chart-season", {
    tooltip: {
      confine: true,
      formatter: (p) =>
        `${s.items[p.value[1]]} · ${p.value[0] + 1}월<br/>` +
        `연평균 대비 <b>${p.value[2] > 0 ? "+" : ""}${p.value[2]}%</b>`,
    },
    grid: { left: 78, right: 22, top: 12, bottom: 62 },
    xAxis: Object.assign({ type: "category",
      data: Array.from({ length: 12 }, (_, i) => i + 1 + "월"),
      splitArea: { show: true } }, AXIS),
    yAxis: Object.assign({ type: "category", data: s.items,
      axisLabel: { color: MUTED, fontSize: 10 },
      splitArea: { show: true } }, AXIS),
    visualMap: {
      min: -lim, max: lim, calculable: true, orient: "horizontal",
      left: "center", bottom: 6, itemHeight: 90,
      textStyle: { color: MUTED, fontSize: 10 },
      // 파랑(쌈) → 빨강(비쌈). 0 이 가운데 오도록 대칭으로 잡는다.
      inRange: { color: ["#3a6ea5", "#8fb3d0", "#f2f2ee", "#e0a080", "#b8452a"] },
    },
    series: [{
      type: "heatmap", data: s.cells,
      label: { show: false },
      emphasis: { itemStyle: { borderColor: FGLINE, borderWidth: 1 } },
      progressive: 0,
    }],
  });
}

function chartHist(d) {
  const h = d.confidence_hist;
  const thr = d.backtest.threshold;
  draw("chart-hist", {
    tooltip: { trigger: "axis", confine: true,
      formatter: (p) => {
        const i = p[0].dataIndex;
        return `확신도 ${h[i].x}<br/>${NUM(h[i].n)}일`;
      } },
    xAxis: Object.assign({ type: "category",
      data: h.map((x) => x.x.toFixed(2)),
      name: "확신도 |P − 0.5|", nameLocation: "middle", nameGap: 26,
      nameTextStyle: { color: MUTED, fontSize: 11 } }, AXIS),
    yAxis: Object.assign({ type: "value", name: "일수",
      nameTextStyle: { color: MUTED, fontSize: 10 } }, AXIS),
    series: [{
      type: "bar", data: h.map((x) => x.n), barCategoryGap: "8%",
      itemStyle: {
        // 임계값 오른쪽만 진하게 — 저기가 행동하는 구간이다
        color: (p) => (h[p.dataIndex].x >= thr ? ACCENT : LINE),
      },
      markLine: {
        silent: true, symbol: "none",
        label: { formatter: `임계값 ${thr}`, color: MUTED, fontSize: 11 },
        lineStyle: { color: DOWN, type: "dashed" },
        data: [{ xAxis: h.findIndex((x) => x.x >= thr) }],
      },
      animationDelay: (i) => i * 18,
    }],
  });
}

function chartRel(d) {
  const k = d.reliability;
  draw("chart-rel", {
    tooltip: { trigger: "axis", confine: true,
      formatter: (p) => {
        const i = p[0].dataIndex;
        return `모델이 말한 확률 <b>${k[i].predicted}%</b><br/>` +
               `실제로 오른 비율 <b>${k[i].actual}%</b><br/>표본 ${NUM(k[i].n)}건`;
      } },
    legend: { data: ["실제", "예측 = 실제"], top: 0,
              textStyle: { color: MUTED, fontSize: 11 } },
    xAxis: Object.assign({ type: "value", min: 0, max: 100,
      name: "모델이 말한 확률", nameLocation: "middle", nameGap: 26,
      nameTextStyle: { color: MUTED, fontSize: 11 },
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    yAxis: Object.assign({ type: "value", min: 0, max: 100,
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    series: [
      { name: "예측 = 실제", type: "line", data: [[0, 0], [100, 100]],
        showSymbol: false, lineStyle: { color: MUTED, type: "dashed", width: 1.4 },
        silent: true },
      { name: "실제", type: "line",
        data: k.map((x) => [x.predicted, x.actual]),
        smooth: true, symbolSize: (v, p) => 5 + Math.sqrt(k[p.dataIndex].n) / 12,
        lineStyle: { color: ACCENT, width: 2.5 }, itemStyle: { color: ACCENT } },
    ],
  });
}

function chartItem(d) {
  const it = d.by_item;
  draw("chart-item", {
    tooltip: { trigger: "axis", confine: true,
      formatter: (p) => {
        const i = p[0].dataIndex;
        const c = it[i].confident_accuracy;
        return `${it[i].item}<br/>적중률 <b>${it[i].accuracy}%</b>` +
               (c ? `<br/>확신한 날 <b>${c}%</b>` : "") +
               `<br/>표본 ${NUM(it[i].n)}건`;
      } },
    legend: { data: ["전체", "확신한 날"], top: 0,
              textStyle: { color: MUTED, fontSize: 11 } },
    grid: { left: 74, right: 24, top: 34, bottom: 34 },
    xAxis: Object.assign({ type: "value", min: 45, max: 85,
      axisLabel: { color: MUTED, fontSize: 11, formatter: "{value}%" } }, AXIS),
    yAxis: Object.assign({ type: "category", data: it.map((x) => x.item),
      axisLabel: { color: MUTED, fontSize: 10.5 } }, AXIS),
    series: [
      { name: "전체", type: "bar", data: it.map((x) => x.accuracy),
        itemStyle: { color: ACCENT, borderRadius: [0, 3, 3, 0] },
        animationDelay: (i) => i * 35 },
      { name: "확신한 날", type: "scatter",
        data: it.map((x) => [x.confident_accuracy, x.item]),
        symbolSize: 9, itemStyle: { color: UP } },
    ],
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
    chartPrice(d);
    chartSeason(d);
    chartCum(d);
    chartConf(d);
    chartHist(d);
    chartRel(d);
    chartFold(d);
    chartItem(d);
  })
  .catch((e) => {
    const b = document.getElementById("freshness");
    b.textContent = "데이터를 불러오지 못했습니다";
    b.className = "badge warn";
    console.error(e);
  });
