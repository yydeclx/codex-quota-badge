"""Opt-in Chromium checks: python composer-inline-validation/test_inline_ui.py."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from playwright.sync_api import sync_playwright

from usage_bridge import UsageBridge
from session_usage_view import summary_metrics


HERE = Path(__file__).resolve().parent
EDGE = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'


def sample_view():
    current = dict(input_tokens=181953, cached_input_tokens=178816, output_tokens=154,
                   reasoning_output_tokens=0, total_tokens=182107)
    total = dict(input_tokens=684000000, cached_input_tokens=596800000, output_tokens=1200000,
                 reasoning_output_tokens=272100)
    timing = dict(speed='57 tok/s', speed_value=56.8, duration_ms=8210, first_ms=4730,
                  stream_ms=2710, first='4.73s')
    turns = [dict(id=str(i), title=f'11/24 18:{i:02} · 已完成', detail='输出 1.2k · 57 tok/s',
                  metrics=[('输入 Token', '32,212')]) for i in range(7)]
    return dict(session='sample', title='测试会话', turn_id='0', turn_state='completed',
                turns=turns, turns_count=len(turns),
                metrics=summary_metrics(current, total, {}, timing, 570000, 182107))


@unittest.skipUnless(sys.platform == 'win32' and EDGE.is_file(), 'Requires installed Windows Edge')
class InlineBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 960, 'height': 700}, device_scale_factor=1.75,
                                          reduced_motion='reduce')
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.set_content('''<!doctype html><style>body{margin:0}#frame{position:absolute;
            bottom:44px;left:50%;width:256px;margin-left:-128px}</style><div id="frame"><div id="slot"></div></div>
            <button id="outside" type="button">outside</button>''')
        self.page.add_script_tag(path=HERE / 'legacy-widget.js')
        self.page.evaluate('''() => {window.inlineUsage = mountInlineUsage(document.body,
            {resolveSlot:()=>document.querySelector('#slot')});inlineUsage.setContext({id:'sample'});}''')
        self.view = sample_view()
        self.update(self.view)
        self.pill = self.page.locator('.inline-pill')
        self.label = self.pill.locator('span:not(.legacy-usage-measure)')
        self.dot = self.pill.locator('i')
        self.popup = self.page.locator('.inline-popup')
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bridge = UsageBridge(Path(tmp.name), live=False)

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def update(self, view):
        self.page.evaluate('v=>inlineUsage.updateView(v)', view)

    def appearance(self, **cfg):
        self.page.evaluate('cfg=>inlineUsage.setAppearance(cfg)', cfg)
        self.page.evaluate('document.fonts.ready')

    def render_card(self, page='summary', **fields):
        card = self.bridge.card(self.view, {'page': page, **fields})
        self.assertIsNotNone(card)
        self.page.evaluate('card=>inlineUsage.setCard(card)', card)

    def fonts(self, selector):
        client = self.page.context.new_cdp_session(self.page)
        try:
            client.send('DOM.enable')
            client.send('CSS.enable')
            root = client.send('DOM.getDocument')['root']['nodeId']
            node = client.send('DOM.querySelector', {'nodeId': root, 'selector': selector})['nodeId']
            return {font['familyName'] for font in client.send('CSS.getPlatformFontsForNode',
                     {'nodeId': node})['fonts'] if font['glyphCount']}
        finally:
            client.detach()

    def test_font_selection_and_fallback(self):
        self.appearance(composerStyle='classic', composerWeight='bold')
        selector = '.inline-pill span:not(.legacy-usage-measure)'
        self.pill.click()
        self.render_card()
        self.page.evaluate('document.fonts.ready')
        expected = self.page.evaluate('''async () => {
            const font = async (name, sample) => (await document.fonts.load('700 11px "'+name+'"', sample).catch(()=>[])).length;
            return [await font('Usage Poppins', 'ABC'), await font('Usage MiSans', '汉字')];
        }''')
        families = self.fonts(selector)
        for installed, font in zip(expected, ('Poppins', 'MiSans')):
            self.assertEqual(any(name.startswith(font) for name in families), bool(installed), families)
        numbers = self.fonts('.usage-card-num')
        self.assertEqual(any(name.startswith('Poppins') for name in numbers), bool(expected[0]), numbers)
        for absent in (['Poppins'], ['MiSans'], ['Poppins', 'MiSans']):
            with self.subTest(absent=absent):
                self.page.evaluate('''absent => {
                    for (const sheet of document.styleSheets) for (const rule of sheet.cssRules) {
                        if (rule.type !== CSSRule.FONT_FACE_RULE) continue;
                        const family = rule.style.getPropertyValue('font-family');
                        rule.fixtureSource ??= rule.style.getPropertyValue('src');
                        rule.style.setProperty('src', absent.some(name => family.includes(name)) ?
                            'local("CodexMissingTestFontxyz")' : rule.fixtureSource);
                    }
                }''', absent)
                self.page.evaluate('document.fonts.ready')
                self.page.wait_for_function('Array.from(document.fonts).every(face => face.status !== "loading")')
                expected_pill = {'Microsoft YaHei'}
                unexpected = []
                for installed, font in zip(expected, ('Poppins', 'MiSans')):
                    if installed and font not in absent:
                        expected_pill.update(name for name in families if name.startswith(font))
                    else:
                        unexpected.append(font)
                self.assertEqual(self.fonts(selector), expected_pill)
                self.assertFalse(any(name.startswith(tuple(unexpected)) for name in self.fonts(selector)))
                self.assertEqual(self.fonts('.usage-card-num'), {'Microsoft YaHei'} if 'Poppins' in absent else numbers)

    def test_settings_status_and_session_isolation(self):
        variants = {'classic': '上下文 32% · 缓存 87.3%', 'english': 'Ctx 32% · Cache 87.3%',
                    'compact': '上下文32% / 缓存87.3%', 'pill': '上下文 32% · 缓存 87.3%'}
        for style, text in variants.items():
            with self.subTest(style=style):
                self.appearance(composerStyle=style, composerWeight='bold')
                self.assertIn(text, self.label.inner_text())
                self.assertEqual(self.pill.evaluate('el=>getComputedStyle(el).fontWeight'), '700')
        self.appearance(composerStyle='english', activityEnabled=False)
        self.assertTrue(self.dot.is_hidden())
        self.page.evaluate("inlineUsage.setContext({id:'sample',starting:true})")
        self.assertIn('Thinking · Ctx 32% · Cache 87.3%', self.label.inner_text())
        self.appearance(composerStyle='english')
        self.assertEqual(self.dot.get_attribute('data-state'), 'active')
        self.update({**self.view, 'turn_id': 'estimate', 'turn_state': 'active',
                     'metrics': {**self.view['metrics'], 'speed': '约 63 tok/s'}})
        self.assertTrue(self.label.inner_text().startswith('~63 tok/s'))
        self.update({**self.view, 'turn_id': 'new', 'turn_state': 'active', 'dot_state': 'active',
                     'metrics': {**self.view['metrics'], 'speed': '63 tok/s'}})
        self.assertTrue(self.label.inner_text().startswith('63 tok/s'))
        self.update({**self.view, 'turn_id': 'new', 'turn_state': 'completed', 'dot_state': 'completed'})
        self.assertTrue(self.label.inner_text().startswith('57 tok/s'))
        self.assertEqual(self.dot.get_attribute('data-state'), 'completed')
        self.page.evaluate("inlineUsage.setContext({id:'different'})")
        self.assertTrue(self.pill.is_hidden())
        self.update(self.view)
        self.assertNotIn('87.3%', self.label.inner_text())
        self.page.evaluate("inlineUsage.setContext({id:null,draft:true,newChat:true})")
        self.assertEqual(self.label.inner_text(), '就绪')
        self.page.evaluate("inlineUsage.setContext({id:null,draft:false,newChat:true,starting:true})")
        self.assertEqual(self.label.inner_text(), '正在启动')
        self.page.evaluate('inlineUsage.setEnabled(false)')
        self.assertEqual(self.pill.count(), 0)
        self.page.evaluate('inlineUsage.setEnabled(true)')
        self.assertEqual(self.pill.count(), 1)

    def test_card_alignment_navigation_and_keyboard(self):
        self.pill.hover()
        self.page.wait_for_function('!!inlineUsage.cardRequest()')
        self.render_card()
        self.pill.click()
        self.page.evaluate("document.querySelector('#outside').onclick = () => window.outsideClicks = 1")
        self.page.locator('#outside').click()
        self.page.wait_for_function("document.querySelector('.inline-popup').hidden")
        self.assertTrue(self.popup.is_hidden())
        self.assertEqual(self.page.evaluate('window.outsideClicks'), 1)
        self.pill.click()
        self.render_card()
        for zoom in (0.8, 1, 1.25, 1.75):
            with self.subTest(zoom=zoom):
                self.page.evaluate('z=>document.querySelector("#frame").style.zoom=z', zoom)
                self.page.wait_for_function('''z=>{const p=document.querySelector('.inline-popup').getBoundingClientRect(),
                    a=document.querySelector('.inline-pill').getBoundingClientRect();
                    return Math.abs((p.left+p.right-a.left-a.right)/2)<1 && Math.abs(a.top-p.bottom-6*z)<.7;}''', arg=zoom)
        self.assertIn('56.8', self.popup.inner_text())
        context_note = self.popup.locator('.usage-card-kpi').nth(1).locator('div').last
        self.assertTrue(context_note.evaluate('''el=>{
            const text = document.createRange(); text.selectNodeContents(el);
            return text.getClientRects().length === 1;
        }'''))
        self.assertEqual(self.popup.locator('.usage-card-num small').evaluate(
            'el=>getComputedStyle(el).fontWeight'), '700')
        self.assertGreater(float(self.popup.locator('.usage-card-num small').evaluate(
            'el=>parseFloat(getComputedStyle(el).fontSize)')), 13)
        self.assertTrue(self.popup.locator('.usage-card-output-legend').evaluate('''el=>{
            const [a,b]=el.children;
            return a.getBoundingClientRect().top===b.getBoundingClientRect().top && el.scrollWidth===el.clientWidth;
        }'''))
        self.assertEqual(self.popup.locator('img').count(), 0)
        self.page.evaluate('document.querySelector("#frame").style.zoom=1')
        self.page.get_by_role('button', name='查看逐轮统计').click()
        self.render_card('turns')
        self.popup.hover()
        self.page.mouse.wheel(0, 100)
        self.page.wait_for_function('inlineUsage.cardRequest()?.offset===1')
        self.render_card('turns', offset=1)
        self.page.get_by_role('button', name='11/24 18:01', exact=False).click()
        self.render_card('turn', turnId='1')
        self.assertIn('输入 Token', self.popup.inner_text())
        self.page.get_by_role('button', name='返回', exact=True).click()
        self.render_card('turns')
        self.page.get_by_role('button', name='返回', exact=True).click()
        self.render_card()
        self.page.set_viewport_size({'width': 310, 'height': 330})
        self.page.wait_for_function('''()=>{const p=document.querySelector('.inline-popup'),r=p.getBoundingClientRect();
            return p.scrollHeight>p.clientHeight && r.left>=0 && r.right<=310 && r.top>=0 && r.bottom<=330;}''')
        self.page.emulate_media(color_scheme='dark')
        self.page.wait_for_function("getComputedStyle(document.querySelector('.inline-popup')).backgroundColor==='rgb(32, 34, 38)'")
        self.page.keyboard.press('Escape')
        self.assertTrue(self.popup.is_hidden())

    def test_card_motion_and_anchor_changes(self):
        self.page.emulate_media(reduced_motion='no-preference')
        self.pill.hover()
        self.page.wait_for_function('!!inlineUsage.cardRequest()')
        self.render_card()
        self.assertTrue(self.popup.evaluate("el => el.getAnimations().some(a => a.transitionProperty === 'opacity') || getComputedStyle(el).opacity === '1'"))
        self.page.wait_for_function("getComputedStyle(document.querySelector('.inline-popup')).opacity === '1'")
        # Hover across the gap into the card, then pin it and move the anchor without resizing it.
        self.popup.hover()
        self.assertTrue(self.popup.is_visible())
        self.pill.click()
        self.page.evaluate("document.querySelector('#frame').style.cssText = 'bottom:auto;top:35px;left:43%'")
        self.page.wait_for_function('''() => {
            const p=document.querySelector('.inline-popup'), a=document.querySelector('.inline-pill').getBoundingClientRect(),
                r=p.getBoundingClientRect();
            return p.hasAttribute('data-below') && Math.abs(r.top-a.bottom-6)<.7 &&
                Math.abs((r.left+r.right-a.left-a.right)/2)<.7;
        }''')
        self.page.set_viewport_size({'width': 260, 'height': 290})
        self.page.wait_for_function('''() => {
            const p=document.querySelector('.inline-popup');
            return p.hasAttribute('data-compact') && p.scrollWidth === p.clientWidth && p.scrollHeight > p.clientHeight;
        }''')
        old_opening = self.page.evaluate('inlineUsage.cardRequest().opening')
        self.pill.click()
        self.assertIsNone(self.page.evaluate('inlineUsage.cardRequest()'))
        self.pill.click()
        self.assertGreater(self.page.evaluate('inlineUsage.cardRequest().opening'), old_opening)
        self.render_card()
        self.page.wait_for_function("getComputedStyle(document.querySelector('.inline-popup')).opacity === '1'")
        self.page.keyboard.press('Escape')
        self.page.wait_for_function("document.querySelector('.inline-popup').hidden")

    def test_restored_chat_first_paint_waits_for_complete_usage(self):
        self.page.evaluate('''() => {
            inlineUsage.dispose();
            window.inlineUsage=mountInlineUsage(document.body,{resolveSlot:()=>document.querySelector('#slot')});
            inlineUsage.setContext({id:'sample'});
        }''')
        self.appearance(composerStyle='english')
        self.assertTrue(self.pill.is_hidden())
        self.update({'session': 'sample', 'summary_speed': '53 tok/s', 'usage_loading': True})
        self.assertTrue(self.pill.is_hidden())
        self.update(self.view)
        self.assertTrue(self.pill.is_visible())
        self.assertEqual(self.label.inner_text(), '57 tok/s · Ctx 32% · Cache 87.3%')

    def test_live_estimate_and_expired_detail(self):
        for final_state in ('aborted', 'failed', 'completed'):
            with self.subTest(final_state=final_state):
                self.page.evaluate("inlineUsage.setContext({id:'other'});inlineUsage.setContext({id:'sample',starting:true})")
                active = {**self.view, 'turn_id': 'active', 'turn_state': 'active',
                          'metrics': {**self.view['metrics'], 'speed': '约 63 tok/s'}}
                self.update(active)
                self.assertTrue(self.label.inner_text().startswith('约 63 tok/s'))
                self.page.evaluate("inlineUsage.setContext({id:'sample'})")
                self.update({**active, 'turn_state': final_state,
                             'metrics': {**active['metrics'], 'speed': '57 tok/s', 'speed_label': '上次输出速度'}})
                self.assertTrue(self.label.inner_text().startswith('57 tok/s'))
                self.assertNotEqual(self.dot.get_attribute('data-state'), 'active')
        self.pill.click()
        self.render_card()
        self.page.get_by_role('button', name='查看逐轮统计').click()
        self.render_card('turns')
        self.page.get_by_role('button', name='11/24 18:00', exact=False).click()
        self.render_card('turn', turnId='0')
        self.view['turns'] = self.view['turns'][1:]
        self.render_card('turn', turnId='0')
        self.page.wait_for_function("inlineUsage.cardRequest().page === 'turns'")
        self.render_card('turns')
        self.assertNotIn('18:00', self.popup.inner_text())

    def test_host_mounts_only_main_and_rejects_stale_data(self):
        # Load the production host against a local fixture. Only the app-origin
        # guard is bypassed here; matching, observers, and generation are real.
        self.page.evaluate('inlineUsage.dispose()')
        self.page.set_content('''<!doctype html><style>
          body{margin:0;display:flex}.pane{width:460px}.pane[hidden]{display:none}
          .layout,.trailing,.row,.model{display:flex}.trailing,.row,.model{flex:1 1 0;min-width:0}
          .leading,.actions,.model{height:26px}.leading,.actions{width:30px}.modelName{width:80px}
          </style><section id="left" class="pane" data-app-shell-active-page="true"></section>
          <section id="right" class="pane" data-app-shell-active-page="true"></section>
          <section id="floating" class="pane" data-floating-chat-surface data-app-shell-active-page="true"></section>''')
        ids = ['11111111-2222-3333-4444-555555555555', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee']
        self.page.evaluate('''ids => {
          for (const [i, pane] of [...document.querySelectorAll('.pane')].entries()) {
            pane.innerHTML = '<div data-thread-scroll-footer="true"><div data-composer-body>' +
              '<div class="ProseMirror" contenteditable>fixture</div><div class="layout" data-composer-layout="multiline">' +
              '<div class="leading" data-composer-leading-controls></div><div class="trailing" data-composer-trailing-controls>' +
              '<div class="row"><div class="model"><span class="modelName">Model</span></div>' +
              '<div class="actions"><button type="button" aria-label="Send">↑</button></div></div></div></div></div></div>';
            const body = pane.querySelector('[data-composer-body]');
            const owner = {stateNode: {}}; owner.stateNode.current = owner;
            body['__reactFiber$fixture'] = {stateNode: body, return: {return: owner, memoizedProps: {
              conversationId: ids[i % 2], hostId: 'local', composerSubmitDisabled: false,
              isThreadHistoryLoading: false, renderComposer: () => null}}};
          }
        }''', ids)
        adapter = (HERE / 'codex-host.js').read_text(encoding='utf-8-sig')
        guard = "location.protocol !== 'app:' || location.host !== '-' || !document.body"
        self.assertEqual(adapter.count(guard), 1)
        self.page.add_script_tag(content=adapter.replace(guard, '!document.body', 1))
        status = lambda: self.page.evaluate('window.__codexInlineUsageTest.status()')
        self.assertEqual(status()['id'], ids[0])
        self.assertEqual(self.page.locator('#left .inline-pill').count(), 1)
        self.assertEqual(self.page.locator('#right .inline-pill, #floating .inline-pill').count(), 0)
        self.assertEqual(status()['geometry']['centerOffset'], 0)
        self.page.evaluate('v=>window.__codexInlineUsageTest.setView(v)', {**self.view, 'session': ids[0]})
        self.assertIn('87.3%', self.label.inner_text())
        before = status()['generation']
        self.page.evaluate('''id=>{const body=document.querySelector('#left [data-composer-body]');
          const first=body['__reactFiber$fixture'];
          const parent=first.return, current=parent.return, updated={stateNode:current.stateNode};
          current.stateNode.current=updated;
          first.alternate={stateNode:body, return:{return:updated, memoizedProps:{...parent.memoizedProps, conversationId:id}}};
          body.querySelector('.actions button').setAttribute('aria-label','Stop');}''', ids[1])
        self.page.wait_for_function('id=>window.__codexInlineUsageTest.status().id===id', arg=ids[1], timeout=3000)
        self.assertGreater(status()['generation'], before)
        self.page.evaluate('v=>window.__codexInlineUsageTest.setView(v)', {**self.view, 'session': ids[0]})
        self.assertNotIn('87.3%', self.label.inner_text())
        self.page.evaluate("()=>{for(const id of ['left','right'])document.getElementById(id).setAttribute('data-app-shell-active-page','false');}")
        self.page.wait_for_function('!window.__codexInlineUsageTest.status().mounted')
        self.assertEqual(self.page.locator('.inline-pill').count(), 0)


if __name__ == '__main__':
    unittest.main()
