// Adapter for the opt-in main-profile validation. Nothing is installed persistently.
(() => {
  if (location.protocol !== 'app:' || location.host !== '-' || !document.body) return;
  window.__codexInlineUsageTest?.dispose();
  const root = document.body;
  const style = document.createElement('style');
  style.textContent = '[data-codex-inline-slot]{display:grid;flex:1 1 0;min-width:0;grid-template-columns:minmax(0,1fr);place-items:center}'
    + '[data-codex-inline-slot]:empty{display:none}';
  document.head.append(style);
  let slot, state, route, value = null, enabled = true, frame, generation = 0;
  const visible = node => Boolean(node?.isConnected && node.getClientRects().length
    && node.checkVisibility({visibilityProperty: true}) && !node.closest('[inert],[aria-hidden="true"],[data-app-shell-active-page="false"]'));
  const inline = mountInlineUsage(root, {resolveSlot: () => slot, readView: id => value?.session === id ? value : null});
  const threadIdAttr = 'data-app-action-sidebar-thread-id';
  const activeAttr = 'data-app-action-sidebar-thread-active';
  const kindAttr = 'data-app-action-sidebar-thread-kind';
  const isUuid = value => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);

  function composerIdentity(body) {
    const key = Object.keys(body).find(name => name.startsWith('__reactFiber$'));
    const fiber = key && body[key];
    if (fiber?.stateNode !== body) return null;
    // Read only the committed branch for this input. The alternate can still
    // hold the previous chat while React is switching the main surface.
    for (const start of [fiber, fiber.alternate]) {
      if (start?.stateNode !== body) continue;
      let rootFiber, composer;
      for (let node = start, steps = 0; node && steps < 180; node = node.return, steps++) {
        const props = node.memoizedProps;
        if (!composer && props && Object.hasOwn(props, 'conversationId')
            && typeof props.renderComposer === 'function' && typeof props.isThreadHistoryLoading === 'boolean'
            && Object.hasOwn(props, 'composerSubmitDisabled')) composer = props;
        if (!node.return) rootFiber = node;
      }
      if (composer && rootFiber?.stateNode?.current === rootFiber) {
        const id = composer.conversationId;
        return {id: composer.hostId === 'local' && isUuid(id) ? id : null, source: 'composer'};
      }
    }
    return null;
  }

  function currentId() {
    const parse = path => /^(?:#)?[/]local[/]([^/?#]+)(?:[/?#]|$)/i.exec(path)?.[1];
    const routeId = parse(location.hash) || parse(location.pathname);
    // The desktop shell can switch its local route without changing window.location.
    const activeRows = [...root.querySelectorAll(
      `[data-app-action-sidebar-thread-row][${activeAttr}="true"][${kindAttr}="local"][${threadIdAttr}]`
    )].filter(row => !row.closest('[data-app-shell-active-page="false"]'));
    const activeIds = new Set(activeRows.map(row => row.getAttribute(threadIdAttr)).filter(isUuid));
    if (activeRows.length) {
      if (activeIds.size !== 1) return null;
      const activeId = activeIds.values().next().value;
      return !routeId || activeId === routeId ? activeId : null;
    }
    if (isUuid(routeId)) return routeId;
    const rows = [...root.querySelectorAll('a[aria-current="page"][href^="/local/"], [aria-current="page"].sidebar-item a[href^="/local/"]')].filter(visible);
    const ids = new Set(rows.map(row => parse(row.getAttribute('href'))).filter(isUuid));
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
      candidates.push({body, right, home, rect: body.getBoundingClientRect()});
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
      if (state) { state = null; route = null; value = null; generation++; inline.setContext(null); }
      return;
    }
    const native = hit.home ? null : composerIdentity(hit.body);
    const id = hit.home ? null : native ? native.id : currentId();
    const running = [...hit.right.querySelectorAll('button')].some(button => visible(button)
      && /^(stop( generation)?|停止(生成|回复|回覆)?|停止產生)$/i.test(button.getAttribute('aria-label') || ''));
    const next = {id, draft: hit.home && !running, newChat: hit.home, starting: running,
      identitySource: hit.home ? 'home' : native?.source ?? (id ? 'route-or-sidebar' : 'unresolved')};
    const key = JSON.stringify(next);
    const changedSlot = slot?.parentElement !== host;
    if (changedSlot) {
      if (!slot) {
        slot = document.createElement('div'); slot.setAttribute('data-codex-inline-slot', '');
      }
      host.prepend(slot);
    }
    if (key !== route || changedSlot) {
      if (state?.id !== id || state?.draft !== next.draft) { generation++; value = null; }
      route = key; state = next; inline.setContext(state);
    }
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
    attributeFilter: ['aria-label','aria-current','aria-hidden','inert','data-app-shell-active-page',
      'data-composer-layout','data-composer-rows',activeAttr,kindAttr,threadIdAttr]});
  for (const event of ['hashchange','popstate','resize']) window.addEventListener(event, schedule);
  window.navigation?.addEventListener('currententrychange', schedule);
  window.__codexInlineUsageTest = {
    status: () => ({mounted: Boolean(slot?.querySelector('.inline-pill')), id: state?.id, draft: state?.draft,
      identitySource: state?.identitySource, usageReady: Boolean(value?.metrics),
      running: state?.starting, composerCount: root.querySelectorAll('[data-composer-body]').length,
      geometry: geometry(), card: inline.cardRequest(), generation}),
    setView(next) {
      if (!state?.id || next?.session !== state.id || JSON.stringify(next) === JSON.stringify(value)) return;
      value = next; inline.updateView(value);
    },
    setAppearance(next) { inline.setAppearance(next); },
    setCard(result) {
      if (state && result.generation === generation && (result.session ?? null) === (state.id ?? null)) {
        inline.setCard(result.card);
      }
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
