/* StudAI · Relatórios de evolução (Fase 5).
 *
 * Painel sem dependências: busca /api/reports/overview e /api/reports/nodes/<id>
 * (contrato em src/studai/features/reports/CONTRACT.md) e desenha gráficos em SVG
 * inline. Expõe window.refreshReports (chamado por showView) e
 * window.renderStudyReport (reutilizado pelo diagnóstico em app.js).
 */
(function () {
  "use strict";

  const DEFAULT_COURSE = "eng-comp-sao-carlos";
  const COURSE_KEY = "studai-reports-course";
  const SVG_NS = "http://www.w3.org/2000/svg";

  const STATUS = {
    strong: { label: "Forte", icon: "✓" },
    uncertain: { label: "Incerto", icon: "?" },
    gap: { label: "Lacuna", icon: "!" },
    untested: { label: "Não testado", icon: "–" },
  };
  const LEVEL_LABEL = { subject: "matéria", area: "área", content: "conteúdo", leaf: "subtópico" };
  const KIND_LABEL = { diagnostic: "Diagnóstico", exam: "Prova completa", daily: "Treinamento" };
  const REASONS = { unknown_content: "Não sabia o conteúdo", concept_confusion: "Confundiu um detalhe", calc_error: "Erro de cálculo", misread: "Interpretação", trap: "Pegadinha", time_pressure: "Faltou tempo", blank: "Em branco" };
  const STATUS_FILTERS = [["all", "Todos"], ["gap", "Lacunas"], ["uncertain", "Incertos"], ["strong", "Fortes"], ["untested", "Não testados"]];
  const SORTS = [["priority", "Prioridade"], ["mastery", "Domínio (menor primeiro)"], ["weight", "Peso no curso"]];

  const state = {
    courseId: null, courses: [], overview: null, error: null,
    focusId: null, expanded: new Set(), status: "all", sort: "priority",
    selectedId: null, detail: null, detailLoading: false, detailError: null,
    tables: { evolution: false, detail: false }, matchCache: new Map(), maxPriority: 0,
    requestSeq: 0,
  };

  // ---------------------------------------------------------------- helpers
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const isNum = value => typeof value === "number" && Number.isFinite(value);
  const clamp01 = value => Math.max(0, Math.min(1, value));
  const formatters = {};
  const nf = (digits = 0) => (formatters[digits] ||= new Intl.NumberFormat("pt-BR", { maximumFractionDigits: digits, minimumFractionDigits: digits }));
  const pct = (value, digits = 0) => isNum(value) ? `${nf(digits).format(value * 100)}%` : "—";
  const intFmt = value => isNum(value) ? nf(0).format(value) : "—";
  function pp(delta, digits = 1) {
    if (!isNum(delta)) return "—";
    const value = Math.abs(delta * 100);
    if (value < 0.05) return "0 p.p.";
    return `${delta > 0 ? "+" : "−"}${nf(digits).format(value)} p.p.`;
  }
  function parseDate(iso) { const date = iso ? new Date(iso) : null; return date && !Number.isNaN(date.getTime()) ? date : null; }
  function dateShort(iso) { const d = parseDate(iso); return d ? d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", timeZone: "UTC" }) : "—"; }
  function dateLong(iso) { const d = parseDate(iso); return d ? d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }) : "—"; }
  const cssId = value => (window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&"));

  async function fetchJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const error = new Error((data && data.error) || `Falha HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  const nodes = () => (state.overview && state.overview.nodes) || {};
  const nodeById = id => nodes()[id] || null;
  function nodeName(item) {
    if (!item) return "";
    if (typeof item === "string") return nodeById(item)?.name || item;
    return item.name || (Array.isArray(item.path) ? item.path[item.path.length - 1] : "") || nodeById(item.id)?.name || item.id || "";
  }
  function nodePath(item) {
    const node = typeof item === "string" ? nodeById(item) : item;
    const path = Array.isArray(node?.path) ? node.path : (nodeById(node?.id)?.path || []);
    return path.slice(0, -1).join(" › ");
  }
  const hasEvidence = node => Boolean(node) && node.status !== "untested" && (node.attempts == null || node.attempts > 0);

  function examLabel(sourceId) {
    if (!sourceId) return "—";
    const id = String(sourceId);
    const match = /(fuvest|simulado)[-_]?(\d{4})/i.exec(id);
    if (match) {
      const phase = /primeira|fase[-_]?1/i.test(id) ? " · 1ª fase" : /segunda|fase[-_]?2/i.test(id) ? " · 2ª fase" : "";
      const version = /prova[-_]([a-z]{1,2}\d?)(?:[-_]|$)/i.exec(id);
      return `${match[1].toUpperCase()} ${match[2]}${phase}${version ? ` · prova ${version[1].toUpperCase()}` : ""}`;
    }
    try {
      // `exams` é declarado em app.js (escopo global compartilhado entre scripts clássicos).
      if (typeof exams !== "undefined" && Array.isArray(exams)) {
        const found = exams.find(item => item.id === id);
        if (found?.name) return found.name;
      }
    } catch { /* app.js ausente */ }
    return id.replace(/-[0-9a-f]{8,}$/i, "").replace(/[_-]+/g, " ");
  }

  // ---------------------------------------------------------------- small marks (string templates)
  function statusBadge(status) {
    const key = STATUS[status] ? status : "untested";
    const info = STATUS[key];
    return `<span class="rp-status rp-status--${key}"><span class="rp-status-icon" aria-hidden="true">${info.icon}</span>${info.label}</span>`;
  }

  function masteryBar(node, { compact = false } = {}) {
    if (!hasEvidence(node) || !isNum(node.mastery)) {
      return `<span class="rp-meter rp-meter--empty${compact ? " rp-meter--compact" : ""}" role="img" aria-label="Sem respostas ainda"><span class="rp-meter-track"></span><span class="rp-meter-value">sem dados</span></span>`;
    }
    const value = clamp01(node.mastery);
    const lower = isNum(node.lower) ? clamp01(node.lower) : value;
    const upper = isNum(node.upper) ? clamp01(node.upper) : value;
    const label = `Domínio ${pct(value)}, intervalo de 80% entre ${pct(lower)} e ${pct(upper)}`;
    return `<span class="rp-meter${compact ? " rp-meter--compact" : ""}" role="img" aria-label="${esc(label)}" title="${esc(label)}"><span class="rp-meter-track"><span class="rp-meter-fill" style="width:${(value * 100).toFixed(1)}%"></span><span class="rp-meter-whisker" style="left:${(lower * 100).toFixed(1)}%;width:${Math.max(0, (upper - lower) * 100).toFixed(1)}%"></span></span><span class="rp-meter-value">${pct(value)}</span></span>`;
  }

  function deltaChip(delta) {
    if (!isNum(delta)) return `<span class="rp-delta rp-delta--none" title="Sem ponto anterior para comparar">—</span>`;
    if (Math.abs(delta) < 0.0005) return `<span class="rp-delta rp-delta--flat">= 0 p.p.</span>`;
    const up = delta > 0;
    return `<span class="rp-delta ${up ? "rp-delta--up" : "rp-delta--down"}"><span aria-hidden="true">${up ? "▲" : "▼"}</span><span class="rp-sr">${up ? "subiu" : "caiu"}</span> ${pp(delta)}</span>`;
  }

  function priorityMeter(node) {
    const value = isNum(node?.priority) ? node.priority : 0;
    const ratio = state.maxPriority > 0 ? Math.min(1, value / state.maxPriority) : 0;
    const label = `Prioridade ${nf(3).format(value)} (${Math.round(ratio * 100)}% da maior)`;
    return `<span class="rp-prio" role="img" aria-label="${esc(label)}" title="${esc(label)}"><span class="rp-prio-fill" style="width:${(ratio * 100).toFixed(1)}%"></span></span>`;
  }

  /** Sparkline (escala própria, janela mínima de 20 p.p.); null = lacuna na linha. */
  function sparkline(series, { width = 72, height = 22 } = {}) {
    const values = Array.isArray(series) ? series : [];
    const present = values.map((value, index) => [index, value]).filter(([, value]) => isNum(value));
    if (!present.length) return `<span class="rp-spark-empty" title="Sem histórico">—</span>`;
    let min = Math.min(...present.map(([, v]) => v));
    let max = Math.max(...present.map(([, v]) => v));
    if (max - min < 0.2) { const mid = (max + min) / 2; max = Math.min(1, Math.max(0.2, mid + 0.1)); min = max - 0.2; }
    const pad = 3;
    const x = index => values.length <= 1 ? width / 2 : pad + index * (width - pad * 2) / (values.length - 1);
    const y = value => pad + (1 - (value - min) / ((max - min) || 1)) * (height - pad * 2);
    const runs = [];
    let run = [];
    values.forEach((value, index) => {
      if (isNum(value)) run.push(`${x(index).toFixed(1)},${y(value).toFixed(1)}`);
      else if (run.length) { runs.push(run); run = []; }
    });
    if (run.length) runs.push(run);
    const paths = runs.filter(points => points.length > 1).map(points => `<polyline class="rp-spark-line" points="${points.join(" ")}"/>`).join("");
    const [lastIndex, lastValue] = present[present.length - 1];
    const label = present.length > 1 ? `Evolução: ${pct(present[0][1])} → ${pct(lastValue)}` : `Um ponto: ${pct(lastValue)}`;
    return `<svg class="rp-spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>${paths}<circle class="rp-spark-dot" cx="${x(lastIndex).toFixed(1)}" cy="${y(lastValue).toFixed(1)}" r="2.6"/></svg>`;
  }

  // ---------------------------------------------------------------- line chart (SVG + crosshair)
  /**
   * points: [{t: Date|null, y: number|null, lo?, hi?, kind, title, lines: [string]}]
   * opts: {yDomain?: [a,b], band?: bool, height?, ariaLabel, valueLabel}
   */
  function mountLineChart(host, points, opts = {}) {
    host.innerHTML = "";
    host.classList.add("rp-chart");
    const width = Math.max(260, Math.round(host.clientWidth || 600));
    const height = opts.height || 220;
    const margin = { top: 14, right: 46, bottom: 28, left: 40 };
    const plotW = width - margin.left - margin.right;
    const plotH = height - margin.top - margin.bottom;
    const valid = points.filter(point => isNum(point.y));

    let [y0, y1] = opts.yDomain || [0, 1];
    if (!opts.yDomain && valid.length) {
      y0 = Math.max(0, Math.floor((Math.min(...valid.map(point => point.y)) - 0.03) * 20) / 20);
      y1 = Math.min(1, Math.ceil((Math.max(...valid.map(point => point.y)) + 0.03) * 20) / 20);
      if (y1 - y0 < 0.1) { y1 = Math.min(1, y0 + 0.1); y0 = Math.max(0, y1 - 0.1); }
    }
    const span = y1 - y0;
    const step = span <= 0.2 ? 0.05 : span <= 0.5 ? 0.1 : 0.25;
    const ticks = [];
    for (let v = Math.ceil(y0 / step - 1e-9) * step; v <= y1 + 1e-9; v += step) ticks.push(+v.toFixed(4));

    const times = points.map(point => point.t ? point.t.getTime() : null);
    const known = times.filter(value => value != null);
    const tMin = known.length ? Math.min(...known) : 0;
    const tMax = known.length ? Math.max(...known) : 0;
    const useTime = known.length === points.length && tMax > tMin;
    const xAt = index => {
      if (points.length <= 1) return margin.left + plotW / 2;
      if (useTime) return margin.left + (times[index] - tMin) / (tMax - tMin) * plotW;
      return margin.left + index * plotW / (points.length - 1);
    };
    const yAt = value => margin.top + (1 - (value - y0) / (span || 1)) * plotH;

    const parts = [];
    ticks.forEach(value => {
      const y = yAt(value).toFixed(1);
      parts.push(`<line class="rp-grid" x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}"/>`);
      parts.push(`<text class="rp-axis-label" x="${margin.left - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${pct(value)}</text>`);
    });
    parts.push(`<line class="rp-baseline" x1="${margin.left}" x2="${width - margin.right}" y1="${margin.top + plotH}" y2="${margin.top + plotH}"/>`);

    // Rótulos do eixo X: todos se couberem (≥ 46px cada), senão um a cada k, sempre o último.
    const every = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor(plotW / 46))));
    points.forEach((point, index) => {
      const last = index === points.length - 1;
      if (!last && (index % every !== 0 || points.length - 1 - index < every)) return;
      parts.push(`<text class="rp-axis-label" x="${xAt(index).toFixed(1)}" y="${height - 8}" text-anchor="middle">${esc(point.t ? dateShort(point.t.toISOString()) : String(index + 1))}</text>`);
    });

    const runs = [];
    let run = [];
    points.forEach((point, index) => {
      if (isNum(point.y)) run.push(index);
      else if (run.length) { runs.push(run); run = []; }
    });
    if (run.length) runs.push(run);
    if (opts.band) {
      runs.forEach(indices => {
        const withBand = indices.filter(index => isNum(points[index].lo) && isNum(points[index].hi));
        if (!withBand.length) return;
        if (withBand.length === 1) {
          const index = withBand[0];
          parts.push(`<rect class="rp-band" x="${(xAt(index) - 4).toFixed(1)}" y="${yAt(points[index].hi).toFixed(1)}" width="8" height="${Math.max(1, yAt(points[index].lo) - yAt(points[index].hi)).toFixed(1)}"/>`);
          return;
        }
        const upper = withBand.map(index => `${xAt(index).toFixed(1)},${yAt(points[index].hi).toFixed(1)}`);
        const lower = withBand.slice().reverse().map(index => `${xAt(index).toFixed(1)},${yAt(points[index].lo).toFixed(1)}`);
        parts.push(`<polygon class="rp-band" points="${upper.concat(lower).join(" ")}"/>`);
      });
    }
    runs.filter(indices => indices.length > 1).forEach(indices => {
      parts.push(`<polyline class="rp-line" points="${indices.map(index => `${xAt(index).toFixed(1)},${yAt(points[index].y).toFixed(1)}`).join(" ")}"/>`);
    });
    points.forEach((point, index) => {
      if (!isNum(point.y)) return;
      const x = xAt(index);
      const y = yAt(point.y);
      parts.push(point.kind === "exam"
        ? `<rect class="rp-marker" x="${(x - 4.5).toFixed(1)}" y="${(y - 4.5).toFixed(1)}" width="9" height="9" rx="1"/>`
        : `<circle class="rp-marker" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5"/>`);
    });
    if (valid.length) {
      const lastIndex = points.lastIndexOf(valid[valid.length - 1]);
      parts.push(`<text class="rp-end-label" x="${(xAt(lastIndex) + 9).toFixed(1)}" y="${yAt(points[lastIndex].y).toFixed(1)}" dominant-baseline="middle">${pct(points[lastIndex].y)}</text>`);
    }
    parts.push(`<line class="rp-crosshair" x1="0" x2="0" y1="${margin.top}" y2="${margin.top + plotH}" visibility="hidden"/>`);
    parts.push(`<circle class="rp-focus-ring" r="7.5" visibility="hidden"/>`);
    parts.push(`<rect class="rp-hit" x="${margin.left - 12}" y="${margin.top}" width="${plotW + 24}" height="${plotH}"/>`);

    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("class", "rp-chart-svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("aria-label", `${opts.ariaLabel || "Gráfico"}. Use as setas para percorrer os pontos; a tabela traz todos os valores.`);
    svg.innerHTML = parts.join("");
    host.appendChild(svg);

    const tip = document.createElement("div");
    tip.className = "rp-tooltip";
    tip.setAttribute("role", "status");
    tip.hidden = true;
    host.appendChild(tip);

    const crosshair = svg.querySelector(".rp-crosshair");
    const ring = svg.querySelector(".rp-focus-ring");
    let current = -1;
    function show(index) {
      if (index < 0 || index >= points.length) return;
      current = index;
      const point = points[index];
      const x = xAt(index);
      crosshair.setAttribute("x1", x.toFixed(1));
      crosshair.setAttribute("x2", x.toFixed(1));
      crosshair.setAttribute("visibility", "visible");
      if (isNum(point.y)) {
        ring.setAttribute("cx", x.toFixed(1));
        ring.setAttribute("cy", yAt(point.y).toFixed(1));
        ring.setAttribute("visibility", "visible");
      } else ring.setAttribute("visibility", "hidden");
      // Rótulos vêm da API: sempre via textContent.
      tip.replaceChildren();
      const value = document.createElement("strong");
      value.textContent = isNum(point.y) ? `${opts.valueLabel ? `${opts.valueLabel} ` : ""}${pct(point.y, 1)}` : "Sem respostas até aqui";
      tip.appendChild(value);
      const key = document.createElement("span");
      key.className = "rp-tooltip-key";
      const mark = document.createElement("i");
      mark.className = `rp-tooltip-mark${point.kind === "exam" ? " is-exam" : ""}`;
      key.append(mark, document.createTextNode(point.title || ""));
      tip.appendChild(key);
      (point.lines || []).filter(Boolean).forEach(text => {
        const line = document.createElement("span");
        line.textContent = text;
        tip.appendChild(line);
      });
      tip.hidden = false;
      const hostWidth = host.clientWidth || width;
      const scale = hostWidth / width;
      const tipWidth = tip.offsetWidth || 170;
      let left = x * scale + 14;
      if (left + tipWidth > hostWidth) left = x * scale - tipWidth - 14;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${Math.round(margin.top * scale)}px`;
    }
    function hide() {
      current = -1;
      crosshair.setAttribute("visibility", "hidden");
      ring.setAttribute("visibility", "hidden");
      tip.hidden = true;
    }
    function nearest(clientX) {
      const rect = svg.getBoundingClientRect();
      const x = (clientX - rect.left) * (width / (rect.width || width));
      let best = 0;
      let bestDistance = Infinity;
      points.forEach((point, index) => { const distance = Math.abs(xAt(index) - x); if (distance < bestDistance) { best = index; bestDistance = distance; } });
      return best;
    }
    svg.addEventListener("pointermove", event => show(nearest(event.clientX)));
    svg.addEventListener("pointerleave", () => { if (document.activeElement !== svg) hide(); });
    svg.addEventListener("focus", () => show(current >= 0 ? current : points.length - 1));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", event => {
      if (event.key === "ArrowRight") { event.preventDefault(); show(Math.min(points.length - 1, current + 1)); }
      else if (event.key === "ArrowLeft") { event.preventDefault(); show(Math.max(0, current - 1)); }
      else if (event.key === "Home") { event.preventDefault(); show(0); }
      else if (event.key === "End") { event.preventDefault(); show(points.length - 1); }
      else if (event.key === "Escape") hide();
    });
  }

  function chartLegend({ band = false, lineLabel = "" } = {}) {
    const line = lineLabel ? `<span class="rp-legend-item"><i class="rp-key-line" aria-hidden="true"></i>${esc(lineLabel)}</span>` : "";
    const area = band ? `<span class="rp-legend-item"><i class="rp-key-band" aria-hidden="true"></i>Intervalo de 80%</span>` : "";
    return `<div class="rp-legend">${line}${area}<span class="rp-legend-item"><i class="rp-key-dot" aria-hidden="true"></i>Diagnóstico</span><span class="rp-legend-item"><i class="rp-key-square" aria-hidden="true"></i>Prova completa</span></div>`;
  }

  // ---------------------------------------------------------------- study report (shared with app.js)
  function reportName(node, interactive) {
    const name = nodeName(node);
    const path = nodePath(node);
    const nameHtml = interactive && node.id ? `<button type="button" class="rp-link" data-node-link="${esc(node.id)}">${esc(name)}</button>` : `<strong>${esc(name)}</strong>`;
    return `<span class="rp-report-name">${nameHtml}${path ? `<small>${esc(path)}</small>` : ""}</span>`;
  }
  const asNode = item => typeof item === "string" ? (nodeById(item) || { id: item }) : (item || {});

  function reportList(items, emptyText, interactive) {
    const list = Array.isArray(items) ? items.map(asNode) : [];
    if (!list.length) return `<p class="rp-report-empty">${esc(emptyText)}</p>`;
    return `<ul class="rp-report-list">${list.map(node => `<li>${reportName(node, interactive)}<span class="rp-report-meta">${hasEvidence(node) && isNum(node.mastery) ? pct(node.mastery) : ""}</span></li>`).join("")}</ul>`;
  }

  function reportItems(items, interactive) {
    return `<ol class="rp-report-priorities">${items.map(asNode).map(node => `<li><div class="rp-report-prio-top">${reportName(node, interactive)}${node.status ? statusBadge(node.status) : ""}</div>${hasEvidence(node) && isNum(node.mastery) ? `<p class="rp-report-line">Domínio estimado: ${pct(node.mastery)}${isNum(node.lower) && isNum(node.upper) ? ` (IC 80% ${pct(node.lower)}–${pct(node.upper)})` : ""}</p>` : ""}${node.reason ? `<p class="rp-report-line">${esc(node.reason)}</p>` : ""}${node.exit_criterion ? `<p class="rp-report-line"><b>Critério de saída:</b> ${esc(node.exit_criterion)}</p>` : ""}</li>`).join("")}</ol>`;
  }

  /** Relatório (metrics.report / overview.latest_report). Renderização defensiva. */
  function renderStudyReport(report, options = {}) {
    if (!report || typeof report !== "object") return "";
    const interactive = Boolean(options.interactive);
    const priorities = Array.isArray(report.priorities) ? report.priorities : [];
    const untested = Array.isArray(report.untested_high_priority) ? report.untested_high_priority : [];
    const nonAssessable = report.non_assessable && typeof report.non_assessable === "object" ? report.non_assessable : {};
    const naEntries = Object.entries(nonAssessable).filter(([, value]) => isNum(value));
    const naNote = typeof nonAssessable.note === "string" ? nonAssessable.note : "";
    const naHtml = naEntries.length || naNote
      ? `<p class="rp-report-note"><span aria-hidden="true">ⓘ</span> ${naEntries.map(([name, value]) => `${esc(name)}: ${pct(value)} da nota final, fora do banco de questões.`).join(" ")} ${esc(naNote)}</p>` : "";
    const head = [];
    if (isNum(report.estimated_score)) head.push(`<span>Nota ponderada estimada <strong>${pct(report.estimated_score, 1)}</strong></span>`);
    if (isNum(report.coverage)) head.push(`<span>Cobertura das áreas <strong>${pct(report.coverage)}</strong></span>`);
    const title = options.title === false ? "" : `<h3>${esc(options.title || "Relatório de domínio")}</h3>`;
    return `<div class="rp-report">${title}${head.length ? `<div class="rp-report-head">${head.join("")}</div>` : ""}<div class="rp-report-cols"><div class="rp-report-block"><h4><span class="rp-status-icon rp-status--strong" aria-hidden="true">✓</span>Pontos fortes</h4>${reportList(report.strengths, "Ainda não há pontos fortes com evidência suficiente.", interactive)}</div><div class="rp-report-block"><h4><span class="rp-status-icon rp-status--gap" aria-hidden="true">!</span>Lacunas</h4>${reportList(report.gaps, "Nenhuma lacuna confirmada.", interactive)}</div></div><div class="rp-report-block"><h4>Prioridades de estudo</h4>${priorities.length ? reportItems(priorities, interactive) : `<p class="rp-report-empty">Nenhuma prioridade calculada neste relatório.</p>`}</div>${untested.length ? `<div class="rp-report-block"><h4><span class="rp-status-icon rp-status--untested" aria-hidden="true">–</span>Ainda não testados (alta prioridade)</h4>${reportItems(untested, interactive)}</div>` : ""}${naHtml}</div>`;
  }

  // ---------------------------------------------------------------- overview sections
  function weightsNote(course) {
    const weights = Object.entries(course?.weights || {}).filter(([, value]) => isNum(value)).sort((a, b) => b[1] - a[1]);
    // Na 1ª fase cada questão vale 1 ponto e a redação não entra: o peso é só o da prova objetiva.
    const firstPhase = course?.phase === "fase1";
    const na = firstPhase ? [] : Object.entries(course?.non_assessable || {}).filter(([, value]) => isNum(value));
    if (!weights.length && !na.length) return "";
    const top = weights.slice(0, 2).map(([name, value]) => `${esc(name)} ${pct(value)}`);
    const naText = na.map(([name, value]) => `${esc(name)} ${pct(value)} (fora do banco)`);
    const all = weights.map(([name, value]) => `<li><span>${esc(name)}</span><span>${pct(value, 1)}</span></li>`).join("");
    const naAll = na.map(([name, value]) => `<li><span>${esc(name)} <small>fora do banco</small></span><span>${pct(value, 1)}</span></li>`).join("");
    return `<div class="rp-weights"><p><span class="rp-weights-label">${firstPhase ? "Peso na 1ª fase" : "Peso na nota final"}:</span> ${top.concat(naText).join(" · ")}</p>${weights.length > 2 ? `<details><summary>Ver todos os pesos</summary><ul class="rp-weights-list">${all}${naAll}</ul></details>` : ""}</div>`;
  }

  function kpiRow(kpis, timeline) {
    const k = kpis || {};
    const last = timeline[timeline.length - 1];
    const lastAt = k.last_activity || last?.at;
    const tiles = [
      { label: "Nota ponderada estimada", value: pct(k.estimated_score, 1), sub: `${deltaChip(k.estimated_score_delta)} <span class="rp-kpi-caption">vs. sessão anterior</span>` },
      { label: "Questões respondidas", value: intFmt(k.answered), sub: `<span class="rp-kpi-caption">somando todas as sessões</span>` },
      { label: "Sessões", value: intFmt(k.sessions), sub: `<span class="rp-kpi-caption">${lastAt ? `última em ${dateLong(lastAt)}` : "nenhuma ainda"}</span>` },
      { label: "Cobertura das áreas prioritárias", value: pct(k.coverage), sub: `<span class="rp-kpi-caption">peso × incidência com evidência</span>` },
      { label: "Pontos fortes · lacunas", value: `<span class="rp-kpi-split"><span><span class="rp-status-icon rp-status--strong" aria-hidden="true">✓</span>${intFmt(k.strong)}</span><span><span class="rp-status-icon rp-status--gap" aria-hidden="true">!</span>${intFmt(k.gaps)}</span></span>`, sub: `<span class="rp-kpi-caption">${intFmt(k.strong)} fortes · ${intFmt(k.gaps)} lacunas</span>` },
    ];
    return `<div class="rp-kpis">${tiles.map(tile => `<div class="metric-card rp-kpi"><span>${tile.label}</span><strong>${tile.value}</strong><div class="rp-kpi-sub">${tile.sub}</div></div>`).join("")}</div>`;
  }

  function evolutionPanel(timeline) {
    const showTable = state.tables.evolution;
    const rows = timeline.map(point => `<tr><td>${esc(point.label || KIND_LABEL[point.kind] || "Sessão")}</td><td>${esc(KIND_LABEL[point.kind] || point.kind || "—")}</td><td>${dateLong(point.at)}</td><td class="rp-num">${intFmt(point.answered)}</td><td class="rp-num">${pct(point.accuracy)}</td><td class="rp-num">${pct(point.estimated_score, 1)}</td></tr>`).join("");
    return `<section class="panel rp-panel rp-evolution" aria-labelledby="rp-evolution-title"><div class="rp-panel-head"><div><h2 id="rp-evolution-title">Evolução da nota estimada</h2><p>Nota ponderada pelo curso, recalculada a cada sessão com todas as respostas até ali.</p></div><button type="button" class="text-button" data-table-toggle="evolution" aria-pressed="${showTable}">${showTable ? "Ver gráfico" : "Ver tabela"}</button></div>${showTable ? "" : chartLegend()}<div id="rp-evolution-chart" class="${showTable ? "hidden" : ""}"></div><div class="rp-table-wrap ${showTable ? "" : "hidden"}"><table class="subject-table rp-table"><caption class="rp-sr">Nota estimada por sessão</caption><thead><tr><th>Sessão</th><th>Tipo</th><th>Data</th><th class="rp-num">Respostas</th><th class="rp-num">Acerto</th><th class="rp-num">Nota estimada</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  function prioritiesPanel(overview) {
    const ids = (overview.priorities || []).slice(0, 10).filter(id => nodeById(id));
    const items = ids.map((id, index) => {
      const node = nodeById(id);
      return `<li><button type="button" class="rp-prio-item" data-node-link="${esc(id)}"><span class="rp-prio-rank">${index + 1}</span><span class="rp-prio-main"><strong>${esc(node.name)}</strong><small>${esc(nodePath(node))}</small></span><span class="rp-prio-side">${statusBadge(node.status)}<span class="rp-prio-mastery">${hasEvidence(node) ? `domínio ${pct(node.mastery)}` : "sem respostas"}</span></span></button></li>`;
    }).join("");
    return `<section class="panel rp-panel rp-priorities" aria-labelledby="rp-priorities-title"><div class="rp-panel-head"><div><h2 id="rp-priorities-title">Prioridades de estudo</h2><p>Peso no curso × incidência × quanto falta dominar × incerteza.</p></div></div>${items ? `<ol class="rp-prio-list">${items}</ol>` : `<p class="rp-report-empty">Sem prioridades calculadas.</p>`}</section>`;
  }

  function subjectsPanel(overview) {
    const roots = (overview.roots || []).filter(id => nodeById(id));
    const rows = roots.map(id => {
      const node = nodeById(id);
      const label = `${node.name}: peso ${pct(node.course_weight)} no curso, situação ${STATUS[node.status]?.label || "—"}. Abrir conteúdos`;
      return `<button type="button" class="rp-subject-row" data-subject="${esc(id)}" aria-label="${esc(label)}"><span class="rp-subject-name"><strong>${esc(node.name)}</strong><small>${intFmt(node.attempts)} resp.</small></span><span class="rp-subject-weight"><span class="rp-col-label">Peso </span>${pct(node.course_weight)}</span><span class="rp-subject-bar">${masteryBar(node)}</span><span class="rp-subject-status">${statusBadge(node.status)}</span><span class="rp-subject-spark">${sparkline(node.series)}</span><span class="rp-subject-delta">${deltaChip(node.delta)}</span></button>`;
    }).join("");
    return `<section class="panel rp-panel rp-subjects" aria-labelledby="rp-subjects-title"><div class="rp-panel-head"><div><h2 id="rp-subjects-title">Domínio por matéria</h2><p>Ordenado pelo peso no curso. A barra é o domínio estimado; o traço escuro, o intervalo de 80%. Clique para abrir os conteúdos.</p></div></div><div class="rp-subject-head" aria-hidden="true"><span>Matéria</span><span>Peso</span><span>Domínio · IC 80%</span><span>Situação</span><span>Evolução</span><span>Variação</span></div><div class="rp-subject-list">${rows}</div></section>`;
  }

  // Resumo da semana: o que foi feito nos últimos 7 dias e como isso mexeu na prontidão.
  const REASON_TEXT = { unknown_content: "não sabia o conteúdo", concept_confusion: "confundiu um detalhe",
    calc_error: "erro de conta", misread: "interpretação", trap: "pegadinha", time_pressure: "faltou tempo" };
  function duration(seconds) {
    const minutes = Math.round((seconds || 0) / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}` : `${minutes} min`;
  }
  function change(pair, format) {
    if (!pair || pair.to == null) return "—";
    if (pair.from == null || Math.abs(pair.to - pair.from) < 1e-9) return format(pair.to);
    const up = pair.to > pair.from;
    return `${format(pair.from)} → <b class="${up ? "rp-week-up" : "rp-week-down"}">${format(pair.to)}</b>`;
  }
  function weekPanel(week) {
    if (!week || !week.answered) return "";
    const tiles = [
      ["Questões", intFmt(week.answered), week.accuracy == null ? "" : `${pct(week.accuracy)} de acerto`],
      ["Tempo de estudo", duration(week.studySeconds), `${week.activeDays} ${week.activeDays === 1 ? "dia" : "dias"} com estudo`],
      ["Sequência", `${week.streak} ${week.streak === 1 ? "dia" : "dias"}`, "seguidos estudando"],
      ["Prontidão", change(week.readiness, value => pct(value)), "para a prova-alvo"],
      ["Acertos estimados", change(week.estimate, value => `${Math.round(value)}/${week.estimate.questions}`), "na 1ª fase"],
    ].map(([label, value, hint]) => `<div class="rp-week-tile"><span>${label}</span><strong>${value}</strong><small>${hint}</small></div>`).join("");
    const list = (items, render) => items.length ? `<ul>${items.map(render).join("")}</ul>` : `<p class="muted">—</p>`;
    const improved = list(week.improved, item => `<li><b>${esc(item.name)}</b> <small>${esc(item.subject)} · ${pct(item.from)} → ${pct(item.to)}</small></li>`);
    const consolidated = list(week.consolidated, item => `<li>${esc(item.name)} <small>${esc(item.subject)}</small></li>`);
    const reasons = Object.entries(week.reasons || {});
    const ladders = week.ladders || { active: [], done: [] };
    const extras = [
      reasons.length ? `Erros mais comuns: ${reasons.map(([reason, count]) => `${esc(REASON_TEXT[reason] || reason)} (${count})`).join(", ")}.` : "",
      ladders.done.length ? `${ladders.done.length} ${ladders.done.length === 1 ? "trilha concluída" : "trilhas concluídas"}.` : "",
      ladders.active.length ? `${ladders.active.length} ${ladders.active.length === 1 ? "trilha em andamento" : "trilhas em andamento"}: ${ladders.active.slice(0, 3).map(item => `${esc(item.name)} (nível ${item.level}/4)`).join(", ")}.` : "",
    ].filter(Boolean).map(text => `<p>${text}</p>`).join("");
    return `<section class="panel rp-panel rp-week" aria-labelledby="rp-week-title"><div class="rp-panel-head"><div><h2 id="rp-week-title">Resumo da semana</h2><p>Últimos 7 dias, comparados com uma semana atrás.</p></div></div>
      <div class="rp-week-tiles">${tiles}</div>
      <div class="rp-week-cols"><div><h3>Mais evoluíram</h3>${improved}</div><div><h3>Consolidados na semana</h3>${consolidated}</div></div>
      ${extras ? `<div class="rp-week-extras">${extras}</div>` : ""}</section>`;
  }

  // Confiança declarada ao responder: certeza, dúvida, chute e erro, mais o ritmo.
  const CONFIDENCE_BUCKETS = [["sure", "Certeza"], ["doubt", "Dúvida"], ["guess", "Chute"], ["wrong", "Erro"]];
  function clock(seconds) {
    const total = Math.round(seconds);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }
  function proficiencyPanel(data) {
    const subjects = (data?.subjects || []).filter(item => Object.values(item.counts).some(Boolean) || item.timed);
    if (!subjects.length) return "";
    const pace = data.paceSeconds;
    const rows = subjects.map(item => {
      const total = Object.values(item.counts).reduce((sum, value) => sum + value, 0);
      const bar = CONFIDENCE_BUCKETS.map(([key, label]) => item.counts[key]
        ? `<i class="rp-conf-${key}" style="width:${(item.counts[key] / total) * 100}%" title="${label}: ${item.counts[key]}"></i>` : "").join("");
      const calibration = item.calibration == null ? "—" : pct(item.calibration);
      const time = item.avgSeconds == null ? "—"
        : `<span class="${item.avgSeconds > pace ? "rp-over-pace" : ""}">${clock(item.avgSeconds)}</span>${item.overPace ? ` <small>${intFmt(item.overPace)} acima</small>` : ""}`;
      return `<div class="rp-conf-row"><strong>${esc(item.subject)}</strong><span class="rp-conf-bar" role="img" aria-label="${CONFIDENCE_BUCKETS.map(([key, label]) => `${label} ${item.counts[key]}`).join(", ")}">${bar}</span><span><span class="rp-col-label">Certeza confirmada </span>${calibration}</span><span><span class="rp-col-label">Tempo médio </span>${time}</span></div>`;
    }).join("");
    const legend = CONFIDENCE_BUCKETS.map(([key, label]) => `<span><i class="rp-conf-${key}"></i>${label}</span>`).join("");
    return `<section class="panel rp-panel rp-confidence" aria-labelledby="rp-confidence-title"><div class="rp-panel-head"><div><h2 id="rp-confidence-title">Confiança e ritmo por matéria</h2><p>Como você respondeu: acerto com certeza, com dúvida, no chute ou erro. "Certeza confirmada" é quanto você acerta quando diz ter certeza. O ritmo da 1ª fase é ${clock(pace)} por questão.</p></div><div class="rp-conf-legend">${legend}</div></div><div class="rp-conf-head" aria-hidden="true"><span>Matéria</span><span>Respostas</span><span>Certeza confirmada</span><span>Tempo médio</span></div>${rows}</section>`;
  }

  // ---------------------------------------------------------------- tree
  function matchesStatus(id) {
    if (state.status === "all") return true;
    if (state.matchCache.has(id)) return state.matchCache.get(id);
    const node = nodeById(id);
    const result = Boolean(node) && (node.status === state.status || (node.children || []).some(child => matchesStatus(child)));
    state.matchCache.set(id, result);
    return result;
  }

  function sortedChildren(ids, isRoot) {
    const list = (ids || []).filter(id => nodeById(id) && matchesStatus(id));
    const byPriority = (a, b) => (nodeById(b).priority || 0) - (nodeById(a).priority || 0);
    if (state.sort === "mastery") {
      const value = id => { const node = nodeById(id); return hasEvidence(node) ? node.mastery : 2; };
      return list.sort((a, b) => value(a) - value(b) || byPriority(a, b));
    }
    if (state.sort === "weight") {
      const weight = id => { const node = nodeById(id); return (node.course_weight || 0) * (node.level === "subject" ? 1 : (node.incidence || 0)); };
      return list.sort((a, b) => weight(b) - weight(a) || byPriority(a, b));
    }
    return isRoot ? list.sort(byPriority) : list; // filhos já vêm por prioridade desc
  }

  function treeRows(ids, level, out, isRoot = false) {
    sortedChildren(ids, isRoot).forEach(id => {
      const node = nodeById(id);
      const children = node.children || [];
      const expandable = children.some(child => nodeById(child) && matchesStatus(child));
      const open = expandable && state.expanded.has(id);
      const context = state.status !== "all" && node.status !== state.status;
      const selected = state.selectedId === id;
      out.push(`<div class="rp-row${context ? " rp-row--context" : ""}${selected ? " is-selected" : ""}" role="treeitem" tabindex="0" data-node="${esc(id)}" aria-level="${level}" aria-selected="${selected}"${expandable ? ` aria-expanded="${open}"` : ""}><span class="rp-cell-name" style="--depth:${level - 1}">${expandable ? `<span class="rp-caret" data-toggle="${esc(id)}" title="${open ? "Recolher" : "Expandir"}">${open ? "▾" : "▸"}</span>` : `<span class="rp-caret rp-caret--leaf" aria-hidden="true">·</span>`}<span class="rp-name">${esc(node.name)}</span><span class="rp-level">${LEVEL_LABEL[node.level] || ""}</span></span><span class="rp-cell-bar">${masteryBar(node, { compact: true })}</span><span class="rp-cell-n">${intFmt(node.attempts)}<span class="rp-sr"> respostas</span></span><span class="rp-cell-status">${statusBadge(node.status)}</span><span class="rp-cell-spark">${sparkline(node.series, { width: 60, height: 20 })}</span><span class="rp-cell-prio">${priorityMeter(node)}</span></div>`);
      if (open) treeRows(children, level + 1, out);
    });
  }

  function breadcrumb() {
    const crumbs = [state.focusId ? `<button type="button" class="rp-crumb" data-focus="">Todas as matérias</button>` : `<span class="rp-crumb is-current" aria-current="location">Todas as matérias</span>`];
    const focus = state.focusId ? nodeById(state.focusId) : null;
    if (focus) {
      const ids = focus.path_ids || [focus.id];
      ids.forEach((id, index) => {
        const node = nodeById(id);
        if (!node) return;
        crumbs.push(index === ids.length - 1
          ? `<span class="rp-crumb is-current" aria-current="location">${esc(node.name)}</span>`
          : `<button type="button" class="rp-crumb" data-focus="${esc(id)}">${esc(node.name)}</button>`);
      });
    }
    return `<nav class="rp-breadcrumb" aria-label="Caminho na taxonomia">${crumbs.join(`<span class="rp-crumb-sep" aria-hidden="true">›</span>`)}</nav>`;
  }

  function renderTree() {
    const host = document.getElementById("rp-tree");
    if (!host || !state.overview) return;
    state.matchCache.clear();
    const top = state.focusId ? (nodeById(state.focusId)?.children || []) : (state.overview.roots || []);
    const rows = [];
    treeRows(top, 1, rows, !state.focusId);
    let note = "";
    if (state.status !== "all") {
      const count = Object.values(nodes()).filter(node => node.status === state.status).length;
      note = `<p class="rp-filter-note">${intFmt(count)} nós com situação “${esc(STATUS[state.status].label)}” no curso. Linhas esmaecidas apenas levam até eles.</p>`;
    }
    host.innerHTML = `${breadcrumb()}${note}<div class="rp-tree-head" aria-hidden="true"><span>Conteúdo</span><span>Domínio · IC 80%</span><span>Resp.</span><span>Situação</span><span>Evolução</span><span>Prioridade</span></div><div class="rp-tree-rows" role="tree" aria-label="Taxonomia de conteúdos">${rows.join("") || `<p class="rp-report-empty">Nenhum conteúdo com este filtro aqui.</p>`}</div>`;
  }

  function treePanel() {
    const statusOptions = STATUS_FILTERS.map(([value, label]) => `<option value="${value}"${state.status === value ? " selected" : ""}>${label}</option>`).join("");
    const sortOptions = SORTS.map(([value, label]) => `<option value="${value}"${state.sort === value ? " selected" : ""}>${label}</option>`).join("");
    return `<section class="panel rp-panel rp-tree-panel" aria-labelledby="rp-tree-title"><div class="rp-panel-head"><div><h2 id="rp-tree-title">Árvore de conteúdos</h2><p>Matéria → área → conteúdo → subtópico. No teclado: ↑/↓ navegam, →/← abrem e fecham, Enter mostra o detalhe.</p></div></div><div class="rp-filters"><label>Situação<select id="rp-filter-status">${statusOptions}</select></label><label>Ordenar por<select id="rp-filter-sort">${sortOptions}</select></label></div><div id="rp-tree"></div></section>`;
  }

  // ---------------------------------------------------------------- node detail
  function detailPoints(detail, node) {
    const timeline = state.overview?.timeline || [];
    if (detail && Array.isArray(detail.series) && detail.series.length) {
      return detail.series.map((point, index) => {
        const session = timeline.find(item => item.id === point.attempt_id);
        const tested = isNum(point.mastery) && point.attempts !== 0;
        return {
          t: parseDate(point.t), y: tested ? point.mastery : null,
          lo: tested ? point.lower : null, hi: tested ? point.upper : null, kind: point.kind,
          title: `${session?.label || KIND_LABEL[point.kind] || `Ponto ${index + 1}`} · ${dateLong(point.t)}`,
          lines: [tested && isNum(point.lower) && isNum(point.upper) ? `IC 80%: ${pct(point.lower)}–${pct(point.upper)}` : "", isNum(point.attempts) ? `${intFmt(point.attempts)} resposta(s) acumulada(s)` : ""],
        };
      });
    }
    const series = Array.isArray(node?.series) ? node.series : [];
    return timeline.map((point, index) => ({
      t: parseDate(point.at), y: isNum(series[index]) ? series[index] : null, kind: point.kind,
      title: `${point.label || KIND_LABEL[point.kind] || "Sessão"} · ${dateLong(point.at)}`, lines: [],
    }));
  }

  function answersTable(detail) {
    const list = (Array.isArray(detail.recent_answers) ? detail.recent_answers : []).slice().reverse();
    if (!list.length) return `<h4>Respostas recentes</h4><p class="rp-report-empty">Nenhuma resposta registrada neste conteúdo.</p>`;
    const rows = list.map(answer => {
      const result = answer.correct
        ? `<span class="rp-result rp-result--ok"><span aria-hidden="true">✓</span> Acertou</span>`
        : `<span class="rp-result rp-result--bad"><span aria-hidden="true">✗</span> Errou</span>${answer.correct_answer ? `<small class="rp-kind">gabarito ${esc(answer.correct_answer)}${answer.selected ? ` · marcou ${esc(answer.selected)}` : ""}</small>` : ""}`;
      const reason = answer.correct ? "—" : esc(REASONS[answer.reason] || (answer.reason ? answer.reason : "Não informado"));
      return `<tr><td><strong>${esc(examLabel(answer.source_id))}${answer.number != null ? ` · Q${esc(answer.number)}` : ""}</strong><small class="rp-kind">${dateShort(answer.at)} · ${esc(KIND_LABEL[answer.kind] || "")}</small>${answer.topic ? `<small class="rp-topic">${esc(answer.topic)}</small>` : ""}</td><td>${result}</td><td>${reason}</td></tr>`;
    }).join("");
    return `<h4>Respostas recentes</h4><div class="rp-table-wrap"><table class="subject-table rp-table rp-answers"><thead><tr><th>Questão</th><th>Resultado</th><th>Motivo</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function detailHtml() {
    const node = nodeById(state.selectedId) || (state.detail?.node?.id === state.selectedId ? state.detail.node : null);
    if (!node) return `<div class="empty-state rp-detail-empty"><div class="empty-icon">◎</div><strong>Selecione um conteúdo</strong><p>Clique em uma matéria, área ou conteúdo para ver a evolução do domínio e as últimas respostas.</p></div>`;
    const detail = state.detail && state.detail.node?.id === node.id ? state.detail : null;
    const shown = detail ? { ...node, ...detail.node } : node;
    const crumbs = (shown.path_ids || []).slice(0, -1).map((id, index) => {
      const name = shown.path?.[index] || nodeById(id)?.name || id;
      return nodeById(id) ? `<button type="button" class="rp-crumb" data-node-link="${esc(id)}">${esc(name)}</button>` : `<span class="rp-crumb">${esc(name)}</span>`;
    }).join(`<span class="rp-crumb-sep" aria-hidden="true">›</span>`);
    const stats = [
      ["Domínio", hasEvidence(shown) ? `${pct(shown.mastery)} <small>IC ${pct(shown.lower)}–${pct(shown.upper)}</small>` : "sem dados"],
      ["Respostas", `${intFmt(shown.attempts)} <small>${intFmt(shown.correct)} acerto(s)</small>`],
      ["Peso da matéria", pct(shown.course_weight)],
      ["Incidência na matéria", pct(shown.incidence)],
      ["Variação", deltaChip(shown.delta)],
    ].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
    const points = detailPoints(detail, shown);
    const showTable = state.tables.detail;
    const tableRows = points.map(point => `<tr><td>${esc(point.title)}</td><td class="rp-num">${isNum(point.y) ? pct(point.y) : "—"}</td><td class="rp-num">${isNum(point.lo) && isNum(point.hi) && isNum(point.y) ? `${pct(point.lo)}–${pct(point.hi)}` : "—"}</td></tr>`).join("");
    const childNodes = detail && Array.isArray(detail.children) ? detail.children : (shown.children || []).map(nodeById).filter(Boolean);
    const children = childNodes.length ? `<h4>Subdivisões</h4><ul class="rp-child-list">${childNodes.map(child => `<li><button type="button" class="rp-child" data-node-link="${esc(child.id)}"><span class="rp-child-name">${esc(child.name)}</span>${masteryBar(child, { compact: true })}${statusBadge(child.status)}</button></li>`).join("")}</ul>` : "";
    let answers = "";
    if (detail) answers = answersTable(detail);
    else if (state.detailLoading) answers = `<h4>Respostas recentes</h4><p class="rp-report-empty">Carregando respostas…</p>`;
    else if (state.detailError) answers = `<h4>Respostas recentes</h4><p class="rp-report-empty">Não foi possível carregar o detalhe: ${esc(state.detailError)}</p>`;
    return `<div class="rp-detail-inner${state.detailLoading ? " is-loading" : ""}"><div class="rp-detail-head"><div>${crumbs ? `<nav class="rp-breadcrumb rp-breadcrumb--small" aria-label="Caminho do conteúdo">${crumbs}</nav>` : ""}<h3 id="rp-detail-title" tabindex="-1">${esc(shown.name)}</h3><div class="rp-detail-tags"><span class="rp-level">${LEVEL_LABEL[shown.level] || ""}</span>${statusBadge(shown.status)}</div></div><button type="button" class="icon-button" data-detail-close title="Fechar detalhe" aria-label="Fechar detalhe">×</button></div><div class="rp-detail-stats">${stats}</div><div class="rp-panel-head rp-panel-head--sub"><h4>Domínio ao longo do tempo</h4><button type="button" class="text-button" data-table-toggle="detail" aria-pressed="${showTable}">${showTable ? "Ver gráfico" : "Ver tabela"}</button></div>${showTable ? "" : chartLegend({ band: Boolean(detail), lineLabel: "Domínio estimado" })}<div id="rp-detail-chart" class="${showTable ? "hidden" : ""}"></div><div class="rp-table-wrap ${showTable ? "" : "hidden"}"><table class="subject-table rp-table"><thead><tr><th>Sessão</th><th class="rp-num">Domínio</th><th class="rp-num">IC 80%</th></tr></thead><tbody>${tableRows}</tbody></table></div>${children}${answers}</div>`;
  }

  function renderDetail() {
    const host = document.getElementById("rp-detail");
    if (!host) return;
    host.innerHTML = detailHtml();
    host.classList.toggle("is-empty", !state.selectedId);
    const node = nodeById(state.selectedId);
    const chartHost = document.getElementById("rp-detail-chart");
    if (node && chartHost && !state.tables.detail) {
      const detail = state.detail && state.detail.node?.id === node.id ? state.detail : null;
      mountLineChart(chartHost, detailPoints(detail, node), { yDomain: [0, 1], band: Boolean(detail), height: 190, valueLabel: "Domínio", ariaLabel: `Domínio estimado de ${node.name} por sessão` });
    }
  }

  async function selectNode(id, { reveal = false, scroll = false } = {}) {
    if (!nodeById(id)) return;
    const node = nodeById(id);
    if (state.selectedId !== id) state.detail = null;
    state.selectedId = id;
    state.detailError = null;
    if (reveal) {
      const path = node.path_ids || [id];
      if (state.focusId && !path.includes(state.focusId)) state.focusId = null;
      path.slice(0, -1).forEach(ancestor => state.expanded.add(ancestor));
      if (state.status !== "all" && node.status !== state.status) {
        state.status = "all";
        const select = document.getElementById("rp-filter-status");
        if (select) select.value = "all";
      }
    }
    state.detailLoading = true;
    renderTree();
    renderDetail();
    if (scroll) {
      const panel = document.getElementById("rp-detail");
      const row = document.querySelector(`.rp-row[data-node="${cssId(id)}"]`);
      const stacked = window.matchMedia("(max-width: 1050px)").matches;
      (stacked ? panel : (row || panel))?.scrollIntoView({ behavior: "smooth", block: stacked ? "start" : "center" });
    }
    const seq = ++state.requestSeq;
    try {
      const detail = await fetchJson(`/api/reports/nodes/${encodeURIComponent(id)}?courseId=${encodeURIComponent(state.courseId)}`);
      if (seq !== state.requestSeq) return;
      state.detail = detail && detail.node ? detail : null;
    } catch (error) {
      if (seq !== state.requestSeq) return;
      state.detail = null;
      state.detailError = error.message;
    }
    state.detailLoading = false;
    renderDetail();
  }

  // ---------------------------------------------------------------- page render
  function emptyHtml(overview) {
    return `${weightsNote(overview?.course)}<div class="panel rp-panel"><div class="empty-state rp-empty"><div class="empty-icon">↗</div><strong>Ainda não há respostas para este curso</strong><p>O relatório acompanha seu domínio por conteúdo a partir das suas respostas. Treine, faça um diagnóstico ou responda a uma prova do acervo: cada dia de Treinamento e cada sessão viram um ponto na linha do tempo.</p><div class="rp-empty-actions"><button type="button" class="button button-primary" data-rp-go="diagnostics">Fazer um diagnóstico</button><button type="button" class="button button-secondary" data-rp-go="exams">Responder uma prova</button></div></div></div>`;
  }

  function renderPage() {
    const root = document.getElementById("reports-root");
    if (!root) return;
    const overview = state.overview;
    root.classList.remove("is-refreshing");
    if (state.error && !overview) {
      root.innerHTML = `<div class="panel rp-panel"><div class="inline-result error">Não foi possível carregar os relatórios: ${esc(state.error)}</div><button type="button" class="button button-secondary rp-retry" data-rp-retry>Tentar novamente</button></div>`;
      return;
    }
    if (!overview) { root.innerHTML = `<div class="loading-card">Carregando relatórios…</div>`; return; }
    const nameEl = document.getElementById("reports-course-name");
    if (nameEl) nameEl.textContent = overview.course?.name ? `${overview.course.name}. Domínio estimado por conteúdo, ponderado pelo peso de cada matéria no curso.` : "Domínio estimado por conteúdo da taxonomia.";
    const timeline = Array.isArray(overview.timeline) ? overview.timeline : [];
    if (!timeline.length) { root.innerHTML = emptyHtml(overview) + weekPanel(state.week) + proficiencyPanel(state.proficiency); return; }
    const report = overview.latest_report ? `<section class="panel rp-panel rp-latest" aria-labelledby="rp-latest-title"><div class="rp-panel-head"><div><h2 id="rp-latest-title">Último relatório de diagnóstico</h2><p>Gerado ao concluir o diagnóstico mais recente.</p></div></div>${renderStudyReport(overview.latest_report, { interactive: true, title: false })}</section>` : "";
    root.innerHTML = `${weightsNote(overview.course)}${weekPanel(state.week)}${kpiRow(overview.kpis, timeline)}<div class="rp-grid-2">${evolutionPanel(timeline)}${prioritiesPanel(overview)}</div>${subjectsPanel(overview)}${proficiencyPanel(state.proficiency)}<div class="rp-explorer">${treePanel()}<aside class="panel rp-panel rp-detail" id="rp-detail" aria-live="polite" aria-label="Detalhe do conteúdo"></aside></div>${report}`;
    drawEvolution();
    renderTree();
    renderDetail();
  }

  function drawEvolution() {
    const host = document.getElementById("rp-evolution-chart");
    const timeline = state.overview?.timeline || [];
    if (!host || state.tables.evolution || !timeline.length) return;
    const points = timeline.map(point => ({
      t: parseDate(point.at), y: isNum(point.estimated_score) ? point.estimated_score : null, kind: point.kind,
      title: `${point.label || KIND_LABEL[point.kind] || "Sessão"} · ${dateLong(point.at)}`,
      lines: [`${intFmt(point.answered)} respostas · ${pct(point.accuracy)} de acerto na sessão`],
    }));
    mountLineChart(host, points, { height: 250, valueLabel: "Nota", ariaLabel: "Nota ponderada estimada por sessão" });
  }

  // ---------------------------------------------------------------- data loading
  async function loadCourses() {
    if (state.courses.length) return;
    try { state.courses = await fetchJson("/api/courses"); } catch { state.courses = []; }
    let saved = null;
    try { saved = localStorage.getItem(COURSE_KEY); } catch { saved = null; }
    const ids = state.courses.map(course => course.id);
    state.courseId = [saved, DEFAULT_COURSE].find(id => id && ids.includes(id)) || ids[0] || DEFAULT_COURSE;
    const select = document.getElementById("reports-course");
    if (select) {
      select.innerHTML = state.courses.length
        ? state.courses.map(course => `<option value="${esc(course.id)}"${course.id === state.courseId ? " selected" : ""}>${esc(course.name)}</option>`).join("")
        : `<option value="${esc(state.courseId)}">${esc(state.courseId)}</option>`;
    }
  }

  async function refreshReports() {
    const root = document.getElementById("reports-root");
    if (!root) return;
    await loadCourses();
    if (state.overview) root.classList.add("is-refreshing"); // mantém o quadro anterior enquanto recarrega
    else renderPage();
    state.error = null;
    const courseId = state.courseId;
    try {
      const [overview, proficiency, week] = await Promise.all([
        fetchJson(`/api/reports/overview?courseId=${encodeURIComponent(courseId)}`),
        fetchJson(`/api/study/proficiency?courseId=${encodeURIComponent(courseId)}`).catch(() => null),
        fetchJson(`/api/study/summary?period=week&courseId=${encodeURIComponent(courseId)}`).catch(() => null),
      ]);
      if (courseId !== state.courseId) return;
      state.proficiency = proficiency;
      state.week = week;
      const courseChanged = state.overview?.course?.id !== overview?.course?.id;
      state.overview = overview;
      state.maxPriority = Math.max(0, ...Object.values(overview.nodes || {}).map(node => isNum(node.priority) ? node.priority : 0));
      if (courseChanged) { state.focusId = null; state.expanded.clear(); state.selectedId = null; state.detail = null; }
      if (state.selectedId && !nodeById(state.selectedId)) { state.selectedId = null; state.detail = null; }
      if (state.focusId && !nodeById(state.focusId)) state.focusId = null;
      renderPage();
      if (state.selectedId) selectNode(state.selectedId);
    } catch (error) {
      if (courseId !== state.courseId) return;
      state.error = error.message;
      if (state.overview && state.overview.course?.id !== courseId) state.overview = null;
      renderPage();
      if (state.overview && typeof toast === "function") toast(`Relatórios: ${error.message}`, true);
    }
  }

  // ---------------------------------------------------------------- events (delegated, bound once)
  function focusRow(row) { if (row) { row.focus({ preventScroll: true }); row.scrollIntoView({ block: "nearest" }); } }
  const rowFor = id => document.querySelector(`.rp-row[data-node="${cssId(id)}"]`);

  function toggleNode(id, open) {
    const isOpen = state.expanded.has(id);
    const next = open == null ? !isOpen : open;
    if (next === isOpen) return;
    if (next) state.expanded.add(id); else state.expanded.delete(id);
    renderTree();
    focusRow(rowFor(id));
  }

  function bind() {
    const root = document.getElementById("reports-root");
    const select = document.getElementById("reports-course");
    if (!root || root.dataset.bound) return;
    root.dataset.bound = "1";
    select?.addEventListener("change", () => {
      state.courseId = select.value;
      try { localStorage.setItem(COURSE_KEY, state.courseId); } catch { /* sem armazenamento local */ }
      refreshReports();
    });
    root.addEventListener("click", event => {
      const target = event.target.closest("button, [data-toggle], .rp-row");
      if (!target || !root.contains(target)) return;
      if (target.matches("[data-rp-go]")) { if (typeof showView === "function") showView(target.dataset.rpGo); return; }
      if (target.matches("[data-rp-retry]")) { refreshReports(); return; }
      if (target.matches("[data-table-toggle]")) {
        const key = target.dataset.tableToggle;
        state.tables[key] = !state.tables[key];
        if (key === "evolution") {
          const panel = target.closest(".rp-evolution");
          const timeline = state.overview?.timeline || [];
          const fresh = document.createElement("div");
          fresh.innerHTML = evolutionPanel(timeline);
          panel.replaceWith(fresh.firstElementChild);
          drawEvolution();
        } else renderDetail();
        document.querySelector(`[data-table-toggle="${key}"]`)?.focus();
        return;
      }
      if (target.matches("[data-detail-close]")) { state.selectedId = null; state.detail = null; state.requestSeq++; renderTree(); renderDetail(); return; }
      if (target.matches("[data-focus]")) { state.focusId = target.dataset.focus || null; renderTree(); return; }
      if (target.matches("[data-subject]")) {
        const id = target.dataset.subject;
        state.focusId = id;
        selectNode(id);
        document.querySelector(".rp-tree-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if (target.matches("[data-node-link]")) { selectNode(target.dataset.nodeLink, { reveal: true, scroll: true }); return; }
      if (target.matches("[data-toggle]")) { toggleNode(target.dataset.toggle); return; }
      if (target.matches(".rp-row")) {
        const id = target.dataset.node;
        if (target.getAttribute("aria-expanded") === "false") state.expanded.add(id);
        selectNode(id).catch(() => {});
        focusRow(rowFor(id));
      }
    });
    root.addEventListener("keydown", event => {
      const row = event.target.closest?.(".rp-row");
      if (!row) return;
      const id = row.dataset.node;
      const rows = [...root.querySelectorAll(".rp-row")];
      const index = rows.indexOf(row);
      if (event.key === "ArrowDown") { event.preventDefault(); focusRow(rows[index + 1]); }
      else if (event.key === "ArrowUp") { event.preventDefault(); focusRow(rows[index - 1]); }
      else if (event.key === "Home") { event.preventDefault(); focusRow(rows[0]); }
      else if (event.key === "End") { event.preventDefault(); focusRow(rows[rows.length - 1]); }
      else if (event.key === "ArrowRight") {
        event.preventDefault();
        if (row.getAttribute("aria-expanded") === "false") toggleNode(id, true);
        else if (row.getAttribute("aria-expanded") === "true") focusRow(rows[index + 1]);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (row.getAttribute("aria-expanded") === "true") toggleNode(id, false);
        else { const parent = nodeById(id)?.parent_id; if (parent) focusRow(rowFor(parent)); }
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(id).catch(() => {});
        focusRow(rowFor(id));
      }
    });
    root.addEventListener("change", event => {
      if (event.target.id === "rp-filter-status") { state.status = event.target.value; renderTree(); }
      if (event.target.id === "rp-filter-sort") { state.sort = event.target.value; renderTree(); }
    });
    let resizeTimer;
    let lastWidth = window.innerWidth;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (Math.abs(window.innerWidth - lastWidth) < 4) return;
        lastWidth = window.innerWidth;
        if (!document.getElementById("view-reports")?.classList.contains("active")) return;
        drawEvolution();
        renderDetail();
      }, 150);
    });
  }

  window.renderStudyReport = renderStudyReport;
  window.refreshReports = () => { bind(); return refreshReports(); };
  window.studaiReports = { state, selectNode };
  if (document.getElementById("view-reports")?.classList.contains("active")) window.refreshReports();
})();
