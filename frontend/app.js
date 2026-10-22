const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let exams = [];
let courses = [];
let currentDiagnostic = null;
let diagnosticSelection = null;
let diagnosticReasonPrompt = null;
const diagnosticDone = new Map();
let currentExamQuiz = null;
let currentStudy = null;
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
  if (name === "reports") window.refreshReports?.();
  if (name === "map") window.refreshKnowledgeMap?.();
  if (name === "training") refreshToday();
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
  const unit = job.unit || (job.stage === "classifying" || job.stage === "rendering" ? "questões" : "");
  const count = job.total ? `${job.completed || 0} de ${job.total} ${unit} ${job.stage === "extracting" ? "lidas" : job.stage === "rendering" ? "preparadas" : "processadas"}${job.cachedQuestions ? ` · ${job.cachedQuestions} do cache` : ""}${job.cacheMisses ? ` · ${job.cacheMisses} chamadas novas` : ""}${job.errors ? ` · ${job.errors} falha(s)` : ""}` : "";
  const progress = Math.max(0, Math.min(100, job.progress || 0));
  const steps = [["extracting", "Leitura e OCR"], ["rendering", "Recortes das questões"], ["classifying", "Classificação por IA"], ["answer_key", "Gabarito"]];
  const activeStep = job.stage === "upload_saved" || job.stage === "queued" ? -1 : steps.findIndex(([stage]) => stage === job.stage);
  const isDone = job.status === "completed" || job.stage === "completed";
  const stepper = steps.map(([stage, label], index) => {
    const complete = isDone || index < activeStep || (stage === "classifying" && job.stage === "answer_key") || (stage === "rendering" && job.stage === "classifying");
    const current = !isDone && index === activeStep;
    return `<div class="process-step ${complete ? "is-complete" : ""} ${current ? "is-current" : ""}" ${current ? 'aria-current="step"' : ""}><span>${complete ? "✓" : index + 1}</span><strong>${label}</strong></div>`;
  }).join("");
  status.innerHTML = `<div class="progress-summary"><div class="process-stepper" aria-label="Etapas do processamento">${stepper}</div><div class="progress-summary-top"><strong>${escapeHtml(job.message || "Preparando processamento…")}</strong><span>${progress}%</span></div><div class="progress-track" role="progressbar" aria-label="Progresso do processamento" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><div class="progress-fill" style="width:${progress}%"></div></div><div class="progress-meta"><span>${escapeHtml(job.stageLabel || stageLabel(job.stage))}</span><span>${escapeHtml(count)}</span></div>${active ? `<div class="active-work"><span class="pulse-dot"></span>Agora: ${escapeHtml(active)}</div>` : ""}</div>`;
  $("#process-progress-subtitle").textContent = job.status === "cancelling" ? "Cancelamento solicitado; aguardando a etapa atual." : job.stage === "extracting" && !job.total ? "A prova está sendo lida. PDFs escaneados podem levar mais tempo porque cada página passa por OCR." : "As etapas e a contagem são atualizadas enquanto o trabalho avança.";
  const cancel = $("#process-cancel");
  cancel.classList.toggle("hidden", !["queued", "running", "cancelling"].includes(job.status));
  cancel.disabled = job.status === "cancelling";
  cancel.textContent = job.status === "cancelling" ? "Cancelando…" : "Cancelar processamento";
}

function stageLabel(stage) {
  return ({ queued: "Na fila", upload_saved: "Arquivo recebido", extracting: "Lendo páginas do PDF", rendering: "Extraindo questões e imagens", extracted: "Extração concluída", classifying: "Classificação por IA", answer_key: "Importando gabarito", completed: "Concluído", cancelled: "Cancelado", failed: "Falhou" })[stage] || "Preparando";
}

function examCard(exam, compact = false) {
  const progress = Number(exam.reviewProgress ?? 100);
  // Revisão completa vale mais que a classificação automática: a prova pode ter sido
  // classificada sem erros ou ter falhas que a revisão manual resolveu.
  const manuallyReviewed = Number(exam.pendingReview) === 0 && progress === 100;
  const state = manuallyReviewed
    ? `<span class="badge" title="Todas as questões foram revisadas">Revisada</span>`
    : exam.classified
      ? `<span class="badge">Classificada</span>`
      : `<span class="badge pending">Pendente</span>`;
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

// Enunciado com a estrutura da prova: parágrafos e fórmulas em destaque (recorte do PDF).
// Só usa os blocos quando o texto não foi editado depois da extração.
function stemMarkup(question, imageMarkup) {
  const text = String(question.enunciado || "");
  const blocks = Array.isArray(question.blocos) ? question.blocos : [];
  const hasFormula = blocks.some(block => block?.tipo === "formula" && block.imagem);
  if (!hasFormula || blocks.map(block => block?.texto || "").join("\n") !== text) return escapeHtml(text);
  return blocks.map(block => block.tipo === "formula" && block.imagem
    ? `<span class="stem-formula" title="${escapeHtml(block.texto)}">${imageMarkup([block.imagem])}</span>`
    : `<span class="stem-paragraph">${escapeHtml(block.texto)}</span>`).join("");
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

// Texto-base e enunciado com as imagens da prova; usado no "Responder prova" e no estudo de hoje.
function questionContentMarkup(examId, question, sharedText, sharedImages = []) {
  const questionImages = question.figuras_enunciado || [];
  const display = questionDisplayState(question);
  const options = Object.entries(question.alternativas || {});
  const hasInlineImages = questionImages.length || (sharedImages || []).length || options.some(([, option]) => typeof option === "object" && option.figuras?.length);
  const fullPageImages = !display.showSource && !hasInlineImages ? (question.imagens_completas || []) : [];
  return `${sharedText ? `<div class="question-body exam-passage"><strong>Texto-base</strong><div>${escapeHtml(sharedText)}</div>${examImageMarkup(examId, sharedImages, "passage-image")}</div>` : ""}
    <div class="question-body exam-prompt">${display.showStem ? stemMarkup(question, paths => examImageMarkup(examId, paths, "formula-image")) : (!display.showSource ? "Enunciado não extraído" : "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${examImageMarkup(examId, questionImages)}${renderSourceQuestionImage(examId, display.sourceImages)}${fullPageImages.length ? `<details class="exam-full-page"><summary>Ver imagem original da questão</summary>${examImageMarkup(examId, fullPageImages)}</details>` : ""}</div>`;
}

function optionsMarkup(examId, question, { selected, submitted, correctAnswer, saved, attr }) {
  const display = questionDisplayState(question);
  return Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b)).map(([letter, option]) => {
    const value = display.showAlternativeText ? (typeof option === "string" ? option : (option?.texto || "")) : (typeof option === "object" && option?.figuras?.length ? "" : "Alternativa apresentada na imagem da questão");
    const images = typeof option === "object" ? option.figuras : [];
    const state = [selected === letter ? "selected" : "", submitted && correctAnswer === letter ? "answer-correct" : "", submitted && saved === letter && correctAnswer && correctAnswer !== letter ? "answer-wrong" : ""].join(" ");
    return `<button type="button" class="exam-option ${state}" ${attr}="${escapeHtml(letter)}" ${submitted ? "disabled" : ""}><span class="exam-option-letter">${escapeHtml(letter)}</span><span class="exam-option-content">${escapeHtml(value)}${examImageMarkup(examId, images, "option-image")}</span></button>`;
  }).join("");
}

function renderSourceQuestionImage(examId, paths, { collapsible = false } = {}) {
  return paths?.length
    ? (collapsible
      ? `<details class="source-question-image-panel" open><summary>Enunciado original da prova</summary>${examImageMarkup(examId, paths, "source-question-image")}</details>`
      : `<div class="source-question-image-panel"><strong>Enunciado original da prova</strong>${examImageMarkup(examId, paths, "source-question-image")}</div>`)
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
    loadReviewFilters(examId);
    reviewSelection = new Set();
    renderExamReview();
    workspace.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    workspace.innerHTML = `<div class="exam-quiz-error">Não foi possível abrir a prova: ${escapeHtml(error.message)}</div>`;
  }
}

const REVIEW_STATUS_LABELS = { pending: "Pendente", reviewed: "Aprovada", unusable: "Inutilizável", bad_crop: "Recorte ruim" };
const REVIEW_FILTER_DEFAULTS = { status: "", reason: "", subject: "", content: "", lowConfidence: false, hasImage: false, noAnswer: false, edited: false, query: "" };
const LOW_CONFIDENCE = 0.7;
let reviewFilters = { ...REVIEW_FILTER_DEFAULTS };
let reviewSelection = new Set();

function reviewFilterKey(examId) { return `studai-review-filters:${examId}`; }

function loadReviewFilters(examId) {
  try { reviewFilters = { ...REVIEW_FILTER_DEFAULTS, ...JSON.parse(localStorage.getItem(reviewFilterKey(examId)) || "{}") }; }
  catch { reviewFilters = { ...REVIEW_FILTER_DEFAULTS }; }
}

function storeReviewFilters(examId) {
  try { localStorage.setItem(reviewFilterKey(examId), JSON.stringify(reviewFilters)); } catch { /* preferência só deste navegador */ }
}

function reviewStatusOf(question) { return question.review?.status || "pending"; }

// Matéria e conteúdo vigentes: a escolha humana (aprovada ou salva sem aprovar) vale sobre a da IA.
function questionLabel(question) {
  const review = question.review || {};
  const classification = question.classificacao || {};
  if (review.subject) return { subject: review.subject, contentId: review.content_id || "", path: review.content_path || review.topic || "" };
  return { subject: question.materia || classification.materia || "", contentId: classification.conteudo_principal_id || "",
    path: classification.caminho_conteudo || classification.assunto || "" };
}

function questionHasImages(question) {
  return Boolean((question.figuras_enunciado || []).length ||
    Object.values(question.alternativas || {}).some(option => typeof option === "object" && option?.figuras?.length));
}

function questionMatchesFilters(question) {
  const f = reviewFilters;
  const label = questionLabel(question);
  const confidence = Number(question.classificacao?.confianca);
  if (f.status && reviewStatusOf(question) !== f.status) return false;
  if (f.reason && !(question.reviewReasons || []).includes(f.reason)) return false;
  if (f.subject && label.subject !== f.subject) return false;
  if (f.content && label.path !== f.content) return false;
  if (f.lowConfidence && !(Number.isFinite(confidence) && confidence < LOW_CONFIDENCE)) return false;
  if (f.hasImage && !questionHasImages(question)) return false;
  if (f.noAnswer && currentExamReview.answerKeys?.[String(question.numero)]) return false;
  if (f.edited && !question.review?.edits) return false;
  const query = f.query.trim().toLocaleLowerCase("pt-BR");
  if (query) {
    if (/^\d+$/.test(query)) return String(question.numero) === query;
    const options = Object.values(question.alternativas || {}).map(option => typeof option === "string" ? option : option?.texto || "");
    const haystack = [question.enunciado, label.path, question.review?.note, ...options].join(" ").toLocaleLowerCase("pt-BR");
    if (!haystack.includes(query)) return false;
  }
  return true;
}

