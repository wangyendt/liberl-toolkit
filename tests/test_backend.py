import os, sys, unittest, tempfile, struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import Editor, SaveContainer, checksum
from game_data import (Catalog,DEFAULT_GAME,SAVE_ROOT,INVENTORY,RESOURCES,
                       RAW_SIZE,CHARACTERS,CHAR_STRIDE,Item)


class InventoryValidationTests(unittest.TestCase):
    """Synthetic fixtures keep the regression runnable without game assets."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.data_patch=patch('backend.DATA_ROOT',self.root/'appdata')
        self.data_patch.start()
        catalog=SimpleNamespace(game_dir=self.root,characters={},items={
            201:Item(201,'测试剧情道具','',2,0,99,0),
            373:Item(373,'测试书籍','',2,13,1,0),
            2100:Item(2100,'测试食材','',20,7,99,0),
            2101:Item(2101,'测试食材二','',20,7,10,0),
        })
        with patch('backend.AchievementCatalog'):
            self.ed=Editor(catalog)
        self.container=SaveContainer.__new__(SaveContainer)
        self.container.raw=bytearray(RAW_SIZE)
        self.container.image=b'\x89PNG\r\n\x1a\nsynthetic image'
        self.container.metadata=b''
        struct.pack_into('<2I',self.container.raw,0,2,2)
        struct.pack_into('<9I',self.container.raw,CHARACTERS+CHAR_STRIDE,
                         0,50,0,100,100,20,20,0,200)
        self.source=self.root/'savedata'

    def tearDown(self):
        self.ed.close();self.data_patch.stop();self.temp.cleanup()

    def write_save(self,qty,flags):
        struct.pack_into('<HH',self.container.raw,INVENTORY+2100*4,qty,flags)
        self.source.write_bytes(self.container.encode())
        return self.source.read_bytes()

    def test_open_accepts_all_low_five_flag_bits_without_changing_source(self):
        for flags in range(0x20):
            with self.subTest(flags=hex(flags)):
                original=self.write_save(9,flags)
                self.ed.open_save(self.source)
                self.assertEqual(self.ed.inventory()[2100],(9,flags))
                self.assertFalse(self.ed.dirty)
                self.assertEqual(self.source.read_bytes(),original)

    def test_item_edit_export_and_undo_preserve_existing_flags(self):
        for flags in (0x10,0x11,0x13,0x17,0x1C,0x1F):
            with self.subTest(flags=hex(flags)):
                original=self.write_save(9,flags)
                self.ed.open_save(self.source)
                self.ed.backup=self.root/'fixture-backup.zip'
                before=bytes(self.ed.container.raw)
                self.ed.set_item(2100,10)
                encoded=SaveContainer(self.ed.container.encode())
                self.assertEqual(struct.unpack_from('<HH',encoded.raw,INVENTORY+2100*4),(10,flags|3))
                changed={i for i,(a,b) in enumerate(zip(before,encoded.raw)) if a!=b}
                self.assertTrue(changed<=set(range(8,12))|set(range(INVENTORY+2100*4,INVENTORY+2100*4+4)))
                self.ed.undo()
                self.assertEqual(bytes(self.ed.container.raw),before)
                self.assertEqual(self.source.read_bytes(),original)

    def test_bulk_edits_preserve_high_flag_bit(self):
        struct.pack_into('<HH',self.container.raw,INVENTORY+373*4,0,0x10)
        self.write_save(9,0x1F)
        self.ed.open_save(self.source)
        self.ed.backup=self.root/'fixture-backup.zip'
        self.ed.refill();self.ed.add_jack()
        self.assertEqual(self.ed.inventory()[2100],(99,0x1F))
        self.assertEqual(self.ed.inventory()[373],(1,0x13))
        self.ed.validate()

    def test_unknown_flag_bits_are_rejected_with_item_details(self):
        for bit in range(5,16):
            with self.subTest(bit=bit):
                original=self.write_save(9,1<<bit)
                with self.assertRaisesRegex(RuntimeError,f'物品 2100.*测试食材.*数量 9.*0x{1<<bit:04X}'):
                    self.ed.open_save(self.source)
                self.assertIsNone(self.ed.mode)
                self.assertEqual(self.source.read_bytes(),original)

    def test_excess_quantity_still_rejected(self):
        self.write_save(9999,0x1F)
        self.ed.open_save(self.source)
        original=self.write_save(10000,0x1F)
        with self.assertRaisesRegex(RuntimeError,'物品 2100.*数量 10000.*0x001F'):
            self.ed.open_save(self.source)
        self.assertIsNone(self.ed.mode)
        self.assertEqual(self.source.read_bytes(),original)

    def test_selected_batch_caps_each_item_skips_readonly_and_undoes_once(self):
        struct.pack_into('<HH',self.container.raw,INVENTORY+201*4,4,0x10)
        struct.pack_into('<HH',self.container.raw,INVENTORY+373*4,0,0x10)
        original=self.write_save(9,0x1C)
        self.ed.open_save(self.source)
        before=bytes(self.ed.container.raw)
        with patch('backend.backup_saves',return_value=self.root/'backup.zip') as backup:
            self.assertTrue(self.ed.set_items([373,2100,201,373],99))
            backup.assert_called_once()
        self.assertEqual(self.ed.inventory()[373],(1,0x13))
        self.assertEqual(self.ed.inventory()[2100],(99,0x1F))
        self.assertEqual(self.ed.inventory()[201],(4,0x10))
        self.assertEqual(self.ed.inventory()[2101],(0,0))
        self.assertEqual(len(self.ed.history),1)
        self.assertEqual(len(self.ed.history[0][1]),2)
        decoded=SaveContainer(self.ed.container.encode())
        allowed=set(range(8,12))|set(range(INVENTORY+373*4,INVENTORY+373*4+4))|set(range(INVENTORY+2100*4,INVENTORY+2100*4+4))
        changed={i for i,(a,b) in enumerate(zip(before,decoded.raw)) if a!=b}
        self.assertTrue(changed<=allowed)
        self.ed.undo()
        self.assertEqual(bytes(self.ed.container.raw),before)
        self.assertEqual(self.source.read_bytes(),original)

    def test_selected_batch_zero_preserves_flags_and_noop_creates_no_history(self):
        self.write_save(9,0x1F)
        self.ed.open_save(self.source)
        self.ed.backup=self.root/'backup.zip'
        self.assertTrue(self.ed.set_items([2100,2101],0))
        self.assertEqual(self.ed.inventory()[2100],(0,0x1F))
        self.assertFalse(self.ed.set_items([2100,2101],0))
        self.assertEqual(len(self.ed.history),1)

    def test_selected_batch_invalid_requests_never_write(self):
        self.write_save(9,0x1F)
        self.ed.open_save(self.source)
        before=bytes(self.ed.container.raw)
        with patch.object(self.ed,'_write') as write,patch('backend.backup_saves') as backup:
            for ids,qty in [([],1),([201],1),([2100,99999],1),([2100],-1),([2100],1000),([2100],1.5)]:
                with self.subTest(ids=ids,qty=qty),self.assertRaises(ValueError):
                    self.ed.set_items(ids,qty)
            write.assert_not_called();backup.assert_not_called()
        self.assertEqual(bytes(self.ed.container.raw),before)
        self.assertFalse(self.ed.history)

    def test_selected_batch_rolls_back_earlier_items_on_write_failure(self):
        self.write_save(9,0x1F)
        self.ed.open_save(self.source)
        self.ed.backup=self.root/'backup.zip'
        before=bytes(self.ed.container.raw)
        original_write=self.ed._write;calls=0
        def fail_second(offset,data):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('simulated batch failure')
            original_write(offset,data)
        with patch.object(self.ed,'_write',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'simulated batch failure'):
                self.ed.set_items([373,2100],99)
        self.assertEqual(bytes(self.ed.container.raw),before)
        self.assertFalse(self.ed.history)

class SaveEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=Path(os.environ.get('LIBERL_TEST_SAVE',str(SAVE_ROOT/'save065/savedata')))
        if not (DEFAULT_GAME/'pac/steam/table_sc.pac').is_file() or not cls.source.is_file():
            raise unittest.SkipTest('Integration tests require a locally owned game and a test save; no game data is distributed')
        cls.catalog=Catalog()
        cls.original=cls.source.read_bytes()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.data_patch=patch('backend.DATA_ROOT',Path(self.temp.name)/'appdata');self.data_patch.start()
        self.ed=Editor(self.catalog)
        self.ed.open_save(self.source)
        self.ed.backup=Path(self.temp.name)/'backup.zip'  # Tests never replace user saves.
    def tearDown(self):self.ed.close();self.data_patch.stop();self.temp.cleanup()
    def test_item_identity_from_actual_game_table(self):
        item=self.catalog.items[373]
        self.assertIn('赌博师杰克',item.name)
        self.assertEqual(item.cap,1)
        self.assertEqual(item.category,'书籍')
    def test_roundtrip_preserves_every_payload_byte(self):
        c=self.ed.container;decoded=SaveContainer(c.encode())
        self.assertEqual(decoded.raw,c.raw);self.assertEqual(decoded.image,c.image);self.assertEqual(decoded.metadata,c.metadata)
    def test_book_add_changes_only_one_inventory_entry_and_checksum(self):
        before=bytes(self.ed.container.raw)
        self.assertEqual(self.ed.inventory()[373],(0,0));self.assertTrue(self.ed.add_jack())
        encoded=self.ed.container.encode();after=bytes(SaveContainer(encoded).raw)
        allowed=set(range(8,12))|set(range(INVENTORY+373*4,INVENTORY+373*4+4))
        changed={i for i,(a,b) in enumerate(zip(before,after)) if a!=b}
        self.assertTrue(changed);self.assertTrue(changed<=allowed)
        self.assertEqual(struct.unpack_from('<HH',after,INVENTORY+373*4),(1,3))
        self.assertEqual(struct.unpack_from('<I',after,8)[0],checksum(after))
        self.assertFalse(self.ed.add_jack());self.ed.undo();self.assertEqual(bytes(self.ed.container.raw),before)
        self.assertEqual(self.source.read_bytes(),self.original)
    def test_bad_quantity_and_protected_items_never_write(self):
        before=bytes(self.ed.container.raw)
        for iid,qty in [(373,2),(373,-1),(201,1),(310,10),(2681,1)]:
            with self.assertRaises(ValueError):self.ed.set_item(iid,qty)
        self.assertEqual(bytes(self.ed.container.raw),before)
    def test_resource_limits_and_undo(self):
        old=self.ed.resources();self.ed.set_resource(8,9999999)
        self.assertEqual(self.ed.resources()[8],9999999);self.ed.undo();self.assertEqual(self.ed.resources(),old)
        with self.assertRaises(ValueError):self.ed.set_resource(0,100000)
        with self.assertRaises(ValueError):self.ed.set_resource(-1,100)
    def test_all_books_keeps_existing_items_unchanged(self):
        before=self.ed.inventory();self.ed.add_jack(True);after=self.ed.inventory()
        for iid in self.catalog.items:
            if iid in range(372,386):self.assertEqual(after[iid][0],1)
            else:self.assertEqual(after[iid],before[iid])
    def test_character_values_use_current_maximum(self):
        c=self.ed.characters()[0];old=c['values'][3]
        self.ed.set_character(c['offset'],3,1);self.assertEqual(self.ed.characters()[0]['values'][3],1)
        self.ed.undo();self.assertEqual(self.ed.characters()[0]['values'][3],old)
        with self.assertRaises(ValueError):self.ed.set_character(c['offset'],3,c['values'][4]+1)
        with self.assertRaises(ValueError):self.ed.set_character(c['offset'],4,99999)
    def test_export_refuses_to_overwrite_source_or_existing_file(self):
        with self.assertRaises(ValueError):self.ed.export(self.source)
        target=Path(self.temp.name)/'edited.sora2';self.ed.add_jack();self.ed.export(target)
        self.assertEqual(SaveContainer(target.read_bytes()).raw[INVENTORY+373*4],1)
        with self.assertRaises(ValueError):self.ed.export(target)
    def test_checksum_corruption_is_rejected(self):
        import zstandard as z
        a,n,c,m,end=struct.unpack_from('<5Q',self.original)
        raw=bytearray(self.ed.container.raw);raw[100]^=1;packed=z.ZstdCompressor().compress(raw)
        bad=struct.pack('<5Q',40,len(packed),40+len(packed),m,40+len(packed)+m)+packed+self.original[c:]
        with self.assertRaisesRegex(ValueError,'校验失败'):SaveContainer(bad)
    def test_new_slot_refuses_existing_and_reserved_slots(self):
        import backend
        root=Path(self.temp.name)/'saves';root.mkdir();(root/'save000').mkdir();(root/'save000/marker').write_bytes(b'untouched')
        with patch.object(backend,'SAVE_ROOT',root):
            slot,path=self.ed.install_new_slot();self.assertEqual(slot,1)
            self.assertEqual((root/'save000/marker').read_bytes(),b'untouched')
            self.assertEqual(SaveContainer(path.read_bytes()).raw,self.ed.container.raw)
    def test_transaction_rolls_back_on_write_failure(self):
        old=bytes(self.ed.container.raw);original_write=self.ed._write;calls=0
        def failing_write(offset,data):
            nonlocal calls
            calls+=1
            if calls==2:raise RuntimeError('simulated failure')
            original_write(offset,data)
        with patch.object(self.ed,'_write',failing_write):
            with self.assertRaises(RuntimeError):self.ed.apply('test rollback',[(RESOURCES,struct.pack('<I',1)),(RESOURCES+4,struct.pack('<I',2))])
        self.assertEqual(bytes(self.ed.container.raw),old)

if __name__=='__main__':unittest.main()
