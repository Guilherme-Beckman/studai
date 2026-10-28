/* StudAI · Leituras obrigatórias da FUVEST.
 *
 * Busca /api/readings (obras da lista, status de leitura, anotação, questões no acervo e
 * respostas) e desenha um cartão por obra. "Treinar esta obra" usa trainCalendarBlock
 * (app.js) com o conteúdo da obra. Expõe window.refreshReadings (chamado por showView).
 */
(function () {
  "use strict";

  const COURSE_KEY = "studai-readings-course";
  const state = { courseId: null, courses: [], data: null, editing: null };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

  async function request(path, options = {}) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Falha HTTP ${response.status}`);
    return data;
  }

  async function loadCourses() {
    if (state.courses.length) return;
    state.courses = await request("/api/courses");
    let saved = null;
    try { saved = localStorage.getItem(COURSE_KEY); } catch {}
    state.courseId = state.courses.some(course => course.id === saved) ? saved : state.courses[0]?.id;
    const select = document.getElementById("readings-course");
    select.innerHTML = state.courses.map(course => `<option value="${esc(course.id)}">${esc(course.name)}</option>`).join("");
    select.value = state.courseId || "";
  }

  async function refreshReadings() {
    const root = document.getElementById("readings-root");
    try {
      await loadCourses();
      state.data = await request(`/api/readings?courseId=${encodeURIComponent(state.courseId)}`);
      render();
    } catch (error) {
      root.innerHTML = `<div class="panel"><div class="inline-result error">Não foi possível carregar as leituras: ${esc(error.message)}</div></div>`;
    }
  }

  function card(work) {
    const statuses = Object.entries(state.data.statuses).map(([value, label]) =>
      `<button type="button" class="reading-status ${work.status === value ? "active" : ""}" data-reading-status="${esc(value)}" aria-pressed="${work.status === value}">${esc(label)}</button>`).join("");
    const [author, title] = work.name.includes(": ") ? work.name.split(": ") : ["", work.name];
    const accuracy = work.answered ? `${work.correct}/${work.answered} acertos` : "nenhuma respondida";
    const note = state.editing === work.contentId
      ? `<label class="reading-note-edit">Anotações (personagens, temas, trechos)<textarea data-reading-note rows="4" maxlength="2000">${esc(work.note || "")}</textarea></label><div class="reading-actions"><button type="button" class="button button-primary" data-reading-save>Salvar</button><button type="button" class="text-button" data-reading-cancel>Cancelar</button></div>`
      : `${work.note ? `<p class="reading-note">${esc(work.note)}</p>` : ""}<button type="button" class="text-button" data-reading-edit>${work.note ? "Editar anotações" : "Anotar"}</button>`;
    return `<article class="reading-card status-${esc(work.status)}" data-reading="${esc(work.contentId)}">
      <header><p class="eyebrow">${esc(author)}</p><h3>${esc(title)}</h3></header>
      <p class="reading-desc">${esc(work.description || "")}</p>
      <div class="reading-statuses" role="group" aria-label="Status de leitura">${statuses}</div>
      <p class="reading-stats">${work.questions} ${work.questions === 1 ? "questão" : "questões"} no acervo · ${accuracy}</p>
      ${work.deadline ? `<p class="reading-deadline">Ler até <b>${esc(new Date(`${work.deadline}T12:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "short" }))}</b></p>` : ""}
      ${note}
      ${work.questions ? `<button type="button" class="button button-secondary" data-reading-train>Treinar esta obra</button>` : ""}
    </article>`;
  }

  function render() {
    const root = document.getElementById("readings-root");
    const data = state.data;
    const share = data.total ? Math.round(data.read / data.total * 100) : 0;
    const plan = data.plan;
    const planText = !plan ? "Cadastre a data da prova no Calendário para receber um prazo de leitura para cada obra."
      : plan.next ? `Plano: terminar as obras até ${new Date(`${plan.until}T12:00`).toLocaleDateString("pt-BR", { day: "2-digit", month: "long" })} (≈ ${plan.daysPerWork} dias por obra), para sobrar tempo de treinar as questões.`
        : "Todas as obras lidas: agora é treinar as questões de cada uma.";
    root.innerHTML = `<div class="panel readings-summary"><div><strong>${data.read} de ${data.total} obras lidas</strong><p class="muted">Lista da FUVEST para a 1ª fase. ${planText}</p></div><div class="readings-bar" role="img" aria-label="${share}% da lista lida"><i style="width:${share}%"></i></div></div>
      <div class="readings-grid">${data.works.map(card).join("")}</div>`;
  }

  async function save(contentId, body) {
    try {
      await request(`/api/readings/${encodeURIComponent(contentId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      state.editing = null;
      await refreshReadings();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function bind() {
    const root = document.getElementById("readings-root");
    if (root.dataset.bound) return;
    root.dataset.bound = "1";
    document.getElementById("readings-course").addEventListener("change", event => {
      state.courseId = event.target.value;
      try { localStorage.setItem(COURSE_KEY, state.courseId); } catch {}
      refreshReadings();
    });
    root.addEventListener("click", event => {
      const button = event.target.closest("button");
      const cardEl = event.target.closest("[data-reading]");
      if (!button || !cardEl) return;
      const contentId = cardEl.dataset.reading;
      const work = state.data.works.find(item => item.contentId === contentId);
      if (button.dataset.readingStatus) return save(contentId, { status: button.dataset.readingStatus });
      if (button.matches("[data-reading-edit]")) { state.editing = contentId; render(); return cardEl.ownerDocument.querySelector(`[data-reading="${CSS.escape(contentId)}"] textarea`)?.focus(); }
      if (button.matches("[data-reading-cancel]")) { state.editing = null; return render(); }
      if (button.matches("[data-reading-save]")) return save(contentId, { note: cardEl.querySelector("[data-reading-note]").value });
      if (button.matches("[data-reading-train]")) {
        return trainCalendarBlock({ courseId: state.courseId, subject: "Literatura", contentIds: [contentId],
          count: Math.min(10, Math.max(1, work.questions)) });
      }
    });
  }

  window.refreshReadings = () => { bind(); return refreshReadings(); };
  if (document.getElementById("view-readings")?.classList.contains("active")) window.refreshReadings();
})();
