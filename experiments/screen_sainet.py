"""Screen one pinned third-party candidate with original model/evaluation intact."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.detector import Detector
from experiments.benchmark_firesense import digest, read, write
from experiments.probe_local_fire import CONFIG, prepare, run_images, run_videos

CANDIDATE_FOLDER = ROOT / 'models/candidates/sainet-2246d5ea1408'


def main():
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('images', 'videos'), required=True)
    parser.add_argument('--image-run', help='Required completed image diagnostic before full videos.')
    args = parser.parse_args()
    source_path = CANDIDATE_FOLDER / 'source-manifest.json'
    source = read(source_path)
    weights = CANDIDATE_FOLDER / 'SAINet_v11.1.pt'
    if digest(weights) != source['sha256']:
        raise ValueError('Candidate weight changed.')
    if args.stage == 'videos':
        if not args.image_run:
            raise ValueError('Specify completed image screening output first.')
        previous = Path(args.image_run).resolve()
        if (ROOT / 'outputs').resolve() not in previous.parents:
            raise ValueError('Image run must be in project outputs.')
        manifest = read(previous / 'run-manifest.json')
        previous_proposal = read(previous / 'proposal.json')
        if (manifest['status'] != 'completed' or
                previous_proposal.get('candidate_weights_sha256') != source['sha256'] or
                digest(previous / 'results.json') != manifest['results_sha256'] or
                not read(previous / 'results.json')['full_video_check_warranted']):
            raise ValueError('Image screening must be intact and warrant a video check.')
    extra = {'policy': 'Third-party SAINet replaces detector in experiment only; same 640 input, 0.35 confidence and original temporal rules. No local crops.',
             'config': {'confidence': CONFIG.confidence}, 'crop_cut_edge_policy': None, 'prior_art': [],
             'candidate_source': source, 'candidate_weights_sha256': source['sha256'],
             'candidate_weights_path': str(weights.relative_to(ROOT)),
             'candidate_source_manifest_sha256': digest(source_path),
             'image_run': args.image_run,
             'originality_note': 'Third-party checkpoint selection is not our model architecture innovation.',
             'confidence_comparison_note': 'Fixed 0.35 is an operating-point comparison; different confidence calibration means this is not model-wide superiority.',
             'image_screen_gate': 'Proceed to complete developmental videos only if at least one class gains matched boxes at fixed IoU>=0.5 and neither class loses matched box count.'}
    folder, plan, proposal, review = prepare('sainet-' + args.stage, extra,
                                            ('experiments/screen_sainet.py', 'experiments/fetch_sainet_candidate.py'))
    try:
        reference = Detector(ROOT / 'models/best.pt', confidence=CONFIG.confidence)
        candidate = Detector(weights, confidence=CONFIG.confidence)
        for detector in (reference, candidate):
            detector.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
        started = time.perf_counter()
        summary = (run_images(folder, plan, reference, candidate) if args.stage == 'images'
                   else run_videos(folder, plan, reference, review, candidate))
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
            raise ValueError('Default model changed.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'device': candidate.device,
              'candidate_names': candidate.model.names, 'candidate_weights_sha256': digest(weights),
              'proposal_sha256': digest(folder / 'proposal.json'), 'elapsed_s': time.perf_counter() - started,
              'results_sha256': digest(folder / 'results.json'), 'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'output': str(folder), **summary}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc), 'proposal_sha256': digest(folder / 'proposal.json')})
        raise


if __name__ == '__main__':
    main()
