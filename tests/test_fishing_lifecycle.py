from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from backend import Editor
from test_fishing_loop import Assist,Inputs

class LifecycleAssist(Assist):
    def __init__(self,enabled=False):
        super().__init__();self.enabled=enabled;self.owned=[1] if enabled else [];self.disabled=0
    def verify_loop_profile(self):pass
    def verify(self):pass
    def enable(self):self.enabled=True;self.owned=[1]
    def disable(self):self.enabled=False;self.owned=[];self.disabled+=1

class FishingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.editor=Editor.__new__(Editor);self.inputs=Inputs()
        self.editor.memory=SimpleNamespace(pid=1)
        self.editor.fishing=LifecycleAssist()
        self.editor.fishing_retry=LifecycleAssist()
        self.editor.fishing_loop=None;self.editor.loop_session=False;self.editor.loop_owned_assist=False
        self.editor.backup=Path('synthetic-backup.zip')
        self.editor._check_live=Mock();self.editor._fishing_quantity=Mock(return_value=3);self.editor.audit=Mock()
        self.patch_inputs=patch('backend.GameInput',return_value=self.inputs);self.patch_inputs.start()
    def tearDown(self):self.editor.stop_fishing_loop();self.patch_inputs.stop()
    def test_loop_owned_assist_is_restored_on_stop(self):
        self.editor.set_fishing_loop(True)
        self.assertTrue(self.editor.fishing.enabled)
        self.editor.stop_fishing_loop()
        self.assertFalse(self.editor.fishing.enabled);self.assertFalse(self.editor.loop_session)
        self.assertEqual(self.editor.fishing.disabled,1)
        self.assertFalse(self.editor.fishing_retry.enabled)
    def test_manual_assist_survives_stopping_only_the_loop(self):
        self.editor.fishing=LifecycleAssist(enabled=True)
        self.editor.set_fishing_loop(True);self.editor.stop_fishing_loop()
        self.assertTrue(self.editor.fishing.enabled);self.assertEqual(self.editor.fishing.disabled,0)
        self.assertFalse(self.editor.fishing_retry.enabled)
        self.editor.disable_locks();self.assertFalse(self.editor.fishing.enabled)
    def test_not_in_fishing_never_enables_patches_or_creates_backup(self):
        self.editor.fishing.snapshot=None
        with patch('backend.backup_saves') as backup:
            with self.assertRaises(RuntimeError):self.editor.set_fishing_loop(True)
            backup.assert_not_called()
        self.assertFalse(self.editor.fishing.enabled);self.assertFalse(self.editor.loop_session)
    def test_connection_change_stops_loop_and_releases_input(self):
        self.editor.set_fishing_loop(True);self.inputs.held=13
        self.editor._check_live.side_effect=RuntimeError('changed')
        with self.assertRaises(RuntimeError):self.editor.tick_fishing_loop()
        self.assertIsNone(self.inputs.held);self.assertFalse(self.editor.loop_session)
        self.assertFalse(self.editor.fishing.enabled)
    def test_scene_exit_finalizes_automatic_stop(self):
        self.editor.set_fishing_loop(True);self.editor.fishing.snapshot=None
        self.editor.tick_fishing_loop()
        self.assertFalse(self.editor.loop_session);self.assertFalse(self.editor.fishing.enabled)
        self.assertIn('离开',self.editor.fishing_loop.message)
    def test_disable_all_cancels_loop_and_manual_assist(self):
        self.editor.fishing=LifecycleAssist(enabled=True)
        self.editor.set_fishing_loop(True);self.inputs.held=13
        self.editor.disable_locks()
        self.assertIsNone(self.inputs.held);self.assertFalse(self.editor.loop_session)
        self.assertFalse(self.editor.fishing.enabled)
    def test_retry_patch_failure_rolls_back_loop_owned_success_assist(self):
        self.editor.fishing_retry.enable=Mock(side_effect=RuntimeError('retry conflict'))
        with self.assertRaisesRegex(RuntimeError,'retry conflict'):self.editor.set_fishing_loop(True)
        self.assertFalse(self.editor.fishing.enabled);self.assertFalse(self.editor.loop_session)
    def test_retry_patch_is_verified_and_cleanup_preserves_manual_assist(self):
        self.editor.fishing=LifecycleAssist(enabled=True)
        self.editor.set_fishing_loop(True)
        self.editor.fishing_retry.verify=Mock(side_effect=RuntimeError('retry changed'))
        with self.assertRaisesRegex(RuntimeError,'retry changed'):self.editor.tick_fishing_loop()
        self.assertTrue(self.editor.fishing.enabled);self.assertFalse(self.editor.fishing_retry.enabled)
