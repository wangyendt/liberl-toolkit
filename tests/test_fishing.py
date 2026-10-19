import sys, unittest, struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fishing import (FishingAssist,FISHING_PATCHES,FISHING_RETRY_PATCHES,FISHING_LOOP_PROBES,
                     FISHING_MANAGER_RVA,FISHING_OBJECT_OFFSET,FISHING_VTABLE_RVA)

class Memory:
    base=0x140000000
    size=0x1000000
    def __init__(self):
        self.data={}
        for p in (*FISHING_PATCHES,*FISHING_RETRY_PATCHES):
            for i,b in enumerate(p.original):self.data[self.base+p.rva+i]=b
        self.writes=[];self.failure=None;self.running=True
    def read(self,address,n):return bytes(self.data.get(address+i,0) for i in range(n))
    def patch_byte(self,address,old,new):
        if address==self.failure:raise RuntimeError('simulated write failure')
        if self.read(address,1)!=old:raise RuntimeError('unexpected byte')
        self.data[address]=new[0];self.writes.append((address,old,new))
    def alive(self):return self.running
    def u64(self,address):return struct.unpack('<Q',self.read(address,8))[0]
    def put(self,address,data):
        for i,b in enumerate(data):self.data[address+i]=b

class FishingTests(unittest.TestCase):
    def test_enable_and_restore_only_three_bytes(self):
        mem=Memory();old=dict(mem.data);a=FishingAssist(mem)
        self.assertTrue(a.enable());self.assertTrue(a.enabled);a.verify()
        self.assertEqual(sum(v!=old[k] for k,v in mem.data.items()),3)
        self.assertEqual(len(mem.writes),3);self.assertFalse(a.enable())
        a.disable();self.assertFalse(a.enabled);self.assertEqual(mem.data,old)
    def test_mismatch_refuses_any_write(self):
        mem=Memory();mem.data[mem.base+FISHING_PATCHES[-1].rva]=0x90;a=FishingAssist(mem)
        with self.assertRaises(RuntimeError):a.enable()
        self.assertEqual(mem.writes,[])
    def test_partial_enable_failure_restores_previous_patch(self):
        mem=Memory();old=dict(mem.data);mem.failure=mem.base+FISHING_PATCHES[1].rva;a=FishingAssist(mem)
        with self.assertRaises(RuntimeError):a.enable()
        self.assertEqual(mem.data,old);self.assertEqual(a.owned,[])
    def test_external_patch_is_not_overwritten_on_disable(self):
        mem=Memory();a=FishingAssist(mem);a.enable();p=FISHING_PATCHES[0];mem.data[mem.base+p.rva]=0x90
        with self.assertRaises(RuntimeError):a.disable()
        self.assertEqual(mem.data[mem.base+p.rva],0x90);self.assertEqual(a.owned,[p])
    def test_process_exit_releases_ownership_without_writing(self):
        mem=Memory();a=FishingAssist(mem);a.enable();n=len(mem.writes);mem.running=False;a.close()
        self.assertEqual(a.owned,[]);self.assertEqual(len(mem.writes),n)
    def test_retry_patches_only_change_three_return_immediates_and_restore(self):
        mem=Memory();original=dict(mem.data)
        success=FishingAssist(mem);retry=FishingAssist(mem,FISHING_RETRY_PATCHES)
        success.enable();retry.enable();retry.verify()
        self.assertEqual(sum(v!=original[k] for k,v in mem.data.items()),6)
        retry.disable();self.assertTrue(success.enabled);success.verify()
        success.disable();self.assertEqual(mem.data,original)
    def test_partial_retry_failure_restores_return_targets(self):
        mem=Memory();original=dict(mem.data);p=FISHING_RETRY_PATCHES[1]
        mem.failure=mem.base+p.rva+p.byte_offset
        retry=FishingAssist(mem,FISHING_RETRY_PATCHES)
        with self.assertRaises(RuntimeError):retry.enable()
        self.assertEqual(mem.data,original);self.assertFalse(retry.owned)
    def test_no_bite_detected_from_nested_wait_state_not_stale_catch_result(self):
        mem=Memory();obj=0x100000;manager=0x200000
        mem.put(mem.base+FISHING_MANAGER_RVA,struct.pack('<Q',manager))
        mem.put(manager+FISHING_OBJECT_OFFSET,struct.pack('<Q',obj))
        mem.put(obj,struct.pack('<2Q',mem.base+FISHING_VTABLE_RVA,obj))
        mem.put(obj+0x100,struct.pack('<6i',6,24,60,29,29,20))
        mem.put(obj+0x130,struct.pack('<2I',1,1))
        mem.put(obj+0x18D0,struct.pack('<I',24))
        assist=FishingAssist(mem)
        self.assertTrue(assist.status()['no_bite'])
        mem.put(obj+0x104,struct.pack('<i',26))
        mem.put(obj+0x18D0,struct.pack('<I',26))
        self.assertTrue(assist.status()['no_bite'])
        mem.put(obj+0x100,struct.pack('<i',18))
        self.assertFalse(assist.status()['no_bite'])
        mem.put(obj+0x130,struct.pack('<2I',0,0))
        self.assertFalse(assist.status()['no_bite'])
        mem.put(obj+0x130,struct.pack('<2I',1,1))
        mem.put(obj+0x100,struct.pack('<2i',3,3))
        mem.put(obj+0x18D0,struct.pack('<I',3))
        self.assertTrue(assist.status()['no_bite'])
        mem.put(obj+0x18D0,struct.pack('<I',26))
        self.assertFalse(assist.status()['no_bite'])

