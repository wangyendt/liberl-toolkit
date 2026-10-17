import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QMainWindow
except ImportError:
    Trainer=None
else:
    from app import Trainer
    from game_data import Item


@unittest.skipIf(Trainer is None,'Item UI tests require PySide6')
class ItemSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        class ItemsWindow(Trainer):
            def __init__(window):
                QMainWindow.__init__(window)
                window.catalog=SimpleNamespace(items={
                    201:Item(201,'剧情道具','只读测试',2,0,99,0),
                    373:Item(373,'测试书籍','描述',2,13,1,0),
                    2100:Item(2100,'测试食材一','描述',20,7,99,0),
                    2101:Item(2101,'测试食材二','描述',20,7,10,0),
                    2200:Item(2200,'测试素材','描述',20,5,9999,0),
                    2500:Item(2500,'测试外观服装','描述',15,0,1,0),
                    3300:Item(3300,'测试外观发色','描述',16,0,1,0),
                })
                window.editor=SimpleNamespace(mode='save',set_item=Mock(),set_items=Mock(),refill=Mock())
                window.selected_items=[]
                window.act=Mock(side_effect=lambda callback:callback())
                window.setCentralWidget(window.items_page())
                window.model.update({201:(1,3),373:(1,3),2100:(5,3),2101:(2,0x13)})
                window.resize(1000,700)
                window.select_item()
            def closeEvent(window,event):event.accept()
        self.window=ItemsWindow()
        self.window.show();self.qt.processEvents()

    def tearDown(self):
        self.window.close();self.window.deleteLater();self.qt.processEvents()

    def click_row(self,row,modifiers=Qt.NoModifier):
        view=self.window.item_table
        point=view.visualRect(self.window.proxy.index(row,1)).center()
        QTest.mouseClick(view.viewport(),Qt.LeftButton,modifiers,point)

    def test_ctrl_selection_submits_visible_ids_once(self):
        self.click_row(1)
        self.click_row(2,Qt.ControlModifier)
        self.assertEqual({it.id for it in self.window.selected_items},{373,2100})
        self.window.item_qty.setValue(50)
        self.window.item_write.click()
        self.window.editor.set_items.assert_called_once_with([373,2100],50)
        self.window.editor.set_item.assert_not_called()
        self.assertIn('可修改 2 项',self.window.item_selection.text())

    def test_shift_selection_and_readonly_summary(self):
        self.click_row(0)
        self.click_row(2,Qt.ShiftModifier)
        self.assertEqual({it.id for it in self.window.selected_items},{201,373,2100})
        self.assertIn('只读 1 项',self.window.item_selection.text())
        self.assertTrue(self.window.item_write.isEnabled())

    def test_filtered_select_all_does_not_include_hidden_rows(self):
        self.window.item_table.selectAll()
        self.window.search.setText('食材')
        self.qt.processEvents()
        self.window.item_table.selectAll()
        self.assertEqual({it.id for it in self.window.selected_items},{2100,2101})
        self.window.item_qty.setValue(99)
        self.window.item_write.click()
        self.window.editor.set_items.assert_called_once_with([2100,2101],99)
        self.window.item_table.clearSelection()
        self.assertFalse(self.window.selected_items)
        self.assertFalse(self.window.item_write.isEnabled())

    def test_single_selection_keeps_cap_and_readonly_is_disabled(self):
        self.click_row(1)
        self.assertEqual(self.window.item_qty.maximum(),1)
        self.window.item_write.click()
        self.window.editor.set_item.assert_called_once_with(373,1)
        self.click_row(0)
        self.assertFalse(self.window.item_write.isEnabled())
        self.assertFalse(self.window.item_qty.isEnabled())

    def test_no_connection_disables_batch_edit(self):
        self.window.editor.mode=None
        self.window.item_table.selectAll()
        self.assertFalse(self.window.item_write.isEnabled())
        self.assertFalse(self.window.item_qty.isEnabled())

    def test_material_can_be_selected_and_set_above_999(self):
        self.click_row(4)
        self.assertEqual(self.window.item_qty.maximum(),9999)
        self.assertTrue(self.window.item_write.isEnabled())
        self.window.item_qty.setValue(5000)
        self.window.item_write.click()
        self.window.editor.set_item.assert_called_once_with(2200,5000)
        self.click_row(2,Qt.ControlModifier)
        self.assertEqual(self.window.item_qty.maximum(),9999)
        self.window.item_qty.setValue(9999)
        self.window.item_write.click()
        self.window.editor.set_items.assert_called_once()
        ids,qty=self.window.editor.set_items.call_args.args
        self.assertCountEqual(ids,[2100,2200])
        self.assertEqual(qty,9999)

    def test_costume_and_hair_filters_allow_single_and_batch_editing(self):
        for category,iid in [('服装',2500),('发色',3300)]:
            with self.subTest(category=category):
                self.window.category.setCurrentText(category)
                self.window.item_table.selectAll()
                self.assertTrue(self.window.item_write.isEnabled())
                self.assertEqual(self.window.item_qty.maximum(),1)
                self.assertIn('穿戴',self.window.item_desc.text())
                self.window.item_qty.setValue(1)
                self.window.item_write.click()
                self.window.editor.set_item.assert_called_once_with(iid,1)
                self.window.editor.set_item.reset_mock()
        self.window.category.setCurrentText('全部分类')
        self.window.search.setText('外观')
        self.window.item_table.selectAll()
        self.assertIn('可修改 2 项',self.window.item_selection.text())
        self.window.item_qty.setValue(1)
        self.window.item_write.click()
        self.window.editor.set_items.assert_called_once()
        ids,qty=self.window.editor.set_items.call_args.args
        self.assertCountEqual(ids,[2500,3300])
        self.assertEqual(qty,1)
