"""Read the Windows Codex composer toolbar via UI Automation."""

from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import sys
import time
import unicodedata
from uuid import UUID


Rect = tuple[int, int, int, int]
BLOCKED = object()  # Confirmed obstruction, distinct from an incomplete UIA read.
ABSENT = object()   # A complete editor query found no visible composer.
NO_MAIN = object()  # A complete query found only a non-main/auxiliary composer.

# UI Automation IDs from UIAutomationClient.h.
_CONTROL_TYPE = 30003
_NAME = 30005
_AUTOMATION_ID = 30011
_CLASS_NAME = 30012
_OFFSCREEN = 30022
_WINDOW_IS_MODAL = 30077
_ARIA_ROLE = 30101
_ARIA_PROPERTIES = 30102
_IS_DIALOG = 30174
_BOUNDING_RECT = 30001
_BUTTON, _EDIT, _IMAGE = 50000, 50004, 50006
_DESCENDANTS = 4
_ACTIVE_CHAT_CLASS = "aria-[current=page]:bg-secondary-ghost-hover"
_BLOCKING_ROLES = ("dialog", "alertdialog", "menu")
_HOME_COMPOSER_CLASSES = frozenset(("home-composer-anchor", "sm:home-composer-centered",
                                    "group/home-composer-layout", "[container-name:home-main-content]"))

_ATTACH = {"add files and more", "添加文件等内容", "新增檔案等內容", "加入檔案等內容"}
_PERMISSIONS = {"change permissions", "更改权限", "更改權限", "變更權限"}
_CONTEXT = ("context usage", "context window", "上下文用量", "上下文窗口", "上下文視窗")
_STOP = {"stop", "stop generation", "停止", "停止生成", "停止產生", "停止回复", "停止回覆"}


def _looks_like_main_composer(window, body, editor):
    """Reject a small auxiliary composer in a docked/full-screen pane.

    The main Codex composer occupies the central thread pane. Auxiliary
    editors can expose the same ProseMirror and ComposerLayoutBody classes,
    so geometry is the stable signal available from the cached UIA snapshot:
    the main body is wide enough for the full toolbar and remains in the
    central/left content area, while a right-side mini composer is narrow and
    close to the window edge.
    """
    window_width = window[2] - window[0]
    body_width = body[2] - body[0]
    editor_width = editor[2] - editor[0]
    if window_width <= 0 or body_width <= 0 or editor_width <= 0:
        return False
    return (
        body_width >= max(520, round(window_width * .24))
        and editor_width >= max(420, round(window_width * .18))
        and (body[0] + body[2]) / 2 <= window[0] + window_width * .72
    )


@dataclass(frozen=True)
class ComposerSample:
    anchor: object
    chat_title: str | None
    pane_x: tuple[int, int] | None = None
    new_chat: bool | None = None
    running: bool | None = None


def selected_chat_title(nodes):
    # Only the selected conversation row qualifies; navigation tabs and
    # selected model/option buttons also expose aria-current.
    names = [node.name.strip() for node in nodes if node.role == 'group'
             and _ACTIVE_CHAT_CLASS in node.classes and node.name.strip()]
    return names[0] if len(names) == 1 else None


@dataclass(frozen=True)
class _Node:
    kind: int
    name: str
    automation_id: str
    class_name: str
    role: str
    rect: Rect
    modal: bool = False

    @property
    def classes(self):
        return self.class_name.split()

    @property
    def label(self):
        return unicodedata.normalize("NFKC", self.name).strip().casefold()


def _valid(rect):
    left, top, right, bottom = rect
    return left < right and top < bottom


