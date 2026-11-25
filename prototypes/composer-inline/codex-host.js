// Adapter for the isolated Codex test profile. Nothing is installed persistently.
(() => {
  if (location.protocol !== 'app:' || location.host !== '-' || !document.body) return;
  window.__codexInlineUsageTest?.dispose();
  const root = document.body;
  const style = document.createElement('style');
  style.textContent = INLINE_TEST_CSS
    + '[data-codex-inline-slot]{display:grid;flex:1 1 0;min-width:0;grid-template-columns:minmax(0,1fr);place-items:center}'
    + '[data-codex-inline-slot]:empty{display:none} [data-codex-inline-slot] .inline-popup{z-index:100}';
  document.head.append(style);
  let slot, state, route, value = null, enabled = true, frame;
  const visible = node => Boolean(node?.isConnected && node.getClientRects().length
    && node.checkVisibility({visibilityProperty: true}) && !node.closest('[inert],[aria-hidden="true"],[data-app-shell-active-page="false"]'));
  const inline = mountInlineUsage(root, {resolveSlot: () => slot, readView: id => value?.session === id ? value : null});

  function currentId() {
    const parse = path => /^(?:#)?[/]local[/]([0-9a-f-]{36})(?:[/?#]|$)/i.exec(path)?.[1];
    const routeId = parse(location.hash) || parse(location.pathname);
    if (routeId) return routeId;
    const rows = [...root.querySelectorAll('a[aria-current="page"][href^="/local/"], [aria-current="page"].sidebar-item a[href^="/local/"]')].filter(visible);
    const ids = new Set(rows.map(row => parse(row.getAttribute('href'))).filter(Boolean));
    return ids.size === 1 ? ids.values().next().value : null;
  }

  function findGapHost(right) {
    // The native row already reserves the gap inside the growing model group.
    // Share that group instead of competing for width with the whole native row.
    for (const row of right.children) {
      const [group, actions] = row.children;
      if (!visible(group) || !visible(actions)) continue;
      const css = getComputedStyle(group);
      if (css.display === 'flex' && Number(css.flexGrow) > 0
          && getComputedStyle(row).display === 'flex') return group;
    }
    return null;
  }

  function findMain() {
    const candidates = [];
    for (const body of root.querySelectorAll('[data-composer-body]')) {
      if (!visible(body) || !body.querySelector('.ProseMirror[contenteditable]')
          || body.closest('[data-floating-chat-surface], [data-composer-floating-surface="true"], [role="dialog"]')) continue;
      const home = Boolean(body.closest('[class~="group/home-composer-layout"], [class~="home-composer-anchor"], [class~="sm:home-composer-centered"]'));
      const footer = body.closest('[data-thread-scroll-footer="true"]');
      if (!home && (!footer || !footer.closest('[data-app-shell-active-page="true"]'))) continue;
      const right = body.querySelector('[data-composer-trailing-controls]');
      const left = body.querySelector('[data-composer-leading-controls]');
      if (!visible(right) || !visible(left) || right.parentElement !== left.parentElement) continue;
      const layout = right.parentElement;
      if (layout.dataset.composerLayout !== 'multiline' && !['stacked','responsive'].includes(layout.dataset.composerRows)) continue;
      if (Math.abs(left.getBoundingClientRect().bottom - right.getBoundingClientRect().bottom) > 12) continue;
      candidates.push({right, home, rect: body.getBoundingClientRect()});
    }
    candidates.sort((a, b) => a.rect.left - b.rect.left);
    const first = candidates[0];
    return first && candidates.slice(1).every(other => other.rect.left >= first.rect.right - 1) ? first : null;
  }

  function clearSlot() {
    slot?.remove(); slot = null;
  }

  function sync() {
    frame = 0;
    const hit = enabled && findMain();
    const host = hit && findGapHost(hit.right);
    if (!host) {
      clearSlot();
      if (state) { state = null; route = null; inline.setContext(null); }
      return;
    }
    const id = hit.home ? null : currentId();
    const running = [...hit.right.querySelectorAll('button')].some(button => visible(button)
      && /^(stop( generation)?|停止(生成|回复|回覆)?|停止產生)$/i.test(button.getAttribute('aria-label') || ''));
    const next = {id, draft: hit.home && !running, newChat: hit.home, starting: running};
    const key = JSON.stringify(next);
    const changedSlot = slot?.parentElement !== host;
    if (changedSlot) {
      clearSlot();
      slot = document.createElement('div'); slot.setAttribute('data-codex-inline-slot', ''); host.prepend(slot);
    }
    if (key !== route || changedSlot) { route = key; state = next; inline.setContext(state); }
  }

  function schedule() { if (!frame) frame = requestAnimationFrame(sync); }
  function geometry() {
    const pill = slot?.querySelector('.inline-pill');
    if (!pill) return;
    const gap = slot.getBoundingClientRect(), rect = pill.getBoundingClientRect();
    const label = pill.querySelector('span');
    return {slotWidth: Math.round(gap.width), pillWidth: Math.round(rect.width),
      centerOffset: Math.round(rect.x + rect.width / 2 - gap.x - gap.width / 2),
      labelClipped: label.scrollWidth > label.clientWidth};
  }
  const observer = new MutationObserver(records => {
    if (records.some(record => !record.target.closest?.('[data-codex-inline-slot]'))) schedule();
  });
  observer.observe(root, {subtree: true, childList: true, attributes: true,
    attributeFilter: ['aria-label','aria-current','aria-hidden','inert','data-app-shell-active-page','data-composer-layout','data-composer-rows']});
  for (const event of ['hashchange','popstate','resize']) window.addEventListener(event, schedule);
  window.navigation?.addEventListener('currententrychange', schedule);
  window.__codexInlineUsageTest = {
    status: () => ({mounted: Boolean(slot?.querySelector('.inline-pill')), id: state?.id, draft: state?.draft,
      running: state?.starting, composerCount: root.querySelectorAll('[data-composer-body]').length, geometry: geometry()}),
    setView(next) {
      if (!state?.id || next?.session !== state.id || JSON.stringify(next) === JSON.stringify(value)) return;
      value = next; inline.updateView(value);
    },
    setEnabled(next) { enabled = Boolean(next); sync(); },
    dispose() {
      enabled = false; observer.disconnect(); cancelAnimationFrame(frame); inline.dispose(); clearSlot(); style.remove();
      for (const event of ['hashchange','popstate','resize']) window.removeEventListener(event, schedule);
      window.navigation?.removeEventListener('currententrychange', schedule);
      delete window.__codexInlineUsageTest;
    },
  };
  sync();
})();
