"""Actual Qt controls + local model integration. No claim of scenario accuracy."""
import json
import os
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from core.runtime import configure_runtime
configure_runtime()

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication
from core.records import load_run
from ui.window import MainWindow


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    # The Windows offscreen platform does not enumerate system fonts.
    if not QFontDatabase.families():
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow()
    errors = []
    def catch_error(message):
        errors.append(message)
        window.last_error = message
    window._notify_error = catch_error
    window.show()
    app.processEvents()
    report = {'platform': os.environ['QT_QPA_PLATFORM'], 'checks': {}, 'runs': {}}
    heartbeat_gaps = []
    last_heartbeat = [time.perf_counter()]
    def heartbeat():
        now = time.perf_counter()
        heartbeat_gaps.append(now - last_heartbeat[0])
        last_heartbeat[0] = now
    pulse = QTimer()
    pulse.setInterval(20)
    pulse.timeout.connect(heartbeat)
    pulse.start()

    def wait_until(predicate, action=None, timeout=90):
        deadline = time.perf_counter() + timeout
        while not predicate():
            app.processEvents()
            if action:
                action()
            if time.perf_counter() > deadline:
                window.stop_analysis()
                raise AssertionError('Desktop integration timed out')
            time.sleep(0.005)
        app.processEvents()

    window.set_source(ROOT / 'videos' / 'missing-video.mp4')
    window.start_button.click()
    assert errors and not window.busy
    report['checks']['missing_video_rejected'] = True
    errors.clear()

    window.demo_button.click()
    window.start_button.click()
    assert window.busy and not window.start_button.isEnabled() and window.stop_button.isEnabled()
    report['checks']['running_controls_locked'] = True
    captured = [False]
    def screenshot():
        if not captured[0] and window.events and window.video.image is not None and len(window.curve.points) >= 25:
            # Live peak uses the backend accumulator, even if a preview frame is skipped.
            assert window.events[1]['risk_summary']['peak_score'] > 47.9
            window.grab().save(str(ROOT / 'outputs' / 'desktop-preview.png'))
            captured[0] = True
    wait_until(lambda: not window.busy, screenshot)
    assert not errors, errors
    real = load_run(window.current_run)
    assert real['summary']['processed_frames'] == 206
    assert real['summary']['temporal']['confirmed_events'] == 1
    assert real['summary']['risk']['peak_level'] == 'LOW'
    assert window.event_table.rowCount() == 1 and window.export_button.isEnabled()
    evidence = real['events'][0]['evidence_image']
    assert (window.current_run / evidence).is_file()
    assert captured[0]
    report['runs']['fireplace'] = str(window.current_run)
    report['checks']['real_video_full_analysis_and_event_image'] = True
    max_gap = max(heartbeat_gaps)
    report['heartbeat_max_gap_during_first_run_s'] = round(max_gap, 4)
    assert max_gap < 2.0, f'GUI heartbeat stalled {max_gap:.3f}s'
    report['checks']['gui_heartbeat_continues_during_inference'] = True

    # Stop through the actual button after a confirmed event, then re-run anew.
    window.start_button.click()
    stopped = [False]
    def stop():
        if not stopped[0] and window.events:
            window.stop_button.click()
            stopped[0] = True
    wait_until(lambda: not window.busy, stop)
    stopped_run = load_run(window.current_run)
    assert stopped[0] and stopped_run['summary']['status'] == 'stopped_by_user'
    assert 0 < stopped_run['summary']['processed_frames'] < 206
    assert stopped_run['events'][0]['close_reason'] == 'stopped_by_user'
    report['runs']['stopped'] = str(window.current_run)
    report['checks']['stop_finalizes_logs_and_event'] = True

    window.set_source(ROOT / 'videos' / 'engineering_no_fire.mp4')
    window.start_button.click()
    wait_until(lambda: not window.busy)
    negative = load_run(window.current_run)
    assert negative['summary']['processed_frames'] == 45
    assert negative['summary']['temporal']['confirmed_events'] == 0
    assert window.event_table.rowCount() == 0
    assert negative['summary']['risk']['peak_score'] == 0
    report['runs']['negative'] = str(window.current_run)
    report['checks']['restart_resets_previous_events_and_risk'] = True

    invalid = ROOT / 'tmp' / 'desktop-invalid-video.mp4'
    invalid.parent.mkdir(exist_ok=True)
    invalid.write_bytes(b'This is not an encoded video')
    window.set_source(invalid)
    window.start_button.click()
    wait_until(lambda: not window.busy)
    assert errors and '无法读取视频' in errors[-1]
    assert window.start_button.isEnabled() and not window.stop_button.isEnabled()
    report['checks']['invalid_video_worker_error_recovers_controls'] = True
    errors.clear()

    # Load through history selection and export through the same worker as the UI.
    window.history_filter.setText('fireplace')
    index = next(i for i, record in enumerate(window.history) if record['output'] == report['runs']['fireplace'])
    assert not window.history_table.isRowHidden(index)
    window.history_table.selectRow(index)
    window.load_button.click()
    wait_until(lambda: not window.busy)
    assert str(window.current_run) == report['runs']['fireplace']
    assert window.event_table.rowCount() == 1 and window.curve.points
    window.event_table.selectRow(0)
    assert '确认截图' in window.detail.toPlainText() and window.video.image is not None
    report['checks']['history_filter_load_curve_and_event_image'] = True
    window.grab().save(str(ROOT / 'outputs' / 'desktop-history-preview.png'))
    archive_path = ROOT / 'outputs' / 'desktop-verification-export.zip'
    window.start_export(archive_path)
    wait_until(lambda: not window.busy)
    assert not errors, errors
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        assert evidence in archive.namelist()
        assert json.loads(archive.read('summary.json')) == real['summary']
    report['checks']['zip_export_readable_and_complete'] = True

    # Request window close during active inference. It stays alive until release.
    window.demo_button.click()
    window.start_button.click()
    wait_until(lambda: window.current_run is not None)
    window.close()
    assert window.busy and window.closing
    wait_until(lambda: not window.busy)
    wait_until(lambda: not window.isVisible())
    closing_run = load_run(window.current_run)
    assert closing_run['summary']['status'] == 'stopped_by_user'
    report['runs']['closed_window'] = str(window.current_run)
    report['checks']['close_waits_for_cooperative_cleanup'] = True
    pulse.stop()
    report['passed_checks'] = len(report['checks'])
    (ROOT / 'outputs' / 'desktop-integration-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
