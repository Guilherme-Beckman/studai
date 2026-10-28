// Modo foco: mostra a questão em tela cheia (Fullscreen API, com sobreposição fixa como
// fallback) e reaproveita o próprio workspace de cada fluxo ("Hoje", Diagnósticos e
// "Responder prova"). O workspace é movido para a sobreposição enquanto o modo está
// aberto; cada fluxo continua renderizando e salvando as respostas pelos seus próprios
// handlers. Este módulo só acrescenta a moldura, o carrossel, os atalhos e o gesto.
//
// Uso nos fluxos: depois de cada renderização, chame
//   window.studaiFocus.attach(workspace, { current, total, key })
// `current`/`total` alimentam "Questão X de N"; `key` identifica a questão exibida
// (muda => a nova questão entra deslizando). Sem `current`, não há questão na tela.
(() => {
  const OPTION = "[data-exam-option], [data-today-option], [data-answer], [data-sim-option]";
  const SUBMIT = "[data-exam-submit], [data-today-submit], [data-diagnostic-submit]";
  const CONFIRM = "#confirm-reason, #today-confirm-reason";
  const NEXT = "[data-exam-next], #today-next, [data-sim-next]";
  const PREV = "[data-exam-prev], [data-sim-prev]";
  const FEEDBACK = ".exam-answer-feedback, .exam-reason-prompt, .reason-row:not(.hidden)";
  const HINT = "A–E escolher · 1/2/3 certeza/dúvida/chute · 1–6 motivo do erro · H dica · R resolução · Enter confirmar · ←/→ navegar · Esc sair";

  const state = {
    workspace: null, overlay: null, stage: null, placeholder: null, observer: null,
    info: null, snapshot: null, fullscreen: false, ghostTimer: null,
  };

  const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const usable = el => Boolean(el) && !el.disabled && !el.closest(".hidden");
  const find = selector => [...(state.workspace?.querySelectorAll(selector) || [])].find(usable) || null;
  const optionLetter = button => button.dataset.examOption || button.dataset.todayOption || button.dataset.answer || button.dataset.simOption || "";

  function buildOverlay() {
    const overlay = document.createElement("div");
    overlay.className = "focus-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Modo foco");
    overlay.innerHTML = `<div class="focus-bar"><span class="focus-count" aria-live="polite"></span><span class="focus-hint">${HINT}</span><button type="button" class="focus-exit" aria-label="Sair do modo foco (Esc)" title="Sair do modo foco (Esc)">×</button></div><div class="focus-meter" aria-hidden="true"><i></i></div><div class="focus-stage" tabindex="-1"></div>`;
    overlay.querySelector(".focus-exit").addEventListener("click", close);
    let touch = null;
    overlay.addEventListener("touchstart", event => {
      const point = event.changedTouches[0];
      touch = event.touches.length === 1 ? { x: point.clientX, y: point.clientY } : null;
    }, { passive: true });
    overlay.addEventListener("touchend", event => {
      if (!touch) return;
      const point = event.changedTouches[0];
      const dx = point.clientX - touch.x;
      const dy = point.clientY - touch.y;
      touch = null;
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      if (dx < 0) find(NEXT)?.click(); else find(PREV)?.click();
    }, { passive: true });
    return overlay;
  }

  function updateBar() {
    if (!state.overlay) return;
    const info = state.info || {};
    const total = Number(info.total);
    const hasTotal = Number.isFinite(total) && total > 0;
    const label = info.current == null
      ? (info.label || "Sessão concluída")
      : `Questão ${info.current}${hasTotal ? ` de ${total}` : ""}`;
    state.overlay.querySelector(".focus-count").textContent = label;
    const ratio = info.current == null ? 1 : (hasTotal ? Math.min(1, info.current / total) : 0);
    state.overlay.querySelector(".focus-meter i").style.width = `${Math.round(ratio * 100)}%`;
  }

  // Cópia inerte do conteúdo atual, usada para animar a saída da questão anterior.
  function takeSnapshot() {
    if (!state.workspace || reducedMotion()) { state.snapshot = null; return; }
    const clone = state.workspace.cloneNode(true);
    clone.removeAttribute("id");
    clone.querySelectorAll("[id]").forEach(el => el.removeAttribute("id"));
    clone.setAttribute("aria-hidden", "true");
    clone.inert = true;
    state.snapshot = clone;
  }

  function slide(direction) {
    const { workspace, stage, snapshot } = state;
    if (!workspace || reducedMotion()) return;
    const incoming = direction < 0 ? "focus-in-left" : "focus-in-right";
    workspace.classList.remove("focus-in-left", "focus-in-right");
    void workspace.offsetWidth; // reinicia a animação
    workspace.classList.add(incoming);
    stage.querySelectorAll(".focus-ghost").forEach(ghost => ghost.remove());
    clearTimeout(state.ghostTimer);
    if (!snapshot) return;
    snapshot.classList.remove("focus-in-left", "focus-in-right");
    snapshot.classList.add("focus-ghost", direction < 0 ? "focus-out-right" : "focus-out-left");
    snapshot.style.left = `${workspace.offsetLeft}px`;
    snapshot.style.top = `${workspace.offsetTop}px`;
    snapshot.style.width = `${workspace.offsetWidth}px`;
    stage.append(snapshot);
    const remove = () => snapshot.remove();
    snapshot.addEventListener("animationend", remove, { once: true });
    state.ghostTimer = setTimeout(remove, 700);
  }

  function injectButton(workspace, info) {
    workspace.querySelector(".focus-open")?.remove();
    const header = workspace.querySelector(".workspace-top");
    if (!header || info.current == null) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-secondary focus-open";
    button.title = `Abrir a questão em tela cheia (${HINT})`;
    button.innerHTML = `<span aria-hidden="true">⛶</span> Modo foco`;
    button.addEventListener("click", () => open(workspace));
    const action = [...header.children].find(child => child.tagName === "BUTTON");
    if (action) header.insertBefore(button, action); else header.append(button);
  }

  function attach(workspace, info = {}) {
    if (!workspace) return;
    workspace.studaiFocusInfo = info;
    if (state.workspace !== workspace) { injectButton(workspace, info); return; }
    const previous = state.info || {};
    state.info = info;
    updateBar();
    if (info.key != null && info.key !== previous.key) {
      state.stage.scrollTop = 0;
      const back = info.current != null && previous.current != null && info.current < previous.current;
      slide(back ? -1 : 1);
    } else {
      // Mesma questão re-renderizada depois de responder: traz o feedback para a vista.
      const feedback = [...workspace.querySelectorAll(FEEDBACK)].pop();
      if (feedback && !state.hadFeedback) feedback.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    }
    state.hadFeedback = Boolean(workspace.querySelector(FEEDBACK));
    takeSnapshot();
  }

  function open(workspace) {
    if (state.workspace) close();
    const overlay = state.overlay || (state.overlay = buildOverlay());
    state.stage = overlay.querySelector(".focus-stage");
    state.workspace = workspace;
    state.info = workspace.studaiFocusInfo || {};
    state.hadFeedback = Boolean(workspace.querySelector(FEEDBACK)); // só rola quando o feedback aparece
    state.placeholder = document.createComment("modo-foco");
    workspace.before(state.placeholder);
    state.stage.append(workspace);
    document.body.append(overlay);
    const toastEl = document.getElementById("toast");
    if (toastEl) overlay.append(toastEl); // em tela cheia só a sobreposição aparece
    document.documentElement.classList.add("focus-locked");
    document.querySelectorAll("body > :not(.focus-overlay)").forEach(el => { el.inert = true; });
    state.observer = new MutationObserver(() => { if (workspace.classList.contains("hidden")) close(); });
    state.observer.observe(workspace, { attributes: true, attributeFilter: ["class"] });
    updateBar();
    takeSnapshot();
    state.stage.focus({ preventScroll: true });
    const request = overlay.requestFullscreen || overlay.webkitRequestFullscreen;
    if (request) {
      try {
        const result = request.call(overlay, { navigationUI: "hide" });
        result?.catch?.(() => {}); // sem permissão: a sobreposição fixa continua valendo
      } catch { /* fallback: sobreposição */ }
    }
  }

  function close() {
    const workspace = state.workspace;
    if (!workspace) return;
    state.workspace = null;
    state.observer?.disconnect();
    clearTimeout(state.ghostTimer);
    state.stage.querySelectorAll(".focus-ghost").forEach(ghost => ghost.remove());
    workspace.classList.remove("focus-in-left", "focus-in-right");
    workspace.querySelectorAll(".focus-preselected").forEach(el => el.classList.remove("focus-preselected"));
    state.placeholder.replaceWith(workspace);
    const toastEl = state.overlay.querySelector("#toast");
    if (toastEl) document.body.append(toastEl);
    document.querySelectorAll("body > *").forEach(el => { el.inert = false; });
    state.overlay.remove();
    document.documentElement.classList.remove("focus-locked");
    const fullscreenEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fullscreenEl) (document.exitFullscreen || document.webkitExitFullscreen)?.call(document)?.catch?.(() => {});
    state.fullscreen = false;
    // Reaplica o botão com o estado mais recente (a questão pode ter mudado no modo foco).
    attach(workspace, workspace.studaiFocusInfo || {});
    workspace.scrollIntoView({ block: "start" });
    workspace.querySelector(".focus-open")?.focus({ preventScroll: true });
  }

  function onFullscreenChange() {
    const fullscreenEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fullscreenEl && fullscreenEl === state.overlay) state.fullscreen = true;
    else if (state.fullscreen && state.workspace) { state.fullscreen = false; close(); } // Esc do navegador
  }
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);

  function choose(letter) {
    const option = [...state.workspace.querySelectorAll(OPTION)].find(button => usable(button) && optionLetter(button) === letter);
    if (!option) return;
    // Fluxo com botão "Responder", ou simulado (marcar já grava e pode ser trocado).
    if (state.workspace.querySelector(SUBMIT) || option.matches("[data-sim-option]")) { option.click(); return; }
    // Diagnóstico: escolher já salva a resposta, então a letra só pré-seleciona e o Enter confirma.
    state.workspace.querySelectorAll(".focus-preselected").forEach(el => el.classList.remove("focus-preselected"));
    option.classList.add("focus-preselected");
    option.focus();
  }

  document.addEventListener("keydown", event => {
    if (!state.workspace || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    const typing = target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key === "Enter") {
      const confirm = find(CONFIRM);
      const ownButton = target instanceof HTMLButtonElement && state.overlay.contains(target);
      // Botão focado (Tab) mantém o Enter nativo, exceto quando o motivo do erro está pendente.
      if (ownButton && (!confirm || target.closest(".reason-row") || target.matches(".focus-exit"))) return;
      const action = confirm || find(".focus-preselected") || find(SUBMIT) || find(NEXT);
      if (action) { event.preventDefault(); action.click(); }
      return;
    }
    if (typing) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      const action = find(event.key === "ArrowRight" ? NEXT : PREV);
      if (action) { event.preventDefault(); action.click(); }
      return;
    }
    // H: mais uma dica; R: abre ou fecha a resolução comentada.
    if (event.key === "h" || event.key === "H") {
      const hint = find("[data-explain-hint]");
      if (hint) { event.preventDefault(); hint.click(); }
      return;
    }
    if (event.key === "r" || event.key === "R") {
      const panel = state.workspace.querySelector(".explain-panel");
      if (panel) { event.preventDefault(); panel.open = !panel.open; } // o "toggle" nativo carrega a resolução
      return;
    }
    if (/^[1-9]$/.test(event.key)) {
      const action = find(`[data-confidence-key="${event.key}"]`) || find(`[data-reason-key="${event.key}"]`);
      if (action) { event.preventDefault(); action.click(); }
      return;
    }
    if (/^[a-e]$/i.test(event.key) && !event.shiftKey) {
      event.preventDefault();
      choose(event.key.toUpperCase());
    }
  });

  window.studaiFocus = { attach, open, close, isOpen: () => Boolean(state.workspace) };
})();
