// Recorte manual por região (Fase 3): a pessoa desenha um retângulo sobre a página do
// PDF e o recorte entra no formulário de revisão da questão, como imagem do enunciado
// ou de uma alternativa. Nada é salvo até ela clicar em "Salvar" no formulário, e a
// correção fica em revisao.json (edits), sem mudar a extração.
// Depende de globais de app.js: currentExamReview, api, toast, escapeHtml,
// figureEditorItems e bindFigureRemoval.
(() => {
  const PREVIEW_DPI = 110;
  const STYLE = `
.crop-overlay{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;padding:16px;background:rgba(10,20,32,.62)}
.crop-dialog{display:flex;flex-direction:column;gap:12px;width:min(980px,100%);max-height:100%;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--paper);color:var(--ink);box-shadow:var(--shadow)}
.crop-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}
.crop-head strong{font-size:16px}.crop-head span{color:var(--muted);font-size:12px}
.crop-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:10px;font-size:13px}
.crop-toolbar label{display:flex;align-items:center;gap:6px}
.crop-toolbar select,.crop-toolbar input[type=number]{padding:5px 7px;border:1px solid var(--line);border-radius:6px;background:var(--paper);color:var(--ink)}
.crop-toolbar input[type=number]{width:64px}
.crop-stage{position:relative;overflow:auto;min-height:200px;border:1px solid var(--line);border-radius:8px;background:var(--canvas)}
.crop-page{position:relative;display:inline-block;cursor:crosshair;user-select:none;touch-action:none}
.crop-page img{display:block;max-width:100%;height:auto;pointer-events:none}
.crop-rect{position:absolute;border:2px solid var(--blue);background:rgba(33,104,216,.14);pointer-events:none}
.crop-message{padding:24px;color:var(--muted);font-size:13px}
.crop-message input{margin-top:10px}
.crop-actions{display:flex;flex-wrap:wrap;gap:10px;justify-content:flex-end}
`;

  let state = null;

  function injectStyle() {
    if (document.getElementById("crop-editor-style")) return;
    const style = document.createElement("style");
    style.id = "crop-editor-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  function examId() { return currentExamReview?.exam?.id; }
  function examPath(suffix) { return `/api/exams/${encodeURIComponent(examId())}${suffix}`; }

  function close() {
    state?.overlay.remove();
    document.removeEventListener("keydown", onKey);
    state = null;
  }

  function onKey(event) { if (event.key === "Escape") close(); }

  function targetOptions(question) {
    const letters = [...new Set([...Object.keys(question?.alternativas || {}), ..."ABCDE"])].sort();
    return `<option value="stem">Imagem do enunciado</option>${letters.map(letter => `<option value="option:${letter}">Imagem da alternativa ${letter}</option>`).join("")}`;
  }

  async function open(number) {
    if (!examId()) return;
    injectStyle();
    close();
    const question = currentExamReview.questions.find(q => String(q.numero) === String(number));
    const overlay = document.createElement("div");
    overlay.className = "crop-overlay";
    overlay.innerHTML = `<div class="crop-dialog" role="dialog" aria-modal="true" aria-label="Recortar região do PDF">
      <div class="crop-head"><div><strong>Recortar região · Questão ${escapeHtml(number)}</strong><br><span>Arraste sobre a página para marcar a região. O recorte entra no formulário; salve a revisão para gravar.</span></div><button type="button" class="blank-button" data-crop-close>Fechar</button></div>
      <div class="crop-toolbar">
        <button type="button" class="button button-secondary" data-crop-prev aria-label="Página anterior">‹</button>
        <label>Página <input type="number" min="1" value="${Number(question?.pagina) || 1}" data-crop-page></label><span data-crop-total></span>
        <button type="button" class="button button-secondary" data-crop-next aria-label="Próxima página">›</button>
        <label>Destino <select data-crop-target>${targetOptions(question)}</select></label>
        <label><input type="checkbox" data-crop-replace> Substituir as imagens atuais</label>
        <label><input type="checkbox" data-crop-text> Preencher o texto com o texto da região</label>
      </div>
      <div class="crop-stage" data-crop-stage><div class="crop-message">Carregando o PDF…</div></div>
      <div class="crop-actions"><button type="button" class="blank-button" data-crop-clear>Limpar seleção</button><button type="button" class="button button-primary" data-crop-apply disabled>Recortar e usar</button></div>
    </div>`;
    document.body.appendChild(overlay);
    state = { overlay, number: String(number), pages: [], page: Number(question?.pagina) || 1, region: null };
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.addEventListener("keydown", onKey);
    const q = selector => overlay.querySelector(selector);
    q("[data-crop-close]").addEventListener("click", close);
    q("[data-crop-prev]").addEventListener("click", () => showPage(state.page - 1));
    q("[data-crop-next]").addEventListener("click", () => showPage(state.page + 1));
    q("[data-crop-page]").addEventListener("change", event => showPage(Number(event.target.value)));
    q("[data-crop-clear]").addEventListener("click", () => setRegion(null));
    q("[data-crop-apply]").addEventListener("click", applyCrop);
    await loadPages();
  }

  async function loadPages() {
    const stage = state.overlay.querySelector("[data-crop-stage]");
    try {
      const { pages } = await api(examPath("/source/pages"));
      if (!state) return;
      state.pages = pages;
      state.overlay.querySelector("[data-crop-total]").textContent = `de ${pages.length}`;
      showPage(state.page);
    } catch (error) {
      if (!state) return;
      stage.innerHTML = `<div class="crop-message">${escapeHtml(error.message)}${error.status === 404 ? `<br><label>Enviar o PDF desta prova <input type="file" accept="application/pdf,.pdf" data-crop-source></label>` : ""}</div>`;
      stage.querySelector("[data-crop-source]")?.addEventListener("change", event => uploadSource(event.target.files?.[0]));
    }
  }

  async function uploadSource(file) {
    if (!file || !state) return;
    try {
      await api(examPath(`/source?filename=${encodeURIComponent(file.name)}`), {
        method: "POST", headers: { "Content-Type": "application/pdf" }, body: file });
      toast("PDF associado à prova.");
      await loadPages();
    } catch (error) { toast(error.message, true); }
  }

  function showPage(number) {
    if (!state || !state.pages.length) return;
    const page = Math.max(1, Math.min(state.pages.length, Number(number) || 1));
    state.page = page;
    state.overlay.querySelector("[data-crop-page]").value = page;
    const stage = state.overlay.querySelector("[data-crop-stage]");
    stage.innerHTML = `<div class="crop-page" data-crop-surface><img alt="Página ${page} do PDF" src="${examPath(`/source/pages/${page}.png?dpi=${PREVIEW_DPI}`)}"><div class="crop-rect" hidden></div></div>`;
    setRegion(null);
    bindDrawing(stage.querySelector("[data-crop-surface]"));
  }

  function bindDrawing(surface) {
    let start = null;
    const point = event => {
      const box = surface.getBoundingClientRect();
      return { x: Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)),
        y: Math.min(1, Math.max(0, (event.clientY - box.top) / box.height)) };
    };
    surface.addEventListener("pointerdown", event => {
      event.preventDefault();
      surface.setPointerCapture(event.pointerId);
      start = point(event);
      setRegion([start.x, start.y, start.x, start.y], false);
    });
    surface.addEventListener("pointermove", event => {
      if (!start) return;
      const current = point(event);
      setRegion([start.x, start.y, current.x, current.y], false);
    });
    const finish = event => {
      if (!start) return;
      const current = point(event);
      const region = [Math.min(start.x, current.x), Math.min(start.y, current.y), Math.max(start.x, current.x), Math.max(start.y, current.y)];
      start = null;
      setRegion(region[2] - region[0] > 0.01 && region[3] - region[1] > 0.005 ? region : null);
    };
    surface.addEventListener("pointerup", finish);
    surface.addEventListener("pointercancel", () => { start = null; setRegion(null); });
  }

  function setRegion(region, final = true) {
    if (!state) return;
    const rect = state.overlay.querySelector(".crop-rect");
    if (rect) {
      if (region) {
        const [x0, y0, x1, y1] = [Math.min(region[0], region[2]), Math.min(region[1], region[3]), Math.max(region[0], region[2]), Math.max(region[1], region[3])];
        Object.assign(rect.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
        rect.hidden = false;
      } else rect.hidden = true;
    }
    if (final) state.region = region;
    state.overlay.querySelector("[data-crop-apply]").disabled = !(final ? region : state.region);
  }

  function reviewForm(number) {
    const workspace = document.getElementById("exam-question-workspace");
    const form = workspace?.querySelector(`[data-review-form="${CSS.escape(number)}"]`);
    if (form?.classList.contains("hidden")) workspace.querySelector(`[data-toggle-review="${CSS.escape(number)}"]`)?.click();
    return form;
  }

  async function applyCrop() {
    if (!state?.region) return;
    const { number, page, region, overlay } = state;
    const target = overlay.querySelector("[data-crop-target]").value;
    const replace = overlay.querySelector("[data-crop-replace]").checked;
    const fillText = overlay.querySelector("[data-crop-text]").checked;
    const button = overlay.querySelector("[data-crop-apply]");
    button.disabled = true;
    try {
      const crop = await api(examPath(`/questions/${encodeURIComponent(number)}/manual-crop`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page, region, extractText: fillText }),
      });
      const form = reviewForm(number);
      if (!form) throw new Error("Abra a questão na revisão para usar o recorte.");
      const container = form.querySelector(`[data-figures="${CSS.escape(target)}"]`);
      if (replace) container.querySelectorAll("[data-figure-path]").forEach(item => item.remove());
      container.querySelector(".review-figure-upload").insertAdjacentHTML("beforebegin", figureEditorItems(examId(), [crop.path]));
      bindFigureRemoval(container);
      const filled = fillText ? fillFromSuggestion(form, crop.suggestion) : 0;
      close();
      form.scrollIntoView({ behavior: "smooth", block: "start" });
      toast(`Recorte adicionado${filled ? " e texto preenchido" : ""}. Confira e salve a revisão.`);
    } catch (error) {
      if (state) button.disabled = false;
      toast(error.message, true);
    }
  }

  function fillFromSuggestion(form, suggestion) {
    let filled = 0;
    if (suggestion?.enunciado?.trim()) {
      form.querySelector("[data-edit-stem]").value = suggestion.enunciado;
      filled += 1;
    }
    Object.entries(suggestion?.alternativas || {}).forEach(([letter, text]) => {
      const field = form.querySelector(`[data-edit-option="${CSS.escape(letter)}"] [data-edit-option-text]`);
      if (field && String(text || "").trim()) { field.value = text; filled += 1; }
    });
    return filled;
  }

  document.addEventListener("click", event => {
    const trigger = event.target.closest?.("[data-manual-crop]");
    if (!trigger) return;
    event.preventDefault();
    open(trigger.dataset.manualCrop);
  });
})();
