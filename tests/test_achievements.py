import math
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from achievements import (AchievementCatalog, AchievementState, ACHIEVEMENT_FLAGS,
                          PROGRESS_SAVE, decode_progress, plan_completion)
from backend import Editor, SaveContainer, checksum
from game_data import RAW_SIZE, CHARACTERS, CHAR_STRIDE, INVENTORY


def synthetic_table():
    """Original test data; never include extracted game assets in the repository."""
    categories = [(3, '活动'), (5, '战斗')]
    # A late ID is inserted between two earlier milestones, as in the game.
    rows = [(184, 5, 96, 10, 1, '勇猛突袭10次'),
            (185, 5, 96, 50, 1, '勇猛突袭50次'),
            (220, 5, 96, 150, 1, '勇猛突袭150次'),
            (186, 5, 96, 250, 1, '勇猛突袭250次'),
            (187, 5, 96, 400, 1, '勇猛突袭400次'),
            (190, 5, 97, 450, 1, '讨伐敌人450次'),
            (214, 3, 108, 3, 0, '无奖励的隐藏记录')]
    head = 8 + 2 * 80
    cat_start = head
    row_start = cat_start + len(categories) * 16
    data = bytearray(row_start + len(rows) * 96)
    data[:8] = b'#TBL' + struct.pack('<I', 2)
    for i, (name, offset, size, count) in enumerate([
            ('AchievementCategoryData', cat_start, 16, len(categories)),
            ('AchievementTableData', row_start, 96, len(rows))]):
        pos = 8 + i * 80
        data[pos:pos + len(name)] = name.encode()
        struct.pack_into('<III', data, pos + 68, offset, size, count)
    def text(value):
        pos = len(data)
        data.extend(value.encode('utf-8') + b'\0')
        return pos
    empty = text('')
    for i, (cid, name) in enumerate(categories):
        struct.pack_into('<QQ', data, cat_start + i * 16, cid, text(name))
    for i, (aid, cid, objective, target, reward, name) in enumerate(rows):
        pos = row_start + i * 96
        struct.pack_into('<4I', data, pos, cid, aid, objective, target)
        struct.pack_into('<q', data, pos + 16, 9999)
        for field in (24, 64, 88):
            struct.pack_into('<Q', data, pos + field, empty)
        struct.pack_into('<II', data, pos + 40, reward, 1 if reward else 0)
        struct.pack_into('<Q', data, pos + 72, text(name))
        struct.pack_into('<Q', data, pos + 80, text(f'累计<C3>{target}</C>次。'))
    return bytes(data)


def synthetic_catalog():
    items = {1: SimpleNamespace(name='测试奖励')}
    catalog = AchievementCatalog.__new__(AchievementCatalog)
    catalog.achievements = AchievementCatalog.parse(synthetic_table(), items)
    catalog.groups = {}
    for achievement in catalog.achievements.values():
        catalog.groups.setdefault((achievement.category_id, achievement.objective), []).append(achievement)
    for group in catalog.groups.values():
        group.sort(key=lambda a: (a.target, a.id))
    return catalog


class AchievementLogicTests(unittest.TestCase):
    def setUp(self):
        self.catalog = synthetic_catalog()
        self.snapshot = {aid: AchievementState(0, 4) for aid in self.catalog.achievements}

    def test_only_reward_rows_and_clean_descriptions(self):
        self.assertNotIn(214, self.catalog.achievements)
        self.assertEqual(self.catalog.achievements[220].description, '累计150次。')
        self.assertEqual(self.catalog.achievements[220].reward_text, '测试奖励 ×1')

    def test_single_click_completes_only_selected_stage(self):
        self.assertEqual(plan_completion(self.catalog, self.snapshot, 220), [(ACHIEVEMENT_FLAGS + 220, b'\x01')])

    def test_lower_stages_order_by_threshold_not_id(self):
        changes = plan_completion(self.catalog, self.snapshot, 220, True)
        self.assertEqual([offset - ACHIEVEMENT_FLAGS for offset, _ in changes], [184, 185, 220])
        self.assertNotIn(ACHIEVEMENT_FLAGS + 186, dict(changes))
        self.assertNotIn(ACHIEVEMENT_FLAGS + 190, dict(changes))

    def test_claimed_and_pending_rewards_are_preserved(self):
        self.snapshot[184] = AchievementState(3, 175)
        self.snapshot[185] = AchievementState(1, 175)
        self.assertEqual(plan_completion(self.catalog, self.snapshot, 220, True), [(ACHIEVEMENT_FLAGS + 220, b'\x01')])
        self.snapshot[220] = AchievementState(3, 175)
        self.assertEqual(plan_completion(self.catalog, self.snapshot, 220, True), [])

    def test_hidden_or_unknown_ids_rejected(self):
        for aid in (214, -1, 512):
            with self.assertRaises(ValueError):
                plan_completion(self.catalog, self.snapshot, aid)

    def test_invalid_claim_only_state_rejected(self):
        self.snapshot[220] = AchievementState(2, 175)
        with self.assertRaises(RuntimeError):
            plan_completion(self.catalog, self.snapshot, 220)

    def test_progress_tagged_integers_and_floats(self):
        self.assertEqual(decode_progress(0x400000AF), 175)
        self.assertEqual(decode_progress(0x7FFFFFFF), -1)
        self.assertEqual(decode_progress(0), 0)
        bits = struct.unpack('<I', struct.pack('<f', 20.5))[0]
        self.assertEqual(decode_progress((bits >> 2) | 0x80000000), 20)
        inf_bits = struct.unpack('<I', struct.pack('<f', math.inf))[0]
        self.assertIsNone(decode_progress((inf_bits >> 2) | 0x80000000))

    def test_malformed_tables_rejected(self):
        data = bytearray(synthetic_table())
        struct.pack_into('<I', data, 8 + 80 + 72, 88)
        with self.assertRaises(ValueError):
            AchievementCatalog.parse(data, {1: SimpleNamespace(name='奖励')})
        with self.assertRaises(ValueError):
            AchievementCatalog.parse(synthetic_table(), {})


class AchievementEditingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = synthetic_catalog()
        self.patch_data = patch('backend.DATA_ROOT', self.root / 'appdata')
        self.patch_data.start()
        item = SimpleNamespace(id=1, cap=99)
        game = SimpleNamespace(game_dir=self.root, items={1: item}, characters={0: '测试角色'})
        with patch('backend.AchievementCatalog', return_value=self.catalog):
            self.ed = Editor(game)
        raw = bytearray(RAW_SIZE)
        struct.pack_into('<2I', raw, 0, 2, 2)
        struct.pack_into('<9I', raw, CHARACTERS + CHAR_STRIDE, 0, 50, 0, 100, 100, 20, 20, 0, 200)
        struct.pack_into('<HH', raw, INVENTORY + 4, 5, 3)
        struct.pack_into('<I', raw, PROGRESS_SAVE + 96 * 4, 0x40000004)
        struct.pack_into('<I', raw, 8, checksum(raw))
        container = SaveContainer.__new__(SaveContainer)
        container.raw = raw
        container.image = b'\x89PNG\r\n\x1a\noriginal test image'
        container.metadata = b'original test metadata'
        self.source = self.root / 'savedata'
        self.source.write_bytes(container.encode())
        self.original = self.source.read_bytes()
        self.ed.open_save(self.source)
        self.ed.backup = self.root / 'fixture-backup.zip'

    def tearDown(self):
        self.ed.close()
        self.patch_data.stop()
        self.temp.cleanup()

    def test_edit_export_only_changes_completion_and_checksum(self):
        before = bytes(self.ed.container.raw)
        self.assertEqual(self.ed.complete_achievement(220), 1)
        after = self.ed.container.raw
        self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b], [ACHIEVEMENT_FLAGS + 220])
        self.assertEqual(self.ed.achievement_snapshot()[220], AchievementState(1, 4))
        exported = SaveContainer(self.ed.container.encode())
        self.assertEqual(exported.raw[ACHIEVEMENT_FLAGS + 220], 1)
        self.assertEqual(exported.metadata, b'original test metadata')
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_live_progress_reads_system_not_stale_save_cache(self):
        self.ed.state = 0x30000000
        self.ed.memory = SimpleNamespace(base=0x10000000, u64=lambda _: 0x20000000,
                                        read=lambda address, size: self.progress_block(address, size),
                                        close=lambda: None)
        try:
            with patch.object(self.ed, '_check_live'):
                snapshot = self.ed.achievement_snapshot()
            self.assertEqual(snapshot[220].progress, 175)
        finally:
            self.ed.memory = None

    def test_live_profile_mismatch_never_writes(self):
        self.ed.memory = SimpleNamespace(base=0x10000000, read=lambda address, size: bytes(size), close=lambda: None)
        try:
            with patch.object(self.ed, '_check_live'), patch.object(self.ed, '_write') as write:
                with self.assertRaisesRegex(RuntimeError, '判定与支持版本不匹配'):
                    self.ed.complete_achievement(220)
                write.assert_not_called()
        finally:
            self.ed.memory = None

    def progress_block(self, address, size):
        if address == 0x20000000 + 0x11100:
            data = bytearray(size)
            struct.pack_into('<I', data, 96 * 4, 0x400000AF)
            return bytes(data)
        return bytes(size)

    def test_idempotency_and_undo(self):
        before = bytes(self.ed.container.raw)
        self.ed.complete_achievement(220, True)
        self.assertEqual(self.ed.complete_achievement(220, True), 0)
        self.assertEqual(len(self.ed.history), 1)
        self.ed.undo()
        self.assertEqual(bytes(self.ed.container.raw), before)

    def test_game_claim_conflict_blocks_undo(self):
        self.ed.complete_achievement(220)
        self.ed.container.raw[ACHIEVEMENT_FLAGS + 220] = 3  # Simulated game claim.
        with self.assertRaises(RuntimeError):
            self.ed.undo()
        self.assertEqual(self.ed.achievement_snapshot()[220].status, '已领奖')

    def test_partial_series_failure_rolls_back_all_flags(self):
        before = bytes(self.ed.container.raw)
        write = self.ed._write
        calls = 0
        def fail_second(offset, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('simulated write failure')
            write(offset, data)
        with patch.object(self.ed, '_write', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.ed.complete_achievement(220, True)
        self.assertEqual(bytes(self.ed.container.raw), before)

    def test_corrupt_flags_rejected_without_any_write(self):
        self.ed.container.raw[ACHIEVEMENT_FLAGS + 220] = 0x80
        before = bytes(self.ed.container.raw)
        with self.assertRaises(RuntimeError):
            self.ed.complete_achievement(220)
        self.assertEqual(bytes(self.ed.container.raw), before)


if __name__ == '__main__':
    unittest.main()
