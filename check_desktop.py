"""Environment diagnosis without model inference or automatic installation."""
import argparse
import json

from core.runtime import ROOT, configure_runtime
from core.diagnostics import collect_diagnostics, diagnostics_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=['auto', 'cpu', '0'], default='auto')
    parser.add_argument('--model-profile', choices=['original', 'brightlight', 'modern'], default='original')
    args = parser.parse_args()
    configure_runtime()
    report = collect_diagnostics(device=args.device, profile=args.model_profile)
    path = ROOT / 'outputs' / 'environment-check.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(diagnostics_text(report))
    print(f'\n本机详细记录：{path}')
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
