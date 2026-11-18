"""Windows batch parsing, Unicode paths, argument forwarding and exit status."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = ('start_desktop.cmd', 'start_monitor_center.cmd')


class LauncherFormatTests(unittest.TestCase):
    def test_batch_launchers_keep_crlf_without_bom_or_codepage_dependent_text(self):
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                data = (ROOT / name).read_bytes()
                self.assertTrue(data.startswith(b'@echo off\r\n'))
                self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
                data.decode('ascii')


@unittest.skipUnless(os.name == 'nt', 'Actual cmd.exe checks require Windows')
class WindowsLauncherTests(unittest.TestCase):
    def make_fixture(self, root, environment_name):
        root.mkdir()
        venv.EnvBuilder(with_pip=False).create(root / environment_name)
        for name in LAUNCHERS:
            shutil.copyfile(ROOT / name, root / name)
        (root / 'desktop.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            'Path(os.environ["FIREGUARD_LAUNCH_REPORT"]).write_text(json.dumps({"args":sys.argv[1:],'
            '"cwd":os.getcwd(),"executable":sys.executable}),encoding="utf-8")\n'
            'raise SystemExit(7 if "--fixture-failure" in sys.argv else 0)\n', encoding='ascii')
        (root / '.cache').mkdir()
        (root / '.cache/qwen-local-runtime.json').write_text('{}', encoding='ascii')
        (root / 'start_local_ai.ps1').write_text(
            "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'ai-started.txt'), 'ok')\n", encoding='ascii')

    def launch(self, root, name, arguments, unrelated, report):
        env = {**os.environ, 'FIREGUARD_LAUNCH_REPORT': str(report)}
        command = f'cmd.exe /d /s /c ""{root / name}" {arguments} <NUL"'
        return subprocess.run(command, cwd=unrelated, env=env, input='', text=True,
                              capture_output=True, encoding='utf-8', errors='replace', timeout=30)

    def test_both_launchers_work_from_other_directory_with_unicode_spaces_and_fallback_environment(self):
        with tempfile.TemporaryDirectory(prefix='fireguard-launch-') as directory:
            parent = Path(directory)
            root = parent / '焰鉴 项目 with spaces'
            self.make_fixture(root, '.venv-pytorch')
            report = parent / '运行结果 with spaces.json'
            for name in LAUNCHERS:
                with self.subTest(launcher=name):
                    process = self.launch(root, name, '"中文 参数" --marker', parent, report)
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    actual = json.loads(report.read_text(encoding='utf-8'))
                    expected = ['中文 参数', '--marker']
                    if name == 'start_monitor_center.cmd':
                        expected.insert(0, '--monitor-center')
                    self.assertEqual(actual['args'], expected)
                    self.assertEqual(Path(actual['cwd']).resolve(), root.resolve())
                    self.assertEqual(Path(actual['executable']).resolve(), (root / '.venv-pytorch/Scripts/python.exe').resolve())
                    self.assertEqual((root / 'ai-started.txt').read_text(), 'ok')

    def test_primary_environment_and_failure_exit_code_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix='fireguard-launch-') as directory:
            parent = Path(directory)
            root = parent / 'launcher project'
            self.make_fixture(root, '.venv')
            report = parent / 'failure-report.json'
            process = self.launch(root, 'start_desktop.cmd', '--fixture-failure', parent, report)
            self.assertEqual(process.returncode, 7, process.stdout + process.stderr)
            actual = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(Path(actual['executable']).resolve(), (root / '.venv/Scripts/python.exe').resolve())


if __name__ == '__main__':
    unittest.main()
