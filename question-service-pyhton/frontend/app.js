const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let exams = [];
let courses = [];
let currentDiagnostic = null;
let currentExamQuiz = null;
let currentExamReview = null;
let currentReprocessPreview = null;
let activeProvider = { provider: "demo" };
let toastTimer;
let currentProcessJob = null;
let processUploadController = null;
const ACTIVE_PROCESS_KEY = "studai-active-process";
const PROCESS_DB_NAME = "studai-browser-state";
const PROCESS_DB_STORE = "pending";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(data?.error || data?.message || `Falha HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function openProcessDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(PROCESS_DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(PROCESS_DB_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function savePendingProcess(record) {
  const db = await openProcessDb();
  await new Promise((resolve, reject) => {
    const transaction = db.transaction(PROCESS_DB_STORE, "readwrite");
    transaction.objectStore(PROCESS_DB_STORE).put(record, "active");
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
  db.close();
  localStorage.setItem(ACTIVE_PROCESS_KEY, JSON.stringify({ jobId: record.jobId }));
}

async function readPendingProcess() {
  const db = await openProcessDb();
  const record = await new Promise((resolve, reject) => {
    const request = db.transaction(PROCESS_DB_STORE).objectStore(PROCESS_DB_STORE).get("active");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return record;
}

async function clearPendingProcess() {
  localStorage.removeItem(ACTIVE_PROCESS_KEY);
  try {
    const db = await openProcessDb();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(PROCESS_DB_STORE, "readwrite");
      transaction.objectStore(PROCESS_DB_STORE).delete("active");
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
    });
    db.close();
  } catch (error) { console.warn("Não foi possível limpar o processamento salvo", error); }
}

function newClientJobId() {
  if (crypto.randomUUID) return crypto.randomUUID().replaceAll("-", "");
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, "0")).join("");
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
  const active = (job.activeQuestions || []).map(item => `Q${String(item.question).padStart(2, "0")} · ${item.provider || "IA"} · ${item.seconds}s`).join(" · ");
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
  const progress = Number(exam.reviewProgress ?? 100);
  const element = `<div class="exam-item exam-item-row"><button class="exam-open-button" type="button" data-open-exam="${escapeHtml(exam.id)}"><span class="exam-file-icon">▤</span><span class="exam-item-main"><strong title="${escapeHtml(exam.name)}">${escapeHtml(exam.name)}</strong><span>${exam.totalQuestions} questões · revisão ${progress}% · ${exam.importedAnswers || 0} respostas do gabarito</span></span>${state}<span class="exam-open-arrow" aria-hidden="true">›</span></button><button class="exam-remove-button" type="button" data-delete-exam="${escapeHtml(exam.id)}" aria-label="Remover ${escapeHtml(exam.name)}" title="Remover prova e questões">Remover</button></div>`;
  return compact ? `<div class="exam-mini"><strong title="${escapeHtml(exam.name)}">${escapeHtml(exam.name)}</strong><span>${exam.totalQuestions} questões · revisão ${progress}%</span></div>` : element;
}

async function deleteExam(examId) {
  const exam = exams.find(item => item.id === examId);
  const name = exam?.name || "esta prova";
  if (!window.confirm(`Remover “${name}” e as questões processadas, revisões e gabarito associado? Respostas de diagnósticos já realizados serão preservadas no histórico.`)) return;
  const button = $(`[data-delete-exam="${CSS.escape(examId)}"]`, $("#exam-list"));
  if (button) { button.disabled = true; button.textContent = "Removendo…"; }
  try {
    await api(`/api/exams/${encodeURIComponent(examId)}`, { method: "DELETE" });
    localStorage.removeItem(`studai-exam-answers:${examId}`);
    if (currentExamReview?.exam?.id === examId) {
      currentExamReview = null;
      $("#exam-question-workspace").classList.add("hidden");
    }
    if (currentExamQuiz?.exam?.id === examId) {
      currentExamQuiz = null;
      $("#exam-quiz-workspace").classList.add("hidden");
    }
    toast(`“${name}” e seus arquivos do acervo foram removidos.`);
    await refreshExams();
  } catch (error) {
    toast(error.message, true);
    if (button) { button.disabled = false; button.textContent = "Remover"; }
  }
}

async function refreshExams() {
  try {
    exams = await api("/api/exams");
    $("#exam-list").innerHTML = exams.length ? exams.map(exam => examCard(exam)).join("") : `<div class="empty-state"><div class="empty-icon">▤</div><strong>Seu acervo está vazio</strong><p>Processe uma prova para começar.</p></div>`;
    $$("[data-open-exam]", $("#exam-list")).forEach(button => button.addEventListener("click", () => openExamReview(button.dataset.openExam)));
    $$ ("[data-delete-exam]", $("#exam-list")).forEach(button => button.addEventListener("click", () => deleteExam(button.dataset.deleteExam)));
    $("#overview-exams").innerHTML = exams.length ? exams.slice(0, 4).map(exam => examCard(exam, true)).join("") : `<div class="loading-card">Nenhuma prova processada ainda.</div>`;
  } catch (error) {
    toast(error.message, true);
  }
}

function examAssetUrl(examId, relativePath) {
  const safePath = String(relativePath || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return `/api/exams/${encodeURIComponent(examId)}/assets/${safePath}`;
}

function examImageMarkup(examId, paths, className = "") {
  return (Array.isArray(paths) ? paths : []).map(path => `<span class="exam-image-wrap"><img class="exam-question-image ${className}" src="${examAssetUrl(examId, path)}" alt="Imagem da questão" loading="lazy" onerror="this.closest('.exam-image-wrap').classList.add('image-unavailable')"><span class="image-unavailable-label">Imagem indisponível</span></span>`).join("");
}

function reprocessImageMarkup(assetPrefix, paths, className = "") {
  return (Array.isArray(paths) ? paths : []).map(path => {
    const safePath = String(path || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
    return `<span class="exam-image-wrap"><img class="exam-question-image ${className}" src="${assetPrefix}/${safePath}" alt="Prévia da imagem reprocessada" loading="lazy"></span>`;
  }).join("");
}

function questionDisplayState(question) {
  const reasons = new Set([...(question.revisar || []), ...(question.reviewReasons || [])]);
  const alternatives = Object.values(question.alternativas || {}).map(option =>
    typeof option === "string" ? option : option?.texto || "");
  const text = [question.enunciado || "", ...alternatives].join(" ");
  // Bloqueia sequências claramente corrompidas que aparecem quando o PDF tem
  // mapeamento incorreto de glifos matemáticos para Unicode.
  const unreadable = reasons.has("glifo_estranho") || /[\ufffd\ue000-\uf8ff\u0b80-\u0bff\u0d00-\u0d7f\u1200-\u137f¬]/u.test(text);
  const hasStem = Boolean(String(question.enunciado || "").trim());
  const showSource = reasons.has("formula_matematica") || reasons.has("enunciado_vazio") || !hasStem || unreadable;
  const sourceImages = showSource ? (question.imagens_completas || []) : [];
  return {
    showSource: sourceImages.length > 0,
    sourceImages,
    showStem: hasStem && !unreadable,
    showAlternativeText: !unreadable,
  };
}

function renderSourceQuestionImage(examId, paths) {
  return paths?.length
    ? `<div class="source-question-image-panel"><strong>Enunciado original da prova</strong>${examImageMarkup(examId, paths, "source-question-image")}</div>`
    : "";
}

async function openExamReview(examId) {
  const workspace = $("#exam-question-workspace");
  const quizWorkspace = $("#exam-quiz-workspace");
  quizWorkspace.classList.add("hidden");
  workspace.classList.remove("hidden");
  workspace.innerHTML = `<div class="loading-card">Carregando questões da prova…</div>`;
  try {
    currentExamReview = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
    renderExamReview();
    workspace.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    workspace.innerHTML = `<div class="exam-quiz-error">Não foi possível abrir a prova: ${escapeHtml(error.message)}</div>`;
  }
}

function renderExamReview() {
  const workspace = $("#exam-question-workspace");
  const data = currentExamReview;
  if (!data) return;
  const { exam, questions, sharedTexts = {}, reviewProgress = {}, ragStatus = {} } = data;
  const progress = Number(reviewProgress.reviewProgress ?? exam.reviewProgress ?? 100);
  const ragLabel = ragStatus.status === "ready" ? `${ragStatus.examples || 0} exemplo(s) no RAG local` :
    ragStatus.status === "indexing" || ragStatus.status === "queued" ? "Indexação local em andamento" :
      ragStatus.status === "pending" ? `Índice pendente: ${ragStatus.error || "Ollama de embeddings indisponível"}` : "Nenhum exemplo revisado no índice";
  const cards = questions.map(question => renderReviewQuestion(exam.id, question, sharedTexts)).join("");
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">PROVA E GABARITO</p><h2>${escapeHtml(exam.name)}</h2><div class="workspace-meta">${questions.length} questões · ${exam.importedAnswers ?? 0} respostas importadas · ${reviewProgress.reviewedQuestions ?? 0}/${reviewProgress.reviewRequired ?? 0} questões sinalizadas revisadas</div></div><div class="review-header-actions"><button class="button button-primary" data-start-quiz="${escapeHtml(exam.id)}">Responder prova</button><button class="blank-button" data-close-exam-review>Fechar</button></div></div>
    <div class="exam-review-progress"><div class="progress-label"><strong>Revisão da prova</strong><span>${progress}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><div class="workspace-meta">${reviewProgress.pendingReview ?? 0} questão(ões) sinalizadas pendentes · ${escapeHtml(ragLabel)}</div>${ragStatus.status === "pending" ? `<button class="text-button" data-retry-rag="${escapeHtml(exam.id)}">Tentar indexar novamente</button>` : ""}</div>
    <div class="exam-review-list">${cards || `<div class="loading-card">Esta prova não contém questões extraídas.</div>`}</div>`;
  bindExamReviewActions();
}

