from datetime import datetime
from pathlib import Path
import hashlib, json, os, struct, uuid, zipfile, zlib
import zstandard as zstd
from game_data import *
from win_memory import ProcessMemory, processes
from fishing import FishingAssist
from achievements import (AchievementCatalog, AchievementState, ACHIEVEMENT_FLAGS,
                          ACHIEVEMENT_CAPACITY, PROGRESS_SAVE, PROGRESS_LIVE,
                          PROGRESS_CAPACITY, SYSTEM_STATE_RVA, PROFILE_PROBES,
                          decode_progress, plan_completion)

DATA_ROOT = Path.home() / 'AppData/Local/Sora2Trainer'

def checksum(raw):
    n = len(raw) - 12
    return zlib.crc32(raw[12:], n ^ 0xFFFFFFFF) ^ 0xFFFFFFFF

class SaveContainer:
    def __init__(self, data):
        if len(data) < 40: raise ValueError('存档文件太短')
        a,n,c,m,end = struct.unpack_from('<5Q',data)
        if a!=40 or c!=a+n or end!=c+m or end>len(data) or not 1<n<10*1024*1024:
            raise ValueError('存档封装格式不受支持，请选择 saveNNN 文件夹中的 savedata')
        if zstd.frame_content_size(data[a:c]) != RAW_SIZE:
            raise ValueError('存档解压长度与当前版本不匹配')
        self.raw=bytearray(zstd.ZstdDecompressor().decompress(data[a:c],max_output_size=RAW_SIZE))
        if len(self.raw)!=RAW_SIZE or struct.unpack_from('<2I',self.raw)!=(2,2):
            raise ValueError('此存档不是当前支持的 the 2nd Steam 格式')
        if struct.unpack_from('<I',self.raw,8)[0] != checksum(self.raw):
            raise ValueError('存档校验失败，已拒绝修改')
        self.image=data[c:end];self.metadata=data[end:]
        if not self.image.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('存档缩略图格式无效')

    def encode(self):
        raw=bytearray(self.raw)
        struct.pack_into('<I',raw,8,checksum(raw))
        packed=zstd.ZstdCompressor(level=3).compress(raw)
        end=40+len(packed)+len(self.image)
        result=struct.pack('<5Q',40,len(packed),40+len(packed),len(self.image),end)+packed+self.image+self.metadata
        decoded=SaveContainer(result)
        if decoded.raw!=raw:raise RuntimeError('存档重新编码校验失败')
        return result

    @property
    def title(self):
        try:
            pos=0;parts=[]
            for _ in range(4):
                n=self.metadata[pos];pos+=1
                parts.append(self.metadata[pos:pos+n].decode('utf-8'));pos+=n
            return ' · '.join(parts[1:3])
        except (ValueError,IndexError,UnicodeError): return '已验证的游戏存档'

def atomic_file(path,data,overwrite=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        if overwrite:os.replace(tmp,path)
        else:os.rename(tmp,path)  # Windows rename refuses to replace an existing file.
    finally:
        if tmp.exists():tmp.unlink()

def backup_saves():
    root=DATA_ROOT/'backups';root.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    path=root/(stamp+'-saves.zip')
    with zipfile.ZipFile(path,'x',zipfile.ZIP_DEFLATED) as z:
        for p in SAVE_ROOT.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(SAVE_ROOT))
    return path

