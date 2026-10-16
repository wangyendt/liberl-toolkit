"""Reward-achievement list and optional completion of earlier milestones."""
from PySide6.QtCore import Qt, QEvent, QAbstractTableModel, QModelIndex, QSortFilterProxyModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QLineEdit, QComboBox, QCheckBox, QTableView,
                              QHeaderView, QAbstractItemView, QPushButton,
                              QStyledItemDelegate, QStyleOptionButton, QStyle)

from achievements import plan_completion


class AchievementModel(QAbstractTableModel):
    HEADERS = ['编号', '成就 / 阶段', '分类', '进度', '奖励', '状态', '操作']

    def __init__(self, catalog):
        super().__init__()
        # Keep the game's series ordering, including additions with late IDs.
        self.rows = [a for group in catalog.groups.values() for a in group]
        self.snapshot = {}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        achievement = self.rows[index.row()]
        state = self.snapshot.get(achievement.id)
        if role == Qt.UserRole:
            return achievement.id
        if role == Qt.DisplayRole:
            return [str(achievement.id), achievement.name, achievement.category,
                    achievement.format_progress(state.progress) if state else '—',
                    achievement.reward_text, state.status if state else '尚未读取',
                    ('完成' if not state.complete else '同步进度' if state.progress is not None and state.progress < achievement.target else '—') if state else '—'][index.column()]
        if role == Qt.ForegroundRole and index.column() == 5 and state:
            return QColor({'未完成': '#647e90', '可领奖': '#397a5c', '已领奖': '#8c9daa'}[state.status])
        if role == Qt.ToolTipRole:
            return f'{achievement.description}\n奖励：{achievement.reward_text}'
        if role == Qt.TextAlignmentRole and index.column() in (0, 2, 3, 5, 6):
            return int(Qt.AlignCenter)

    def update(self, snapshot):
        status_changed = ({i: s.flags for i, s in self.snapshot.items()} !=
                          {i: s.flags for i, s in snapshot.items()})
        self.snapshot = snapshot
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 6))
        return status_changed


class AchievementFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.text = ''
        self.category = '全部分类'
        self.status = '全部状态'

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        achievement = model.rows[row]
        state = model.snapshot.get(achievement.id)
        text = f'{achievement.id} {achievement.name} {achievement.description} {achievement.category} {achievement.reward_text}'.casefold()
        return (not self.text or self.text in text) and (
            self.category == '全部分类' or self.category == achievement.category) and (
            self.status == '全部状态' or state is not None and self.status == state.status)


class CompleteButtonDelegate(QStyledItemDelegate):
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback

    def paint(self, painter, option, index):
        if index.data() not in ('完成','同步进度'):
            super().paint(painter, option, index)
            return
        button = QStyleOptionButton()
        button.rect = option.rect.adjusted(5, 3, -5, -3)
        button.text = index.data()
        button.state = QStyle.State_Enabled
        if option.state & QStyle.State_MouseOver:
            button.state |= QStyle.State_MouseOver
        QApplication.style().drawControl(QStyle.CE_PushButton, button, painter)

    def editorEvent(self, event, model, option, index):
        if (index.data() in ('完成','同步进度') and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
                and option.rect.adjusted(5, 3, -5, -3).contains(event.position().toPoint())):
            self.callback(index.data(Qt.UserRole),index.data()=='同步进度')
            return True
        return False


