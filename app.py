"""Chinese desktop trainer for the locally verified Steam build."""
import json, os, sys, traceback
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QGridLayout,QLabel,QPushButton,QLineEdit,QComboBox,QCheckBox,QSpinBox,QTableView,QHeaderView,QListWidget,QStackedWidget,QFileDialog,QMessageBox,QPlainTextEdit,QFrame,QGroupBox,QAbstractItemView)
from game_data import *
from backend import Editor,DATA_ROOT,SaveContainer,backup_saves,atomic_file
from win_memory import processes
from achievement_ui import AchievementsPage

STYLE='''
QWidget { font-family: "Microsoft YaHei UI"; font-size: 13px; color:#263e51; }
QMainWindow, QWidget#content { background:#edf4f8; }
QFrame#rail { background:#203e56; }
QFrame#rail QLabel { color:#c5d7e4; }
QLabel#brand { color:#ffffff; font-size:24px; font-weight:700; }
QLabel#title { font-size:26px; font-weight:700; color:#203e56; }
QLabel#subtitle, QLabel#muted { color:#647e90; }
QLabel#bookTitle { font-size:23px; font-weight:700; }
QLabel#status { background:#dceaf2; padding:10px; border-radius:5px; }
QListWidget#nav { border:0; background:transparent; color:#c5d7e4; font-size:15px; outline:0; }
QListWidget#nav::item { color:#c5d7e4; padding:14px 16px; border-radius:5px; margin:3px 0; }
QListWidget#nav::item:selected { color:white; background:#327caa; }
QGroupBox { background:#ffffff; border:1px solid #d3e1ea; border-radius:7px; margin-top:17px; padding:18px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:18px; padding:0 6px; }
QPushButton { background:#ffffff; color:#254c68; border:1px solid #bdd0dd; border-radius:5px; padding:9px 15px; }
QPushButton:hover { background:#e4f0f8; border-color:#327caa; }
QPushButton:pressed { background:#cedfeb; }
QPushButton#primary { color:white; background:#327caa; border:1px solid #327caa; font-weight:600; }
QPushButton#primary:hover { background:#286b96; }
QPushButton:disabled { color:#a1afb9; border-color:#d4dfe6; background:#e7edf1; }
QLineEdit,QSpinBox,QComboBox { background:white; border:1px solid #bdd0dd; border-radius:4px; padding:7px; selection-background-color:#327caa; }
QLineEdit:focus,QSpinBox:focus,QComboBox:focus { border:1px solid #327caa; }
QCheckBox { spacing:7px; }
QCheckBox::indicator { width:16px; height:16px; }
QTableView,QListWidget,QPlainTextEdit { background:white; border:1px solid #d3e1ea; border-radius:5px; gridline-color:#e1eaf0; selection-background-color:#d6eaf6; selection-color:#203e56; }
QHeaderView::section { background:#e5eff5; color:#3f6077; border:0; border-bottom:1px solid #cbdce7; padding:10px; font-weight:600; }
QStatusBar { background:#dfeaf1; }
'''

VERSION='1.5.1'
ASSET_ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets'

def configure_ui(app):
    # Explicit registration also lets the offscreen verifier render Chinese.
    for name in ['msyh.ttc','msyhbd.ttc','georgia.ttf','georgiab.ttf']:
        path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if path.exists():QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont('Microsoft YaHei',10));app.setStyle('Fusion');app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(str(ASSET_ROOT/'liberl-toolkit.ico')))

def label(text,kind=None,wrap=False):
    w=QLabel(text)
    if kind:w.setObjectName(kind)
    w.setWordWrap(wrap)
    return w

def button(text,callback,primary=False):
    w=QPushButton(text);w.clicked.connect(callback)
    if primary:w.setObjectName('primary')
    return w

def row(*widgets):
    layout=QHBoxLayout()
    for w in widgets:
        if isinstance(w,str):layout.addWidget(label(w))
        else:layout.addWidget(w)
    return layout

