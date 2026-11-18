"""Build an allowlisted local source handoff, optionally with registered weights."""
import argparse
from pathlib import Path

from core.runtime import ROOT
from core.transfer import build_transfer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs' / 'FireGuard-transfer.zip')
    parser.add_argument('--include-models', action='store_true')
    args = parser.parse_args()
    manifest = build_transfer(ROOT, args.output, args.include_models)
    print(f'迁移包：{args.output.resolve()}')
    print(f'文件数：{len(manifest["files"])}；包含模型：{manifest["contains_models"]}；不包含 Python 环境。')


if __name__ == '__main__':
    main()