function reviewFilterBar(questions) {
  const f = reviewFilters;
  const reasons = [...new Set(questions.flatMap(question => question.reviewReasons || []))].sort();
  const subjects = [...new Set(questions.map(question => questionLabel(question).subject).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
  const contents = [...new Set(questions.filter(question => !f.subject || questionLabel(question).subject === f.subject)
    .map(question => questionLabel(question).path).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
  const option = (value, label, selected) => `<option value="${escapeHtml(value)}" ${value === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
  const check = (key, label) => `<label class="check-row review-filter-check"><input type="checkbox" data-review-filter="${key}" ${f[key] ? "checked" : ""}>${escapeHtml(label)}</label>`;
  return `<div class="review-filters" role="search">
    <label>Buscar<input type="search" data-review-filter="query" value="${escapeHtml(f.query)}" placeholder="Número ou trecho do texto"></label>
    <label>Status<select data-review-filter="status">${option("", "Todos", f.status)}${Object.entries(REVIEW_STATUS_LABELS).map(([value, label]) => option(value, label, f.status)).join("")}</select></label>
    <label>Sinalização<select data-review-filter="reason">${option("", "Todas", f.reason)}${reasons.map(reason => option(reason, reviewReasonLabel(reason), f.reason)).join("")}</select></label>
    <label>Matéria<select data-review-filter="subject">${option("", "Todas", f.subject)}${subjects.map(subject => option(subject, subject, f.subject)).join("")}</select></label>
    <label>Conteúdo<select data-review-filter="content">${option("", "Todos", f.content)}${contents.map(path => option(path, path, f.content)).join("")}</select></label>
    <div class="review-filter-checks">${check("lowConfidence", `Confiança IA < ${Math.round(LOW_CONFIDENCE * 100)}%`)}${check("hasImage", "Com imagem")}${check("noAnswer", "Sem gabarito")}${check("edited", "Editadas")}</div>
    <div class="review-filter-summary"><span data-review-visible></span><button class="text-button" type="button" data-clear-review-filters>Limpar filtros</button></div>
  </div>
  <div class="review-bulk-bar"><label class="check-row"><input type="checkbox" data-select-visible>Selecionar visíveis</label><span data-selection-count></span><button class="button button-primary" type="button" data-bulk-approve disabled>Aprovar selecionadas</button></div>`;
}

function applyReviewFilters() {
  const workspace = $("#exam-question-workspace");
  const questions = currentExamReview?.questions || [];
  let visible = 0;
  questions.forEach(question => {
    const card = $(`[data-question-card="${question.numero}"]`, workspace);
    const show = questionMatchesFilters(question);
    if (card) card.classList.toggle("hidden", !show);
    if (show) visible += 1;
  });
  const counter = $("[data-review-visible]", workspace);
  if (counter) counter.textContent = `${visible} de ${questions.length} questões visíveis`;
  updateSelectionBar();
}

function visibleReviewNumbers() {
  return (currentExamReview?.questions || []).filter(questionMatchesFilters).map(question => String(question.numero));
}

function updateSelectionBar() {
  const workspace = $("#exam-question-workspace");
  const count = reviewSelection.size;
  const label = $("[data-selection-count]", workspace);
  if (label) label.textContent = count ? `${count} selecionada(s)` : "Nenhuma selecionada";
  const button = $("[data-bulk-approve]", workspace);
  if (button) button.disabled = !count;
  const all = $("[data-select-visible]", workspace);
  const visible = visibleReviewNumbers();
  if (all) all.checked = visible.length > 0 && visible.every(number => reviewSelection.has(number));
}

function renderExamReview() {
  const workspace = $("#exam-question-workspace");
  const data = currentExamReview;
  if (!data) return;
  const { exam, questions, sharedTexts = {}, reviewProgress = {}, ragStatus = {} } = data;
  const progress = Number(reviewProgress.reviewProgress ?? exam.reviewProgress ?? 100);
  const ragLabel = ragStatus.status === "ready" ? `${ragStatus.examples || 0} exemplo(s) aprovados no RAG local` :
    ragStatus.status === "indexing" || ragStatus.status === "queued" ? "Indexação local em andamento" :
      ragStatus.status === "pending" ? `Índice pendente: ${ragStatus.error || "Ollama de embeddings indisponível"}` : "Nenhum exemplo aprovado no índice";
  const known = new Set(questions.map(question => String(question.numero)));
  reviewSelection = new Set([...reviewSelection].filter(number => known.has(number)));
  const cards = questions.map(question => renderReviewQuestion(exam.id, question, sharedTexts)).join("");
  const counter = (key, label, cls = "") => `<span class="review-count ${cls}"><strong>${reviewProgress[key] ?? 0}</strong>${label}</span>`;
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">PROVA E GABARITO</p><h2>${escapeHtml(exam.name)}</h2><div class="workspace-meta">${questions.length} questões · ${exam.importedAnswers ?? 0} respostas importadas · ${reviewProgress.reviewedQuestions ?? 0}/${reviewProgress.reviewRequired ?? 0} sinalizadas pela IA já decididas</div></div><div class="review-header-actions"><button class="button button-primary" data-start-quiz="${escapeHtml(exam.id)}">Responder prova</button><button class="blank-button" data-close-exam-review>Fechar</button></div></div>
    <div class="exam-review-progress"><div class="progress-label"><strong>Revisão da prova</strong><span>${progress}%</span></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><div class="review-counts">${counter("approved", "aprovadas", "is-approved")}${counter("pending", "pendentes", "is-pending")}${counter("unusable", "inutilizáveis")}${counter("badCrop", "recorte ruim", "is-bad-crop")}</div><div class="workspace-meta">${escapeHtml(ragLabel)} · só questões aprovadas entram no RAG</div>${ragStatus.status === "pending" ? `<button class="text-button" data-retry-rag="${escapeHtml(exam.id)}">Tentar indexar novamente</button>` : ""}</div>
    ${questions.length ? reviewFilterBar(questions) : ""}
    <div class="exam-review-list">${cards || `<div class="loading-card">Esta prova não contém questões extraídas.</div>`}</div>`;
  bindExamReviewActions();
  applyReviewFilters();
}

function reviewReasonLabel(reason) {
  return ({ classificacao_baixa_confianca: "Baixa confiança", topico_revisar: "Tópico sem correspondência", erro_classificacao: "Erro de classificação", classificacao_ausente: "Sem classificação", subconteudo_pendente: "Subconteúdo pendente", formula_matematica: "Fórmula pode ter sido extraída incorretamente", glifo_estranho: "Símbolo incomum na extração", alternativas_incompletas: "Alternativas incompletas", enunciado_vazio: "Enunciado vazio", alternativa_vazia: "Alternativa vazia", texto_ocr: "Texto lido por OCR" })[reason] || reason.replaceAll("_", " ");
}

function figureEditorItems(examId, paths) {
  return (paths || []).map(path => `<span class="review-figure" data-figure-path="${escapeHtml(path)}"><img src="${examAssetUrl(examId, path)}" alt="Figura" loading="lazy"><button type="button" class="blank-button" data-remove-figure title="Remover figura">Remover</button></span>`).join("");
}

function figureEditor(examId, number, target, paths) {
  return `<div class="review-figures" data-figures="${escapeHtml(target)}">${figureEditorItems(examId, paths)}<label class="review-figure-upload">+ Enviar imagem<input type="file" accept="image/png,image/jpeg,image/webp" data-upload-figure="${escapeHtml(target)}" data-upload-question="${number}"></label></div>`;
}

function renderReviewEditor(examId, question) {
  const number = String(question.numero);
  const review = question.review || {};
  const label = questionLabel(question);
  const answer = currentExamReview.answerKeys?.[number]?.answer || "";
  const letters = [...new Set([...Object.keys(question.alternativas || {}), ..."ABCDE"])].sort();
  const options = Object.fromEntries(Object.entries(question.alternativas || {}));
  return `<form class="review-editor hidden" data-review-form="${number}"><div class="review-editor-heading"><strong>Revisar questão ${escapeHtml(number)}</strong><span>As correções ficam salvas na revisão; a extração original do PDF é mantida.</span></div>
    <div class="review-editor-fields"><label>Matéria<select data-review-subject="${number}"><option value="">Selecione</option>${(currentExamReview.subjects || []).map(item => `<option value="${escapeHtml(item)}" ${item === label.subject ? "selected" : ""}>${escapeHtml(item)}</option>`).join("")}</select></label><div class="review-topic-field"><label for="review-topic-search-${number}">Conteúdo principal</label><input id="review-topic-search-${number}" type="search" data-review-topic-search="${number}" placeholder="Buscar: logaritmo, Clarice, ecologia…" autocomplete="off"><select data-review-topic="${number}" size="9" aria-label="Conteúdo principal"><option value="">Selecione uma matéria primeiro</option></select><span class="review-topic-path" data-review-topic-path="${number}"></span></div></div>
    <label>Gabarito<select data-review-answer="${number}"><option value="" ${answer ? "" : "selected"}>Sem gabarito</option>${"ABCDE".split("").map(letter => `<option value="${letter}" ${answer === letter ? "selected" : ""}>${letter}</option>`).join("")}<option value="*" ${answer === "*" ? "selected" : ""}>Anulada</option></select></label>
    <label>Enunciado<textarea data-edit-stem rows="5">${escapeHtml(question.enunciado || "")}</textarea></label>
    ${figureEditor(examId, number, "stem", question.figuras_enunciado)}
    <div class="review-edit-options">${letters.map(letter => { const option = options[letter]; const text = typeof option === "string" ? option : option?.texto || ""; const figures = typeof option === "object" ? option?.figuras : []; return `<div class="review-edit-option" data-edit-option="${letter}"><span class="exam-option-letter">${letter}</span><div><textarea rows="2" data-edit-option-text placeholder="${option ? "" : "Alternativa ausente na extração"}">${escapeHtml(text)}</textarea>${figureEditor(examId, number, `option:${letter}`, figures)}</div></div>`; }).join("")}</div>
    <label>Observação <span class="optional">opcional</span><textarea data-review-note="${number}" rows="2" maxlength="2000">${escapeHtml(review.note || "")}</textarea></label>
    <div class="review-editor-actions"><button class="button button-primary" type="submit">Salvar e aprovar</button><button class="button button-secondary" type="button" data-save-draft="${number}">Salvar sem aprovar</button>${review.edits ? `<button class="blank-button" type="button" data-discard-edits="${number}">Descartar correções</button>` : ""}</div></form>`;
}

function renderReviewQuestion(examId, question, sharedTexts) {
  const number = String(question.numero);
  const review = question.review || {};
  const status = reviewStatusOf(question);
  const unusable = status === "unusable";
  const reprocessReasons = new Set([...(question.revisar || []), ...(question.reviewReasons || [])]);
  const canReprocess = question.reprocessRecommended ?? (unusable || ["alternativas_incompletas", "alternativa_vazia", "enunciado_vazio", "glifo_estranho", "formula_matematica"].some(reason => reprocessReasons.has(reason)));
  const classification = question.classificacao || {};
  const label = questionLabel(question);
  const confidence = Number.isFinite(Number(classification.confianca)) ? `${Math.round(Number(classification.confianca) * 100)}%` : "—";
  const shared = question.texto_id != null ? sharedTexts[String(question.texto_id)] : null;
  const sharedText = typeof shared === "string" ? shared : shared?.texto;
  const sharedImages = shared && typeof shared === "object" ? shared.figuras : [];
  const options = Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b));
  const reasons = (question.reviewReasons || []).map(reason => `<span class="review-reason-tag">${escapeHtml(reviewReasonLabel(reason))}</span>`).join("");
  const answer = currentExamReview.answerKeys?.[number];
  const stateClass = { reviewed: "", pending: "pending", unusable: "review-unusable", bad_crop: "review-bad-crop" }[status];
  const state = `<span class="badge ${stateClass}">${REVIEW_STATUS_LABELS[status]}</span>${review.edits ? `<span class="badge review-edited" title="Corrigida na revisão">Editada</span>` : ""}`;
  const imagePaths = question.figuras_enunciado || [];
  // Texto corrigido à mão é exibido mesmo que a extração original tenha sido sinalizada.
  const display = review.edits ? { showSource: false, sourceImages: [], showStem: true, showAlternativeText: true } : questionDisplayState(question);
  const hasInlineImages = imagePaths.length || (sharedImages || []).length || options.some(([, option]) => typeof option === "object" && option.figuras?.length);
  const fallbackImages = !display.showSource && !hasInlineImages ? (question.imagens_completas || []) : [];
  const actions = `<div class="exam-review-actions">${status !== "reviewed" ? `<button class="button button-primary" type="button" data-quick-approve="${number}" title="Aprova com a matéria e o conteúdo mostrados">✓ Aprovar</button>` : ""}<button class="button button-secondary" type="button" data-toggle-review="${number}">Editar</button>${status !== "bad_crop" ? `<button class="button button-secondary" type="button" data-set-status="bad_crop" data-status-question="${number}">Recorte ruim</button>` : ""}${status !== "unusable" ? `<button class="button button-danger" type="button" data-set-status="unusable" data-status-question="${number}">Inutilizável</button>` : ""}${status !== "pending" ? `<button class="blank-button" type="button" data-set-status="pending" data-status-question="${number}">Voltar para pendente</button>` : ""}${canReprocess ? "" : `<button class="blank-button" type="button" data-reprocess="${number}" title="Extrai de novo só esta questão; nada muda até você confirmar">Reprocessar pelo PDF</button>`}</div>`;
  return `<article class="exam-review-question review-status-${status}" data-question-card="${number}"><div class="exam-review-question-top"><div><label class="check-row review-select"><input type="checkbox" data-select-question="${number}" ${reviewSelection.has(number) ? "checked" : ""}><strong>Questão ${escapeHtml(number)}</strong></label><span class="exam-review-subject">${escapeHtml(label.subject || "Não classificada")} · ${escapeHtml(label.path || "Tópico não definido")}</span></div><div class="exam-review-state">${state}<span class="confidence-chip">Confiança IA: ${confidence}</span><span class="confidence-chip">Gabarito: ${answer ? (answer.annulled ? "anulada" : escapeHtml(answer.answer)) : "—"}</span></div></div>
    ${reasons ? `<div class="review-reasons">${reasons}</div>` : ""}
    ${sharedText ? `<div class="question-body exam-passage"><strong>Texto-base</strong><div>${escapeHtml(sharedText)}</div>${examImageMarkup(examId, sharedImages, "passage-image")}</div>` : ""}
    <div class="question-body exam-prompt">${display.showStem ? stemMarkup(question, paths => examImageMarkup(examId, paths, "formula-image")) : (!display.showSource ? "Enunciado não extraído" : "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${examImageMarkup(examId, imagePaths)}${renderSourceQuestionImage(examId, display.sourceImages)}${fallbackImages.length ? `<details class="exam-full-page"><summary>Ver imagem original da questão</summary>${examImageMarkup(examId, fallbackImages)}</details>` : ""}</div>
    <div class="exam-review-options">${options.map(([letter, option]) => { const hasFigure = typeof option === "object" && option?.figuras?.length; const text = display.showAlternativeText ? (typeof option === "string" ? option : (option?.texto || (hasFigure ? "" : "Alternativa sem texto"))) : (hasFigure ? "" : "Alternativa apresentada na imagem da questão"); const figures = typeof option === "object" ? option.figuras : []; return `<div class="exam-review-option"><span class="exam-option-letter">${escapeHtml(letter)}</span><div>${escapeHtml(text)}${examImageMarkup(examId, figures, "option-image")}</div></div>`; }).join("")}</div>
    ${review.note ? `<p class="review-saved-note"><strong>Observação:</strong> ${escapeHtml(review.note)}</p>` : ""}
    ${actions}${renderReviewEditor(examId, question)}
    ${canReprocess ? `<div class="reprocess-actions"><button class="button button-primary" type="button" data-reprocess="${number}">Reprocessar detalhadamente pelo PDF</button><label class="reprocess-upload">PDF ausente ou diferente? Enviar PDF<input type="file" accept="application/pdf,.pdf" data-reprocess-file="${number}"></label></div>` : ""}<div data-reprocess-preview="${number}"></div></article>`;
}

function foldText(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
}

// Caminho "matéria → área → conteúdo → subconteúdo": a matéria já está no outro campo,
// então o grupo mostra "área › conteúdo" e a opção só o subconteúdo.
// Um conteúdo intermediário (que tem subconteúdos) entra no grupo dos próprios filhos,
// como primeira opção, para aprovar a questão no nível geral sem descer.
function contentParts(content) {
  const parts = String(content.path || "").split(" → ").slice(1);
  if (content.intermediate && parts.length) return { group: parts.join(" › "), name: `${parts[parts.length - 1]} (conteúdo geral)` };
  const name = parts.pop() || content.name || content.id;
  return { group: parts.join(" › ") || "Geral", name };
}

function refreshReviewTopic(number, selectedTopic) {
  const subjectSelect = $(`[data-review-subject="${number}"]`);
  const topicSelect = $(`[data-review-topic="${number}"]`);
  const search = $(`[data-review-topic-search="${number}"]`);
  if (!subjectSelect || !topicSelect) return;
  const selected = selectedTopic ?? topicSelect.value;
  const contents = currentExamReview.contents?.[subjectSelect.value] || [];
  const query = foldText(search?.value).trim();
  const question = currentExamReview.questions.find(q => String(q.numero) === String(number));
  const suggestedId = question?.classificacao?.conteudo_principal_id;
  const suggested = contents.find(content => content.id === suggestedId);
  const option = (content, label) => `<option value="${escapeHtml(content.id)}" title="${escapeHtml(content.path)}" ${content.id === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
  const groups = new Map();
  contents.forEach(content => {
    if (query && !foldText(content.path).includes(query) && content.id !== selected) return;
    const { group, name } = contentParts(content);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(option(content, name));
  });
  const head = !subjectSelect.value ? `<option value="">Selecione uma matéria primeiro</option>`
    : !groups.size ? `<option value="" disabled>Nenhum conteúdo encontrado</option>` : "";
  const suggestion = suggested && !query ? `<optgroup label="Sugestão da IA">${option({ ...suggested, id: suggested.id }, contentParts(suggested).name)}</optgroup>` : "";
  topicSelect.innerHTML = head + suggestion + [...groups].map(([group, items]) => `<optgroup label="${escapeHtml(group)}">${items.join("")}</optgroup>`).join("");
  // A sugestão repete um item da lista; mantém marcada só a primeira ocorrência.
  const marked = $$("option[selected]", topicSelect);
  marked.slice(1).forEach(item => item.removeAttribute("selected"));
  if (marked[0]) topicSelect.value = marked[0].value;
  updateReviewTopicPath(number);
}

function updateReviewTopicPath(number) {
  const topicSelect = $(`[data-review-topic="${number}"]`);
  const target = $(`[data-review-topic-path="${number}"]`);
  if (!topicSelect || !target) return;
  const option = topicSelect.selectedOptions[0];
  target.textContent = option?.value ? `Selecionado: ${option.title}` : "Nenhum conteúdo selecionado";
  target.classList.toggle("is-empty", !option?.value);
}

function figurePaths(container) {
  return $$("[data-figure-path]", container).map(item => item.dataset.figurePath);
}

// Só manda a camada de correções quando algo mudou em relação ao que está exibido.
function collectReviewEdits(form, question) {
  const stemFigures = figurePaths($('[data-figures="stem"]', form));
  const alternatives = {};
  $$("[data-edit-option]", form).forEach(row => {
    const letter = row.dataset.editOption;
    const text = $("[data-edit-option-text]", row).value;
    const figures = figurePaths($(`[data-figures="option:${letter}"]`, row));
    if (text.trim() || figures.length || question.alternativas?.[letter]) alternatives[letter] = { texto: text, figuras: figures };
  });
  const edits = { enunciado: $("[data-edit-stem]", form).value, figuras_enunciado: stemFigures, alternativas: alternatives };
  const current = { enunciado: question.enunciado || "", figuras_enunciado: question.figuras_enunciado || [],
    alternativas: Object.fromEntries(Object.entries(question.alternativas || {}).map(([letter, option]) =>
      [letter, { texto: typeof option === "string" ? option : option?.texto || "", figuras: typeof option === "object" ? option?.figuras || [] : [] }])) };
  const sameOptions = Object.keys({ ...alternatives, ...current.alternativas }).every(letter =>
    JSON.stringify(alternatives[letter] || null) === JSON.stringify(current.alternativas[letter] || null));
  const changed = edits.enunciado !== current.enunciado || JSON.stringify(stemFigures) !== JSON.stringify(current.figuras_enunciado) || !sameOptions;
  return changed ? edits : undefined;
}

function reviewPayloadFromForm(form, status) {
  const number = form.dataset.reviewForm;
  const question = currentExamReview.questions.find(q => String(q.numero) === number);
  const payload = { status, subject: $(`[data-review-subject="${number}"]`, form).value || undefined,
    content_id: $(`[data-review-topic="${number}"]`, form).value || undefined, note: $(`[data-review-note="${number}"]`, form).value };
  const edits = collectReviewEdits(form, question);
  if (edits) payload.edits = edits;
  const answer = $(`[data-review-answer="${number}"]`, form).value;
  if (answer !== (currentExamReview.answerKeys?.[number]?.answer || "")) payload.answer = answer;
  return payload;
}

async function uploadReviewFigure(input) {
  const file = input.files?.[0];
  if (!file) return;
  const examId = currentExamReview.exam.id;
  try {
    const { path } = await api(`/api/exams/${encodeURIComponent(examId)}/questions/${encodeURIComponent(input.dataset.uploadQuestion)}/images`,
      { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
    input.closest(".review-figure-upload").insertAdjacentHTML("beforebegin", figureEditorItems(examId, [path]));
    bindFigureRemoval(input.closest("[data-figures]"));
  } catch (error) { toast(error.message, true); }
  input.value = "";
}

function bindFigureRemoval(root) {
  $$("[data-remove-figure]", root).forEach(button => { button.onclick = () => button.closest("[data-figure-path]").remove(); });
}

function bindExamReviewActions() {
  const workspace = $("#exam-question-workspace");
  const examId = currentExamReview.exam.id;
  $("[data-close-exam-review]", workspace)?.addEventListener("click", () => {
    workspace.classList.add("hidden"); currentExamReview = null; reviewSelection = new Set();
  });
  $("[data-start-quiz]", workspace)?.addEventListener("click", () => openExamQuiz(examId));
  $("[data-retry-rag]", workspace)?.addEventListener("click", () => retryReviewIndex(examId));
  $$("[data-review-filter]", workspace).forEach(control => control.addEventListener(control.type === "search" ? "input" : "change", () => {
    const key = control.dataset.reviewFilter;
    reviewFilters[key] = control.type === "checkbox" ? control.checked : control.value;
    if (key === "subject") reviewFilters.content = "";
    storeReviewFilters(examId);
    if (key === "subject") { renderExamReview(); return; }  // a lista de conteúdos depende da matéria
    applyReviewFilters();
  }));
  $("[data-clear-review-filters]", workspace)?.addEventListener("click", () => {
    reviewFilters = { ...REVIEW_FILTER_DEFAULTS }; storeReviewFilters(examId); renderExamReview();
  });
  $$("[data-select-question]", workspace).forEach(box => box.addEventListener("change", () => {
    if (box.checked) reviewSelection.add(box.dataset.selectQuestion); else reviewSelection.delete(box.dataset.selectQuestion);
    updateSelectionBar();
  }));
  $("[data-select-visible]", workspace)?.addEventListener("change", event => {
    const visible = visibleReviewNumbers();
    visible.forEach(number => event.target.checked ? reviewSelection.add(number) : reviewSelection.delete(number));
    visible.forEach(number => { const box = $(`[data-select-question="${number}"]`, workspace); if (box) box.checked = event.target.checked; });
    updateSelectionBar();
  });
  $("[data-bulk-approve]", workspace)?.addEventListener("click", () => bulkApproveQuestions([...reviewSelection]));
  $$("[data-quick-approve]", workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.quickApprove;
    const question = currentExamReview.questions.find(q => String(q.numero) === number);
    const label = questionLabel(question);
    if (!label.subject || !label.contentId) {
      toast("Escolha matéria e conteúdo antes de aprovar.", true);
      $(`[data-toggle-review="${number}"]`, workspace)?.click();
      return;
    }
    saveQuestionReview(number, { status: "reviewed", subject: label.subject, content_id: label.contentId, note: question.review?.note || "" });
  }));
  $$("[data-set-status]", workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.statusQuestion;
    const question = currentExamReview.questions.find(q => String(q.numero) === number);
    saveQuestionReview(number, { status: button.dataset.setStatus, note: $(`[data-review-note="${number}"]`, workspace)?.value ?? question?.review?.note ?? "" });
  }));
  $$('[data-toggle-review]', workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.toggleReview;
    const form = $(`[data-review-form="${number}"]`, workspace);
    form.classList.toggle("hidden");
    const question = currentExamReview.questions.find(q => String(q.numero) === number);
    refreshReviewTopic(number, questionLabel(question).contentId);
  }));
  $$('[data-review-subject]', workspace).forEach(select => select.addEventListener("change", () => {
    const search = $(`[data-review-topic-search="${select.dataset.reviewSubject}"]`, workspace);
    if (search) search.value = "";
    refreshReviewTopic(select.dataset.reviewSubject, "");
  }));
  $$('[data-review-topic-search]', workspace).forEach(input => input.addEventListener("input", () => refreshReviewTopic(input.dataset.reviewTopicSearch)));
  $$('[data-review-topic]', workspace).forEach(select => select.addEventListener("change", () => updateReviewTopicPath(select.dataset.reviewTopic)));
  $$('[data-review-form]', workspace).forEach(form => {
    bindFigureRemoval(form);
    form.addEventListener("submit", event => {
      event.preventDefault();
      const payload = reviewPayloadFromForm(form, "reviewed");
      if (!payload.subject || !payload.content_id) { toast("Escolha matéria e conteúdo para aprovar.", true); return; }
      saveQuestionReview(form.dataset.reviewForm, payload);
    });
  });
  $$("[data-save-draft]", workspace).forEach(button => button.addEventListener("click", () => {
    const form = button.closest("[data-review-form]");
    const question = currentExamReview.questions.find(q => String(q.numero) === form.dataset.reviewForm);
    const status = reviewStatusOf(question);
    const payload = reviewPayloadFromForm(form, status);
    if (status === "reviewed" && (!payload.subject || !payload.content_id)) { toast("Questão aprovada precisa de matéria e conteúdo.", true); return; }
    if (payload.subject && !payload.content_id) delete payload.subject;
    saveQuestionReview(form.dataset.reviewForm, payload);
  }));
  $$("[data-discard-edits]", workspace).forEach(button => button.addEventListener("click", () => {
    const number = button.dataset.discardEdits;
    const question = currentExamReview.questions.find(q => String(q.numero) === number);
    const label = questionLabel(question);
    if (!window.confirm("Descartar as correções e voltar ao texto extraído do PDF?")) return;
    saveQuestionReview(number, { status: reviewStatusOf(question), subject: label.subject || undefined,
      content_id: label.contentId || undefined, note: question.review?.note || "", edits: null });
  }));
  $$("[data-upload-figure]", workspace).forEach(input => input.addEventListener("change", () => uploadReviewFigure(input)));
  $$('[data-reprocess]', workspace).forEach(button => button.addEventListener("click", () =>
    reprocessUnusableQuestion(button.dataset.reprocess)));
  $$('[data-reprocess-file]', workspace).forEach(input => input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) reprocessUnusableQuestion(input.dataset.reprocessFile, file);
  }));
}

async function bulkApproveQuestions(numbers) {
  if (!numbers.length) return;
  const examId = currentExamReview.exam.id;
  if (!window.confirm(`Aprovar ${numbers.length} questão(ões) com a matéria e o conteúdo mostrados?`)) return;
  try {
    const result = await api(`/api/exams/${encodeURIComponent(examId)}/reviews/bulk`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ numbers: numbers.map(Number) }),
    });
    reviewSelection = new Set(result.skipped.map(item => String(item.numero)));
    currentExamReview = await api(`/api/exams/${encodeURIComponent(examId)}/questions`);
    renderExamReview();
    await refreshExams();
    const skipped = result.skipped.length ? ` ${result.skipped.length} ficaram de fora (seguem selecionadas): ${result.skipped.map(item => `Q${item.numero} – ${item.motivo}`).join("; ")}.` : "";
    toast(`${result.approved.length} questão(ões) aprovada(s).${skipped}`, Boolean(result.skipped.length));
  } catch (error) { toast(error.message, true); }
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
  slot.innerHTML = `<section class="reprocess-preview"><div class="reprocess-preview-heading"><strong>Prévia detalhada · Questão ${escapeHtml(preview.number)}</strong><span>Texto recomposto e recorte completo da região da questão. Os dados atuais continuam preservados até você confirmar.</span></div>${shared ? `<div class="question-body exam-passage"><strong>Texto compartilhado · questões ${escapeHtml((shared.questoes || []).join(" e "))}</strong><div>${escapeHtml(shared.texto || "")}</div>${reprocessImageMarkup(preview.assetPrefix, shared.figuras, "passage-image")}</div>` : ""}<div class="question-body exam-prompt">${question.enunciado ? stemMarkup(question, paths => reprocessImageMarkup(preview.assetPrefix, paths, "formula-image")) : "Enunciado não extraído"}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${reprocessImageMarkup(preview.assetPrefix, question.figuras_enunciado)}${reprocessImageMarkup(preview.assetPrefix, question.imagens_completas, "source-question-image")}</div><div class="exam-review-options">${alternatives.map(([letter, option]) => `<div class="exam-review-option"><span class="exam-option-letter">${escapeHtml(letter)}</span><div>${escapeHtml(typeof option === "string" ? option : option?.texto || (option?.figuras?.length ? "" : "Alternativa sem texto"))}${reprocessImageMarkup(preview.assetPrefix, option?.figuras || [], "option-image")}</div></div>`).join("")}</div><div class="review-editor-actions"><button type="button" class="button button-primary" data-confirm-reprocess="${preview.previewId}">Confirmar e reativar</button><button type="button" class="blank-button" data-cancel-reprocess="${preview.previewId}">Cancelar</button></div></section>`;
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

// ---- Persistência do "Responder prova" (attempts.mode = 'exam'); sessionStorage segue como fallback offline.
const EXAM_SESSION_PREFIX = "studai-exam-session:";
const EXAM_REASONS = [["unknown_content", "Não sabia o conteúdo"], ["calc_error", "Erro de cálculo"], ["misread", "Interpretação"]];

function examSessionCourseId() {
  // Mesmo curso do formulário de diagnóstico (padrão: Eng. de Computação São Carlos).
  return $("#course-select")?.value || window.studaiReports?.state?.courseId || "eng-comp-sao-carlos";
}

function saveExamSession(quiz) {
  if (!quiz?.session) return;
  try { sessionStorage.setItem(`${EXAM_SESSION_PREFIX}${quiz.examId}`, JSON.stringify(quiz.session)); } catch { /* armazenamento indisponível */ }
}

async function startExamSession(examId, answers = {}, { fresh = false } = {}) {
  const key = `${EXAM_SESSION_PREFIX}${examId}`;
  let stored = null;
  try { stored = JSON.parse(sessionStorage.getItem(key) || "null"); } catch { stored = null; }
  // Retoma a sessão desta aba quando ainda há respostas locais em andamento.
  if (!fresh && stored?.id && !stored.finished && !stored.offline && Object.keys(answers).length) return stored;
  try {
    const created = await api(`/api/exams/${encodeURIComponent(examId)}/sessions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ courseId: examSessionCourseId() }),
    });
    const ordinals = Object.fromEntries((created.questions || []).map(item => [String(item.number), item.ordinal]));
    const courseId = examSessionCourseId();
    const session = { id: created.id, courseId, courseName: courses.find(course => course.id === courseId)?.name || courseId, ordinals, sent: {}, finished: false, offline: false };
    sessionStorage.setItem(key, JSON.stringify(session));
    return session;
  } catch (error) {
    sessionStorage.removeItem(key);
    toast(`Não foi possível abrir a sessão no histórico (${error.message}): respostas não serão salvas no histórico.`, true);
    return null;
  }
}

