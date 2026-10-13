"""Checked Windows process-memory API, including reversible single-byte patches."""
import ctypes as C
from ctypes import wintypes as W
import struct

k = C.WinDLL('kernel32', use_last_error=True)
class PROCESSENTRY(C.Structure):
    _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',C.c_size_t),('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',W.LONG),('dwFlags',W.DWORD),('szExeFile',W.WCHAR*260)]
class MODULEENTRY(C.Structure):
    _fields_=[('dwSize',W.DWORD),('th32ModuleID',W.DWORD),('th32ProcessID',W.DWORD),('GlblcntUsage',W.DWORD),('ProccntUsage',W.DWORD),('modBaseAddr',C.c_void_p),('modBaseSize',W.DWORD),('hModule',W.HMODULE),('szModule',W.WCHAR*256),('szExePath',W.WCHAR*260)]
k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];k.CreateToolhelp32Snapshot.restype=W.HANDLE
k.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(PROCESSENTRY)];k.Process32FirstW.restype=W.BOOL
k.Process32NextW.argtypes=[W.HANDLE,C.POINTER(PROCESSENTRY)];k.Process32NextW.restype=W.BOOL
k.Module32FirstW.argtypes=[W.HANDLE,C.POINTER(MODULEENTRY)];k.Module32FirstW.restype=W.BOOL
k.Module32NextW.argtypes=[W.HANDLE,C.POINTER(MODULEENTRY)];k.Module32NextW.restype=W.BOOL
k.CloseHandle.argtypes=[W.HANDLE];k.CloseHandle.restype=W.BOOL
k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE
k.ReadProcessMemory.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)];k.ReadProcessMemory.restype=W.BOOL
k.WriteProcessMemory.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)];k.WriteProcessMemory.restype=W.BOOL
k.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL
k.VirtualProtectEx.argtypes=[W.HANDLE,C.c_void_p,C.c_size_t,W.DWORD,C.POINTER(W.DWORD)];k.VirtualProtectEx.restype=W.BOOL
k.FlushInstructionCache.argtypes=[W.HANDLE,C.c_void_p,C.c_size_t];k.FlushInstructionCache.restype=W.BOOL

def processes():
    h=k.CreateToolhelp32Snapshot(2,0)
    if h == C.c_void_p(-1).value: raise C.WinError(C.get_last_error())
    result=[]
    try:
        e=PROCESSENTRY();e.dwSize=C.sizeof(e)
        ok=k.Process32FirstW(h,C.byref(e))
        while ok:
            if e.szExeFile.lower() == 'sora_2nd.exe': result.append(e.th32ProcessID)
            ok=k.Process32NextW(h,C.byref(e))
    finally:k.CloseHandle(h)
    return result

class ProcessMemory:
    def __init__(self,pid):
        self.pid=pid; self.handle=None
        h=k.CreateToolhelp32Snapshot(0x18,pid)
        if h == C.c_void_p(-1).value: raise C.WinError(C.get_last_error())
        try:
            e=MODULEENTRY();e.dwSize=C.sizeof(e)
            ok=k.Module32FirstW(h,C.byref(e));found=False
            while ok:
                if e.szModule.lower()=='sora_2nd.exe':
                    self.base=e.modBaseAddr;self.size=e.modBaseSize;self.path=e.szExePath;found=True;break
                ok=k.Module32NextW(h,C.byref(e))
            if not found: raise RuntimeError('未找到游戏主模块')
        finally:k.CloseHandle(h)
        self.handle=k.OpenProcess(0x438,False,pid)
        if not self.handle: raise RuntimeError('无法连接游戏。若游戏以管理员身份运行，请用同样权限启动修改器。')

    def read(self,address,size):
        if not self.handle: raise RuntimeError('游戏连接已关闭')
        b=C.create_string_buffer(size);n=C.c_size_t()
        if not k.ReadProcessMemory(self.handle,address,b,size,C.byref(n)) or n.value != size:
            raise RuntimeError('游戏内存读取失败，请重新连接')
        return b.raw

    def write(self,address,data):
        if not self.handle: raise RuntimeError('游戏连接已关闭')
        b=C.create_string_buffer(data);n=C.c_size_t()
        if not k.WriteProcessMemory(self.handle,address,b,len(data),C.byref(n)) or n.value!=len(data):
            raise RuntimeError('游戏内存写入失败')
        if self.read(address,len(data)) != data:
            raise RuntimeError('写入后校验失败；游戏可能正在更新这个数值')

    def u64(self,address):return struct.unpack('<Q',self.read(address,8))[0]

    def patch_byte(self,address,expected,replacement):
        if len(expected)!=1 or len(replacement)!=1:raise ValueError('代码补丁必须为单字节')
        if self.read(address,1)!=expected:raise RuntimeError('钓鱼代码已被其他工具改变，拒绝覆盖')
        old=W.DWORD()
        if not k.VirtualProtectEx(self.handle,address,1,0x40,C.byref(old)):
            raise RuntimeError('无法临时写入钓鱼判定代码')
        try:
            self.write(address,replacement)
            if not k.FlushInstructionCache(self.handle,address,1):
                raise RuntimeError('无法刷新游戏指令缓存')
        finally:
            restored=W.DWORD()
            if not k.VirtualProtectEx(self.handle,address,1,old.value,C.byref(restored)):
                raise RuntimeError('无法恢复游戏代码页保护')

    def alive(self):
        c=W.DWORD()
        return bool(self.handle and k.GetExitCodeProcess(self.handle,C.byref(c)) and c.value==259)

    def close(self):
        if self.handle:k.CloseHandle(self.handle);self.handle=None

    def __del__(self):self.close()