def _intersects(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _contains(outer, inner):
    return (outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def _is_editor(node):
    return node.kind == _EDIT and "ProseMirror" in node.classes


def _composer_running(nodes, gap):
    # The main request button replaces Send with Stop before any log exists.
    return any(node.kind == _BUTTON and "size-token-button-composer" in node.classes
               and node.label in _STOP and _valid(node.rect) and node.rect[0] >= gap[2]
               and node.rect[1] < gap[3] and gap[1] < node.rect[3] for node in nodes)


def _eligible_editor(matches, window):
    if any(node.modal or node.role in ("alertdialog", "menu") for _, node in matches):
        return None
    editors = [(obj, node) for obj, node in matches
               if _is_editor(node) and _contains(window, node.rect)]
    if len(editors) <= 1:
        return editors[0] if editors else None
    # The sidebar's selected conversation belongs to the main pane. A side
    # agent can have focus without changing that selection. Only disjoint
    # horizontal editors establish a left pane; never pick an overlapping one.
    left = min(editors, key=lambda item: item[1].rect[0])
    return left if all(item is left or left[1].rect[2] <= item[1].rect[0] for item in editors) else None


def _toolbar_gap(window, body, editor, nodes, *, blocked=None):
    if not _contains(window, body) or not _contains(body, editor):
        return None
    # Chromium flattens this row in UIA. Identify it by its actual buttons
    # beneath the editor, and use their heights to scale padding and fit checks.
    buttons = [n for n in nodes if n.kind == _BUTTON and n.rect[1] >= editor[3]
               and _contains(body, n.rect) and "h-token-button-composer" in n.classes]
    attach = [n for n in buttons if n.label in _ATTACH]
    permissions = [n for n in buttons if n.label in _PERMISSIONS]
    models = [n for n in buttons if "min-w-0" in n.classes and n.name.strip()
              and n.automation_id.startswith("radix-") and n.label not in _PERMISSIONS]
    if len(attach) != 1 or len(permissions) != 1 or len(models) != 1:
        return None
    plus, permission, model = attach[0].rect, permissions[0].rect, models[0].rect
    top = max(plus[1], permission[1], model[1])
    bottom = min(plus[3], permission[3], model[3])
    height = plus[3] - plus[1]
    if (bottom - top < height * 0.75 or top - editor[3] > height
            or not plus[0] < permission[0] < permission[2] <= model[0]):
        return blocked

    right = model[0]
    context = [n.rect for n in nodes if n.label.startswith(_CONTEXT)
               and n.kind in (_BUTTON, _IMAGE) and _valid(n.rect)
               and n.rect[1] < bottom and top < n.rect[3]]
    if context:
        if any(not _contains(body, rect) or rect[2] > model[0] for rect in context):
            return blocked
        right = min(rect[0] for rect in context)

    gap = (permission[2], top, right, bottom)
    if not _valid(gap) or any(_valid(n.rect) and _intersects(n.rect, gap) for n in nodes):
        return blocked
    padding = max(2, round(height / 8))
    usable = (gap[0] + padding, top, right - padding, bottom)
    return usable if usable[2] - usable[0] >= 2 * height else blocked


class _Unavailable(Exception):
    pass


class _VariantValue(C.Union):
    # BRECORD is the largest member on 64-bit Windows. Keep the ABI size and
    # alignment even though the query uses only VT_I4, VT_BOOL and VT_BSTR.
    _fields_ = [("integer", W.LONG), ("boolean", W.SHORT), ("bstr", C.c_void_p),
                ("record", C.c_void_p * 2), ("alignment", C.c_double)]


class _Variant(C.Structure):
    _fields_ = [("vt", W.USHORT), ("reserved", W.USHORT * 3), ("value", _VariantValue)]


def _check(hr):
    if hr < 0:
        raise _Unavailable(f"UI Automation HRESULT 0x{hr & 0xffffffff:08X}")


def _signature(function, result, *args):
    function.restype = result
    function.argtypes = args
    return function


def _method(obj, index, result, *args):
    table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p)))[0]
    return C.WINFUNCTYPE(result, C.c_void_p, *args)(table[index])


def _call(obj, index, *args):
    method = _method(obj, index, W.LONG, *(arg_type for arg_type, _ in args))
    _check(method(obj, *(value for _, value in args)))


def _get(obj, index, result_type, *args):
    value = result_type()
    _call(obj, index, *args, (C.POINTER(result_type), C.byref(value)))
    return value


_PTR = C.c_void_p
_INT = C.c_int


