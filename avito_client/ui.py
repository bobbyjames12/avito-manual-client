from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSize, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QComboBox, QFormLayout, QScrollArea,
    QListWidget, QListWidgetItem, QSplitter, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QCheckBox, QAbstractItemView, QCompleter,
)

from .model import new_ad, FIELDS, validate, CHOICES, MULTIPLE, REQUIRED, field_issues, validate_assets, SUPPORTED_DELIVERY
from .store import Store
from .profile import read_profile
from .secrets import protect, unprotect
from .publisher import publish, check_config, public_url


STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #202c3c; }
QMainWindow, QDialog { background: #f4f6fa; }
QLineEdit, QTextEdit, QComboBox, QListWidget {
    background: white; border: 1px solid #d9e0eb; border-radius: 6px; padding: 7px;
}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #3875e8; }
QPushButton { background: white; border: 1px solid #d9e0eb; border-radius: 6px; padding: 9px 14px; }
QPushButton:hover { background: #eaf0fc; }
QPushButton:disabled { color: #9aa6b6; background: #edf0f5; }
QPushButton[primary="true"] { background: #2763d9; border: none; color: white; }
QPushButton[primary="true"]:hover { background: #1d52bd; }
QLabel[heading="true"] { font-size: 24px; font-weight: 650; }
QLabel[muted="true"] { color: #69788d; }
QListWidget::item { padding: 10px; border-radius: 5px; }
QListWidget::item:selected { background: #e7efff; color: #184da6; }
QScrollArea { border: none; background: transparent; }
QStatusBar { background: #eaf0f9; }
QComboBox QAbstractItemView { background: white; color: #202c3c;
    selection-background-color: #e7efff; selection-color: #184da6; }
QToolTip { background: #ffffff; color: #202c3c; border: 1px solid #d9e0eb; }
"""


def apply_theme(app):
    # A partial stylesheet otherwise mixes dark Windows surfaces with dark text.
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    colors = {
        "Window": "#f4f6fa", "WindowText": "#202c3c", "Base": "#ffffff",
        "AlternateBase": "#edf2fa", "Text": "#202c3c", "Button": "#ffffff",
        "ButtonText": "#202c3c", "Highlight": "#e7efff", "HighlightedText": "#184da6",
        "ToolTipBase": "#ffffff", "ToolTipText": "#202c3c", "PlaceholderText": "#69788d",
        "Light": "#ffffff", "Midlight": "#eaf0f9", "Mid": "#b7c3d4",
        "Dark": "#69788d", "Shadow": "#46566c", "Link": "#2763d9",
        "LinkVisited": "#6347a8", "BrightText": "#ffffff",
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#69788d"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def button(text, callback, primary=False):
    result = QPushButton(text)
    result.setProperty("primary", primary)
    result.clicked.connect(lambda checked=False: callback())
    return result


class MultiChoice(QListWidget):
    currentTextChanged = Signal(str)

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.setFixedHeight(135)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setToolTip("Отметьте один или несколько вариантов. Для выключения доставки снимите остальные отметки.")
        for value in CHOICES[key]:
            item = QListWidgetItem(value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            if key == "Delivery" and value not in SUPPORTED_DELIVERY:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("Требует дополнительных настроек собственного перевозчика; пока не поддерживается клиентом.")
            self.addItem(item)
        self.itemChanged.connect(lambda item: self.currentTextChanged.emit(self.currentText()))

    def currentText(self):
        return " | ".join(self.item(i).text() for i in range(self.count()) if self.item(i).checkState() == Qt.CheckState.Checked)

    def setCurrentText(self, text):
        selected = [v.strip() for v in text.split("|") if v.strip()]
        for i in reversed(range(self.count())):
            if self.item(i).text() not in CHOICES[self.key]:
                self.takeItem(i)
        for value in selected:
            if value not in CHOICES[self.key]:
                item = QListWidgetItem(value)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                self.addItem(item)
        for i in range(self.count()):
            item = self.item(i)
            if item.text() in selected:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
            elif self.key == "Delivery" and item.text() not in SUPPORTED_DELIVERY:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if item.text() in selected else Qt.CheckState.Unchecked)


class Worker(QThread):
    progress = Signal(str)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, ads, root, config):
        super().__init__()
        self.ads, self.root, self.config = ads, root, config

    def run(self):
        try:
            self.succeeded.emit(publish(self.ads, self.root, self.config, self.progress.emit))
        except Exception as exc:
            # Never show credentials echoed by a provider.
            message = str(exc)
            for key in ("access_key", "secret_key"):
                if self.config.get(key):
                    message = message.replace(self.config[key], "***")
            self.failed.emit(message)


class Settings(QDialog):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.imported_profile = False
        self.setWindowTitle("Настройки автозагрузки по ссылке")
        self.setMinimumWidth(860)
        layout = QVBoxLayout(self)
        note = QLabel("Клиент обновляет фото и XML в S3. Постоянную ссылку ниже нужно один раз\n"
                      "указать в настройках автозагрузки Авито. Отдельный сервер не нужен.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(button("Загрузить настройки из файла", self.import_profile))
        form = QFormLayout()
        self.inputs = {}
        saved = json.loads(store.get_setting("s3", "{}"))
        defaults = {"region": "ru-central1", "prefix": store.get_setting("feed_prefix")}
        labels = {
            "endpoint": "S3 endpoint, HTTPS", "bucket": "Имя бакета",
            "region": "Регион", "public_base": "Публичный URL корня бакета",
            "prefix": "Папка клиента в бакете", "access_key": "Access key", "secret_key": "Secret key",
        }
        for key, title in labels.items():
            edit = QLineEdit(saved.get(key, defaults.get(key, "")))
            if key in ("access_key", "secret_key"):
                edit.setEchoMode(QLineEdit.EchoMode.Password)
                edit.setText(unprotect(saved.get(key, "")))
            if key == "endpoint":
                edit.setPlaceholderText("https://storage.yandexcloud.net")
            if key == "public_base":
                edit.setPlaceholderText("https://storage.yandexcloud.net/имя-бакета")
            self.inputs[key] = edit
            if key == "public_base":
                row = QHBoxLayout()
                row.addWidget(edit, 1)
                row.addWidget(button("Вставить", self.paste_public_base))
                self.open_base = button("Открыть", lambda: self.open_url(self.inputs["public_base"].text()))
                row.addWidget(self.open_base)
                form.addRow(title, row)
            else:
                form.addRow(title, edit)
        layout.addLayout(form)
        self.acl = QCheckBox("Отправлять ACL public-read (если этого требует хранилище)")
        self.acl.setChecked(saved.get("public_acl", False))
        layout.addWidget(self.acl)
        layout.addWidget(QLabel("Чтение фото и XML по HTTPS должно быть доступно без входа.\n"
                                "Ключи шифруются для текущего пользователя Windows."))
        self.url = QLineEdit()
        self.url.setReadOnly(True)
        layout.addWidget(QLabel("Постоянная ссылка для Авито"))
        url_row = QHBoxLayout()
        url_row.addWidget(self.url, 1)
        self.copy_feed = button("Скопировать", self.copy_url)
        self.open_feed = button("Открыть", lambda: self.open_url(self.url.text()))
        url_row.addWidget(self.copy_feed)
        url_row.addWidget(self.open_feed)
        layout.addLayout(url_row)
        hint = QLabel("Public URL — адрес вашего бакета. Ссылку для Авито скопируйте кнопкой выше.\n"
                      "Фид начнёт открываться после первой успешной отправки очереди в S3.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        for edit in self.inputs.values():
            edit.textChanged.connect(self.update_url)
        self.update_url()
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def import_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл настроек", "", "Настройки Авито (*.avitoconfig)")
        if not path:
            return
        try:
            config = read_profile(path)
            for key, edit in self.inputs.items():
                edit.setText(config[key])
            self.acl.setChecked(config["public_acl"])
            self.imported_profile = True
            self.error.setText("Настройки загружены. Нажмите «Сохранить».")
        except Exception:
            self.error.setText("Не удалось прочитать файл настроек. Выберите исходный файл .avitoconfig.")

    def values(self):
        return {**{k: v.text().strip() for k, v in self.inputs.items()}, "public_acl": self.acl.isChecked()}

    def paste_public_base(self):
        edit = self.inputs["public_base"]
        edit.setText(QApplication.clipboard().text().strip())
        edit.setFocus()

    def copy_url(self):
        QApplication.clipboard().setText(self.url.text())
        self.error.setText("Ссылка для Авито скопирована.")

    @staticmethod
    def valid_link(text):
        url = QUrl(text.strip())
        return url.isValid() and url.scheme() == "https" and bool(url.host()) and not url.userInfo()

    def open_url(self, text):
        if self.valid_link(text) and not QDesktopServices.openUrl(QUrl(text.strip())):
            self.error.setText("Не удалось открыть браузер. Скопируйте ссылку вручную.")

    def update_url(self):
        config = self.values()
        base_valid = self.valid_link(config["public_base"])
        self.url.setText(public_url(config, config["prefix"].strip("/") + "/feed.xml")
                         if base_valid and config["prefix"].strip("/") else "")
        self.open_base.setEnabled(base_valid)
        self.copy_feed.setEnabled(bool(self.url.text()))
        self.open_feed.setEnabled(bool(self.url.text()))

    def save(self):
        config = self.values()
        try:
            check_config(config)
            previous = json.loads(self.store.get_setting("s3", "{}"))
            if self.store.jobs() and any(config[k] != previous.get(k) for k in ("endpoint", "bucket", "prefix")) and not (self.imported_profile and not any(row["sent"] for row in self.store.all())):
                raise ValueError("После первой попытки отправки бакет и папка фида закреплены. Public URL можно исправить. Смена хранилища требует отдельного переноса.")
            for key in ("access_key", "secret_key"):
                config[key] = protect(config[key])
            self.store.setting("s3", json.dumps(config))
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.store.recover_jobs()
        if not self.store.get_setting("feed_prefix"):
            self.store.setting("feed_prefix", "avito-manual/" + uuid4().hex[:16])
        self.current = None
        self.loading = False
        self.dirty = False
        self.worker = None
        self.setWindowTitle("Авито · Ручной клиент")
        self.resize(1260, 880)
        self.setMinimumSize(1000, 680)
        shell = QWidget()
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(24, 20, 24, 12)
        top = QHBoxLayout()
        title = QLabel("Объявления")
        title.setProperty("heading", True)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(button("История", self.history))
        top.addWidget(button("Резервная копия", self.backup))
        top.addWidget(button("Настройки S3", self.settings))
        layout.addLayout(top)
        subtitle = QLabel("Кофты и футболки  /  Ручное заполнение  /  Автозагрузка по постоянной ссылке")
        subtitle.setProperty("muted", True)
        layout.addWidget(subtitle)
        toolbar = QHBoxLayout()
        toolbar.addWidget(button("+ Новое объявление", self.create, True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по заголовку…")
        self.search.textChanged.connect(self.refresh)
        toolbar.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["Все объявления", "Черновики", "В очереди", "Отправлены в S3"])
        self.filter.currentIndexChanged.connect(self.refresh)
        toolbar.addWidget(self.filter)
        self.send_button = button("Отправить очередь", self.send, True)
        toolbar.addWidget(self.send_button)
        layout.addLayout(toolbar)
        self.counts = QLabel()
        layout.addWidget(self.counts)
        splitter = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(275)
        self.list.currentItemChanged.connect(self.select)
        splitter.addWidget(self.list)
        self.editor = QWidget()
        body = QVBoxLayout(self.editor)
        body.setContentsMargins(12, 0, 0, 0)
        self.state = QLabel("Создайте объявление или выберите карточку слева.")
        self.state.setWordWrap(True)
        body.addWidget(self.state)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form_body = QVBoxLayout(content)
        self.photo_list = QListWidget()
        self.photo_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.photo_list.setIconSize(QSize(100, 100))
        self.photo_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.photo_list.setFixedHeight(170)
        self.photo_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        form_body.addWidget(QLabel("Фотографии · первое фото — главное"))
        form_body.addWidget(self.photo_list)
        photo_bar = QHBoxLayout()
        photo_bar.addWidget(button("Добавить фото", self.add_photos))
        photo_bar.addWidget(button("← Раньше", lambda: self.move_photo(-1)))
        photo_bar.addWidget(button("Позже →", lambda: self.move_photo(1)))
        photo_bar.addWidget(button("Убрать фото", self.remove_photo))
        photo_bar.addStretch()
        form_body.addLayout(photo_bar)
        self.inputs = {}
        form = QFormLayout()
        form.setVerticalSpacing(12)
        choices = CHOICES
        self.field_errors = {}
        for key, label in FIELDS.items():
            if key == "Description":
                edit = QTextEdit()
                edit.setAcceptRichText(False)
                edit.setMinimumHeight(155)
                edit.setPlaceholderText("Опишите футболку: ткань, посадка, состояние, особенности…")
                edit.textChanged.connect(self.changed)
            elif key in MULTIPLE:
                edit = MultiChoice(key)
                edit.currentTextChanged.connect(self.changed)
            elif key in choices:
                edit = QComboBox()
                edit.setEditable(key == "Brand")
                edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                edit.addItems([""] + choices[key])
                if key == "Brand":
                    edit.setToolTip("Начните вводить бренд и выберите из справочника Авито. Без марки — «Без бренда».")
                    edit.completer().setFilterMode(Qt.MatchFlag.MatchContains)
                    edit.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                    edit.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
                edit.currentTextChanged.connect(self.changed)
            else:
                edit = QLineEdit()
                if key in ("Category", "Apparel", "GoodsSubType"):
                    edit.setReadOnly(True)
                if key == "Brand":
                    edit.setPlaceholderText("Название бренда или «Без бренда»")
                edit.textChanged.connect(self.changed)
            self.inputs[key] = edit
            field = QWidget()
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.addWidget(edit)
            error = QLabel()
            error.setWordWrap(True)
            error.setStyleSheet("color: #b42318; font-size: 12px;")
            error.hide()
            self.field_errors[key] = error
            field_layout.addWidget(error)
            form.addRow(label + (" *" if key in REQUIRED else ""), field)
        form_body.addLayout(form)
        info = QLabel("* Обязательные поля. Справочник Авито от 10.09.2026: мужская одежда → кофты и футболки.\n"
                      "Проверка полей не заменяет модерацию и проверку тарифа, телефона и доставки в аккаунте Авито.")
        info.setWordWrap(True)
        info.setProperty("muted", True)
        form_body.addWidget(info)
        scroll.setWidget(content)
        body.addWidget(scroll)
        actions = QHBoxLayout()
        actions.addWidget(button("Сохранить черновик", self.save))
        actions.addWidget(button("Проверить", self.check_card))
        actions.addWidget(button("Дублировать", self.duplicate))
        actions.addWidget(button("Убрать из очереди", self.unqueue))
        actions.addStretch()
        actions.addWidget(button("В очередь / обновить", self.enqueue, True))
        body.addLayout(actions)
        splitter.addWidget(self.editor)
        splitter.setSizes([320, 880])
        layout.addWidget(splitter, 1)
        self.editor.setEnabled(False)
        self.statusBar().showMessage("Локальные черновики · сеть нужна только для отправки")
        self.timer = QTimer(self)
        self.timer.setInterval(700)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.save)
        self.refresh()

    def changed(self, *args):
        if not self.loading and self.current:
            self.dirty = True
            self.timer.start()

    def capture(self):
        if not self.current:
            return None
        ad = copy.deepcopy(self.current)
        for key, edit in self.inputs.items():
            ad["fields"][key] = edit.toPlainText() if isinstance(edit, QTextEdit) else edit.currentText() if isinstance(edit, (QComboBox, MultiChoice)) else edit.text()
        return ad

    def save(self):
        self.timer.stop()
        if self.current and self.dirty:
            self.current = self.capture()
            self.store.save(self.current)
            self.dirty = False
            self.refresh()
            self.show_state()
            self.show_validation()
            self.statusBar().showMessage("Черновик сохранён на компьютере")

    def status(self, row):
        if row["queued"]:
            return "В очереди · есть новые правки" if row["draft"] != row["queued"] else "Обновление в очереди" if row["sent"] else "В очереди"
        if row["sent"]:
            return "Есть несохранённые в фид правки" if row["draft"] != row["sent"] else "Отправлено в S3"
        return "Черновик"

    def refresh(self, *args):
        selected = self.current["id"] if self.current else None
        self.list.blockSignals(True)
        self.list.clear()
        rows = self.store.all()
        queued = sum(bool(r["queued"]) for r in rows)
        sent = sum(bool(r["sent"]) for r in rows)
        self.counts.setText(f"Всего {len(rows)}     ·     В очереди {queued}     ·     Отправлены в S3 {sent}")
        self.send_button.setText(f"Отправить очередь ({queued})")
        self.send_button.setEnabled(queued > 0)
        for row in rows:
            mode = self.filter.currentIndex()
            if (mode == 1 and (row["queued"] or row["sent"])) or (mode == 2 and not row["queued"]) or (mode == 3 and not row["sent"]):
                continue
            title = row["draft"]["fields"].get("Title") or "Без заголовка"
            if self.search.text().casefold() not in title.casefold():
                continue
            price = row["draft"]["fields"].get("Price") or "—"
            item = QListWidgetItem(f"{title}\n{price} ₽   ·   {self.status(row)}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.list.addItem(item)
            if row["id"] == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)

    def select(self, item, previous=None):
        if not item:
            return
        ad_id = item.data(Qt.ItemDataRole.UserRole)
        self.save()
        self.load(self.store.get(ad_id)["draft"])

    def load(self, ad):
        self.loading = True
        self.current = copy.deepcopy(ad)
        self.editor.setEnabled(True)
        for key, edit in self.inputs.items():
            value = ad["fields"].get(key, "")
            if isinstance(edit, QTextEdit):
                edit.setPlainText(value)
            elif isinstance(edit, (QComboBox, MultiChoice)):
                if isinstance(edit, QComboBox):
                    for index in reversed(range(edit.count())):
                        if edit.itemData(index, Qt.ItemDataRole.UserRole) == "legacy-invalid":
                            edit.removeItem(index)
                    value = value.replace("\xa0", " ")
                    if value and edit.findText(value) < 0:
                        edit.addItem(value, "legacy-invalid")
                        edit.model().item(edit.count() - 1).setEnabled(False)
                edit.setCurrentText(value)
            else:
                edit.setText(value)
        self.photos()
        self.loading = False
        self.dirty = False
        self.show_state()
        self.show_validation()
        self.refresh()

    def show_state(self):
        if self.current:
            row = self.store.get(self.current["id"])
            self.state.setText(f"{self.status(row)}\nID: {self.current['id']}\n"
                "Правки сохраняются в черновик. Для отправки нажмите «В очередь / обновить».")

    def create(self):
        self.save()
        ad = new_ad()
        self.store.save(ad)
        self.load(ad)
        self.inputs["Title"].setFocus()

    def duplicate(self):
        self.save()
        if self.current:
            ad = copy.deepcopy(self.current)
            ad["id"] = new_ad()["id"]
            self.store.save(ad)
            self.load(ad)

    def photos(self):
        self.photo_list.clear()
        for index, name in enumerate(self.current["photos"]):
            pix = QPixmap(str(self.store.root / "photos" / name))
            item = QListWidgetItem(QIcon(pix), "Главное" if index == 0 else f"Фото {index + 1}")
            item.setToolTip(name)
            self.photo_list.addItem(item)

    def add_photos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Выберите фотографии", "", "Фотографии (*.jpg *.jpeg *.png)")
        if not paths:
            return
        invalid = []
        for path in paths:
            if len(self.current["photos"]) >= 10:
                invalid.append(Path(path).name + ": максимум 10 фото в объявлении")
                continue
            if Path(path).stat().st_size > 25 * 1024 * 1024:
                invalid.append(Path(path).name + ": файл больше 25 МБ")
                continue
            if QPixmap(path).isNull():
                invalid.append(Path(path).name)
                continue
            name = self.store.import_photo(path)
            if name not in self.current["photos"]:
                self.current["photos"].append(name)
        self.photos()
        self.changed()
        if invalid:
            QMessageBox.warning(self, "Не удалось прочитать фото", "\n".join(invalid))

    def move_photo(self, direction):
        index = self.photo_list.currentRow()
        other = index + direction
        photos = self.current["photos"]
        if index >= 0 and 0 <= other < len(photos):
            photos[index], photos[other] = photos[other], photos[index]
            self.photos()
            self.photo_list.setCurrentRow(other)
            self.changed()

    def remove_photo(self):
        index = self.photo_list.currentRow()
        if index >= 0:
            self.current["photos"].pop(index)
            self.photos()
            self.changed()

    def show_validation(self):
        if not self.current:
            return []
        ad = self.capture()
        issues = field_issues(ad)
        for key, label in self.field_errors.items():
            text = "\n".join(issues.get(key, []))
            label.setText(text)
            label.setVisible(bool(text))
        errors = validate(ad) + validate_assets(ad, self.store.root)
        self.photo_list.setToolTip("\n".join(issues.get("photos", [])))
        return errors

    def check_card(self):
        self.save()
        errors = self.show_validation()
        if errors:
            QMessageBox.warning(self, "Что исправить", "\n".join(errors))
        else:
            QMessageBox.information(self, "Проверка", "Поля и фотографии прошли локальную проверку. Окончательное решение — после обработки Авито.")

    def enqueue(self):
        self.save()
        if not self.current:
            return
        errors = self.show_validation()
        if errors:
            QMessageBox.warning(self, "Проверьте объявление", "\n".join(errors))
            return
        self.store.enqueue(self.current)
        self.refresh()
        self.show_state()

    def unqueue(self):
        self.save()
        if self.current:
            self.store.unqueue(self.current["id"])
            self.refresh()
            self.show_state()

    def settings(self):
        try:
            Settings(self.store, self).exec()
        except Exception as exc:
            QMessageBox.warning(self, "Настройки", str(exc))

    def send(self):
        self.save()
        stale = [row for row in self.store.all() if row["queued"] and row["queued"] != row["draft"]]
        if stale:
            QMessageBox.warning(self, "Обновите очередь", "В карточках есть правки после добавления в очередь.\n"
                "Откройте их и нажмите «В очередь / обновить» либо уберите из очереди:\n\n" +
                "\n".join(row["draft"]["fields"].get("Title", "Без заголовка") for row in stale))
            return
        try:
            config = json.loads(self.store.get_setting("s3", "{}"))
            for key in ("access_key", "secret_key"):
                config[key] = unprotect(config.get(key, ""))
            check_config(config)
        except Exception as exc:
            QMessageBox.warning(self, "Нужны настройки S3", str(exc))
            self.settings()
            return
        ads = self.store.snapshot()
        problems = []
        for ad in ads:
            errors = validate(ad) + validate_assets(ad, self.store.root)
            if errors:
                problems.append((ad["fields"].get("Title") or ad["id"]) + ":\n" + "\n".join(errors))
        if problems:
            QMessageBox.warning(self, "Фид не отправлен — исправьте карточки", "\n\n".join(problems))
            return
        if not ads:
            return
        self.job_id = self.store.begin_job(ads)
        self.centralWidget().setEnabled(False)
        self.worker = Worker(ads, self.store.root, config)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.succeeded.connect(self.sent)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def sent(self, url):
        self.store.complete_job(self.job_id, url)
        self.statusBar().showMessage("Фид обновлён. Ожидается обработка Авито.")
        QMessageBox.information(self, "Отправлено в S3", "Постоянная ссылка обновлена:\n\n" + url +
            "\n\nУкажите её в автозагрузке Авито. Результат размещения проверьте в отчёте Авито — клиент пока не получает статусы через API.")

    def failed(self, message):
        self.store.fail_job(self.job_id, message)
        self.statusBar().showMessage("Отправка не подтверждена. Очередь сохранена для повтора.")
        QMessageBox.warning(self, "Не удалось подтвердить отправку", message +
            "\n\nОчередь сохранена. Повтор использует те же идентификаторы объявлений.")

    def finished(self):
        self.centralWidget().setEnabled(True)
        self.refresh()
        if self.current:
            self.show_state()
        self.worker.deleteLater()
        self.worker = None

    def history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("История отправок")
        dialog.resize(780, 450)
        box = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        statuses = {"sent": "Отправлено в S3", "error": "Ошибка / результат не подтверждён", "unknown": "Отправка прервана", "sending": "Отправка"}
        text.setPlainText("\n\n".join(f"{j['created']} · {statuses[j['status']]}\n{j['detail']}" for j in self.store.jobs()) or "Отправок пока нет.")
        box.addWidget(text)
        box.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def backup(self):
        self.save()
        path, _ = QFileDialog.getSaveFileName(self, "Резервная копия базы и фотографий", "avito-backup.zip", "ZIP (*.zip)")
        if path:
            try:
                self.store.backup(path)
                self.statusBar().showMessage("Резервная копия сохранена: " + path)
            except Exception as exc:
                QMessageBox.warning(self, "Резервная копия", str(exc))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Идёт отправка", "Дождитесь завершения отправки перед закрытием клиента.")
            event.ignore()
            return
        self.save()
        self.store.close()
        event.accept()
