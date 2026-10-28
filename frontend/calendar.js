/* StudAI · Calendário de preparação.
 *
 * Busca /api/calendar (provas, prontidão, carga diária e o plano dia a dia até a
 * próxima prova) e desenha: provas, prontidão, hoje, semana e mês. "Treinar agora"
 * leva ao Treinamento com um bloco filtrado (trainCalendarBlock em app.js).
 * Expõe window.refreshCalendar (chamado por showView) e window.refreshCalendarStrip
 * (faixa "Plano de hoje" da aba Treinamento).
 */
(function () {
  "use strict";

  const COURSE_KEY = "studai-calendar-course";
  const PALETTE = ["#2f7de1", "#e0663a", "#2fa77f", "#8b63e0", "#d4a017", "#d24f8a", "#1f9eb3", "#7d8a2e",
    "#b8642e", "#5a6fd6", "#c0453f", "#3c9a4f", "#9a5bb4"];
  const KIND = { study: "Treino", simulado: "Simulado", eve: "Véspera", exam: "Prova", past: "" };
  const PHASE = { construcao: "Construção", reta_final: "Reta final" };
  const STATUS = { done: "Concluído", partial: "Em andamento", missed: "Não feito", pending: "Pendente",
    exam: "Dia da prova", active: "Estudou", idle: "" };
  const WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

  const state = { courseId: null, courses: [], data: null, month: null, selected: null, loading: false };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const pct = value => `${Math.round((value || 0) * 100)}%`;
  const day = iso => new Date(`${iso}T12:00:00`);
  const fmt = (iso, options) => day(iso).toLocaleDateString("pt-BR", options);
  const capitalize = text => text.charAt(0).toUpperCase() + text.slice(1);
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

  async function request(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Falha HTTP ${response.status}`);
    return data;
  }

  function color(subject) {
    const subjects = state.data?.subjects || [];
    const index = subjects.indexOf(subject);
    return PALETTE[(index < 0 ? subjects.length : index) % PALETTE.length];
  }

  function chip(subject, text = subject) {
    return `<span class="cal-chip" style="--c:${color(subject)}">${esc(text)}</span>`;
  }

  // ---------------------------------------------------------------- dados

  async function loadCourses() {
    if (state.courses.length) return;
    state.courses = await request("/api/courses");
    let saved = null;
    try { saved = localStorage.getItem(COURSE_KEY); } catch {}
    state.courseId = state.courses.some(course => course.id === saved) ? saved : state.courses[0]?.id;
    const select = document.getElementById("cal-course");
    select.innerHTML = state.courses.map(course => `<option value="${esc(course.id)}">${esc(course.name)}</option>`).join("");
    select.value = state.courseId || "";
  }

  async function refreshCalendar() {
    const root = document.getElementById("cal-root");
    if (state.loading) return;
    state.loading = true;
    try {
      await loadCourses();
      if (!state.courseId) { root.innerHTML = `<div class="inline-result error">Nenhum curso configurado.</div>`; return; }
      if (!state.data) root.innerHTML = `<div class="loading-card">Montando seu calendário…</div>`;
      state.data = await request(`/api/calendar?courseId=${encodeURIComponent(state.courseId)}`);
      state.month ||= state.data.today.slice(0, 7);
      state.selected ||= state.data.today;
      render();
    } catch (error) {
      root.innerHTML = `<div class="inline-result error">Não foi possível montar o calendário: ${esc(error.message)}</div>`;
    } finally {
      state.loading = false;
    }
  }

  async function addExam(form) {
    const body = { courseId: state.courseId, name: form.name.value, date: form.date.value, phase: form.phase.value,
      cutoff: form.cutoff.value === "" ? null : Number(form.cutoff.value) };
    try {
      await request("/api/calendar/exams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      toast("Prova adicionada ao calendário.");
      state.month = null;
      await refreshCalendar();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function editCutoff(id, current) {
    const value = window.prompt("Nota de corte da 1ª fase, em acertos de 90 (deixe vazio para apagar):", current ?? "");
    if (value === null) return;
    try {
      await request(`/api/calendar/exams/${encodeURIComponent(id)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cutoff: value.trim() }),
      });
      await refreshCalendar();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function removeExam(id, name) {
    if (!window.confirm(`Remover “${name}” do calendário?`)) return;
    try {
      await request(`/api/calendar/exams/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refreshCalendar();
    } catch (error) {
      toast(error.message, true);
    }
  }

  // ---------------------------------------------------------------- partes

  function examsPanel(data) {
    const cards = data.exams.map(exam => {
      const past = exam.daysLeft < 0;
      const target = data.target?.id === exam.id;
      const left = exam.daysLeft === 0 ? "É hoje!" : past ? "Já passou" : `faltam ${plural(exam.daysLeft, "dia", "dias")}`;
      return `<div class="cal-exam ${target ? "target" : ""} ${past ? "past" : ""}">
        <div><strong>${esc(exam.name)}</strong><span>${esc(exam.phaseLabel)} · ${esc(fmt(exam.date, { day: "2-digit", month: "long", year: "numeric" }))}${exam.cutoff != null ? ` · corte ${esc(exam.cutoff)} acertos` : ""}</span></div>
        <div class="cal-exam-side"><b>${esc(left)}</b>${target ? `<small>Plano atual</small>` : ""}
        <button class="text-button" data-cal-cutoff="${esc(exam.id)}" data-value="${esc(exam.cutoff ?? "")}">${exam.cutoff != null ? "Editar corte" : "Definir corte"}</button>
        <button class="text-button" data-cal-remove="${esc(exam.id)}" data-name="${esc(exam.name)}">Remover</button></div></div>`;
    }).join("");
    return `<div class="panel cal-exams"><div class="panel-heading"><span class="step-number">◷</span><div><h2>Suas provas</h2><p>O plano mira a próxima prova; a 2ª fase foca nas matérias específicas do curso.</p></div></div>
      ${cards || `<p class="help-copy">Nenhuma prova cadastrada. Adicione a data para gerar seu plano.</p>`}
      <form class="cal-exam-form" id="cal-exam-form">
        <label>Prova<input name="name" required placeholder="FUVEST 2027 · 1ª fase"></label>
        <label>Data<input name="date" type="date" required min="${esc(data.today)}"></label>
        <label>Fase<select name="phase"><option value="fase1">1ª fase</option><option value="fase2">2ª fase</option><option value="outra">Outra prova</option></select></label>
        <label>Nota de corte<input name="cutoff" type="number" min="0" max="90" inputmode="numeric" placeholder="acertos (opcional)"></label>
        <button class="button button-primary" type="submit">Adicionar</button>
      </form></div>`;
  }

  function readinessPanel(data) {
    const r = data.readiness;
    const rows = r.subjects.map(item => `<div class="cal-ready-row">
        <span class="cal-ready-name">${chip(item.subject)}</span>
        <div class="cal-bar"><i style="width:${pct(item.readiness)};--c:${color(item.subject)}"></i></div>
        <b>${pct(item.readiness)}</b><small>${item.consolidated}/${item.contents} conteúdos</small></div>`).join("");
    const load = data.load;
    const loadNote = !load ? "" : load.feasible
      ? `<p class="cal-load">Ritmo necessário: <b>${plural(load.questionsPerDay, "questão", "questões")} por dia</b> em ${plural(load.studyDays, "dia de treino", "dias de treino")} para cobrir ${plural(load.contentsPending, "conteúdo", "conteúdos")} ainda não consolidados.</p>`
      : `<p class="cal-load warn">O tempo não cobre tudo: mesmo com ${load.questionsPerDay} questões por dia, ${plural(load.contentsPending - load.contentsPlanned, "conteúdo fica", "conteúdos ficam")} de fora. O plano prioriza lacunas e o que mais cai na prova.</p>`;
    return `<div class="panel cal-readiness"><div class="panel-heading"><span class="step-number step-green">✓</span><div><h2>Quão preparado você está</h2><p>Profundidade por matéria nas questões que já respondeu, ponderada pelo peso no curso.</p></div></div>
      <div class="cal-ready-top"><div class="cal-ring" style="--p:${Math.round(r.overall * 100)}"><span>${pct(r.overall)}</span></div>
      <div>${data.target ? `<strong>${esc(data.target.name)}</strong><p class="muted">${data.target.daysLeft === 0 ? "A prova é hoje." : `Faltam ${plural(data.target.daysLeft, "dia", "dias")}.`}</p>` : `<p class="muted">Cadastre uma prova para ver o ritmo necessário.</p>`}${loadNote}</div></div>
      ${projectionBlock(data.projection)}
      <div class="cal-ready-list">${rows}</div></div>`;
  }

  // Acertos estimados na 1ª fase: hoje, contra o corte e no ritmo atual até a prova.
  function projectionBlock(p) {
    if (!p) return "";
    if (p.current == null) {
      return `<div class="cal-projection empty"><strong>Acertos estimados na 1ª fase</strong><p class="muted">Responda pelo menos ${p.minResponses} questões (você tem ${p.responses}) para ver quantas acertaria se a prova fosse hoje.</p></div>`;
    }
    const gap = p.cutoff != null ? Math.round(p.current - p.cutoff) : null;
    const cutoffNote = p.cutoff == null ? `<span class="muted">Defina a nota de corte na sua prova para comparar.</span>`
      : `<span class="${gap >= 0 ? "cal-proj-ok" : "cal-proj-bad"}">corte ${p.cutoff} · ${gap >= 0 ? `${gap} acima` : `faltam ${-gap}`}</span>`;
    const atExam = p.atExam != null
      ? `<p>No ritmo atual (${p.slopePerWeek >= 0 ? "+" : ""}${String(p.slopePerWeek).replace(".", ",")} por semana), você chega à prova com <b>~${Math.round(p.atExam)}/${p.questions}</b>.</p>`
      : `<p class="muted">A tendência aparece com duas semanas de histórico.</p>`;
    const points = [...p.weekly.map(point => ({ ...point, kind: "estimate" })),
      ...p.simulados.map(point => ({ date: point.at.slice(0, 10), value: point.value, kind: "simulado", label: point.label }))]
      .sort((a, b) => a.date.localeCompare(b.date));
    const max = Math.max(p.questions, ...points.map(point => point.value));
    const spark = points.length > 1 ? `<div class="cal-proj-spark" role="img" aria-label="Evolução dos acertos estimados">${points.map(point =>
      `<i class="${point.kind}" style="height:${Math.max(4, (point.value / max) * 100)}%" title="${esc(fmt(point.date, { day: "2-digit", month: "2-digit" }))}: ${Math.round(point.value)}${point.kind === "simulado" ? ` (${esc(point.label || "simulado")})` : ""}"></i>`).join("")}${p.cutoff != null ? `<b style="bottom:${(p.cutoff / max) * 100}%"></b>` : ""}</div>` : "";
    const coverage = p.coverage < 0.999
      ? `<p class="muted">${pct(p.coverage)} da prova já tem respostas suas; nas matérias sem nenhuma, a estimativa conta só o chute (1 em 5).</p>` : "";
    return `<div class="cal-projection"><div><strong>Se a prova fosse hoje: ~${Math.round(p.current)}/${p.questions} acertos</strong> ${cutoffNote}</div>${coverage}${atExam}${spark}</div>`;
  }

  function blockRow(block, entry, { actions = true } = {}) {
    const contents = block.contents.slice(0, 3).map(item => esc(item.name)).join(", ")
      + (block.contents.length > 3 ? ` +${block.contents.length - 3}` : "");
    const done = block.done ?? 0;
    const remaining = Math.max(0, block.questions - done);
    const button = actions && remaining > 0
      ? `<button class="button button-primary" data-cal-train="${esc(block.subject)}" data-contents="${esc(block.contentIds.join(","))}" data-count="${remaining}">Treinar agora</button>`
      : actions ? `<span class="cal-check">✓ Feito</span>` : "";
    return `<div class="cal-block">
      <div class="cal-block-main">${chip(block.subject)}<span class="cal-block-kind">${block.kind === "review" ? "Revisão" : "Foco"}</span>
        <p>${contents || "Questões da matéria"}</p></div>
      <div class="cal-block-side"><b>${entry.frozen || entry.kind === "past" ? `${done}/${block.questions}` : block.questions}</b><small>questões</small>${button}</div></div>`;
  }

  function dayBody(entry, { actions }) {
    if (entry.kind === "exam") return `<p class="cal-big">Dia da prova. Boa sorte! Descanse e confie no que treinou.</p>`;
    if (entry.simulado) {
      const sim = entry.simulado;
      const action = actions && entry.status !== "done"
        ? `<button class="button button-primary" data-cal-simulado="${esc(sim.sourceId)}">Fazer prova</button>` : entry.status === "done" ? `<span class="cal-check">✓ Feito</span>` : "";
      return `<div class="cal-block"><div class="cal-block-main"><span class="cal-chip sim">Simulado</span><p>${esc(sim.label)} · ${plural(sim.questions, "questão", "questões")}${sim.answered ? " (já respondida antes)" : ""}</p></div>
        <div class="cal-block-side">${entry.frozen ? `<b>${entry.done}/${entry.target}</b><small>respondidas</small>` : ""}${action}</div></div>`;
    }
    if (!entry.blocks.length) return `<p class="help-copy">${entry.kind === "past" ? (entry.answered ? `${plural(entry.answered, "questão respondida", "questões respondidas")} neste dia.` : "Sem plano para este dia.") : "Sem questões disponíveis para este dia."}</p>`;
    return entry.blocks.map(block => blockRow(block, entry, { actions })).join("")
      + (entry.kind === "eve" ? `<p class="help-copy">Véspera: revisão leve. Durma bem.</p>` : "");
  }

  function todayPanel(data) {
    const entry = data.days.find(item => item.date === data.today);
    if (!entry) return "";
    const progress = entry.target ? Math.min(100, Math.round(100 * entry.done / entry.target)) : 0;
    return `<div class="panel cal-today"><div class="workspace-top"><div><p class="eyebrow">Hoje, ${esc(fmt(entry.date, { weekday: "long", day: "2-digit", month: "long" }))}</p>
      <h2>${entry.kind === "exam" ? "Dia da prova" : entry.simulado ? "Dia de simulado" : `${KIND[entry.kind]} · ${PHASE[entry.phase] || ""}`}</h2>
      <div class="workspace-meta">${entry.target ? `${entry.done} de ${entry.target} questões · ${STATUS[entry.status] || ""}` : ""}</div></div></div>
      ${entry.target ? `<div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>` : ""}
      <div class="cal-blocks">${dayBody(entry, { actions: true })}</div>
      <p class="help-copy">Vale qualquer questão da matéria respondida hoje: tarefa diária, treino livre ou o bloco do calendário. O plano de hoje não muda; os próximos dias se ajustam ao seu desempenho.</p></div>`;
  }

  function weekPanel(data) {
    const start = data.days.findIndex(item => item.date === data.today);
    const days = data.days.slice(start, start + 7);
    if (!days.length) return "";
    const week = data.weeks[0];
    const summary = week ? week.subjects.map(item => chip(item.subject, `${item.subject} · ${item.questions}`)).join("") : "";
    const rows = days.map(entry => {
      const subjects = entry.simulado ? `<span class="cal-chip sim">${esc(entry.simulado.label)}</span>`
        : entry.kind === "exam" ? `<span class="cal-chip exam">Prova</span>`
          : entry.blocks.map(block => chip(block.subject, `${block.subject} · ${block.questions}`)).join("");
      return `<button class="cal-week-day ${entry.date === state.selected ? "selected" : ""}" data-cal-day="${esc(entry.date)}">
        <span class="cal-week-date"><b>${esc(fmt(entry.date, { weekday: "short" }))}</b>${esc(fmt(entry.date, { day: "2-digit", month: "2-digit" }))}</span>
        <span class="cal-week-subjects">${subjects}</span>
        <span class="cal-status ${esc(entry.status)}">${esc(STATUS[entry.status] || "")}</span></button>`;
    }).join("");
    return `<div class="panel cal-week"><div class="panel-heading"><span class="step-number step-violet">▦</span><div><h2>Próximos 7 dias</h2><p>Questões planejadas por matéria nesta semana.</p></div></div>
      <div class="cal-chips">${summary}</div><div class="cal-week-list">${rows}</div></div>`;
  }

  function monthPanel(data) {
    const byDate = Object.fromEntries(data.days.map(entry => [entry.date, entry]));
    const [year, month] = state.month.split("-").map(Number);
    const first = new Date(year, month - 1, 1);
    const offset = (first.getDay() + 6) % 7;
    const total = new Date(year, month, 0).getDate();
    const cells = [];
    for (let i = 0; i < offset; i++) cells.push(`<div class="cal-cell empty"></div>`);
    for (let d = 1; d <= total; d++) {
      const iso = `${state.month}-${String(d).padStart(2, "0")}`;
      const entry = byDate[iso];
      const marks = !entry ? "" : entry.kind === "exam" ? `<span class="cal-chip exam">Prova</span>`
        : entry.simulado ? `<span class="cal-chip sim">Simulado</span>`
          : entry.blocks.map(block => `<i class="cal-dot" style="--c:${color(block.subject)}" title="${esc(block.subject)}"></i>`).join("");
      const classes = ["cal-cell", entry ? `s-${entry.status}` : "off", iso === data.today ? "today" : "", iso === state.selected ? "selected" : ""].join(" ");
      cells.push(`<button class="${classes}" data-cal-day="${iso}" ${entry ? "" : "disabled"}><span class="cal-num">${d}</span><span class="cal-marks">${marks}</span>${entry?.target && entry.kind !== "past" ? `<small>${entry.target}q</small>` : ""}</button>`);
    }
    const monthSummary = data.months.find(item => item.key === state.month);
    const summary = monthSummary ? `<p class="muted">${plural(monthSummary.questions, "questão planejada", "questões planejadas")} · ${plural(monthSummary.simulados, "simulado", "simulados")}${monthSummary.contents.length ? ` · ${plural(monthSummary.contents.length, "conteúdo em foco", "conteúdos em foco")}` : ""}</p>
      <div class="cal-chips">${monthSummary.subjects.map(item => chip(item.subject, `${item.subject} · ${item.questions}`)).join("")}</div>` : "";
    const selected = byDate[state.selected];
    const detail = selected ? `<div class="cal-detail"><p class="eyebrow">${esc(capitalize(fmt(selected.date, { weekday: "long", day: "2-digit", month: "long" })))}${selected.kind !== "past" && KIND[selected.kind] ? ` (${esc(KIND[selected.kind].toLowerCase())})` : ""}</p>
      ${dayBody(selected, { actions: selected.date === data.today })}</div>` : "";
    const monthName = first.toLocaleDateString("pt-BR", { month: "long" });
    const label = `${monthName.charAt(0).toUpperCase()}${monthName.slice(1)} de ${year}`;
    return `<div class="panel cal-month"><div class="cal-month-head"><button class="icon-button" data-cal-month="-1" title="Mês anterior">‹</button>
      <h2>${esc(label)}</h2><button class="icon-button" data-cal-month="1" title="Próximo mês">›</button></div>${summary}
      <div class="cal-grid">${WEEKDAYS.map(name => `<span class="cal-weekday">${name}</span>`).join("")}${cells.join("")}</div>${detail}</div>`;
  }

  function render() {
    const data = state.data;
    const root = document.getElementById("cal-root");
    const plan = data.target
      ? `${todayPanel(data)}<div class="cal-two">${weekPanel(data)}${monthPanel(data)}</div>`
      : `<div class="panel empty-state"><div class="empty-icon">◷</div><strong>Sem prova no horizonte</strong><p>Adicione a data da sua próxima prova para receber o caminho dia a dia até ela.</p></div>`;
    root.innerHTML = `<div class="cal-two">${examsPanel(data)}${readinessPanel(data)}</div>${plan}`;
  }

  // ---------------------------------------------------------------- eventos

  function bind() {
    const root = document.getElementById("cal-root");
    if (root.dataset.bound) return;
    root.dataset.bound = "1";
    document.getElementById("cal-course").addEventListener("change", event => {
      state.courseId = event.target.value;
      try { localStorage.setItem(COURSE_KEY, state.courseId); } catch {}
      state.data = null; state.month = null; state.selected = null;
      refreshCalendar();
    });
    root.addEventListener("submit", event => {
      if (event.target.id !== "cal-exam-form") return;
      event.preventDefault();
      addExam(event.target);
    });
    root.addEventListener("click", event => {
      const target = event.target.closest("button");
      if (!target) return;
      if (target.dataset.calRemove) return removeExam(target.dataset.calRemove, target.dataset.name);
      if (target.dataset.calCutoff) return editCutoff(target.dataset.calCutoff, target.dataset.value);
      if (target.dataset.calTrain) return train(target);
      if (target.dataset.calSimulado) return simulado(target.dataset.calSimulado);
      if (target.dataset.calMonth) {
        const [year, month] = state.month.split("-").map(Number);
        const next = new Date(year, month - 1 + Number(target.dataset.calMonth), 1);
        state.month = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}`;
        return render();
      }
      if (target.dataset.calDay) {
        state.selected = target.dataset.calDay;
        state.month = state.selected.slice(0, 7);
        render();
      }
    });
  }

  function train(button) {
    return trainCalendarBlock({
      courseId: state.courseId, subject: button.dataset.calTrain,
      contentIds: button.dataset.contents ? button.dataset.contents.split(",") : [],
      count: Number(button.dataset.count),
    });
  }

  function simulado(sourceId) {
    showView("exams");
    openSimulado(sourceId);
  }

  // ---------------------------------------------------------------- faixa do Treinamento

  async function refreshCalendarStrip(courseId) {
    const strip = document.getElementById("calendar-today-strip");
    if (!strip || !courseId) return;
    try {
      const data = await request(`/api/calendar/today?courseId=${encodeURIComponent(courseId)}`);
      const entry = data.today;
      if (!data.target || !entry) {
        strip.innerHTML = `<div class="cal-strip"><span>◷</span><p>Cadastre a data da sua prova no <button class="text-button" data-go-calendar>Calendário</button> para receber o plano de cada dia.</p></div>`;
      } else {
        const parts = entry.simulado ? `Simulado: ${esc(entry.simulado.label)}`
          : entry.kind === "exam" ? "Dia da prova"
            : entry.blocks.map(block => `${chip(block.subject)} ${block.done ?? 0}/${block.questions}`).join(" ");
        const next = entry.blocks.find(block => (block.done ?? 0) < block.questions);
        const action = entry.simulado && entry.status !== "done"
          ? `<button class="button button-primary" data-strip-simulado="${esc(entry.simulado.sourceId)}">Fazer prova</button>`
          : next ? `<button class="button button-primary" data-strip-train>Treinar ${esc(next.subject)}</button>` : `<span class="cal-check">✓ Plano de hoje feito</span>`;
        strip.innerHTML = `<div class="cal-strip"><span>◷</span><div><p class="eyebrow">Plano de hoje para ${esc(data.target.name)}, ${data.target.daysLeft === 1 ? "falta 1 dia" : `faltam ${data.target.daysLeft} dias`}</p><div class="cal-strip-parts">${parts}</div></div>${action}</div>`;
        strip.querySelector("[data-strip-train]")?.addEventListener("click", () => trainCalendarBlock({
          courseId, subject: next.subject, contentIds: next.contentIds, count: next.questions - (next.done ?? 0) }));
        strip.querySelector("[data-strip-simulado]")?.addEventListener("click", () => simulado(entry.simulado.sourceId));
      }
      strip.querySelector("[data-go-calendar]")?.addEventListener("click", () => showView("calendar"));
    } catch {
      strip.innerHTML = "";
    }
  }

  window.refreshCalendar = () => { bind(); return refreshCalendar(); };
  window.refreshCalendarStrip = refreshCalendarStrip;
  if (document.getElementById("view-calendar")?.classList.contains("active")) window.refreshCalendar();
})();