function examSessionFailed(quiz, error) {
  if (!quiz.session || quiz.session.offline) return;
  quiz.session.offline = true;
  saveExamSession(quiz);
  toast(`Falha ao salvar a resposta (${error.message}): respostas não serão salvas no histórico.`, true);
}

async function persistExamAnswer(quiz, number, answer, reason = null) {
  const session = quiz?.session;
  if (!session || session.offline || session.finished) return;
  const ordinal = session.ordinals[String(number)];
  if (ordinal == null || session.sent[String(number)]) return; // questão fora do pool elegível: fica só local
  session.sent[String(number)] = true;
  saveExamSession(quiz);
  try {
    await api(`/api/exam-sessions/${encodeURIComponent(session.id)}/answers`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reason ? { ordinal, answer, reason } : { ordinal, answer }),
    });
  } catch (error) {
    if (/já respondida/i.test(error.message)) return;
    delete session.sent[String(number)];
    examSessionFailed(quiz, error);
  }
}

function flushExamReason(quiz) {
  const pending = quiz?.pendingReason;
  if (!pending) return Promise.resolve();
  quiz.pendingReason = null;
  return persistExamAnswer(quiz, pending.number, pending.answer);
}

async function finishExamSession(quiz) {
  const session = quiz?.session;
  if (!session || session.offline || session.finished || quiz.finishing) return;
  quiz.finishing = true;
  try {
    await flushExamReason(quiz);
    if (!Object.keys(session.sent).length || session.offline) return;
    await api(`/api/exam-sessions/${encodeURIComponent(session.id)}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    session.finished = true;
    saveExamSession(quiz);
    toast("Prova salva no histórico. Veja sua evolução em Relatórios.");
    if (currentExamQuiz === quiz) renderExamQuiz();
  } catch (error) {
    examSessionFailed(quiz, error);
  } finally {
    quiz.finishing = false;
  }
}

function examSessionStatus(quiz) {
  const session = quiz.session;
  if (!session || session.offline) return `<span class="exam-session-status offline" title="As respostas ficam apenas nesta aba do navegador.">Somente neste navegador</span>`;
  const title = session.courseName ? ` title="Curso do histórico: ${escapeHtml(session.courseName)}"` : "";
  if (session.finished) return `<span class="exam-session-status saved"${title}>Salva no histórico</span>`;
  return `<span class="exam-session-status online"${title}>Salvando no histórico</span>`;
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
    currentExamQuiz = { ...data, examId, questions: data.questions.filter(question => !["unusable", "bad_crop"].includes(question.review?.status)), index: 0, selected: null, answers, storageKey, session: null, pendingReason: null };
    currentExamQuiz.session = await startExamSession(examId, answers);
    renderExamQuiz();
    workspace.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    workspace.innerHTML = `<div class="exam-quiz-error">Não foi possível abrir a prova: ${escapeHtml(error.message)}<button class="blank-button" data-exam-close>Voltar à revisão</button></div>`;
    bindExamQuizActions();
  }
}

let focusWorkspace = null;

function enhanceFocusMode(workspace, progress) {
  const header = $(".workspace-top", workspace);
  if (!header || !$(".question-body, .exam-prompt", workspace)) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button button-secondary focus-open";
  button.textContent = "Modo foco";
  button.addEventListener("click", async () => {
    focusWorkspace = workspace;
    workspace.classList.add("focus-mode");
    workspace.dataset.focusProgress = `Questão ${progress}`;
    if (workspace.requestFullscreen) {
      try { await workspace.requestFullscreen(); } catch { /* Sobreposição CSS é o fallback. */ }
    }
    $("[data-focus-exit]", workspace)?.focus();
  });
  header.append(button);
  const indicator = document.createElement("span");
  indicator.className = "focus-progress";
  indicator.textContent = `Questão ${progress}`;
  workspace.append(indicator);
  if (workspace.classList.contains("focus-mode")) {
    const question = $(".question-body, .exam-prompt, .exam-options, .options-list", workspace);
    question?.classList.add("focus-slide");
  }
  const exit = document.createElement("button");
  exit.type = "button";
  exit.className = "focus-exit";
  exit.dataset.focusExit = "";
  exit.setAttribute("aria-label", "Sair do modo foco (Esc)");
  exit.textContent = "×";
  exit.addEventListener("click", closeFocusMode);
  workspace.append(exit);
  if (!workspace.dataset.focusGestures) {
    workspace.dataset.focusGestures = "true";
    workspace.addEventListener("touchstart", event => { workspace.dataset.swipeX = event.changedTouches[0].clientX; }, { passive: true });
    workspace.addEventListener("touchend", event => {
      const start = Number(workspace.dataset.swipeX);
      const delta = event.changedTouches[0].clientX - start;
      if (!workspace.classList.contains("focus-mode") || Math.abs(delta) < 70) return;
      const next = $("[data-exam-next], #today-next", workspace);
      const prev = $("[data-exam-prev]", workspace);
      if (delta < 0) next?.click(); else prev?.click();
    }, { passive: true });
  }
}

function closeFocusMode() {
  const workspace = focusWorkspace;
  if (!workspace) return;
  workspace.classList.remove("focus-mode");
  focusWorkspace = null;
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
}

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && focusWorkspace) {
    focusWorkspace.classList.remove("focus-mode");
    focusWorkspace = null;
  }
});

document.addEventListener("keydown", event => {
  const workspace = focusWorkspace;
  if (!workspace?.classList.contains("focus-mode")) return;
  if (event.key === "Escape") { closeFocusMode(); return; }
  if (event.key === "ArrowRight") { $("[data-exam-next], #today-next", workspace)?.click(); return; }
  if (event.key === "ArrowLeft") { $("[data-exam-prev]", workspace)?.click(); return; }
  if (/^[a-e]$/i.test(event.key)) {
    const letter = event.key.toUpperCase();
    const option = $$('[data-exam-option], [data-today-option], [data-answer]', workspace).find(button => (button.dataset.examOption || button.dataset.todayOption || button.dataset.answer) === letter);
    option?.click();
  }
  if (event.key === "Enter") {
    const action = $("[data-exam-submit], #today-submit, #diagnostic-submit, [data-exam-next], #today-next, #confirm-reason", workspace);
    action?.click();
  }
});

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
    if (quiz.session && !quiz.session.finished && !quiz.session.offline) finishExamSession(quiz);
    const savedNote = quiz.session?.finished ? `<p>Respostas salvas no histórico. <button class="text-button" data-go-reports>Ver relatórios →</button></p>` : (!quiz.session || quiz.session.offline ? "<p>As respostas desta sessão não foram salvas no histórico.</p>" : "<p>Salvando respostas no histórico…</p>");
    workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">PROVA CONCLUÍDA</p><h2>${escapeHtml(exam.name)}</h2><div class="workspace-meta">${questions.length} questões respondidas nesta sessão · ${examSessionStatus(quiz)}</div></div><button class="blank-button" data-exam-close>Fechar</button></div><div class="exam-final-summary"><strong>${scored.graded ? `${scored.correct} de ${scored.graded} acertos` : "Respostas registradas"}</strong><p>${scored.annulled ? `${scored.annulled} questão(ões) anulada(s). ` : ""}${scored.graded < questions.length - scored.annulled ? "Algumas questões ainda não têm gabarito importado." : ""}</p>${savedNote}</div><button class="button button-primary" data-exam-restart>Responder novamente</button>`;
    bindExamQuizActions();
    return;
  }
  const number = String(question.numero);
  const saved = quiz.answers[number] || null;
  const submitted = Object.hasOwn(quiz.answers, number);
  const awaitingReason = quiz.pendingReason?.number === number;
  const key = answerKeys[number];
  const selected = submitted || awaitingReason ? (awaitingReason ? quiz.pendingReason.answer : saved) : quiz.selected;
  const shared = question.texto_id != null ? sharedTexts[String(question.texto_id)] : null;
  const sharedText = typeof shared === "string" ? shared : shared?.texto;
  const sharedImages = shared && typeof shared === "object" ? shared.figuras : [];
  const outcome = submitted && key ? (key.annulled || key.answer === "*"
    ? `<div class="exam-answer-feedback annulled">Questão anulada no gabarito.</div>`
    : saved == null ? `<div class="exam-answer-feedback neutral">Resposta em branco.</div>`
      : `<div class="exam-answer-feedback ${saved === key.answer ? "correct" : "incorrect"}">${saved === key.answer ? "Resposta correta!" : `Resposta incorreta. Gabarito: ${escapeHtml(key.answer)}.`}</div>`) : (submitted ? `<div class="exam-answer-feedback neutral">Resposta registrada. Ainda não há gabarito importado para esta questão.</div>` : "");
  const reasonPrompt = awaitingReason
    ? `<div class="exam-reason-prompt" role="group" aria-label="Motivo do erro"><span>Qual foi o principal motivo do erro?</span><div class="exam-reason-options">${EXAM_REASONS.map(([value, label]) => `<button type="button" class="button button-secondary" data-exam-reason="${value}">${label}</button>`).join("")}</div></div>` : "";
  const progress = Math.round(((index + 1) / questions.length) * 100);
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">${escapeHtml(exam.name)}</p><h2>Responda à prova</h2><div class="workspace-meta">Questão ${index + 1} de ${questions.length}${question.pagina ? ` · página ${question.pagina}` : ""} · ${examSessionStatus(quiz)}</div></div><button class="blank-button" data-exam-close>Fechar prova</button></div>
    <div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>
    ${questionContentMarkup(exam.id, question, sharedText, sharedImages)}
    <div class="exam-options">${optionsMarkup(exam.id, question, { selected, submitted: submitted || awaitingReason, correctAnswer: key && !key.annulled ? key.answer : null, saved, attr: "data-exam-option" })}</div>
    ${outcome}${reasonPrompt}<div class="exam-quiz-actions"><button class="blank-button" data-exam-prev ${index === 0 || awaitingReason ? "disabled" : ""}>← Anterior</button><div>${awaitingReason ? `<span class="muted">Escolha o motivo para continuar.</span>` : submitted ? `<button class="button button-primary" data-exam-next>${index === questions.length - 1 ? "Ver resultado" : "Próxima questão"} →</button>` : `<button type="button" class="blank-button ${selected === "" ? "selected" : ""}" data-exam-blank aria-pressed="${selected === ""}">Deixar em branco</button><button class="button button-primary" data-exam-submit ${selected == null ? "disabled" : ""}>Responder</button>`}</div></div>`;
  enhanceFocusMode(workspace, `${index + 1} de ${questions.length}`);
  bindExamQuizActions();
}

function bindExamQuizActions() {
  const workspace = $("#exam-quiz-workspace");
  $$('[data-exam-option]', workspace).forEach(button => button.addEventListener("click", () => {
    if (!currentExamQuiz) return;
    currentExamQuiz.selected = button.dataset.examOption;
    renderExamQuiz();
  }));
  $(`[data-exam-blank]`, workspace)?.addEventListener("click", () => {
    if (!currentExamQuiz || currentExamQuiz.pendingReason) return;
    currentExamQuiz.selected = currentExamQuiz.selected === "" ? null : "";
    renderExamQuiz();
  });
  $("[data-exam-submit]", workspace)?.addEventListener("click", () => {
    const quiz = currentExamQuiz;
    if (quiz?.selected == null) return;
    flushExamReason(quiz);
    const number = String(quiz.questions[quiz.index].numero);
    const answer = quiz.selected || null;
    const key = (quiz.answerKeys || {})[number];
    const wrong = answer != null && key && !key.annulled && key.answer !== "*" && key.answer !== answer;
    if (wrong) {
      quiz.pendingReason = { number, answer };
      renderExamQuiz();
      return;
    }
    commitExamAnswer(quiz, number, answer);
    renderExamQuiz();
  });
  $$("[data-exam-reason]", workspace).forEach(button => button.addEventListener("click", () => {
    const quiz = currentExamQuiz;
    const pending = quiz?.pendingReason;
    if (!pending) return;
    const reason = button.dataset.examReason;
    if (!reason) return;
    quiz.pendingReason = null;
    commitExamAnswer(quiz, pending.number, pending.answer, reason);
    toast("Motivo registrado.");
    renderExamQuiz();
  }));
  $("[data-exam-next]", workspace)?.addEventListener("click", () => { flushExamReason(currentExamQuiz); currentExamQuiz.index++; currentExamQuiz.selected = null; renderExamQuiz(); });
  $("[data-exam-prev]", workspace)?.addEventListener("click", () => { currentExamQuiz.index--; currentExamQuiz.selected = null; renderExamQuiz(); });
  $("[data-exam-close]", workspace)?.addEventListener("click", () => {
    workspace.classList.add("hidden");
    $("#exam-question-workspace").classList.remove("hidden");
  });
  $("[data-go-reports]", workspace)?.addEventListener("click", () => showView("reports"));
  $("[data-exam-restart]", workspace)?.addEventListener("click", async () => {
    const quiz = currentExamQuiz;
    sessionStorage.removeItem(quiz.storageKey);
    quiz.answers = {}; quiz.index = 0; quiz.selected = null; quiz.pendingReason = null;
    quiz.session = await startExamSession(quiz.examId, {}, { fresh: true });
    renderExamQuiz();
  });
}

async function commitExamAnswer(quiz, number, answer, reason = null) {
  const answerIndex = quiz.index;
  quiz.answers[number] = answer;
  sessionStorage.setItem(quiz.storageKey, JSON.stringify(quiz.answers));
  quiz.selected = null;
  await persistExamAnswer(quiz, number, answer, reason);
  if (currentExamQuiz !== quiz) return;
  window.setTimeout(() => {
    if (currentExamQuiz !== quiz || quiz.index !== answerIndex || quiz.pendingReason || quiz.answers[number] !== answer) return;
    quiz.index++;
    quiz.selected = null;
    renderExamQuiz();
  }, 800);
}

async function refreshCourses() {
  try {
    courses = await api("/api/courses");
    $("#course-select").innerHTML = courses.map(course => `<option value="${escapeHtml(course.id)}">${escapeHtml(course.name)} · ${course.subjects.length} matérias</option>`).join("");
    $("#training-course").innerHTML = courses.map(course => `<option value="${escapeHtml(course.id)}">${escapeHtml(course.name)}</option>`).join("");
    try { const saved = localStorage.getItem("studai-today-course"); if (saved && courses.some(course => course.id === saved)) $("#training-course").value = saved; } catch {}
    renderTrainingSubjects();
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
  const useOllama = $("#process-use-ollama");
  const localSelect = $("#process-ollama-models");
  const threadsInput = $("#process-ollama-threads");
  let loadedOptions = null;
  const updateStatus = () => {
    if (!loadedOptions) return;
    const cloudReady = (loadedOptions.freeCloud || []).filter(lane => lane.configured).length;
    status.textContent = `${useOllama.checked ? (loadedOptions.ollamaAvailable ? `${loadedOptions.ollamaModels.length} modelo(s) Ollama ativo(s)` : "Ollama não encontrado") : "Ollama desativado"} · ${loadedOptions.geminiConfigured ? `${loadedOptions.geminiKeyCount} chave(s) Gemini configurada(s)` : "Gemini sem chaves configuradas"} · ${cloudReady ? `${cloudReady} faixa(s) grátis de nuvem` : "nenhuma faixa grátis de nuvem"}`;
  };
  const updateOllamaControls = () => {
    localSelect.disabled = !useOllama.checked || !localSelect.options.length || !localSelect.options[0].value;
    threadsInput.disabled = !useOllama.checked;
    updateStatus();
  };
  useOllama.addEventListener("change", updateOllamaControls);
  try {
    const options = await api("/api/ai/options");
    loadedOptions = options;
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
    updateOllamaControls();
    updateStatus();
    geminiStatus.textContent = options.geminiConfigured
      ? `${options.geminiKeyCount} chave(s) no backend · nenhum segredo é exibido aqui.`
      : "Adicione GEMINI_API_KEYS ao .env local para ativar o Gemini.";
    if (options.geminiConfigured && !options.geminiModels.length) {
      geminiStatus.textContent = "Não foi possível listar modelos Gemini disponíveis para as chaves configuradas.";
    }
    renderCloudLanes(options.freeCloud || []);
  } catch (error) {
    status.textContent = `Não foi possível carregar os modelos: ${error.message}`;
    geminiStatus.textContent = "Verifique se a API local está ativa.";
    $("#process-cloud-lanes").innerHTML = `<p class="field-help">Verifique se a API local está ativa.</p>`;
  }
}

function renderCloudLanes(lanes) {
  const list = $("#process-cloud-lanes");
  const checked = new Set($$("#process-cloud-lanes input:checked").map(input => input.value));
  const firstRender = !list.querySelector("input");
  list.innerHTML = lanes.length ? lanes.map(lane => {
    const models = lane.models.map(model => `<code>${escapeHtml(model)}</code>`).join(" → ");
    const detail = lane.configured
      ? `${lane.defaultModels ? "Padrão" : "Do .env"}: ${models}`
      : (lane.models.length ? `Adicione <code>${escapeHtml(lane.keyEnv)}</code> ao .env para ativar.`
                            : `Adicione <code>${escapeHtml(lane.keyEnv)}</code> e <code>${escapeHtml(lane.modelsEnv)}</code> ao .env para ativar.`);
    const on = lane.configured && (firstRender || checked.has(lane.id));
    return `<label class="cloud-lane${lane.configured ? "" : " unavailable"}"><input type="checkbox" value="${escapeHtml(lane.id)}"${on ? " checked" : ""}${lane.configured ? "" : " disabled"}><span class="cloud-lane-main"><strong>${escapeHtml(lane.label)}</strong><span class="cloud-lane-models">${detail}</span></span></label>`;
  }).join("") : `<p class="field-help">Nenhuma faixa grátis disponível.</p>`;
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
  const useOllama = $("#process-use-ollama").checked;
  const ollamaModels = useOllama
    ? [...$("#process-ollama-models").selectedOptions].map(option => option.value).filter(Boolean)
    : [];
  if (ollamaModels.length > 2) throw new Error("Selecione no máximo dois modelos Ollama.");
  return {
    useOllama,
    ollamaModels,
    geminiModels: [...$("#process-gemini-models").selectedOptions].map(option => option.value).filter(Boolean),
    cloudProviders: $$("#process-cloud-lanes input:checked").map(input => input.value),
    ollamaThreads: $("#process-ollama-threads").value || "0",
  };
}

function toggleProviderFields(prefix) {
  const extra = $(`#${prefix}-provider-extra`);
  const provider = $(`#${prefix}-provider`).value;
  const hasModel = !["demo", "mock"].includes(provider);
  extra.classList.toggle("hidden", !hasModel);
  const baseUrl = $(`#${prefix}-base-url-field`);
  if (baseUrl) baseUrl.classList.toggle("hidden", provider !== "ollama");
  const model = $(`#${prefix}-model`);
  if (model) model.placeholder = provider === "ollama" ? "qwen3.5:4b" : provider === "gemini" ? "gemini-3.8-flash (opcional)" : "Modelo opcional; usa o padrão se vazio";
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
    if (!config.ollamaModels.length && !config.geminiModels.length && !config.cloudProviders.length) throw new Error("Selecione ao menos um modelo Ollama, Gemini ou uma faixa grátis de nuvem.");
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
    useOllama: String(record.config.useOllama !== false),
    ollamaModels: (record.config.ollamaModels || []).join(","), geminiModels: (record.config.geminiModels || []).join(","),
    ollamaThreads: record.config.ollamaThreads || "0" });
  // Envios salvos antes desta opção não têm cloudProviders: o backend usa todas as faixas.
  if (Array.isArray(record.config.cloudProviders)) {
    query.set("useFreeCloud", String(record.config.cloudProviders.length > 0));
    query.set("cloudProviders", record.config.cloudProviders.join(","));
  }
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
  return `<div class="history-item"><span class="history-mark">◉</span><button class="history-open" type="button" data-diagnostic="${escapeHtml(item.id)}"><span class="history-main"><strong>${escapeHtml(item.course_name)}</strong><span>${state} · ${item.actual_count} questões · ${new Date(item.created_at).toLocaleDateString("pt-BR")}</span></span><span class="history-arrow">›</span></button><button class="history-delete" type="button" data-delete-diagnostic="${escapeHtml(item.id)}" aria-label="Remover diagnóstico" title="Remover diagnóstico">×</button></div>`;
}