class ItemModel(QAbstractTableModel):
    HEADERS=['编号','物品名称','分类','持有','上限','编辑']
    def __init__(self,catalog):
        super().__init__();self.items=sorted(catalog.items.values(),key=lambda x:x.id);self.quantities={}
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.items)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.HEADERS)
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole and orientation==Qt.Horizontal:return self.HEADERS[section]
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return
        it=self.items[index.row()];qty=self.quantities.get(it.id,(0,0))[0]
        if role==Qt.UserRole:return it
        if role==Qt.DisplayRole:return [str(it.id),it.name,it.category,str(qty) if self.quantities else '—',str(it.cap),'只读' if it.protected else '可修改'][index.column()]
        if role==Qt.ForegroundRole and it.protected:return QColor('#8c9daa')
        if role==Qt.ToolTipRole:return it.description
        if role==Qt.TextAlignmentRole and index.column() in [0,3,4]:return int(Qt.AlignCenter)
    def update(self,quantities):
        self.quantities=quantities
        self.dataChanged.emit(self.index(0,0),self.index(len(self.items)-1,5))

class ItemFilter(QSortFilterProxyModel):
    def __init__(self):super().__init__();self.text='';self.category='全部分类';self.owned=False
    def filterAcceptsRow(self,r,parent):
        model=self.sourceModel();it=model.items[r]
        return (not self.text or self.text in f'{it.id} {it.id:x} {it.name} {it.category}'.lower()) and (self.category=='全部分类' or it.category==self.category) and (not self.owned or model.quantities.get(it.id,(0,0))[0]>0)
    def refresh(self):self.invalidateRowsFilter()

