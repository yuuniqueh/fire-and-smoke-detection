"""Scene-oriented selection, persistence, missing models and run provenance."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLabel
from core.model_profiles import (PROFILE_NAMES, SCENE_PROFILES, COMPARISON_PROFILES, SCENE_WEIGHTS,
                                 profile_weight_specs, profile_availability, profile_details, selection_metadata)
from core.preferences import PreferencesStore, validate_preferences
from core.pipeline import AnalysisOptions
from core.records import render_review_report
from ui.model_picker import ModelInfoDialog, ModelPickerDialog
from ui.window import MainWindow
from ui.worker import AnalysisWorker


class SceneSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.windows, self.dialogs = [], []
        self.history_patch = patch('ui.window.list_runs', return_value=[])
        self.history_patch.start()

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
        for window in self.windows:
            window.task = None
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.history_patch.stop()
        self.temp.cleanup()

    def window(self, settings=False):
        window = MainWindow(settings_path=settings)
        self.windows.append(window)
        return window

    def write_model_files(self, profile):
        for relative in profile_details(profile)['files']:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'presence fixture, not a real model')

    def test_each_registered_profile_has_explicit_composition_tradeoff_and_validation_scope(self):
        for profile in PROFILE_NAMES:
            details = profile_details(profile)
            self.assertTrue(details['composition'])
            self.assertTrue(details['tradeoff'])
            self.assertTrue(details['validation'])
            meta = selection_metadata(profile)
            self.assertEqual(meta['profile'], profile)
            self.assertIn('no automatic', meta['selection'])
        with self.assertRaises(ValueError):
            profile_details('unknown')

    def test_candidate_requires_both_model_files_and_does_not_fall_back(self):
        self.write_model_files('original')
        self.assertTrue(profile_availability('original', self.root)['ready'])
        state = profile_availability('modern', self.root)
        self.assertFalse(state['ready'])
        self.assertEqual(state['profile'], 'modern')
        self.assertEqual(len(state['missing']), 1)
        self.write_model_files('modern')
        self.assertTrue(profile_availability('modern', self.root)['ready'])
        (self.root / profile_details('modern')['files'][1]).write_bytes(b'')
        self.assertFalse(profile_availability('modern', self.root)['ready'])

    def test_dialog_explains_limits_disables_missing_models_and_cancellation_keeps_selection(self):
        self.write_model_files('original')
        dialog = ModelPickerDialog('original', assets=self.root)
        self.dialogs.append(dialog)
        self.assertFalse(dialog.choice_buttons['modern'].isEnabled())
        info = ModelInfoDialog('modern', assets=self.root)
        self.dialogs.append(info)
        self.assertIn('独立', info.technical_details.text())
        self.assertIn('主要优点', info.introduction.text())
        self.assertIn('容易出错的情况与使用建议', info.introduction.text())
        self.assertNotIn('阈值', info.introduction.text())
        self.assertNotIn('困难图', info.introduction.text())
        self.assertTrue(info.technical_details.isHidden())
        info.details_toggle.click()
        self.assertFalse(info.technical_details.isHidden())
        info.details_toggle.click()
        self.assertTrue(info.technical_details.isHidden())
        self.assertTrue(dialog.info_buttons['modern'].isEnabled())
        dialog.reject()
        self.assertIsNone(dialog.selected_profile)
        dialog.choice_buttons['original'].click()
        self.assertEqual(dialog.selected_profile, 'original')

    def test_compact_picker_keeps_introductions_separate_and_viewing_does_not_select(self):
        self.write_model_files('modern')
        dialog = ModelPickerDialog('original', assets=self.root)
        self.dialogs.append(dialog)
        texts = '\n'.join(label.text() for label in dialog.findChildren(QLabel))
        self.assertNotIn('YOLO26', texts)
        self.assertNotIn('验证范围', texts)
        self.assertNotIn('试用', texts)
        with patch.object(ModelInfoDialog, 'exec', return_value=ModelInfoDialog.DialogCode.Accepted) as show:
            dialog.info_buttons['modern'].click()
            show.assert_called_once()
        self.assertIsNone(dialog.selected_profile)
        self.assertEqual(dialog.result(), 0)

    def test_homepage_introduction_is_available_while_selection_is_locked(self):
        window = self.window()
        window.scene_combo.setCurrentIndex(window.scene_combo.findData('modern'))
        worker = AnalysisWorker(AnalysisOptions(source='not-opened.mp4', model_profile='modern'))
        window.task = worker
        window._set_controls()
        self.assertFalse(window.scene_combo.isEnabled())
        with patch.object(ModelInfoDialog, 'exec', return_value=ModelInfoDialog.DialogCode.Accepted) as show:
            window.scene_info_button.click()
            show.assert_called_once()
        self.assertEqual(window.model_profile.currentData(), 'modern')
        self.assertIs(window.task, worker)

    def test_picker_rechecks_files_if_removed_while_open(self):
        self.write_model_files('modern')
        dialog = ModelPickerDialog('original', assets=self.root)
        self.dialogs.append(dialog)
        (self.root / profile_details('modern')['files'][1]).unlink()
        dialog.choice_buttons['modern'].click()
        self.assertIsNone(dialog.selected_profile)
        self.assertFalse(dialog.choice_buttons['modern'].isEnabled())

    def test_scene_and_advanced_model_selection_stay_in_sync_without_changing_other_modes(self):
        window = self.window()
        for profile in PROFILE_NAMES:
            window.scene_combo.setCurrentIndex(window.scene_combo.findData(profile))
            self.assertEqual(window.model_profile.currentData(), profile)
            self.assertIn(profile_details(profile)['summary'], window.scene_note.toolTip())
            self.assertTrue(window.scene_note.isHidden())
            self.assertEqual(window.confirmation_profile.currentData(), 'standard')
            self.assertEqual(window.monitoring_profile.currentData(), 'full')
        window.model_profile.setCurrentIndex(window.model_profile.findData('modern'))
        self.assertEqual(window.scene_combo.currentData(), 'modern')
        self.assertFalse(window.confidence.isEnabled())
        self.assertFalse(window.association_profile.isEnabled())

    def test_typical_scenes_use_distinct_weights_and_primary_picker_separates_comparisons(self):
        self.assertEqual(SCENE_PROFILES, ('original', 'domestic', 'forest', 'uav'))
        self.assertEqual(len({spec['sha256'] for spec in SCENE_WEIGHTS.values()}), 3)
        dialog = ModelPickerDialog('original', assets=self.root)
        self.dialogs.append(dialog)
        self.assertEqual(dialog.tabs.count(), 2)
        self.assertEqual(dialog.tabs.currentIndex(), 0)
        for profile in SCENE_PROFILES:
            self.assertTrue(dialog.tabs.widget(0).isAncestorOf(dialog.choice_buttons[profile]))
        for profile in COMPARISON_PROFILES:
            self.assertTrue(dialog.tabs.widget(1).isAncestorOf(dialog.choice_buttons[profile]))
        for profile in SCENE_WEIGHTS:
            state = validate_preferences({'model_profile': profile, 'confidence': .9, 'association_profile': 'regions'})
            self.assertEqual((state['model_profile'], state['confidence'], state['association_profile']), (profile, .35, 'original'))
            self.assertIn(SCENE_WEIGHTS[profile]['path'], dict(profile_weight_specs(profile)))
            info = ModelInfoDialog(profile, assets=self.root)
            self.dialogs.append(info)
            self.assertIn(SCENE_WEIGHTS[profile]['source_url'], info.technical_details.text())

    def test_scene_models_reach_worker_restore_preferences_and_recheck_missing_files(self):
        window = self.window()
        window.set_source(Path(__file__))
        for profile in SCENE_WEIGHTS:
            window.scene_combo.setCurrentIndex(window.scene_combo.findData(profile))
            self.assertEqual(window.model_profile.currentData(), profile)
            self.assertFalse(window.association_profile.isEnabled())
            self.assertFalse(window.confidence.isEnabled())
            with patch('ui.window.AnalysisWorker') as worker_type, patch.object(window, '_attach_task'):
                window.start_analysis()
                self.assertEqual(worker_type.call_args[0][0].model_profile, profile)
            settings = self.root / (profile + '.json')
            PreferencesStore(settings).save(window._current_preferences())
            self.assertEqual(self.window(settings).scene_combo.currentData(), profile)
            with patch('ui.window.profile_availability', return_value={'ready': False, 'missing': ['removed.pt']}), \
                    patch('ui.window.AnalysisWorker') as worker_type:
                window.start_analysis()
                worker_type.assert_not_called()
                self.assertIn('removed.pt', window.last_error)

    def test_selected_scene_restores_via_existing_preferences_and_reset_returns_to_baseline(self):
        settings = self.root / 'preferences.json'
        first = self.window(settings)
        first.scene_combo.setCurrentIndex(first.scene_combo.findData('brightlight'))
        first._save_preferences(explicit=True)
        second = self.window(settings)
        self.assertEqual(second.scene_combo.currentData(), 'brightlight')
        self.assertEqual(second.model_profile.currentData(), 'brightlight')
        self.assertEqual(PreferencesStore(settings).load()[0]['model_profile'], 'brightlight')
        second.reset_preferences()
        self.assertEqual(second.scene_combo.currentData(), 'original')

    def test_busy_scene_change_is_rejected_and_missing_files_disable_start(self):
        window = self.window()
        window.set_source(Path(__file__))
        worker = AnalysisWorker(AnalysisOptions(source=str(Path(__file__))))
        window.task = worker
        window._set_controls()
        self.assertFalse(window.scene_combo.isEnabled())
        self.assertFalse(window.compare_models_button.isEnabled())
        window.scene_combo.setCurrentIndex(window.scene_combo.findData('modern'))
        self.assertEqual(window.scene_combo.currentData(), 'original')
        self.assertEqual(window.model_profile.currentData(), 'original')
        window.task = None
        with patch('ui.window.profile_availability', return_value={'ready': False, 'missing': ['missing.pt'], 'message': '模型文件缺失'}):
            window._set_controls()
            self.assertFalse(window.start_button.isEnabled())
            self.assertEqual(window.model_profile.currentData(), 'original')

    def test_scene_selection_reaches_worker_and_report_uses_recorded_not_current_model(self):
        window = self.window()
        window.set_source(Path(__file__))
        window.scene_combo.setCurrentIndex(window.scene_combo.findData('modern'))
        with patch('ui.window.AnalysisWorker') as worker_type, patch.object(window, '_attach_task'):
            window.start_analysis()
            self.assertEqual(worker_type.call_args[0][0].model_profile, 'modern')
        (self.root / 'summary.json').write_text(json.dumps({'status': 'completed', 'processed_frames': 1}), encoding='utf-8')
        config = {'source': 'example.mp4', 'model_profile': 'brightlight',
                  'model_profile_name': PROFILE_NAMES['brightlight'], 'scene_selection': selection_metadata('brightlight')}
        (self.root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        report = render_review_report(self.root)
        self.assertIn(PROFILE_NAMES['brightlight'], report)
        self.assertIn(profile_details('brightlight')['scene'], report)
        self.assertIn('确认延迟', report)
        self.assertNotIn('YOLO26', report)


if __name__ == '__main__':
    unittest.main()