async function refreshDiagnostics() {
  try {
    const items = await api("/api/diagnostics");
    $("#diagnostic-list").innerHTML = items.length ? items.map(diagnosticCard).join("") : `<div class="empty-state"><div class="empty-icon">◉</div><strong>Nenhum diagnóstico ainda</strong><p>Gere um novo diagnóstico quando houver questões classificadas com gabarito.</p></div>`;
    $$("[data-diagnostic]").forEach(button => button.addEventListener("click", () => openDiagnostic(button.dataset.diagnostic)));
    $$('[data-delete-diagnostic]').forEach(button => button.addEventListener("click", async event => {
      event.stopPropagation();
      const id = button.dataset.deleteDiagnostic;
      if (!window.confirm("Remover este diagnóstico e todas as respostas e resultados associados? Esta ação não pode ser desfeita.")) return;
      button.disabled = true;
      try {
        await api(`/api/diagnostics/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (currentDiagnostic?.id === id) {
          currentDiagnostic = null;
          $("#diagnostic-workspace").classList.add("hidden");
        }
        await refreshDiagnostics();
        toast("Diagnóstico removido.");
      } catch (error) {
        button.disabled = false;
        toast(error.message, true);
      }
    }));
  } catch (error) { toast(error.message, true); }
}

async function createDiagnostic(event) {
  event.preventDefault();
  const status = $("#diagnostic-create-status");
  status.classList.remove("hidden", "error");
  status.textContent = "Preparando diagnóstico…";
  const config = providerConfig("diagnostic");
  activeProvider = config;
  const mode = $("#diagnostic-mode")?.value || "adaptive";
  const body = { ...config, mode, courseId: $("#course-select").value, total: Number($("#diagnostic-total").value) };
  if ($("#diagnostic-seed").value) body.seed = Number($("#diagnostic-seed").value);
  try {
    const { jobId } = await api("/api/diagnostics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const result = await pollJob(jobId, job => { status.textContent = job.message; });
    sessionStorage.setItem(`studai-provider-${result.id}`, JSON.stringify(config));
    sessionStorage.setItem(`studai-diagnostic-mode-${result.id}`, result.mode || mode);
    status.textContent = result.mode === "adaptive" || mode === "adaptive"
      ? `Diagnóstico adaptativo criado (até ${result.requested ?? body.total} questões). Seed: ${result.seed}.`
      : `Diagnóstico criado com ${result.selected} questões. Seed: ${result.seed}.`;
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
  const contentRows = Object.values(metrics.contents || {}).sort((a, b) => a.path.localeCompare(b.path, "pt-BR")).map(row => `<tr><td>${escapeHtml(row.path)}</td><td>${row.attempts}</td><td>${row.correct}</td><td>${row.accuracy == null ? "—" : `${Math.round(row.accuracy * 100)}%`}</td><td>${row.status === "assessed" ? "Avaliado" : "Poucos dados"}</td></tr>`).join("");
  const contentTable = contentRows ? `<h3>Desempenho por conteúdo</h3><p class="muted">As áreas da árvore somam as questões relacionadas aos conteúdos descendentes.</p><table class="subject-table"><thead><tr><th>Matéria e caminho do conteúdo</th><th>Tentativas</th><th>Acertos</th><th>Desempenho</th><th>Situação</th></tr></thead><tbody>${contentRows}</tbody></table>` : "";
  const report = metrics.report && window.renderStudyReport ? window.renderStudyReport(metrics.report, { title: "Pontos fortes, lacunas e prioridades" }) : "";
  return `${report}<div class="result-grid"><div class="metric-card"><span>Acertos</span><strong>${metrics.correct}/${metrics.answered}</strong></div><div class="metric-card"><span>Desempenho geral</span><strong>${accuracy}</strong></div><div class="metric-card"><span>Questões</span><strong>${metrics.total_questions}</strong></div><div class="metric-card"><span>Em branco/incorretas</span><strong>${metrics.incorrect_or_blank}</strong></div></div><table class="subject-table"><thead><tr><th>Matéria</th><th>Tentativas</th><th>Acertos</th><th>Desempenho</th><th>Situação</th></tr></thead><tbody>${rows}</tbody></table>${contentTable}`;
}

function renderAnalysis(analysis, error) {
  if (!analysis) return error ? `<div class="analysis-card"><h3>Análise não disponível</h3><p>${escapeHtml(error)}</p></div>` : "";
  const steps = (analysis.steps || []).map((step, index) => `<div class="analysis-step"><strong>${index + 1}. ${escapeHtml(step.subject)} · ${escapeHtml(step.action)}</strong><p>${escapeHtml(step.rationale)}</p><p><b>Critério:</b> ${escapeHtml(step.exit_criterion)}</p></div>`).join("");
  return `<div class="analysis-card"><h3>Plano inicial de estudos</h3><p>${escapeHtml(analysis.summary)}</p>${steps}</div>`;
}

function diagnosticMode(data) {
  return data.mode || sessionStorage.getItem(`studai-diagnostic-mode-${data.id}`) || "static";
}

function renderDiagnostic(workspace, data) {
  currentDiagnostic = data;
  workspace.classList.remove("hidden");
  const adaptive = diagnosticMode(data) === "adaptive";
  const answered = data.questions.filter(question => question.answered).length;
  const pending = data.questions.find(question => !question.answered);
  // Adaptativo: o backend acrescenta a próxima questão a cada resposta e sinaliza `done`.
  const finished = adaptive ? (data.done === true || diagnosticDone.has(data.id) || !pending) : !pending;
  const progress = adaptive
    ? (data.metrics || finished ? 100 : Math.min(95, Math.round(answered / Math.max(1, data.requested || data.total || 1) * 100)))
    : (data.total ? Math.round(answered / data.total * 100) : 0);
  let body = "";
  if (data.metrics) {
    body = `${renderMetrics(data.metrics)}${renderAnalysis(data.analysis, data.analysisError)}`;
  } else if (pending && !(adaptive && finished)) {
    const item = pending;
    const question = item.question || {};
    const position = adaptive ? `Questão ${answered + 1} · adaptativo` : `Questão ${item.ordinal} de ${data.total}`;
    const display = questionDisplayState(question);
    const sharedImages = question._figuras_compartilhadas || [];
    // Keep the original crop available even when extracted inline figures exist;
    // a stale or missing figure path should not make the diagnostic unsolvable.
    const fullPageImages = !display.showSource ? (question.imagens_completas || []) : [];
    const alternatives = Object.entries(question.alternativas || {}).sort(([a], [b]) => a.localeCompare(b)).map(([letter, raw]) => {
      const alternative = typeof raw === "string" ? { texto: raw, figuras: [] } : (raw || {});
      const label = display.showAlternativeText ? (alternative.texto || (alternative.figuras?.length ? "" : "Alternativa apresentada como figura")) : (alternative.figuras?.length ? "" : "Alternativa apresentada na imagem da questão");
      const selected = diagnosticSelection?.id === data.id && diagnosticSelection.ordinal === item.ordinal && diagnosticSelection.answer === letter;
      return `<button class="option-button ${selected ? "selected" : ""}" data-answer="${escapeHtml(letter)}" ${diagnosticReasonPrompt?.id === data.id && diagnosticReasonPrompt.ordinal === item.ordinal ? "disabled" : ""}><span class="option-letter">${escapeHtml(letter)}</span><span>${escapeHtml(label)}${examImageMarkup(item.source_id, alternative.figuras || [], "option-image")}</span></button>`;
    }).join("");
    const reasonPending = diagnosticReasonPrompt?.id === data.id && diagnosticReasonPrompt.ordinal === item.ordinal;
    const selectedAnswer = diagnosticSelection?.id === data.id && diagnosticSelection.ordinal === item.ordinal ? diagnosticSelection.answer : undefined;
    body = `<div class="question-top"><span class="question-subject">${escapeHtml(item.subject)} · ${escapeHtml(item.topic)}</span><span>${position}</span></div>${question._texto_compartilhado ? `<div class="question-body"><b>Texto-base</b><br>${escapeHtml(question._texto_compartilhado)}${examImageMarkup(item.source_id, sharedImages, "passage-image")}</div>` : ""}<div class="question-body">${display.showStem ? stemMarkup(question, paths => examImageMarkup(item.source_id, paths, "formula-image")) : (!display.showSource ? "Enunciado não extraído" : "")}${question.nota ? `<div class="question-note">${escapeHtml(question.nota)}</div>` : ""}${examImageMarkup(item.source_id, question.figuras_enunciado || [])}${renderSourceQuestionImage(item.source_id, display.sourceImages, { collapsible: true })}${fullPageImages.length ? `<details class="exam-full-page"><summary>Ver imagem original da questão</summary>${examImageMarkup(item.source_id, fullPageImages)}</details>` : ""}</div><div class="options-list">${alternatives}</div><div class="question-actions"><button class="blank-button ${selectedAnswer === null ? "selected" : ""}" id="answer-blank" aria-pressed="${selectedAnswer === null}" ${reasonPending ? "disabled" : ""}>Deixar em branco</button><span class="muted">Escolha uma alternativa e clique em Responder.</span></div><div class="exam-quiz-actions"><span></span><button class="button button-primary" id="diagnostic-submit" ${selectedAnswer === undefined || reasonPending ? "disabled" : ""}>Responder</button></div><div id="reason-panel" class="reason-row ${reasonPending ? "" : "hidden"}"><label>Qual foi o principal motivo do erro?<select id="answer-reason"><option value="">Selecione</option><option value="unknown_content">Não sabia o conteúdo</option><option value="calc_error">Errei o cálculo</option><option value="misread">Interpretei errado</option></select></label><button class="button button-primary" id="confirm-reason">Registrar resposta</button></div><div id="answer-feedback"></div>`;
  } else {
    const stop = adaptive ? `O diagnóstico adaptativo terminou após ${answered} questões${data.stopReason || diagnosticDone.get(data.id) ? `: ${escapeHtml(data.stopReason || diagnosticDone.get(data.id))}` : ""}. ` : "Todas as respostas foram registradas. ";
    body = `<div class="inline-result">${stop}Gere a análise para ver suas métricas e prioridades.</div><button class="button button-primary" id="finalize-diagnostic">Concluir e analisar <span>→</span></button>`;
  }
  const meta = adaptive ? `${answered} respondida(s) · adaptativo${data.requested ? ` · até ${data.requested}` : ""}` : `${answered} de ${data.total} respondidas`;
  workspace.innerHTML = `<div class="workspace-top"><div><p class="eyebrow">TENTATIVA ${escapeHtml(data.id)}</p><h2>${escapeHtml(data.course)}</h2><div class="workspace-meta">${meta} · ${escapeHtml(data.status)}</div></div><button class="icon-button" id="close-diagnostic" title="Fechar">×</button></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>${body}`;
  if (pending && !data.metrics && !(adaptive && finished)) enhanceFocusMode(workspace, `${answered + 1} de ${adaptive ? (data.requested || data.total || "?") : data.total}`);
  $("#close-diagnostic", workspace).addEventListener("click", () => { workspace.classList.add("hidden"); currentDiagnostic = null; });
  if (pending && !data.metrics && !(adaptive && finished)) {
    $$("[data-answer]", workspace).forEach(button => button.addEventListener("click", () => {
      diagnosticSelection = { id: data.id, ordinal: pending.ordinal, answer: button.dataset.answer };
      renderDiagnostic(workspace, data);
    }));
    $("#answer-blank", workspace).addEventListener("click", () => {
      diagnosticSelection = { id: data.id, ordinal: pending.ordinal, answer: null };
      renderDiagnostic(workspace, data);
    });
    $("#diagnostic-submit", workspace).addEventListener("click", () => {
      const answer = diagnosticSelection?.id === data.id && diagnosticSelection.ordinal === pending.ordinal
        ? diagnosticSelection.answer : undefined;
      if (answer === undefined || diagnosticReasonPrompt?.id === data.id) return;
      submitAnswer(data.id, pending, answer);
    });
    $("#confirm-reason", workspace).addEventListener("click", () => {
      const reason = $("#answer-reason", workspace).value;
      if (!reason) return toast("Escolha o motivo do erro.", true);
      submitAnswer(data.id, pending, diagnosticReasonPrompt.answer, reason);
    });
  }
  $("#finalize-diagnostic", workspace)?.addEventListener("click", () => finalizeDiagnostic(data.id));
}

function stopReasonLabel(reason) {
  return ({ budget: "orçamento de questões atingido", budget_exhausted: "orçamento de questões atingido", converged: "domínio estimado com confiança suficiente", confident: "domínio estimado com confiança suficiente", pool_exhausted: "não há mais questões elegíveis", exhausted: "não há mais questões elegíveis" })[reason] || String(reason).replace(/_/g, " ");
}

async function openDiagnostic(id) {
  try {
    const data = await api(`/api/diagnostics/${encodeURIComponent(id)}`);
    activeProvider = JSON.parse(sessionStorage.getItem(`studai-provider-${id}`) || '{"provider":"demo"}');
    renderDiagnostic($("#diagnostic-workspace"), data);
  } catch (error) { toast(error.message, true); }
}

async function submitAnswer(id, item, answer, reason = null) {
  const workspace = $("#diagnostic-workspace");
  if (workspace.dataset.answerSubmitting === "true") return;
  workspace.dataset.answerSubmitting = "true";
  const feedback = $("#answer-feedback");
  try {
    const result = await api(`/api/diagnostics/${encodeURIComponent(id)}/answers`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ordinal: item.ordinal, answer, reason }),
    });
    if (result.requiresReason) {
      workspace.dataset.answerSubmitting = "false";
      diagnosticReasonPrompt = { id, ordinal: item.ordinal, answer };
      renderDiagnostic($("#diagnostic-workspace"), currentDiagnostic);
      return;
    }
    workspace.dataset.answerSubmitting = "false";
    diagnosticReasonPrompt = null;
    diagnosticSelection = null;
    // Adaptativo: {next_ordinal, done, stop_reason?}; relemos a tentativa para mostrar a próxima questão.
    if (result.done === true) diagnosticDone.set(id, result.stop_reason ? stopReasonLabel(result.stop_reason) : "");
    feedback.innerHTML = `<div class="inline-result">${result.answer == null ? "Resposta em branco registrada." : result.correct ? "Resposta correta registrada." : "Resposta registrada."}</div>`;
    setTimeout(() => openDiagnostic(id), 350);
  } catch (error) {
    workspace.dataset.answerSubmitting = "false";
    toast(error.message, true);
  }
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

const STUDY_REASONS = {
  overdue_question: "QUESTÃO ERRADA", due_content: "REVISÃO VENCIDA",
  new_gap: "LACUNA DO DIAGNÓSTICO", new_content: "CONTEÚDO NOVO", practice: "PRÁTICA",
};

function formatDay(isoDate) {
  return new Date(`${isoDate}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
}

function updateTrainingCounters(session) {
  $("#training-count").textContent = `${session.answered} questão${session.answered === 1 ? "" : "ões"} respondida${session.answered === 1 ? "" : "s"} hoje`;
  const extra = session.items.filter(item => item.id.startsWith("t"));
  const done = extra.filter(item => item.answered).length;
  $("#training-block-status").textContent = extra.length
    ? `${extra.length} questões extras enfileiradas · ${done} concluídas`
    : "Nenhum bloco extra adicionado ainda.";
}

async function refreshToday() {
  const courseId = $("#training-course").value;
  if (!courseId) return;
  try {
    const [session, progress] = await Promise.all([
      api(`/api/study/today?courseId=${encodeURIComponent(courseId)}`),
      api("/api/study/progress"),
    ]);
    currentStudy = { session, selected: null, pendingReason: null, training: true,
      dailyCount: session.items.filter(item => !item.id.startsWith("t")).length };
    $("#today-date").textContent = formatDay(session.date);
    updateTrainingCounters(session);
    const dailyItems = session.items.filter(item => !item.id.startsWith("t"));
    const dailyDone = dailyItems.filter(item => item.answered).length;
    $("#daily-task-status").textContent = dailyItems.length
      ? dailyDone >= dailyItems.length ? `Tarefa diária concluída · ${dailyDone} de ${dailyItems.length} questões`
        : `${dailyDone} de ${dailyItems.length} questões concluídas`
      : "Tarefa diária concluída · nenhuma questão pendente";
    renderTodaySummary(session, progress);
    renderTodayProgress(progress);
    renderToday();
  } catch (error) {
    $("#today-workspace").innerHTML = `<div class="inline-result error">Não foi possível montar o estudo de hoje: ${escapeHtml(error.message)}</div>`;
    toast(error.message, true);
  }
}

function renderTrainingSubjects() {
  const course = courses.find(item => item.id === $("#training-course").value);
  const subjects = course?.subjects || [];
  $("#training-subject-list").innerHTML = subjects.length
    ? subjects.map((subject, index) => `<label class="training-subject"><input type="checkbox" name="training-subject" value="${escapeHtml(subject)}" checked><span>${escapeHtml(subject)}</span></label>`).join("")
    : `<p class="help-copy">Este curso não tem matérias disponíveis.</p>`;
}

function renderTodaySummary(session, progress) {
  const c = session.counts;
  const forecast = Object.entries(progress.forecast || {}).slice(1).map(([day, count]) => `<div class="forecast-day">${escapeHtml(formatDay(day))}<strong>${count}</strong></div>`).join("");
  $("#today-summary").innerHTML = `<div class="result-grid"><div class="metric-card"><span>Questões erradas para refazer</span><strong>${c.overdue_question}</strong></div><div class="metric-card"><span>Revisões vencidas</span><strong>${c.due_content}</strong></div><div class="metric-card"><span>Lacunas do diagnóstico</span><strong>${c.new_gap}</strong></div><div class="metric-card"><span>Conteúdos novos</span><strong>${c.new_content}</strong></div></div>${forecast ? `<p class="eyebrow" style="margin-top:14px">REVISÕES NOS PRÓXIMOS DIAS</p><div class="forecast-row">${forecast}</div>` : ""}`;
}

function renderTodayProgress(progress) {
  const rows = (progress.contents || []).map(row => `<tr><td>${escapeHtml(row.path)}</td><td>${row.reviews}</td><td>${row.accuracy == null ? "—" : `${Math.round(row.accuracy * 100)}%`}</td><td>${Math.round(row.retrievability * 100)}%</td><td>${new Date(row.dueAt).toLocaleDateString("pt-BR")}</td></tr>`).join("");
  $("#today-progress").innerHTML = rows
    ? `<table class="subject-table"><thead><tr><th>Conteúdo</th><th>Revisões</th><th>Acertos</th><th>Lembrança hoje</th><th>Próxima revisão</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty-state"><div class="empty-icon">◷</div><strong>Nenhum conteúdo em revisão ainda</strong><p>Responda o estudo de hoje ou um diagnóstico para começar o agendamento.</p></div>`;
}

function updateDailyTaskStatus() {
  if (!currentStudy) return;
  const daily = currentStudy.session.items.filter(item => !item.id.startsWith("t"));
  const done = daily.filter(item => item.answered).length;
  $("#daily-task-status").textContent = daily.length
    ? done >= daily.length ? `Tarefa diária concluída · ${done} de ${daily.length} questões`
      : `${done} de ${daily.length} questões concluídas`
    : "Tarefa diária concluída · nenhuma questão pendente";
}

function renderToday(workspace = $("#today-workspace")) {
  const { session, selected } = currentStudy;
  const items = session.items;
  if (!items.length) {
    workspace.innerHTML = `<div class="empty-state"><div class="empty-icon">☀</div><strong>Nada para estudar hoje</strong><p>Não há revisões vencidas nem questões classificadas com gabarito para conteúdos novos.</p></div>`;
    return;
  }
  const answered = items.filter(item => item.answered).length;
  const progress = Math.round(answered / items.length * 100);
  const top = `<div class="workspace-top"><div><p class="eyebrow">${currentStudy.training ? "TREINAMENTO CONTÍNUO" : `SESSÃO DE ${escapeHtml(formatDay(session.date)).toUpperCase()}`}</p><h2>${answered} de ${items.length} respondidas</h2><div class="workspace-meta">${session.correct} acertos até agora</div></div></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>`;
  const item = currentStudy.lastAnswer ? items.find(entry => entry.id === currentStudy.lastAnswer.itemId) : items.find(entry => !entry.answered);
  if (!item) {
    const list = items.map(entry => `<tr><td><span class="today-reason ${entry.reason}">${STUDY_REASONS[entry.reason]}</span></td><td>${escapeHtml(entry.contentPath)}</td><td>${entry.selectedAnswer ? escapeHtml(entry.selectedAnswer) : "Branco"} / ${escapeHtml(entry.correctAnswer)}</td><td>${entry.correct ? "✓" : "✗"}</td></tr>`).join("");
    workspace.innerHTML = `${top}<div class="exam-final-summary"><strong>Bloco concluído: ${session.correct} acertos</strong><p>As revisões foram atualizadas. Ajuste matérias e quantidade acima para adicionar mais questões quando quiser.</p></div><table class="subject-table"><thead><tr><th>Motivo</th><th>Conteúdo</th><th>Sua resposta / gabarito</th><th></th></tr></thead><tbody>${list}</tbody></table>`;
    return;
  }
  const question = item.question || {};
  const result = currentStudy.lastAnswer;
  const submitted = Boolean(result);
  const feedback = submitted
    ? `<div class="exam-answer-feedback ${result.correct ? "correct" : "incorrect"}">${result.correct ? "Resposta correta!" : `${result.answer ? "Resposta incorreta" : "Em branco"}. Gabarito: ${escapeHtml(result.correctAnswer)}.`} Próxima revisão deste conteúdo: ${new Date(result.nextReview).toLocaleDateString("pt-BR")}.</div>`
    : "";
  const reasonPanel = currentStudy.pendingReason ? `<div class="reason-row"><label>Qual foi o motivo principal?<select id="today-reason"><option value="">Selecione</option><option value="unknown_content">Não sabia o conteúdo</option><option value="calc_error">Errei o cálculo</option><option value="misread">Interpretei errado</option></select></label><button class="button button-primary" id="today-confirm-reason">Registrar resposta</button></div>` : "";
  const actions = submitted
    ? `<div class="exam-quiz-actions"><span></span><button class="button button-primary" id="today-next">${answered === items.length ? "Ver resumo" : "Próxima questão"} →</button></div>`
    : `<div class="exam-quiz-actions"><button class="blank-button ${selected === "" ? "selected" : ""}" id="today-blank" aria-pressed="${selected === ""}">Deixar em branco</button><button class="button button-primary" id="today-submit" ${selected == null ? "disabled" : ""}>Responder</button></div>`;
  workspace.innerHTML = `${top}<div class="question-top"><span><span class="today-reason ${item.reason}">${STUDY_REASONS[item.reason]}</span><span class="question-subject">${escapeHtml(item.contentPath)}</span></span><span>Questão ${items.indexOf(item) + 1} de ${items.length}</span></div>
    ${questionContentMarkup(item.sourceId, question, question._texto_compartilhado)}
    <div class="exam-options">${optionsMarkup(item.sourceId, question, { selected: submitted ? result.answer : selected, submitted, correctAnswer: submitted ? result.correctAnswer : null, saved: submitted ? result.answer : null, attr: "data-today-option" })}</div>
    ${reasonPanel}${feedback}${actions}`;
  enhanceFocusMode(workspace, `${items.indexOf(item) + 1} de ${items.length}`);
  $$("[data-today-option]", workspace).forEach(button => button.addEventListener("click", () => {
    currentStudy.selected = button.dataset.todayOption;
    currentStudy.pendingReason = null;
    renderToday(workspace);
  }));
  $("#today-submit", workspace)?.addEventListener("click", () => submitStudyAnswer(item, currentStudy.selected));
  $("#today-blank", workspace)?.addEventListener("click", () => {
    currentStudy.selected = currentStudy.selected === "" ? null : "";
    currentStudy.pendingReason = null;
    renderToday(workspace);
  });
  $("#today-confirm-reason", workspace)?.addEventListener("click", () => {
    const reason = $("#today-reason", workspace).value;
    if (!reason) return toast("Escolha o motivo do erro.", true);
    submitStudyAnswer(item, currentStudy.selected, reason);
  });
  $("#today-next", workspace)?.addEventListener("click", () => {
    currentStudy.lastAnswer = null;
    currentStudy.selected = null;
    renderToday(workspace);
  });
}

async function submitStudyAnswer(item, answer, reason = null) {
  if (currentStudy?.submitting) return;
  currentStudy.submitting = true;
  try {
    const result = await api("/api/study/today/answers", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: currentStudy.session.id, itemId: item.id, answer: answer || null, reason }),
    });
    if (result.requiresReason) {
      currentStudy.submitting = false;
      currentStudy.pendingReason = true;
      return renderToday();
    }
    currentStudy.submitting = false;
    Object.assign(item, { answered: true, selectedAnswer: result.answer, correct: result.correct, correctAnswer: result.correctAnswer });
    currentStudy.session.answered++;
    if (result.correct) currentStudy.session.correct++;
    currentStudy.lastAnswer = result;
    currentStudy.pendingReason = null;
    currentStudy.selected = null;
    updateDailyTaskStatus();
    if (currentStudy.training) {
      updateTrainingCounters(currentStudy.session);
    }
    renderToday();
    window.setTimeout(() => {
      if (currentStudy?.lastAnswer !== result) return;
      currentStudy.lastAnswer = null;
      renderToday();
    }, 900);
  } catch (error) {
    if (currentStudy) currentStudy.submitting = false;
    toast(error.message, true);
  }
}

async function appendTrainingBlock() {
  if (!currentStudy?.session || currentStudy.submitting) return toast("Aguarde o carregamento da tarefa diária.", true);
  const button = $("#add-training-block");
  const subjects = $$('input[name="training-subject"]:checked').map(input => input.value);
  const count = Number($("#training-question-count").value);
  if (!subjects.length) return toast("Selecione ao menos uma matéria.", true);
  if (!Number.isInteger(count) || count < 1 || count > 100) return toast("Escolha uma quantidade entre 1 e 100 questões.", true);
  if (button) { button.disabled = true; button.textContent = "Montando seu bloco…"; }
  try {
    const previousCount = currentStudy.session.items.length;
    const session = await api("/api/study/training/next", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: currentStudy.session.id, subjects, count }),
    });
    currentStudy.session = session;
    currentStudy.lastAnswer = null;
    currentStudy.selected = null;
    currentStudy.pendingReason = null;
    updateTrainingCounters(session);
    const added = session.items.length - previousCount;
    toast(`${added} ${added === 1 ? "questão adicionada" : "questões adicionadas"}. Elas aparecem uma por vez no treino.`);
    renderToday();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) { button.disabled = false; button.textContent = "Adicionar ao treino →"; }
  }
}

