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
# Only while the full loop owns these patches: the no-bite branch in
# 0x38A920 returns through native reel-in state 24 instead of scene exit 26.
FISHING_RETRY_PATCHES=(
    FishingPatch('无咬钩提示返回目标',0x38AD6D,bytes.fromhex('c786d01800001a000000'),bytes.fromhex('c786d018000018000000'),6),
    FishingPatch('无咬钩返回回调',0x38AD8A,bytes.fromhex('41b81a000000'),bytes.fromhex('41b818000000'),2),
    FishingPatch('无咬钩返回状态',0x38ADB1,bytes.fromhex('c74486081a000000'),bytes.fromhex('c744860818000000'),4),
)
FISHING_MANAGER_RVA=0xC61340
FISHING_OBJECT_OFFSET=0x3078
FISHING_VTABLE_RVA=0xB0BDD8
FISHING_LOOP_PROBES=((0x37D332,bytes.fromhex('48634a70')),
                     (0x37D3BF,bytes.fromhex('430fb78c814c8a1200')),
                     (0x379223,bytes.fromhex('48634838')))

class FishingAssist:
    def __init__(self,memory,patches=FISHING_PATCHES):
        self.memory=memory;self.patches=patches;self.owned=[]

    @property
    def enabled(self):return len(self.owned)==len(self.patches)

    def enable(self):
        if self.enabled:return False
        if self.owned:raise RuntimeError('钓鱼补丁未完全还原，请先关闭自动钓鱼')
        for patch in self.patches:
            if self.memory.read(self.memory.base+patch.rva,len(patch.original))!=patch.original:
                raise RuntimeError(f'{patch.label}的原始代码不匹配，拒绝修改')
        try:
            for patch in self.patches:
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
        # Both native no-bite call sites use the same dialog resource: selection
        # returns to 3, while the waiting timeout normally leaves through 26.
        # The result flag may still describe the preceding catch.
        no_bite=False
        if state==29 and current>0:
            parent,parent_pending,_=struct.unpack_from('<3i',data,(current-1)*12)
            target=struct.unpack('<I',self.memory.read(obj+0x18D0,4))[0]
            no_bite=(parent==6 and parent_pending==target and target in (24,26) or
                     parent==3 and parent_pending==target==3)
        return {'object':obj,'state':state,'pending':pending,'frames':frames,'result':result,'fish_id':fish_id,'no_bite':no_bite}

    def verify_loop_profile(self):
        for rva,expected in FISHING_LOOP_PROBES:
            if self.memory.read(self.memory.base+rva,len(expected))!=expected:
                raise RuntimeError('钓鱼选饵菜单与支持版本不匹配')

    def bait_menu(self,status,quantity):
        """Read the native menu; never change selection or inventory in memory."""
        if status['state']!=3:raise RuntimeError('当前不在选饵界面')
        def pointer(value):return 0x10000<value<0x7FFFFFFFFFFF
        menu=self.memory.u64(status['object']+0x1C0)
        if not pointer(menu):return None
        vtable=self.memory.u64(menu)
        if not self.memory.base<=vtable<self.memory.base+self.memory.size:
            raise RuntimeError('鱼饵菜单结构不匹配')
        entries,count=struct.unpack('<2Q',self.memory.read(menu+0x38,16))
        selected=struct.unpack('<i',self.memory.read(menu+0x70,4))[0]
        if count>256 or not -1<=selected<max(1,count):raise RuntimeError('鱼饵菜单索引无效')
        if not count:return {'selected':-1,'rows':[]}
        if not pointer(entries):raise RuntimeError('鱼饵菜单条目无效')
        rows=[]
        for index in range(count):
            entry=self.memory.u64(entries+index*8)
            if not pointer(entry):raise RuntimeError('鱼饵条目结构无效')
            iid,kind,usable,special=struct.unpack('<IIBB',self.memory.read(entry+0x18,10))
            # Separator entries have a different layout after their zero kind.
            if kind!=1 or not 0<iid<5000:
                rows.append({'index':index,'id':0,'quantity':0,'usable':False,'suitable':False,'compatible':False});continue
            if usable>1 or special>1:raise RuntimeError('鱼饵可用标记无效')
            stock=quantity(iid)
            rows.append({'index':index,'id':iid,'quantity':stock,'suitable':bool(usable and stock>0),
                         'usable':bool(usable and special and stock>0),'compatible':bool(special)})
        return {'selected':selected,'rows':rows}

    def rod_choices(self,status,quantity):
        def pointer(value):
            if not 0x10000<value<0x7FFFFFFFFFFF:raise RuntimeError('鱼竿选择数据尚未载入')
            return value
        menu=pointer(self.memory.u64(status['object']+0x1C0))
        selector=pointer(self.memory.u64(menu+0x68))
        entries,count=struct.unpack('<2Q',self.memory.read(selector+0x20,16))
        selected=struct.unpack('<i',self.memory.read(selector+0x38,4))[0]
        if not 0<count<=32 or not 0<=selected<count:raise RuntimeError('鱼竿选择索引无效')
        pointer(entries)
        # This is the same native rod/bait table consulted at 0x37D3CC.
        assets=pointer(self.memory.u64(self.memory.base+0xC5DC88))
        pack=pointer(self.memory.u64(assets+0x120));table=pointer(self.memory.u64(pack+8))
        section=struct.unpack('<I',self.memory.read(table+0x34,4))[0]
        if section>100:raise RuntimeError('鱼竿表索引无效')
        metadata=pointer(self.memory.u64(table+0x20))+section*80
        offset,stride,total=struct.unpack('<3I',self.memory.read(metadata+0x44,12))
        if not 76<=stride<=1024 or not 0<total<=32 or offset>10*1024*1024:raise RuntimeError('鱼竿兼容表结构无效')
        data=pointer(self.memory.u64(table+0x10))+offset
        compatible={}
        for i in range(total):
            values=struct.unpack('<19I',self.memory.read(data+i*stride,76))
            if not 0<values[0]<5000 or any(v>=5000 for v in values[1:]):raise RuntimeError('鱼竿兼容表编号无效')
            compatible[values[0]]=set(values[1:])-{0}
        rods=[]
        for index in range(count):
            entry=pointer(self.memory.u64(entries+index*8))
            iid=struct.unpack('<I',self.memory.read(entry+0x18,4))[0]
            if iid not in compatible:raise RuntimeError('鱼竿菜单与兼容表不匹配')
            if quantity(iid)>0:rods.append({'index':index,'id':iid,'baits':compatible[iid]})
        return {'selected':selected,'rods':rods}

    def close(self):self.disable()
