/* The host provides the current route, the main toolbar slot, and exact-ID data. */
function mountInlineUsage(root, { resolveSlot, readView }) {
  const doc = root.ownerDocument;
  const widget = doc.createElement('div');
  widget.className = 'inline-usage';
  widget.innerHTML = '<button class="inline-pill" type="button" aria-expanded="false"><i aria-hidden="true"></i><span></span></button>' +
    '<div class="inline-popup" inert><section aria-label="会话用量"><strong class="inline-title">会话用量</strong><div class="inline-details"></div></section></div>';
  const pill = widget.querySelector('button');
  const dot = pill.querySelector('i');
  const label = pill.querySelector('span');
  const popup = widget.querySelector('.inline-popup');
  const details = widget.querySelector('.inline-details');
  let enabled = true, context = null, request, disposed = false;
  let lastView = null;

  function positionInToolbar() {
    const slot = enabled && context && resolveSlot(root, context);
    if (slot && root.contains(slot) && slot.isConnected) {
      if (widget.parentElement !== slot) slot.append(widget);
    } else if (widget.parentElement) {
      show(false);
      widget.remove();
    }
  }

  function show(open) {
    pill.setAttribute('aria-expanded', String(open));
    popup.inert = !open;
    widget.toggleAttribute('data-open', open);
  }

  function render(view, fallback = '读取中…') {
    const m = view?.metrics;
    const valid = v => typeof v === 'string' && v !== '' && v !== '—';
    const speed = m?.speed || view?.summary_speed;
    const active = view?.turn_state === 'active' || (context?.starting && view?.turn_state !== 'completed');
    const waitingForSpeed = active && (view?.turn_state !== 'active' || !valid(speed) || m?.speed_label === '上次输出速度');
    const starting = waitingForSpeed && (context?.newChat || view?.first_turn_active);
    const fields = [waitingForSpeed ? '思考中' : speed,
      (waitingForSpeed || valid(m?.context)) && ('上下文 ' + (m?.context || '—')),
      (waitingForSpeed || valid(m?.cache)) && ('缓存 ' + (m?.cache || '—'))];
    label.textContent = starting ? '正在启动' : context?.draft ? '就绪' : fields.filter(valid).join(' · ') || fallback;
    dot.dataset.state = active ? 'active' : context?.draft ? 'ready' : view?.turn_state || 'idle';
    const rows = doc.createDocumentFragment();
    if (m) {
      const metrics = doc.createElement('div');
      metrics.className = 'inline-metrics';
      for (const [name, value] of [['生成速度', speed], ['上下文窗口', m.context], ['累计缓存命中', m.cache]]) {
        const cell = doc.createElement('div');
        const small = doc.createElement('small'); small.textContent = name;
        const number = doc.createElement('strong'); number.textContent = value || '—';
        cell.append(small, number); metrics.append(cell);
      }
      rows.append(metrics);
      const extras = ['timing', 'context_total', 'last_cache', 'total_input', 'total_output'];
      const names = ['响应', '当前上下文', '缓存', '累计输入', '累计输出'];
      extras.forEach((key, i) => {
        if (!valid(m[key])) return;
        const row = doc.createElement('div'); row.className = 'inline-detail';
        const name = doc.createElement('span'); name.textContent = names[i];
        const value = doc.createElement('strong'); value.textContent = m[key];
        row.append(name, value); rows.append(row);
      });
    } else {
      const text = doc.createElement('p');
      text.textContent = context?.draft ? '第一轮开始后，这里会显示用量和速度。' : label.textContent;
      rows.append(text);
    }
    details.replaceChildren(rows);
  }

  function refresh() {
    request?.abort();
    request = null;
    positionInToolbar();
    if (!enabled || !context) return;
    const target = context;
    render(lastView);
    if (target.draft || !target.id) {
      if (!target.draft && !target.starting) render(null, '暂无用量');
      return;
    }
    const current = request = new AbortController();
    Promise.resolve().then(() => readView(target.id, current.signal)).then(view => {
      if (disposed || current.signal.aborted || context !== target) return;
      if (view?.session === target.id) lastView = view;
      render(lastView, '暂无用量');
    }).catch(() => {
      if (!disposed && !current.signal.aborted && context === target) render(lastView, '暂无用量');
    });
  }

  let pinned = false, timer;
  function after(ms, open) { clearTimeout(timer); timer = setTimeout(() => show(open), ms); }
  widget.addEventListener('pointerenter', () => { if (!pinned) after(85, true); });
  widget.addEventListener('pointerleave', () => { if (!pinned) after(180, false); });
  function resetPopup() { clearTimeout(timer); pinned = false; show(false); }
  pill.addEventListener('click', () => { clearTimeout(timer); pinned = !pinned; show(pinned); });
  widget.addEventListener('keydown', e => { if (e.key === 'Escape') resetPopup(); });

  const observer = new MutationObserver(records => {
    if (records.some(record => !widget.contains(record.target))) positionInToolbar();
  });
  observer.observe(root, { childList: true, subtree: true });
  return {
    setContext(next) {
      if (disposed) return;
      if (!next?.id || next.id !== context?.id) { resetPopup(); lastView = null; }
      context = next && { ...next };
      refresh();
    },
    setEnabled(next) { if (!disposed) { resetPopup(); enabled = Boolean(next); refresh(); } },
    updateView(view) {
      if (!disposed && enabled && context?.id && view?.session === context.id) {
        request?.abort();
        lastView = view;
        render(lastView, '暂无用量');
      }
    },
    dispose() { disposed = true; resetPopup(); request?.abort(); observer.disconnect(); widget.remove(); },
  };
}
