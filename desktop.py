"""Windows desktop entry point. No model download or training on startup."""
import sys

from core.runtime import ROOT, configure_runtime


def main():
    configure_runtime()
    if '--fusion-shadow-model' in sys.argv:
        import argparse
        import os
        from pathlib import Path
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('--fusion-shadow-model', required=True)
        shadow_args, _ = parser.parse_known_args()
        os.environ['FIREGUARD_FUSION_SHADOW_MODEL'] = str(Path(shadow_args.fusion_shadow_model).resolve())
    if '--self-test' in sys.argv:
        import argparse
        from core.package_check import run_package_check
        parser = argparse.ArgumentParser()
        parser.add_argument('--self-test', required=True)
        parser.add_argument('--self-test-video')
        parser.add_argument('--self-test-ai-endpoint')
        args = parser.parse_args()
        return run_package_check(args.self_test, args.self_test_video, args.self_test_ai_endpoint)
    startup_report = None
    if '--startup-check' in sys.argv:
        import argparse
        from pathlib import Path
        parser = argparse.ArgumentParser()
        parser.add_argument('--startup-check', required=True)
        args, _ = parser.parse_known_args()
        startup_report = Path(args.startup_check).resolve()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from ui.window import MainWindow
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setApplicationName('焰鉴')
    from ui.product import ui_font
    app.setFont(ui_font())
    window = MainWindow(settings_path=False) if startup_report else MainWindow()
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(max(980, min(1280, available.width() - 60)),
                      max(740, min(920, available.height() - 60)))
    window.show()
    if '--monitor-center' in sys.argv:
        window.navigate('center')
    if startup_report:
        import json
        from PySide6.QtCore import QTimer
        def check_startup():
            from PySide6.QtGui import QFontInfo
            expected_page = '监控台'
            checks = {'window_visible': window.isVisible(), 'eight_pages': window.tabs.count() == 8,
                      'expected_page': window.page_title.text() == expected_page,
                      'scene_description_present': hasattr(window, 'scene_description'),
                      'no_analysis_started': not window.busy}
            startup_report.parent.mkdir(parents=True, exist_ok=True)
            font_info = QFontInfo(window.review_choice.font())
            window.grab().save(str(startup_report.with_suffix('.png')))
            startup_report.write_text(json.dumps({'ok': all(checks.values()), 'checks': checks,
                'font': {'family': font_info.family(), 'pixels': font_info.pixelSize(), 'device_pixel_ratio': window.devicePixelRatioF()},
                'page': window.page_title.text(), 'platform': app.platformName(),
                'executable': sys.executable}, ensure_ascii=False, indent=2), encoding='utf-8')
            window.close()
            app.exit(0 if all(checks.values()) else 1)
        QTimer.singleShot(350, check_startup)
    return app.exec()


if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        path = ROOT / 'outputs' / 'desktop-startup-error.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding='utf-8')
        raise
