"""Real Qt drag/save/reopen verification; isolated records, no production human labels."""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from review_boxes import PILOT, BoxReviewWindow
from core.box_review import load, training_eligible


def main():
    production = [ROOT / 'data/firesense/review.json', PILOT / 'manifest.json'] + list((PILOT / 'images').glob('*.png'))
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in production}
    app = QApplication([])
    for filename in ('msyh.ttc', 'msyhbd.ttc'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + filename)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    warnings = []
    QMessageBox.warning = lambda parent, title, message: warnings.append(message)
    checks = []
    (ROOT / 'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as folder:
        path = Path(folder) / 'annotations.json'
        window = BoxReviewWindow(annotation_path=path)
        window.show()
        app.processEvents()
        assert window.files.count() == 27 and window.canvas.image.width() == 480
        assert not path.exists()
        checks.append('27 raw images; opening does not create completed or negative labels')
        window.zoom.setCurrentIndex(2)
        assert window.canvas.zoom == 2
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 60))
        QTest.mouseMove(window.canvas, QPoint(240, 260))
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(240, 260))
        assert window.boxes == [{'label': 'fire', 'xyxy': [20., 30., 120., 130.]}]
        assert window.class_boxes['fire'].currentText() == '有'
        checks.append('real mouse drag at 2x stores original pixel coordinates')
        window.label.setCurrentIndex(1)
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=QPoint(500, 400))
        assert len(window.boxes) == 2 and window.boxes[-1]['label'] == 'smoke'
        window.undo_box()
        assert len(window.boxes) == 1 and window.class_boxes['smoke'].currentText() == '未判断'
        checks.append('both classes draw; undo removes last box without inventing absence')
        window.completed.setChecked(True)
        assert not window.save_current() and warnings and not path.exists()
        checks.append('invalid completed record rejected without creating labels')
        window.reviewer.setText('QA-only-not-human-ground-truth')
        window.class_boxes['smoke'].setCurrentIndex(1)
        window.completed.setChecked(True)
        assert window.save_current()
        window.move(1)
        assert window.selected == 1
        window.move(-1)
        assert window.completed.isChecked() and window.boxes[0]['xyxy'] == [20., 30., 120., 130.]
        checks.append('save, next, previous retain coordinates and completion')
        window.add_box([200, 100, 210, 110])
        assert not window.completed.isChecked()
        window.undo_box()
        window.class_boxes['smoke'].setCurrentIndex(1)
        window.completed.setChecked(True)
        assert window.save_current()
        window.move(1)
        window.class_boxes['fire'].setCurrentIndex(3)
        window.class_boxes['smoke'].setCurrentIndex(1)
        window.notes.setPlainText('QA test: 模糊不确定')
        window.completed.setChecked(True)
        assert window.save_current()
        assert not training_eligible(window.document['images'][1])
        checks.append('editing clears completion; uncertain completed record excluded from training')
        window.move(2)
        window.class_boxes['fire'].setCurrentIndex(1)
        window.class_boxes['smoke'].setCurrentIndex(1)
        window.completed.setChecked(True)
        assert window.save_current() and training_eligible(window.document['images'][3])
        assert not window.document['images'][3]['boxes']
        checks.append('explicit reviewed negative may be empty; pending images remain unknown')
        window.close()
        reopened = BoxReviewWindow(annotation_path=path)
        reopened.show()
        reopened.resize(1000, 720)
        app.processEvents()
        reopened.apply_zoom()
        assert reopened.completed.isChecked() and reopened.boxes[0]['xyxy'] == [20., 30., 120., 130.]
        assert reopened.scroll.height() >= 150
        reopened.grab().save(str(ROOT / 'outputs/box-review-verification.png'))
        reopened.close()
        checks.append('saved labels reopen; minimum-size layout captured')
    assert before == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in production}
    checks.append('original 27 images, source manifest and video-time annotation file unchanged')
    report = {'checks_passed': checks, 'human_ground_truth_written_to_production': 0}
    (ROOT / 'outputs/box-review-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