class Editor:
    def __init__(self,catalog):
        self.catalog=catalog;self.memory=None;self.container=None;self.source=None
        self.achievement_catalog=AchievementCatalog(catalog.game_dir,catalog.items)
        self.state=None;self.history=[];self.backup=None;self.lock_resources={};self.sustain=set();self.dirty=False;self.fishing=None
        self.audit_dir=DATA_ROOT/'logs';self.audit_dir.mkdir(parents=True,exist_ok=True)
        self.audit_file=self.audit_dir/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.jsonl')

    @property
    def mode(self):return 'live' if self.memory else 'save' if self.container else None

    def close(self):
        self.disable_locks()
        if self.memory:self.memory.close()
        self.memory=None;self.container=None;self.state=None;self.source=None;self.history=[];self.dirty=False;self.backup=None;self.fishing=None

    def connect(self):
        ids=processes()
        if len(ids)!=1:raise RuntimeError('请启动一个 sora_2nd.exe 游戏进程并读入存档，再连接')
        mem=ProcessMemory(ids[0])
        try:
            if hashlib.sha256(Path(mem.path).read_bytes()).hexdigest()!=EXE_SHA256:
                raise RuntimeError(f'游戏版本不匹配。当前支持 Steam 构建 {BUILD}；已拒绝写入。')
            # Verify the loaded serializer call site too, not just the executable on disk.
            if mem.read(mem.base+0x29DAAD,7)!=bytes.fromhex('488b0dac389c00'):
                raise RuntimeError('游戏内存代码与支持版本不匹配')
            self.close();self.memory=mem
            self.state=mem.u64(mem.base+STATE_POINTER_RVA)
            if not 0x10000<self.state<0x7FFFFFFFFFFF:raise RuntimeError('尚未载入游戏存档')
            self.validate()
            self.fishing=FishingAssist(mem)
        except Exception:
            mem.close();self.memory=None;self.state=None;raise

    def open_save(self,path):
        path=Path(path)
        container=SaveContainer(path.read_bytes())
        self.close();self.container=container;self.source=path;self.source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        try:self.validate()
        except Exception:
            self.close();raise

    def _check_live(self):
        if not self.memory.alive():raise RuntimeError('游戏已退出，已停止全部持续修改')
        if self.memory.u64(self.memory.base+STATE_POINTER_RVA)!=self.state:
            self.disable_locks();raise RuntimeError('游戏数据位置已改变，已停止修改，请重新连接')

    def read(self,offset,size):
        if not 0<=offset<=RAW_SIZE-size:raise ValueError('读取范围无效')
        if self.memory:
            self._check_live();return self.memory.read(self._live_address(offset,size),size)
        if self.container:return bytes(self.container.raw[offset:offset+size])
        raise RuntimeError('请先连接游戏或打开存档')

    def _write(self,offset,data):
        if not 0<=offset<=RAW_SIZE-len(data):raise ValueError('写入范围无效')
        if self.memory:
            self._check_live();self.memory.write(self._live_address(offset,len(data)),data)
        elif self.container:
            self.container.raw[offset:offset+len(data)]=data;self.dirty=True
        else:raise RuntimeError('没有载入游戏数据')

    def _live_address(self,offset,size):
        # The serializer rebuilds this block from the system object. Reading or
        # writing the main object's corresponding bytes would only change a cache.
        if PROGRESS_SAVE<=offset and offset+size<=PROGRESS_SAVE+PROGRESS_CAPACITY*4:
            system=self.memory.u64(self.memory.base+SYSTEM_STATE_RVA)
            if not 0x10000<system<0x7FFFFFFFFFFF:raise RuntimeError('成就统计尚未载入')
            return system+PROGRESS_LIVE+offset-PROGRESS_SAVE
        return self.state+8+offset

    def validate(self):
        if struct.unpack('<2I',self.read(0,8))!=(2,2):raise RuntimeError('游戏数据版本无效')
        a=CHARACTERS+CHAR_STRIDE
        iid,level,_,hp,mhp,ep,mep,cp,mcp=struct.unpack('<9I',self.read(a,36))
        if iid!=0 or not 1<=level<=200 or not 0<hp<=mhp<=1000000 or not ep<=mep<=10000 or not cp<=mcp<=200:
            # A dead protagonist is valid too.
            if not (iid==0 and 1<=level<=200 and 0<=hp<=mhp<=1000000 and 0<mhp and ep<=mep<=10000 and cp<=mcp<=200):
                raise RuntimeError('角色结构校验失败，拒绝修改')
        inv=self.read(INVENTORY,5000*4)
        for item in self.catalog.items.values():
            qty,flags=struct.unpack_from('<HH',inv,item.id*4)
            # Real saves also use bit 0x10 (e.g. flags 0x17 / 0x1F).
            # Keep all five observed bits; item edits preserve existing flags.
            if qty>9999 or flags&~0x1F:
                raise RuntimeError(f'背包结构校验失败，拒绝修改：物品 {item.id}（{item.name}），数量 {qty}，标记 0x{flags:04X}')
        if any(v>9999999 for v in self.resources()):raise RuntimeError('资源结构校验失败')

    def audit(self,event):
        event={'time':datetime.now().astimezone().isoformat(),'mode':self.mode,'pid':self.memory.pid if self.memory else None,'state':hex(self.state) if self.state else None,'source':str(self.source) if self.source else None,**event}
        with self.audit_file.open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush()

    def apply(self,label,changes):
        if not changes:return False
        if self.memory:self.validate()
        normalized=[]
        for offset,new in changes:
            old=self.read(offset,len(new))
            if old!=new:normalized.append((offset,old,new))
        if not normalized:return False
        if not self.backup:self.backup=backup_saves()
        self.audit({'action':'prepare','label':label,'backup':str(self.backup),'changes':[{'offset':hex(a),'old':old.hex(),'new':new.hex()} for a,old,new in normalized]})
        written=[]
        try:
            for offset,old,new in normalized:
                if self.read(offset,len(old))!=old:raise RuntimeError('游戏数值已变化，请刷新后再修改')
                self._write(offset,new);written.append((offset,old,new))
        except Exception as error:
            rollback_failed=[]
            for offset,old,new in reversed(written):
                try:
                    if self.read(offset,len(new))==new:self._write(offset,old)
                    else:rollback_failed.append(hex(offset))
                except Exception:rollback_failed.append(hex(offset))
            self.audit({'action':'failed','label':label,'error':str(error),'rollback_failed':rollback_failed})
            if rollback_failed:raise RuntimeError(f'{error}；部分数值已变化，无法自动撤销，详情见日志') from error
            raise
        self.history.append((label,normalized));self.audit({'action':'committed','label':label});return True

    def undo(self):
        self.disable_locks()
        if not self.history:raise RuntimeError('本次连接没有可撤销的修改')
        label,changes=self.history[-1]
        for a,old,new in changes:
            if self.read(a,len(new))!=new:raise RuntimeError('游戏已经改变该数值，无法安全撤销；可从备份恢复存档')
        done=[]
        try:
            for a,old,new in reversed(changes):self._write(a,old);done.append((a,old,new))
        except Exception:
            for a,old,new in reversed(done):
                if self.read(a,len(old))==old:self._write(a,new)
            raise
        self.history.pop();self.audit({'action':'undo','label':label});return label

    def inventory(self):
        b=self.read(INVENTORY,5000*4)
        return {iid:struct.unpack_from('<HH',b,iid*4) for iid in self.catalog.items}

    def set_item(self,iid,qty):
        item=self.catalog.items[iid]
        if item.protected:raise ValueError('剧情道具、服装及特殊资源仅供查看')
        if not 0<=qty<=item.cap:raise ValueError(f'数量应为 0～{item.cap}')
        a=INVENTORY+iid*4;oldqty,flags=struct.unpack('<HH',self.read(a,4))
        if qty>oldqty:flags|=3  # The game's NEW and possession-history bits.
        return self.apply(f'{item.name} → {qty}',[(a,struct.pack('<HH',qty,flags))])

    def add_jack(self,all_volumes=False):
        ids=range(372,386) if all_volumes else [373];changes=[]
        for iid in ids:
            a=INVENTORY+iid*4;qty,flags=struct.unpack('<HH',self.read(a,4))
            if qty==0:changes.append((a,struct.pack('<HH',1,flags|3)))
        return self.apply('补齐赌博师杰克全14卷' if all_volumes else '补入赌博师杰克第二卷',changes)

    def refill(self):
        changes=[]
        for iid,(qty,flags) in self.inventory().items():
            item=self.catalog.items[iid]
            if qty>0 and (item.category_id==1 or item.category_id==20 and item.subcategory in [7,28,29]) and not item.protected and item.cap>1 and qty<item.cap:
                changes.append((INVENTORY+iid*4,struct.pack('<HH',item.cap,flags)))
        return self.apply('补满已拥有的消耗品和食材',changes)

    def resources(self):return list(struct.unpack('<9I',self.read(RESOURCES,36)))

    def set_resource(self,index,value):
        if not 0<=index<9:raise ValueError('资源编号无效')
        maximum=9999999 if index==8 else 99999
        if not 0<=value<=maximum:raise ValueError(f'数量应为 0～{maximum}')
        return self.apply(f'{RESOURCE_NAMES[index]} → {value}',[(RESOURCES+index*4,struct.pack('<I',value))])

    def characters(self):
        result=[]
        for slot in range(1,11):
            a=CHARACTERS+slot*CHAR_STRIDE
            values=struct.unpack('<18I',self.read(a,72));iid,level=values[:2]
            if iid in range(8) and 1<=level<=200:
                result.append({'offset':a,'id':iid,'name':self.catalog.characters.get(iid,str(iid)),'values':values})
        return result

    def set_character(self,offset,field,value):
        char=next((c for c in self.characters() if c['offset']==offset),None)
        if char is None:raise ValueError('角色记录已改变，请刷新')
        limits={2:99999999,3:char['values'][4],5:char['values'][6],7:char['values'][8]}
        if field not in limits or not 0<=value<=limits[field]:raise ValueError('数值超出角色允许范围')
        name={2:'EXP',3:'HP',5:'EP',7:'CP'}[field]
        return self.apply(f'{char["name"]} {name} → {value}',[(offset+field*4,struct.pack('<I',value))])

    def restore_party(self):
        changes=[]
        for c in self.characters():
            a=c['offset'];v=c['values']
            for field,maximum in [(3,v[4]),(5,v[6]),(7,v[8])]:changes.append((a+field*4,struct.pack('<I',maximum)))
        return self.apply('恢复角色 HP / EP / CP',changes)

    def set_fishing(self,enabled):
        if not self.memory or not self.fishing:raise RuntimeError('自动钓鱼仅支持实时连接游戏')
        self._check_live()
        if enabled:
            if not self.backup:self.backup=backup_saves()
            changed=self.fishing.enable()
            self.audit({'action':'fishing_on','backup':str(self.backup)})
        else:
            changed=bool(self.fishing.owned);self.fishing.disable();self.audit({'action':'fishing_off'})
        return changed

    def achievement_snapshot(self):
        flags=self.read(ACHIEVEMENT_FLAGS,ACHIEVEMENT_CAPACITY)
        progress=self.read(PROGRESS_SAVE,PROGRESS_CAPACITY*4)
        if any(flags[i] not in (0,1,3) for i in self.achievement_catalog.achievements):
            raise RuntimeError('成就状态格式无效，已拒绝修改')
        return {aid:AchievementState(flags[aid],decode_progress(struct.unpack_from('<I',progress,a.objective*4)[0]))
                for aid,a in self.achievement_catalog.achievements.items()}

    def complete_achievement(self,aid,include_lower=False,sync_progress=True):
        if self.memory:
            self._check_live()
            for rva,original in PROFILE_PROBES:
                if self.memory.read(self.memory.base+rva,len(original))!=original:
                    raise RuntimeError('游戏成就判定与支持版本不匹配，已拒绝修改')
        changes=plan_completion(self.achievement_catalog,self.achievement_snapshot(),aid,include_lower,sync_progress)
        completed=sum(ACHIEVEMENT_FLAGS<=offset<ACHIEVEMENT_FLAGS+ACHIEVEMENT_CAPACITY for offset,_ in changes)
        name=self.achievement_catalog.achievements[aid].name
        self.apply(f'完成奖励成就：{name}（{completed} 项；同步进度：{sync_progress}）',changes)
        return completed

    def disable_locks(self):
        self.lock_resources={};self.sustain=set()
        if self.fishing and self.fishing.owned:
            self.fishing.disable();self.audit({'action':'fishing_off'})

    def tick(self):
        if not self.memory:return
        if not self.lock_resources and not self.sustain and not (self.fishing and self.fishing.owned):return
        self._check_live()
        if self.fishing:self.fishing.verify()
        # Revalidate only the headers; full inventory checks happen on user writes.
        if struct.unpack('<2I',self.read(0,8))!=(2,2):raise RuntimeError('游戏状态无效')
        for i,value in self.lock_resources.items():
            data=struct.pack('<I',value)
            if self.read(RESOURCES+i*4,4)!=data:self._write(RESOURCES+i*4,data)
        for c in self.characters():
            v=c['values']
            if not 0<=v[3]<=v[4]<=1000000 or not 0<=v[5]<=v[6]<=10000 or not 0<=v[7]<=v[8]<=200:
                raise RuntimeError('角色数值发生异常，已停止自动恢复')
            for field in self.sustain:
                if field not in [3,5,7]:raise ValueError('持续修改字段无效')
                value=v[field+1]
                if v[field]!=value:self._write(c['offset']+field*4,struct.pack('<I',value))

    def export(self,path):
        if not self.container:raise RuntimeError('存档导出需要先打开一个存档')
        path=Path(path)
        if self.source and path.resolve()==self.source.resolve():raise ValueError('请导出到新文件；原存档保留')
        if path.exists():raise ValueError('目标已存在，请选一个新文件名')
        atomic_file(path,self.container.encode())
        self.audit({'action':'export','path':str(path)});self.dirty=False
        return path

    def install_new_slot(self):
        if not self.container:raise RuntimeError('请先打开并修改一个存档')
        # Only ordinary manual slots. Never touch auto/chapter/system slots.
        for i in range(170):
            folder=SAVE_ROOT/f'save{i:03d}'
            try:folder.mkdir(parents=True,exist_ok=False)
            except FileExistsError:continue
            try:
                path=folder/'savedata';atomic_file(path,self.container.encode())
            except Exception:
                if not any(folder.iterdir()):folder.rmdir()
                raise
            self.audit({'action':'install_new_slot','slot':i,'path':str(path)});self.dirty=False
            return i,path
        raise RuntimeError('没有空的手动存档槽，请导出到新文件')
