import unittest
from unittest.mock import MagicMock
from native_badge import Win,Badge
class WindowTargetTests(unittest.TestCase):
    def candidate(self,kind='Chrome_WidgetWin_1',owner=None,style=0x14c70000,extended=0):
        w=Win.__new__(Win);w.u=MagicMock();w.u.IsWindowVisible.return_value=True
        def name(hwnd,buffer,length):buffer.value=kind;return len(kind)
        def rect(hwnd,value):value._obj.right=1000;value._obj.bottom=800;return True
        w.u.GetClassNameW.side_effect=name;w.u.GetClientRect.side_effect=rect
        w.u.GetWindow.return_value=owner
        w.u.GetWindowLongPtrW.side_effect=lambda h,index:style if index==-16 else extended
        w.process_path=lambda h:r'C:\Program Files\WindowsApps\OpenAI.Codex_test\ChatGPT.exe'
        return w
    def test_main_window(self):self.assertTrue(self.candidate().is_codex(1))
    def test_dialog_same_process_and_large_size(self):self.assertFalse(self.candidate('#32770').is_codex(1))
    def test_owned_electron_popup(self):self.assertFalse(self.candidate(owner=2).is_codex(1))
    def test_child(self):self.assertFalse(self.candidate(style=0x40000000).is_codex(1))
    def test_tool_window(self):self.assertFalse(self.candidate(extended=0x80).is_codex(1))
    def test_attach_rejects_popup_and_preserves_parent(self):
        b=Badge.__new__(Badge);b.test_host=None;b.win=self.candidate('#32770');b.target=7;b.hwnd=8
        b.attach(1);self.assertEqual((b.target,b.hwnd),(7,8))
if __name__=='__main__':unittest.main()
