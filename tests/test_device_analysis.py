"""Real per-point detector choices and compatible launch configuration."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.devices import (ANALYSIS_FIELDS, DeviceRegistry, resolve_device_analysis,
                          validate_device, validate_device_analysis)
from core.model_profiles import PROFILE_NAMES, SCENE_WEIGHTS, profile_weight_specs


class DeviceAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = {'id': 'north1', 'name': '北门', 'location': '教学楼北门',
                       'kind': 'file', 'source': str(self.root / 'clip.mp4')}
        self.ready = patch('core.devices.profile_availability',
                           side_effect=lambda profile: {'profile': profile, 'ready': True, 'missing': []})
        self.availability = self.ready.start()
        self.addCleanup(self.ready.stop)

    def test_legacy_registry_inherits_all_global_settings(self):
        path = self.root / 'devices.json'
        path.write_text(json.dumps({'version': 1, 'devices': [self.device]}), encoding='utf-8')
        device = DeviceRegistry(path).load()[0]
        self.assertTrue(all(device[key] == 'inherit' for key in ANALYSIS_FIELDS))
        resolved = resolve_device_analysis(device, {'model_profile': 'brightlight',
            'association_profile': 'regions', 'confirmation_profile': 'fast', 'conf': .75})
        self.assertEqual((resolved['model_profile'], resolved['association_profile'], resolved['confirmation_profile']),
                         ('brightlight', 'regions', 'fast'))
        self.assertEqual(resolved['conf'], .35)

    def test_every_public_model_maps_to_the_actual_registered_profile(self):
        shared = {'model_profile': 'brightlight', 'association_profile': 'regions',
                  'confirmation_profile': 'fast', 'conf': .6}
        for profile in PROFILE_NAMES:
            with self.subTest(profile=profile):
                resolved = resolve_device_analysis({**self.device, 'model_profile': profile}, shared)
                self.assertEqual(resolved['model_profile'], profile)
                self.assertEqual(resolved['association_profile'], 'original')
                self.assertEqual(resolved['confirmation_profile'], 'fast')
                self.assertEqual(resolved['scene_selection']['profile'], profile)
                self.assertTrue(profile_weight_specs(profile))
                self.assertEqual(resolved['conf'], .6 if profile == 'original' else .35)
                if profile in SCENE_WEIGHTS:
                    self.assertEqual(resolved['scene_selection']['source_url'], SCENE_WEIGHTS[profile]['source_url'])
        self.assertNotIn('forest_near', PROFILE_NAMES)

    def test_selected_model_is_not_overwritten_by_another_points_model(self):
        shared = {'model_profile': 'original', 'association_profile': 'original', 'confirmation_profile': 'standard'}
        first = resolve_device_analysis({**self.device, 'model_profile': 'forest'}, shared)
        second = resolve_device_analysis({**self.device, 'model_profile': 'uav', 'confirmation_profile': 'fast'}, shared)
        third = resolve_device_analysis(self.device, shared)
        self.assertEqual([first['model_profile'], second['model_profile'], third['model_profile']], ['forest', 'uav', 'original'])
        self.assertEqual(shared['model_profile'], 'original')

    def test_explicit_profiles_round_trip_without_changing_device_identity(self):
        device = {**self.device, 'model_profile': 'brightlight', 'association_profile': 'regions',
                  'confirmation_profile': 'fast'}
        registry = DeviceRegistry(self.root / 'devices.json')
        checked = registry.save([device])[0]
        self.assertEqual(registry.load()[0], checked)
        self.assertEqual(checked['id'], self.device['id'])
        self.assertEqual(checked['model_profile'], 'brightlight')
        self.assertEqual(checked['association_profile'], 'regions')

    def test_incompatible_regions_profile_is_never_saved_or_resolved(self):
        for profile in ('original', 'forest', 'domestic', 'uav', 'modern'):
            value = {**self.device, 'model_profile': profile, 'association_profile': 'regions'}
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    validate_device(value)
                with self.assertRaises(ValueError):
                    resolve_device_analysis(value, {})
        # The inherited model is unknown at edit time, so resolve it at launch.
        value = {**self.device, 'association_profile': 'regions'}
        validate_device(value)
        with self.assertRaises(ValueError):
            resolve_device_analysis(value, {'model_profile': 'forest'})
        self.assertEqual(resolve_device_analysis(value, {'model_profile': 'brightlight'})['association_profile'], 'regions')

    def test_baseline_mode_cannot_start_regions_association(self):
        with self.assertRaises(ValueError):
            resolve_device_analysis({**self.device, 'model_profile': 'brightlight', 'association_profile': 'regions'},
                                    {'baseline': True})

    def test_missing_models_are_not_silently_replaced(self):
        self.availability.side_effect = lambda profile: {'profile': profile, 'ready': False, 'missing': ['missing.pt']}
        checked = validate_device({**self.device, 'model_profile': 'forest'})
        self.assertEqual(checked['model_profile'], 'forest')
        with self.assertRaisesRegex(ValueError, 'missing.pt'):
            resolve_device_analysis(checked, {'model_profile': 'original'})

    def test_unknown_or_malformed_choices_and_invalid_global_values_fail(self):
        for field in ANALYSIS_FIELDS:
            for value in (None, False, ['original'], 'invented'):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        validate_device_analysis({field: value})
        for shared in ({'model_profile': []}, {'confirmation_profile': []},
                       {'association_profile': []}, {'conf': float('nan')}, {'conf': True}):
            with self.subTest(shared=shared):
                with self.assertRaises(ValueError):
                    resolve_device_analysis(self.device, shared)


if __name__ == '__main__':
    unittest.main()
