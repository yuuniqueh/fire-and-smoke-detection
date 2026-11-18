"""Per-point model editing uses real local profile definitions without inference."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from core.model_profiles import PROFILE_NAMES, profile_weight_specs
from ui.point_models import PointModelSelector


class PointModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        for profile in PROFILE_NAMES:
            for relative, _ in profile_weight_specs(profile):
                path = self.assets / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'presence-only-test-fixture')

    def widget(self, device=None, **kwargs):
        widget = PointModelSelector(device, assets=kwargs.get('assets', self.assets))
        self.addCleanup(widget.deleteLater)
        return widget

    def test_default_is_inherited_and_all_available_options_are_real(self):
        widget = self.widget()
        self.assertEqual(widget.value(), {'model_profile': 'inherit', 'association_profile': 'inherit',
                                          'confirmation_profile': 'inherit'})
        self.assertIn('跟随全局', widget.model_profile.currentText())
        values = {widget.model_profile.itemData(index) for index in range(widget.model_profile.count())}
        self.assertEqual(values, {'inherit', *PROFILE_NAMES})
        self.assertFalse(widget.details_button.isEnabled())
        self.assertFalse(widget.advanced_toggle.isChecked())
        self.assertTrue(widget.advanced.isHidden())
        self.assertIn('AI画面理解共用本机视觉模型', widget.explanation.text())
        widget.advanced_toggle.setChecked(True)
        self.assertFalse(widget.advanced.isHidden())

    def test_existing_choice_and_confirmation_are_loaded_and_editable(self):
        widget = self.widget({'model_profile': 'forest', 'confirmation_profile': 'fast'})
        self.assertEqual(widget.value()['model_profile'], 'forest')
        self.assertEqual(widget.value()['confirmation_profile'], 'fast')
        self.assertIn('山林', widget.summary.text())
        self.assertTrue(widget.details_button.isEnabled())
        widget.set_model_profile('uav')
        self.assertEqual(widget.value()['model_profile'], 'uav')
        widget.set_confirmation_profile('standard')
        self.assertEqual(widget.value()['confirmation_profile'], 'standard')

    def test_association_options_follow_the_actual_model(self):
        widget = self.widget({'model_profile': 'brightlight', 'association_profile': 'regions'})
        self.assertEqual(widget.value()['association_profile'], 'regions')
        widget.model_profile.setCurrentIndex(widget.model_profile.findData('forest'))
        self.assertEqual(widget.value()['association_profile'], 'inherit')
        self.assertEqual(widget.association_profile.findData('regions'), -1)
        with self.assertRaises(ValueError):
            widget.set_association_profile('regions')
        with self.assertRaises(ValueError):
            widget.set_value({'model_profile': 'uav', 'association_profile': 'regions'})

    def test_missing_weights_disable_new_selection_and_preserve_stored_choice(self):
        missing_assets = self.assets / 'empty'
        missing_assets.mkdir()
        widget = self.widget({'model_profile': 'forest'}, assets=missing_assets)
        self.assertEqual(widget.value()['model_profile'], 'forest')
        index = widget.model_profile.findData('forest')
        self.assertFalse(widget.model_profile.model().item(index).isEnabled())
        self.assertIn('尚未就绪', widget.summary.text())

    def test_model_introduction_uses_the_actual_registered_source_dialog(self):
        widget = self.widget({'model_profile': 'uav'})
        with patch('ui.point_models.ModelInfoDialog') as dialog:
            widget.show_model_info()
            dialog.assert_called_once_with('uav', widget, assets=self.assets)
            dialog.return_value.exec.assert_called_once()
        widget.set_model_profile('inherit')
        with patch('ui.point_models.ModelInfoDialog') as dialog:
            widget.show_model_info()
            dialog.assert_not_called()


if __name__ == '__main__':
    unittest.main()