class FishingMenuTests(unittest.TestCase):
    def setUp(self):
        self.memory=Memory();self.assist=FishingAssist(self.memory)
        self.obj=0x100000;self.menu=0x200000;self.entries=0x300000
        self.memory.put(self.obj+0x1C0,struct.pack('<Q',self.menu))
        self.memory.put(self.menu,struct.pack('<Q',self.memory.base+0x200))
        self.memory.put(self.menu+0x38,struct.pack('<2Q',self.entries,2))
        self.memory.put(self.menu+0x70,struct.pack('<i',1))
        self.memory.put(self.entries,struct.pack('<2Q',0x400000,0x500000))
        self.memory.put(0x400000+0x18,struct.pack('<2I',0,0)+b'separator')
        self.memory.put(0x500000+0x18,struct.pack('<IIBB',490,1,1,1))
        self.status={'object':self.obj,'state':3}
    def test_separators_are_not_treated_as_bait_flags(self):
        menu=self.assist.bait_menu(self.status,lambda _:3)
        self.assertFalse(menu['rows'][0]['usable'])
        self.assertEqual(menu['rows'][1]['id'],490)
        self.assertTrue(menu['rows'][1]['usable'])
    def test_zero_stock_is_not_selected_even_when_native_flag_is_true(self):
        menu=self.assist.bait_menu(self.status,lambda _:0)
        self.assertFalse(menu['rows'][1]['usable'])
    def test_incompatible_bait_is_not_selected_despite_stock(self):
        self.memory.put(0x500000+0x18,struct.pack('<IIBB',490,1,1,0))
        row=self.assist.bait_menu(self.status,lambda _:3)['rows'][1]
        self.assertTrue(row['suitable']);self.assertFalse(row['usable'])
    def test_invalid_menu_count_or_index_is_rejected(self):
        self.memory.put(self.menu+0x70,struct.pack('<i',99))
        with self.assertRaises(RuntimeError):self.assist.bait_menu(self.status,lambda _:3)
    def test_foreign_vtable_is_rejected(self):
        self.memory.put(self.menu,struct.pack('<Q',0x900000))
        with self.assertRaises(RuntimeError):self.assist.bait_menu(self.status,lambda _:3)
    def test_loop_profile_verification_is_read_only(self):
        for rva,expected in FISHING_LOOP_PROBES:self.memory.put(self.memory.base+rva,expected)
        self.assist.verify_loop_profile();self.assertEqual(self.memory.writes,[])
        self.memory.put(self.memory.base+FISHING_LOOP_PROBES[0][0],b'\x90')
        with self.assertRaises(RuntimeError):self.assist.verify_loop_profile()

    def rod_fixture(self):
        m=self.memory;selector=0x600000;arr=0x610000;assets=0x700000;pack=0x710000;table=0x720000;metadata=0x730000;data=0x740000
        m.put(self.menu+0x68,struct.pack('<Q',selector))
        m.put(selector+0x20,struct.pack('<2Q',arr,2));m.put(selector+0x38,struct.pack('<i',0))
        m.put(arr,struct.pack('<2Q',0x620000,0x630000))
        m.put(0x620000+0x18,struct.pack('<I',480));m.put(0x630000+0x18,struct.pack('<I',482))
        m.put(m.base+0xC5DC88,struct.pack('<Q',assets));m.put(assets+0x120,struct.pack('<Q',pack))
        m.put(pack+8,struct.pack('<Q',table));m.put(table+0x34,struct.pack('<I',0))
        m.put(table+0x20,struct.pack('<Q',metadata));m.put(table+0x10,struct.pack('<Q',data))
        m.put(metadata+0x44,struct.pack('<3I',0,96,2))
        m.put(data,struct.pack('<19I',480,490,*([0]*17)))
        m.put(data+96,struct.pack('<19I',482,491,*([0]*17)))
        return metadata
    def test_rod_choices_match_native_compatibility_table_and_owned_stock(self):
        self.rod_fixture()
        result=self.assist.rod_choices(self.status,lambda iid:1 if iid==482 else 0)
        self.assertEqual(result['rods'],[{'index':1,'id':482,'baits':{491}}])
        self.assertEqual(self.memory.writes,[])
    def test_invalid_rod_table_stride_is_rejected(self):
        metadata=self.rod_fixture();self.memory.put(metadata+0x48,struct.pack('<I',4))
        with self.assertRaises(RuntimeError):self.assist.rod_choices(self.status,lambda _:1)

if __name__=='__main__':unittest.main()
