import sys
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from avito_client.store import data_directory, Store
from avito_client.ui import Window, apply_theme


def main():
    smoke = len(sys.argv) == 3 and sys.argv[1] == "--smoke-test"
    if smoke:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)
    app.setApplicationName("AvitoManualClient")
    apply_theme(app)
    if smoke:
        from PySide6.QtGui import QFontDatabase
        import boto3
        from avito_client.secrets import protect, unprotect
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        with tempfile.TemporaryDirectory() as temp:
            window = Window(Store(temp))
            window.show()
            window.create()
            window.inputs["Title"].setText("Проверка сборки")
            window.save()
            assert window.store.all()[0]["draft"]["fields"]["Title"] == "Проверка сборки"
            assert unprotect(protect("test")) == "test"
            client = boto3.client("s3", endpoint_url="https://example.com",
                                  aws_access_key_id="test", aws_secret_access_key="test")
            assert client.meta.service_model.service_name == "s3"
            app.processEvents()
            window.close()
        Path(sys.argv[2]).write_text("PASS: packaged Qt, SQLite, DPAPI and S3 service models", encoding="utf-8")
        return 0
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(root / "client.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Авито · Ручной клиент", "Клиент уже запущен. Откройте существующее окно.")
        return 0
    store = Store()
    profile = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "settings.avitoconfig"
    if profile.exists() and not store.get_setting("s3"):
        from avito_client.profile import read_profile, save_profile
        try:
            save_profile(store, read_profile(profile))
        except Exception:
            QMessageBox.warning(None, "Настройки S3", "Не удалось загрузить settings.avitoconfig. Выберите файл в настройках S3.")
    window = Window(store)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
