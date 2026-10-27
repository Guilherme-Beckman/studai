// Resolução comentada e dicas graduais (Fase 11).
//
// Os fluxos de resposta só inserem um marcador com `studaiExplain.slot(...)` e chamam
// `studaiExplain.mount(workspace)` depois de renderizar. Antes de responder, o marcador
// mostra o botão "Me dá uma dica"; depois, a resolução completa num <details>. O estado
// fica num cache por questão, então re-renderizar o fluxo não perde as dicas já abertas.
(() => {
  "use strict";

  const HINTS = 3;
  const cache = new Map(); // "sourceId:number" -> { hints, full, loading, error, editing }
  const esc = value => escapeHtml(value ?? "");
  const keyOf = (sourceId, number) => `${sourceId}:${number}`;
  const entry = key => {
    if (!cache.has(key)) cache.set(key, { hints: [], full: null, loading: false, error: null, editing: false });
    return cache.get(key);
  };

  function slot(sourceId, number, context = {}, { mode = "hints", open = false } = {}) {
    return `<div class="explain-slot" data-explain-slot data-source="${esc(sourceId)}" data-number="${esc(number)}" data-mode="${mode}" data-open="${open ? "1" : ""}" data-context="${esc(JSON.stringify(context))}"></div>`;
  }

  function hintsUsed(sourceId, number) {
    return cache.get(keyOf(sourceId, number))?.hints.length || 0;
  }

  async function waitJob(jobId) {
    for (let attempt = 0; attempt < 240; attempt++) {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (job.status === "completed") return;
      if (job.status === "failed" || job.status === "cancelled") throw new Error(job.error || job.message || "a geração falhou");
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
    throw new Error("a geração está demorando demais; tente de novo em instantes");
  }

  async function fetchExplanation(sourceId, number, context, hints) {
    const params = new URLSearchParams(Object.entries({ ...context, hints: String(hints) }).filter(([, value]) => value != null && value !== ""));
    const path = `/api/questions/${encodeURIComponent(sourceId)}/${encodeURIComponent(number)}/explanation?${params}`;
    let data = await api(path);
    if (data.status === "generating") {
      await waitJob(data.jobId);
      data = await api(path);
    }
    return data;
  }

  function render(el) {
    const { source, number, mode } = el.dataset;
    const state = entry(keyOf(source, number));
    if (mode === "hints") {
      const used = state.hints.length;
      const list = state.hints.map((hint, index) => `<li><b>Dica ${index + 1}.</b> ${esc(hint)}</li>`).join("");
      const button = used < HINTS
        ? `<button type="button" class="text-button explain-hint-button" data-explain-hint ${state.loading ? "disabled" : ""}>${state.loading ? "Preparando a dica…" : used ? `Mais uma dica (${used}/${HINTS})` : "Me dá uma dica"}</button>`
        : "";
      const note = used ? `<span class="explain-note">Usar dica conta como resposta com dúvida na revisão.</span>` : "";
      el.innerHTML = `${list ? `<ol class="explain-hints">${list}</ol>` : ""}<div class="explain-hint-row">${button}${note}</div>${state.error ? `<div class="explain-error">${esc(state.error)}</div>` : ""}`;
      return;
    }
    const open = el.dataset.open === "1" || state.opened;
    const full = state.full;
    let body = "";
    if (state.loading) body = `<p class="explain-loading">Gerando a resolução comentada…</p>`;
    else if (state.error) body = `<div class="explain-error">${esc(state.error)} <button type="button" class="text-button" data-explain-retry>Tentar de novo</button></div>`;
    else if (full) {
      const steps = full.steps.map(step => `<li>${esc(step)}</li>`).join("");
      const distractors = Object.entries(full.distractors || {}).map(([letter, text]) => `<li><b>${esc(letter)}</b> ${esc(text)}</li>`).join("");
      const review = full.status === "approved"
        ? `<span class="explain-approved">✓ Você aprovou esta resolução</span>`
        : state.editing
          ? `<label class="explain-feedback">O que está errado?<textarea data-explain-feedback rows="2" maxlength="1000" placeholder="Ex.: o passo 2 troca o sinal"></textarea></label><div class="explain-review"><button type="button" class="button button-primary" data-explain-reject>Gerar outra resolução</button><button type="button" class="text-button" data-explain-cancel>Cancelar</button></div>`
          : `<div class="explain-review"><span>A resolução está certa?</span><button type="button" class="button button-secondary" data-explain-approve>Está certa ✓</button><button type="button" class="text-button" data-explain-edit>Tem erro</button></div>`;
      body = `<p class="explain-concept"><b>Conceito-chave:</b> ${esc(full.key_concept)}</p><h4>Resolução</h4><ol class="explain-steps">${steps}</ol>${distractors ? `<h4>Por que as outras estão erradas</h4><ul class="explain-distractors">${distractors}</ul>` : ""}${review}`;
    }
    el.innerHTML = `<details class="explain-panel" ${open ? "open" : ""}><summary>Resolução comentada</summary><div class="explain-body">${body}</div></details>`;
    if (open && !full && !state.loading && !state.error) loadFull(el);
  }

  async function loadFull(el) {
    const { source, number } = el.dataset;
    const state = entry(keyOf(source, number));
    state.loading = true; state.error = null; state.opened = true;
    render(el);
    try {
      state.full = await fetchExplanation(source, number, JSON.parse(el.dataset.context || "{}"), HINTS);
      if (!state.full.steps) throw new Error("responda à questão para ver a resolução");
    } catch (error) {
      state.error = `Não foi possível carregar a resolução: ${error.message}`;
    } finally {
      state.loading = false;
      if (el.isConnected) render(el);
    }
  }

  async function nextHint(el) {
    const { source, number } = el.dataset;
    const state = entry(keyOf(source, number));
    if (state.loading || state.hints.length >= HINTS) return;
    state.loading = true; state.error = null;
    render(el);
    try {
      const data = await fetchExplanation(source, number, JSON.parse(el.dataset.context || "{}"), state.hints.length + 1);
      state.hints = data.hints || state.hints;
    } catch (error) {
      state.error = `Não foi possível gerar a dica: ${error.message}`;
    } finally {
      state.loading = false;
      if (el.isConnected) render(el);
    }
  }

  async function review(el, approved) {
    const { source, number } = el.dataset;
    const state = entry(keyOf(source, number));
    const context = JSON.parse(el.dataset.context || "{}");
    const feedback = el.querySelector("[data-explain-feedback]")?.value.trim() || "";
    if (!approved && !feedback) return toast("Descreva o erro da resolução.", true);
    try {
      const result = await api(`/api/questions/${encodeURIComponent(source)}/${encodeURIComponent(number)}/explanation/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, feedback, courseId: context.courseId }),
      });
      state.editing = false;
      if (result.status === "generating") {
        state.full = null; state.loading = true;
        render(el);
        await waitJob(result.jobId);
        state.loading = false;
        return loadFull(el);
      }
      state.full = { ...state.full, status: result.status };
      toast("Obrigado! Resolução marcada como certa.");
    } catch (error) {
      state.loading = false;
      toast(error.message, true);
    }
    if (el.isConnected) render(el);
  }

  function mount(root) {
    root.querySelectorAll("[data-explain-slot]").forEach(el => {
      render(el);
      if (el.dataset.bound) return;
      el.dataset.bound = "1";
      el.addEventListener("toggle", event => {
        if (event.target.matches?.(".explain-panel") && event.target.open) {
          const state = entry(keyOf(el.dataset.source, el.dataset.number));
          state.opened = true;
          if (!state.full && !state.loading) { state.error = null; loadFull(el); }
        }
      }, true);
      el.addEventListener("click", event => {
        const target = event.target.closest("button");
        if (!target) return;
        const state = entry(keyOf(el.dataset.source, el.dataset.number));
        if (target.matches("[data-explain-hint]")) nextHint(el);
        else if (target.matches("[data-explain-retry]")) { state.error = null; loadFull(el); }
        else if (target.matches("[data-explain-approve]")) review(el, true);
        else if (target.matches("[data-explain-edit]")) { state.editing = true; render(el); el.querySelector("textarea")?.focus(); }
        else if (target.matches("[data-explain-cancel]")) { state.editing = false; render(el); }
        else if (target.matches("[data-explain-reject]")) review(el, false);
      });
    });
  }

  function prefetch(sessionId) {
    if (!sessionId) return;
    api("/api/study/explanations/prefetch", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessionId }),
    }).catch(() => { /* sem provedor configurado: as resoluções são geradas quando pedidas */ });
  }

  window.studaiExplain = { slot, mount, hintsUsed, prefetch };
})();