@contextmanager
def _owned(obj):
    try:
        if not obj:
            raise _Unavailable("UI Automation did not return an object")
        yield obj
    finally:
        if obj:
            _method(obj, 2, W.ULONG)(obj)  # IUnknown::Release


class _UIA:
    """Small native COM binding. Slots follow the SDK's named interfaces."""

    def __init__(self, stack, *, read_chat=False):
        ole = C.WinDLL("ole32")
        automation = C.WinDLL("oleaut32")
        self.free_bstr = _signature(automation.SysFreeString, None, _PTR)
        self.bstr_len = _signature(automation.SysStringLen, W.UINT, _PTR)
        self.clear_variant = _signature(automation.VariantClear, W.LONG, C.POINTER(_Variant))
        alloc_bstr = _signature(automation.SysAllocString, _PTR, W.LPCWSTR)

        # COINIT_MULTITHREADED, paired for both S_OK and S_FALSE. RPC_E_CHANGED_MODE
        # fails closed on an STA thread. All interfaces are released on this thread.
        _check(_signature(ole.CoInitializeEx, W.LONG, _PTR, W.DWORD)(None, 0))
        stack.callback(_signature(ole.CoUninitialize, None))
        create = _signature(ole.CoCreateInstance, W.LONG, _PTR, _PTR, W.DWORD,
                            _PTR, C.POINTER(_PTR))
        clsid = UUID("e22ad333-b25f-460c-83d0-0581107395c9").bytes_le  # CUIAutomation8
        iid = UUID("34723aff-0c9d-49d0-9896-7ab52df8cd8a").bytes_le  # IUIAutomation2
        client = _PTR()
        _check(create(C.byref(C.create_string_buffer(clsid)), None, 1,
                      C.byref(C.create_string_buffer(iid)), C.byref(client)))
        self.client = stack.enter_context(_owned(client))
        self.deadline = time.monotonic() + 0.8
        _call(client, 61, (W.DWORD, 250))  # put_ConnectionTimeout
        _call(client, 63, (W.DWORD, 350))  # put_TransactionTimeout
        self.raw = stack.enter_context(_owned(_get(client, 17, _PTR)))  # RawViewCondition
        self.walker = stack.enter_context(_owned(_get(client, 16, _PTR)))  # RawViewWalker
        self.cache = stack.enter_context(_owned(_get(client, 20, _PTR)))  # CreateCacheRequest
        _call(self.cache, 9, (_PTR, self.raw))  # put_TreeFilter
        for property_id in (_CONTROL_TYPE, _NAME, _AUTOMATION_ID, _CLASS_NAME,
                            _OFFSCREEN, _BOUNDING_RECT, _ARIA_ROLE, _IS_DIALOG, _WINDOW_IS_MODAL):
            _call(self.cache, 3, (_INT, property_id))  # AddProperty

        def condition(property_id, value, flags=0):
            variant = _Variant()
            if isinstance(value, str):
                variant.vt = 8  # VT_BSTR
                variant.value.bstr = alloc_bstr(value)
                if not variant.value.bstr:
                    raise _Unavailable("SysAllocString failed")
            elif isinstance(value, bool):
                variant.vt = 11  # VT_BOOL
                variant.value.boolean = -int(value)
            else:
                variant.vt = 3  # VT_I4
                variant.value.integer = value
            try:
                args = ((_INT, property_id), (_Variant, variant))
                if flags:
                    args += ((_INT, flags),)
                return stack.enter_context(_owned(_get(client, 24 if flags else 23, _PTR, *args)))
            finally:
                if variant.vt == 8:
                    self.free_bstr(variant.value.bstr)

        self.visible = condition(_OFFSCREEN, False)
        self.selected = None
        if read_chat:
            current = condition(_ARIA_PROPERTIES, ';current=page', flags=2)
            self.selected = stack.enter_context(_owned(_get(client, 25, _PTR, (_PTR, current), (_PTR, self.visible))))
        candidates = [condition(_CONTROL_TYPE, _EDIT), condition(_WINDOW_IS_MODAL, True),
                      condition(_IS_DIALOG, True)]
        candidates.extend(condition(_ARIA_ROLE, role) for role in _BLOCKING_ROLES)
        array = (_PTR * len(candidates))(*(p.value for p in candidates))
        either = stack.enter_context(_owned(_get(client, 30, _PTR, (_PTR, array),
                                                  (_INT, len(array)))))  # CreateOrConditionFromNativeArray
        self.candidates = stack.enter_context(_owned(_get(client, 25, _PTR, (_PTR, either),
                                                          (_PTR, self.visible))))  # CreateAndCondition

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise _Unavailable("UI Automation search deadline exceeded")

    def string(self, obj, slot):
        ptr = _get(obj, slot, _PTR)  # cached BSTR getter
        try:
            return C.wstring_at(ptr, self.bstr_len(ptr)) if ptr else ""
        finally:
            if ptr:
                self.free_bstr(ptr)

    def cached_bool(self, obj, property_id):
        variant = _get(obj, 12, _Variant, (_INT, property_id))  # GetCachedPropertyValue
        value = variant.vt == 11 and variant.value.boolean != 0
        _check(self.clear_variant(C.byref(variant)))
        return value

    def node(self, obj):
        if _get(obj, 70, W.BOOL).value:  # CachedIsOffscreen
            return None
        rect = self.rect(obj, 75)  # CachedBoundingRectangle
        # The candidate query includes IsDialog outright. Preserve that signal
        # even for providers reporting a non-window control type or ARIA role.
        is_dialog = self.cached_bool(obj, _IS_DIALOG) or self.cached_bool(obj, _WINDOW_IS_MODAL)
        modal = self.cached_bool(obj, _WINDOW_IS_MODAL)
        return _Node(_get(obj, 53, _INT).value, self.string(obj, 55), self.string(obj, 61),
                     self.string(obj, 62),
                     "dialog" if is_dialog else self.string(obj, 77),
                     rect, modal)

    @staticmethod
    def rect(obj, slot):
        rect = _get(obj, slot, W.RECT)
        return rect.left, rect.top, rect.right, rect.bottom

    def find(self, stack, parent, condition, limit):
        self.check_time()
        # FindAllBuildCache batches property reads within one provider transaction.
        with _owned(_get(parent, 8, _PTR, (_INT, _DESCENDANTS), (_PTR, condition),
                         (_PTR, self.cache))) as results:
            count = _get(results, 3, _INT).value
            if count > limit:
                raise _Unavailable("UI Automation result limit exceeded")
            for index in range(count):
                self.check_time()
                obj = stack.enter_context(_owned(_get(results, 4, _PTR, (_INT, index))))
                node = self.node(obj)
                if node is not None:
                    yield obj, node

    def sample(self, hwnd):
        with ExitStack() as stack:
            root = stack.enter_context(_owned(_get(self.client, 6, _PTR, (_PTR, hwnd))))
            chat = selected_chat_title(node for _, node in self.find(stack, root, self.selected, 18)) if self.selected else None
            # Keep the selected chat observation even if a composer control
            # is between layouts. Only geometry needs the deeper scan.
            self.pane_x = None
            self.new_chat = None
            self.running = None
            try:
                anchor = self.anchor(stack, root, check_home=bool(self.selected and not chat))
            except (_Unavailable, OSError):
                anchor = None
            return ComposerSample(anchor, chat, self.pane_x, self.new_chat, self.running)

    def _home_composer(self, stack, body_obj):
        # The sidebar may lack a selected row in an existing chat too. Only
        # classify a draft from its own composer ancestors, never from that miss.
        parent = body_obj
        for _ in range(12):
            self.check_time()
            parent = stack.enter_context(_owned(_get(self.walker, 9, _PTR, (_PTR, parent),
                                                      (_PTR, self.cache))))
            classes = self.string(parent, 62).split()
            if _HOME_COMPOSER_CLASSES.intersection(classes) or any(token.startswith('Hero-') for token in classes):
                return True
            if 'thread-scroll-container' in classes or '@container/app-shell-main-content' in classes:
                return False
        return False

    def anchor(self, stack, root, *, check_home=False):
        window = self.rect(root, 43)  # CurrentBoundingRectangle, physical screen pixels
        if not _valid(window):
            return None
        candidates = list(self.find(stack, root, self.candidates, 32))
        if any(node.modal or node.role == 'alertdialog' for _, node in candidates):
            return BLOCKED
        match = _eligible_editor([(obj, node) for obj, node in candidates if node.role != 'menu'], window)
        if match is None:
            return None if any(_is_editor(node) for _, node in candidates) else ABSENT
        editor_obj, editor = match
        self.check_time()
        body_obj = stack.enter_context(_owned(_get(self.walker, 9, _PTR, (_PTR, editor_obj),
                                                    (_PTR, self.cache))))
        body = self.node(body_obj)
        if body is None or not any(token.startswith("ComposerLayoutBody-") for token in body.classes):
            return NO_MAIN
        if not _looks_like_main_composer(window, body.rect, editor.rect):
            return NO_MAIN
        controls = list(self.find(stack, body_obj, self.visible, 96))
        nodes = [node for _, node in controls]
        if [node.rect for node in nodes if _is_editor(node)] != [editor.rect]:
            return None
        gap = _toolbar_gap(window, body.rect, editor.rect, nodes, blocked=BLOCKED)
        if isinstance(gap, tuple) and any(node.role == 'menu' and _intersects(node.rect, gap) for _, node in candidates):
            gap = BLOCKED
        self.check_time()
        if gap is None or window != self.rect(root, 43) or body.rect != self.rect(body_obj, 43):
            return None
        if isinstance(gap, tuple):
            # The composer can reflow while the old editor/toolbar snapshot
            # is being read. Never publish a frame built from that mix.
            for obj, node in controls:
                if (_is_editor(node) or node.label in _PERMISSIONS
                        or (node.rect[1] >= editor.rect[3] and node.kind in (_BUTTON, _IMAGE)
                            and (node.label.startswith(_CONTEXT) or 'min-w-0' in node.classes))):
                    self.check_time()
                    if node.rect != self.rect(obj, 43):
                        return None
            self.pane_x = body.rect[0], body.rect[2]
            self.running = _composer_running(nodes, gap)
            if check_home:
                try:
                    self.new_chat = self._home_composer(stack, body_obj)
                except (_Unavailable, OSError):
                    pass  # Keep valid geometry when the route cannot be read.
        return gap