class AchievementsPage(QWidget):
    def __init__(self, trainer):
        super().__init__(trainer)
        self.trainer = trainer
        self.catalog = trainer.editor.achievement_catalog
        self.selected_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.summary = QLabel('连接游戏或打开存档后，可查看成就状态与奖励。')
        self.summary.setObjectName('muted')
        layout.addWidget(self.summary)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索成就、奖励物品或编号')
        self.category = QComboBox()
        self.category.addItems(['全部分类'] + sorted({a.category for a in self.catalog.achievements.values()}))
        self.status_filter = QComboBox()
        self.status_filter.addItems(['全部状态', '未完成', '可领奖', '已领奖'])
        for widget in (self.search, self.category, self.status_filter):
            filters.addWidget(widget)
        layout.addLayout(filters)
        self.model = AchievementModel(self.catalog)
        self.proxy = AchievementFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(35)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in [(0, 48), (2, 62), (3, 115), (4, 178), (5, 82), (6, 88)]:
            self.table.setColumnWidth(column, width)
        self.table.setItemDelegateForColumn(6, CompleteButtonDelegate(self.complete, self.table))
        self.table.activated.connect(lambda index: self.complete(index.data(Qt.UserRole),index.data()=='同步进度') if index.column() == 6 else None)
        layout.addWidget(self.table, 1)
        self.details = QLabel('选择一条成就，查看要求、奖励及递进阶段。')
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.series_info = QLabel('')
        self.series_info.setObjectName('muted')
        self.series_info.setWordWrap(True)
        layout.addWidget(self.series_info)
        actions = QHBoxLayout()
        self.include_lower = QCheckBox('一并完成该系列的较低阶段')
        self.include_lower.setChecked(False)
        self.sync_progress = QCheckBox('同步系列进度至目标')
        self.sync_progress.setChecked(True)
        self.sync_progress.setToolTip('同系列共享一个计数；只提高到当前目标，保留已有更高计数。游戏也可能据此自动完成较低阶段。')
        self.complete_button = QPushButton('完成选中成就')
        self.complete_button.setObjectName('primary')
        self.complete_button.clicked.connect(lambda: self.complete(self.selected_id))
        actions.addWidget(self.include_lower)
        actions.addWidget(self.sync_progress)
        actions.addStretch()
        actions.addWidget(self.complete_button)
        layout.addLayout(actions)
        hint = QLabel('同系列共用进度计数。完成后重新打开游戏 REWARD 领奖；已领奖条目可同步不足的进度，领奖后无法通过撤销收回奖励。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for signal in (self.search.textChanged, self.category.currentTextChanged,
                       self.status_filter.currentTextChanged):
            signal.connect(self.filter_rows)
        self.table.selectionModel().selectionChanged.connect(self.select)
        self.include_lower.toggled.connect(self.update_enabled)
        self.sync_progress.toggled.connect(self.update_enabled)
        self.update_enabled()

    def filter_rows(self, *_):
        self.proxy.text = self.search.text().strip().casefold()
        self.proxy.category = self.category.currentText()
        self.proxy.status = self.status_filter.currentText()
        self.proxy.invalidateRowsFilter()
        self.select()

    def select(self, *_):
        rows = self.table.selectionModel().selectedRows()
        self.selected_id = rows[0].data(Qt.UserRole) if rows else None
        if self.selected_id is None:
            self.details.setText('选择一条成就，查看要求、奖励及递进阶段。')
            self.series_info.clear()
        else:
            achievement = self.catalog.achievements[self.selected_id]
            self.details.setText(f'{achievement.name}\n{achievement.description}\n奖励：{achievement.reward_text}')
        self.update_enabled()

    def update_enabled(self, *_):
        active = bool(self.trainer.editor.mode and self.model.snapshot)
        selected = self.selected_id
        self.complete_button.setEnabled(False)
        self.complete_button.setText('完成选中成就')
        self.include_lower.setEnabled(active)
        self.sync_progress.setEnabled(active)
        if selected is None:
            return
        achievement = self.catalog.achievements[selected]
        group = self.catalog.series(selected)
        if len(group) > 1:
            stages = ' → '.join(f'×{a.target / 10:.1f}' if a.objective == 95 else str(a.target) for a in group)
            position = next(i for i, a in enumerate(group, 1) if a.id == selected)
            text = f'递进目标：{stages} · 当前第 {position}/{len(group)} 阶段'
        else:
            text = '此成就没有较低阶段。'
        if active:
            try:
                changes = plan_completion(self.catalog, self.model.snapshot, selected, self.include_lower.isChecked(),self.sync_progress.isChecked())
            except RuntimeError as error:
                self.series_info.setText(f'{text} · {error}')
                return
            self.complete_button.setEnabled(bool(changes))
            flag_count=sum(len(data)==1 for _,data in changes)
            text += f' · 本次将完成 {flag_count} 项'
            if any(len(data)==4 for _,data in changes):text+=f'，进度同步至 {achievement.target}'
            self.complete_button.setText('同步选中进度' if self.model.snapshot[selected].complete else '完成选中成就')
        self.series_info.setText(text)

    def refresh(self, snapshot):
        if self.model.update(snapshot):
            self.proxy.invalidateRowsFilter()
        if snapshot:
            claimed = sum(s.flags == 3 for s in snapshot.values())
            pending = sum(s.flags == 1 for s in snapshot.values())
            self.summary.setText(f'{len(snapshot)} 条奖励成就 · 未完成 {len(snapshot) - claimed - pending} · 可领奖 {pending} · 已领奖 {claimed}')
        else:
            self.summary.setText('连接游戏或打开存档后，可查看成就状态与奖励。')
        self.select()

    def complete(self, aid, force_sync=False):
        if aid is None or not self.trainer.editor.mode:
            return
        count = 0
        def write():
            nonlocal count
            count = self.trainer.editor.complete_achievement(aid, self.include_lower.isChecked(),force_sync or self.sync_progress.isChecked())
        if self.trainer.act(write):
            self.trainer.note(f'已处理成就，新增完成 {count} 项。' + (
                '重新打开游戏 REWARD 领取奖励，并另存一个手动存档。' if self.trainer.editor.mode == 'live'
                else '请导出或保存到空槽位，在游戏载入后领取奖励。'))
