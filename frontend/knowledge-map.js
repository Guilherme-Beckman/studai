/**
 * StudAI · Mapa do conhecimento (Fase 7)
 * Grafo de forças em canvas, sem dependências: busca /api/knowledge-map e
 * /api/knowledge-map/nodes/<id> (contrato em src/studai/features/reports/CONTRACT.md).
 * Cor = status do domínio; tamanho e brilho = profundidade (amplitude × domínio).
 */
(function () {
  "use strict";

  const DEFAULT_COURSE = "eng-comp-sao-carlos";
  const COURSE_KEY = "studai-map-course";
  const STATUS = {
    strong: { label: "Forte", icon: "✓", color: "--rp-good" },
    uncertain: { label: "Incerto", icon: "?", color: "--rp-warn" },
    gap: { label: "Lacuna", icon: "!", color: "--rp-crit" },
    untested: { label: "Não testado", icon: "–", color: "--rp-untested" },
  };
  const TIER_LABEL = ["matéria", "área", "conteúdo", "subconteúdo"];
  const KIND_LABEL = { diagnostic: "Diagnóstico", exam: "Prova", daily: "Hoje" };
  const REASONS = { unknown_content: "Não sabia o conteúdo", concept_confusion: "Confundiu um detalhe", calc_error: "Erro de cálculo", misread: "Interpretação", trap: "Pegadinha", time_pressure: "Faltou tempo", blank: "Em branco" };
  const BASE_RADIUS = [22, 12, 7.5, 4.5];
  const LINK_LENGTH = [0, 120, 58, 30];
  const LABEL_ZOOM = [0, 0.35, 1.1, 2.2];
  const CUTOFF = 150;
  const ALPHA_MIN = 0.004;

  const state = {
    bound: false, courses: [], courseId: null, data: null, error: null,
    byId: new Map(), children: new Map(), tierOf: new Map(),
    expanded: new Set(), showAll: false, subject: "", query: "", matches: [],
    selectedId: null, hoverId: null, detail: null, detailError: null, requestSeq: 0,
  };
  const sim = { nodes: new Map(), visible: [], alpha: 0, target: 0, running: false, anchors: new Map() };
  const view = { k: 1, tx: 0, ty: 0, width: 0, height: 0, dpr: 1 };
  let canvas = null, ctx = null, colors = {}, pointer = null;

  // ---------------------------------------------------------------- helpers
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const isNum = value => typeof value === "number" && Number.isFinite(value);
  const pctFmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const pct = value => isNum(value) ? `${pctFmt.format(value * 100)}%` : "—";
  const fold = text => String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const $ = selector => document.querySelector(selector);

  async function fetchJson(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error((data && data.error) || `Falha HTTP ${response.status}`);
    return data;
  }

  function readColors() {
    const style = getComputedStyle(document.documentElement);
    const get = name => style.getPropertyValue(name).trim();
    colors = { ink: get("--ink"), muted: get("--muted"), line: get("--line"), paper: get("--paper"), blue: get("--blue") };
    for (const [key, info] of Object.entries(STATUS)) colors[key] = get(info.color);
  }

  function withAlpha(color, alpha) {
    if (/^#[0-9a-f]{6}$/i.test(color)) {
      const n = parseInt(color.slice(1), 16);
      return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha})`;
    }
    return color;
  }

  function statusBadge(status) {
    const key = STATUS[status] ? status : "untested";
    return `<span class="rp-status rp-status--${key}"><span class="rp-status-icon" aria-hidden="true">${STATUS[key].icon}</span>${STATUS[key].label}</span>`;
  }

  function depthBar(value) {
    return `<span class="km-bar" aria-hidden="true"><span style="width:${Math.round(Math.max(0, Math.min(1, value || 0)) * 100)}%"></span></span>`;
  }

  // ---------------------------------------------------------------- graph model
  function indexGraph(data) {
    state.byId = new Map(data.nodes.map(node => [node.id, node]));
    state.children = new Map(data.nodes.map(node => [node.id, []]));
    for (const edge of data.edges) state.children.get(edge.source)?.push(edge.target);
    state.tierOf = new Map();
    const tier = id => {
      if (state.tierOf.has(id)) return state.tierOf.get(id);
      const node = state.byId.get(id);
      const value = node && node.parent_id && state.byId.has(node.parent_id) ? Math.min(3, tier(node.parent_id) + 1) : 0;
      state.tierOf.set(id, value);
      return value;
    };
    data.nodes.forEach(node => tier(node.id));
  }

  const tierOf = id => state.tierOf.get(id) ?? 0;
  const roots = () => (state.data?.nodes || []).filter(node => tierOf(node.id) === 0);

  function pathOf(id) {
    const path = [];
    for (let node = state.byId.get(id); node; node = state.byId.get(node.parent_id)) path.unshift(node);
    return path;
  }

  function isVisible(node) {
    if (state.subject && node.subject !== state.subject) return false;
    const tier = tierOf(node.id);
    return tier < 3 || state.showAll || state.expanded.has(node.parent_id);
  }

  function descendants(id) {
    let total = 1;
    for (const child of state.children.get(id) || []) total += descendants(child);
    return total;
  }

  /** Posições iniciais radiais e determinísticas: um setor por matéria. */
  function seedLayout() {
    sim.nodes.clear();
    sim.anchors.clear();
    const subjects = roots().filter(node => !state.subject || node.subject === state.subject);
    const sizes = subjects.map(node => Math.sqrt(descendants(node.id)));
    const total = sizes.reduce((a, b) => a + b, 0) || 1;
    const ring = subjects.length > 1 ? 260 + 26 * subjects.length : 0;
    let angle = -Math.PI / 2;
    subjects.forEach((subject, index) => {
      const span = (sizes[index] / total) * Math.PI * 2;
      const mid = angle + span / 2;
      const anchor = { x: Math.cos(mid) * ring, y: Math.sin(mid) * ring };
      sim.anchors.set(subject.id, anchor);
      place(subject.id, anchor.x, anchor.y, mid, Math.min(span, Math.PI * 1.2), 0);
      angle += span;
    });
  }

  function place(id, x, y, direction, span, tier) {
    sim.nodes.set(id, { id, x, y, vx: 0, vy: 0, fx: null, fy: null });
    const kids = state.children.get(id) || [];
    if (!kids.length) return;
    const weights = kids.map(child => Math.sqrt(descendants(child)));
    const total = weights.reduce((a, b) => a + b, 0) || 1;
    const childSpan = Math.max(span, tier === 0 ? Math.PI * 2 : 0);
    let angle = direction - childSpan / 2;
    kids.forEach((child, index) => {
      const share = (weights[index] / total) * childSpan;
      const mid = angle + share / 2;
      const length = LINK_LENGTH[Math.min(3, tier + 1)] * (tier === 0 ? 1.3 : 1);
      place(child, x + Math.cos(mid) * length, y + Math.sin(mid) * length, mid, Math.min(share * 1.6, Math.PI), tier + 1);
      angle += share;
    });
  }

  function refreshVisible() {
    sim.visible = (state.data?.nodes || []).filter(isVisible).map(node => {
      const body = sim.nodes.get(node.id);
      body.node = node;
      body.tier = tierOf(node.id);
      body.r = BASE_RADIUS[body.tier] * (0.7 + 0.6 * (node.depth || 0));
      return body;
    });
    sim.visibleIds = new Set(sim.visible.map(body => body.id));
  }

  // ---------------------------------------------------------------- simulation
  function tick() {
    const alpha = sim.alpha;
    const bodies = sim.visible;
    for (const body of bodies) {
      const parent = body.node.parent_id && sim.visibleIds.has(body.node.parent_id) ? sim.nodes.get(body.node.parent_id) : null;
      if (!parent) {
        const anchor = sim.anchors.get(body.id);
        if (anchor) { body.vx += (anchor.x - body.x) * 0.02 * alpha; body.vy += (anchor.y - body.y) * 0.02 * alpha; }
        continue;
      }
      const dx = body.x - parent.x, dy = body.y - parent.y;
      const distance = Math.hypot(dx, dy) || 0.01;
      const pull = ((distance - LINK_LENGTH[body.tier]) / distance) * alpha * 0.5;
      body.vx -= dx * pull * 0.85; body.vy -= dy * pull * 0.85;
      parent.vx += dx * pull * 0.15; parent.vy += dy * pull * 0.15;
    }
    // Repulsão e colisão por grade espacial: só pares a menos de CUTOFF.
    const grid = new Map();
    for (const body of bodies) {
      const key = `${Math.floor(body.x / CUTOFF)},${Math.floor(body.y / CUTOFF)}`;
      (grid.get(key) || grid.set(key, []).get(key)).push(body);
    }
    for (const [key, cell] of grid) {
      const [gx, gy] = key.split(",").map(Number);
      for (let ox = -1; ox <= 1; ox++) for (let oy = -1; oy <= 1; oy++) {
        const other = grid.get(`${gx + ox},${gy + oy}`);
        if (!other) continue;
        for (const a of cell) for (const b of other) {
          if (a.id >= b.id) continue;
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 > CUTOFF * CUTOFF) continue;
          if (d2 < 0.01) { dx = (a.id < b.id ? 1 : -1) * 0.1; dy = 0.1; d2 = 0.02; }
          const distance = Math.sqrt(d2);
          const push = (alpha * 45 * (a.r + b.r)) / d2;
          a.vx -= dx * push / distance; a.vy -= dy * push / distance;
          b.vx += dx * push / distance; b.vy += dy * push / distance;
          const gap = a.r + b.r + 3;
          if (distance < gap) {
            const shift = ((gap - distance) / distance) * 0.5;
            a.x -= dx * shift; a.y -= dy * shift; b.x += dx * shift; b.y += dy * shift;
          }
        }
      }
    }
    for (const body of bodies) {
      if (body.fx !== null) { body.x = body.fx; body.y = body.fy; body.vx = body.vy = 0; continue; }
      body.vx *= 0.6; body.vy *= 0.6;
      body.x += body.vx; body.y += body.vy;
    }
    sim.alpha += (sim.target - sim.alpha) * 0.025;
  }

  function reheat(alpha = 0.6) {
    sim.alpha = Math.max(sim.alpha, alpha);
    start();
  }

  function start() {
    if (sim.running) return;
    sim.running = true;
    requestAnimationFrame(frame);
  }

  function frame() {
    if (!isActive()) { sim.running = false; return; }
    if (sim.alpha >= ALPHA_MIN || sim.target > 0) tick();
    draw();
    if (sim.alpha >= ALPHA_MIN || sim.target > 0) requestAnimationFrame(frame);
    else sim.running = false;
  }

  function requestDraw() {
    if (!sim.running) requestAnimationFrame(() => { if (!sim.running) draw(); });
  }

  // ---------------------------------------------------------------- drawing
  function neighbors(id) {
    const node = state.byId.get(id);
    const set = new Set([id, ...(state.children.get(id) || [])]);
    if (node?.parent_id) set.add(node.parent_id);
    return set;
  }

  function draw() {
    if (!ctx || !view.width) return;
    const { k, tx, ty, dpr } = view;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, view.width, view.height);
    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * tx, dpr * ty);
    const focus = state.selectedId || state.hoverId;
    const near = focus ? neighbors(focus) : null;
    const matched = state.matches.length ? new Set(state.matches) : null;

    ctx.lineWidth = 1 / k;
    for (const body of sim.visible) {
      const parentId = body.node.parent_id;
      if (!parentId || !sim.visibleIds.has(parentId)) continue;
      const parent = sim.nodes.get(parentId);
      const lit = near && near.has(body.id) && near.has(parentId) && (body.id === focus || parentId === focus);
      ctx.strokeStyle = lit ? colors.blue : withAlpha(colors.muted, near ? 0.12 : 0.32);
      ctx.lineWidth = (lit ? 2 : 1) / k;
      ctx.beginPath(); ctx.moveTo(parent.x, parent.y); ctx.lineTo(body.x, body.y); ctx.stroke();
    }

    for (const body of sim.visible) {
      const node = body.node;
      const color = colors[node.status] || colors.untested;
      const dim = (near && !near.has(body.id)) || (matched && !matched.has(body.id));
      ctx.globalAlpha = dim ? 0.22 : 1;
      if (node.depth > 0.02 && !dim) {
        ctx.beginPath(); ctx.arc(body.x, body.y, body.r * (1.5 + 1.6 * node.depth), 0, Math.PI * 2);
        ctx.fillStyle = withAlpha(color, 0.1 + 0.3 * node.depth); ctx.fill();
      }
      ctx.beginPath(); ctx.arc(body.x, body.y, body.r, 0, Math.PI * 2);
      if (node.status === "untested") {
        ctx.fillStyle = colors.paper; ctx.fill();
        ctx.strokeStyle = color; ctx.lineWidth = 1.5 / k; ctx.stroke();
      } else {
        ctx.fillStyle = withAlpha(color, 0.45 + 0.55 * Math.min(1, node.depth * 1.5)); ctx.fill();
      }
      const hidden = body.tier === 2 && !state.showAll && !state.expanded.has(body.id) && (state.children.get(body.id) || []).length;
      if (hidden) {
        ctx.beginPath(); ctx.arc(body.x, body.y, body.r + 2.5 / k + 1, 0, Math.PI * 2);
        ctx.setLineDash([2 / k, 2 / k]); ctx.strokeStyle = withAlpha(colors.muted, 0.8); ctx.lineWidth = 1 / k; ctx.stroke(); ctx.setLineDash([]);
      }
      if (body.id === state.selectedId) {
        ctx.beginPath(); ctx.arc(body.x, body.y, body.r + 4 / k, 0, Math.PI * 2);
        ctx.strokeStyle = colors.blue; ctx.lineWidth = 2.5 / k; ctx.stroke();
      }
    }

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (const body of sim.visible) {
      // Destacados ganham rótulo antes do zoom normal, mas não com o grafo todo à vista.
      const emphasized = body.id === focus || (((near && near.has(body.id)) || (matched && matched.has(body.id)))
                                               && k >= LABEL_ZOOM[body.tier] / 3);
      if (!emphasized && k < LABEL_ZOOM[body.tier]) continue;
      if ((near && !near.has(body.id) && !(matched && matched.has(body.id))) && body.tier > 0) continue;
      const size = (body.tier === 0 ? 13 : body.tier === 1 ? 11.5 : 10.5) / k;
      ctx.font = `${body.tier < 2 ? "700" : "400"} ${size}px "Schibsted Grotesk", "Segoe UI", system-ui, sans-serif`;
      ctx.globalAlpha = near && !near.has(body.id) ? 0.4 : 1;
      ctx.lineWidth = 3 / k; ctx.strokeStyle = colors.paper; ctx.lineJoin = "round";
      const label = body.node.name.length > 34 && body.id !== focus ? `${body.node.name.slice(0, 32)}…` : body.node.name;
      ctx.strokeText(label, body.x, body.y + body.r + 3 / k);
      ctx.fillStyle = colors.ink;
      ctx.fillText(label, body.x, body.y + body.r + 3 / k);
    }
    ctx.globalAlpha = 1;
  }

  // ---------------------------------------------------------------- camera
  function resize() {
    if (!canvas) return;
    const rect = canvas.parentElement.getBoundingClientRect();
    if (!rect.width) return;
    const first = !view.width;
    view.dpr = window.devicePixelRatio || 1;
    view.width = rect.width; view.height = rect.height;
    canvas.width = Math.round(rect.width * view.dpr); canvas.height = Math.round(rect.height * view.dpr);
    canvas.style.width = `${rect.width}px`; canvas.style.height = `${rect.height}px`;
    if (first) fit(); else draw();
  }

  function fit(ids, maxZoom = 2.5) {
    const bodies = ids ? ids.map(id => sim.nodes.get(id)).filter(Boolean) : sim.visible;
    if (!bodies.length || !view.width) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const body of bodies) {
      minX = Math.min(minX, body.x - 30); maxX = Math.max(maxX, body.x + 30);
      minY = Math.min(minY, body.y - 30); maxY = Math.max(maxY, body.y + 40);
    }
    view.k = Math.max(0.12, Math.min(maxZoom, 0.92 * Math.min(view.width / (maxX - minX), view.height / (maxY - minY))));
    view.tx = view.width / 2 - ((minX + maxX) / 2) * view.k;
    view.ty = view.height / 2 - ((minY + maxY) / 2) * view.k;
    requestDraw();
  }

  function zoomAt(factor, sx = view.width / 2, sy = view.height / 2) {
    const k = Math.max(0.1, Math.min(6, view.k * factor));
    view.tx = sx - ((sx - view.tx) / view.k) * k;
    view.ty = sy - ((sy - view.ty) / view.k) * k;
    view.k = k;
    requestDraw();
  }

  /** Enquadra o nó com o pai e os filhos visíveis, para os rótulos não se sobreporem. */
  function centerOn(id) {
    const ids = [...neighbors(id)].filter(other => sim.visibleIds.has(other));
    if (ids.length) fit(ids, 1.6);
  }

  function toWorld(event) {
    const rect = canvas.getBoundingClientRect();
    const sx = event.clientX - rect.left, sy = event.clientY - rect.top;
    return { sx, sy, x: (sx - view.tx) / view.k, y: (sy - view.ty) / view.k };
  }

  function hit(point) {
    let best = null, bestDistance = Infinity;
    for (const body of sim.visible) {
      const distance = Math.hypot(body.x - point.x, body.y - point.y);
      if (distance < body.r + 5 / view.k && distance < bestDistance) { best = body; bestDistance = distance; }
    }
    return best;
  }

  // ---------------------------------------------------------------- interaction
  function bindCanvas() {
    canvas.addEventListener("pointerdown", event => {
      const point = toWorld(event);
      const body = hit(point);
      pointer = { id: event.pointerId, startX: point.sx, startY: point.sy, moved: false, body,
                  tx: view.tx, ty: view.ty };
      canvas.setPointerCapture(event.pointerId);
      if (body) { body.fx = body.x; body.fy = body.y; }
    });
    canvas.addEventListener("pointermove", event => {
      const point = toWorld(event);
      if (!pointer) {
        const body = hit(point);
        const id = body ? body.id : null;
        canvas.style.cursor = body ? "pointer" : "grab";
        if (id !== state.hoverId) { state.hoverId = id; requestDraw(); }
        tooltip(body, point);
        return;
      }
      if (Math.hypot(point.sx - pointer.startX, point.sy - pointer.startY) > 4) pointer.moved = true;
      if (!pointer.moved) return;
      tooltip(null);
      if (pointer.body) {
        pointer.body.fx = point.x; pointer.body.fy = point.y;
        sim.target = 0.25; start();
      } else {
        canvas.style.cursor = "grabbing";
        view.tx = pointer.tx + point.sx - pointer.startX;
        view.ty = pointer.ty + point.sy - pointer.startY;
        requestDraw();
      }
    });
    const release = event => {
      if (!pointer) return;
      const { body, moved } = pointer;
      pointer = null;
      sim.target = 0;
      if (body) { body.fx = null; body.fy = null; }
      canvas.style.cursor = "grab";
      if (!moved && event.type === "pointerup") {
        if (body) selectNode(body.id);
        else if (state.selectedId) { state.selectedId = null; state.detail = null; renderPanel(); requestDraw(); }
      }
    };
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", release);
    canvas.addEventListener("pointerleave", () => { if (!pointer && state.hoverId) { state.hoverId = null; tooltip(null); requestDraw(); } });
    canvas.addEventListener("dblclick", event => {
      const body = hit(toWorld(event));
      if (body && body.tier === 2) toggleExpanded(body.id);
    });
    canvas.addEventListener("wheel", event => {
      event.preventDefault();
      const point = toWorld(event);
      zoomAt(Math.exp(-event.deltaY * 0.0015), point.sx, point.sy);
    }, { passive: false });
    canvas.addEventListener("keydown", event => {
      if (event.key === "+" || event.key === "=") zoomAt(1.25);
      else if (event.key === "-") zoomAt(0.8);
      else if (event.key === "0") fit();
      else if (event.key === "Escape" && state.selectedId) { state.selectedId = null; state.detail = null; renderPanel(); requestDraw(); }
    });
  }

  function tooltip(body, point) {
    const tip = $("#km-tooltip");
    if (!tip) return;
    if (!body) { tip.hidden = true; return; }
    const node = body.node;
    const tier = tierOf(node.id);
    tip.innerHTML = `<strong>${esc(node.name)}</strong><span>${TIER_LABEL[tier]} · ${STATUS[node.status]?.label || ""}</span>`
      + `<span>Domínio ${pct(node.mastery)} · Profundidade ${pct(node.depth)}</span>`
      + (node.children_total ? `<span>Cobertura ${node.children_practiced}/${node.children_total} (${pct(node.coverage)})</span>` : `<span>${node.attempts} respostas</span>`);
    tip.hidden = false;
    const x = Math.min(point.sx + 14, view.width - tip.offsetWidth - 6);
    const y = Math.min(point.sy + 14, view.height - tip.offsetHeight - 6);
    tip.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
  }

  function toggleExpanded(id, open) {
    const kids = state.children.get(id) || [];
    if (!kids.length) return;
    const willOpen = open ?? !state.expanded.has(id);
    if (willOpen) {
      state.expanded.add(id);
      const parent = sim.nodes.get(id);
      kids.forEach((child, index) => {
        const body = sim.nodes.get(child);
        const angle = (index / kids.length) * Math.PI * 2;
        body.x = parent.x + Math.cos(angle) * LINK_LENGTH[3]; body.y = parent.y + Math.sin(angle) * LINK_LENGTH[3];
        body.vx = body.vy = 0;
      });
    } else state.expanded.delete(id);
    refreshVisible();
    reheat(0.35);
    if (state.selectedId === id) renderPanel();
  }

  async function selectNode(id, { center = false } = {}) {
    const node = state.byId.get(id);
    if (!node) return;
    const parent = node.parent_id;
    if (tierOf(id) === 3 && !state.showAll && !state.expanded.has(parent)) toggleExpanded(parent, true);
    state.selectedId = id;
    state.detail = null; state.detailError = null;
    renderPanel();
    if (center) centerOn(id);
    requestDraw();
    const seq = ++state.requestSeq;
    try {
      const detail = await fetchJson(`/api/knowledge-map/nodes/${encodeURIComponent(id)}?courseId=${encodeURIComponent(state.courseId)}`);
      if (seq !== state.requestSeq) return;
      state.detail = detail;
    } catch (error) {
      if (seq !== state.requestSeq) return;
      state.detailError = error.message;
    }
    renderPanel();
  }

  function search(query) {
    state.query = query;
    const needle = fold(query.trim());
    state.matches = needle.length < 2 ? [] : (state.data?.nodes || [])
      .filter(node => (!state.subject || node.subject === state.subject) && fold(node.name).includes(needle))
      .sort((a, b) => tierOf(a.id) - tierOf(b.id) || a.name.localeCompare(b.name, "pt-BR"))
      .map(node => node.id);
    for (const id of state.matches) {
      if (tierOf(id) === 3) state.expanded.add(state.byId.get(id).parent_id);
    }
    refreshVisible();
    const count = $("#km-search-count");
    if (count) count.textContent = needle.length < 2 ? "" : `${state.matches.length} encontrados`;
    if (state.matches.length) reheat(0.2);
    requestDraw();
  }

  // ---------------------------------------------------------------- panel
  function nodeButton(node, extra = "") {
    return `<button type="button" class="km-node-link" data-km-node="${esc(node.id)}"><span class="km-dot km-dot--${esc(node.status)}" aria-hidden="true"></span><span class="km-node-name">${esc(node.name)}</span>${extra}</button>`;
  }

  function overviewPanel() {
    const data = state.data;
    const subjects = roots().slice().sort((a, b) => (data.course.weights[b.subject] || 0) - (data.course.weights[a.subject] || 0));
    const items = subjects.map(node => `<li>${nodeButton(node, `<span class="km-node-meta">${pct(node.depth)}</span>${depthBar(node.depth)}`)}</li>`).join("");
    return `<div class="km-panel-head"><p class="eyebrow">Como ler</p><h2>Seu mapa</h2><p class="muted">Cada ponto é um nó da taxonomia. A <strong>cor</strong> mostra o status do domínio; o <strong>tamanho e o brilho</strong>, a profundidade: quanto do setor você já cobriu e com que domínio. Clique num nó para ver detalhes; dê dois cliques num conteúdo para abrir os subconteúdos.</p></div>
      <h3>Profundidade por matéria</h3><ul class="km-list">${items}</ul>`;
  }

  function stat(label, value, hint = "") {
    return `<div class="km-stat"><span>${esc(label)}</span><strong>${value}</strong>${hint ? `<small>${hint}</small>` : ""}</div>`;
  }

  function detailPanel() {
    const node = state.byId.get(state.selectedId);
    const tier = tierOf(node.id);
    const crumbs = pathOf(node.id).slice(0, -1).map(item => `<button type="button" class="text-button" data-km-node="${esc(item.id)}">${esc(item.name)}</button>`).join(`<span aria-hidden="true">›</span>`);
    const kids = state.children.get(node.id) || [];
    const expandable = tier === 2 && kids.length && !state.showAll;
    const detail = state.detail && state.detail.node.id === node.id ? state.detail : null;
    let body = `<div class="loading-card">Carregando detalhes…</div>`;
    if (state.detailError) body = `<p class="inline-result error">${esc(state.detailError)}</p>`;
    else if (detail) {
      const children = detail.children.map(child => `<li>${nodeButton(child, `<span class="km-node-meta">${pct(child.depth)}</span>${depthBar(child.depth)}`)}</li>`).join("");
      const answers = detail.recent_answers.slice().reverse().map(answer => `<li class="km-answer"><span class="km-mark ${answer.correct ? "is-right" : "is-wrong"}" aria-label="${answer.correct ? "Acertou" : "Errou"}">${answer.correct ? "✓" : "✗"}</span><span><strong>${esc(answer.exam_label)} · Q${esc(answer.number)}</strong><small>${esc(KIND_LABEL[answer.kind] || answer.kind)}${answer.at ? ` · ${new Date(answer.at).toLocaleDateString("pt-BR")}` : ""}${!answer.correct && answer.reason ? ` · ${esc(REASONS[answer.reason] || answer.reason)}` : ""}</small></span></li>`).join("");
      const questions = detail.questions.map(question => `<li class="km-question"><strong>${esc(question.exam_label)} · Q${esc(question.number)}${question.answered ? ` <span class="km-tag">respondida</span>` : ""}</strong><small>${esc(question.content_path)}</small>${question.statement ? `<p>${esc(question.statement)}${question.statement.length >= 180 ? "…" : ""}</p>` : ""}</li>`).join("");
      body = (children ? `<h3>${tier === 2 ? "Subconteúdos" : tier === 0 ? "Áreas" : "Conteúdos"}</h3><ul class="km-list">${children}</ul>` : "")
        + `<h3>Respostas recentes</h3>${answers ? `<ul class="km-list">${answers}</ul>` : `<p class="muted">Nenhuma resposta neste nó ainda.</p>`}`
        + `<h3>Questões do acervo <span class="optional">${node.questions}</span></h3>${questions ? `<ul class="km-list">${questions}</ul>` : `<p class="muted">Nenhuma questão revisada neste nó.</p>`}`;
    }
    const coverage = node.children_total ? `${node.children_practiced}/${node.children_total}` : (node.attempts ? "1/1" : "0/1");
    return `<div class="km-panel-head"><div class="km-crumbs">${crumbs}</div><div class="km-title-row"><h2>${esc(node.name)}</h2><button type="button" class="icon-button" data-km-close title="Fechar" aria-label="Fechar detalhes">×</button></div><p class="muted">${TIER_LABEL[tier]} · ${statusBadge(node.status)}</p></div>
      <div class="km-stats">${stat("Profundidade", pct(node.depth))}${stat("Domínio", pct(node.mastery), `IC 80% ${pct(node.lower)}–${pct(node.upper)}`)}${stat("Cobertura", coverage, pct(node.coverage))}${stat("Respostas", String(node.attempts))}</div>
      ${expandable ? `<button type="button" class="button button-primary full-width" data-km-expand="${esc(node.id)}">${state.expanded.has(node.id) ? "Esconder subconteúdos" : "Mostrar subconteúdos"}</button>` : ""}
      ${body}`;
  }

  function renderPanel() {
    const panel = $("#km-panel");
    if (!panel || !state.data) return;
    panel.innerHTML = state.selectedId && state.byId.has(state.selectedId) ? detailPanel() : overviewPanel();
  }

  function renderKpis() {
    const host = $("#km-kpis");
    if (!host || !state.data) return;
    const { kpis } = state.data;
    const best = Object.entries(kpis.depth_by_subject).sort((a, b) => b[1] - a[1])[0];
    host.innerHTML = [
      ["Profundidade do curso", pct(kpis.depth), "média ponderada pelo peso das matérias"],
      ["Nós praticados", `${kpis.practiced} / ${kpis.nodes - roots().length}`, "áreas, conteúdos e subconteúdos com resposta"],
      ["Matéria mais aprofundada", best && best[1] > 0 ? esc(best[0]) : "—", best && best[1] > 0 ? pct(best[1]) : "responda questões para começar"],
    ].map(([label, value, hint]) => `<div class="metric-card km-kpi"><span>${label}</span><strong>${value}</strong><small>${hint}</small></div>`).join("");
  }

  function renderShell() {
    const root = $("#km-root");
    if (!root) return;
    if (state.error && !state.data) { root.innerHTML = `<div class="panel"><p class="inline-result error">${esc(state.error)}</p></div>`; return; }
    if (!state.data) { root.innerHTML = `<div class="loading-card">Carregando o mapa…</div>`; return; }
    if (!$("#km-canvas")) {
      const subjects = roots().map(node => `<option value="${esc(node.subject)}">${esc(node.subject)}</option>`).join("");
      const legend = Object.entries(STATUS).map(([key, info]) => `<span><span class="km-dot km-dot--${key}" aria-hidden="true"></span>${info.label}</span>`).join("");
      root.innerHTML = `<div id="km-kpis" class="km-kpis"></div>
        <div class="km-layout">
          <section class="panel km-stage" aria-label="Grafo do conhecimento">
            <div class="km-toolbar">
              <label class="km-search">Buscar<input id="km-search" type="search" placeholder="Nome do nó" autocomplete="off"><small id="km-search-count" aria-live="polite"></small></label>
              <label>Matéria<select id="km-subject"><option value="">Todas</option>${subjects}</select></label>
              <label class="check-row"><input id="km-show-all" type="checkbox"><span>Todos os subconteúdos</span></label>
              <div class="km-zoom"><button type="button" class="icon-button" data-km-zoom="in" title="Aproximar" aria-label="Aproximar">+</button><button type="button" class="icon-button" data-km-zoom="out" title="Afastar" aria-label="Afastar">−</button><button type="button" class="icon-button" data-km-zoom="fit" title="Enquadrar" aria-label="Enquadrar o grafo">⤢</button></div>
            </div>
            <div class="km-canvas-wrap"><canvas id="km-canvas" tabindex="0" aria-label="Grafo da taxonomia. Use o painel ao lado para navegar pelo teclado; + e − ajustam o zoom e 0 enquadra."></canvas><div id="km-tooltip" class="km-tooltip" hidden></div></div>
            <div class="km-legend">${legend}<span class="km-legend-size"><span class="km-dot km-dot--small"></span><span class="km-dot km-dot--big"></span>tamanho e brilho = profundidade</span><span><span class="km-dot km-dot--dashed"></span>conteúdo com subconteúdos fechados</span></div>
          </section>
          <aside id="km-panel" class="panel km-panel" aria-live="polite"></aside>
        </div>`;
      canvas = $("#km-canvas");
      ctx = canvas.getContext("2d");
      bindCanvas();
      new ResizeObserver(() => resize()).observe(canvas.parentElement);
    }
    renderKpis();
    renderPanel();
  }

  // ---------------------------------------------------------------- data loading
  const isActive = () => document.getElementById("view-map")?.classList.contains("active");

  async function loadCourses() {
    if (state.courses.length) return;
    try { state.courses = await fetchJson("/api/courses"); } catch { state.courses = []; }
    let saved = null;
    try { saved = localStorage.getItem(COURSE_KEY) || localStorage.getItem("studai-reports-course"); } catch { saved = null; }
    const ids = state.courses.map(course => course.id);
    state.courseId = [saved, DEFAULT_COURSE].find(id => id && ids.includes(id)) || ids[0] || DEFAULT_COURSE;
    const select = $("#km-course");
    if (select) {
      select.innerHTML = state.courses.length
        ? state.courses.map(course => `<option value="${esc(course.id)}"${course.id === state.courseId ? " selected" : ""}>${esc(course.name)}</option>`).join("")
        : `<option value="${esc(state.courseId)}">${esc(state.courseId)}</option>`;
    }
  }

  async function refresh() {
    bind();
    await loadCourses();
    const courseId = state.courseId;
    const root = $("#km-root");
    if (state.data) root?.classList.add("is-refreshing"); else renderShell();
    try {
      const data = await fetchJson(`/api/knowledge-map?courseId=${encodeURIComponent(courseId)}`);
      if (courseId !== state.courseId) return;
      const sameCourse = state.data?.course?.id === data.course.id;
      state.data = data; state.error = null;
      indexGraph(data);
      if (!sameCourse) {
        state.expanded.clear(); state.selectedId = null; state.detail = null; state.matches = [];
        if (canvas) { $("#km-root").innerHTML = ""; canvas = null; ctx = null; view.width = 0; }
      }
      if (state.selectedId && !state.byId.has(state.selectedId)) state.selectedId = null;
      renderShell();
      readColors();
      if (!sameCourse || !sim.nodes.size) {
        seedLayout();
        refreshVisible();
        sim.alpha = 1;
        for (let i = 0; i < 160; i++) tick();
      } else refreshVisible();
      resize();
      if (!sameCourse) fit();
      reheat(0.1);
      if (state.selectedId) selectNode(state.selectedId);
    } catch (error) {
      if (courseId !== state.courseId) return;
      state.error = error.message;
      renderShell();
      if (state.data && typeof toast === "function") toast(`Mapa: ${error.message}`, true);
    } finally {
      root?.classList.remove("is-refreshing");
    }
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
    const root = $("#view-map");
    root.addEventListener("click", event => {
      const target = event.target.closest("button");
      if (!target) return;
      if (target.dataset.kmNode) selectNode(target.dataset.kmNode, { center: true });
      else if (target.dataset.kmExpand) { toggleExpanded(target.dataset.kmExpand); renderPanel(); }
      else if (target.matches("[data-km-close]")) { state.selectedId = null; state.detail = null; state.requestSeq++; renderPanel(); requestDraw(); }
      else if (target.dataset.kmZoom === "in") zoomAt(1.3);
      else if (target.dataset.kmZoom === "out") zoomAt(1 / 1.3);
      else if (target.dataset.kmZoom === "fit") fit();
    });
    root.addEventListener("input", event => {
      if (event.target.id === "km-search") search(event.target.value);
    });
    root.addEventListener("keydown", event => {
      if (event.target.id === "km-search" && event.key === "Enter" && state.matches.length) {
        event.preventDefault();
        selectNode(state.matches[0], { center: true });
      }
    });
    root.addEventListener("change", event => {
      if (event.target.id === "km-course") {
        state.courseId = event.target.value;
        try { localStorage.setItem(COURSE_KEY, state.courseId); } catch { /* armazenamento indisponível */ }
        refresh();
      } else if (event.target.id === "km-subject") {
        state.subject = event.target.value;
        if (state.selectedId && state.subject && state.byId.get(state.selectedId)?.subject !== state.subject) { state.selectedId = null; renderPanel(); }
        seedLayout(); refreshVisible();
        sim.alpha = 1;
        for (let i = 0; i < 120; i++) tick();
        if (state.query) search(state.query);
        fit(); reheat(0.1);
      } else if (event.target.id === "km-show-all") {
        state.showAll = event.target.checked;
        refreshVisible(); reheat(0.5); renderPanel();
      }
    });
    new MutationObserver(() => { readColors(); requestDraw(); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  window.refreshKnowledgeMap = refresh;
  window.studaiKnowledgeMap = { state, sim, view, selectNode };
  if (isActive()) refresh();
})();
