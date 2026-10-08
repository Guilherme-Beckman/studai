const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let exams = [];
let courses = [];
let currentDiagnostic = null;
let activeProvider = { provider: "demo" };
let toastTimer;
let currentProcessJob = null;
let processUploadController = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data?.error || data?.message || `Falha HTTP ${response.status}`);
  return data;
}

function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.style.background = error ? "#8f3b34" : "#17395f";
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3400);
}

function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const button = $("#theme-toggle");
  button.setAttribute("aria-pressed", String(dark));
  $("#theme-icon").textContent = dark ? "☀" : "☾";
  $("#theme-label").textContent = dark ? "Modo claro" : "Modo escuro";
  localStorage.setItem("studai-theme", dark ? "dark" : "light");
}

function showView(name) {
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  if (name === "exams") refreshExams();
  if (name === "diagnostics") refreshDiagnostics();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function pollJob(jobId, onUpdate = () => {}) {
  while (true) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    onUpdate(job);
    if (job.status === "completed") return job.result;
    if (job.status === "cancelled") throw new Error("Processamento cancelado.");
    if (job.status === "failed") throw new Error(job.error || "A tarefa falhou.");
    await new Promise(resolve => setTimeout(resolve, 850));
  }
}

function renderProcessProgress(job) {
  const status = $("#process-status");
  status.classList.remove("empty-state");
  const active = (job.activeQuestions || []).map(item => `Q${String(item.question).padStart(2, "0")} · ${item.seconds}s`).join(" · ");
  const count = job.total ? `${job.completed || 0} de ${job.total} questões processadas${job.cachedQuestions ? ` · ${job.cachedQuestions} do cache` : ""}${job.cacheMisses ? ` · ${job.cacheMisses} chamadas novas` : ""}${job.errors ? ` · ${job.errors} falha(s)` : ""}` : "";
  const progress = Math.max(0, Math.min(100, job.progress || 0));
  status.innerHTML = `<div class="progress-summary"><div class="progress-summary-top"><strong>${escapeHtml(job.message || "Preparando processamento…")}</strong><span>${progress}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><div class="progress-meta"><span>${escapeHtml(job.stageLabel || stageLabel(job.stage))}</span><span>${escapeHtml(count)}</span></div>${active ? `<div class="active-work"><span class="pulse-dot"></span>Agora: ${escapeHtml(active)}</div>` : ""}</div>`;
  $("#process-progress-subtitle").textContent = job.status === "cancelling" ? "Cancelamento solicitado; aguardando a etapa atual." : "Atualização em tempo real · detalhes das tarefas ativas abaixo.";
  const cancel = $("#process-cancel");
  cancel.classList.toggle("hidden", !["queued", "running", "cancelling"].includes(job.status));
  cancel.disabled = job.status === "cancelling";
  cancel.textContent = job.status === "cancelling" ? "Cancelando…" : "Cancelar processamento";
}

function stageLabel(stage) {
  return ({ queued: "Na fila", upload_saved: "Arquivo recebido", extracting: "Lendo páginas do PDF", rendering: "Extraindo questões e imagens", extracted: "Extração concluída", classifying: "Classificação por IA", answer_key: "Importando gabarito", completed: "Concluído", cancelled: "Cancelado", failed: "Falhou" })[stage] || "Preparando";
}

function examCard(exam, compact = false) {
  const state = exam.classified ? `<span class="badge">Classificada</span>` : `<span class="badge pending">Pendente</span>`;
  const element = `<div class="exam-item"><div class="exam-file-icon">▤</div><div class="exam-item-main"><strong title="${escapeHtml(exam.name)}">${escapeHtml(exam.name)}</strong><span>${exam.totalQuestions} questões · ${exam.importedAnswers || 0} respostas importadas${exam.subjects?.length ? ` · ${escapeHtml(exam.subjects.slice(0, 3).join(", "))}` : ""}</span></div>${state}</div>`;
  return compact ? `<div class="exam-mini"><strong title="${escapeHtml(exam.name)}">${escapeHtml(exam.name)}</strong><span>${exam.totalQuestions} questões · ${exam.classified ? "classificada" : "aguardando classificação"}</span></div>` : element;
}