function reviewReasonLabel(reason) {
  return ({ classificacao_baixa_confianca: "Baixa confiança", topico_revisar: "Tópico sem correspondência", erro_classificacao: "Erro de classificação", classificacao_ausente: "Sem classificação", formula_matematica: "Fórmula pode ter sido extraída incorretamente", glifo_estranho: "Símbolo incomum na extração", alternativas_incompletas: "Alternativas incompletas", enunciado_vazio: "Enunciado vazio", alternativa_vazia: "Alternativa vazia" })[reason] || reason.replaceAll("_", " ");
}

function renderReviewQuestion(examId, question, sharedTexts) {
  const number = String(question.numero);
  const review = question.review || {};
  const reviewed = review.status === "reviewed";
  const unusable = review.status === "unusable";
  const needsReview = Boolean(question.needsReview);
  const reprocessReasons = new Set([...(question.revisar || []), ...(question.reviewReasons || [])]);
  const canReprocess = unusable || (!reviewed && ["alternativas_incompletas", "alternativa_vazia", "enunciado_vazio", "glifo_estranho", "formula_matematica"].some(reason => reprocessReasons.has(reason)));
  const classification = question.classificacao || {};
  const subject = reviewed ? review.subject : (question.materia || classification.materia || "Não classificada");
  const topic = reviewed ? review.topic : (classification.assunto || "Tópico não definido");
  const confidence = Number.isFinite(Number(classification.confianca)) ? `${Math.round(Number(classification.confianca) * 100)}%` : "—";
  const shared = question.texto_id != null ? sharedTexts[String(question.texto_id)] : null;
  const sharedText = typeof shared === "string" ? shared : shared?.texto;
  const sharedImages = shared && typeof shared === "object" ? shared.figuras : [];
  const options = Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b));
  const reasons = (question.reviewReasons || []).map(reason => `<span class="review-reason-tag">${escapeHtml(reviewReasonLabel(reason))}</span>`).join("");
  const state = unusable ? `<span class="badge review-unusable">Inutilizável</span>` : reviewed ? `<span class="badge">Revisada</span>` : needsReview ? `<span class="badge pending">Precisa revisar</span>` : `<span class="badge">Sem pendências</span>`;
  const panel = needsReview ? `<form class="review-editor hidden" data-review-form="${number}"><div class="review-editor-heading"><strong>Revisar classificação</strong><span>Confirme ou corrija os rótulos usados nos exemplos locais.</span></div><div class="review-editor-fields"><label>Matéria<select data-review-subject="${number}" required><option value="">Selecione</option>${(currentExamReview.subjects || []).map(item => `<option value="${escapeHtml(item)}" ${review.subject === item || (!review.subject && item === classification.materia) ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label><label>Tópico<select data-review-topic="${number}" required><option value="">Selecione uma matéria primeiro</option></select></label></div><label>Observação <span class="optional">opcional</span><textarea data-review-note="${number}" rows="2" maxlength="2000">${escapeHtml(review.note || "")}</textarea></label><div class="review-editor-actions"><button class="button button-primary" type="submit">Salvar revisão</button><button class="button button-danger" type="button" data-mark-unusable="${number}">Marcar como inutilizável</button></div></form>` : "";
  const imagePaths = question.figuras_enunciado || [];
  const display = questionDisplayState(question);
  const hasInlineImages = imagePaths.length || (sharedImages || []).length || options.some(([, option]) => typeof option === "object" && option.figuras?.length);
  const fallbackImages = !display.showSource && !hasInlineImages ? (question.imagens_completas || []) : [];
  return `<article class="exam-review-question" data-question-card="${number}"><div class="exam-review-question-top"><div><strong>Questão ${escapeHtml(number)}</strong><span class="exam-review-subject">${escapeHtml(subject)} · ${escapeHtml(topic)}</span></div><div class="exam-review-state">${state}<span class="confidence-chip">Confiança IA: ${confidence}</span></div></div>
    ${reasons ? `<div class="review-reasons">${reasons}</div>` : ""}
    ${sharedText ? `<div class="question-body exam-passage"><strong>Texto-base</strong><div>${escapeHtml(sharedText)}</div>${examImageMarkup(examId, sharedImages, "passage-image")}</div>` : ""}
    <div class="question-body exam-prompt">${display.showStem ? escapeHtml(question.enunciado) : (!display.showSource ? "Enunciado não extraído" : "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${examImageMarkup(examId, imagePaths)}${renderSourceQuestionImage(examId, display.sourceImages)}${fallbackImages.length ? `<details class="exam-full-page"><summary>Ver imagem original da questão</summary>${examImageMarkup(examId, fallbackImages)}</details>` : ""}</div>
    <div class="exam-review-options">${options.map(([letter, option]) => { const text = display.showAlternativeText ? (typeof option === "string" ? option : (option?.texto || "Alternativa apresentada como imagem")) : "Alternativa apresentada na imagem da questão"; const figures = typeof option === "object" ? option.figuras : []; return `<div class="exam-review-option"><span class="exam-option-letter">${escapeHtml(letter)}</span><div>${escapeHtml(text)}${examImageMarkup(examId, figures, "option-image")}</div></div>`; }).join("")}</div>
    ${review.note ? `<p class="review-saved-note"><strong>Observação:</strong> ${escapeHtml(review.note)}</p>` : ""}
    ${needsReview ? `<div class="exam-review-actions"><button class="text-button" type="button" data-toggle-review="${number}">${reviewed || unusable ? "Editar revisão" : "Revisar questão"}</button></div>${panel}` : ""}
    ${canReprocess ? `<div class="reprocess-actions"><button class="button button-primary" type="button" data-reprocess="${number}">Reprocessar detalhadamente pelo PDF</button><label class="reprocess-upload">PDF ausente ou diferente? Enviar PDF<input type="file" accept="application/pdf,.pdf" data-reprocess-file="${number}"></label></div><div data-reprocess-preview="${number}"></div>` : ""}</article>`;
}

function refreshReviewTopic(number, selectedTopic = "") {
  const subjectSelect = $(`[data-review-subject="${number}"]`);
  const topicSelect = $(`[data-review-topic="${number}"]`);
  if (!subjectSelect || !topicSelect) return;
  const topics = currentExamReview.topics?.[subjectSelect.value] || [];
  topicSelect.innerHTML = `<option value="">Selecione</option>` + topics.map(topic => `<option value="${escapeHtml(topic)}" ${topic === selectedTopic ? "selected" : ""}>${escapeHtml(topic)}</option>`).join("");
}

function bindExamReviewActions() {
  const workspace = $("#exam-question-workspace");
  $("[data-close-exam-review]", workspace)?.addEventListener("click", () => {
    workspace.classList.add("hidden"); currentExamReview = null;
  });
  $("[data-start-quiz]", workspace)?.addEventListener("click", () => openExamQuiz(currentExamReview.exam.id));
  $("[data-retry-rag]", workspace)?.addEventListener("click", () => retryReviewIndex(currentExamReview.exam.id));
  $$('[data-toggle-review]', workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.toggleReview;
    const form = $(`[data-review-form="${number}"]`, workspace);
    form.classList.toggle("hidden");
    refreshReviewTopic(number, currentExamReview.questions.find(q => String(q.numero) === number)?.review?.topic || "");
  }));
  $$('[data-review-subject]', workspace).forEach(select => select.addEventListener("change", () => refreshReviewTopic(select.dataset.reviewSubject)));
  $$('[data-review-form]', workspace).forEach(form => form.addEventListener("submit", event => {
    event.preventDefault();
    const number = form.dataset.reviewForm;
    saveQuestionReview(number, { status: "reviewed", subject: $(`[data-review-subject="${number}"]`, form).value,
      topic: $(`[data-review-topic="${number}"]`, form).value, note: $(`[data-review-note="${number}"]`, form).value });
  }));
  $$('[data-mark-unusable]', workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.markUnusable;
    saveQuestionReview(number, { status: "unusable", note: $(`[data-review-note="${number}"]`, workspace).value });
  }));
  $$('[data-reprocess]', workspace).forEach(button => button.addEventListener("click", () =>
    reprocessUnusableQuestion(button.dataset.reprocess)));
  $$('[data-reprocess-file]', workspace).forEach(input => input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) reprocessUnusableQuestion(input.dataset.reprocessFile, file);
  }));
}

async function reprocessUnusableQuestion(number, file = null) {
  const examId = currentExamReview?.exam?.id;
  const slot = $(`[data-reprocess-preview="${number}"]`, $("#exam-question-workspace"));
  if (!examId || !slot) return;
  slot.innerHTML = `<div class="loading-card">Reprocessando a questão ${escapeHtml(number)} a partir do PDF…</div>`;
  try {
    const url = `/api/exams/${encodeURIComponent(examId)}/questions/${encodeURIComponent(number)}/reprocess${file ? `?filename=${encodeURIComponent(file.name)}` : ""}`;
    const response = await fetch(url, file ? { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file } : { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result?.error || `Falha HTTP ${response.status}`);
    const preview = await pollJob(result.jobId, job => {
      const progress = Math.max(0, Math.min(100, job.progress || 0));
      slot.innerHTML = `<div class="loading-card">${escapeHtml(job.message || "Reprocessando a questão…")}<div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div></div>`;
    });
    currentReprocessPreview = preview;
    renderReprocessPreview(slot, preview);
  } catch (error) {
    slot.innerHTML = `<div class="reprocess-error">${escapeHtml(error.message)}<p>Você pode enviar o PDF correto pelo campo acima.</p></div>`;
    toast(error.message, true);
  }
}

function renderReprocessPreview(slot, preview) {
  const question = preview.question || {};
  const alternatives = Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b));
  const shared = preview.sharedText;
  slot.innerHTML = `<section class="reprocess-preview"><div class="reprocess-preview-heading"><strong>Prévia detalhada · Questão ${escapeHtml(preview.number)}</strong><span>Texto recomposto e recorte completo da região da questão. Os dados atuais continuam preservados até você confirmar.</span></div>${shared ? `<div class="question-body exam-passage"><strong>Texto compartilhado · questões ${escapeHtml((shared.questoes || []).join(" e "))}</strong><div>${escapeHtml(shared.texto || "")}</div>${reprocessImageMarkup(preview.assetPrefix, shared.figuras, "passage-image")}</div>` : ""}<div class="question-body exam-prompt">${escapeHtml(question.enunciado || "Enunciado não extraído")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${reprocessImageMarkup(preview.assetPrefix, question.figuras_enunciado)}${reprocessImageMarkup(preview.assetPrefix, question.imagens_completas, "source-question-image")}</div><div class="exam-review-options">${alternatives.map(([letter, option]) => `<div class="exam-review-option"><span class="exam-option-letter">${escapeHtml(letter)}</span><div>${escapeHtml(typeof option === "string" ? option : option?.texto || "Alternativa como imagem")}${reprocessImageMarkup(preview.assetPrefix, option?.figuras || [], "option-image")}</div></div>`).join("")}</div><div class="review-editor-actions"><button type="button" class="button button-primary" data-confirm-reprocess="${preview.previewId}">Confirmar e reativar</button><button type="button" class="blank-button" data-cancel-reprocess="${preview.previewId}">Cancelar</button></div></section>`;
  $("[data-confirm-reprocess]", slot)?.addEventListener("click", () => confirmQuestionReprocess(preview, slot));
  $("[data-cancel-reprocess]", slot)?.addEventListener("click", () => cancelQuestionReprocess(preview, slot));
}

async function confirmQuestionReprocess(preview, slot) {
  try {
    const path = `/api/exams/${encodeURIComponent(preview.examId)}/questions/${encodeURIComponent(preview.number)}/reprocess/${encodeURIComponent(preview.previewId)}/confirm`;
    await api(path, { method: "POST" });
    currentReprocessPreview = null;
    currentExamReview = await api(`/api/exams/${encodeURIComponent(preview.examId)}/questions`);
    renderExamReview();
    await refreshExams();
    toast("Questão reprocessada e devolvida à revisão.");
  } catch (error) { slot.insertAdjacentHTML("afterbegin", `<div class="reprocess-error">${escapeHtml(error.message)}</div>`); }
}

async function cancelQuestionReprocess(preview, slot) {
  try {
    await api(`/api/exams/${encodeURIComponent(preview.examId)}/questions/${encodeURIComponent(preview.number)}/reprocess/${encodeURIComponent(preview.previewId)}`, { method: "POST" });
    currentReprocessPreview = null;
    slot.innerHTML = "";
  } catch (error) { toast(error.message, true); }
}

async function saveQuestionReview(number, value) {
  try {
    const examId = currentExamReview.exam.id;
    const result = await api(`/api/exams/${encodeURIComponent(examId)}/questions/${encodeURIComponent(number)}/review`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
    });
    currentExamReview = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
    renderExamReview();
    await refreshExams();
    if (result.ragJobId) {
      toast("Revisão salva; atualizando o índice local…");
      const indexed = await pollJob(result.ragJobId);
      currentExamReview = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
      renderExamReview();
      await refreshExams();
      toast(indexed.status === "pending" ? "Revisão salva; indexação pendente. Confira o Ollama e tente novamente." : "Revisão salva e acervo local atualizado.", indexed.status === "pending");
    }
  } catch (error) { toast(error.message, true); }
}

async function retryReviewIndex(examId) {
  try {
    const { jobId } = await api("/api/rag/reindex", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ examId }) });
    toast("Indexando exemplos revisados localmente…");
    const result = await pollJob(jobId);
    currentExamReview = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
    renderExamReview();
    await refreshExams();
    toast(result.status === "pending" ? "Indexação ainda pendente; verifique o modelo de embeddings no Ollama." : "Índice RAG local atualizado.", result.status === "pending");
  } catch (error) { toast(error.message, true); }
}

async function openExamQuiz(examId) {
  const workspace = $("#exam-quiz-workspace");
  workspace.classList.remove("hidden");
  $("#exam-question-workspace").classList.add("hidden");
  workspace.innerHTML = `<div class="loading-card">Carregando prova para responder…</div>`;
  try {
    const data = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
    const storageKey = `studai-exam-answers:${examId}`;
    let answers = {};
    try { answers = JSON.parse(sessionStorage.getItem(storageKey) || "{}"); } catch { answers = {}; }
    currentExamQuiz = { ...data, questions: data.questions.filter(question => question.review?.status !== "unusable"), index: 0, selected: null, answers, storageKey };
    renderExamQuiz();
    workspace.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    workspace.innerHTML = `<div class="exam-quiz-error">Não foi possível abrir a prova: ${escapeHtml(error.message)}<button class="blank-button" data-exam-close>Voltar à revisão</button></div>`;
    bindExamQuizActions();
  }
}

function renderExamQuiz() {
  const workspace = $("#exam-quiz-workspace");
  const quiz = currentExamQuiz;
  if (!quiz) return;
  const { exam, questions, sharedTexts = {}, answerKeys = {}, index } = quiz;
  const question = questions[index];
  if (!questions.length) {
    workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">${escapeHtml(exam.name)}</p><h2>Nenhuma questão disponível</h2><div class="workspace-meta">Todas as questões estão marcadas como inutilizáveis. Reprocesse e revalide-as na revisão da prova.</div></div><button class="blank-button" data-exam-close>Voltar à revisão</button></div>`;
    bindExamQuizActions();
    return;
  }
  if (!question) {
    const scored = questions.reduce((totals, item) => {
      const key = answerKeys[String(item.numero)];
      const response = quiz.answers[String(item.numero)];
      if (!response || !key) return totals;
      if (key.annulled || key.answer === "*") totals.annulled++;
      else { totals.graded++; if (response === key.answer) totals.correct++; }
      return totals;
    }, { graded: 0, correct: 0, annulled: 0 });
    workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">PROVA CONCLUÍDA</p><h2>${escapeHtml(exam.name)}</h2><div class="workspace-meta">${questions.length} questões respondidas nesta sessão</div></div><button class="blank-button" data-exam-close>Fechar</button></div><div class="exam-final-summary"><strong>${scored.graded ? `${scored.correct} de ${scored.graded} acertos` : "Respostas registradas"}</strong><p>${scored.annulled ? `${scored.annulled} questão(ões) anulada(s). ` : ""}${scored.graded < questions.length - scored.annulled ? "Algumas questões ainda não têm gabarito importado." : ""}</p></div><button class="button button-primary" data-exam-restart>Responder novamente</button>`;
    bindExamQuizActions();
    return;
  }
  const number = String(question.numero);
  const saved = quiz.answers[number] || null;
  const submitted = Boolean(saved);
  const key = answerKeys[number];
  const selected = submitted ? saved : quiz.selected;
  const shared = question.texto_id != null ? sharedTexts[String(question.texto_id)] : null;
  const sharedText = typeof shared === "string" ? shared : shared?.texto;
  const sharedImages = shared && typeof shared === "object" ? shared.figuras : [];
  const questionImages = question.figuras_enunciado || [];
  const display = questionDisplayState(question);
  const options = Object.entries(question.alternativas || {});
  const hasInlineImages = questionImages.length || (sharedImages || []).length || options.some(([, option]) => typeof option === "object" && option.figuras?.length);
  const fullPageImages = !display.showSource && !hasInlineImages ? (question.imagens_completas || []) : [];
  const outcome = submitted && key ? (key.annulled || key.answer === "*"
    ? `<div class="exam-answer-feedback annulled">Questão anulada no gabarito.</div>`
    : `<div class="exam-answer-feedback ${saved === key.answer ? "correct" : "incorrect"}">${saved === key.answer ? "Resposta correta!" : `Resposta incorreta. Gabarito: ${escapeHtml(key.answer)}.`}</div>`) : (submitted ? `<div class="exam-answer-feedback neutral">Resposta registrada. Ainda não há gabarito importado para esta questão.</div>` : "");
  const progress = Math.round(((index + 1) / questions.length) * 100);
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">${escapeHtml(exam.name)}</p><h2>Responda à prova</h2><div class="workspace-meta">Questão ${index + 1} de ${questions.length}${question.pagina ? ` · página ${question.pagina}` : ""}</div></div><button class="blank-button" data-exam-close>Fechar prova</button></div>
    <div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>
    ${sharedText ? `<div class="question-body exam-passage"><strong>Texto-base</strong><div>${escapeHtml(sharedText)}</div>${examImageMarkup(exam.id, sharedImages, "passage-image")}</div>` : ""}
    <div class="question-body exam-prompt">${display.showStem ? escapeHtml(question.enunciado) : (!display.showSource ? "Enunciado não extraído" : "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${examImageMarkup(exam.id, questionImages)}${renderSourceQuestionImage(exam.id, display.sourceImages)}${fullPageImages.length ? `<details class="exam-full-page"><summary>Ver imagem original da questão</summary>${examImageMarkup(exam.id, fullPageImages)}</details>` : ""}</div>
    <div class="exam-options">${options.map(([letter, option]) => { const value = display.showAlternativeText ? (typeof option === "string" ? option : (option?.texto || "")) : "Alternativa apresentada na imagem da questão"; const images = typeof option === "object" ? option.figuras : []; return `<button type="button" class="exam-option ${selected === letter ? "selected" : ""} ${submitted && key && key.answer === letter ? "answer-correct" : ""} ${submitted && saved === letter && key && key.answer !== letter && !key.annulled ? "answer-wrong" : ""}" data-exam-option="${escapeHtml(letter)}" ${submitted ? "disabled" : ""}><span class="exam-option-letter">${escapeHtml(letter)}</span><span class="exam-option-content">${escapeHtml(value)}${examImageMarkup(exam.id, images, "option-image")}</span></button>`; }).join("")}</div>
    ${outcome}<div class="exam-quiz-actions"><button class="blank-button" data-exam-prev ${index === 0 ? "disabled" : ""}>← Anterior</button><div>${submitted ? `<button class="button button-primary" data-exam-next>${index === questions.length - 1 ? "Ver resultado" : "Próxima questão"} →</button>` : `<button class="button button-primary" data-exam-submit ${selected ? "" : "disabled"}>Responder</button>`}</div></div>`;
  bindExamQuizActions();
}

function bindExamQuizActions() {
  const workspace = $("#exam-quiz-workspace");
  $$('[data-exam-option]', workspace).forEach(button => button.addEventListener("click", () => {
    if (!currentExamQuiz) return;
    currentExamQuiz.selected = button.dataset.examOption;
    renderExamQuiz();
  }));
  $("[data-exam-submit]", workspace)?.addEventListener("click", () => {
    const quiz = currentExamQuiz;
    if (!quiz?.selected) return;
    quiz.answers[String(quiz.questions[quiz.index].numero)] = quiz.selected;
    sessionStorage.setItem(quiz.storageKey, JSON.stringify(quiz.answers));
    quiz.selected = null;
    renderExamQuiz();
  });
  $("[data-exam-next]", workspace)?.addEventListener("click", () => { currentExamQuiz.index++; currentExamQuiz.selected = null; renderExamQuiz(); });
  $("[data-exam-prev]", workspace)?.addEventListener("click", () => { currentExamQuiz.index--; currentExamQuiz.selected = null; renderExamQuiz(); });
  $("[data-exam-close]", workspace)?.addEventListener("click", () => {
    workspace.classList.add("hidden");
    $("#exam-question-workspace").classList.remove("hidden");
  });
  $("[data-exam-restart]", workspace)?.addEventListener("click", () => {
    const quiz = currentExamQuiz;
    sessionStorage.removeItem(quiz.storageKey);
    quiz.answers = {}; quiz.index = 0; quiz.selected = null;
    renderExamQuiz();
  });
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

function setMultiSelectOptions(select, values, selectedValues = []) {
  select.innerHTML = values.length
    ? values.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")
    : `<option value="" disabled>Nenhum modelo disponível</option>`;
  const selected = new Set(selectedValues);
  [...select.options].forEach(option => { option.selected = selected.has(option.value); });
}

async function setupAIOptions() {
  const status = $("#ai-options-status");
  const geminiStatus = $("#gemini-key-status");
  try {
    const options = await api("/api/ai/options");
    const localSelect = $("#process-ollama-models");
    const geminiSelect = $("#process-gemini-models");
    const oldLocal = new Set([...localSelect.selectedOptions].map(option => option.value));
    const oldGemini = new Set([...geminiSelect.selectedOptions].map(option => option.value));
    const localSelection = options.ollamaModels.filter(model => oldLocal.has(model));
    if (!localSelection.length && options.ollamaModels.length) localSelection.push(options.ollamaModels[0]);
    const preferredGemini = options.geminiModels.filter(model => oldGemini.has(model));
    const geminiSelection = preferredGemini.length ? preferredGemini : options.geminiModels.filter(model =>
      /^gemini-3\.(?:8|7|6|5)-flash(?:-lite)?$/.test(model));
    setMultiSelectOptions(localSelect, options.ollamaModels, localSelection.slice(0, 2));
    setMultiSelectOptions(geminiSelect, options.geminiModels, geminiSelection);
    geminiSelect.disabled = !options.geminiConfigured || !options.geminiModels.length;
    localSelect.disabled = !options.ollamaModels.length;
    status.textContent = `${options.ollamaAvailable ? `${options.ollamaModels.length} modelo(s) Ollama` : "Ollama não encontrado"} · ${options.geminiConfigured ? `${options.geminiKeyCount} chave(s) Gemini configurada(s)` : "Gemini sem chaves configuradas"}`;
    geminiStatus.textContent = options.geminiConfigured
      ? `${options.geminiKeyCount} chave(s) no backend · nenhum segredo é exibido aqui.`
      : "Adicione GEMINI_API_KEYS ao .env local para ativar o Gemini.";
    if (options.geminiConfigured && !options.geminiModels.length) {
      geminiStatus.textContent = "Não foi possível listar modelos Gemini disponíveis para as chaves configuradas.";
    }
  } catch (error) {
    status.textContent = `Não foi possível carregar os modelos: ${error.message}`;
    geminiStatus.textContent = "Verifique se a API local está ativa.";
  }
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

function processConfig() {
  const ollamaModels = [...$("#process-ollama-models").selectedOptions].map(option => option.value).filter(Boolean);
  if (ollamaModels.length > 2) throw new Error("Selecione no máximo dois modelos Ollama.");
  return {
    ollamaModels,
    geminiModels: [...$("#process-gemini-models").selectedOptions].map(option => option.value).filter(Boolean),
    ollamaThreads: $("#process-ollama-threads").value || "0",
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
  if (!keyFile) return toast("Selecione o gabarito correspondente.", true);
  const button = $("#process-form button[type=submit]");
  const status = $("#process-status");
  button.disabled = true;
  button.textContent = "Enviando prova…";
  processUploadController = new AbortController();
  renderProcessProgress({ status: "running", stage: "upload_saved", message: `Enviando ${file.name}`, progress: 1 });
  try {
    const config = processConfig();
    if (!config.ollamaModels.length && !config.geminiModels.length) throw new Error("Selecione ao menos um modelo de IA disponível.");
    const record = { jobId: newClientJobId(), proof: file, keyFile, filename: file.name, keyFilename: keyFile.name, config, images: $("#process-images").checked };
    await savePendingProcess(record);
    await runSavedProcess(record, processUploadController.signal);
  } catch (error) {
    const cancelled = error.name === "AbortError" || /cancelado/i.test(error.message);
    if (cancelled) await clearPendingProcess();
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

async function submitSavedProcess(record, signal) {
  const query = new URLSearchParams({ filename: record.filename, provider: "pool", clientJobId: record.jobId,
    ollamaModels: (record.config.ollamaModels || []).join(","), geminiModels: (record.config.geminiModels || []).join(","),
    ollamaThreads: record.config.ollamaThreads || "0" });
  if (record.images) query.set("images", "sempre");
  return api(`/api/exams/process?${query}`, { method: "POST", headers: { "Content-Type": "application/pdf" }, body: record.proof, signal });
}

async function runSavedProcess(record, signal) {
  let job;
  try { job = await api(`/api/jobs/${encodeURIComponent(record.jobId)}`); }
  catch (error) {
    if (error.status !== 404) throw error;
    renderProcessProgress({ status: "running", stage: "upload_saved", message: `Retomando envio de ${record.filename}`, progress: 1 });
    await submitSavedProcess(record, signal);
  }
  currentProcessJob = record.jobId;
  const result = await pollJob(record.jobId, renderProcessProgress);
  let keySummary = "";
  renderProcessProgress({ status: "running", stage: "answer_key", progress: 95, message: "Importando respostas do gabarito…" });
  try {
    const imported = await api(`/api/exams/${encodeURIComponent(result.examId)}/answer-key?${new URLSearchParams({ filename: record.keyFilename })}`, {
      method: "POST", headers: { "Content-Type": "application/pdf" }, body: record.keyFile,
    });
    keySummary = `<br>${imported.imported} respostas do gabarito importadas.`;
  } catch (error) { keySummary = `<br>Prova pronta, mas não foi possível importar o gabarito: ${escapeHtml(error.message)}`; }
  await clearPendingProcess();
  const cacheSummary = result.examCacheHit ? " · prova reutilizada do cache" : result.cachedQuestions ? ` · ${result.cachedQuestions} questões reaproveitadas do cache` : "";
  $("#process-status").innerHTML = `<div class="inline-result ${result.classified ? "" : "error"}"><strong>${result.examCacheHit ? "Prova já processada" : result.classified ? "Prova processada" : "Extração concluída; classificação pendente"}</strong><br>${escapeHtml(result.name)} · ${result.questions} questões${escapeHtml(cacheSummary)}${keySummary}${result.classificationError ? `<br>${escapeHtml(result.classificationError)}` : ""}</div>`;
  $("#process-cancel").classList.add("hidden");
  $("#process-progress-subtitle").textContent = "Processamento finalizado.";
  toast(result.classified ? "Prova pronta no acervo." : "Prova extraída; confira a configuração do provedor.");
  $("#pdf-file").value = "";
  $("#key-pdf-file").value = "";
  $("#process-images").checked = false;
  $("#file-label").textContent = "Escolher PDF";
  $("#key-file-label").textContent = "Escolher PDF do gabarito";
  await refreshExams();
  showView("exams");
  await openExamReview(result.examId);
}

async function recoverActiveProcess() {
  if (!localStorage.getItem(ACTIVE_PROCESS_KEY)) return;
  const button = $("#process-form button[type=submit]");
  showView("process");
  button.disabled = true;
  button.textContent = "Retomando processamento…";
  try {
    const record = await readPendingProcess();
    if (!record?.jobId || !record.proof || !record.keyFile) throw new Error("Os PDFs salvos para retomar não estão disponíveis neste navegador.");
    currentProcessJob = record.jobId;
    renderProcessProgress({ status: "running", message: "Reconectando ao processamento…", progress: 2 });
    await runSavedProcess(record);
  } catch (error) {
    $("#process-status").innerHTML = `<div class="inline-result error">Não foi possível retomar o processamento: ${escapeHtml(error.message)}. Os arquivos continuam salvos; recarregue a página para tentar novamente.</div>`;
    $("#process-progress-subtitle").textContent = "A retomada pode ser tentada novamente.";
    if (error.message.includes("não estão disponíveis")) await clearPendingProcess();
  } finally {
    currentProcessJob = null;
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
  $("#diagnostic-provider").addEventListener("change", () => toggleProviderFields("diagnostic"));
}

applyTheme(localStorage.getItem("studai-theme") || "light");
bindEvents();
setupProviders();
setupAIOptions();
refreshExams();
refreshCourses();
refreshDiagnostics();
recoverActiveProcess();
