"""Causal candidate preparation and independent, source-grouped labels."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.fusion_dataset import (adjudicated_samples, annotation_summary, load_dataset,
                                 prepare_dataset, save_annotation, scene_record_key)


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.mp4'
        self.source.write_bytes(b'original-video')

    def run_log(self, name='recording', *, source=None, exact=True, scene=True, event_id=1):
        run = self.root / name
        run.mkdir()
        config = {'source': str(source or self.source), 'source_kind': 'file'}
        (run / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        rows = [{'timestamp_s': timestamp, 'score': 45., 'level': 'LOW', 'events': [
                    {'event_id': event_id, 'label': 'fire', 'score': 45., 'level': 'LOW',
                     'features': {'confidence': .8, 'coverage_ratio_smoothed': .04}}]}
                for timestamp in (0., 1., 2., 5., 6., 10.)]
        (run / 'risk.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        if scene:
            folder = run / 'scene-evidence'
            folder.mkdir()
            image = folder / 'original.jpg'
            image.write_bytes(b'independently-hashed-frame')
            record = {'model': 'local-vision', 'created_at': '2026-11-17T10:00:00+08:00',
                      'review_profile': 'fast', 'elapsed_s': 2., 'event_id': event_id,
                      'frame_count': 1, 'span_s': 0.,
                      'scene': {'scene': 'candle', 'relation': 'expected_burning'},
                      'evidence': [{'timestamp_s': 1., 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                                    'image': 'scene-evidence/original.jpg'}]}
            (run / 'scene-reviews.jsonl').write_text(json.dumps(record) + '\n', encoding='utf-8')
            if exact:
                fusion = {'timestamp_s': 5., 'fusion': {'scene_record_key': scene_record_key(record),
                                                       'scene_available_at_s': 4.}}
                (run / 'fusion.jsonl').write_text(json.dumps(fusion) + '\n', encoding='utf-8')
        return run

    def prepare(self, run=None, **kwargs):
        destination = self.root / 'dataset'
        manifest = prepare_dataset([run or self.run_log()], destination, **kwargs)
        return destination, manifest

    def agree(self, path, sample_id, label='normal'):
        save_annotation(path, sample_id, reviewer='Alice', label=label)
        save_annotation(path, sample_id, reviewer='Bob', label=label)

    def test_actual_causal_time_does_not_backfill_future_ai(self):
        path, manifest = self.prepare()
        first, second, third = manifest['samples']
        self.assertEqual([item['timestamp_s'] for item in manifest['samples']], [0., 5., 10.])
        self.assertIsNone(first['scene_record'])
        self.assertEqual(second['availability'], 'exact')
        self.assertEqual(second['scene_available_at_s'], 4.)
        self.assertEqual(second['active_event_count'], 1)
        self.assertEqual(second['scene_record'], third['scene_record'])
        self.assertTrue(second['evidence_images'][0]['verified'])
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_old_estimated_timing_is_reviewable_but_excluded_from_fitting(self):
        path, manifest = self.prepare(self.run_log(exact=False))
        sample = manifest['samples'][1]
        self.assertEqual(sample['estimated_ready_time'], 3.)
        self.agree(path, sample['sample_id'])
        accepted, excluded = adjudicated_samples(path)
        self.assertEqual(accepted, [])
        self.assertTrue(any('估计AI' in row['reason'] for row in excluded))

    def test_same_reviewer_revisions_never_count_twice(self):
        path, manifest = self.prepare()
        sample = manifest['samples'][1]
        save_annotation(path, sample['sample_id'], reviewer='Alice', label='abnormal')
        save_annotation(path, sample['sample_id'], reviewer=' alice ', label='normal')
        self.assertEqual(adjudicated_samples(path)[0], [])
        save_annotation(path, sample['sample_id'], reviewer='Bob', label='normal')
        accepted, _ = adjudicated_samples(path)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]['label'], 0)
        self.assertEqual(accepted[0]['reviewers'], ['alice', 'bob'])
        self.assertEqual(annotation_summary(path)[sample['sample_id']]['label'], 'normal')

    def test_disagreement_and_uncertain_excluded(self):
        path, manifest = self.prepare()
        sample = manifest['samples'][1]
        self.agree(path, sample['sample_id'], 'abnormal')
        self.assertEqual(adjudicated_samples(path)[0][0]['label'], 1)
        save_annotation(path, sample['sample_id'], reviewer='Bob', label='normal')
        self.assertEqual(annotation_summary(path)[sample['sample_id']]['status'], 'disagreement')
        self.assertEqual(adjudicated_samples(path)[0], [])
        save_annotation(path, sample['sample_id'], reviewer='Bob', label='uncertain')
        self.assertEqual(annotation_summary(path)[sample['sample_id']]['status'], 'uncertain')

    def test_workflow_feedback_is_never_a_label(self):
        run = self.run_log()
        (run / 'feedback.jsonl').write_text(json.dumps({'conclusion': 'confirmed_abnormal',
                                                       'workflow_status': 'completed'}), encoding='utf-8')
        path, manifest = self.prepare(run)
        self.assertTrue(all(sample['label'] is None for sample in manifest['samples']))
        self.assertEqual(adjudicated_samples(path)[0], [])
        self.assertEqual(load_dataset(path)['annotations'], [])

    def test_qa_candidates_remain_excluded_even_after_agreement(self):
        path, manifest = self.prepare(self.run_log(name='qa-run'))
        sample = manifest['samples'][1]
        self.assertTrue(sample['is_qa'])
        self.agree(path, sample['sample_id'])
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_image_hash_mismatch_is_explicit_and_excludes_sample(self):
        run = self.run_log()
        (run / 'scene-evidence' / 'original.jpg').write_bytes(b'changed-before-preparation')
        path, manifest = self.prepare(run)
        sample = manifest['samples'][1]
        self.assertFalse(sample['evidence_images'][0]['verified'])
        self.assertIn('evidence_integrity', sample['integrity_errors'])
        self.agree(path, sample['sample_id'])
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_image_hash_is_rechecked_before_fitting(self):
        run = self.run_log()
        path, manifest = self.prepare(run)
        sample = manifest['samples'][1]
        self.agree(path, sample['sample_id'])
        (run / 'scene-evidence' / 'original.jpg').write_bytes(b'changed-after-preparation')
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_image_traversal_not_exposed(self):
        run = self.run_log()
        log = run / 'scene-reviews.jsonl'
        record = json.loads(log.read_text())
        record['evidence'][0]['image'] = '../original.mp4'
        record['evidence'][0]['sha256'] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        log.write_text(json.dumps(record) + '\n', encoding='utf-8')
        path, manifest = self.prepare(run)
        sample = manifest['samples'][1]
        self.assertEqual(sample['evidence_images'][0]['path'], '')
        self.assertFalse(sample['evidence_images'][0]['verified'])

    def test_source_groups_and_duplicates_survive_replay_event_number_changes(self):
        first = self.run_log('first', event_id=1)
        second = self.run_log('second', event_id=9)
        manifest = prepare_dataset([first, second], self.root / 'dataset')
        self.assertEqual(len(manifest['groups']), 1)
        self.assertEqual(len(manifest['samples']), 3)
        self.assertEqual(len({item['sample_id'] for item in manifest['samples']}), 3)
        self.assertTrue(manifest['preparation_exclusions'])
        other = self.root / 'other.mp4'
        other.write_bytes(b'different-video')
        third = self.run_log('third', source=other)
        two = prepare_dataset([first, third], self.root / 'two')
        self.assertEqual(len(two['groups']), 2)
        self.assertEqual(len({item['sample_id'] for item in two['samples']}), 6)

    def test_copied_video_has_same_content_group_and_sample_ids(self):
        copy = self.root / 'copied-with-another-name.mp4'
        copy.write_bytes(self.source.read_bytes())
        first, second = self.run_log('first'), self.run_log('second', source=copy, event_id=9)
        original = prepare_dataset([first], self.root / 'original-dataset')
        copied = prepare_dataset([second], self.root / 'copied-dataset')
        self.assertEqual(original['samples'][0]['source_group'], copied['samples'][0]['source_group'])
        self.assertEqual([item['sample_id'] for item in original['samples']],
                         [item['sample_id'] for item in copied['samples']])
        combined = prepare_dataset([first, second], self.root / 'combined')
        self.assertEqual(len(combined['samples']), 3)
        self.assertEqual(combined['samples'][0]['source_sha256'],
                         hashlib.sha256(self.source.read_bytes()).hexdigest())

    def test_complete_source_hash_includes_bytes_after_the_first_megabyte(self):
        first_source, second_source = self.root / 'large-a.mp4', self.root / 'large-b.mp4'
        prefix = b'X' * (1024 * 1024)
        first_source.write_bytes(prefix + b'tail-a')
        second_source.write_bytes(prefix + b'tail-b')
        first = self.run_log('first', source=first_source)
        second = self.run_log('second', source=second_source)
        manifest = prepare_dataset([first, second], self.root / 'dataset')
        self.assertEqual(len(manifest['groups']), 2)
        hashes = {item['source_sha256'] for item in manifest['samples']}
        self.assertEqual(hashes, {hashlib.sha256(first_source.read_bytes()).hexdigest(),
                                 hashlib.sha256(second_source.read_bytes()).hexdigest()})

    def test_manual_scene_group_unifies_distinct_videos_without_dropping_samples(self):
        second_source = self.root / 'second-capture.mp4'
        second_source.write_bytes(b'new-video-from-the-same-camera')
        first, second = self.run_log('first'), self.run_log('second', source=second_source)
        manifest = prepare_dataset([first, second], self.root / 'dataset',
                                   source_groups={str(self.source): 'camera-location-A',
                                                  str(second_source): 'camera-location-A'})
        self.assertEqual(len(manifest['groups']), 1)
        self.assertEqual(len(manifest['samples']), 6)
        self.assertEqual(len({item['sample_id'] for item in manifest['samples']}), 6)
        self.assertEqual(len({item['content_group'] for item in manifest['samples']}), 2)

    def test_manual_group_applies_to_unmapped_identical_copy(self):
        copy = self.root / 'copied.mp4'
        copy.write_bytes(self.source.read_bytes())
        first, second = self.run_log('first'), self.run_log('second', source=copy)
        manifest = prepare_dataset([first, second], self.root / 'dataset',
                                   source_groups={str(copy): 'location-A'})
        self.assertEqual(len(manifest['groups']), 1)
        self.assertEqual(next(iter(manifest['groups'].values()))['manual_group'], 'location-A')
        with self.assertRaises(ValueError):
            prepare_dataset([first, second], self.root / 'conflicting',
                            source_groups={str(copy): 'location-A', str(self.source): 'location-B'})

    def test_missing_and_over_limit_source_identity_cannot_train(self):
        run = self.run_log(source=self.root / 'missing.mp4')
        path, manifest = self.prepare(run)
        sample = manifest['samples'][1]
        self.assertTrue(sample['identity_unverified'])
        self.assertEqual(sample['identity_reason'], 'source_missing')
        self.agree(path, sample['sample_id'])
        self.assertEqual(adjudicated_samples(path)[0], [])
        other_run = self.run_log('second')
        with patch('core.fusion_dataset.MAX_SOURCE_BYTES', 1):
            limited = prepare_dataset([other_run], self.root / 'limited')
        self.assertTrue(limited['samples'][0]['identity_unverified'])
        self.assertEqual(limited['samples'][0]['identity_reason'], 'source_exceeds_2_gib_limit')

    def test_source_content_is_rechecked_before_fitting(self):
        path, manifest = self.prepare()
        sample = manifest['samples'][1]
        self.agree(path, sample['sample_id'])
        self.source.write_bytes(b'replaced-original-source')
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_legacy_path_only_identity_remains_reviewable_but_cannot_train(self):
        path, manifest = self.prepare()
        sample = manifest['samples'][1]
        self.agree(path, sample['sample_id'])
        file = path / 'manifest.json'
        legacy = json.loads(file.read_text(encoding='utf-8'))
        for item in legacy['samples']:
            item.pop('source_sha256')
            item.pop('source_kind')
            item.pop('identity_unverified')
        file.write_text(json.dumps(legacy), encoding='utf-8')
        self.assertEqual(len(load_dataset(path)['samples']), 3)
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_exact_log_identity_mismatch_does_not_grant_causal_status(self):
        run = self.run_log()
        log = run / 'fusion.jsonl'
        row = json.loads(log.read_text())
        row['fusion']['scene_record_key'] = 'a' * 64
        log.write_text(json.dumps(row) + '\n', encoding='utf-8')
        path, manifest = self.prepare(run)
        sample = manifest['samples'][1]
        self.assertEqual(sample['availability'], 'estimated')
        self.agree(path, sample['sample_id'])
        self.assertEqual(adjudicated_samples(path)[0], [])

    def test_exact_consumption_never_makes_expired_or_future_evidence_current(self):
        run = self.run_log()
        fusion_log = run / 'fusion.jsonl'
        fusion = json.loads(fusion_log.read_text())
        fusion.update(timestamp_s=20.)
        fusion['fusion']['scene_available_at_s'] = 16.
        fusion_log.write_text(json.dumps(fusion) + '\n', encoding='utf-8')
        risk_log = run / 'risk.jsonl'
        rows = [json.loads(line) for line in risk_log.read_text().splitlines()]
        last = json.loads(json.dumps(rows[-1]))
        last['timestamp_s'] = 20.
        rows.append(last)
        risk_log.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        _, manifest = self.prepare(run)
        # Earlier samples precede availability; the later one is >12s after its
        # source frame. Neither may carry that AI answer into model features.
        self.assertTrue(all(item['scene_record'] is None for item in manifest['samples']))

    def test_shared_key_with_different_scene_answers_is_rejected(self):
        run = self.run_log()
        log = run / 'scene-reviews.jsonl'
        record = json.loads(log.read_text())
        changed = json.loads(json.dumps(record))
        changed['scene']['relation'] = 'suspected_abnormal'
        self.assertEqual(scene_record_key(record), scene_record_key(changed))
        log.write_text(json.dumps(record) + '\n' + json.dumps(changed) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prepare(run)

    def test_key_ignores_paths_and_ui_fields_but_keeps_evidence_identity(self):
        record = {'model': 'vision', 'created_at': 'now', 'review_profile': 'fast',
                  'evidence': [{'timestamp_s': 2., 'sha256': 'a' * 64, 'image': 'a.jpg'}]}
        changed = json.loads(json.dumps(record))
        changed['evidence'][0]['image'] = 'another-path.jpg'
        changed['ui_description'] = 'UI only'
        self.assertEqual(scene_record_key(record), scene_record_key(changed))
        changed['evidence'][0]['timestamp_s'] = 3.
        self.assertNotEqual(scene_record_key(record), scene_record_key(changed))

    def test_invalid_future_availability_rejected(self):
        run = self.run_log()
        path = run / 'fusion.jsonl'
        row = json.loads(path.read_text())
        row['fusion']['scene_available_at_s'] = 6.
        path.write_text(json.dumps(row) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prepare(run)

    def test_destination_is_never_overwritten_and_sampling_is_bounded(self):
        run = self.run_log()
        path, manifest = self.prepare(run, max_samples=2)
        self.assertEqual(len(manifest['samples']), 2)
        before = (path / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            prepare_dataset([run], path)
        self.assertEqual(before, (path / 'manifest.json').read_bytes())
        with self.assertRaises(ValueError):
            save_annotation(path, manifest['samples'][0]['sample_id'], reviewer='Alice', label='resolved')


if __name__ == '__main__':
    unittest.main()
