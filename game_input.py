"""Short, foreground-only Windows key presses owned by the fishing loop."""
import ctypes as C
from ctypes import wintypes as W
import struct

u=C.WinDLL('user32',use_last_error=True)
u.GetForegroundWindow.argtypes=[];u.GetForegroundWindow.restype=W.HWND
u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)];u.GetWindowThreadProcessId.restype=W.DWORD
u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=C.c_short
u.MapVirtualKeyW.argtypes=[W.UINT,W.UINT];u.MapVirtualKeyW.restype=W.UINT

class KEYBDINPUT(C.Structure):
    _fields_=[('wVk',W.WORD),('wScan',W.WORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class MOUSEINPUT(C.Structure):
    _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class INPUTUNION(C.Union):
    _fields_=[('ki',KEYBDINPUT),('mi',MOUSEINPUT)]
class INPUT(C.Structure):
    _anonymous_=('data',)
    _fields_=[('type',W.DWORD),('data',INPUTUNION)]
u.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int];u.SendInput.restype=W.UINT

def game_key_to_vk(code):
    # Verified conversion in this profile's sora_2nd.exe + 0x361E0.
    if 20<=code<=45:return code+45
    if 46<=code<=55:return code+50
    if 63<=code<=72:return code-15
    if 73<=code<=84:return code+39
    return {56:107,57:109,85:37,86:38,87:39,88:40,89:45,90:46,91:33,92:34,93:35,94:36,
            95:44,102:27,103:32,104:8,105:13,106:187,107:9,109:192,110:221,
            111:219,112:186,114:188,115:190,116:191,117:220}.get(code)

def read_confirm_key(memory):
    config=memory.u64(memory.base+0xC5DCD0+0x9B8)
    if not 0x10000<config<0x7FFFFFFFFFFF:raise RuntimeError('游戏确认键设置尚未载入')
    block=memory.read(config+0x40,16)
    index=struct.unpack_from('<i',block,12)[0]
    if index!=-1:
        pointer=struct.unpack_from('<Q',block)[0]
        if not 0<=index<64 or not 0x10000<pointer<0x7FFFFFFFFFFF:raise RuntimeError('游戏确认键设置格式无效')
        block=memory.read(pointer+index*16,16)
    for code in struct.unpack_from('<2i',block):
        vk=game_key_to_vk(code)
        if vk is not None and vk not in (0x1B,0x77):return vk
    raise RuntimeError('未找到可用的键盘确认键，请在游戏设置中为确认操作绑定 Enter 等按键')

class GameInput:
    def __init__(self,memory):
        self.pid=memory.pid;self.confirm_key=read_confirm_key(memory)
        self.held=None;self.release_at=0

    def foreground(self):
        pid=W.DWORD();window=u.GetForegroundWindow()
        if not window:return False
        u.GetWindowThreadProcessId(window,C.byref(pid))
        return pid.value==self.pid

    def cancelled(self):
        return bool(u.GetAsyncKeyState(0x77)&0x8000 or self.foreground() and
                    (u.GetAsyncKeyState(0x1B)&0x8000 or u.GetAsyncKeyState(0x02)&0x8000))

    def _send(self,vk,up=False):
        scan=u.MapVirtualKeyW(vk,0)
        if not scan:raise RuntimeError('无法转换游戏按键')
        flags=8|(2 if up else 0)|(1 if vk in (0x21,0x22,0x23,0x24,0x25,0x26,0x27,0x28,0x2D,0x2E) else 0)
        event=INPUT(type=1,ki=KEYBDINPUT(wScan=scan,dwFlags=flags))
        if u.SendInput(1,C.byref(event),C.sizeof(INPUT))!=1:raise RuntimeError('游戏按键发送失败，请检查游戏与工具的运行权限是否一致')

    def press(self,vk,now):
        if self.held or not self.foreground() or u.GetAsyncKeyState(vk)&0x8000:return False
        # Own the key before sending, so cleanup covers errors after key-down.
        self.held=vk;self.release_at=now+0.08
        try:self._send(vk)
        except Exception:
            self.release();raise
        return True

    def release(self):
        if self.held is not None:
            self._send(self.held,up=True);self.held=None

    def tick(self,now):
        if self.held is not None and now>=self.release_at:self.release()