async function refreshExams() {
  try {
    exams = await api("/api/exams");
    $("#exam-list").innerHTML = exams.length ? exams.map(exam => examCard(exam)).join("") : `<div class="empty-state"><div class="empty-icon">▤</div><strong>Seu acervo está vazio</strong><p>Processe uma prova para começar.</p></div>`;
    $("#overview-exams").innerHTML = exams.length ? exams.slice(0, 4).map(exam => examCard(exam, true)).join("") : `<div class="loading-card">Nenhuma prova processada ainda.</div>`;
    const options = `<option value="">Selecione uma prova</option>` + exams.map(exam => `<option value="${escapeHtml(exam.id)}">${escapeHtml(exam.name)} · ${exam.totalQuestions} questões</option>`).join("");
    $("#key-exam").innerHTML = options;
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshCourses() {
  try {
    courses = await api("/api/courses");
    $("#course-select").innerHTML = courses.map(course => `<option value="${escapeHtml(course.id)}">${escapeHtml(course.name)} · ${course.subjects.length} matérias</option>`).join("");
  } catch (error) {
    $("#course-select").innerHTML = `<option value="">Não foi possível carregar os cursos</option>`;
    toast(error.message, true);
  }
}

async function setupProviders() {
  try {
    const providers = await api("/api/providers");
    $$(".provider-select").forEach(select => {
      select.innerHTML = providers.map(provider => `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.name)}</option>`).join("");
      select.value = "demo";
    });
  } catch (error) { toast(error.message, true); }
}

function providerConfig(prefix) {
  const provider = $(`#${prefix}-provider`).value;
  return {
    provider,
    model: $(`#${prefix}-model`)?.value.trim() || "",
    baseUrl: $(`#${prefix}-base-url`)?.value.trim() || "",
    workers: $(`#${prefix}-workers`)?.value || "",
    ollamaThreads: $(`#${prefix}-ollama-threads`)?.value || "",
  };
}

function toggleProviderFields(prefix) {
  const extra = $(`#${prefix}-provider-extra`);
  const isExternal = $(`#${prefix}-provider`).value === "ollama";
  extra.classList.toggle("hidden", !isExternal);
}

async function processPdf(event) {
  event.preventDefault();
  const file = $("#pdf-file").files[0];
  const keyFile = $("#key-pdf-file").files[0];
  if (!file) return toast("Selecione um PDF.", true);
  const button = $("#process-form button[type=submit]");
  const status = $("#process-status");
  button.disabled = true;
  button.textContent = "Enviando prova…";
  processUploadController = new AbortController();
  renderProcessProgress({ status: "running", stage: "upload_saved", message: `Enviando ${file.name}`, progress: 1 });
  try {
    const config = providerConfig("process");
    const query = new URLSearchParams({ filename: file.name, provider: config.provider });
    if (config.model) query.set("model", config.model);
    if (config.baseUrl) query.set("baseUrl", config.baseUrl);
    if (config.workers) query.set("workers", config.workers);
    if (config.ollamaThreads) query.set("ollamaThreads", config.ollamaThreads);
    if ($("#process-images").checked) query.set("images", "sempre");
    const { jobId } = await api(`/api/exams/process?${query}`, { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file, signal: processUploadController.signal });
    currentProcessJob = jobId;
    const result = await pollJob(jobId, job => {
      renderProcessProgress(job);
    });
    let keySummary = "";
    if (keyFile && result.classified) {
      renderProcessProgress({ status: "running", stage: "answer_key", progress: 95, message: "Importando respostas do gabarito…" });
      const keyQuery = new URLSearchParams({ filename: keyFile.name });
      try {
        const imported = await api(`/api/exams/${encodeURIComponent(result.examId)}/answer-key?${keyQuery}`, {
          method: "POST", headers: { "Content-Type": "application/pdf" }, body: keyFile,
        });
        keySummary = `<br>${imported.imported} respostas do gabarito importadas.`;
      } catch (error) { keySummary = `<br>Prova pronta, mas não foi possível importar o gabarito: ${escapeHtml(error.message)}`; }
    }
    const cacheSummary = result.examCacheHit ? " · prova reutilizada do cache" : result.cachedQuestions ? ` · ${result.cachedQuestions} questões reaproveitadas do cache` : "";
    status.innerHTML = `<div class="inline-result ${result.classified ? "" : "error"}"><strong>${result.examCacheHit ? "Prova já processada" : result.classified ? "Prova processada" : "Extração concluída; classificação pendente"}</strong><br>${escapeHtml(result.name)} · ${result.questions} questões${escapeHtml(cacheSummary)}${keySummary}${result.classificationError ? `<br>${escapeHtml(result.classificationError)}` : ""}</div>`;
    $("#process-cancel").classList.add("hidden");
    $("#process-progress-subtitle").textContent = "Processamento finalizado.";
    toast(result.classified ? "Prova pronta no acervo." : "Prova extraída; confira a configuração do provedor.");
    $("#process-form").reset();
    $("#file-label").textContent = "Escolher PDF";
    $("#key-file-label").textContent = "Escolher PDF do gabarito";
    await refreshExams();
  } catch (error) {
    const cancelled = error.name === "AbortError" || /cancelado/i.test(error.message);
    status.innerHTML = `<div class="inline-result ${cancelled ? "" : "error"}">${escapeHtml(cancelled ? "Processamento cancelado a pedido." : error.message)}</div>`;
    $("#process-cancel").classList.add("hidden");
    $("#process-progress-subtitle").textContent = cancelled ? "A tarefa foi interrompida." : "Ocorreu um erro no processamento.";
    if (!cancelled) toast(error.message, true);
  } finally {
    currentProcessJob = null;
    processUploadController = null;
    button.disabled = false;
    button.innerHTML = "Extrair e classificar <span>→</span>";
  }
}

async function cancelProcess() {
  const button = $("#process-cancel");
  button.disabled = true;
  button.textContent = "Cancelando…";
  if (processUploadController && !currentProcessJob) {
    processUploadController.abort();
    return;
  }
  if (currentProcessJob) {
    try {
      await api(`/api/jobs/${encodeURIComponent(currentProcessJob)}/cancel`, { method: "POST" });
      $("#process-progress-subtitle").textContent = "Pedido enviado. A tarefa vai parar após a chamada de IA em andamento.";
    } catch (error) {
      toast(error.message, true);
      button.disabled = false;
      button.textContent = "Cancelar processamento";
    }
  }
}

async function importKey(event) {
  event.preventDefault();
  const resultEl = $("#key-result");
  resultEl.classList.remove("hidden", "error");
  try {
    const answers = JSON.parse($("#key-answers").value);
    const result = await api("/api/answer-keys/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ examId: $("#key-exam").value, answers, replace: $("#key-replace").checked }),
    });
    resultEl.textContent = `${result.imported} respostas importadas. ${result.questions_without_key.length} questões sem resposta.`;
    await refreshExams();
    toast("Gabarito importado.");
  } catch (error) {
    resultEl.textContent = error.message;
    resultEl.classList.add("error");
    toast(error.message, true);
  }
}

function diagnosticCard(item) {
  const state = item.status === "completed" ? "Concluído" : item.status === "analysis_failed" ? "Análise incompleta" : "Em andamento";
  return `<button class="history-item" data-diagnostic="${escapeHtml(item.id)}"><span class="history-mark">◉</span><span class="history-main"><strong>${escapeHtml(item.course_name)}</strong><span>${state} · ${item.actual_count} questões · ${new Date(item.created_at).toLocaleDateString("pt-BR")}</span></span><span class="history-arrow">›</span></button>`;
}

async function refreshDiagnostics() {
  try {
    const items = await api("/api/diagnostics");
    $("#diagnostic-list").innerHTML = items.length ? items.map(diagnosticCard).join("") : `<div class="empty-state"><div class="empty-icon">◉</div><strong>Nenhum diagnóstico ainda</strong><p>Gere um novo diagnóstico quando houver questões classificadas com gabarito.</p></div>`;
    $$("[data-diagnostic]").forEach(button => button.addEventListener("click", () => openDiagnostic(button.dataset.diagnostic)));
  } catch (error) { toast(error.message, true); }
}

async function createDiagnostic(event) {
  event.preventDefault();
  const status = $("#diagnostic-create-status");
  status.classList.remove("hidden", "error");
  status.textContent = "Preparando diagnóstico…";
  const config = providerConfig("diagnostic");
  activeProvider = config;
  const body = { ...config, courseId: $("#course-select").value, total: Number($("#diagnostic-total").value) };
  if ($("#diagnostic-seed").value) body.seed = Number($("#diagnostic-seed").value);
  try {
    const { jobId } = await api("/api/diagnostics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const result = await pollJob(jobId, job => { status.textContent = job.message; });
    sessionStorage.setItem(`studai-provider-${result.id}`, JSON.stringify(config));
    status.textContent = `Diagnóstico criado com ${result.selected} questões. Seed: ${result.seed}.`;
    await refreshDiagnostics();
    await openDiagnostic(result.id);
    $("#diagnostic-workspace").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Diagnóstico pronto.");
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
    toast(error.message, true);
  }
}

function renderMetrics(metrics) {
  if (!metrics) return "";
  const accuracy = metrics.accuracy == null ? "—" : `${Math.round(metrics.accuracy * 100)}%`;
  const rows = Object.entries(metrics.subjects || {}).map(([subject, row]) => `<tr><td>${escapeHtml(subject)}</td><td>${row.attempts}</td><td>${row.correct}</td><td>${row.accuracy == null ? "—" : `${Math.round(row.accuracy * 100)}%`}</td><td>${row.status === "assessed" ? "Avaliada" : "Poucos dados"}</td></tr>`).join("");
  return `<div class="result-grid"><div class="metric-card"><span>Acertos</span><strong>${metrics.correct}/${metrics.answered}</strong></div><div class="metric-card"><span>Desempenho geral</span><strong>${accuracy}</strong></div><div class="metric-card"><span>Questões</span><strong>${metrics.total_questions}</strong></div><div class="metric-card"><span>Em branco/incorretas</span><strong>${metrics.incorrect_or_blank}</strong></div></div><table class="subject-table"><thead><tr><th>Matéria</th><th>Tentativas</th><th>Acertos</th><th>Desempenho</th><th>Situação</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderAnalysis(analysis, error) {
  if (!analysis) return error ? `<div class="analysis-card"><h3>Análise não disponível</h3><p>${escapeHtml(error)}</p></div>` : "";
  const steps = (analysis.steps || []).map((step, index) => `<div class="analysis-step"><strong>${index + 1}. ${escapeHtml(step.subject)} · ${escapeHtml(step.action)}</strong><p>${escapeHtml(step.rationale)}</p><p><b>Critério:</b> ${escapeHtml(step.exit_criterion)}</p></div>`).join("");
  return `<div class="analysis-card"><h3>Plano inicial de estudos</h3><p>${escapeHtml(analysis.summary)}</p>${steps}</div>`;
}

function renderDiagnostic(workspace, data) {
  currentDiagnostic = data;
  workspace.classList.remove("hidden");
  const answered = data.questions.filter(question => question.answered).length;
  const progress = data.total ? Math.round(answered / data.total * 100) : 0;
  let body = "";
  if (data.metrics) {
    body = `${renderMetrics(data.metrics)}${renderAnalysis(data.analysis, data.analysisError)}`;
  } else if (answered < data.questions.length) {
    const item = data.questions.find(question => !question.answered);
    const question = item.question || {};
    const alternatives = Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b)).map(([letter, alternative]) => `<button class="option-button" data-answer="${escapeHtml(letter)}"><span class="option-letter">${escapeHtml(letter)}</span><span>${escapeHtml(alternative.texto || "Alternativa apresentada como figura")}</span></button>`).join("");
    body = `<div class="question-top"><span class="question-subject">${escapeHtml(item.subject)} · ${escapeHtml(item.topic)}</span><span>Questão ${item.ordinal} de ${data.total}</span></div>${question._texto_compartilhado ? `<div class="question-body"><b>Texto-base</b><br>${escapeHtml(question._texto_compartilhado)}</div>` : ""}<div class="question-body">${escapeHtml(question.enunciado || "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}</div><div class="options-list">${alternatives}</div><div class="question-actions"><button class="blank-button" id="answer-blank">Deixar em branco</button><span class="muted">Sua resposta fica salva ao selecionar.</span></div><div id="reason-panel" class="reason-row hidden"><label>Qual foi o motivo principal?<select id="answer-reason"><option value="">Selecione</option><option value="unknown_content">Não sabia o conteúdo</option><option value="calc_error">Errei o cálculo</option><option value="misread">Interpretei errado</option></select></label><button class="button button-primary" id="confirm-reason">Registrar resposta</button></div><div id="answer-feedback"></div>`;
  } else {
    body = `<div class="inline-result">Todas as respostas foram registradas. Gere a análise para ver suas métricas e prioridades.</div><button class="button button-primary" id="finalize-diagnostic">Concluir e analisar <span>→</span></button>`;
  }
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">TENTATIVA ${escapeHtml(data.id)}</p><h2>${escapeHtml(data.course)}</h2><div class="workspace-meta">${answered} de ${data.total} respondidas · ${escapeHtml(data.status)}</div></div><button class="icon-button" id="close-diagnostic" title="Fechar">×</button></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>${body}`;
  $("#close-diagnostic", workspace).addEventListener("click", () => { workspace.classList.add("hidden"); currentDiagnostic = null; });
  if (data.questions.some(question => !question.answered) && !data.metrics) {
    $$("[data-answer]", workspace).forEach(button => button.addEventListener("click", () => submitAnswer(data.id, data.questions.find(question => !question.answered), button.dataset.answer)));
    $("#answer-blank", workspace).addEventListener("click", () => submitAnswer(data.id, data.questions.find(question => !question.answered), null));
  }
  if (answered === data.questions.length && !data.metrics) $("#finalize-diagnostic", workspace).addEventListener("click", () => finalizeDiagnostic(data.id));
}

async function openDiagnostic(id) {
  try {
    const data = await api(`/api/diagnostics/${encodeURIComponent(id)}`);
    activeProvider = JSON.parse(sessionStorage.getItem(`studai-provider-${id}`) || '{"provider":"demo"}');
    renderDiagnostic($("#diagnostic-workspace"), data);
  } catch (error) { toast(error.message, true); }
}

async function submitAnswer(id, item, answer, reason = null) {
  const feedback = $("#answer-feedback");
  try {
    const result = await api(`/api/diagnostics/${encodeURIComponent(id)}/answers`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ordinal: item.ordinal, answer, reason }),
    });
    if (result.requiresReason) {
      const panel = $("#reason-panel");
      panel.classList.remove("hidden");
      $("#confirm-reason", panel).onclick = () => {
        const selectedReason = $("#answer-reason", panel).value;
        if (!selectedReason) return toast("Escolha o motivo do erro.", true);
        submitAnswer(id, item, answer, selectedReason);
      };
      return;
    }
    feedback.innerHTML = `<div class="inline-result">${result.answer == null ? "Resposta em branco registrada." : result.correct ? "Resposta correta registrada." : "Resposta registrada."}</div>`;
    setTimeout(() => openDiagnostic(id), 350);
  } catch (error) { toast(error.message, true); }
}

