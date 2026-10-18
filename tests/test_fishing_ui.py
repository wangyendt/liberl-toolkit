import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtWidgets import QApplication,QMainWindow
except ImportError:
    Trainer=None
else:
    from app import Trainer

@unittest.skipIf(Trainer is None,'Fishing UI tests require PySide6')
class FishingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):
        class Window(Trainer):
            def __init__(w):
                QMainWindow.__init__(w)
                w.editor=SimpleNamespace(mode='save',loop_session=False,fishing_loop=None,fishing=SimpleNamespace(enabled=False))
                w.editor.set_fishing_loop=Mock(side_effect=w.change_loop)
                w.note=Mock();w.error=Mock()
                w.setCentralWidget(w.fishing_page());w.sync_fishing_loop()
            def change_loop(w,enabled):
                w.editor.loop_session=enabled;w.editor.fishing.enabled=enabled
                w.editor.fishing_loop=SimpleNamespace(rounds=2,message='运行中' if enabled else '已停止')
            def closeEvent(w,event):event.accept()
        self.window=Window()
    def tearDown(self):self.window.close();self.window.deleteLater();self.qt.processEvents()
    def test_save_mode_does_not_allow_loop(self):
        self.assertFalse(self.window.fishing_loop_start.isEnabled())
        self.assertFalse(self.window.fishing_loop_stop.isEnabled())
    def test_start_and_stop_buttons_follow_session(self):
        self.window.editor.mode='live';self.window.sync_fishing_loop()
        self.window.fishing_loop_start.click()
        self.window.editor.set_fishing_loop.assert_called_with(True)
        self.assertFalse(self.window.fishing_loop_start.isEnabled())
        self.assertTrue(self.window.fishing_loop_stop.isEnabled())
        self.assertTrue(self.window.fishing_check.isChecked())
        self.window.fishing_loop_stop.click()
        self.window.editor.set_fishing_loop.assert_called_with(False)
        self.assertTrue(self.window.fishing_loop_start.isEnabled())
        self.assertFalse(self.window.fishing_loop_stop.isEnabled())
    def test_automatic_stop_updates_controls_and_message(self):
        self.window.editor.mode='live';self.window.change_loop(True)
        self.window.editor.tick_fishing_loop=Mock(side_effect=lambda:self.window.change_loop(False))
        self.window.tick_fishing_loop()
        self.assertTrue(self.window.fishing_loop_start.isEnabled())
        self.assertIn('已停止',self.window.fishing_loop_status.text())
        self.window.note.assert_called_with('已停止')