def find_composer_anchor(hwnd: int, *, report_blocked=False, read_chat=False):
    """Return usable (left, top, right, bottom) in physical screen pixels.

    Call on a background thread that is uninitialized for COM or uses MTA.
    None means the composer is absent, ambiguous or unavailable. With
    report_blocked, a confirmed obstruction returns BLOCKED instead of None.
    The caller owns window selection, visibility policy, and polling cadence.
    """
    if sys.platform != "win32" or not isinstance(hwnd, int) or hwnd <= 0:
        return None
    try:
        user = C.WinDLL("user32")
        visible = _signature(user.IsWindowVisible, W.BOOL, W.HWND)
        enabled = _signature(user.IsWindowEnabled, W.BOOL, W.HWND)
        minimized = _signature(user.IsIconic, W.BOOL, W.HWND)
        if not visible(hwnd) or not enabled(hwnd) or minimized(hwnd):
            return ComposerSample(BLOCKED, None) if read_chat else BLOCKED if report_blocked else None
        with ExitStack() as stack:
            set_dpi = _signature(user.SetThreadDpiAwarenessContext, _PTR, _PTR)
            previous = set_dpi(C.c_void_p(-4))  # PER_MONITOR_AWARE_V2
            if not previous:
                return None
            stack.callback(set_dpi, previous)
            sample = _UIA(stack, read_chat=read_chat).sample(hwnd)
        if not visible(hwnd) or not enabled(hwnd) or minimized(hwnd):
            sample = ComposerSample(BLOCKED, None)
        if read_chat:
            return sample
        if sample.anchor is ABSENT:
            return None
        return (BLOCKED if report_blocked else None) if sample.anchor is BLOCKED else sample.anchor
    except (_Unavailable, OSError, AttributeError, OverflowError):
        return None