async function resetLearningContext() {
  const confirmed = window.confirm(
    "Zerar o contexto de aprendizado? Isso remove progresso, sessões diárias, cartões de revisão, respostas e histórico de diagnósticos. As tentativas de provas serão mantidas, mas suas respostas e métricas serão zeradas. Arquivos, questões, gabaritos e configurações não serão apagados.",
  );
  if (!confirmed) return;
  const button = $("#reset-learning-context");
  button.disabled = true;
  button.textContent = "Zerando progresso…";
  try {
    await api("/api/learning-context", { method: "DELETE" });
    currentStudy = null;
    currentDiagnostic = null;
    diagnosticDone.clear();
    $("#diagnostic-workspace").classList.add("hidden");
    await Promise.all([refreshToday(), refreshDiagnostics()]);
    window.refreshReports?.();
    window.refreshKnowledgeMap?.();
    toast("Contexto de aprendizado resetado. Suas provas e gabaritos continuam salvos.");
  } catch (error) {
    toast(`Não foi possível resetar o contexto: ${error.message}`, true);
  } finally {
    button.disabled = false;
    button.textContent = "Resetar meu contexto";
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
  $("#refresh-today").addEventListener("click", refreshToday);
  $("#training-course").addEventListener("change", () => {
    try { localStorage.setItem("studai-today-course", $("#training-course").value); } catch {}
    renderTrainingSubjects();
    refreshToday();
  });
  $("#add-training-block").addEventListener("click", appendTrainingBlock);
  $("#reset-learning-context").addEventListener("click", resetLearningContext);
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
  $("#diagnostic-mode")?.addEventListener("change", event => {
    const adaptive = event.target.value === "adaptive";
    const total = $("#diagnostic-total");
    total.max = adaptive ? "51" : "100";
    total.value = adaptive ? "30" : "51";
    $("#diagnostic-total-label").textContent = adaptive ? "Máximo de questões" : "Questões";
    $("#diagnostic-mode-help").textContent = adaptive
      ? "O adaptativo escolhe a próxima questão pelas suas respostas e respeita o peso de cada matéria no curso."
      : "O estático sorteia todas as questões de uma vez, distribuídas pelo peso das matérias.";
  });
}

applyTheme(localStorage.getItem("studai-theme") || "light");
bindEvents();
setupProviders();
setupAIOptions();
refreshExams();
refreshCourses();
refreshDiagnostics();
recoverActiveProcess();
