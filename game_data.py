"""Read the installed game's FPAC and #TBL tables; never modify game assets."""
from dataclasses import dataclass
from pathlib import Path
import os, re, struct

def discover_game_dir():
    override=os.environ.get('LIBERL_GAME_DIR')
    if override:return Path(override).expanduser()
    steam_roots=[]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as key:
            steam_roots.append(Path(winreg.QueryValueEx(key,'SteamPath')[0]))
    except (ImportError,OSError):pass
    steam_roots.append(Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Steam')
    libraries=list(steam_roots)
    for root in steam_roots:
        path=root/'steamapps/libraryfolders.vdf'
        if path.is_file():
            try:
                text=path.read_text(encoding='utf-8')
                libraries.extend(Path(p.replace('\\\\','\\')) for p in re.findall(r'"path"\s*"([^"\n]+)"',text))
            except OSError:pass
    for root in libraries:
        game=root/'steamapps/common/Trails in the Sky 2nd Chapter'
        if (game/'sora_2nd.exe').is_file() and (game/'pac/steam/table_sc.pac').is_file():return game
    return steam_roots[0]/'steamapps/common/Trails in the Sky 2nd Chapter'

DEFAULT_GAME = discover_game_dir()
SAVE_ROOT = Path.home() / 'Saved Games/Falcom/Trails in the Sky 2nd Chapter/savedata'
BUILD = '25721473'
EXE_SHA256 = 'cab62e5872222efb2aaf272be47f14263db4e7132ad7df5255db8efbee9959ea'
RAW_SIZE = 0x20BD50
INVENTORY = 0x128A44
ITEM_QUANTITY_MAX = 9999
CHARACTERS = 0x1142F0
CHAR_STRIDE = 672
RESOURCES = 0x20B620
STATE_POINTER_RVA = 0xC61360
RESOURCE_NAMES = ['地之耀晶石', '水之耀晶石', '火之耀晶石', '风之耀晶石', '时之耀晶石', '空之耀晶石', '幻之耀晶石', '耀晶石块', '米拉']

def cstring(data, offset):
    if not 0 <= offset < len(data):
        raise ValueError('物品表的文本位置无效')
    end = data.find(b'\0', offset)
    if end < 0:
        raise ValueError('物品表的文本没有结束标记')
    return data[offset:end].decode('utf-8', errors='strict')

def pac_file(path, suffix):
    data = Path(path).read_bytes()
    if data[:4] != b'FPAC':
        raise ValueError('游戏资源不是 FPAC 格式')
    count = struct.unpack_from('<I', data, 4)[0]
    if count > 10000 or 16 + count * 32 > len(data):
        raise ValueError('游戏资源索引无效')
    matches = []
    for i in range(count):
        _, _, name, size, offset = struct.unpack_from('<IIQQQ', data, 16 + i * 32)
        if cstring(data, name).endswith(suffix):
            if offset + size > len(data):
                raise ValueError('游戏资源长度无效')
            matches.append(data[offset:offset+size])
    if len(matches) != 1:
        raise ValueError(f'无法唯一找到 {suffix}')
    return matches[0]

def tbl_section(data, name):
    if data[:4] != b'#TBL':
        raise ValueError('游戏表格格式不受支持')
    n = struct.unpack_from('<I', data, 4)[0]
    if n > 100 or len(data) < 8 + n * 80:
        raise ValueError('游戏表格索引无效')
    for i in range(n):
        a = 8 + i * 80
        if cstring(data, a) == name:
            offset, size, count = struct.unpack_from('<III', data, a + 68)
            if offset + size * count > len(data):
                raise ValueError('游戏表格记录越界')
            return offset, size, count
    raise ValueError(f'游戏表格缺少 {name}')

@dataclass(frozen=True)
class Item:
    id: int
    name: str
    description: str
    category_id: int
    subcategory: int
    cap: int
    price: int

    @property
    def category(self):
        if self.subcategory == 13: return '书籍'
        if self.subcategory == 30: return '钓竿'
        if self.category_id == 2: return '剧情道具'
        if self.category_id == 0: return '资源'
        if self.category_id == 20: return {5:'素材',7:'食材',28:'鱼饵',29:'鱼类'}.get(self.subcategory,'材料')
        return {1:'消耗品', 11:'武器', 12:'防具', 13:'鞋子', 14:'饰品', 15:'服装', 16:'发色', 17:'装饰', 18:'结晶回路', 19:'机壳'}.get(self.category_id, f'分类 {self.category_id}')

    @property
    def protected(self):
        # Resources, quest state, attachments and orbment covers remain read-only.
        # Costumes and hair colors use their table's stack cap (currently 1).
        return self.category_id == 2 and self.subcategory not in [13,30] or self.category_id in [0,17,19] or not 0 < self.cap <= ITEM_QUANTITY_MAX

class Catalog:
    def __init__(self, game_dir=DEFAULT_GAME):
        self.game_dir = Path(game_dir)
        data = pac_file(self.game_dir / 'pac/steam/table_sc.pac', '/t_item.tbl')
        offset, size, count = tbl_section(data, 'ItemTableData')
        if size != 256:
            raise ValueError('此版本的物品表布局不受支持')
        self.items = {}
        for i in range(count):
            a = offset + i * size
            iid = struct.unpack_from('<I', data, a)[0]
            name = cstring(data, struct.unpack_from('<Q', data, a + 224)[0])
            desc = cstring(data, struct.unpack_from('<Q', data, a + 232)[0])
            cap, price = struct.unpack_from('<ii', data, a + 204)
            item = Item(iid, name, desc, data[a+40], data[a+41], cap, price)
            if iid in self.items or not 0 < iid < 5000:
                raise ValueError('物品表含重复或无效编号')
            self.items[iid] = item
        if self.items.get(373) is None or '赌博师杰克' not in self.items[373].name:
            raise ValueError('此物品表与第二卷编号不匹配')
        data = pac_file(self.game_dir / 'pac/steam/table_sc.pac', '/t_name.tbl')
        offset, size, count = tbl_section(data, 'NameTableData')
        self.characters = {}
        for i in range(count):
            a = offset + i * size
            iid = struct.unpack_from('<I', data, a)[0]
            if iid != 65535 and iid not in self.characters:
                self.characters[iid] = cstring(data, struct.unpack_from('<Q', data, a + 8)[0])
