"""Version-specific fishing assistance; native game states still award catches."""
from dataclasses import dataclass
import struct

@dataclass(frozen=True)
class FishingPatch:
    label: str
    rva: int
    original: bytes
    enabled: bytes
    byte_offset: int

# Three independent single-byte changes, each validated against its full
# instruction. Their original control flow is documented in research.
FISHING_PATCHES=(
    FishingPatch('咬钩自动提竿',0x37FF45,bytes.fromhex('a801'),bytes.fromhex('0c01'),0),
    FishingPatch('不进入收杆失败分支',0x381BCB,bytes.fromhex('724e'),bytes.fromhex('eb4e'),0),
    FishingPatch('收杆成功判定',0x381C2D,bytes.fromhex('7209'),bytes.fromhex('7200'),1),
)
FISHING_MANAGER_RVA=0xC61340
FISHING_OBJECT_OFFSET=0x3078
FISHING_VTABLE_RVA=0xB0BDD8

class FishingAssist:
    def __init__(self,memory):
        self.memory=memory;self.owned=[]

    @property
    def enabled(self):return len(self.owned)==len(FISHING_PATCHES)

    def enable(self):
        if self.enabled:return False
        if self.owned:raise RuntimeError('钓鱼补丁未完全还原，请先关闭自动钓鱼')
        for patch in FISHING_PATCHES:
            if self.memory.read(self.memory.base+patch.rva,len(patch.original))!=patch.original:
                raise RuntimeError(f'{patch.label}的原始代码不匹配，拒绝修改')
        try:
            for patch in FISHING_PATCHES:
                offset=patch.byte_offset
                # Record ownership before writing so even a post-write error
                # (verification/cache/protection) is covered by rollback.
                self.owned.append(patch)
                self.memory.patch_byte(self.memory.base+patch.rva+offset,patch.original[offset:offset+1],patch.enabled[offset:offset+1])
            return True
        except Exception as error:
            try:self.disable()
            except Exception as rollback:raise RuntimeError(f'{error}；钓鱼判定还原失败：{rollback}') from error
            raise

    def disable(self):
        errors=[]
        for patch in list(reversed(self.owned)):
            address=self.memory.base+patch.rva
            try:
                if not self.memory.alive():self.owned.remove(patch);continue
                current=self.memory.read(address,len(patch.original))
                if current==patch.original:self.owned.remove(patch);continue
                if current!=patch.enabled:raise RuntimeError(f'{patch.label}被外部改变，无法安全还原')
                offset=patch.byte_offset
                self.memory.patch_byte(address+offset,patch.enabled[offset:offset+1],patch.original[offset:offset+1])
                if self.memory.read(address,len(patch.original))!=patch.original:raise RuntimeError('还原校验失败')
                self.owned.remove(patch)
            except Exception as error:errors.append(str(error))
        if errors:raise RuntimeError('；'.join(errors))

    def verify(self):
        if self.enabled:
            for patch in self.owned:
                if self.memory.read(self.memory.base+patch.rva,len(patch.enabled))!=patch.enabled:
                    raise RuntimeError('自动钓鱼判定被外部改变，请关闭功能后重新连接')

    def status(self):
        manager=self.memory.u64(self.memory.base+FISHING_MANAGER_RVA)
        if not 0x10000<manager<0x7FFFFFFFFFFF:return None
        obj=self.memory.u64(manager+FISHING_OBJECT_OFFSET)
        if not 0x10000<obj<0x7FFFFFFFFFFF:return None
        if self.memory.u64(obj)!=self.memory.base+FISHING_VTABLE_RVA:
            raise RuntimeError('钓鱼对象结构不匹配')
        if self.memory.u64(obj+8)!=obj:raise RuntimeError('钓鱼状态机校验失败')
        data=self.memory.read(obj+0x100,0x50)
        stack,current=struct.unpack_from('<2I',data,0x30)
        if not stack<4 or not current<4:raise RuntimeError('钓鱼状态索引无效')
        state,pending,frames=struct.unpack_from('<3i',data,current*12)
        if not -1<=state<30 or not -1<=pending<30 or frames<0:raise RuntimeError('钓鱼状态无效')
        result=struct.unpack('<I',self.memory.read(obj+0x18C8,4))[0]
        fish_id=struct.unpack('<I',self.memory.read(obj+0x18B4,4))[0]
        return {'object':obj,'state':state,'pending':pending,'frames':frames,'result':result,'fish_id':fish_id}

    def close(self):self.disable()
