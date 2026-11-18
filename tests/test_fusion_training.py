"""Synthetic software-contract fixtures only: no real hazard labels or accuracy claims.

These temporary records exercise the actual preparation, independent-annotation,
training-artifact and replay APIs. Labels describe fixture branches, not the tiny
placeholder image's safety. Nothing is added to the user's training data.
"""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from core.adaptive_fusion import FusionModel
from core.fusion_dataset import (adjudicated_samples, load_dataset, prepare_dataset,
                                 save_annotation, scene_record_key)
from core.fusion_shadow import FusionShadow
from train_scene_fusion import dataset_status, replay_dataset, train_dataset


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jX1kAAAAASUVORK5CYII=')


class FusionTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fusion-software-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_run(self, index):
        """All bytes, scene responses, and labels here are synthetic test fixtures."""
        run = self.root / f'run-{index}'
        run.mkdir()
        source = self.root / f'video-{index}.mp4'
        source.write_bytes(f'Software-contract source {index}; not a real recording.'.encode())
        (run / 'config.json').write_text(json.dumps({'source': str(source), 'source_kind': 'file'}), encoding='utf-8')
        (run / 'scene-evidence').mkdir()
        image = run / 'scene-evidence' / 'frame.png'
        image.write_bytes(PNG)
        digest = hashlib.sha256(PNG).hexdigest()
        risks, records, fusion = [], [], []
        for branch, timestamp in enumerate((5., 10.)):
            event = {'event_id': 1, 'label': 'fire', 'score': 48. if not branch else 75.,
                'level': 'MEDIUM' if not branch else 'HIGH', 'fresh_evidence': True,
                'features': {'label': 'fire', 'event_id': 1, 'fresh_evidence': True,
                    'coverage_ratio_smoothed': .03 if not branch else .12,
                    'positive_duration_s': 1. if not branch else 6.,
                    'growth_pp_per_s': 0. if not branch else 2., 'confidence': .9}}
            risks.append({'timestamp_s': timestamp, 'events': [event], 'score': event['score'],
                'level': event['level'], 'fresh_evidence': True})
            scene = {'description': '软件测试用场景意见，不构成实际危险判断。', 'scene': 'candle',
                'relation': 'expected_burning' if not branch else 'suspected_abnormal',
                'evidence_quality': 'adequate', 'spread': 'not_observed' if not branch else 'observed',
                'visible_evidence': ['软件测试占位原画面'],
                'hazard_cues': [] if not branch else ['软件测试异常分支'],
                'uncertainties': [], 'next_check': '仅验证软件数据流。'}
            record = {'model': 'software-contract-vision', 'event_id': None, 'frame_count': 3,
                'span_s': 1., 'elapsed_s': .1, 'created_at': f'2026-11-17T10:00:{branch:02d}+08:00',
                'review_profile': 'fast', 'scene': scene,
                'evidence': [{'timestamp_s': timestamp - 2. + step * .5,
                    'sha256': digest, 'image': 'scene-evidence/frame.png'} for step in range(3)]}
            records.append(record)
            fusion.append({'timestamp_s': timestamp, 'fusion': {
                'scene_record_key': scene_record_key(record), 'scene_available_at_s': timestamp - .5}})
        for name, rows in (('risk.jsonl', risks), ('scene-reviews.jsonl', records), ('fusion.jsonl', fusion)):
            (run / name).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        return run

    def make_dataset(self, count=3, *, annotated=True):
        runs = [self.make_run(index) for index in range(count)]
        path = self.root / 'dataset'
        manifest = prepare_dataset(runs, path)
        self.assertEqual(len(manifest['samples']), 2 * count)
        self.assertEqual(len(manifest['groups']), count)
        if annotated:
            for sample in manifest['samples']:
                label = 'normal' if sample['timestamp_s'] == 5. else 'abnormal'
                for reviewer in ('software-reviewer-a', 'software-reviewer-b'):
                    save_annotation(path, sample['sample_id'], reviewer=reviewer, label=label,
                        note='Synthetic software-contract fixture, not a true hazard label.')
        groups = [next(sample['source_group'] for sample in manifest['samples']
            if sample['run_path'] == str(run.resolve())) for run in runs]
        return path, manifest, groups

    def train(self, dataset, groups, name='candidate'):
        target = self.root / name
        result = train_dataset(dataset, target, validation_groups=[groups[-1]], iterations=20)
        return target, result

    def test_real_dataset_api_train_and_replay_preserve_source_isolation(self):
        path, manifest, groups = self.make_dataset()
        accepted, exclusions = adjudicated_samples(path)
        self.assertEqual(len(accepted), 6)
        self.assertEqual(exclusions, [])
        self.assertTrue(dataset_status(path)['ready_for_training'])
        target, result = self.train(path, groups)
        self.assertFalse(result['online_enabled'])
        self.assertEqual((result['training_samples'], result['validation_samples']), (4, 2))
        model = FusionModel.load(target / 'model.json')
        self.assertEqual(set(model.training['source_groups']), set(groups[:-1]))
        self.assertNotIn(groups[-1], model.training['source_groups'])
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['validation_groups'], [groups[-1]])
        self.assertTrue(set(report['training_groups']).isdisjoint(report['validation_groups']))
        self.assertEqual(set(report['validation']), {'interactions', 'no_interactions'})
        self.assertTrue(all(case['source_group'] == groups[-1]
            for case in report['validation']['interactions']['cases']))
        self.assertEqual(model.to_dict()['status'], 'development')
        self.assertFalse(model.to_dict()['calibrated'])
        self.assertFalse(model.to_dict()['residual']['validated'])
        replay = self.root / 'replay'
        replay_result = replay_dataset(path, target / 'model.json', replay)
        self.assertEqual(replay_result['replayed'], 6)
        self.assertFalse(replay_result['affects_notifications'])
        rows = json.loads((replay / 'replay.json').read_text(encoding='utf-8'))['samples']
        for row in rows:
            self.assertEqual(row['source_seen_in_training'], row['source_group'] in groups[:-1])
            self.assertEqual(row['candidate']['model_id'], model.model_id)
            self.assertEqual(row['candidate']['status'], 'ready')

    def test_unreviewed_data_and_one_reviewer_cannot_be_trained(self):
        path, manifest, groups = self.make_dataset(annotated=False)
        target = self.root / 'unreviewed-model'
        self.assertEqual(dataset_status(path)['eligible_labels'], 0)
        with self.assertRaises(ValueError):
            train_dataset(path, target, validation_groups=[groups[-1]], iterations=5)
        for sample in manifest['samples']:
            save_annotation(path, sample['sample_id'], reviewer='one-reviewer',
                label='normal' if sample['timestamp_s'] == 5. else 'abnormal')
        with self.assertRaises(ValueError):
            train_dataset(path, target, validation_groups=[groups[-1]], iterations=5)
        self.assertFalse(target.exists())

    def test_validation_group_must_be_explicit_and_leaves_adequate_training_sources(self):
        path, manifest, groups = self.make_dataset()
        for held in ([], ['unregistered-source'], groups, groups[1:]):
            with self.subTest(held=held), self.assertRaises(ValueError):
                train_dataset(path, self.root / 'invalid-model', validation_groups=held, iterations=5)
        self.assertFalse((self.root / 'invalid-model').exists())

    def test_same_source_clips_cannot_be_split_between_train_and_validation(self):
        path, manifest, groups = self.make_dataset(count=1)
        self.assertEqual({sample['label'] for sample in adjudicated_samples(path)[0]}, {0, 1})
        with self.assertRaises(ValueError):
            train_dataset(path, self.root / 'same-source-model', validation_groups=groups, iterations=5)
        self.assertFalse((self.root / 'same-source-model').exists())

    def test_outputs_are_exclusive_for_train_and_replay(self):
        path, manifest, groups = self.make_dataset()
        target = self.root / 'keep-existing'
        target.mkdir()
        sentinel = target / 'operator-file.txt'
        sentinel.write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            train_dataset(path, target, validation_groups=[groups[-1]], iterations=5)
        self.assertEqual(list(target.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), 'keep')
        trained, _ = self.train(path, groups)
        with self.assertRaises(FileExistsError):
            replay_dataset(path, trained / 'model.json', target)
        self.assertEqual(list(target.iterdir()), [sentinel])

    def test_development_artifact_can_run_only_as_shadow_and_bad_artifact_is_isolated(self):
        path, manifest, groups = self.make_dataset()
        target, _ = self.train(path, groups)
        shadow = FusionShadow(target / 'model.json')
        sample = manifest['samples'][0]
        raw = {'timestamp_s': sample['timestamp_s'], 'events': [sample['baseline']]}
        before = json.dumps(raw, sort_keys=True)
        result = shadow.update(raw, sample['scene_record'])
        self.assertEqual(result['mode'], 'shadow')
        self.assertFalse(result['affects_notifications'])
        self.assertEqual(before, json.dumps(raw, sort_keys=True))
        self.assertEqual(result['events'][0]['status'], 'ready')
        invalid = self.root / 'bad.json'
        invalid.write_text('{"status":"promoted"}', encoding='utf-8')
        unavailable = FusionShadow(invalid)
        self.assertEqual(unavailable.metadata()['status'], 'unavailable')
        self.assertFalse(unavailable.metadata()['affects_notifications'])
        self.assertNotIn('events', unavailable.update(raw, sample['scene_record']))


if __name__ == '__main__':
    unittest.main()
