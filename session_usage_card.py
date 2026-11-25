"""Native session statistics card. Hit areas are in logical pixels."""
from PIL import Image, ImageDraw

from composer_font import fit, scaled_fonts, width as text_width, write
from live_metrics import nonnegative
from ui_render import popup_frame, raster_cache


WIDTH = 340
PAGE_SIZE = 5
INK, MUTED, RULE = '#0d0f12', '#4b5563', '#e3e6eb'
BLUE, PALE_BLUE, GREEN, VIOLET = '#2b7fff', '#93c5fd', '#059669', '#6366f1'


def _amount(value, short=False):
    if not nonnegative(value):
        return '—'
    if short:
        for unit, suffix in ((10**9, 'b'), (10**6, 'm'), (1000, 'k')):
            if value >= unit:
                return f'{value / unit:.1f}'.removesuffix('.0') + suffix
    return f'{value:,.0f}'


def _duration(value):
    return f'{value / 1000:.2f}s' if nonnegative(value) else '—'


def _segments(total, *parts):
    if not nonnegative(total) or not total:
        return ()
    if not all(nonnegative(value) for value, _ in parts) or sum(value for value, _ in parts) > total:
        return ()
    return tuple((value / total, color) for value, color in parts)


@raster_cache
def render_session_card(scale, view, focus=None, page='summary', offset=0, turn_id=None, archive=None, dark=False):
    """Render a summary, session picker, or one turn in the companion popup."""
    p = lambda value: round(value * scale * 3)
    sessions = view.get('sessions', ())
    turns = view.get('turns', ())
    selected = next((turn for turn in turns if turn['id'] == turn_id), None) if page == 'turn' else None
    picker = page == 'sessions'
    shown = (sessions if picker else turns)[offset:offset + PAGE_SIZE] if page in ('sessions', 'turns') else ()
    has_summary = view.get('session') and view.get('metrics') and page == 'summary'
    height = (94 + 39 * len(shown) if shown else 122) if page in ('sessions', 'turns') else (
        228 if selected else 344 if has_summary else 131)
    ink, muted, rule = ('#f0f2f5', '#c0c8d4', '#404751') if dark else (INK, MUTED, RULE)
    blue, pale_blue, green, violet = ('#80adff', '#a5c9fb', '#70caa5', '#a89aee') if dark else (BLUE, PALE_BLUE, GREEN, VIOLET)
    panel, track, badge = ('#272c35', '#353c46', '#343a44') if dark else ('#f5f6f8', '#e9ebee', '#eef0f3')
    slate, mist = ('#a9b7cc', '#77869c') if dark else ('#94a3b8', '#cbd5e1')
    quiet = '#a9b7cc' if dark else '#647184'
    image = popup_frame(p, WIDTH, height, height - 8, dark=dark)
    draw = ImageDraw.Draw(image)
    fonts = {name: scaled_fonts(archive, p(size), bold) for name, size, bold in (
        ('body', 10, False), ('small', 9, False), ('title', 12, True),
        ('number', 15, True), ('strong', 10, True), ('label', 9, True))}
    actions = []

    def line(x, y, text, *, style='body', color=ink, width=None, right=False):
        f = fonts[style]
        text = fit(draw, str(text), f, p(width)) if width is not None else str(text)
        write(draw, (p(x), p(y)), text, f, color, 'right' if right else 'left', tabular=True)

    def action(rect, value):
        if focus == len(actions):
            draw.rounded_rectangle(tuple(p(v) for v in rect), p(5), fill='#3a3a38' if dark else '#eeedeb')
        actions.append((rect, value))

    def divider(y):
        draw.line((p(19), p(y), p(WIDTH - 19), p(y)), fill=rule, width=max(1, p(.6)))

    def pair(y, label, value):
        line(19, y, label, style='small', color=muted, width=110)
        line(WIDTH - 19, y, value, style='strong', width=174, right=True)

    def bar(y, segments, left=19, right=WIDTH - 19):
        left, right = p(left), p(right)
        draw.rounded_rectangle((left, p(y), right, p(y + 5)), p(2.5), fill=track)
        start = 0.0
        for value, color in segments:
            end = max(start, min(1, start + value))
            x0 = left + round((right - left) * start) + (p(1) if start else 0)
            x1 = left + round((right - left) * end)
            if x1 > x0:
                draw.rounded_rectangle((x0, p(y), x1, p(y + 5)), min(p(2.5), (x1 - x0) // 2), fill=color)
            start = end

    def legend(y, entries, left=19, right=WIDTH - 19, compact=False):
        cuts = (0, .43, 1) if compact else tuple(i / len(entries) for i in range(len(entries) + 1))
        for (label, color), start, end in zip(entries, cuts, cuts[1:]):
            x = left + start * (right - left)
            draw.ellipse((p(x), p(y - 2.4), p(x + 4.8), p(y + 2.4)), fill=color)
            line(x + 8, y, label, style='small' if compact else 'body', color=muted,
                 width=(end - start) * (right - left) - 10)

    def chip(y, label):
        right = WIDTH - 19
        left = right - text_width(draw, label, fonts['label'], tabular=True) / p(1) - 12
        draw.rounded_rectangle((p(left), p(y - 7), p(right), p(y + 7)), p(6), fill=badge)
        line(right - 6, y, label, style='label', color=muted, right=True)

    title = {'sessions': '选择会话', 'turns': '逐轮统计', 'turn': '本轮统计'}.get(page, '本轮最新调用' if has_summary else '会话用量')
    line(19, 24, title, style='title')
    if page != 'summary':
        action((WIDTH - 68, 13, WIDTH - 15, 35), ('usage_page', 'turns' if page == 'turn' else 'summary'))
        line(WIDTH - 23, 24, '返回', style='small', color=muted, right=True)
    elif not view.get('follow_visible'):
        action((WIDTH - 83, 13, WIDTH - 15, 35), ('usage_page', 'sessions'))
        line(WIDTH - 23, 24, '切换会话 ›', style='small', color=muted, right=True)
    elif has_summary and view.get('turn_state') == 'active':
        chip(24, '运行中')

    if page in ('sessions', 'turns'):
        line(19, 48, view.get('picker_note', '本机 Codex 会话') if picker else view.get('title', '当前会话'),
             style='small', color=muted, width=WIDTH - 38)
        for index, row in enumerate(shown):
            y = 59 + index * 39
            action((14, y, WIDTH - 14, y + 37), ('usage_select' if picker else 'usage_turn', row['id']))
            line(20, y + 11, row['title'], style='strong', width=WIDTH - 60)
            line(20, y + 27, row.get('detail', ''), style='small', color=muted, width=WIDTH - 58)
            line(WIDTH - 23, y + 19, '›', color=muted, right=True)
        rows = sessions if picker else turns
        if not shown:
            line(19, 80, '暂无可查看记录', color=muted)
        if len(rows) > PAGE_SIZE:
            line(19, height - 24, f'{offset + 1}–{offset + len(shown)} / {len(rows)} · 滚轮翻阅', style='small', color=muted)
    elif selected:
        line(19, 48, selected['title'], style='strong', width=WIDTH - 38)
        line(19, 66, selected['detail'], style='small', color=muted, width=WIDTH - 38)
        divider(82)
        for index, (label, value) in enumerate(selected['metrics']):
            pair(97 + 17 * index, label, value)
    elif has_summary:
        metrics = view['metrics']
        detail = metrics['detail']
        used, window = detail['used'], detail['window']
        remaining = max(0, window - used) if nonnegative(window) and nonnegative(used) else None
        speed = metrics['speed']
        raw_speed = detail['speed_value']
        if raw_speed is not None:
            speed = f'{raw_speed:.1f} tok/s'
        speed, speed_unit = (speed[:-6], 't/s') if speed.endswith(' tok/s') else (speed, '')
        draw.rounded_rectangle((p(19), p(39), p(WIDTH - 19), p(97)), p(8), fill=panel, outline=rule)
        for i, (label, value, sub, color) in enumerate((
            (metrics.get('speed_label', '生成速度'), speed, metrics['timing'], ink),
            ('上下文窗口', metrics['context'], f'余 {_amount(remaining, True)} / {_amount(window, True)}', blue),
            ('累计缓存命中', metrics['cache'], metrics['last_cache'].replace('最近调用 ', '本步 '), green),
        )):
            x = 29 + 100.5 * i
            if i:
                draw.line((p(x - 9), p(46), p(x - 9), p(90)), fill=rule, width=max(1, p(.6)))
            line(x, 52, label, style='label', color=quiet, width=85)
            line(x, 68, value, style='number', color=color, width=85)
            if not i and speed_unit:
                unit_x = x + text_width(draw, value, fonts['number'], tabular=True) / p(1) + 3
                if unit_x < x + 70:
                    line(unit_x, 70, speed_unit, style='small', color=quiet)
            line(x, 84, sub, color=muted, width=85)

        def metric(y, label, value, left=19, right=WIDTH - 19):
            line(left, y, label, style='strong')
            line(right, y, value, style='strong', width=(right - left) * .64, right=True)

        fresh, reused = detail['context_input']
        ctx = _segments(window, (fresh, blue), (reused, pale_blue))
        metric(112, '当前上下文', f'{_amount(used)} / {_amount(window, True)}')
        bar(122, ctx or ((metrics['context_ratio'], blue),))
        legend(137, ((f'新增 {_amount(fresh)}', blue), (f'缓存 {_amount(reused)}', pale_blue),
                     (f'余 {_amount(remaining, True)}', slate)))
        divider(150)

        mid = WIDTH / 2
        draw.line((p(mid), p(158), p(mid), p(198)), fill=rule, width=max(1, p(.6)))
        duration, first, stream = (detail[k] for k in ('duration_ms', 'first_ms', 'stream_ms'))
        metric(166, '响应耗时', _duration(duration), right=mid - 9)
        bar(177, _segments(duration, (first, slate), (stream, blue)), right=mid - 9)
        legend(191, ((f'首字 {_duration(first)}', slate), (f'流式 {_duration(stream)}', blue)), right=mid - 9)
        result, reasoning = detail['step_split']
        step = detail['step_output']
        metric(166, '本步输出', _amount(step), left=mid + 9)
        bar(177, _segments(step, (reasoning, violet), (result, blue)), left=mid + 9)
        legend(191, ((f'思考 {_amount(reasoning, True)}', violet), (f'正文/工具 {_amount(result, True)}', blue)),
               left=mid + 9, compact=True)

        for x in range(19, WIDTH - 19, 5):
            draw.line((p(x), p(209), p(x + 3), p(209)), fill=rule, width=max(1, p(.6)))
        line(19, 223, '当前会话累计', style='label', color=quiet)
        if turns:
            chip(223, f'{len(turns)} 轮有记录' if len(turns) < 25 else f'最近 {len(turns)} 轮有记录')
        input_total = detail['input_total']
        fresh, reused = detail['input_split']
        metric(243, '累计输入量', f'总计 {_amount(input_total, True)} · 命中率 {metrics["cache"]}')
        bar(254, _segments(input_total, (fresh, mist), (reused, green)))
        legend(269, ((f'新增 {_amount(fresh, True)}', mist), (f'缓存读取 {_amount(reused, True)}', green)))
        output_total = detail['output_total']
        result, reasoning = detail['output_split']
        metric(288, '累计输出量', f'总计 {_amount(output_total, True)}')
        bar(299, _segments(output_total, (reasoning, violet), (result, blue)))
        legend(313, ((f'思考链 {_amount(reasoning, True)}', violet), (f'正文/工具 {_amount(result, True)}', blue)))
        if turns:
            action((14, 319, WIDTH - 14, 335), ('usage_page', 'turns'))
            line(19, 327, '查看逐轮统计', style='small', color=muted)
            line(WIDTH - 19, 327, '›', color=muted, right=True)
    else:
        line(19, 48, view.get('title', '选择要查看的会话'), style='strong', width=WIDTH - 38)
        line(19, 71, view.get('message', '正在读取当前会话用量' if view.get('follow_visible') else '点击「切换会话」查看本机 Codex 记录'), color=muted, width=WIDTH - 38)
        if view.get('subtitle'):
            line(19, 93, view['subtitle'], style='small', color=muted, width=WIDTH - 38)

    return image.resize((round(WIDTH * scale), round(height * scale)), Image.Resampling.LANCZOS), actions