async function finalizeDiagnostic(id) {
  const button = $("#finalize-diagnostic");
  button.disabled = true;
  button.textContent = "Analisando respostas…";
  try {
    const { jobId } = await api(`/api/diagnostics/${encodeURIComponent(id)}/finalize`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(activeProvider),
    });
    await pollJob(jobId, job => { button.textContent = job.message === "Em andamento" ? "Calculando métricas e plano…" : job.message; });
    await openDiagnostic(id);
    await refreshDiagnostics();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Concluir e analisar →";
    toast(error.message, true);
  }
}

function bindEvents() {
  $("#theme-toggle").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  $$("[data-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $$("[data-go]").forEach(button => button.addEventListener("click", () => showView(button.dataset.go)));
  $("#process-form").addEventListener("submit", processPdf);
  $("#process-cancel").addEventListener("click", cancelProcess);
  $("#answer-key-form").addEventListener("submit", importKey);
  $("#diagnostic-form").addEventListener("submit", createDiagnostic);
  $("#refresh-exams").addEventListener("click", refreshExams);
  $("#refresh-diagnostics").addEventListener("click", refreshDiagnostics);
  $("#pdf-file").addEventListener("change", event => { $("#file-label").textContent = event.target.files[0]?.name || "Escolher PDF"; });
  $("#key-pdf-file").addEventListener("change", event => { $("#key-file-label").textContent = event.target.files[0]?.name || "Escolher PDF do gabarito"; });
  $("#drop-zone").addEventListener("dragover", event => { event.preventDefault(); event.currentTarget.classList.add("dragover"); });
  $("#drop-zone").addEventListener("dragleave", event => event.currentTarget.classList.remove("dragover"));
  $("#drop-zone").addEventListener("drop", event => {
    event.preventDefault();
    event.currentTarget.classList.remove("dragover");
    const file = event.dataTransfer.files[0];
    if (file) { $("#pdf-file").files = event.dataTransfer.files; $("#file-label").textContent = file.name; }
  });
  const keyDropZone = $("#key-drop-zone");
  keyDropZone.addEventListener("dragover", event => { event.preventDefault(); keyDropZone.classList.add("dragover"); });
  keyDropZone.addEventListener("dragleave", () => keyDropZone.classList.remove("dragover"));
  keyDropZone.addEventListener("drop", event => {
    event.preventDefault();
    keyDropZone.classList.remove("dragover");
    const file = event.dataTransfer.files[0];
    if (file) { $("#key-pdf-file").files = event.dataTransfer.files; $("#key-file-label").textContent = file.name; }
  });
  for (const prefix of ["process", "diagnostic"]) $(`#${prefix}-provider`).addEventListener("change", () => toggleProviderFields(prefix));
}

applyTheme(localStorage.getItem("studai-theme") || "light");
bindEvents();
setupProviders();
refreshExams();
refreshCourses();
refreshDiagnostics();
