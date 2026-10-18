import ctypes
import struct
import unittest
from unittest.mock import Mock,patch
from game_input import GameInput,INPUT,game_key_to_vk,read_confirm_key

class Bindings:
    base=0x140000000
    def __init__(self,codes=(15,105,-1,-1)):self.codes=codes
    def u64(self,_):return 0x200000
    def read(self,address,size):return struct.pack('<4i',*self.codes)

class GameInputTests(unittest.TestCase):
    def test_confirm_uses_keyboard_alternative_to_mouse(self):
        self.assertEqual(read_confirm_key(Bindings()),13)
        self.assertEqual(game_key_to_vk(20),ord('A'))
        self.assertEqual(game_key_to_vk(88),0x28)
    def test_missing_keyboard_binding_refuses_to_guess(self):
        with self.assertRaises(RuntimeError):read_confirm_key(Bindings((15,16,-1,-1)))
    def test_invalid_binding_index_refuses_read(self):
        with self.assertRaises(RuntimeError):read_confirm_key(Bindings((15,105,-1,99)))
    def test_windows_input_structure_has_native_size(self):
        self.assertEqual(ctypes.sizeof(INPUT),40 if ctypes.sizeof(ctypes.c_void_p)==8 else 28)
    def driver(self):
        d=GameInput.__new__(GameInput);d.pid=1;d.confirm_key=13;d.held=None;d.release_at=0
        return d
    def test_background_game_never_receives_key_down(self):
        d=self.driver();d.foreground=Mock(return_value=False)
        with patch('game_input.u') as api:
            self.assertFalse(d.press(13,100));api.SendInput.assert_not_called()
    def test_key_is_released_after_short_hold(self):
        d=self.driver();d.foreground=Mock(return_value=True)
        with patch('game_input.u') as api:
            api.GetAsyncKeyState.return_value=0;api.MapVirtualKeyW.return_value=28;api.SendInput.return_value=1
            self.assertTrue(d.press(13,100));d.tick(100.05);self.assertEqual(d.held,13)
            d.tick(100.1);self.assertIsNone(d.held);self.assertEqual(api.SendInput.call_count,2)
    def test_user_held_key_is_not_owned_or_released(self):
        d=self.driver();d.foreground=Mock(return_value=True)
        with patch('game_input.u') as api:
            api.GetAsyncKeyState.return_value=0x8000
            self.assertFalse(d.press(13,100));d.release();api.SendInput.assert_not_called()
    def test_f8_cancels_even_when_game_is_not_foreground(self):
        d=self.driver();d.foreground=Mock(return_value=False)
        with patch('game_input.u') as api:
            api.GetAsyncKeyState.side_effect=lambda vk:0x8000 if vk==0x77 else 0
            self.assertTrue(d.cancelled())
    def test_escape_only_cancels_in_game(self):
        d=self.driver();d.foreground=Mock(return_value=False)
        with patch('game_input.u') as api:
            api.GetAsyncKeyState.side_effect=lambda vk:0x8000 if vk==0x1B else 0
            self.assertFalse(d.cancelled())
            d.foreground.return_value=True;self.assertTrue(d.cancelled())