class Trainer(QMainWindow):
    def __init__(self,game_dir=DEFAULT_GAME,auto_connect=False):
        super().__init__();self.setWindowTitle(f'Liberl Toolkit · 空之轨迹 the 2nd v{VERSION}');self.resize(1190,820);self.setMinimumSize(1040,740)
        self.catalog=Catalog(game_dir);self.editor=Editor(self.catalog);self.loading=False;self.resource_inputs=[];self.resource_locks=[];self.char_rows=[];self.selected_items=[];self.pages=[]
        self._build();self.refresh_saves();self.refresh_backups()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(200)
        self.refresh_timer=QTimer(self);self.refresh_timer.timeout.connect(self.poll);self.refresh_timer.start(2000)
        self.fishing_timer=QTimer(self);self.fishing_timer.timeout.connect(self.tick_fishing_loop);self.fishing_timer.start(50)
        QShortcut(QKeySequence('Ctrl+Z'),self,activated=lambda:self.act(self.editor.undo))
        QShortcut(QKeySequence('Ctrl+F'),self,activated=self.focus_search)
        if auto_connect:QTimer.singleShot(0,self.connect_game)

    def _build(self):
        base=QWidget();outer=QHBoxLayout(base);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0);self.setCentralWidget(base)
        rail=QFrame();rail.setObjectName('rail');rail.setFixedWidth(207);rl=QVBoxLayout(rail);rl.setContentsMargins(20,30,20,22)
        emblem=QLabel();emblem.setPixmap(QIcon(str(ASSET_ROOT/'liberl-toolkit.ico')).pixmap(64,64));rl.addWidget(emblem)
        rl.addWidget(label('利贝尔工具箱','brand'));rl.addWidget(label('Liberl Toolkit · the 2nd'));rl.addSpacing(25)
        self.nav=QListWidget();self.nav.setObjectName('nav');rl.addWidget(self.nav,1)
        rl.addWidget(label(f'Steam · {BUILD}\nv{VERSION} · 本地修改',wrap=True));outer.addWidget(rail)
        content=QWidget();content.setObjectName('content');cl=QVBoxLayout(content);cl.setContentsMargins(28,24,28,18);cl.setSpacing(15)
        cl.addWidget(label('空之轨迹 the 2nd 修改器','title'));cl.addWidget(label('管理物品、资源、角色与成就，开启钓鱼辅助，编辑与备份存档。','subtitle'))
        self.connect_btn=button('连接游戏',self.connect_game,True);self.disconnect_btn=button('断开连接',self.disconnect);self.undo_btn=button('撤销上次修改',lambda:self.act(self.editor.undo));self.stop_btn=button('关闭全部持续修改',self.stop_locks)
        cl.addLayout(row(self.connect_btn,self.disconnect_btn,self.undo_btn,self.stop_btn));self.status=label('尚未连接游戏','status');cl.addWidget(self.status)
        self.stack=QStackedWidget();cl.addWidget(self.stack,1);outer.addWidget(content,1)
        self.page_indexes={}
        for key,title,maker in [('items','物品与装备',self.items_page),('resources','米拉与耀晶石',self.resources_page),('characters','角色与恢复',self.characters_page),('fishing','自动钓鱼',self.fishing_page),('achievements','游戏内成就',self.achievements_page),('saves','存档与备份',self.saves_page),('logs','操作记录',self.logs_page)]:
            self.page_indexes[key]=len(self.pages);self.nav.addItem(title)
            page=maker();self.stack.addWidget(page);self.pages.append(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.setCurrentRow(0)
        self.message=label('修改前自动备份。关闭工具后，持续恢复和锁定会停止。','muted',True);cl.addWidget(self.message)
        self.update_enabled()

    def page(self):
        w=QWidget();v=QVBoxLayout(w);v.setContentsMargins(0,0,0,0);v.setSpacing(14);return w,v

    def items_page(self):
        w,v=self.page();self.search=QLineEdit();self.search.setPlaceholderText('搜索物品名称、分类或编号（Ctrl+F）');self.category=QComboBox();self.category.addItems(['全部分类']+sorted({x.category for x in self.catalog.items.values()}));self.owned=QCheckBox('仅看已拥有')
        v.addLayout(row(self.search,self.category,self.owned));self.model=ItemModel(self.catalog);self.proxy=ItemFilter();self.proxy.setSourceModel(self.model)
        self.item_table=QTableView();self.item_table.setModel(self.proxy);self.item_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.item_table.setSelectionMode(QAbstractItemView.ExtendedSelection);self.item_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.item_table.verticalHeader().hide();self.item_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.item_table.setColumnWidth(0,65);self.item_table.setColumnWidth(2,100);self.item_table.setColumnWidth(3,65);self.item_table.setColumnWidth(4,65);self.item_table.setColumnWidth(5,70);v.addWidget(self.item_table,1)
        self.item_selection=label('已选 0 项','muted')
        v.addLayout(row(self.item_selection,button('全选当前结果',self.item_table.selectAll),button('清除选择',self.item_table.clearSelection)))
        self.item_desc=label('选择物品查看描述；按 Ctrl 点选多项，按 Shift 连选，Ctrl+A 全选当前结果。','muted',True);v.addWidget(self.item_desc)
        self.item_qty=QSpinBox();self.item_qty.setRange(0,ITEM_QUANTITY_MAX);self.item_qty.setFixedWidth(100);self.item_write=button('设置数量',self.write_item,True);self.refill_btn=button('补满已有消耗品 / 食材 / 鱼饵',lambda:self.act(self.editor.refill));v.addLayout(row('目标数量',self.item_qty,self.item_write,self.refill_btn))
        v.addWidget(label('批量设置时，超出各物品上限的数量按上限设置，只读物品自动跳过。一次撤销可还原整批修改。','muted',True))
        for signal in [self.search.textChanged,self.category.currentTextChanged,self.owned.toggled]:signal.connect(self.filter_items)
        self.item_table.selectionModel().selectionChanged.connect(self.select_item);return w

    def filter_items(self,*_):
        self.proxy.text=self.search.text().strip().lower();self.proxy.category=self.category.currentText();self.proxy.owned=self.owned.isChecked();self.proxy.refresh()

    def focus_search(self):self.nav.setCurrentRow(self.page_indexes['items']);self.search.setFocus();self.search.selectAll()

    def achievements_page(self):
        self.achievement_page=AchievementsPage(self)
        return self.achievement_page

    def select_item(self,*_):
        indexes=self.item_table.selectionModel().selectedRows()
        self.selected_items=[self.model.items[self.proxy.mapToSource(index).row()] for index in indexes]
        editable=sum(not it.protected for it in self.selected_items)
        self.item_selection.setText(f'已选 {len(indexes)} 项 · 可修改 {editable} 项 · 只读 {len(indexes)-editable} 项')
        self.item_write.setText(f'设置选中 {editable} 项' if len(indexes)>1 else '设置数量')
        enabled=bool(self.editor.mode and editable)
        self.item_write.setEnabled(enabled);self.item_qty.setEnabled(enabled)
        if not indexes:
            self.item_desc.setText('选择物品查看描述；按 Ctrl 点选多项，按 Shift 连选，Ctrl+A 全选当前结果。')
        elif len(indexes)==1:
            it=self.selected_items[0];qty=self.model.quantities.get(it.id,(0,0))[0]
            self.item_qty.setRange(0,max(0,min(it.cap,ITEM_QUANTITY_MAX)));self.item_qty.setValue(min(qty,self.item_qty.maximum()))
            hint='\n此分类仅供查看。' if it.protected else '\n服装、发色仅修改拥有数量；穿戴请在游戏中操作。' if it.category_id in (15,16) else ''
            self.item_desc.setText(f'{it.name} · 编号 {it.id}\n{it.description}'+hint)
        else:
            self.item_qty.setRange(0,ITEM_QUANTITY_MAX)
            names='、'.join(it.name for it in self.selected_items[:5])+(' 等' if len(indexes)>5 else '')
            self.item_desc.setText(f'{names}\n统一设置目标数量，每项按各自上限处理；只读物品自动跳过。')

    def write_item(self):
        ids=[it.id for it in self.selected_items];qty=self.item_qty.value()
        if len(ids)==1:self.act(lambda:self.editor.set_item(ids[0],qty))
        elif ids:self.act(lambda:self.editor.set_items(ids,qty))

    def resources_page(self):
        w,v=self.page();box=QGroupBox('设置资源数量');g=QGridLayout(box)
        g.addWidget(label('资源'),0,0);g.addWidget(label('当前数量'),0,1);g.addWidget(label('目标数量'),0,2);g.addWidget(label('写入后锁定'),0,4)
        self.resource_current=[];self.resource_buttons=[]
        for i,name in enumerate(RESOURCE_NAMES):
            current=label('—');value=QSpinBox();value.setRange(0,9999999 if i==8 else 99999);value.setMinimumWidth(150)
            lock=QCheckBox();lock.toggled.connect(lambda checked,j=i:self.unlock_resource(j,checked));write=button('写入',lambda _,j=i:self.write_resource(j))
            g.addWidget(label(name),i+1,0);g.addWidget(current,i+1,1);g.addWidget(value,i+1,2);g.addWidget(write,i+1,3);g.addWidget(lock,i+1,4)
            self.resource_inputs.append(value);self.resource_locks.append(lock);self.resource_current.append(current);self.resource_buttons.append(write)
        v.addWidget(box);v.addWidget(label('勾选锁定后点击“写入”才开始锁定。取消勾选立即停止。锁定只在实时模式下生效。','muted',True));v.addStretch();return w

    def unlock_resource(self,index,checked):
        if not checked:self.editor.lock_resources.pop(index,None)

    def write_resource(self,index):
        value=self.resource_inputs[index].value()
        def action():
            changed=self.editor.set_resource(index,value)
            if self.editor.mode=='live' and self.resource_locks[index].isChecked():
                self.editor.lock_resources[index]=value;self.editor.audit({'action':'lock_resource','index':index,'value':value})
            return changed
        self.act(action)

    def characters_page(self):
        w,v=self.page();self.char_table=QTableView();self.char_model=CharacterModel();self.char_table.setModel(self.char_model);self.char_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.char_table.setSelectionMode(QAbstractItemView.SingleSelection);self.char_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.char_table.verticalHeader().hide();self.char_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);v.addWidget(self.char_table,1)
        self.char_field=QComboBox();self.char_field.addItems(['HP','EP','CP','EXP']);self.char_value=QSpinBox();self.char_value.setRange(0,99999999);self.char_write=button('写入选中角色',self.write_character,True);v.addLayout(row(self.char_field,self.char_value,self.char_write))
        self.recover_btn=button('恢复所有主角 HP / EP / CP',lambda:self.act(self.editor.restore_party));v.addWidget(self.recover_btn,alignment=Qt.AlignLeft)
        self.sustain_checks=[]
        sustain_row=QHBoxLayout()
        for field,name in [(3,'自动恢复 HP'),(5,'自动恢复 EP'),(7,'自动恢复 CP')]:
            check=QCheckBox(name);check.toggled.connect(lambda checked,f=field:self.sustain(f,checked));sustain_row.addWidget(check);self.sustain_checks.append(check)
        v.addLayout(sustain_row);v.addWidget(label('自动恢复每 0.2 秒补到上限，瞬间致死攻击仍可能生效。包含已登记的八名主角；不修改剧情队伍。EXP 写入后需要游戏结算来更新等级。','muted',True));return w

    def write_character(self):
        indexes=self.char_table.selectionModel().selectedRows()
        if not indexes:self.note('请先选择一个角色。');return
        c=self.char_model.characters[indexes[0].row()];field={'HP':3,'EP':5,'CP':7,'EXP':2}[self.char_field.currentText()]
        self.act(lambda:self.editor.set_character(c['offset'],field,self.char_value.value()))

    def sustain(self,field,checked):
        if checked and self.editor.mode=='live':
            try:
                if not self.editor.backup:self.editor.backup=backup_saves()
                self.editor.sustain.add(field);self.editor.audit({'action':'sustain_on','field':field,'backup':str(self.editor.backup)})
            except Exception as error:self.stop_locks();self.error(error)
        else:self.editor.sustain.discard(field)

    def fishing_page(self):
        w,v=self.page();box=QGroupBox('自动钓鱼成功');bv=QVBoxLayout(box)
        bv.addWidget(label('抛竿，等鱼上钩','bookTitle'))
        bv.addWidget(label('自动提竿并完成收杆。鱼种、尺寸和奖励由游戏正常决定。','muted',True))
        self.fishing_check=QCheckBox('开启自动钓鱼成功');self.fishing_check.toggled.connect(self.toggle_fishing);bv.addWidget(self.fishing_check)
        self.fishing_status=label('连接游戏后可开启。');bv.addWidget(self.fishing_status)
        bv.addWidget(label('选择匹配的鱼竿和鱼饵，然后抛竿；无需按提竿／收杆键。鱼饵按正常规则消耗，结果页面仍由你确认。','muted',True));v.addWidget(box)
        loop_box=QGroupBox('全自动循环');lv=QVBoxLayout(loop_box)
        lv.addWidget(label('在钓鱼点进入鱼竿／鱼饵界面，点击开始，再切回游戏。自动选饵、抛竿、收线、确认奖励并开始下一轮。','muted',True))
        self.fishing_loop_start=button('开始全自动钓鱼',lambda:self.toggle_fishing_loop(True),True)
        self.fishing_loop_stop=button('停止循环（F8）',lambda:self.toggle_fishing_loop(False))
        lv.addLayout(row(self.fishing_loop_start,self.fishing_loop_stop))
        self.fishing_loop_status=label('全自动循环未开启。','muted',True);lv.addWidget(self.fishing_loop_status)
        lv.addWidget(label('优先使用当前鱼竿和鱼饵，不足或没有上钩迹象时自动换饵，必要时切换已拥有的匹配鱼竿。切出游戏暂停；F8、游戏中按 Esc／右键或停止按钮可取消。无可用组合、离开钓鱼界面或长时间无响应时停止。','muted',True))
        v.addWidget(loop_box)
        v.addWidget(label('取消勾选、断开连接或正常退出工具时，会还原钓鱼判定。存档编辑模式不支持自动钓鱼。','muted',True));v.addStretch();return w

    def toggle_fishing_loop(self,enabled):
        try:
            self.editor.set_fishing_loop(enabled)
            self.note('全自动钓鱼已开启，切回游戏后开始；按 F8 取消。' if enabled else '全自动钓鱼已停止。')
        except Exception as error:self.error(error)
        self.sync_fishing_loop()

    def sync_fishing_loop(self):
        running=self.editor.loop_session;live=self.editor.mode=='live'
        self.fishing_loop_start.setEnabled(live and not running);self.fishing_loop_stop.setEnabled(running)
        if self.editor.fishing_loop:self.fishing_loop_status.setText(f'已完成 {self.editor.fishing_loop.rounds} 轮 · {self.editor.fishing_loop.message}')
        self.fishing_check.blockSignals(True)
        self.fishing_check.setChecked(bool(self.editor.fishing and self.editor.fishing.enabled))
        self.fishing_check.blockSignals(False)

    def tick_fishing_loop(self):
        was_running=self.editor.loop_session
        if not was_running:return
        try:self.editor.tick_fishing_loop()
        except Exception as error:self.stop_locks();self.note(f'全自动钓鱼已停止：{error}')
        self.sync_fishing_loop()
        if was_running and not self.editor.loop_session and self.editor.fishing_loop:
            self.note(self.editor.fishing_loop.message)

    def toggle_fishing(self,enabled):
        try:
            self.editor.set_fishing(enabled)
            self.fishing_status.setText('已开启 · 选择鱼竿、鱼饵并抛竿即可。' if enabled else '已关闭 · 钓鱼判定已还原。')
            self.note('自动钓鱼已开启。' if enabled else '自动钓鱼已关闭，原判定已还原。')
        except Exception as error:
            self.fishing_check.blockSignals(True);self.fishing_check.setChecked(bool(self.editor.fishing and self.editor.fishing.owned));self.fishing_check.blockSignals(False)
            self.error(error)

    def saves_page(self):
        w,v=self.page();v.addWidget(label('打开存档后，物品、资源和角色修改先保存在工具中。导出到新槽位后，在游戏中读入该槽位。','muted',True))
        self.save_list=QListWidget();self.save_list.setMaximumHeight(160);v.addWidget(self.save_list)
        v.addLayout(row(button('刷新存档列表',self.refresh_saves),button('打开选中存档',self.open_selected_save,True),button('选择存档文件…',self.open_file)))
        self.export_btn=button('导出到新文件…',self.export_file);self.slot_btn=button('保存到空的手动槽位',self.install_slot,True);v.addLayout(row(self.export_btn,self.slot_btn))
        box=QGroupBox('自动备份');bv=QVBoxLayout(box);self.backup_list=QListWidget();self.backup_list.setMaximumHeight(100);bv.addWidget(self.backup_list)
        bv.addLayout(row(button('备份全部存档',lambda:self.act(backup_saves)),button('提取选中备份…',self.extract_backup),button('打开备份文件夹',self.open_backup_folder)))
        bv.addWidget(label('恢复备份时先提取到一个独立文件夹，再打开其中的 savedata 并保存到空槽位。','muted',True));v.addWidget(box);v.addStretch();return w

    def logs_page(self):
        w,v=self.page();v.addWidget(label('记录本次打开工具后的手动修改、撤销和导出。','muted',True));self.log=QPlainTextEdit();self.log.setReadOnly(True);v.addWidget(self.log,1);v.addWidget(button('打开操作日志文件夹',lambda:os.startfile(str(self.editor.audit_dir))),alignment=Qt.AlignLeft);return w

    def note(self,text):self.message.setText(text);self.log.appendPlainText(text)

    def act(self,callback):
        try:
            result=callback()
            suffix='；记得在游戏中另存一个手动存档。' if self.editor.mode=='live' else '；修改暂存在工具中，请导出或保存到空槽位。' if self.editor.mode=='save' else ''
            if result is False:self.note('当前数量已经符合目标，无需修改。')
            elif isinstance(result,Path):self.note(f'已完成：{result}')
            elif isinstance(result,str):self.note(f'已撤销：{result}')
            else:self.note('修改完成'+suffix)
            self.refresh();self.refresh_backups()
            return True
        except Exception as error:self.error(error);return False

    def error(self,error):
        self.note(str(error));QMessageBox.warning(self,'操作未完成',str(error));self.update_enabled()

    def connect_game(self):
        if not self.allow_switch():return
        try:self.editor.connect();self.initialize_inputs();self.refresh();self.note('已连接游戏。打开物品菜单后即可查看修改。')
        except Exception as error:self.error(error)
        self.update_enabled()

    def allow_switch(self):
        return not self.editor.dirty or QMessageBox.question(self,'尚未导出','有尚未导出的存档修改。放弃这些修改并切换？')==QMessageBox.Yes

    def disconnect(self):
        if not self.allow_switch():return
        self.stop_locks();self.editor.close();self.refresh();self.note('已断开连接。')

    def stop_locks(self):
        try:self.editor.disable_locks()
        except Exception as error:
            self.note(f'还原钓鱼判定未完成：{error}')
            self.fishing_status.setText('判定未完全还原，请取消勾选重试。')
            return False
        for check in self.resource_locks+self.sustain_checks:check.blockSignals(True);check.setChecked(False);check.blockSignals(False)
        self.fishing_check.blockSignals(True);self.fishing_check.setChecked(False);self.fishing_check.blockSignals(False);self.fishing_status.setText('自动钓鱼已关闭。')
        self.note('全部持续修改已关闭。')
        self.sync_fishing_loop()
        return True

    def initialize_inputs(self):
        self.stop_locks()
        for widget,value in zip(self.resource_inputs,self.editor.resources()):widget.setValue(value)

    def refresh(self):
        if self.editor.mode:
            inv=self.editor.inventory();self.model.update(inv);self.filter_items()
            self.achievement_page.refresh(self.editor.achievement_snapshot())
            for widget,value in zip(self.resource_current,self.editor.resources()):widget.setText(f'{value:,}')
            self.char_model.update(self.editor.characters())
            self.status.setText(f'实时连接 · PID {self.editor.memory.pid} · Steam 构建 {BUILD}' if self.editor.mode=='live' else f'存档编辑 · {self.editor.source.parent.name} · {self.editor.container.title}')
        else:
            self.status.setText('尚未连接游戏');self.model.update({});self.char_model.update([])
            self.achievement_page.refresh({})
            for widget in self.resource_current:widget.setText('—')
        self.update_enabled()

    def update_enabled(self):
        active=bool(self.editor.mode);live=self.editor.mode=='live';save=self.editor.mode=='save'
        for w in [self.refill_btn,self.char_write,self.recover_btn,*self.resource_buttons]:w.setEnabled(active)
        self.disconnect_btn.setEnabled(active);self.undo_btn.setEnabled(bool(self.editor.history));self.stop_btn.setEnabled(live)
        self.export_btn.setEnabled(save);self.slot_btn.setEnabled(save)
        for w in self.resource_locks+self.sustain_checks+[self.fishing_check]:w.setEnabled(live)
        editable=any(not it.protected for it in self.selected_items)
        self.item_write.setEnabled(bool(active and editable));self.item_qty.setEnabled(bool(active and editable))
        self.achievement_page.update_enabled()
        self.sync_fishing_loop()

    def tick(self):
        try:self.editor.tick()
        except Exception as error:
            self.stop_locks();self.note(f'持续修改已停止：{error}')

    def poll(self):
        if not self.editor.mode:return
        try:
            # Do not rebuild tables under a user selecting a row every two seconds.
            inv=self.editor.inventory();self.model.update(inv)
            for widget,value in zip(self.resource_current,self.editor.resources()):widget.setText(f'{value:,}')
            self.char_model.update(self.editor.characters())
            self.achievement_page.refresh(self.editor.achievement_snapshot())
        except Exception as error:
            self.stop_locks()
            try:self.editor.close();self.refresh();self.note(f'连接已停止：{error}')
            except Exception as restore_error:self.note(f'连接异常，钓鱼判定仍需还原：{restore_error}')

    def refresh_saves(self):
        self.save_list.clear()
        paths=sorted(SAVE_ROOT.glob('save[0-9][0-9][0-9]/savedata'),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in paths:
            from PySide6.QtWidgets import QListWidgetItem
            try:title=SaveContainer(path.read_bytes()).title
            except Exception:title='不受支持或校验失败'
            it=QListWidgetItem(f'{path.parent.name} · {title}');it.setData(Qt.UserRole,str(path));self.save_list.addItem(it)
        if paths:self.save_list.setCurrentRow(0)

    def load_save(self,path):
        if not self.allow_switch():return
        try:self.editor.open_save(path);self.initialize_inputs();self.refresh();self.note('存档已载入工具。原文件保留，修改后请保存到空槽位。')
        except Exception as error:self.error(error)

    def open_selected_save(self):
        it=self.save_list.currentItem()
        if it:self.load_save(it.data(Qt.UserRole))

    def open_file(self):
        path,_=QFileDialog.getOpenFileName(self,'选择存档 savedata',str(SAVE_ROOT),'游戏存档 (savedata *.sora2);;所有文件 (*)')
        if path:self.load_save(path)

    def export_file(self):
        path,_=QFileDialog.getSaveFileName(self,'导出为新文件',str(DATA_ROOT/'exports/edited.sora2'),'游戏存档 (*.sora2);;所有文件 (*)')
        if path:self.act(lambda:self.editor.export(path))

    def install_slot(self):
        try:
            slot,path=self.editor.install_new_slot();self.note(f'已保存到 {path.parent.name}。在游戏的读取菜单中选择这个手动槽位。原存档保留。');self.refresh_saves()
        except Exception as error:self.error(error)

    def refresh_backups(self):
        self.backup_list.clear();root=DATA_ROOT/'backups';root.mkdir(parents=True,exist_ok=True)
        for path in sorted(root.glob('*.zip'),reverse=True):
            from PySide6.QtWidgets import QListWidgetItem
            it=QListWidgetItem(path.name);it.setData(Qt.UserRole,str(path));self.backup_list.addItem(it)
        if self.backup_list.count():self.backup_list.setCurrentRow(0)

    def open_backup_folder(self):os.startfile(str(DATA_ROOT/'backups'))

    def extract_backup(self):
        it=self.backup_list.currentItem()
        if not it:self.note('请先选择一个备份。');return
        folder=QFileDialog.getExistingDirectory(self,'选择提取目标文件夹',str(DATA_ROOT))
        if not folder:return
        try:
            import zipfile
            dest=Path(folder)/('restored-'+Path(it.data(Qt.UserRole)).stem)
            dest.mkdir(exist_ok=False)
            with zipfile.ZipFile(it.data(Qt.UserRole)) as archive:
                for info in archive.infolist():
                    name=Path(info.filename)
                    if name.is_absolute() or '..' in name.parts:raise ValueError('备份包含无效路径')
                    target=(dest/name).resolve()
                    if not target.is_relative_to(dest.resolve()):raise ValueError('备份路径越界')
                    archive.extract(info,dest)
            self.note(f'已提取到 {dest}；可打开其中的 savedata 编辑并保存到空槽位。');os.startfile(str(dest))
        except Exception as error:self.error(error)

    def closeEvent(self,event):
        if not self.allow_switch():event.ignore();return
        try:self.editor.close();event.accept()
        except Exception as error:self.error(error);event.ignore()

class CharacterModel(QAbstractTableModel):
    HEADERS=['角色','等级','HP','EP','CP','EXP']
    def __init__(self):super().__init__();self.characters=[]
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.characters)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else 6
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole and orientation==Qt.Horizontal:return self.HEADERS[section]
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return
        c=self.characters[index.row()];v=c['values']
        if role==Qt.DisplayRole:return [c['name'],str(v[1]),f'{v[3]} / {v[4]}',f'{v[5]} / {v[6]}',f'{v[7]} / {v[8]}',str(v[2])][index.column()]
        if role==Qt.TextAlignmentRole:return int(Qt.AlignCenter)
    def update(self,characters):
        if [c['id'] for c in self.characters]!=[c['id'] for c in characters]:
            self.beginResetModel();self.characters=characters;self.endResetModel()
        else:
            self.characters=characters
            if characters:self.dataChanged.emit(self.index(0,0),self.index(len(characters)-1,5))

