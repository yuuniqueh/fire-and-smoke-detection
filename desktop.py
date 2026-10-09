"""Windows desktop entry point. No model download or training on startup."""
import sys

from core.runtime import ROOT, configure_runtime


def main():
    configure_runtime()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from ui.window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName('焰鉴')
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = MainWindow()
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(max(980, min(1280, available.width() - 60)),
                      max(740, min(920, available.height() - 60)))
    window.show()
    return app.exec()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        path = ROOT / 'outputs' / 'desktop-startup-error.txt'
        path.parent.mkdir(exist_ok=True)
        path.write_text(str(exc), encoding='utf-8')
        raise
