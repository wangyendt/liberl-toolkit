import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fishing import FishingAssist,FISHING_PATCHES

class Memory:
    base=0x140000000
    def __init__(self):
        self.data={}
        for p in FISHING_PATCHES:
            for i,b in enumerate(p.original):self.data[self.base+p.rva+i]=b
        self.writes=[];self.failure=None;self.running=True
    def read(self,address,n):return bytes(self.data.get(address+i,0) for i in range(n))
    def patch_byte(self,address,old,new):
        if address==self.failure:raise RuntimeError('simulated write failure')
        if self.read(address,1)!=old:raise RuntimeError('unexpected byte')
        self.data[address]=new[0];self.writes.append((address,old,new))
    def alive(self):return self.running

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

if __name__=='__main__':unittest.main()
