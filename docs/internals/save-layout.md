# 本机 Steam 构建 25721473 数据布局

适配目标 SHA-256：`cab62e5872222efb2aaf272be47f14263db4e7132ad7df5255db8efbee9959ea`。

| 字段 | 解压存档偏移 | 实时地址 |
|---|---:|---|
| 状态版本 | 0 / 4，各 uint32，值 2 | state + 8 / 12 |
| CRC | 8，uint32 | 实时无需更新，游戏保存时计算 |
| 主角记录数组 | 0x1142F0，间距 672；slot 0 是空记录 | state + 8 + offset |
| 背包数组 | 0x128A44，5,000 条，每条 uint16 qty + uint16 flags | state + 8 + offset |
| 七种耀晶石、石块、米拉 | 0x20B620，连续 9 个 uint32 | state + 8 + offset |
| 数据长度 | 0x20BD50 | 此范围包括运行时与存档的差异区域，不直接当存档导出 |

全局主状态指针在 `sora_2nd.exe + 0xC61360`。主对象前 8 字节是 vtable 指针，因此实时数据位置比解压存档多 8。

确认依据：`0x29DAAD` 从该全局读取 RCX，调用 `0x439790`；该序列化函数以 RCX 为源对象，从源对象 + 8 复制数据到保存结构，单独重建若干运行时区域。`0x439CB0` 将源 + 0x114598 的角色记录复制到存档 + 0x114590。实际读取的主角 ID、等级、HP 和物品与最近两个存档一致；实时 EXP 随游戏进程变化，可区分存档缓存。

第二卷编号 373；其背包条目在解压存档 `0x129018`。添加时 qty=1，flags 保留原位并 OR 3（NEW / possession history）；与已拥有第一、三、四、五、六卷的 flags=3 一致。玩家在游戏中确认了第二卷出现。

背包 flags 的已观测有效位为 `0x1F`。本机 `save015`、`save016` 等通过 CRC 校验的存档中，编号 2100～2304 的部分条目包含 `0x10` 位，出现 `0x11`、`0x13`、`0x17`、`0x1F` 等值；该位具体含义尚未确认。旧版以 `0x0F` 校验会误拒绝这些存档。读取时接受低五位，修改数量时保留原 flags；仍拒绝超过 9999 的数量及低五位以外的标记，并在错误中报告物品编号、名称、数量和 flags。

CRC 初始值是 payload 长度 `0x20BD44`，对 raw[12:] 使用 reflected CRC-32 多项式 `0xEDB88320`，不加最终 XOR。用 zlib 的接口等价表达为 `crc32(raw[12:], n ^ 0xFFFFFFFF) ^ 0xFFFFFFFF`，n=len(raw)-12。由 EXE `0x43D5C3` 起的 checksum 循环和真实存档对照确认。

外层封装头：5 个 little-endian uint64：数据偏移(40)、压缩长度、PNG 偏移、PNG 长度、说明文字偏移。Zstandard 解压长度固定为 0x20BD50。说明文字不属于 CRC 范围。

物品 FPAC 中 `table_sc/t_item.tbl` 的 ItemTableData：记录长 256。ID +0，category +40，subcategory +41，stack cap +204，price +208，name utf8 offset +224，description offset +232。共 1,382 条有效记录。与同名 1st 表格有额外字段，未直接复用其布局。
