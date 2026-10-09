"""Exercise real Qt controls and AVI decoding without writing human labels to real data."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from core.runtime import configure_runtime
configure_runtime()
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox
from review_data import REVIEW_PATH, ReviewWindow
from core.data_review import read_review, save_review


def main():
    original_hash = hashlib.sha256(REVIEW_PATH.read_bytes()).hexdigest()
    document, _ = read_review(REVIEW_PATH)
    app = QApplication([])
    for filename in ('msyh.ttc', 'msyhbd.ttc'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
    warnings = []
    QMessageBox.warning = lambda parent, title, message: warnings.append(message)
    checks = []
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as folder:
        path = Path(folder) / 'review.json'
        save_review(path, document)
        window = ReviewWindow(path)
        window.show()
        app.processEvents()
        assert window.files.count() == 49 and window.frame_index == 0 and window.view.image is not None
        checks.append('49 rows and raw first AVI frame')
        window.seek(75)
        assert window.frame_index == 75
        window.advance()
        assert window.frame_index == 76
        window.step(-1)
        assert window.frame_index == 75
        checks.append('seek, sequential decode and single-frame stepping')
        window.start_frame.setValue(10)
        window.end_frame.setValue(30)
        window.add_interval()
        assert len(window.intervals) == 1 and window.class_boxes['fire'].currentText() == '有'
        checks.append('add interval and set only its class')
        window.start_frame.setValue(20)
        window.end_frame.setValue(40)
        window.add_interval()
        assert len(window.intervals) == 1 and warnings
        checks.append('overlapping interval refused')
        window.reviewer.setText('QA-only-not-a-human-ground-truth')
        window.class_boxes['smoke'].setCurrentIndex(1)
        window.reviewed.setChecked(True)
        assert window.save()
        actual, _ = read_review(path)
        row = actual['videos'][window.selected]
        assert row['full_video_reviewed'] and row['intervals'][0]['start_frame'] == 10
        assert row['split'] == 'development_seen'
        checks.append('valid completed record saved in isolated test file')
        index = next(i for i, row in enumerate(document['videos']) if row['quality'].startswith('quarantined'))
        window.files.setCurrentRow(index)
        assert not window.reviewed.isEnabled()
        checks.append('quarantined source cannot be marked fully reviewed in UI')
        window.close()
        reopened = ReviewWindow(path)
        assert reopened.reviewed.isChecked() and len(reopened.intervals) == 1
        reopened.resize(1020, 740)
        reopened.show()
        app.processEvents()
        assert reopened.view.height() >= 120
        reopened.grab().save(str(ROOT / 'outputs/data-review-verification.png'))
        reopened.close()
        checks.append('saved record reopens; minimum-size raw-video preview')
    assert hashlib.sha256(REVIEW_PATH.read_bytes()).hexdigest() == original_hash
    checks.append('production annotation file unchanged by checks')
    output = {'checks_passed': checks, 'human_labels_written_to_production': 0,
              'note': 'Screenshot shows a synthetic QA annotation in a temporary copy, not ground truth.'}
    (ROOT / 'outputs/data-review-verification.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
