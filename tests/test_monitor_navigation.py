"""Monitor/scene selection and event sorting must not disturb active detection."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from core.records import load_run
from ui.monitor_center import MonitorCenter


class QuietWorker:
    def __init__(self, output):
        self.output = output
        self.stop = Mock()

    def isRunning(self):
        return True

    def preview(self):
        return None, []

    def monitor_events(self):
        return []

    def take_notifications(self):
        return []


def scene_record(description):
    return {'event_id': None, 'model': 'software-navigation-fixture', 'elapsed_s': .1,
        'frame_count': 3, 'span_s': 1., 'evidence': [{'timestamp_s': t} for t in (0., .5, 1.)],
        'scene': {'description': description, 'scene': 'candle', 'relation': 'expected_burning',
            'evidence_quality': 'adequate', 'spread': 'not_observed', 'visible_evidence': ['烛芯处局部火焰'],
            'hazard_cues': [], 'uncertainties': [], 'next_check': '回看原画面。'}}


class MonitorNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.desk = MonitorCenter(registry_path=self.root / 'points.json')
        self.desk.timer.stop()
        self.desk.auto_ai.setChecked(False)
        self.points = {value: {'id': value, 'name': value.upper() + '监控', 'location': value.upper() + '区',
            'kind': 'file', 'source': str(self.root / (value + '.mp4'))} for value in ('a', 'b')}
        self.desk.set_devices(list(self.points.values()))
        self.outputs = {value: str(self.root / ('run-' + value)) for value in self.points}
        self.desk.outputs.update(self.outputs)
        self.desk.resize(1360, 1000)
        self.addCleanup(self.cleanup_desk)

    def cleanup_desk(self):
        self.desk.workers.clear()
        self.desk.shutdown()
        self.desk.close()
        self.desk.deleteLater()
        QCoreApplication.sendPostedEvents(self.desk, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def add_scene(self, identifier='a', description='本路A画面中可见蜡烛。'):
        key = (identifier, self.outputs[identifier], None)
        self.desk.scenes[key] = {'event_id': None, 'device': self.points[identifier], 'output': key[1],
            'baseline': None, 'closed': True, 'reference_s': 1., 'last_timestamp_s': 2., 'frames': [],
            'scene_record': scene_record(description), 'scene_status': '已保存场景描述'}
        return key

    def add_event(self, identifier='a', event_id=1, timestamp=10., level='MEDIUM'):
        score = {'LOW': 30., 'MEDIUM': 55., 'HIGH': 80.}[level]
        priority = {'policy': 'context-score-v2', 'score': score, 'level': level,
            'baseline_score': score, 'baseline_level': level, 'context_delta': 0., 'reason': '软件导航测试依据。'}
        key = (identifier, self.outputs[identifier], event_id)
        self.desk.events[key] = {'event_id': event_id, 'device': self.points[identifier], 'output': key[1],
            'label': 'fire', 'confirmed_at_s': timestamp, 'reference_s': timestamp, 'last_timestamp_s': timestamp,
            'closed': True, 'baseline': {'score': score, 'level': level},
            'assessment': {'score': score, 'level': level, 'fusion': priority},
            'review': 'pending', 'scene_status': '等待场景分析', 'scene_record': None, 'frames': []}
        return key

    def table_keys(self):
        return [self.desk.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.desk.event_table.rowCount())]

    def sequence_map(self):
        return {self.desk.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole):
                self.desk.event_table.item(row, 0).text()
                for row in range(self.desk.event_table.rowCount())}

    def choose_sort(self, value):
        index = self.desk.event_sort.findData(value)
        self.assertGreaterEqual(index, 0, 'Missing requested event sort')
        self.desk.event_sort.setCurrentIndex(index)
        self.app.processEvents()

    def show_desk(self):
        self.desk.show()
        self.app.processEvents()

    def click_event(self, key):
        row = self.table_keys().index(key)
        item = self.desk.event_table.item(row, 0)
        self.desk.event_table.scrollToItem(item)
        self.app.processEvents()
        QTest.mouseClick(self.desk.event_table.viewport(), Qt.MouseButton.LeftButton,
                        pos=self.desk.event_table.visualItemRect(item).center())
        self.app.processEvents()

    def test_scene_selection_works_without_any_detection_event(self):
        key = self.add_scene()
        self.desk.select_device_scene('a')
        self.assertEqual(self.desk.selected_key, key)
        self.assertTrue(self.desk.selected_scene_locked)
        self.assertIn('本路A', self.desk.scene_text.toPlainText())
        self.assertFalse(self.desk.save_review_button.isEnabled())
        self.assertEqual(self.desk.events, {})

    def test_empty_monitor_gets_scene_placeholder_without_fake_event(self):
        self.desk.outputs.pop('a')
        self.desk.select_device_scene('a')
        key = self.desk.selected_key
        self.assertIsNotNone(key)
        self.assertEqual((key[0], key[2]), ('a', None))
        self.assertTrue(self.desk.selected_scene_locked)
        self.assertIn(key, self.desk.scenes)
        self.assertIsNone(self.desk.scenes[key].get('scene_record'))
        self.assertEqual(self.desk.events, {})
        self.assertFalse(self.desk.review_toggle.isEnabled())
        self.assertFalse(self.desk.save_review_button.isEnabled())
        self.desk._render_events()
        self.assertEqual(self.desk.selected_key, key)

    def test_card_ai_description_is_clickable_without_events(self):
        key = self.add_scene()
        self.show_desk()
        QTest.mouseClick(self.desk.cards['a'].scene_description, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.desk.selected_key, key)
        self.assertTrue(self.desk.selected_scene_locked)

    def test_waiting_placeholder_follows_the_same_monitor_when_new_run_starts(self):
        self.desk.outputs.pop('a')
        self.desk.select_device_scene('a')
        self.assertTrue(self.desk.selected_scene_locked)
        new_run = str(self.root / 'run-a-new')
        self.desk._started('a', {'output': new_run})
        self.assertEqual(self.desk.selected_key, ('a', new_run, None))
        self.assertTrue(self.desk.selected_scene_locked)
        self.assertIn(self.desk.selected_key, self.desk.scenes)
        self.assertEqual(self.desk.events, {})

    def test_card_scene_choice_survives_event_refresh_and_sorting(self):
        key = self.add_scene()
        self.add_event('b', 1, 3., 'HIGH')
        self.desk._render_events()
        self.desk.select_device_scene('a')
        self.add_event('b', 2, 12., 'HIGH')
        self.desk._render_events()
        self.app.processEvents()
        self.choose_sort('time_desc')
        self.desk._render_events()
        self.app.processEvents()
        self.assertEqual(self.desk.selected_key, key)
        self.assertTrue(self.desk.selected_scene_locked)
        self.assertIn('本路A', self.desk.scene_text.toPlainText())
        self.assertEqual(self.desk.event_table.selectedItems(), [])

    def test_event_click_clears_scene_lock_and_expands_list_without_stopping_worker(self):
        self.add_scene()
        event = self.add_event('b', 1, 3., 'HIGH')
        worker = QuietWorker(self.outputs['a'])
        self.desk.workers['a'] = worker
        self.desk._render_events()
        self.desk.select_device_scene('a')
        self.show_desk()
        self.assertEqual(self.desk.event_splitter.orientation(), Qt.Orientation.Vertical)
        self.assertEqual(self.desk.event_splitter.count(), 2)
        self.click_event(event)
        self.assertEqual(self.desk.selected_key, event)
        self.assertFalse(self.desk.selected_scene_locked)
        self.assertTrue(self.desk.cards['b'].property('selected'))
        self.assertFalse(self.desk.cards['a'].property('selected'))
        sizes = self.desk.event_splitter.sizes()
        self.assertGreater(sizes[0], 0)
        self.assertGreaterEqual(sizes[1] / sum(sizes), .35)
        expanded_events = sizes[1]
        self.desk.expand_events_button.click()
        self.app.processEvents()
        self.assertLess(self.desk.event_splitter.sizes()[1], expanded_events)
        worker.stop.assert_not_called()
        self.assertIs(self.desk.workers['a'], worker)

    def test_numbers_are_stable_across_sorting_and_new_arrivals(self):
        a1 = self.add_event('a', 1, 10., 'MEDIUM')
        b1 = self.add_event('b', 1, 5., 'HIGH')
        a2 = self.add_event('a', 2, 20., 'LOW')
        self.desk._render_events()
        self.assertEqual(self.desk.event_table.columnCount(), 5)
        self.assertEqual(self.desk.event_table.horizontalHeaderItem(0).text(), '序号')
        original = self.sequence_map()
        self.assertEqual(len(set(original.values())), 3)
        self.assertTrue(all(value.strip() for value in original.values()))
        self.choose_sort('time_desc')
        self.assertEqual(self.table_keys(), [a2, a1, b1])
        self.assertEqual(self.sequence_map(), original)
        self.choose_sort('time_asc')
        self.assertEqual(self.table_keys(), [b1, a1, a2])
        self.assertEqual(self.sequence_map(), original)
        self.choose_sort('device')
        self.assertEqual([key[0] for key in self.table_keys()], ['a', 'a', 'b'])
        self.assertEqual(self.sequence_map(), original)
        self.choose_sort('priority')
        self.assertEqual(self.table_keys()[0], b1)
        self.add_event('b', 2, 30., 'HIGH')
        self.desk._render_events()
        current = self.sequence_map()
        self.assertTrue(all(current[key] == number for key, number in original.items()))
        self.assertEqual(len(set(current.values())), 4)

    def test_compatibility_entry_opens_general_scene_instead_of_latest_event(self):
        key = self.add_scene()
        self.add_event('a', 1, 10., 'MEDIUM')
        self.desk._render_events()
        self.desk.select_device_event('a')
        self.assertEqual(self.desk.selected_key, key)
        self.assertTrue(self.desk.selected_scene_locked)

    def test_saved_no_event_ai_description_remains_selectable(self):
        run = Path(self.outputs['a'])
        run.mkdir()
        data = {'summary.json': {'status': 'completed'},
                'config.json': {'source': self.points['a']['source'], 'source_kind': 'file',
                                'monitoring_point': self.points['a']},
                'events.json': {'events': []}}
        for filename, value in data.items():
            (run / filename).write_text(json.dumps(value), encoding='utf-8')
        (run / 'scene-reviews.jsonl').write_text(json.dumps(scene_record('已保存的A路独立场景描述。')) + '\n', encoding='utf-8')
        self.desk.restore_saved_events([load_run(run)])
        self.desk.select_device_scene('a')
        self.assertIn('已保存的A路', self.desk.scene_text.toPlainText())
        self.assertEqual(self.desk.events, {})
        self.assertTrue(self.desk.selected_scene_locked)

    def test_camera_opens_latest_same_run_scene_without_using_future_frames(self):
        self.add_scene(description='较早的本路画面。')
        key = self.add_event('a', 1, 12., 'HIGH')
        newer = scene_record('最新的本路事件原画面。')
        for frame in newer['evidence']:
            frame['timestamp_s'] += 10.
        newer['event_id'] = 1
        self.desk.events[key]['scene_record'] = newer
        self.desk.select_device_scene('a')
        self.assertIn('最新的本路', self.desk.analysis_summary.description_card.body.text())
        self.desk.workers['a'] = QuietWorker(self.outputs['a'])
        self.desk.packets['a'] = {'timestamp_s': 2.}
        self.desk.scenes[(key[0], key[1], None)]['scene_record'] = scene_record('目前可用的较早画面。')
        self.desk.select_device_scene('a')
        self.assertIn('目前可用', self.desk.analysis_summary.description_card.body.text())

    def test_late_arriving_older_event_does_not_renumber_existing_event(self):
        first = self.add_event('a', 1, 10., 'MEDIUM')
        self.desk._render_events()
        number = self.sequence_map()[first]
        older = self.add_event('b', 1, 2., 'HIGH')
        self.desk._render_events()
        self.choose_sort('time_asc')
        self.assertEqual(self.table_keys()[0], older)
        self.assertEqual(self.sequence_map()[first], number)
        self.assertNotEqual(self.sequence_map()[older], number)

    def test_live_refresh_keeps_list_scrolled_away_from_selected_event(self):
        for number in range(1, 51):
            self.add_event('a', number, float(number), 'MEDIUM')
        self.desk._render_events()
        self.show_desk()
        self.desk._set_events_expanded(True)
        for _ in range(3):
            self.app.processEvents()
        bar = self.desk.event_table.verticalScrollBar()
        self.assertGreater(bar.maximum(), 10)
        selected = self.desk.selected_key
        for position in (bar.maximum() // 2, bar.maximum()):
            bar.setValue(position)
            for _ in range(3):
                self.desk.refresh()
                self.app.processEvents()
                self.assertEqual(bar.value(), position)
                self.assertEqual(self.desk.selected_key, selected)

    def test_scrollbar_drag_is_not_interrupted_by_periodic_refresh(self):
        for number in range(1, 51):
            self.add_event('a', number, float(number), 'MEDIUM')
        self.desk._render_events()
        self.show_desk()
        self.desk._set_events_expanded(True)
        for _ in range(3):
            self.app.processEvents()
        bar = self.desk.event_table.verticalScrollBar()
        option = QStyleOptionSlider()
        bar.initStyleOption(option)
        handle = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, option,
                                           QStyle.SubControl.SC_ScrollBarSlider, bar)
        QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=handle.center())
        QTest.mouseMove(bar, QPoint(handle.center().x(), bar.height() * 3 // 4))
        self.assertTrue(bar.isSliderDown())
        position = bar.sliderPosition()
        self.assertGreater(position, 0)
        self.desk.timer.start()
        try:
            QTest.qWait(360)
            self.assertTrue(bar.isSliderDown())
            self.assertEqual(bar.sliderPosition(), position)
            QTest.mouseRelease(bar, Qt.MouseButton.LeftButton,
                               pos=QPoint(handle.center().x(), bar.height() * 3 // 4))
            QTest.qWait(260)
            self.assertEqual(bar.value(), position)
        finally:
            self.desk.timer.stop()

    def test_new_event_keeps_the_first_visible_event_in_place(self):
        from PySide6.QtWidgets import QTableWidget
        for number in range(1, 51):
            self.add_event('a', number, float(number), 'MEDIUM')
        self.desk._render_events()
        self.show_desk()
        self.desk._set_events_expanded(True)
        table = self.desk.event_table
        for number, mode in enumerate((QTableWidget.ScrollMode.ScrollPerItem,
                                       QTableWidget.ScrollMode.ScrollPerPixel), 51):
            with self.subTest(mode=mode):
                table.setVerticalScrollMode(mode)
                for _ in range(3):
                    self.app.processEvents()
                bar = table.verticalScrollBar()
                bar.setValue(bar.maximum() // 2)
                top_row = table.rowAt(0)
                top_key = table.item(top_row, 0).data(Qt.ItemDataRole.UserRole)
                offset = table.rowViewportPosition(top_row)
                selected = self.desk.selected_key
                self.add_event('a', number, float(number), 'HIGH')
                self.desk.refresh()
                for _ in range(3):
                    self.app.processEvents()
                new_top_row = table.rowAt(0)
                self.assertEqual(table.item(new_top_row, 0).data(Qt.ItemDataRole.UserRole), top_key)
                self.assertEqual(table.rowViewportPosition(new_top_row), offset)
                self.assertEqual(self.desk.selected_key, selected)

    def test_card_buttons_keep_their_actions_and_video_selects_once(self):
        from PySide6.QtTest import QSignalSpy
        from ui.monitor_center import MonitorCard
        card = MonitorCard(self.points['a'])
        def dispose_card():
            card.close()
            card.deleteLater()
            QCoreApplication.sendPostedEvents(card, QEvent.Type.DeferredDelete)
        self.addCleanup(dispose_card)
        selected = QSignalSpy(card.selected)
        models = QSignalSpy(card.modelRequested)
        card.resize(430, 600)
        card.show()
        self.app.processEvents()
        QTest.mouseClick(card.model_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(card.start_button, Qt.MouseButton.LeftButton)
        self.assertEqual(models.count(), 1)
        self.assertEqual(selected.count(), 0)
        QTest.mouseClick(card.video, Qt.MouseButton.LeftButton)
        self.assertEqual(selected.count(), 1)


if __name__ == '__main__':
    unittest.main()
