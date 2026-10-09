"""Explicit download with a pinned revision and provenance manifest."""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone

from core.runtime import ROOT, configure_runtime


def main():
    configure_runtime()
    from huggingface_hub import HfApi, hf_hub_download

    repo = 'rabahdev/fire-smoke-yolov8n'
    manifest_path = ROOT / 'models' / 'manifest.json'
    previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}
    revision = previous.get('revision') if previous.get('repo_id') == repo else None
    info = HfApi().model_info(repo, revision=revision, files_metadata=True)
    entry = next(item for item in info.siblings if item.rfilename == 'best.pt')
    downloaded = hf_hub_download(repo, 'best.pt', revision=info.sha)
    with open(downloaded, 'rb') as model_file:
        digest = hashlib.sha256(model_file.read()).hexdigest()
    expected = entry.lfs.sha256 if entry.lfs else None
    if expected and digest != expected:
        raise RuntimeError('模型文件校验失败，未复制到 models 目录。')
    if revision and previous.get('sha256') and digest != previous['sha256']:
        raise RuntimeError('模型与已登记版本的 SHA256 不一致，未覆盖现有模型。')
    target = ROOT / 'models' / 'best.pt'
    shutil.copyfile(downloaded, target)
    manifest = {
        'repo_id': repo, 'revision': info.sha, 'filename': 'best.pt',
        'source_url': f'https://huggingface.co/{repo}',
        'license_declared_by_repository': 'agpl-3.0',
        'sha256': digest, 'repository_sha256': expected,
        'size_bytes': target.stat().st_size,
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
        'training_provenance': 'Repository describes a YOLOv8n D-Fire fine-tune; not trained by this project.',
    }
    (ROOT / 'models' / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'模型已保存：{target}\nSHA256：{digest}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(f'下载失败：{exc}', file=sys.stderr)
        sys.exit(1)

