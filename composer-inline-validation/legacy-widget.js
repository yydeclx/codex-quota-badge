// Windows composer usage renderer. The host owns exact chat identity and data.
const LEGACY_WIDGET_CSS = `
@font-face {
  font-family: "Usage Poppins"; src: local("Poppins Regular"), local("Poppins-Regular"); font-weight: 400;
  unicode-range: U+0000-024F, U+1E00-1EFF, U+2000-206F, U+20A0-20CF, U+2190-21FF, U+2212;
}
@font-face {
  font-family: "Usage Poppins"; src: local("Poppins Bold"), local("Poppins-Bold"); font-weight: 700;
  unicode-range: U+0000-024F, U+1E00-1EFF, U+2000-206F, U+20A0-20CF, U+2190-21FF, U+2212;
}
@font-face {
  font-family: "Usage MiSans"; src: local("MiSans Regular"), local("MiSans-Regular"), local("MiSans"); font-weight: 400;
  unicode-range: U+2E80-33FF, U+3400-9FFF, U+F900-FAFF, U+FE10-FE6F, U+FF00-FFEF, U+20000-323AF;
}
@font-face {
  font-family: "Usage MiSans"; src: local("MiSans Demibold"), local("MiSans-Demibold"), local("MiSans Bold"), local("MiSans-Bold"); font-weight: 700;
  unicode-range: U+2E80-33FF, U+3400-9FFF, U+F900-FAFF, U+FE10-FE6F, U+FF00-FFEF, U+20000-323AF;
}
.inline-usage[data-legacy-usage] {
  display: grid; place-items: center; width: 100%; min-width: 0; max-width: 256px;
  --usage-font: "Usage Poppins", "Usage MiSans", "Microsoft YaHei", "Microsoft YaHei UI", sans-serif;
  --usage-ink: #555450;
  --usage-hover: rgb(0 0 0 / calc(13 / 255));
  --usage-active: 36 137 87;
  --usage-blue: 43 127 255;
  --usage-red: 194 76 69;
  --usage-amber: 190 137 49;
}
.inline-usage[data-legacy-usage][data-dark] {
  --usage-ink: #d8d7d4;
  --usage-hover: rgb(255 255 255 / calc(24 / 255));
  --usage-active: 86 204 134;
  --usage-blue: 98 156 255;
  --usage-red: 251 123 119;
  --usage-amber: 240 185 81;
}
.inline-usage[data-legacy-usage][data-ready] { max-width: 68px; }
[data-legacy-usage] .inline-pill {
  appearance: none; box-sizing: border-box; position: relative;
  display: flex; align-items: center; justify-content: center; gap: 5px;
  width: 100%; min-width: 0; height: 26px; max-height: 100%;
  margin: 0; padding: 0 6px; border: 0; border-radius: 7px;
  background: rgb(255 255 255 / calc(1 / 255)); color: var(--usage-ink);
  font: 400 11px/1.25 var(--usage-font);
  font-variant-numeric: tabular-nums; letter-spacing: normal; text-transform: none;
  white-space: pre; cursor: default;
}
[data-legacy-usage] .inline-pill:hover,
[data-legacy-usage][data-open] .inline-pill { background: var(--usage-hover); }
[data-legacy-usage] .inline-pill[data-status] { padding-inline: 2px; }
[data-legacy-usage][data-summary-weight="bold"] .inline-pill { font-weight: 700; }
[data-legacy-usage][data-summary-style="pill"] .inline-pill { border-radius: 999px; background: rgb(0 0 0 / .035); }
[data-legacy-usage][data-summary-style="pill"][data-dark] .inline-pill { background: rgb(255 255 255 / .055); }
[data-legacy-usage][data-summary-style="pill"] .inline-pill:hover,
[data-legacy-usage][data-summary-style="pill"][data-open] .inline-pill { background: var(--usage-hover); }
[data-legacy-usage] .inline-pill[data-narrow] { font-size: 9px; }
[data-legacy-usage] .inline-pill[disabled] { visibility: hidden; }
[data-legacy-usage] .inline-pill :is(span, i) { flex: none; }
[data-legacy-usage] .inline-pill span { overflow: visible; text-overflow: clip; }
[data-legacy-usage] .legacy-usage-measure {
  position: absolute; inset: 0 auto auto 0; width: max-content;
  visibility: hidden; pointer-events: none;
}
[data-legacy-usage] .inline-pill i {
  position: relative; width: 12px; height: 12px;
  background: none; box-shadow: none; animation: none; opacity: .941176;
  --usage-dot: var(--usage-blue);
  --usage-halo: 9px;
  --usage-halo-alpha: .160784;
}
[data-legacy-usage] .inline-pill i[data-state="active"] {
  --usage-dot: var(--usage-active); --usage-halo: 10px; --usage-halo-alpha: .207843;
  animation: legacy-usage-pulse 1.4s cubic-bezier(.37, 0, .63, 1) infinite;
}
[data-legacy-usage] .inline-pill i[data-state="red"] { --usage-dot: var(--usage-red); }
[data-legacy-usage] .inline-pill i[data-state="amber"] { --usage-dot: var(--usage-amber); }
[data-legacy-usage] .inline-pill i[hidden] { display: none; }
[data-legacy-usage] .inline-pill i::before,
[data-legacy-usage] .inline-pill i::after {
  content: ''; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: var(--usage-halo); height: var(--usage-halo); border-radius: 50%;
  background: rgb(var(--usage-dot) / var(--usage-halo-alpha));
}
[data-legacy-usage] .inline-pill i::after { width: 6px; height: 6px; background: rgb(var(--usage-dot)); }
@keyframes legacy-usage-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
[data-legacy-usage] .inline-popup {
  position: fixed; inset: auto; z-index: 100; box-sizing: border-box;
  overflow: auto; overscroll-behavior: contain; scrollbar-width: thin;
  margin: 0; padding: 0; max-width: none; max-height: none;
  border: 1px solid var(--usage-card-rule); border-radius: 12px;
  background: var(--usage-card-bg); color: var(--usage-card-ink);
  box-shadow: 0 8px 24px rgb(0 0 0 / .12), 0 2px 5px rgb(0 0 0 / .06);
  font: 400 11px/1.45 var(--usage-font);
  font-variant-numeric: tabular-nums; letter-spacing: normal; text-align: start;
  --usage-card-bg: #fff; --usage-card-ink: #14171b; --usage-card-muted: #4e5969;
  --usage-card-rule: #e4e7eb; --usage-card-panel: #f4f6f8; --usage-card-track: #e9ebee;
  --usage-card-blue: #1c66d4; --usage-card-green: #087959;
  --usage-card-offset: 4px;
  opacity: 0; transform: translateY(var(--usage-card-offset)); pointer-events: none; visibility: visible;
  transition: opacity 120ms ease-out, transform 120ms ease-out;
}
[data-legacy-usage] .inline-popup[data-below] { --usage-card-offset: -4px; }
[data-legacy-usage][data-dark] .inline-popup {
  --usage-card-bg: #202226; --usage-card-ink: #f2f3f4; --usage-card-muted: #bbc3cf;
  --usage-card-rule: #42474f; --usage-card-panel: #2c3139; --usage-card-track: #3a414c;
  --usage-card-blue: #8cb8ff; --usage-card-green: #78d5af;
  box-shadow: 0 8px 24px rgb(0 0 0 / .35), 0 2px 5px rgb(0 0 0 / .22);
}
[data-legacy-usage] .inline-popup[hidden] { display: none; }
[data-legacy-usage] .inline-popup[data-shown] { opacity: 1; transform: none; pointer-events: auto; }
[data-legacy-usage] .inline-popup::backdrop { display: none; }
[data-legacy-usage] .usage-card-content { padding: 10px 12px 7px; }
[data-legacy-usage] .usage-card-heading,
[data-legacy-usage] .usage-card-pair { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; }
[data-legacy-usage] .usage-card-heading { margin-bottom: 7px; }
[data-legacy-usage] .usage-card-heading strong { font-size: 13px; font-weight: 700; }
[data-legacy-usage] .usage-card-kpis {
  display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr) minmax(0, 1fr); border: 1px solid var(--usage-card-rule);
  border-radius: 8px; background: var(--usage-card-panel); padding: 6px 0;
}
[data-legacy-usage] .usage-card-kpi { min-width: 0; padding: 0 8px; }
[data-legacy-usage] .usage-card-kpi > div { overflow-wrap: anywhere; }
[data-legacy-usage] .usage-card-kpi > div:last-child { font-size: 10px; }
[data-legacy-usage] .usage-card-kpi + .usage-card-kpi { border-inline-start: 1px solid var(--usage-card-rule); }
[data-legacy-usage] .usage-card-muted { color: var(--usage-card-muted); }
[data-legacy-usage] .usage-card-num { display: block; font-size: 17px; font-weight: 700; line-height: 1.23; white-space: nowrap; }
[data-legacy-usage] .usage-card-num[data-tone="blue"] { color: var(--usage-card-blue); }
[data-legacy-usage] .usage-card-num[data-tone="green"] { color: var(--usage-card-green); }
[data-legacy-usage] .usage-card-num small { font-size: .8em; font-weight: 700; color: var(--usage-card-muted); }
[data-legacy-usage] .usage-card-chip { border-radius: 999px; background: var(--usage-card-panel); padding: 1px 7px; }
[data-legacy-usage] .usage-card-block { margin-top: 7px; }
[data-legacy-usage] .usage-card-section { margin-top: 7px; padding-top: 6px; border-top: 1px solid var(--usage-card-rule); }
[data-legacy-usage] .usage-card-split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
[data-legacy-usage] .usage-card-split > .usage-card-block { margin-top: 0; }
[data-legacy-usage] .usage-card-split > :nth-child(2) { border-inline-start: 1px solid var(--usage-card-rule); padding-inline-start: 8px; }
[data-legacy-usage] .inline-popup[data-compact] .usage-card-kpis { grid-template-columns: minmax(0, 1fr); padding: 0; }
[data-legacy-usage] .inline-popup[data-compact] .usage-card-kpi { padding-block: 5px; }
[data-legacy-usage] .inline-popup[data-compact] .usage-card-kpi + .usage-card-kpi {
  border-inline-start: 0; border-block-start: 1px solid var(--usage-card-rule);
}
[data-legacy-usage] .inline-popup[data-compact] .usage-card-split { grid-template-columns: minmax(0, 1fr); }
[data-legacy-usage] .inline-popup[data-compact] .usage-card-split > :nth-child(2) { border: 0; padding-inline-start: 0; }
[data-legacy-usage] .usage-card-bar { display: flex; height: 5px; margin: 4px 0; border-radius: 4px; overflow: hidden; background: var(--usage-card-track); }
[data-legacy-usage] .usage-card-bar > span { height: 100%; border-radius: 4px; }
[data-legacy-usage] .usage-card-legend { display: flex; flex-wrap: wrap; gap: 2px 7px; color: var(--usage-card-muted); }
[data-legacy-usage] .usage-card-legend span { display: flex; align-items: baseline; gap: 4px; }
[data-legacy-usage] .usage-card-legend i { align-self: center; width: 5px; height: 5px; border-radius: 50%; flex: none; }
[data-legacy-usage] .usage-card-output-legend { flex-wrap: nowrap; gap: 6px; font-size: 10px; overflow-x: auto; scrollbar-width: none; }
[data-legacy-usage] .usage-card-output-legend > span { flex: none; white-space: nowrap; }
[data-legacy-usage] .usage-card-title { overflow-wrap: anywhere; }
[data-legacy-usage] .legacy-usage-action {
  appearance: none; box-sizing: border-box; text-align: start; font: inherit; color: inherit;
  margin: 0; padding: 3px 6px; border: 0; border-radius: 5px; background: transparent; cursor: pointer;
}
[data-legacy-usage] .legacy-usage-action:hover { background: var(--usage-card-panel); }
[data-legacy-usage] .usage-card-heading > button { min-height: 24px; }
[data-legacy-usage] .usage-card-row { display: flex; width: 100%; justify-content: space-between; align-items: center; gap: 6px; min-height: 24px; }
[data-legacy-usage] .usage-card-detail { display: block; }
[data-legacy-usage] :is(.inline-pill, .legacy-usage-action):focus-visible {
  outline: 2px solid rgb(var(--usage-blue)); outline-offset: -2px;
}
@media (prefers-reduced-motion: reduce) {
  [data-legacy-usage] .inline-pill i[data-state="active"] { animation: none; }
  [data-legacy-usage] .inline-popup { transform: none; transition: none; }
}
`;

