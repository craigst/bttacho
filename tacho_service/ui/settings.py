"""Settings dialog -- general options and delivery destinations."""

import uuid
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from ..config import Config, Destination
from ..dispatch import post

WINDOWS = [("Last 7 days", 7), ("Last 14 days", 14), ("Last 28 days", 28),
           ("All data", None)]


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tacho Settings")
        self.setMinimumSize(560, 440)
        self.config = config

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._destinations_tab(), "Destinations")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------------------------------------------------------- general

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(10)

        self.auto_sync = QCheckBox("Send automatically when a card is read")
        self.auto_sync.setChecked(self.config.auto_sync)
        form.addRow("", self.auto_sync)

        self.window = QComboBox()
        for label, _ in WINDOWS:
            self.window.addItem(label)
        idx = next((i for i, (_, v) in enumerate(WINDOWS)
                    if v == self.config.send_window_days), 1)
        self.window.setCurrentIndex(idx)
        form.addRow("Send", self.window)

        self.preview = QSpinBox()
        self.preview.setRange(1, 50)
        self.preview.setValue(self.config.preview_trips)
        form.addRow("Trips in preview", self.preview)

        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setSuffix(" days")
        self.retention.setValue(self.config.download_retention_days)
        form.addRow("Keep downloads", self.retention)

        self.retry_hours = QSpinBox()
        self.retry_hours.setRange(1, 720)
        self.retry_hours.setSuffix(" hours")
        self.retry_hours.setValue(self.config.retry_limit_hours)
        form.addRow("Retry failed sends for", self.retry_hours)

        self.notify_fail = QCheckBox("Notify when a send fails")
        self.notify_fail.setChecked(self.config.notify_on_failure)
        form.addRow("", self.notify_fail)

        self.notify_ok = QCheckBox("Notify when a send succeeds")
        self.notify_ok.setChecked(self.config.notify_on_success)
        form.addRow("", self.notify_ok)

        self.auto_update = QCheckBox("Check for signed app updates automatically")
        self.auto_update.setChecked(self.config.auto_update)
        form.addRow("", self.auto_update)

        self.update_apply = QCheckBox("Apply verified updates when the service is idle")
        self.update_apply.setChecked(self.config.update_auto_apply)
        form.addRow("", self.update_apply)

        self.update_poll = QSpinBox()
        self.update_poll.setRange(5, 15)
        self.update_poll.setSuffix(" minutes")
        self.update_poll.setValue(self.config.update_poll_minutes)
        form.addRow("Update check", self.update_poll)

        self.update_key = QLineEdit()
        self.update_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.update_key.setPlaceholderText("Ed25519 public key (base64url)")
        self.update_key.setText(self.config.update_public_key)
        form.addRow("Update key", self.update_key)

        note = QLabel("Card downloads are always saved locally, whether or not "
                      "they are sent.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        form.addRow("", note)
        return w

    # ----------------------------------------------------------- destinations

    def _destinations_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(12, 12, 12, 12)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list)

        btns = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        btns.addWidget(add)
        btns.addWidget(rm)
        left.addLayout(btns)
        outer.addLayout(left, 1)

        self.editor = QStackedWidget()
        self.editor.addWidget(self._empty_page())
        self.editor.addWidget(self._form_page())
        outer.addWidget(self.editor, 2)

        self._working = [Destination(**vars(d)) for d in self.config.destinations]
        self._refresh_list()
        return w

    def _empty_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        lbl = QLabel("Select a destination, or add one.\n\n"
                     "Every destination receives the same JSON body; only the "
                     "URL, method, headers and authentication differ.")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: palette(mid);")
        v.addWidget(lbl)
        return w

    def _form_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(9)

        self.d_enabled = QCheckBox("Enabled")
        form.addRow("", self.d_enabled)

        self.d_name = QLineEdit()
        form.addRow("Name", self.d_name)

        self.d_url = QLineEdit()
        self.d_url.setPlaceholderText("https://example.com/webhook/tacho")
        form.addRow("URL", self.d_url)

        self.d_method = QComboBox()
        self.d_method.addItems(["POST", "PUT", "PATCH"])
        form.addRow("Method", self.d_method)

        self.d_auth_type = QComboBox()
        self.d_auth_type.addItems(["none", "bearer", "basic", "header"])
        self.d_auth_type.currentTextChanged.connect(self._auth_changed)
        form.addRow("Auth", self.d_auth_type)

        self.d_auth_a = QLineEdit()
        self.d_auth_b = QLineEdit()
        self.d_auth_b.setEchoMode(QLineEdit.EchoMode.Password)
        self.d_auth_a_label = QLabel("Token")
        self.d_auth_b_label = QLabel("Password")
        form.addRow(self.d_auth_a_label, self.d_auth_a)
        form.addRow(self.d_auth_b_label, self.d_auth_b)

        self.d_headers = QPlainTextEdit()
        self.d_headers.setPlaceholderText("X-Custom-Header: value\nOne per line")
        self.d_headers.setFixedHeight(70)
        form.addRow("Headers", self.d_headers)

        self.d_timeout = QSpinBox()
        self.d_timeout.setRange(1, 300)
        self.d_timeout.setSuffix(" s")
        form.addRow("Timeout", self.d_timeout)

        test = QPushButton("Send test request")
        test.clicked.connect(self._test)
        form.addRow("", test)

        for widget in (self.d_enabled, self.d_name, self.d_url, self.d_method,
                       self.d_auth_type, self.d_auth_a, self.d_auth_b,
                       self.d_headers, self.d_timeout):
            for sig in ("textChanged", "stateChanged", "currentTextChanged",
                        "valueChanged"):
                if hasattr(widget, sig):
                    getattr(widget, sig).connect(self._capture)
                    break
        return w

    def _auth_changed(self, kind: str):
        show_a = kind in ("bearer", "basic", "header")
        show_b = kind in ("basic", "header")
        self.d_auth_a_label.setText({"bearer": "Token", "basic": "Username",
                                     "header": "Header name"}.get(kind, "Token"))
        self.d_auth_b_label.setText({"basic": "Password",
                                     "header": "Header value"}.get(kind, "Password"))
        self.d_auth_a.setVisible(show_a)
        self.d_auth_a_label.setVisible(show_a)
        self.d_auth_b.setVisible(show_b)
        self.d_auth_b_label.setVisible(show_b)
        self.d_auth_b.setEchoMode(QLineEdit.EchoMode.Password if kind == "basic"
                                  else QLineEdit.EchoMode.Normal)

    # ------------------------------------------------------------------ state

    def _refresh_list(self):
        self.list.clear()
        for d in self._working:
            item = QListWidgetItem(d.name or "(unnamed)")
            if not d.enabled:
                item.setForeground(self.palette().mid())
            self.list.addItem(item)
        if self._working:
            self.list.setCurrentRow(min(self.list.currentRow() if
                                        self.list.currentRow() >= 0 else 0,
                                        len(self._working) - 1))
        else:
            self.editor.setCurrentIndex(0)

    def _select(self, row: int):
        if row < 0 or row >= len(self._working):
            self.editor.setCurrentIndex(0)
            return
        self._loading = True
        d = self._working[row]
        self.d_enabled.setChecked(d.enabled)
        self.d_name.setText(d.name)
        self.d_url.setText(d.url)
        self.d_method.setCurrentText(d.method or "POST")
        auth = d.auth or {"type": "none"}
        self.d_auth_type.setCurrentText(auth.get("type", "none"))
        self.d_auth_a.setText(auth.get("token") or auth.get("username")
                              or auth.get("name") or "")
        self.d_auth_b.setText(auth.get("password") or auth.get("value") or "")
        self.d_headers.setPlainText(
            "\n".join(f"{k}: {v}" for k, v in (d.headers or {}).items()))
        self.d_timeout.setValue(d.timeout_seconds)
        self._auth_changed(self.d_auth_type.currentText())
        self._loading = False
        self.editor.setCurrentIndex(1)

    def _capture(self, *_):
        if getattr(self, "_loading", False):
            return
        row = self.list.currentRow()
        if row < 0 or row >= len(self._working):
            return
        d = self._working[row]
        d.enabled = self.d_enabled.isChecked()
        d.name = self.d_name.text().strip()
        d.url = self.d_url.text().strip()
        d.method = self.d_method.currentText()
        d.timeout_seconds = self.d_timeout.value()

        kind = self.d_auth_type.currentText()
        a, b = self.d_auth_a.text(), self.d_auth_b.text()
        d.auth = {"type": kind}
        if kind == "bearer":
            d.auth["token"] = a
        elif kind == "basic":
            d.auth.update(username=a, password=b)
        elif kind == "header":
            d.auth.update(name=a, value=b)

        headers = {}
        for line in self.d_headers.toPlainText().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip():
                    headers[k.strip()] = v.strip()
        d.headers = headers

        item = self.list.item(row)
        if item:
            item.setText(d.name or "(unnamed)")

    def _add(self):
        self._working.append(Destination(
            id=uuid.uuid4().hex[:8], name="New destination", url="", enabled=False))
        self._refresh_list()
        self.list.setCurrentRow(len(self._working) - 1)

    def _remove(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._working):
            del self._working[row]
            self._refresh_list()

    def _test(self):
        row = self.list.currentRow()
        if row < 0:
            return
        d = self._working[row]
        if not d.url:
            QMessageBox.warning(self, "No URL", "Set a URL before testing.")
            return
        import json
        body = json.dumps({"driver_name": "TEST", "card_number": "TEST",
                           "total_trips": 0, "trips": [],
                           "_test": True}).encode()
        ok, status, error = post(d, body)
        if ok:
            QMessageBox.information(self, "Test succeeded",
                                    f"{d.name} responded {status}.")
        else:
            QMessageBox.warning(self, "Test failed",
                                f"{d.name}: {error}"
                                + ("\n\nThis is a permanent failure — the "
                                   "endpoint rejected the request."
                                   if status and 400 <= status < 500
                                      and status not in (408, 429) else ""))

    def _save(self):
        c = self.config
        c.auto_sync = self.auto_sync.isChecked()
        c.send_window_days = WINDOWS[self.window.currentIndex()][1]
        c.preview_trips = self.preview.value()
        c.download_retention_days = self.retention.value()
        c.retry_limit_hours = self.retry_hours.value()
        c.notify_on_failure = self.notify_fail.isChecked()
        c.notify_on_success = self.notify_ok.isChecked()
        c.auto_update = self.auto_update.isChecked()
        c.update_auto_apply = self.update_apply.isChecked()
        c.update_poll_minutes = self.update_poll.value()
        c.update_public_key = self.update_key.text().strip()
        c.destinations = self._working
        c.save()
        self.accept()
