"""Opt-in packaged-app engineering checks; never opens a physical camera."""
import json
import os
from pathlib import Path
import time


def _synthetic_camera(index, frames, messages, stopping):
    """Spawn test fixture only; not a real camera or fire detection benchmark."""
    import numpy as np
    from queue import Full
    messages.put({'kind': 'ready', 'width': 320, 'height': 240, 'backend': 'synthetic_packaging_check'})
    sequence = 0
    while not stopping.is_set():
        try:
            frames.put_nowait((sequence, time.monotonic(), np.zeros((240, 320, 3), dtype=np.uint8)))
        except Full:
            pass
        sequence += 1
        stopping.wait(.05)
    frames.cancel_join_thread()


def run_package_check(report_path, video=None, ai_endpoint=None):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Self-check settings and generated records never overwrite normal usage.
    import core.runtime as runtime
    runtime.ROOT = report_path.parent / 'self-test-data'
    from core.runtime import ASSET_ROOT, ROOT, FROZEN, configure_runtime
    configure_runtime()
    report = {'scope': 'Packaged engineering check; not clean-PC, real-camera or accuracy acceptance.',
              'frozen': FROZEN, 'checks': {}, 'runs': [], 'errors': []}
    window = None
    app = None
    def passed(name):
        report['checks'][name] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    def until(predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() >= deadline:
                raise TimeoutError('Packaged desktop operation timed out.')
            time.sleep(.01)
        app.processEvents()
    try:
        import cv2
        import numpy as np
        from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
        from PySide6.QtCore import QCoreApplication, QEvent, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from core.diagnostics import collect_diagnostics
        from core.model_profiles import create_detector, PROFILE_NAMES, profile_availability
        from core.records import load_run
        from core.replay import review_media, review_window
        from ui.window import MainWindow
        from ui.replay import ReplayDialog
        for profile in PROFILE_NAMES:
            if profile in ('domestic', 'forest', 'uav') and not profile_availability(profile)['ready']:
                continue
            diagnostic = collect_diagnostics(device='cpu', profile=profile)
            assert diagnostic['ready'], diagnostic
            passed('diagnostics_' + profile)
            detector = create_detector(ASSET_ROOT / 'models/best.pt', 'cpu', .35, profile)
            image, boxes = detector.predict(np.zeros((240, 320, 3), dtype=np.uint8))
            assert image.shape == (240, 320, 3) and isinstance(boxes, list)
            del detector
            passed('real_model_load_and_forward_' + profile)
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        # Windows' offscreen Qt plugin may expose no system fonts. Read the
        # installed system font for this test; never copy it into the package.
        if not QFontDatabase.families():
            font = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts/msyh.ttc'
            QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont('Microsoft YaHei UI', 10))
        assert QFontMetrics(app.font()).inFontUcs4(ord('焰')), 'Chinese UI glyphs unavailable'
        passed('chinese_ui_font_available')
        window = MainWindow()
        window.resize(1280, 920)
        window.show()
        app.processEvents()
        assert window.tabs.count() == 8
        assert window.page_title.text() == '监控台'
        passed('eight_desktop_pages_and_monitor_center_default')
        assert window.input_kind.currentData() == 'camera' and window.task is None
        assert window.tools_panel.isHidden()
        window.grab().save(str(report_path.with_name(report_path.stem + '-home.png')))
        passed('monitoring_home_default_without_opening_camera')
        from ui.model_picker import ModelInfoDialog, ModelPickerDialog
        picker = ModelPickerDialog('original', window, assets=ASSET_ROOT)
        picker.show()
        app.processEvents()
        assert set(picker.choice_buttons) == set(PROFILE_NAMES)
        assert all(button.isEnabled() for button in picker.choice_buttons.values())
        assert picker.tabs.count() == 2
        assert all('试用' not in window.scene_combo.itemText(index)
                   for index in range(window.scene_combo.count()))
        assert all('试用' not in name for name in PROFILE_NAMES.values())
        picker.grab().save(str(report_path.with_name(report_path.stem + '-models.png')))
        picker.close()
        picker.deleteLater()
        introduction = ModelInfoDialog('brightlight', window, assets=ASSET_ROOT)
        introduction.show()
        app.processEvents()
        assert '主要优点' in introduction.introduction.text()
        assert '灯光' in introduction.introduction.text()
        assert introduction.technical_details.isHidden()
        introduction.grab().save(str(report_path.with_name(report_path.stem + '-introduction.png')))
        introduction.details_toggle.click()
        assert not introduction.technical_details.isHidden()
        introduction.close()
        introduction.deleteLater()
        from core.alert_presentation import notice_title
        assert notice_title({'kind': 'event_confirmed', 'label': 'fire', 'level': 'LOW'}).startswith('检测到火焰')
        window._show_assessment({'status': 'assessed', 'level': 'HIGH', 'score': 72.,
                                'dominant_event_id': 1,
                                'events': [{'event_id': 1, 'label': 'fire', 'fresh_evidence': True,
                                            'features': {'positive_duration_s': 2.}, 'score_components': {}}]})
        assert window.evidence.text().startswith('检测到火焰')
        window._show_assessment(None)
        passed('all_registered_scene_choices_in_packaged_picker')
        notice = {}
        def inspect_license_dialog():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                notice['text'] = dialog.text()
                dialog.accept()
        QTimer.singleShot(0, inspect_license_dialog)
        window.show_licenses()
        assert all(value in notice.get('text', '') for value in
                   ('AGPL-3.0-only', 'application-source.zip', 'corresponding-sources', 'FFmpeg'))
        passed('license_notice_and_corresponding_source_access')
        fixture = ROOT / 'package-check-input.mp4'
        writer = cv2.VideoWriter(str(fixture), cv2.VideoWriter_fourcc(*'mp4v'), 10., (320, 240))
        assert writer.isOpened(), 'MP4 encoder unavailable'
        capture = cv2.VideoCapture(str(video)) if video else None
        try:
            for index in range(40):
                if capture:
                    ok, frame = capture.read()
                    assert ok, 'Test video needs at least 40 decodable frames'
                    frame = cv2.resize(frame, (320, 240))
                else:
                    frame = np.zeros((240, 320, 3), dtype=np.uint8)
                    cv2.putText(frame, str(index), (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
                writer.write(frame)
        finally:
            writer.release()
            if capture:
                capture.release()
        window.device.setCurrentIndex(window.device.findData('cpu'))
        window.realtime.setChecked(False)
        window.set_source(fixture)
        window.start_button.click()
        until(lambda: not window.busy)
        assert not window.last_error, window.last_error
        record = load_run(window.current_run)
        assert record['summary']['processed_frames'] == 40
        assert record['summary']['status'] == 'completed', record['summary']
        report['runs'].append(str(window.current_run))
        passed('desktop_background_inference_save_and_history')
        media = review_media(window.current_run, {**record['config'], 'source': ''})
        assert media and media[0]['kind'] == 'annotated'
        dialog = ReplayDialog(media, review_window({'confirmed_at_s': 1.}), 0, window)
        dialog.show()
        try:
            until(lambda: dialog.current_frame is not None)
        finally:
            dialog.close()
            until(lambda: dialog.worker is None)
        passed('saved_mp4_decode_and_replay')
        export = ROOT / 'package-check-export.zip'
        window.start_export(str(export))
        until(lambda: not window.busy)
        assert export.is_file() and not window.last_error
        passed('background_export')
        center = window.monitor_center
        center.runSaved.disconnect()
        center.auto_ai.setChecked(False)
        center.set_devices([{'id': 'packageA', 'name': '自检A', 'location': '软件接入验证',
            'kind': 'file', 'source': str(fixture), 'model_profile': 'original'},
            {'id': 'packageB', 'name': '自检B', 'location': '软件接入验证',
            'kind': 'file', 'source': str(fixture), 'model_profile': 'domestic'}])
        center.start_all()
        until(lambda: len(center.outputs) == 2 and not center.active)
        for point in center.devices:
            saved = load_run(center.outputs[point['id']])
            assert saved['summary']['processed_frames'] == 40
            assert saved['config']['model_profile'] == point['model_profile']
        passed('two_monitor_actual_cpu_models_and_saved_configurations')
        center.select_device_scene('packageA')
        assert center.selected_scene_locked and center.selected_key[2] is None
        passed('monitor_card_opens_scene_without_requiring_detector_event')
        if ai_endpoint:
            from core.scene_review import local_endpoint
            center.endpoint = local_endpoint(ai_endpoint)
            scene_key = center.selected_key
            center.review_selected()
            until(lambda: not center.active and not center.scene_queue, 120)
            scene = center.scenes[scene_key].get('scene_record')
            assert scene and scene['scene']['description'], center.scene_text.toPlainText()
            assert scene['evidence'] and all(frame.get('sha256') for frame in scene['evidence'])
            assert (Path(center.outputs['packageA']) / 'scene-reviews.jsonl').is_file()
            assert scene['scene']['description'] in center.analysis_summary.description_card.body.text()
            passed('local_ai_real_description_and_retained_scene_evidence')
        # In-memory UI fixtures only; never create artificial detector logs.
        center.events.clear()
        for index in range(50):
            point = center.devices[0]
            output = center.outputs[point['id']]
            center.events[(point['id'], output, index + 1000)] = {
                'event_id': index + 1000, 'device': point, 'output': output, 'label': 'fire',
                'confirmed_at_s': float(index), 'reference_s': float(index), 'last_timestamp_s': float(index),
                'closed': True, 'baseline': {'score': 50., 'level': 'MEDIUM'}, 'review': 'pending',
                'scene_status': '仅软件界面自检数据', 'scene_record': None, 'frames': []}
        center.selected_scene_locked = False
        center.selected_key = None
        center.set_event_mode(True)
        window.navigate('event_board')
        for _ in range(4):
            app.processEvents()
        bar = center.event_table.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())
        position = bar.value()
        center.refresh()
        app.processEvents()
        assert bar.value() == position
        assert center.event_table.horizontalHeaderItem(0).text() == '序号'
        assert center.event_sort.count() == 4
        passed('numbered_sorted_monitor_events_keep_scroll_position_on_refresh')
        center.events.clear()
        center._render_events()
        # Exercise the actual spawn and queue path without accessing user hardware.
        import core.camera as camera
        real_capture = camera.CameraCapture
        camera.CameraCapture = lambda index, stop_event=None: real_capture(index, stop_event, _target=_synthetic_camera)
        try:
            window.input_kind.setCurrentIndex(window.input_kind.findData('camera'))
            window.video.clear_frame('Synthetic packaging check')
            window.preview_button.click()
            until(lambda: window.video.image is not None)
            window.stop_button.click()
            until(lambda: not window.busy)
            assert not window.last_error, window.last_error
            passed('spawn_camera_fixture_preview_and_stop')
            window.start_button.click()
            until(lambda: window.current_run is not None and len(window.curve.points) >= 8)
            window.stop_button.click()
            until(lambda: not window.busy)
            assert not window.last_error, window.last_error
            record = load_run(window.current_run)
            assert record['summary']['status'] == 'stopped_by_user'
            assert record['config']['source_kind'] == 'camera'
            assert record['config']['recording_mode'] == 'events'
            report['runs'].append(str(window.current_run))
            passed('spawn_camera_fixture_monitor_and_save_on_stop')
        finally:
            camera.CameraCapture = real_capture
        window.tabs.setCurrentIndex(window.storage_tab_index)
        window.scan_storage()
        until(lambda: not window.busy)
        window.grab().save(str(report_path.with_suffix('.png')))
        passed('storage_inventory_page')
        window.close()
        app.processEvents()
        assert (ROOT / '.cache/desktop-settings.json').is_file()
        passed('portable_preferences_persist')
    except Exception:
        import traceback
        report['errors'].append(traceback.format_exc())
    finally:
        if window:
            if window.busy:
                window.stop_analysis()
                try:
                    until(lambda: not window.busy, 30)
                except Exception as exc:
                    report['errors'].append(str(exc))
            window.close()
            if window.monitor_center.active:
                try:
                    until(lambda: not window.monitor_center.active, 30)
                except Exception as exc:
                    report['errors'].append(str(exc))
            if not window.monitor_center.active:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
        report['ok'] = not report['errors']
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1