let legacyUsagePopupId = 0;

function mountInlineUsage(root, { resolveSlot, readView }) {
  const doc = root.ownerDocument;
  const win = doc.defaultView;
  const systemDark = win.matchMedia('(prefers-color-scheme: dark)');
  const reducedMotion = win.matchMedia('(prefers-reduced-motion: reduce)');
  const style = doc.createElement('style');
  style.textContent = LEGACY_WIDGET_CSS;
  doc.head.append(style);

  const widget = doc.createElement('div');
  widget.className = 'inline-usage';
  widget.setAttribute('data-legacy-usage', '');
  widget.innerHTML = '<button class="inline-pill" type="button" aria-expanded="false">' +
    '<i aria-hidden="true" hidden></i><span></span><span class="legacy-usage-measure" aria-hidden="true"></span></button>' +
    '<div class="inline-popup" role="region" hidden inert><div class="usage-card-content" inert></div></div>';
  const pill = widget.querySelector('.inline-pill');
  const dot = pill.querySelector('i');
  const [label, measure] = pill.querySelectorAll('span');
  const popup = widget.querySelector('.inline-popup');
  const controls = popup.querySelector('.usage-card-content');
  popup.id = 'legacy-usage-card-' + ++legacyUsagePopupId;
  pill.setAttribute('aria-controls', popup.id);
  const topLayer = typeof popup.showPopover === 'function';
  if (topLayer) popup.setAttribute('popover', 'manual');

  const HOVER_DELAY = 85, LEAVE_DELAY = 180, FADE_TIME = 120;
  const CARD_GAP = 6, CARD_WIDTH = 332, PAGE_SIZE = 5;
  const pageLabels = { summary: '会话用量', turns: '逐轮统计', turn: '本轮统计' };
  const dotStates = new Set(['ready', 'active', 'completed', 'red', 'amber']);
  let context = null, view = null, slot = null, read = null;
  let summaryStyle = 'classic', summaryWeight = 'normal';
  let activityEnabled = true;
  let lastUsage = null;
  let hasShownSessionSummary = false;
  let previousTurn = null, observedActiveTurn = null, measuredSpeed = null, settling = 0;
  let enabled = true, disposed = false, dark = false, summaryText = '';
  let open = false, pinned = false, suppressHover = false, overPill = false, overCard = false;
  let hoverTimer = 0, hideTimer = 0;
  let page = 'summary', offset = 0, turnId = null, opening = 0;
  let displayed = null, restoreFocus = false, positionFrame = 0;

  function syncTheme() {
    const theme = widget.closest('[data-theme="dark"], [data-theme="light"], .dark, .light');
    const scheme = win.getComputedStyle(widget).colorScheme;
    const next = context?.dark ?? (theme ? theme.dataset.theme ? theme.dataset.theme === 'dark' :
      theme.classList.contains('dark') : scheme === 'dark' || (scheme !== 'light' && systemDark.matches));
    if (dark === next) return;
    dark = next;
    widget.toggleAttribute('data-dark', dark);
    controls.inert = !open || !displayed || !sameCard(displayed);
  }

  function isSpeed(value) {
    return typeof value === 'string' && /^(?:约 )?[0-9]+ tok\/s$/.test(value);
  }

  function renderSummary() {
    if (!slot) return;
    // A cold history scan can yield request speed before context and cache.
    // Keep the slot reserved and make the first idle summary a single paint.
    if (context.id && !context.starting && !hasShownSessionSummary && (!view || view.usage_loading)) {
      pill.disabled = true;
      return;
    }
    if (context.id && view && !view.usage_loading) hasShownSessionSummary = true;
    const metrics = view?.metrics;
    const active = !context.draft && view?.turn_state === 'active';
    // The native Stop button and the final sample can arrive in either order.
    // Only a completed sample from this run can supersede a still-visible Stop button.
    const finished = view?.turn_state === 'completed' && view.turn_id &&
      (view.turn_id === observedActiveTurn || (previousTurn && view.turn_id !== previousTurn));
    const awaitingFinalSample = settling && previousTurn === view?.turn_id && !finished;
    const running = active || ((context.starting || awaitingFinalSample) && !finished);
    const reported = isSpeed(metrics?.speed) && metrics.speed_label !== '上次输出速度' ? metrics.speed : null;
    const measured = view?.turn_id && measuredSpeed?.turn === view.turn_id ? measuredSpeed.value : null;
    const waitingForSpeed = running && !(active && (reported || measured));
    const startingNewChat = waitingForSpeed && (context.newChat || (active && view.first_turn_active));
    const status = startingNewChat ? '正在启动' : waitingForSpeed ? (summaryStyle === 'english' ? 'Thinking' : '思考中') : context.draft ? '就绪' : '';
    const state = running ? 'active' : context.draft ? 'ready' :
      view?.dot_state === undefined ? view?.turn_state : view.dot_state;
    widget.toggleAttribute('data-ready', status === '就绪');
    pill.toggleAttribute('data-status', Boolean(status));
    dot.hidden = !activityEnabled || !dotStates.has(state);
    const dotState = dot.hidden ? '' : state;
    if (dot.dataset.state !== dotState) dot.dataset.state = dotState;
    pill.removeAttribute('data-narrow');

    const speed = reported || (active ? measured : null) || metrics?.speed || view?.summary_speed;
    const groups = [];
    const [contextLabel, cacheLabel, separator] = summaryStyle === 'english' ? ['Ctx ', 'Cache ', ' · '] :
      summaryStyle === 'compact' ? ['上下文', '缓存', ' / '] : ['上下文 ', '缓存 ', ' · '];
    if (!context.draft && !startingNewChat) {
      const showPlaceholders = waitingForSpeed;
      const usageContext = lastUsage?.context;
      const usageCache = lastUsage?.cache;
      if (usageContext || showPlaceholders) groups.push(contextLabel + (usageContext || '—'));
      if (usageCache || showPlaceholders) groups.push(cacheLabel + (usageCache || '—'));
    }
    let lead = status || (isSpeed(speed) ? speed : '— tok/s');
    if (summaryStyle === 'english' && lead.startsWith('约 ')) lead = '~' + lead.slice(2);
    summaryText = [lead, ...groups].join(separator);
    const { width, height } = pill.getBoundingClientRect();
    const zoom = pill.currentCSSZoom || 1;
    pill.disabled = width < 44 || height < 18;
    const available = width - ((status ? 4 : 12) + (dot.hidden ? 0 : 17)) * zoom;
    const fits = text => {
      measure.textContent = text;
      return measure.getBoundingClientRect().width <= available;
    };
    if (!fits(lead)) pill.setAttribute('data-narrow', '');
    if (!fits(lead)) lead = status === '正在启动' ? '启动' :
      lead.endsWith(' tok/s') ? lead.slice(0, -6) : lead;
    if (!fits(lead)) lead = '—';
    let line = [lead, ...groups].join(separator);
    if (groups.length && !fits(line)) pill.setAttribute('data-narrow', '');
    while (groups.length && !fits(line)) {
      groups.pop();
      line = [lead, ...groups].join(separator);
    }
    measure.textContent = '';
    if (label.textContent !== line) label.textContent = line;
    const stateName = {active: '运行中', completed: '已完成', red: '连接异常', amber: '正在重试'}[dotState];
    pill.setAttribute('aria-label', ['会话用量', stateName, summaryText].filter(Boolean).join('，'));
    if (pill.disabled && open) close(true);
  }

  function updateGeometry() {
    if (!displayed || popup.hidden || !slot) return;
    const anchor = pill.getBoundingClientRect();
    const viewportWidth = doc.documentElement.clientWidth;
    const viewportHeight = doc.documentElement.clientHeight;
    // Client rects include CSS zoom, while fixed style offsets use the card's local units.
    const zoom = popup.currentCSSZoom || 1;
    const gap = CARD_GAP * zoom;
    const w = Math.min(CARD_WIDTH * zoom, viewportWidth - 2 * gap);
    const above = anchor.top - 2 * gap, below = viewportHeight - anchor.bottom - 2 * gap;
    if (w <= 0 || Math.max(above, below) <= 0) return;
    const apply = (key, value) => {
      const css = (Math.round(value / zoom * 1000) / 1000) + 'px';
      if (popup.style[key] !== css) popup.style[key] = css;
    };
    apply('width', w);
    popup.toggleAttribute('data-compact', w / zoom < 284);
    const naturalHeight = controls.getBoundingClientRect().height + 2 * zoom;
    const putAbove = naturalHeight <= above || above >= below;
    popup.toggleAttribute('data-below', !putAbove);
    const left = Math.max(gap, Math.min((anchor.left + anchor.right - w) / 2, viewportWidth - w - gap));
    apply('maxHeight', putAbove ? above : below);
    apply('left', left);
    apply('top', putAbove ? anchor.top - gap - popup.getBoundingClientRect().height : anchor.bottom + gap);
  }

  function scheduleGeometry() {
    if (!open || popup.hidden || positionFrame) return;
    positionFrame = win.requestAnimationFrame(() => {
      positionFrame = 0;
      if (open && !popup.hidden) {
        syncTheme();
        updateGeometry();
      }
    });
  }

  function clearCard() {
    if (topLayer && popup.isConnected && popup.matches(':popover-open')) popup.hidePopover();
    popup.hidden = true;
    displayed = null;
    controls.replaceChildren();
  }

  function resetPage() {
    page = 'summary';
    offset = 0;
    turnId = null;
  }

  function close(immediate = false) {
    win.clearTimeout(hoverTimer);
    if (!open && !immediate) return;
    win.clearTimeout(hideTimer);
    open = pinned = false;
    overCard = false;
    restoreFocus = false;
    win.cancelAnimationFrame(positionFrame);
    positionFrame = 0;
    pill.setAttribute('aria-expanded', 'false');
    widget.removeAttribute('data-open');
    popup.removeAttribute('data-shown');
    popup.inert = controls.inert = true;
    if (immediate || popup.hidden || reducedMotion.matches) {
      clearCard();
      resetPage();
    } else {
      hideTimer = win.setTimeout(() => { clearCard(); resetPage(); }, FADE_TIME);
    }
  }

  function openCard() {
    if (!slot || !widget.isConnected || pill.disabled || !pill.getClientRects().length) return;
    win.clearTimeout(hideTimer);
    if (!open) opening++;
    open = true;
    syncTheme();
    pill.setAttribute('aria-expanded', 'true');
    widget.setAttribute('data-open', '');
    if (displayed) showCard();
  }

  function leaveCard() {
    win.clearTimeout(hoverTimer);
    if (open && !pinned && !overPill && !overCard && !popup.contains(doc.activeElement)) {
      hoverTimer = win.setTimeout(() => close(), LEAVE_DELAY);
    }
  }

  function layout() {
    if (disposed || !slot) return;
    syncTheme();
    renderSummary();
    updateGeometry();
  }

  const resize = new win.ResizeObserver(layout);
  const cardResize = new win.ResizeObserver(scheduleGeometry);
  cardResize.observe(popup);
  cardResize.observe(controls);
  function positionInToolbar() {
    const next = enabled && context && resolveSlot(root, context);
    const target = next?.isConnected && root.contains(next) ? next : null;
    if (target === slot && (!slot || widget.parentElement === slot)) return;
    resize.disconnect();
    if (!target) {
      close(true);
      overPill = suppressHover = false;
      widget.remove();
    } else {
      target.append(widget);
      resize.observe(pill);
      resize.observe(target);
    }
    slot = target;
    layout();
    if (open && displayed) showCard();
  }

  function changePage(next, nextOffset = 0, nextTurn = null) {
    if (page === next && offset === nextOffset && turnId === nextTurn) return;
    restoreFocus ||= controls.contains(doc.activeElement);
    page = next;
    offset = nextOffset;
    turnId = nextTurn;
    controls.inert = true;
    if (displayed && sameCard(displayed)) showCard();
  }

  function lastOffset() {
    const count = view?.turns_count ?? view?.turns?.length ?? 0;
    return Number.isSafeInteger(count) ? Math.max(0, count - PAGE_SIZE) : 0;
  }

  function scrollTurns(direction) {
    if (open && page === 'turns' && direction) {
      changePage('turns', Math.max(0, Math.min(lastOffset(), offset + direction)));
    }
  }

  function acceptView(next) {
    // A read that has not found the turn yet must not erase an already rendered frame.
    if (view?.turn_id && !next.turn_id && (context.starting || view.turn_state !== 'active')) return;
    if (view?.turn_id && next.turn_id === view.turn_id && view.turn_state === 'completed' && next.turn_state === 'active') return;
    view = next;
    if (next.turn_state === 'active' && next.turn_id) {
      observedActiveTurn = next.turn_id;
      if (isSpeed(next.metrics?.speed) && next.metrics.speed_label !== '上次输出速度') {
        measuredSpeed = {turn: next.turn_id, value: next.metrics.speed};
      }
    }
    for (const key of ['context', 'cache']) {
      const value = next.metrics?.[key];
      if (value && value !== '—') (lastUsage ??= {})[key] = value;
    }
    if (page === 'turns' && offset > lastOffset()) changePage('turns', lastOffset());
    if (page === 'turn' && (view.turns_count === 0 || (Array.isArray(view.turn_ids) && !view.turn_ids.includes(turnId)) ||
      (Array.isArray(view.turns) && (view.turns_count ?? view.turns.length) === view.turns.length &&
       !view.turns.some(row => row.id === turnId)))) changePage('turns');
    layout();
  }

  function readContext() {
    read?.abort();
    if (!enabled || !context?.id || context.draft || typeof readView !== 'function') return;
    const id = context.id;
    const current = read = new win.AbortController();
    Promise.resolve().then(() => {
      if (!current.signal.aborted) return readView(id, current.signal);
    }).then(next => {
      if (!current.signal.aborted && next?.session === id) acceptView(next);
    }).catch(() => { /* Keep the last verified view for this chat. */ });
  }

  function cardRequest() {
    if (disposed || !open || !slot || !widget.isConnected || pill.disabled ||
      !pill.getClientRects().length || !pill.checkVisibility({ visibilityProperty: true })) return null;
    syncTheme();
    return { page, offset, turnId, opening };
  }

  function sameCard(result) {
    return result.page === page && result.offset === offset && (result.turnId ?? null) === turnId;
  }

  function node(tag, className, label) {
    const element = doc.createElement(tag);
    if (className) element.className = className;
    if (label != null) element.textContent = label;
    return element;
  }

  function cardButton(label, target, id = null) {
    const button = node('button', 'legacy-usage-action', label);
    button.type = 'button';
    button.dataset.action = JSON.stringify([target, id]);
    button.addEventListener('pointerdown', event => { if (event.pointerType === 'mouse') event.preventDefault(); });
    button.addEventListener('click', () => { if (!controls.inert) changePage(target, 0, id); });
    return button;
  }

  function pair(label, value, subtle = false) {
    const row = node('div', 'usage-card-pair');
    row.append(node(subtle ? 'span' : 'strong', 'usage-card-title', label), node('strong', null, value));
    return row;
  }

  const countFormat = new Intl.NumberFormat('en-US', {maximumFractionDigits: 0});
  const kiloFormat = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
  const validNumber = value => Number.isFinite(value) && value >= 0;
  function amount(value, short = false) {
    if (!validNumber(value)) return '—';
    if (short) {
      for (const [size, suffix] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'k']]) {
        if (value >= size || (size > 1e3 && value >= size - size / 20000)) {
          return Number((value / size).toFixed(1)) + suffix;
        }
      }
    }
    return countFormat.format(value);
  }
  const inThousands = value => validNumber(value) && value >= 1000 ? kiloFormat.format(value / 1000) + 'K' : amount(value);
  const duration = value => validNumber(value) ? (value / 1000).toFixed(1) + 's' : '—';

  function metricBlock(label, value, total, entries, fallback = 0) {
    const section = node('div', 'usage-card-block');
    const bar = node('div', 'usage-card-bar');
    bar.setAttribute('aria-hidden', 'true');
    const legend = node('div', 'usage-card-legend');
    const plot = entries.every(([, value]) => validNumber(value)) &&
      entries.reduce((sum, [, value]) => sum + value, 0) <= total && total > 0;
    for (const [text, value, color] of entries) {
      const item = node('span');
      const dot = node('i');
      dot.style.backgroundColor = color;
      dot.setAttribute('aria-hidden', 'true');
      item.append(dot, doc.createTextNode(text));
      legend.append(item);
      if (!plot || value === 0) continue;
      const segment = node('span');
      segment.style.width = (value / total * 100) + '%';
      segment.style.backgroundColor = color;
      bar.append(segment);
    }
    if (!plot && fallback > 0) {
      const segment = node('span');
      segment.style.width = (Math.min(1, fallback) * 100) + '%';
      segment.style.backgroundColor = 'var(--usage-card-blue)';
      bar.append(segment);
    }
    section.append(pair(label, value), bar, legend);
    return section;
  }

  function summaryCard(result) {
    const metrics = result.metrics, d = metrics.detail;
    const blue = '#2b7fff', pale = '#93c5fd', slate = '#94a3b8', violet = '#6366f1', green = '#059669';
    const remaining = validNumber(d.window) && validNumber(d.used) ? Math.max(0, d.window - d.used) : null;
    const speed = validNumber(d.speed_value) ? d.speed_value.toFixed(1) + ' tok/s' : metrics.speed;
    const kpis = node('div', 'usage-card-kpis');
    for (const [label, value, extra, tone] of [
      [metrics.speed_label || '生成速度', speed, validNumber(d.first_ms) ? '首字 ' + duration(d.first_ms) : metrics.timing, 'ink'],
      ['上下文窗口', metrics.context, '余 ' + amount(remaining, true) + ' / ' + amount(d.window, true), 'blue'],
      ['累计缓存命中', metrics.cache, metrics.last_cache.replace('最近调用 ', '本步 '), 'green'],
    ]) {
      const cell = node('div', 'usage-card-kpi');
      const hasUnit = value.endsWith(' tok/s');
      const number = node('strong', 'usage-card-num', hasUnit ? value.slice(0, -6) + ' ' : value);
      number.dataset.tone = tone;
      if (hasUnit) number.append(node('small', null, 't/s'));
      cell.append(node('div', 'usage-card-muted', label), number, node('div', 'usage-card-muted', extra));
      kpis.append(cell);
    }
    const [newContext, cachedContext] = d.context_input;
    const context = metricBlock('当前上下文', inThousands(d.used) + ' / ' + amount(d.window, true), d.window,
      [['新增 ' + amount(newContext), newContext, blue], ['缓存 ' + inThousands(cachedContext), cachedContext, pale]], metrics.context_ratio);
    const contextLegend = context.lastElementChild;
    const remainingLabel = node('span', null, '余 ' + amount(remaining, true));
    contextLegend.append(remainingLabel);
    const split = node('div', 'usage-card-section usage-card-split');
    const [stepText, stepThought] = d.step_split;
    const output = metricBlock('本步输出', amount(d.step_output), d.step_output,
      [['思考 ' + amount(stepThought, true), stepThought, violet], ['正文/工具 ' + amount(stepText, true), stepText, blue]]);
    output.lastElementChild.classList.add('usage-card-output-legend');
    split.append(metricBlock('响应耗时', duration(d.duration_ms), d.duration_ms,
      [['首字 ' + duration(d.first_ms), d.first_ms, slate], ['流式 ' + duration(d.stream_ms), d.stream_ms, blue]]),
      output);
    const cumulative = node('div', 'usage-card-section');
    const heading = node('div', 'usage-card-pair usage-card-muted');
    heading.append(node('strong', null, '当前会话累计'));
    if (result.turnCount) heading.append(node('span', 'usage-card-chip',
      (result.turnCount >= 25 ? '最近 ' : '') + result.turnCount + ' 轮有记录'));
    const [fresh, cached] = d.input_split, [outputText, outputThought] = d.output_split;
    cumulative.append(heading,
      metricBlock('累计输入量', '总计 ' + amount(d.input_total, true) + ' · 命中率 ' + metrics.cache, d.input_total,
        [['新增 ' + amount(fresh, true), fresh, '#cbd5e1'], ['缓存读取 ' + amount(cached, true), cached, green]]),
      metricBlock('累计输出量', '总计 ' + amount(d.output_total, true), d.output_total,
        [['思考链 ' + amount(outputThought, true), outputThought, violet], ['正文/工具 ' + amount(outputText, true), outputText, blue]]));
    return [kpis, context, split, cumulative];
  }

  function renderCard(result) {
    const content = doc.createDocumentFragment();
    const heading = node('div', 'usage-card-heading');
    heading.append(node('strong', null, result.metrics ? '本轮最新调用' : pageLabels[result.page]));
    if (result.page !== 'summary') {
      heading.append(cardButton('返回', result.page === 'turn' ? 'turns' : 'summary'));
    } else if (result.running) {
      heading.append(node('span', 'usage-card-chip usage-card-muted', '运行中'));
    }
    content.append(heading);
    if (result.page === 'turns') {
      content.append(node('div', 'usage-card-muted usage-card-title', result.title));
      for (const row of result.rows) {
        const button = cardButton(null, 'turn', row.id);
        button.classList.add('usage-card-row');
        const labels = node('span', 'usage-card-title');
        labels.append(node('strong', null, row.title), node('small', 'usage-card-muted usage-card-detail', row.detail));
        button.append(labels, node('span', 'usage-card-muted', '›'));
        content.append(button);
      }
      if (!result.rows.length) content.append(node('div', 'usage-card-muted', '暂无可查看记录'));
      if (result.turnCount > PAGE_SIZE) content.append(node('div', 'usage-card-muted',
        (result.offset + 1) + '–' + (result.offset + result.rows.length) + ' / ' + result.turnCount + ' · 滚轮翻阅'));
    } else if (result.page === 'turn' && result.turn) {
      content.append(node('strong', null, result.turn.title), node('div', 'usage-card-muted', result.turn.detail));
      const rows = node('div', 'usage-card-section');
      for (const [label, value] of result.turn.metrics) rows.append(pair(label, value, true));
      content.append(rows);
    } else if (result.metrics?.detail) {
      content.append(...summaryCard(result));
      if (result.turnCount) {
        const button = cardButton('查看逐轮统计', 'turns');
        button.classList.add('usage-card-row');
        button.append(node('span', 'usage-card-muted', '›'));
        content.append(button);
      }
    } else {
      content.append(node('strong', 'usage-card-title', result.title),
        node('div', 'usage-card-muted usage-card-block', result.message || '正在读取当前会话用量'));
    }
    return content;
  }

  function showCard() {
    const wasHidden = popup.hidden;
    popup.hidden = false;
    if (topLayer && !popup.matches(':popover-open')) popup.showPopover();
    updateGeometry();
    if (!open) return;
    if (wasHidden) popup.getBoundingClientRect();
    const current = sameCard(displayed);
    popup.inert = false;
    controls.inert = !current;
    popup.setAttribute('data-shown', '');
    scheduleGeometry();
    if (current && restoreFocus) {
      restoreFocus = false;
      (controls.querySelector('button') || pill).focus({ preventScroll: true });
    }
  }

  function setCard(result) {
    if (!cardRequest() || !result || !sameCard(result)) return;
    if (result.expired && page === 'turn') { changePage('turns'); return; }
    const key = JSON.stringify(result);
    if (displayed?.key === key) return;
    const previousPage = displayed?.page;
    const focused = controls.contains(doc.activeElement) ? doc.activeElement?.dataset.action : null;
    const scrollTop = popup.scrollTop;
    controls.replaceChildren(renderCard(result));
    const match = focused && [...controls.querySelectorAll('button')].find(button => button.dataset.action === focused);
    restoreFocus ||= Boolean(focused && !match);
    displayed = { ...result, key };
    popup.setAttribute('aria-label', pageLabels[page]);
    showCard();
    popup.scrollTop = previousPage === result.page ? scrollTop : 0;
    if (match) match.focus({ preventScroll: true });
  }

  pill.addEventListener('pointerenter', event => {
    if (event.pointerType === 'touch') return;
    overPill = true;
    win.clearTimeout(hoverTimer);
    if (suppressHover || pinned) return;
    if (open || displayed) openCard();
    else hoverTimer = win.setTimeout(openCard, HOVER_DELAY);
  });
  pill.addEventListener('pointerleave', () => {
    overPill = suppressHover = false;
    leaveCard();
  });
  pill.addEventListener('pointerdown', event => { if (event.pointerType === 'mouse') event.preventDefault(); });
  pill.addEventListener('click', () => {
    win.clearTimeout(hoverTimer);
    if (pinned) {
      suppressHover = overPill;
      close();
    } else {
      suppressHover = false;
      openCard();
      pinned = open;
    }
  });
  popup.addEventListener('pointerenter', () => { overCard = true; win.clearTimeout(hoverTimer); });
  popup.addEventListener('pointerleave', () => { overCard = false; leaveCard(); });
  popup.addEventListener('focusout', () => win.queueMicrotask(leaveCard));
  popup.addEventListener('wheel', event => {
    if (!open || page !== 'turns' || !event.deltaY || event.ctrlKey) return;
    const remaining = popup.scrollHeight - popup.clientHeight - popup.scrollTop;
    if (event.deltaY > 0 ? remaining > 1 : popup.scrollTop > 1) return;
    event.preventDefault();
    scrollTurns(Math.sign(event.deltaY));
  }, { passive: false });
  function escapeCard(event) {
    if (event.key !== 'Escape' || event.isComposing || !open) return;
    if (popup.hidden) { suppressHover = overPill; close(true); return; }
    event.preventDefault();
    event.stopPropagation();
    if (popup.contains(doc.activeElement)) pill.focus({ preventScroll: true });
    suppressHover = overPill;
    close();
  }
  function dismissOutside(event) {
    if (!open || event.composedPath().includes(widget)) return;
    suppressHover = overPill;
    close();
  }
  doc.addEventListener('keydown', escapeCard, true);
  doc.addEventListener('click', dismissOutside, true);
  widget.addEventListener('keydown', event => {
    if (open && page === 'turns' && popup.contains(event.target) && ['PageUp', 'PageDown'].includes(event.key)) {
      event.preventDefault();
      scrollTurns(event.key === 'PageUp' ? -1 : 1);
    }
  });

  const observer = new win.MutationObserver(records => {
    if (!records.some(record => !widget.contains(record.target) && record.target !== style)) return;
    positionInToolbar();
    scheduleGeometry();
  });
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'data-theme', 'hidden'] });
  if (root === doc.body) observer.observe(doc.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
  systemDark.addEventListener('change', layout);
  win.addEventListener('resize', layout);
  doc.addEventListener('scroll', scheduleGeometry, { capture: true, passive: true });
  doc.fonts?.ready.then(layout);
  doc.fonts?.addEventListener('loadingdone', layout);

  return {
    setAppearance(next) {
      const style = ['classic', 'english', 'compact', 'pill'].includes(next?.composerStyle) ? next.composerStyle : 'classic';
      const weight = next?.composerWeight === 'bold' ? 'bold' : 'normal';
      const indicators = next?.activityEnabled !== false;
      if (disposed || (style === summaryStyle && weight === summaryWeight && activityEnabled === indicators)) return;
      summaryStyle = widget.dataset.summaryStyle = style;
      summaryWeight = widget.dataset.summaryWeight = weight;
      activityEnabled = indicators;
      layout();
    },
    setContext(next) {
      if (disposed) return;
      const sameChat = context && next && (context.id ?? null) === (next.id ?? null) &&
        (next.id || (context.draft === next.draft && context.starting === next.starting));
      if (!sameChat || next.starting !== context.starting) {
        win.clearTimeout(settling);
        settling = 0;
      }
      if (!sameChat) {
        close(true);
        overPill = suppressHover = false;
        view = null;
        lastUsage = null;
        hasShownSessionSummary = false;
        previousTurn = observedActiveTurn = measuredSpeed = null;
      } else if (next.starting && !context.starting) {
        previousTurn = view?.turn_state === 'completed' ? view.turn_id : null;
        observedActiveTurn = null;
        measuredSpeed = null;
      } else if (!next.starting && context.starting && previousTurn && view?.turn_id === previousTurn) {
        // A short turn can finish between samples; avoid briefly showing its predecessor.
        settling = win.setTimeout(() => { settling = 0; layout(); }, 1000);
      }
      context = next ? { ...next } : null;
      positionInToolbar();
      layout();
      readContext();
    },
    setEnabled(next) {
      if (disposed || enabled === Boolean(next)) return;
      enabled = Boolean(next);
      positionInToolbar();
      readContext();
    },
    updateView(next) {
      if (disposed || !enabled || !context?.id || context.draft || next?.session !== context.id) return;
      read?.abort();
      acceptView(next);
    },
    cardRequest,
    setCard,
    dispose() {
      if (disposed) return;
      disposed = true;
      win.clearTimeout(settling);
      close(true);
      read?.abort();
      observer.disconnect();
      resize.disconnect();
      cardResize.disconnect();
      systemDark.removeEventListener('change', layout);
      win.removeEventListener('resize', layout);
      doc.removeEventListener('scroll', scheduleGeometry, true);
      doc.removeEventListener('keydown', escapeCard, true);
      doc.removeEventListener('click', dismissOutside, true);
      doc.fonts?.removeEventListener('loadingdone', layout);
      widget.remove();
      style.remove();
    },
  };
}