def main():
    app=QApplication(sys.argv);configure_ui(app)
    game_dir=DEFAULT_GAME
    if '--game-dir' in sys.argv:game_dir=Path(sys.argv[sys.argv.index('--game-dir')+1])
    try:
        if '--self-check' in sys.argv:
            window=Trainer(game_dir,auto_connect=False)
            if processes():window.editor.connect()
            else:
                saves=sorted(SAVE_ROOT.glob('save[0-9][0-9][0-9]/savedata'),key=lambda p:p.stat().st_mtime,reverse=True)
                if saves:window.editor.open_save(saves[0])
            if window.editor.mode:window.initialize_inputs()
            window.refresh()
            result={'ok':True,'mode':window.editor.mode or 'idle','items':len(window.catalog.items),'owned_items':sum(qty>0 for qty,flags in window.editor.inventory().values()) if window.editor.mode else None,'pid':window.editor.memory.pid if window.editor.memory else None,'characters':len(window.editor.characters()) if window.editor.mode else 0,'default_page':window.nav.currentItem().text(),'reward_achievements':len(window.editor.achievement_catalog.achievements)}
            from fishing import FISHING_PATCHES
            if window.editor.memory:
                result['fishing_profile_verified']=all(window.editor.memory.read(window.editor.memory.base+p.rva,len(p.original))==p.original for p in FISHING_PATCHES)
            else:
                import hashlib
                exe=(window.catalog.game_dir/'sora_2nd.exe').read_bytes()
                # All three sites are within the verified .text section:
                # RVA 0x1000 corresponds to file offset 0x400 in this hash.
                result['fishing_profile_verified']=hashlib.sha256(exe).hexdigest()==EXE_SHA256 and all(exe[p.rva-0xC00:p.rva-0xC00+len(p.original)]==p.original for p in FISHING_PATCHES)
            DATA_ROOT.mkdir(parents=True,exist_ok=True)
            (DATA_ROOT/'last-self-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            window.editor.close();window.close();return 0
        window=Trainer(game_dir,auto_connect=bool(processes()))
        if '--achievements' in sys.argv:window.nav.setCurrentRow(window.page_indexes['achievements'])
        if '--fishing' in sys.argv or '--auto-fishing' in sys.argv:window.nav.setCurrentRow(window.page_indexes['fishing'])
        if '--auto-fishing' in sys.argv:
            def enable_fishing():
                if window.editor.mode=='live':window.fishing_check.setChecked(True)
            QTimer.singleShot(1000,enable_fishing)
        window.show();return app.exec()
    except Exception as error:
        DATA_ROOT.mkdir(parents=True,exist_ok=True);(DATA_ROOT/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        QMessageBox.critical(None,'无法启动修改器',f'{error}\n\n可用 --game-dir 指定游戏安装目录。');return 1

if __name__=='__main__':sys.exit(main())
