"""Reward achievements from the user's installed table, separate from trophies."""
from dataclasses import dataclass
from pathlib import Path
import math
import re
import struct

from game_data import cstring, pac_file, tbl_section

ACHIEVEMENT_FLAGS = 0x20B3A0  # Serialized save; live state has an eight-byte prefix.
ACHIEVEMENT_CAPACITY = 512
PROGRESS_SAVE = 0x5100C
SYSTEM_STATE_RVA = 0xC61368
PROGRESS_LIVE = 0x11100
PROGRESS_CAPACITY = 256

# Read-only profile probes: reward eligibility checks flags & 3 == 1;
# claiming a reward ORs bit 1. No Steam APIs or native functions are called.
PROFILE_PROBES = (
    (0x131188, bytes.fromhex('0fb68c30a8b3200080e10380f901')),
    (0x13179C, bytes.fromhex('42808c08a8b3200002')),
)


def plain_text(text):
    return re.sub(r'<[^>]*>', '', text).strip()


def decode_progress(word):
    tag = word >> 30
    if tag == 1:
        payload = word & 0x3FFFFFFF
        return (payload ^ 0x20000000) - 0x20000000
    if tag == 2:
        value = struct.unpack('<f', struct.pack('<I', (word << 2) & 0xFFFFFFFF))[0]
        if not math.isfinite(value) or not -2147483648 <= value < 2147483648:
            return None
        return math.trunc(value)
    return 0


@dataclass(frozen=True)
class Achievement:
    id: int
    category_id: int
    category: str
    objective: int
    target: int
    name: str
    description: str
    rewards: tuple  # (item ID, quantity); game controls delivery and special rewards.
    reward_text: str

    def format_progress(self, value):
        if value is None:
            return '—'
        if self.objective == 95:
            return f'×{value / 10:.1f} / ×{self.target / 10:.1f}'
        return f'{value:,} / {self.target:,}'


@dataclass(frozen=True)
class AchievementState:
    flags: int
    progress: int | None

    @property
    def status(self):
        if self.flags & 2:
            return '已领奖'
        return '可领奖' if self.flags & 1 else '未完成'

    @property
    def complete(self):
        return bool(self.flags & 1)


class AchievementCatalog:
    def __init__(self, game_dir, items):
        data = pac_file(Path(game_dir) / 'pac/steam/table_sc.pac', '/t_achievement.tbl')
        self.achievements = self.parse(data, items)
        self.groups = {}
        for achievement in self.achievements.values():
            self.groups.setdefault((achievement.category_id, achievement.objective), []).append(achievement)
        for group in self.groups.values():
            group.sort(key=lambda achievement: (achievement.target, achievement.id))

    @staticmethod
    def parse(data, items):
        offset, size, count = tbl_section(data, 'AchievementCategoryData')
        if size != 16:
            raise ValueError('成就分类表布局不受支持')
        categories = {}
        for i in range(count):
            category, name = struct.unpack_from('<QQ', data, offset + i * size)
            if category in categories:
                raise ValueError('成就分类重复')
            categories[category] = plain_text(cstring(data, name))
        offset, size, count = tbl_section(data, 'AchievementTableData')
        if size != 96 or not 1 <= count <= ACHIEVEMENT_CAPACITY:
            raise ValueError('奖励成就表布局不受支持')
        result = {}
        ids = set()
        for i in range(count):
            a = offset + i * size
            category, aid, objective, target = struct.unpack_from('<4I', data, a)
            if aid in ids or aid >= ACHIEVEMENT_CAPACITY or objective >= PROGRESS_CAPACITY or not target or category not in categories:
                raise ValueError('奖励成就表含重复或无效记录')
            ids.add(aid)
            name = plain_text(cstring(data, struct.unpack_from('<Q', data, a + 72)[0]))
            description = plain_text(cstring(data, struct.unpack_from('<Q', data, a + 80)[0]))
            rewards = tuple((iid, qty) for p in (40, 52)
                            for iid, qty in [struct.unpack_from('<II', data, a + p)] if qty)
            for iid, qty in rewards:
                if iid not in items or not 1 <= qty <= 9999999:
                    raise ValueError('成就奖励与物品表不匹配')
            if not rewards:
                continue  # Hidden CH entry is not a redeemable reward achievement.
            if not name:
                raise ValueError('奖励成就缺少名称')
            reward_text = '、'.join(f'{items[iid].name} ×{qty:,}' for iid, qty in rewards)
            result[aid] = Achievement(aid, category, categories[category], objective,
                                      target, name, description, rewards, reward_text)
        if not result:
            raise ValueError('没有可读取的奖励成就')
        return result

    def series(self, aid):
        selected = self.achievements[aid]
        return tuple(self.groups[(selected.category_id, selected.objective)])

    def completion_ids(self, aid, include_lower=False):
        if aid not in self.achievements:
            raise ValueError('请选择奖励成就列表中的一项')
        selected = self.achievements[aid]
        if not include_lower:
            return (aid,)
        return tuple(a.id for a in self.series(aid) if a.target <= selected.target)


def plan_completion(catalog, snapshot, aid, include_lower=False):
    """Only set completion bits. Preserve claimed bits and all other game data."""
    ids = catalog.completion_ids(aid, include_lower)
    changes = []
    for item_id in ids:
        state = snapshot[item_id]
        if state.flags not in (0, 1, 3):
            raise RuntimeError('成就状态格式无效，已拒绝修改')
        if not state.complete:
            changes.append((ACHIEVEMENT_FLAGS + item_id, bytes([state.flags | 1])))
    return changes
