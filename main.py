import sys
import os
import json
import time
import threading
import requests
import shutil
import hashlib
import datetime
import traceback
from pathlib import Path

from send2trash import send2trash
from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QInputDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QPushButton,
)

APP_NAME = "Webhook-Uploader"
APP_VERSION = "1.8.2"
BASE_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / APP_NAME
CFG_DIR = BASE_DIR / "cfg"
LOG_DIR = BASE_DIR / "log"
CONFIG_FILE = CFG_DIR / "cfg.json"
LOG_FILE = LOG_DIR / "log.json"
TEMPLATE_FILE = CFG_DIR / "post.txt"
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

BG = "#0f1012"
PANEL = "#151618"
TEXT = "#d8d8d8"
MUTED = "#7f7f7f"
FIELD_BG = "#3b3b3b"
FIELD_TEXT = "#181818"
BLUE = "#4a9bff"
YELLOW = "#f2b01e"
ICON_GRAY = "#5b5b5b"
HOVER_DARK = "#222428"

WAIT_TIME = 3600
POST_INTERVAL = 10
MONITOR_CHECK_INTERVAL = 5

file_lock = threading.RLock()
send_lock = threading.Lock()
monitoring = True
stop_event = threading.Event()


def load_json(path: Path, default):
    with file_lock:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default


def save_json(path: Path, data):
    with file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


def load_template():
    with file_lock:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        if not TEMPLATE_FILE.exists():
            default = """🆕
📄 `{filename}`
📅 `{creation_str}`
🆙 Upload: {upload_str}
___"""
            TEMPLATE_FILE.write_text(default, encoding="utf-8")
            return default
        try:
            return TEMPLATE_FILE.read_text(encoding="utf-8")
        except Exception:
            return """🆕
📄 `{filename}`
📅 `{creation_str}`
🆙 Upload: {upload_str}
___"""


config = load_json(CONFIG_FILE, {"folder": "", "webhook": ""})
sent_history = load_json(LOG_FILE, [])


class UISignals(QObject):
    status_changed = Signal(bool)
    toast = Signal(str)
    refresh_fields = Signal()


signals = UISignals()


def create_tray_icon(active: bool) -> QIcon:
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    outer = QColor(BLUE if active else YELLOW)
    p.setBrush(outer)
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor(BG))
    p.drawEllipse(16, 16, 32, 32)
    p.end()
    return QIcon(pix)


def get_file_hash(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def file_is_free(path):
    try:
        with open(path, "rb+"):
            return True
    except Exception:
        return False


def send_file(path):
    if not config.get("webhook"):
        return False

    filename = os.path.basename(path)
    watched_folder = config.get("folder", "")
    error_dir = Path(watched_folder) / "ERROR" if watched_folder else None

    try:
        if os.path.getsize(path) / (1024 * 1024) > 25:
            if error_dir is not None:
                error_dir.mkdir(exist_ok=True)
                shutil.move(path, error_dir / filename)
            return False
    except Exception:
        return False

    file_hash = get_file_hash(path)
    if not file_hash:
        return False

    with file_lock:
        if any(item.get("hash") == file_hash for item in sent_history):
            return False

    if not file_is_free(path):
        return False

    try:
        stat = os.stat(path)
        now_dt = datetime.datetime.now()
        creation_dt = datetime.datetime.fromtimestamp(stat.st_ctime)
        creation_str = f"{DAYS_OF_WEEK[creation_dt.weekday()]}, {creation_dt.strftime('%d/%m/%y %H:%M:%S')}"
        upload_str = f"{DAYS_OF_WEEK[now_dt.weekday()]}, {now_dt.strftime('%d/%m/%y %H:%M:%S')}"

        template = load_template()
        message = template.format(filename=filename, creation_str=creation_str, upload_str=upload_str)

        for attempt in range(4):
            try:
                with open(path, "rb") as f:
                    res = requests.post(
                        config["webhook"],
                        data={"content": message},
                        files={"file": (filename, f)},
                        timeout=15,
                    )

                if res.status_code in [200, 204]:
                    send2trash(os.path.abspath(path))
                    with file_lock:
                        sent_history.append({"file": filename, "hash": file_hash, "date": upload_str})
                    save_json(LOG_FILE, sent_history)
                    return True

                if res.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue

                break
            except Exception:
                time.sleep(2 ** attempt)

        return False
    except Exception:
        return False


def send_now_manual():
    if not config.get("folder"):
        signals.toast.emit("Selecione uma pasta primeiro.")
        return

    folder = config.get("folder", "")
    if not os.path.isdir(folder):
        signals.toast.emit("A pasta monitorada não existe.")
        return

    if not send_lock.acquire(blocking=False):
        signals.toast.emit("Já existe um envio em andamento.")
        return

    try:
        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
        ]
        sent_any = False
        for file in sorted(files, key=os.path.getctime):
            if stop_event.is_set():
                break
            if send_file(file):
                sent_any = True
                signals.toast.emit(f"Enviado: {os.path.basename(file)}")
                for _ in range(POST_INTERVAL):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
        if not sent_any:
            signals.toast.emit("Nenhum arquivo disponível para enviar agora.")
    except Exception:
        traceback.print_exc()
        signals.toast.emit("Falha ao enviar agora.")
    finally:
        send_lock.release()
        signals.refresh_fields.emit()


