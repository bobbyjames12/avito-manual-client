"""Headless native-window smoke test; no live S3/Avito actions."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
from pathlib import Path
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor, QFontDatabase, QPalette
from avito_client.store import Store
from avito_client.ui import Window, apply_theme, Settings
from avito_client.secrets import protect, unprotect

app = QApplication([])
QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
dark = QPalette()
for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button):
    dark.setColor(role, QColor("#111111"))
app.setPalette(dark)
apply_theme(app)
with tempfile.TemporaryDirectory() as temp:
    store = Store(temp)
    window = Window(store)
    window.show()
    window.create()
    values = dict(Title="Футболка хлопковая, белая", Description="Базовая футболка из плотного хлопка.\nПрямой крой, круглый вырез.",
        Price="1200", Brand="Без бренда", Color="Белый", Size="48 (M)",
        Condition="Новое с биркой", AdType="Товар приобретен на продажу", Delivery="ПВЗ", Address="Москва", ContactPhone="+79990000000")
    for key, value in values.items():
        edit = window.inputs[key]
        if hasattr(edit, "setPlainText"):
            edit.setPlainText(value)
        elif hasattr(edit, "setCurrentText"):
            edit.setCurrentText(value)
        else:
            edit.setText(value)
    photo = QImage(500, 500, QImage.Format.Format_RGB32)
    photo.fill(QColor("#e8edf7"))
    path = Path(temp) / "sample.jpg"
    photo.save(str(path))
    window.current["photos"] = [store.import_photo(path)]
    window.photos()
    window.dirty = True
    window.enqueue()
    assert len(store.snapshot()) == 1
    original_id = window.current["id"]
    window.inputs["Title"].setText("Футболка хлопковая, размер M")
    window.save()
    assert store.get(original_id)["queued"]["fields"]["Title"] != store.get(original_id)["draft"]["fields"]["Title"]
    window.enqueue()
    assert store.snapshot()[0]["fields"]["Title"] == "Футболка хлопковая, размер M"
    window.duplicate()
    assert window.current["id"] != original_id
    assert len(store.all()) == 2
    assert unprotect(protect("test-secret-ключ")) == "test-secret-ключ"
    app.processEvents()
    output = Path(__file__).resolve().parents[1] / ".test-data"
    output.mkdir(exist_ok=True)
    window.grab().save(str(output / "client-preview.png"))
    settings = Settings(store, window)
    settings.show()
    app.processEvents()
    settings.grab().save(str(output / "settings-preview.png"))
    settings.close()
    window.close()
print("UI smoke passed: editing, queue snapshots, duplicate, DPAPI, native render")
