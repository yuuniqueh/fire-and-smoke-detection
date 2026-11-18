"""Readable analysis categories stay explicit and fit narrow native sidebars."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QLabel, QScrollArea, QVBoxLayout, QWidget

from ui.analysis_cards import AnalysisSummary, KeyValueTable, ResultCard, TextCard, WrapLabel
from ui.product import STYLE, ui_font


class AnalysisCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_plain_text_categories_do_not_turn_model_text_into_html(self):
        summary = AnalysisSummary()
        self.addCleanup(summary.close)
        summary.set_content('<b>火焰位于画面中的灶台附近</b>', '橙色提示 · 优先核查', 63.4,
            [('位置', '<img src="outside">厨房西侧'), ('持续时间', '12.3 秒')],
            ['可见局部火焰', '<script>不应作为富文本执行</script>'],
            status='已结合当前画面分析', uncertainties=['画面外情况未知'],
            details=[('模型', '本机视觉模型'), ('分析耗时', '3.2 秒')], level='MEDIUM')
        self.assertEqual(summary.description_card.body.text(), '<b>火焰位于画面中的灶台附近</b>')
        self.assertEqual(summary.result_card.score_label.text(), '63.4 / 100')
        self.assertTrue(summary.result_card.score_label.isHidden())
        self.assertEqual(summary.result_card.score_value, 63.4)
        self.assertEqual(summary.result_card.conclusion_label.text(), '橙色提示 · 优先核查')
        self.assertEqual(summary.metrics_table.rows[0], ('位置', '<img src="outside">厨房西侧'))
        for label in summary.findChildren(QLabel):
            self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(summary.findChildren(QAbstractScrollArea), [])

    def test_details_are_collapsed_and_old_event_content_is_cleared(self):
        summary = AnalysisSummary()
        self.addCleanup(summary.close)
        summary.set_content('蜡烛位于桌面上。', '请核查', 35., [('位置', '一层大厅')],
            ['火焰局限在烛芯附近'], details=[('模型', '模型A')])
        self.assertTrue(summary.details_table.isHidden())
        summary.set_details_visible(True)
        self.assertFalse(summary.details_table.isHidden())
        self.assertTrue(summary.details_toggle.isChecked())
        summary.set_content('正在分析另一路画面。', '等待分析', None, [], [])
        self.assertEqual(summary.details_table.rows, [])
        self.assertTrue(summary.details_toggle.isHidden())
        self.assertEqual(summary.reasons_card.items, [])
        self.assertEqual(summary.result_card.score_label.text(), '— / 100')
        summary.set_details([('模型', '模型B')])
        summary.set_details_visible(True)
        summary.clear_content()
        self.assertFalse(summary.details_toggle.isChecked())
        self.assertTrue(summary.metrics_table.isHidden())
        self.assertIn('选择记录', summary.description_card.body.text())

    def test_narrow_and_wide_layouts_wrap_labels_without_nested_scrollbars(self):
        for width in (320, 620):
            with self.subTest(width=width):
                host = QWidget()
                host.setFont(ui_font())
                host.setStyleSheet(STYLE)
                box = QVBoxLayout(host)
                box.setContentsMargins(0, 0, 0, 0)
                box.setAlignment(Qt.AlignmentFlag.AlignTop)
                summary = AnalysisSummary()
                box.addWidget(summary)
                summary.set_content('厨房内可见人员与灶台，火焰位于操作区域附近，当前原画面仍需人工核对。',
                    '橙色提示 · 优先核查', 63.4,
                    [('位置', '一层厨房西侧操作台及相邻通道'), ('持续时间', '12.3 秒'),
                     ('核查结论', '已确认异常，等待现场处理')],
                    ['可见烟火持续出现，画面范围有所变化。', 'AI画面描述提供了厨房场景线索。'],
                    status='已结合当前画面，等待人工核查。', uncertainties=['画面外可燃物情况未知。'],
                    details=[('模型', '本机视觉模型')], level='MEDIUM')
                host.resize(width, 2200)
                host.show()
                self.app.processEvents()
                self.assertEqual(summary.width(), width)
                self.assertGreaterEqual(summary.result_card.conclusion_label.font().pixelSize(), 28)
                self.assertTrue(summary.result_card.score_label.isHidden())
                for label in summary.findChildren(QLabel):
                    if not label.isVisible():
                        continue
                    point = label.mapTo(summary, QPoint(0, 0))
                    self.assertGreaterEqual(point.x(), 0)
                    self.assertLessEqual(point.x() + label.width(), width)
                    expected = label.heightForWidth(label.width())
                    self.assertLessEqual(expected, label.height() + 1, label.text())
                self.assertEqual(summary.findChildren(QAbstractScrollArea), [])
                host.close()
                host.deleteLater()
                self.app.processEvents()

    def test_live_scroll_area_updates_remove_old_cells_before_deferred_delete(self):
        for width in (620, 320):
            with self.subTest(width=width):
                host = QWidget()
                host.setFont(ui_font())
                host.setStyleSheet(STYLE)
                host_box = QVBoxLayout(host)
                host_box.setContentsMargins(0, 0, 0, 0)
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                content = QWidget()
                box = QVBoxLayout(content)
                box.setContentsMargins(16, 16, 16, 16)
                box.setAlignment(Qt.AlignmentFlag.AlignTop)
                summary = AnalysisSummary()
                box.addWidget(summary)
                scroll.setWidget(content)
                host_box.addWidget(scroll)
                summary.set_content('厨房内可见局部火焰。', '橙色提示 · 优先核查', 41.7,
                    [('检测原始指数', '41.7'), ('旧场景影响', '+14.0'), ('持续', '1.3 秒'),
                     ('旧位置', '旧位置'), ('旧核查', '旧状态')],
                    ['旧依据一', '旧依据二', '旧依据三'], level='MEDIUM')
                host.resize(width, 470)
                host.show()
                self.app.processEvents()
                old_cells = list(summary.metrics_table.key_labels + summary.metrics_table.value_labels)
                old_reasons = list(summary.reasons_card.labels)
                summary.set_content('厨房内，水槽中燃烧的火焰。', '当时提示：优先核查', 55.7,
                    [('检测原始指数', '55.7'), ('场景辅助影响', '+0.0'),
                     ('位置', '一层厨房西侧操作台及相邻疏散通道，请核对现场具体位置')],
                    ['火焰位于水槽附近，仍需核对周围情况。'], status='已处理', level='MEDIUM')
                # Check immediately, before the event loop can service DeferredDelete.
                for old in old_cells + old_reasons:
                    self.assertTrue(old.isHidden())
                    self.assertIsNone(old.parent())
                for _ in range(3):
                    self.app.processEvents()
                self.assertEqual(len(summary.metrics_table.findChildren(QLabel)), 6)
                self.assertEqual(len(summary.reasons_card.findChildren(QLabel)), 2)
                self.assertNotIn('41.7', [label.text() for label in summary.findChildren(QLabel)])
                recommendation = summary.result_card.conclusion_label
                self.assertGreaterEqual(recommendation.height(), recommendation.fontMetrics().height())
                self.assertGreaterEqual(recommendation.font().pixelSize(), 28)
                self.assertTrue(summary.result_card.score_label.isHidden())
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                for label in summary.metrics_table.key_labels + summary.metrics_table.value_labels:
                    self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                    self.assertGreaterEqual(label.height(), label.fontMetrics().height())
                values = summary.metrics_table.value_labels
                for previous, current in zip(values, values[1:]):
                    self.assertLess(previous.geometry().bottom(), current.geometry().top())
                host.close()
                host.deleteLater()
                self.app.processEvents()

    def test_shorter_text_releases_old_wrapped_minimum_height(self):
        label = WrapLabel('厨房画面中的可见火焰需要人工核查。' * 8, size=18)
        self.addCleanup(label.close)
        label.resize(150, 800)
        label.show()
        self.app.processEvents()
        long_height = label.minimumHeight()
        label.setText('厨房')
        self.app.processEvents()
        self.assertLess(label.minimumHeight(), long_height)
        self.assertEqual(label.minimumHeight(), label.fontMetrics().height())

    def test_priority_uses_supplied_level_and_keeps_rule_index_hidden(self):
        card = ResultCard()
        self.addCleanup(card.close)
        card.set_result('立即核查', 5., level='HIGH')
        self.assertEqual(card.title_label.text(), '研判建议')
        self.assertEqual(card.conclusion_label.text(), '立即核查')
        self.assertTrue(card.score_label.isHidden())
        self.assertEqual(card.score_label.text(), '5.0 / 100')
        self.assertEqual(card.score_value, 5.)
        self.assertEqual(card.priority_level, 'HIGH')
        self.assertTrue(card.priority_segments['HIGH'].property('selected'))
        self.assertFalse(card.priority_segments['LOW'].property('selected'))
        card.set_result('请核查', 99., level='LOW')
        self.assertEqual(card.priority_level, 'LOW')
        self.assertTrue(card.priority_segments['LOW'].property('selected'))
        self.assertFalse(card.priority_segments['HIGH'].property('selected'))
        self.assertEqual(card.score_value, 99.)
        for label in (card.title_label, card.conclusion_label, card.priority_title,
                      *card.priority_segments.values(), card.priority_state):
            self.assertNotIn('%', label.text())
            self.assertNotIn('火灾概率', label.text())

    def test_historical_original_priority_is_separate_from_muted_workflow_color(self):
        summary = AnalysisSummary()
        self.addCleanup(summary.close)
        summary.set_content('已保存的历史画面描述。', '当时提示：立即核查', 55.7, [], [],
            status='已处理', level='NORMAL', priority_level='HIGH', historical=True)
        card = summary.result_card
        self.assertEqual(card.conclusion_label.text(), '当时提示：立即核查')
        self.assertFalse(card.context_label.isHidden())
        self.assertIn('历史记录', card.context_label.text())
        self.assertEqual(card.priority_title.text(), '当时核查优先级')
        self.assertEqual(card.priority_level, 'HIGH')
        self.assertTrue(card.priority_segments['HIGH'].property('selected'))
        self.assertTrue(card.priority_state.isHidden())
        self.assertTrue(card.score_label.isHidden())
        card.set_result('当时提示：立即核查', 99., '已处理', level='NORMAL')
        self.assertTrue(card.priority_row.isHidden())
        self.assertEqual(card.priority_state.text(), '以当时记录为准')
        self.assertNotIn('持续观察', card.priority_state.text())
        summary.clear_content()
        self.assertFalse(card.historical)
        self.assertTrue(card.context_label.isHidden())
        self.assertEqual(card.priority_state.text(), '等待分析')

    def test_normal_and_missing_levels_do_not_infer_priority_from_numeric_score(self):
        card = ResultCard()
        self.addCleanup(card.close)
        card.set_result('', 95., level='NORMAL')
        self.assertEqual(card.conclusion_label.text(), '持续观察')
        self.assertEqual(card.priority_state.text(), '持续观察')
        self.assertTrue(card.priority_row.isHidden())
        self.assertFalse(any(segment.property('selected') for segment in card.priority_segments.values()))
        card.set_result('', None)
        self.assertEqual(card.conclusion_label.text(), '等待分析')
        self.assertEqual(card.priority_state.text(), '等待分析')
        self.assertIsNone(card.score_value)
        self.assertTrue(card.score_label.isHidden())


if __name__ == '__main__':
    unittest.main()