def monitoring_loop():
    global monitoring
    while not stop_event.is_set():
        if monitoring and config.get("folder") and config.get("webhook"):
            folder = config.get("folder", "")
            if os.path.isdir(folder) and send_lock.acquire(blocking=False):
                try:
                    now = time.time()
                    files = [
                        os.path.join(folder, f)
                        for f in os.listdir(folder)
                        if os.path.isfile(os.path.join(folder, f))
                    ]
                    ready = [p for p in files if now - os.path.getctime(p) >= WAIT_TIME]
                    for file in sorted(ready, key=os.path.getctime):
                        if stop_event.is_set() or not monitoring:
                            break
                        if send_file(file):
                            signals.toast.emit(f"Enviado automaticamente: {os.path.basename(file)}")
                            for _ in range(POST_INTERVAL):
                                if stop_event.is_set() or not monitoring:
                                    break
                                time.sleep(1)
                except Exception:
                    traceback.print_exc()
                finally:
                    send_lock.release()
        for _ in range(MONITOR_CHECK_INTERVAL):
            if stop_event.is_set():
                break
            time.sleep(1)


class HoverButton(QPushButton):
    def __init__(self, text, size=44, tooltip="", bg="transparent", hover=HOVER_DARK, fg=TEXT, font_size=18):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self._bg = bg
        self._hover = hover
        self._fg = fg
        self._size = size
        self.setFixedSize(size, size)
        self.setFont(QFont("Segoe UI Symbol", font_size))
        self.apply_style(False)

    def apply_style(self, hovered):
        bg = self._hover if hovered else self._bg
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {bg};
                color: {self._fg};
                border: none;
                border-radius: {self._size // 2}px;
            }}
            """
        )

    def enterEvent(self, event):
        self.apply_style(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.apply_style(False)
        super().leaveEvent(event)


class CircleActionButton(HoverButton):
    def __init__(self, text="●", tooltip=""):
        super().__init__(text, size=32, tooltip=tooltip, bg=BLUE, hover="#6fb4ff", fg="#ffffff", font_size=15)


class RoundedPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        painter.fillPath(path, QColor(PANEL))
        pen = QPen(QColor("#1c1d21"))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


class WebhookWindow(QWidget):
    def __init__(self, tray_icon):
        super().__init__()
        self.tray_icon = tray_icon
        self.drag_pos = None
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(648, 376)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        self.panel = RoundedPanel()
        outer.addWidget(self.panel)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(26, 18, 20, 16)
        layout.setSpacing(14)

        self.title = QLabel(f"Webhook Uploader v{APP_VERSION}")
        self.title.setAlignment(Qt.AlignHCenter)
        self.title.setStyleSheet(f"color:{BLUE}; font: 700 19px 'Segoe UI';")
        layout.addWidget(self.title)

        self.webhook_label = QLabel("Webhook")
        self.webhook_label.setStyleSheet(f"color:{MUTED}; font: 600 14px 'Segoe UI';")
        layout.addWidget(self.webhook_label)

        self.webhook_row = QHBoxLayout()
        self.webhook_row.setSpacing(12)
        self.webhook_edit = self.make_field("Cole o webhook do Discord")
        self.webhook_row.addWidget(self.webhook_edit, 1)
        self.webhook_btn = CircleActionButton("✎", "Editar webhook")
        self.webhook_btn.clicked.connect(self.edit_webhook)
        self.webhook_row.addWidget(self.webhook_btn)
        layout.addLayout(self.webhook_row)

        self.folder_label = QLabel("Watched Folder")
        self.folder_label.setStyleSheet(f"color:{MUTED}; font: 600 14px 'Segoe UI';")
        layout.addWidget(self.folder_label)

        self.folder_row = QHBoxLayout()
        self.folder_row.setSpacing(12)
        self.folder_edit = self.make_field("Selecione a pasta monitorada")
        self.folder_row.addWidget(self.folder_edit, 1)
        self.folder_btn = CircleActionButton("⋯", "Escolher pasta")
        self.folder_btn.clicked.connect(self.choose_folder)
        self.folder_row.addWidget(self.folder_btn)
        layout.addLayout(self.folder_row)

        layout.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)

        self.pause_btn = HoverButton("❚❚", size=42, tooltip="Pausar / continuar", bg="transparent", hover="#2c2210", fg=YELLOW, font_size=18)
        self.pause_btn.clicked.connect(self.toggle_monitoring)
        bottom.addWidget(self.pause_btn)

        self.send_now_btn = HoverButton("➜", size=42, tooltip="Enviar agora", bg="transparent", hover="#1d2733", fg=BLUE, font_size=18)
        self.send_now_btn.clicked.connect(self.start_send_now)
        bottom.addWidget(self.send_now_btn)

        self.clear_btn = HoverButton("⌫", size=42, tooltip="Limpar histórico", bg="transparent", hover="#232323", fg=ICON_GRAY, font_size=18)
        self.clear_btn.clicked.connect(self.clear_history)
        bottom.addWidget(self.clear_btn)

        self.cfg_btn = HoverButton("⚙", size=42, tooltip="Abrir pasta de configs", bg="transparent", hover="#232323", fg=ICON_GRAY, font_size=18)
        self.cfg_btn.clicked.connect(self.open_config_folder)
        bottom.addWidget(self.cfg_btn)

        self.close_btn = HoverButton("✕", size=42, tooltip="Fechar aplicativo", bg="#3a3a3a", hover="#525252", fg="#161616", font_size=16)
        self.close_btn.clicked.connect(self.exit_app)
        bottom.addWidget(self.close_btn)

        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.refresh_fields()
        self.update_pause_visual()

        signals.status_changed.connect(self.on_status_changed)
        signals.toast.connect(self.show_tray_message)
        signals.refresh_fields.connect(self.refresh_fields)

    def make_field(self, placeholder):
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        edit.setMinimumHeight(37)
        edit.setStyleSheet(
            f"""
            QLineEdit {{
                background: {FIELD_BG};
                color: {FIELD_TEXT};
                border: none;
                border-radius: 18px;
                padding: 0 14px;
                font: 600 13px 'Segoe UI';
            }}
            QLineEdit::placeholder {{ color: #7c7c7c; }}
            """
        )
        return edit

    def refresh_fields(self):
        self.webhook_edit.setText(config.get("webhook", ""))
        self.folder_edit.setText(config.get("folder", ""))

    def show_tray_message(self, text):
        self.tray_icon.showMessage(APP_NAME, text, QSystemTrayIcon.Information, 2500)

    def edit_webhook(self):
        current = config.get("webhook", "")
        text, ok = QInputDialog.getText(self, "Webhook", "Paste the Discord Webhook:", text=current)
        if ok:
            text = text.strip()
            if text and "discord.com" not in text and "discordapp.com" not in text:
                QMessageBox.warning(self, APP_NAME, "Webhook inválido.")
                return
            config["webhook"] = text
            save_json(CONFIG_FILE, config)
            self.refresh_fields()
            signals.toast.emit("Webhook atualizado.")

    def choose_folder(self):
        current = config.get("folder", "") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select folder to monitor", current)
        if folder:
            config["folder"] = folder
            save_json(CONFIG_FILE, config)
            self.refresh_fields()
            signals.toast.emit("Pasta monitorada atualizada.")

    def start_send_now(self):
        thread = threading.Thread(target=send_now_manual, daemon=True)
        thread.start()

    def clear_history(self):
        with file_lock:
            sent_history.clear()
        save_json(LOG_FILE, sent_history)
        signals.toast.emit("Histórico limpo.")

    def open_config_folder(self):
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(CFG_DIR))

    def toggle_monitoring(self):
        global monitoring
        monitoring = not monitoring
        signals.status_changed.emit(monitoring)

    def on_status_changed(self, active):
        self.tray_icon.setIcon(create_tray_icon(active))
        self.update_pause_visual()

    def update_pause_visual(self):
        if monitoring:
            self.pause_btn.setText("❚❚")
            self.pause_btn._fg = YELLOW
            self.pause_btn._hover = "#2c2210"
            self.pause_btn.apply_style(False)
            self.pause_btn.setToolTip("Pausar")
        else:
            self.pause_btn.setText("▶")
            self.pause_btn._fg = YELLOW
            self.pause_btn._hover = "#2c2210"
            self.pause_btn.apply_style(False)
            self.pause_btn.setToolTip("Continuar")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_near_tray()

    def show_near_tray(self):
        self.refresh_fields()
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 24
        y = screen.bottom() - self.height() - 54
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def exit_app(self):
        stop_event.set()
        self.hide()
        self.tray_icon.hide()
        QApplication.quit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)


class TrayController(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tray = QSystemTrayIcon(create_tray_icon(True), app)
        self.menu = QMenu()

        self.open_action = QAction("Open")
        self.send_now_action = QAction("Send Now")
        self.pause_action = QAction("Pause")
        self.configs_action = QAction("Open Config Folder")
        self.exit_action = QAction("Exit")

        self.menu.addAction(self.open_action)
        self.menu.addAction(self.send_now_action)
        self.menu.addSeparator()
        self.menu.addAction(self.pause_action)
        self.menu.addAction(self.configs_action)
        self.menu.addSeparator()
        self.menu.addAction(self.exit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")

        self.window = WebhookWindow(self.tray)

        self.open_action.triggered.connect(self.window.show_near_tray)
        self.send_now_action.triggered.connect(self.start_send_now)
        self.pause_action.triggered.connect(self.toggle_monitoring)
        self.configs_action.triggered.connect(self.window.open_config_folder)
        self.exit_action.triggered.connect(self.exit_app)
        self.tray.activated.connect(self.on_tray_activated)
        signals.status_changed.connect(self.sync_pause_action)

        self.sync_pause_action(monitoring)
        self.tray.show()

    def start_send_now(self):
        thread = threading.Thread(target=send_now_manual, daemon=True)
        thread.start()

    def toggle_monitoring(self):
        global monitoring
        monitoring = not monitoring
        signals.status_changed.emit(monitoring)

    def sync_pause_action(self, active):
        self.pause_action.setText("Pause" if active else "Resume")
        self.tray.setIcon(create_tray_icon(active))
        self.window.update_pause_visual()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.toggle_visible()

    def exit_app(self):
        stop_event.set()
        self.window.hide()
        self.tray.hide()
        QApplication.quit()


def ensure_first_run(window: WebhookWindow):
    if not config.get("folder"):
        window.choose_folder()
    if not config.get("webhook"):
        window.edit_webhook()


if __name__ == "__main__":
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = TrayController(app)
    ensure_first_run(controller.window)

    worker = threading.Thread(target=monitoring_loop, daemon=True)
    worker.start()

    sys.exit(app.exec())