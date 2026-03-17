import sys
import os
import json
import time
import math
import colorsys
import threading
import requests
import shutil
import hashlib
import datetime
import traceback
from pathlib import Path

from send2trash import send2trash
from PySide6.QtCore import Qt, Signal, QObject, QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QBrush, QLinearGradient
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSystemTrayIcon,
    QPushButton,
    QStackedWidget,
    QGraphicsOpacityEffect,
    QFileDialog,
    QScrollArea,
    QTextEdit,
    QDialog,
    QColorDialog,
    )

try:
    import winreg
except Exception:
    winreg = None

APP_NAME = "Webhook-Uploader"
APP_VERSION = "2.0.4"
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
FIELD_BG = "#222428"
FIELD_TEXT = "#e9ecf2"
BLUE = "#4a9bff"
YELLOW = "#f2b01e"
ICON_GRAY = "#7a7f89"
HOVER_DARK = "#222428"
RED = "#ff5f73"
GREEN = "#4fd18b"
CARD = "#1a1c20"
CARD_BORDER = "#252830"
DEFAULT_EMBED_COLOR = "#F54927"

WAIT_TIME = 3600
POST_INTERVAL = 10
MONITOR_CHECK_INTERVAL = 5
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

file_lock = threading.RLock()
send_lock = threading.Lock()
sending_event = threading.Event()
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


def default_template_text():
    return """🆕
📄 `{filename}`
📅 `{creation_str}`
🆙 Upload: {upload_str}
___"""


def load_template():
    with file_lock:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        if not TEMPLATE_FILE.exists():
            default = default_template_text()
            TEMPLATE_FILE.write_text(default, encoding="utf-8")
            return default
        try:
            return TEMPLATE_FILE.read_text(encoding="utf-8")
        except Exception:
            return default_template_text()


def save_template(text: str):
    with file_lock:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        TEMPLATE_FILE.write_text(text, encoding="utf-8")


def parse_hex_color(value: str):
    text = (value or "").strip().upper()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3 and all(c in "0123456789ABCDEF" for c in text):
        text = "".join(c * 2 for c in text)
    if len(text) == 6 and all(c in "0123456789ABCDEF" for c in text):
        return f"#{text}"
    return None


def normalize_hex_color(value: str, default: str = DEFAULT_EMBED_COLOR) -> str:
    parsed = parse_hex_color(value)
    if parsed:
        return parsed
    return parse_hex_color(default) or "#F54927"


def discord_color_int(hex_color: str) -> int:
    return int(normalize_hex_color(hex_color)[1:], 16)


def render_template_text(template: str, filename: str, creation_str: str, upload_str: str) -> str:
    return (
        template
        .replace("{filename}", filename)
        .replace("{creation_str}", creation_str)
        .replace("{upload_str}", upload_str)
    )


def normalize_config(raw):
    return {
        "folder": raw.get("folder", ""),
        "webhook": raw.get("webhook", ""),
        "start_with_windows": bool(raw.get("start_with_windows", False)),
        "delete_after_send": bool(raw.get("delete_after_send", True)),
        "use_embed": bool(raw.get("use_embed", False)),
        "embed_color": normalize_hex_color(raw.get("embed_color", DEFAULT_EMBED_COLOR)),
    }


config = normalize_config(load_json(CONFIG_FILE, {}))
sent_history = load_json(LOG_FILE, [])


class UISignals(QObject):
    status_changed = Signal(bool)
    toast = Signal(str, str)
    refresh_fields = Signal()


signals = UISignals()


TRAY_ICON_SIZE = 64
TRAY_ICON_CENTER = TRAY_ICON_SIZE // 2
TRAY_RING_RADIUS = 23
TRAY_DOT_RADIUS = 5
TRAY_DOT_COUNT = 12
TRAY_BLUE = QColor(70, 140, 255)
TRAY_YELLOW = QColor(255, 210, 0)
TRAY_GREEN = QColor(0, 220, 120)


def draw_tray_ring(color: QColor) -> QIcon:
    pix = QPixmap(TRAY_ICON_SIZE, TRAY_ICON_SIZE)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = QPen(color)
    pen.setWidth(8)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(
        TRAY_ICON_CENTER - TRAY_RING_RADIUS,
        TRAY_ICON_CENTER - TRAY_RING_RADIUS,
        TRAY_RING_RADIUS * 2,
        TRAY_RING_RADIUS * 2,
    )

    painter.end()
    return QIcon(pix)


def draw_tray_sending(rotation: float) -> QIcon:
    pix = QPixmap(TRAY_ICON_SIZE, TRAY_ICON_SIZE)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = QPen(TRAY_BLUE)
    pen.setWidth(8)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(
        TRAY_ICON_CENTER - TRAY_RING_RADIUS,
        TRAY_ICON_CENTER - TRAY_RING_RADIUS,
        TRAY_RING_RADIUS * 2,
        TRAY_RING_RADIUS * 2,
    )

    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(TRAY_GREEN))

    for i in range(TRAY_DOT_COUNT):
        angle = (i / TRAY_DOT_COUNT) * 2 * math.pi + rotation
        x = TRAY_ICON_CENTER + math.cos(angle) * TRAY_RING_RADIUS
        y = TRAY_ICON_CENTER + math.sin(angle) * TRAY_RING_RADIUS
        painter.drawEllipse(
            int(x - TRAY_DOT_RADIUS),
            int(y - TRAY_DOT_RADIUS),
            TRAY_DOT_RADIUS * 2,
            TRAY_DOT_RADIUS * 2,
        )

    painter.end()
    return QIcon(pix)


def create_tray_icon(active: bool, sending: bool = False, rotation: float = 0.0) -> QIcon:
    if sending:
        return draw_tray_sending(rotation)
    return draw_tray_ring(TRAY_BLUE if active else TRAY_YELLOW)


def save_config():
    save_json(CONFIG_FILE, config)
    signals.refresh_fields.emit()


def get_startup_command() -> str:
    script_path = Path(sys.argv[0]).resolve()
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'

    exe = Path(sys.executable).resolve()
    if exe.name.lower() == "python.exe":
        alt = exe.with_name("pythonw.exe")
        if alt.exists():
            exe = alt
    return f'"{exe}" "{script_path}"'


def set_start_with_windows(enabled: bool):
    if winreg is None:
        raise RuntimeError("Registro do Windows indisponível.")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, get_startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


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


def is_valid_webhook(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return (
        lowered.startswith("https://discord.com/api/webhooks/")
        or lowered.startswith("https://discordapp.com/api/webhooks/")
    )


def send_test_message():
    webhook = (config.get("webhook") or "").strip()
    if not webhook:
        return False, "Preencha um webhook antes de testar."

    try:
        res = requests.post(webhook, data={"content": "Texto de teste."}, timeout=12)
        if res.status_code in (200, 204):
            return True, "Teste enviado com sucesso."
        if res.status_code == 404:
            return False, "Webhook não encontrado."
        if res.status_code == 401:
            return False, "Webhook sem autorização."
        return False, f"Falha no teste ({res.status_code})."
    except Exception:
        return False, "Não foi possível testar o webhook."


def finalize_sent_file(path, filename, file_hash, upload_str):
    if config.get("delete_after_send", True):
        send2trash(os.path.abspath(path))
    with file_lock:
        sent_history.append({"file": filename, "hash": file_hash, "date": upload_str})
    save_json(LOG_FILE, sent_history)


def send_file(path):
    webhook = (config.get("webhook") or "").strip()
    if not webhook:
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
        message = render_template_text(template, filename, creation_str, upload_str)
        use_embed = bool(config.get("use_embed", False))
        embed_color = normalize_hex_color(config.get("embed_color", DEFAULT_EMBED_COLOR))

        for attempt in range(4):
            try:
                with open(path, "rb") as f:
                    sending_event.set()
                    try:
                        if use_embed:
                            embed_description = message if len(message) <= 4096 else message[:4093] + "..."
                            payload = {
                                "embeds": [
                                    {
                                        "description": embed_description,
                                        "color": discord_color_int(embed_color),
                                    }
                                ]
                            }
                            res = requests.post(
                                webhook,
                                data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                                files={"file": (filename, f)},
                                timeout=15,
                            )
                        else:
                            res = requests.post(
                                webhook,
                                data={"content": message},
                                files={"file": (filename, f)},
                                timeout=15,
                            )
                    finally:
                        sending_event.clear()

                if res.status_code in [200, 204]:
                    finalize_sent_file(path, filename, file_hash, upload_str)
                    return True

                if res.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception:
                sending_event.clear()
                time.sleep(2 ** attempt)

        return False
    except Exception:
        return False


def send_now_manual():
    if not config.get("folder"):
        signals.toast.emit("error", "Selecione uma pasta primeiro.")
        return

    folder = config.get("folder", "")
    if not os.path.isdir(folder):
        signals.toast.emit("error", "A pasta monitorada não existe.")
        return

    if not send_lock.acquire(blocking=False):
        signals.toast.emit("warning", "Já existe um envio em andamento.")
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
                signals.toast.emit("success", f"Enviado: {os.path.basename(file)}")
                for _ in range(POST_INTERVAL):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
        if not sent_any:
            signals.toast.emit("info", "Nenhum arquivo disponível para enviar agora.")
    except Exception:
        traceback.print_exc()
        signals.toast.emit("error", "Falha ao enviar agora.")
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
                            signals.toast.emit("success", f"Enviado automaticamente: {os.path.basename(file)}")
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
    def __init__(self, text, size=36, tooltip="", bg="transparent", hover=HOVER_DARK, fg=TEXT, font_size=15):
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


class ToggleSwitch(QPushButton):
    toggled_visual = Signal(bool)

    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(52, 28)
        self.clicked.connect(lambda: self.toggled_visual.emit(self.isChecked()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = rect.height() / 2
        bg = QColor(BLUE if self.isChecked() else "#363943")
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)

        knob_size = rect.height() - 6
        x = rect.right() - knob_size - 3 if self.isChecked() else rect.left() + 3
        knob_rect = x, rect.top() + 3, knob_size, knob_size
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(*knob_rect)
        painter.end()


class ColorSwatchButton(QPushButton):
    def __init__(self, hex_color=DEFAULT_EMBED_COLOR):
        super().__init__()
        self._hovered = False
        self._color = normalize_hex_color(hex_color)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Cor do embed")
        self.setFixedSize(30, 30)
        self.apply_style()

    def set_color(self, hex_color):
        self._color = normalize_hex_color(hex_color)
        self.apply_style()

    def apply_style(self):
        border = "#ffffff" if self._hovered else "#2f343d"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: {self._color};
                border: 2px solid {border};
                border-radius: 15px;
            }}
            """
        )

    def enterEvent(self, event):
        self._hovered = True
        self.apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.apply_style()
        super().leaveEvent(event)


class ColorSpectrumBox(QWidget):
    colorChanged = Signal(float, float)

    def __init__(self, hue=0.0, sat=1.0, val=1.0, parent=None):
        super().__init__(parent)
        self._hue = max(0.0, min(1.0, hue))
        self._sat = max(0.0, min(1.0, sat))
        self._val = max(0.0, min(1.0, val))
        self.setMinimumSize(250, 180)
        self.setCursor(Qt.CrossCursor)

    def set_hsv(self, hue, sat, val):
        self._hue = max(0.0, min(1.0, hue))
        self._sat = max(0.0, min(1.0, sat))
        self._val = max(0.0, min(1.0, val))
        self.update()

    def set_hue(self, hue):
        self._hue = max(0.0, min(1.0, hue))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.setClipPath(path)

        hue_color = QColor.fromHsvF(self._hue, 1.0, 1.0)
        painter.fillRect(rect, hue_color)

        white_grad = QLinearGradient(rect.topLeft(), rect.topRight())
        white_grad.setColorAt(0.0, QColor(255, 255, 255, 255))
        white_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, white_grad)

        black_grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        black_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        black_grad.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, black_grad)

        painter.setClipping(False)
        pen = QPen(QColor('#2f343d'))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        px = rect.left() + self._sat * rect.width()
        py = rect.top() + (1.0 - self._val) * rect.height()
        painter.setPen(QPen(QColor('#ffffff'), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(px) - 6, int(py) - 6, 12, 12)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._update_from_pos(event.position())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._update_from_pos(event.position())
        super().mouseMoveEvent(event)

    def _update_from_pos(self, pos):
        rect = self.rect().adjusted(1, 1, -1, -1)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        x = max(rect.left(), min(rect.right(), pos.x()))
        y = max(rect.top(), min(rect.bottom(), pos.y()))
        self._sat = (x - rect.left()) / max(1, rect.width())
        self._val = 1.0 - ((y - rect.top()) / max(1, rect.height()))
        self.update()
        self.colorChanged.emit(self._sat, self._val)


class HueSlider(QWidget):
    hueChanged = Signal(float)

    def __init__(self, hue=0.0, parent=None):
        super().__init__(parent)
        self._hue = max(0.0, min(1.0, hue))
        self.setFixedHeight(18)
        self.setCursor(Qt.PointingHandCursor)

    def set_hue(self, hue):
        self._hue = max(0.0, min(1.0, hue))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        grad = QLinearGradient(rect.topLeft(), rect.topRight())
        stops = [
            (0.0, '#ff0000'),
            (1/6, '#ffff00'),
            (2/6, '#00ff00'),
            (3/6, '#00ffff'),
            (4/6, '#0000ff'),
            (5/6, '#ff00ff'),
            (1.0, '#ff0000'),
        ]
        for pos, color in stops:
            grad.setColorAt(pos, QColor(color))
        painter.fillPath(path, grad)
        painter.setPen(QPen(QColor('#2f343d'), 1))
        painter.drawPath(path)

        x = rect.left() + self._hue * rect.width()
        painter.setPen(QPen(QColor('#ffffff'), 2))
        painter.setBrush(QColor(15, 16, 18))
        painter.drawEllipse(int(x) - 6, rect.center().y() - 6, 12, 12)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._update_from_pos(event.position().x())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._update_from_pos(event.position().x())
        super().mouseMoveEvent(event)

    def _update_from_pos(self, x):
        rect = self.rect().adjusted(1, 1, -1, -1)
        if rect.width() <= 0:
            return
        x = max(rect.left(), min(rect.right(), x))
        self._hue = (x - rect.left()) / max(1, rect.width())
        self.update()
        self.hueChanged.emit(self._hue)


class EmbedColorDialog(QDialog):
    def __init__(self, initial_hex=DEFAULT_EMBED_COLOR, parent=None):
        super().__init__(parent)
        self.selected_hex = normalize_hex_color(initial_hex)
        self.setModal(True)
        self.setWindowTitle('Cor do embed')
        self.resize(520, 430)
        self.setStyleSheet(
            f"""
            QDialog {{
                background: {PANEL};
                color: {TEXT};
            }}
            QLabel {{
                color: {TEXT};
                font: 600 10px 'Segoe UI';
            }}
            QLineEdit {{
                background: {FIELD_BG};
                color: {FIELD_TEXT};
                border: 1px solid #2c3038;
                border-radius: 12px;
                padding: 0 12px;
                min-height: 32px;
                font: 700 10px 'Segoe UI';
            }}
            QLineEdit:focus {{
                border: 1px solid {BLUE};
            }}
            QPushButton {{
                background: #24272d;
                color: {TEXT};
                border: 1px solid #30343d;
                border-radius: 12px;
                padding: 7px 12px;
                font: 700 10px 'Segoe UI';
            }}
            QPushButton:hover {{
                background: #2b3038;
            }}
            QColorDialog {{
                background: {PANEL};
            }}
            QColorDialog QWidget {{
                background: {PANEL};
                color: {TEXT};
                font: 600 10px 'Segoe UI';
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.picker = QColorDialog(QColor(self.selected_hex), self)
        self.picker.setOption(QColorDialog.DontUseNativeDialog, True)
        self.picker.setOption(QColorDialog.NoButtons, True)
        self.picker.setOption(QColorDialog.ShowAlphaChannel, False)
        self.picker.setCurrentColor(QColor(self.selected_hex))
        self.picker.currentColorChanged.connect(self.on_picker_color_changed)
        self.picker.setMinimumHeight(300)
        root.addWidget(self.picker, 1)

        info = QLabel('Hex')
        root.addWidget(info)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)

        self.preview = QLabel()
        self.preview.setFixedSize(28, 28)
        controls.addWidget(self.preview)

        self.hex_input = QLineEdit(self.selected_hex)
        self.hex_input.setMaxLength(7)
        self.hex_input.editingFinished.connect(self.apply_hex_input)
        controls.addWidget(self.hex_input, 1)

        self.cancel_btn = QPushButton('Cancelar')
        self.cancel_btn.clicked.connect(self.reject)
        controls.addWidget(self.cancel_btn)

        self.apply_btn = QPushButton('Aplicar')
        self.apply_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {BLUE};
                color: white;
                border: none;
                border-radius: 12px;
                padding: 7px 14px;
                font: 700 10px 'Segoe UI';
            }}
            QPushButton:hover {{
                background: #69adff;
            }}
            """
        )
        self.apply_btn.clicked.connect(self.accept_current)
        controls.addWidget(self.apply_btn)

        root.addLayout(controls)
        self.update_preview(self.selected_hex)

    def update_preview(self, hex_color):
        self.preview.setStyleSheet(
            f'background:{hex_color}; border:2px solid #2f343d; border-radius:14px;'
        )

    def on_picker_color_changed(self, color):
        if not color.isValid():
            return
        self.selected_hex = color.name().upper()
        self.hex_input.setText(self.selected_hex)
        self.update_preview(self.selected_hex)

    def apply_hex_input(self):
        parsed = parse_hex_color(self.hex_input.text())
        if not parsed:
            self.hex_input.setText(self.selected_hex)
            return
        self.selected_hex = parsed
        self.picker.setCurrentColor(QColor(parsed))
        self.hex_input.setText(parsed)
        self.update_preview(parsed)

    def accept_current(self):
        self.apply_hex_input()
        self.accept()


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


class PageBase(QWidget):
    def __init__(self, title, subtitle):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        top = QVBoxLayout()
        top.setSpacing(1)

        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{BLUE}; font: 700 14px 'Segoe UI';")
        top.addWidget(self.title)

        self.subtitle = QLabel(subtitle)
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(f"color:{MUTED}; font: 500 10px 'Segoe UI';")
        top.addWidget(self.subtitle)

        root.addLayout(top)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        root.addLayout(self.body, 1)


class HomeValueRow(QFrame):
    def __init__(self, window, title, button_text, handler):
        super().__init__()
        self.setStyleSheet(
            f"""
            QFrame {{
                background: {CARD};
                border: 1px solid {CARD_BORDER};
                border-radius: 16px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 10, 10)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color:{TEXT}; font: 700 11px 'Segoe UI';")
        left.addWidget(self.title_label)

        self.value_label = QLabel("")
        self.value_label.setWordWrap(False)
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value_label.setStyleSheet(f"color:{FIELD_TEXT}; font: 500 10px 'Segoe UI'; background: transparent; border: none;")
        self.value_label.setMinimumHeight(18)
        left.addWidget(self.value_label)

        root.addLayout(left, 1)

        self.button = window.make_small_button(button_text, handler)
        self.button.setFixedSize(78, 28)
        root.addWidget(self.button, 0, Qt.AlignVCenter)

    def set_value(self, text, placeholder):
        value = (text or '').strip()
        self.value_label.setText(value if value else placeholder)
        self.value_label.setStyleSheet(
            f"color:{FIELD_TEXT if value else '#6f7580'}; font: 500 10px 'Segoe UI'; background: transparent; border: none;"
        )


class HomePage(PageBase):
    def __init__(self, window):
        super().__init__(f"Webhook Uploader v{APP_VERSION}", "Monitoramento simples, visual refinado e tudo dentro da mesma interface.")
        self.window = window

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)

        left = QVBoxLayout()
        left.setSpacing(1)
        left.addWidget(self.title)
        left.addWidget(self.subtitle)
        header.addLayout(left, 1)

        self.cfg_btn = HoverButton("⚙", size=18, tooltip="Configurações", bg="transparent", hover="#1d2025", fg="#6f7580", font_size=8)
        self.cfg_btn.clicked.connect(self.window.open_settings_page)
        header.addWidget(self.cfg_btn, 0, Qt.AlignTop | Qt.AlignRight)

        self.layout().insertLayout(0, header)
        self.layout().removeItem(self.layout().itemAt(1))

        self.webhook_row = HomeValueRow(self.window, "Webhook", "Editar", self.window.open_webhook_page)
        self.body.addWidget(self.webhook_row)

        self.folder_row = HomeValueRow(self.window, "Watched Folder", "Editar", self.window.open_folder_page)
        self.body.addWidget(self.folder_row)

        self.body.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.setSpacing(8)

        self.pause_btn = self.window.make_small_button("Rodando", self.window.toggle_monitoring, accent=BLUE)
        self.pause_btn.setFixedSize(102, 30)
        bottom.addWidget(self.pause_btn)

        self.send_now_btn = self.window.make_small_button("Enviar agora", self.window.start_send_now, accent=BLUE)
        self.send_now_btn.setFixedSize(102, 30)
        bottom.addWidget(self.send_now_btn)

        self.close_btn = self.window.make_secondary_button("Esconder", self.window.hide_to_tray)
        self.close_btn.setFixedSize(102, 30)
        bottom.addWidget(self.close_btn)

        self.body.addLayout(bottom)

    def refresh(self):
        self.webhook_row.set_value(config.get("webhook", ""), "Nenhum webhook configurado")
        self.folder_row.set_value(config.get("folder", ""), "Nenhuma pasta selecionada")
        self.update_pause_visual()

    def update_pause_visual(self):
        if monitoring:
            self.pause_btn.setText("Rodando")
            self.pause_btn.setStyleSheet(self.window.small_button_style(enabled=True, accent=BLUE))
            self.pause_btn.setToolTip("Pausar")
        else:
            self.pause_btn.setText("Pausado")
            self.pause_btn.setStyleSheet(self.window.small_button_style(enabled=True, accent=YELLOW, hover="#ffca52", text_color="#1e1a10"))
            self.pause_btn.setToolTip("Retomar")


class WebhookPage(PageBase):
    def __init__(self, window):
        super().__init__("Editar webhook", "Digite ou cole a URL completa do webhook do Discord.")
        self.window = window
        self.body.addSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.input.setMinimumHeight(38)
        self.input.setStyleSheet(self.window.input_style())
        self.body.addWidget(self.input)

        buttons = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Voltar", self.window.go_home)
        self.save_btn = self.window.make_primary_button("Salvar", self.save)
        buttons.addWidget(self.back_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        self.body.addLayout(buttons)
        self.body.addStretch(1)

    def refresh(self):
        self.input.setText(config.get("webhook", ""))
        self.input.setFocus()
        self.input.selectAll()

    def save(self):
        text = self.input.text().strip()
        if not is_valid_webhook(text):
            self.window.show_message("error", "Cole uma URL válida de webhook.")
            return
        config["webhook"] = text
        save_config()
        self.window.show_message("success", "Webhook atualizado.")
        self.window.go_home()


class FolderPage(PageBase):
    def __init__(self, window):
        super().__init__("Editar pasta monitorada", "Escolha a pasta pelo navegador do Windows para evitar digitar o caminho manualmente.")
        self.window = window
        self.body.addSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.input = QLineEdit()
        self.input.setPlaceholderText(r"Nenhuma pasta selecionada")
        self.input.setMinimumHeight(38)
        self.input.setReadOnly(True)
        self.input.setStyleSheet(self.window.input_style())
        row.addWidget(self.input, 1)

        self.browse_btn = self.window.make_secondary_button("Procurar", self.browse_folder)
        self.browse_btn.setMinimumHeight(38)
        row.addWidget(self.browse_btn)

        self.body.addLayout(row)

        buttons = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Voltar", self.window.go_home)
        self.save_btn = self.window.make_primary_button("Salvar", self.save)
        buttons.addWidget(self.back_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        self.body.addLayout(buttons)
        self.body.addStretch(1)

    def refresh(self):
        self.input.setText(config.get("folder", ""))

    def browse_folder(self):
        current = config.get("folder", "") or str(Path.home())
        selected = QFileDialog.getExistingDirectory(
            self.window,
            "Selecionar pasta monitorada",
            current,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if selected:
            self.input.setText(selected)

    def save(self):
        text = self.input.text().strip().strip('"')
        if not text:
            self.window.show_message("error", "Selecione uma pasta válida.")
            return
        path = Path(text)
        if not path.exists() or not path.is_dir():
            self.window.show_message("error", "A pasta selecionada não existe.")
            return
        config["folder"] = str(path)
        save_config()
        self.window.show_message("success", "Pasta monitorada atualizada.")
        self.window.go_home()


class PostTemplatePage(PageBase):
    def __init__(self, window):
        super().__init__("Personalizar post", "Edite o conteúdo bruto que será enviado junto com o arquivo no Discord.")
        self.window = window
        self.body.addSpacing(6)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Digite aqui o conteúdo do post...")
        self.editor.setStyleSheet(self.window.text_edit_style())
        self.body.addWidget(self.editor, 1)

        self.help_label = QLabel("Variáveis: {filename}  •  {creation_str}  •  {upload_str}")
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet(f"color:{MUTED}; font: 500 9px 'Segoe UI';")
        self.body.addWidget(self.help_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self.back_btn = self.window.make_secondary_button("← Voltar", self.back_to_settings)
        buttons.addWidget(self.back_btn)

        self.test_btn = self.window.make_small_button("Testar webhook", self.test_webhook)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)

        self.color_btn = ColorSwatchButton(config.get("embed_color", DEFAULT_EMBED_COLOR))
        self.color_btn.clicked.connect(self.open_embed_color_dialog)
        buttons.addWidget(self.color_btn, 0, Qt.AlignVCenter)

        self.embed_label = QLabel("Embed")
        self.embed_label.setStyleSheet(f"color:{TEXT}; font: 700 10px 'Segoe UI';")
        buttons.addWidget(self.embed_label, 0, Qt.AlignVCenter)

        self.embed_toggle = ToggleSwitch(config.get("use_embed", False))
        self.embed_toggle.clicked.connect(self.toggle_embed)
        buttons.addWidget(self.embed_toggle, 0, Qt.AlignVCenter)

        self.body.addLayout(buttons)

    def refresh(self):
        self.editor.setPlainText(load_template())
        self.embed_toggle.setChecked(bool(config.get("use_embed", False)))
        self.color_btn.set_color(config.get("embed_color", DEFAULT_EMBED_COLOR))
        has_webhook = bool((config.get("webhook") or "").strip())
        self.test_btn.setEnabled(has_webhook)
        self.test_btn.setStyleSheet(self.window.small_button_style(enabled=has_webhook, accent=BLUE))
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def toggle_embed(self):
        config["use_embed"] = self.embed_toggle.isChecked()
        save_config()

    def open_embed_color_dialog(self):
        dialog = EmbedColorDialog(config.get("embed_color", DEFAULT_EMBED_COLOR), self.window)
        if dialog.exec() == QDialog.Accepted:
            config["embed_color"] = normalize_hex_color(dialog.selected_hex)
            save_config()
            self.color_btn.set_color(config["embed_color"])

    def test_webhook(self):
        ok, msg = send_test_message()
        self.window.show_message("success" if ok else "error", msg)

    def save_template(self, show_feedback=False):
        text = self.editor.toPlainText().replace("\r\n", "\n")
        save_template(text)
        if show_feedback:
            self.window.show_message("success", "post.txt salvo automaticamente.")

    def back_to_settings(self):
        self.save_template(show_feedback=True)
        self.window.open_settings_page()


class SettingRow(QFrame):
    def __init__(self, title, subtitle, right_widget):
        super().__init__()
        self.setObjectName("settingRow")
        self.setStyleSheet(
            f"""
            QFrame#settingRow {{
                background: {CARD};
                border: 1px solid {CARD_BORDER};
                border-radius: 16px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)

        t = QLabel(title)
        t.setStyleSheet(f"color:{TEXT}; font: 700 11px 'Segoe UI';")
        left.addWidget(t)

        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setStyleSheet(f"color:{MUTED}; font: 500 10px 'Segoe UI';")
        left.addWidget(s)

        root.addLayout(left, 1)
        root.addWidget(right_widget, 0, Qt.AlignVCenter)


class SettingsPage(PageBase):
    def __init__(self, window):
        super().__init__("Configurações", "Tudo é salvo imediatamente ao modificar cada opção.")
        self.window = window

        back_row = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Voltar", self.window.go_home)
        back_row.addWidget(self.back_btn)
        back_row.addStretch(1)
        self.body.addLayout(back_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollBar:vertical { background: transparent; width: 8px; } QScrollBar::handle:vertical { background: #2a2d34; border-radius: 4px; min-height: 24px; } QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; } QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")

        self.scroll_host = QWidget()
        self.scroll_host.setStyleSheet("background: transparent;")
        self.scroll_body = QVBoxLayout(self.scroll_host)
        self.scroll_body.setContentsMargins(0, 0, 4, 0)
        self.scroll_body.setSpacing(10)
        self.scroll.setWidget(self.scroll_host)
        self.body.addWidget(self.scroll, 1)

        self.start_toggle = ToggleSwitch(config.get("start_with_windows", False))
        self.start_toggle.clicked.connect(self.toggle_startup)
        self.scroll_body.addWidget(SettingRow("Iniciar com Windows", "Abre oculto na bandeja quando o Windows iniciar.", self.start_toggle))

        self.delete_toggle = ToggleSwitch(config.get("delete_after_send", True))
        self.delete_toggle.clicked.connect(self.toggle_delete_after_send)
        self.scroll_body.addWidget(SettingRow("Excluir após enviar", "Ligado: move para a lixeira. Desligado: mantém o arquivo e evita duplicidade pelo log.", self.delete_toggle))

        post_wrap = QWidget()
        post_wrap.setStyleSheet("background: transparent;")
        post_layout = QHBoxLayout(post_wrap)
        post_layout.setContentsMargins(0, 0, 0, 0)
        self.post_btn = self.window.make_small_button("Editar post", self.window.open_post_template_page)
        post_layout.addWidget(self.post_btn)
        self.scroll_body.addWidget(SettingRow("Personalizar post", "Abre uma página para editar o texto do post e salvar no arquivo post.txt.", post_wrap))

        clear_wrap = QWidget()
        clear_wrap.setStyleSheet("background: transparent;")
        clear_layout = QHBoxLayout(clear_wrap)
        clear_layout.setContentsMargins(0, 0, 0, 0)
        self.clear_log_btn = self.window.make_small_button("Limpar log", self.clear_log, accent=YELLOW)
        clear_layout.addWidget(self.clear_log_btn)
        self.scroll_body.addWidget(SettingRow("Limpar log", "Apaga o histórico de arquivos já enviados e permite novo envio desses arquivos.", clear_wrap))

        open_wrap = QWidget()
        open_wrap.setStyleSheet("background: transparent;")
        open_layout = QHBoxLayout(open_wrap)
        open_layout.setContentsMargins(0, 0, 0, 0)
        self.open_cfg_btn = self.window.make_small_button("Abrir pasta", self.open_config_folder)
        open_layout.addWidget(self.open_cfg_btn)
        self.scroll_body.addWidget(SettingRow("Pasta de configurações", str(BASE_DIR), open_wrap))

        self.version_value = self.window.make_info_value()
        self.scroll_body.addWidget(SettingRow("Versão do app", "Versão atual em uso.", self.version_value))
        self.scroll_body.addStretch(1)

    def refresh(self):
        self.start_toggle.setChecked(config.get("start_with_windows", False))
        self.delete_toggle.setChecked(config.get("delete_after_send", True))
        self.version_value.setText(APP_VERSION)

    def toggle_startup(self):
        enabled = self.start_toggle.isChecked()
        try:
            set_start_with_windows(enabled)
            config["start_with_windows"] = enabled
            save_config()
            self.window.show_message("success", "Inicialização com Windows atualizada.")
        except Exception:
            self.start_toggle.setChecked(not enabled)
            self.window.show_message("error", "Não foi possível alterar a inicialização com Windows.")

    def toggle_delete_after_send(self):
        config["delete_after_send"] = self.delete_toggle.isChecked()
        save_config()
        self.window.show_message("success", "Opção de exclusão atualizada.")

    def clear_log(self):
        clear_sent_log()
        self.window.show_message("success", "Log de envio limpo.")

    def open_config_folder(self):
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(BASE_DIR))
            self.window.show_message("info", "Pasta raiz do Webhook-Uploader aberta.")
        except Exception:
            self.window.show_message("error", "Não foi possível abrir a pasta raiz do Webhook-Uploader.")




def clear_sent_log():
    global sent_history
    with file_lock:
        sent_history = []
    save_json(LOG_FILE, sent_history)


class MainWindow(QWidget):
    def __init__(self, tray_icon):
        super().__init__()
        self.tray_icon = tray_icon
        self.drag_pos = None
        self.anim = None
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(560, 320)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.panel = RoundedPanel()
        outer.addWidget(self.panel)

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.message_label = QLabel("")
        self.message_label.setMinimumHeight(16)
        self.message_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.message_label.setStyleSheet(f"color:{MUTED}; font: 600 10px 'Segoe UI';")
        root.addWidget(self.message_label)

        self.home_page = HomePage(self)
        self.webhook_page = WebhookPage(self)
        self.folder_page = FolderPage(self)
        self.settings_page = SettingsPage(self)
        self.post_template_page = PostTemplatePage(self)

        for page in [self.home_page, self.webhook_page, self.folder_page, self.settings_page, self.post_template_page]:
            self.stack.addWidget(page)

        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self.clear_message)

        signals.status_changed.connect(self.on_status_changed)
        signals.toast.connect(self.show_message)
        signals.refresh_fields.connect(self.refresh_all)

        self.refresh_all()
        self.go_home(animated=False)

    def input_style(self):
        return f"""
        QLineEdit {{
            background: {FIELD_BG};
            color: {FIELD_TEXT};
            border: 1px solid #2c3038;
            border-radius: 16px;
            padding: 0 14px;
            font: 600 10px 'Segoe UI';
        }}
        QLineEdit:focus {{ border: 1px solid {BLUE}; }}
        QLineEdit::placeholder {{ color: #6f7580; }}
        """

    def text_edit_style(self):
        return f"""
        QTextEdit {{
            background: {FIELD_BG};
            color: {FIELD_TEXT};
            border: 1px solid #2c3038;
            border-radius: 18px;
            padding: 10px 12px;
            font: 600 10px 'Segoe UI';
        }}
        QTextEdit:focus {{ border: 1px solid {BLUE}; }}
        """

    def make_primary_button(self, text, handler):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(handler)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {BLUE};
                color: white;
                border: none;
                border-radius: 13px;
                padding: 7px 14px;
                font: 700 10px 'Segoe UI';
            }}
            QPushButton:hover {{ background: #69adff; }}
            """
        )
        return btn

    def make_secondary_button(self, text, handler):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(handler)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: #24272d;
                color: {TEXT};
                border: 1px solid #30343d;
                border-radius: 13px;
                padding: 7px 12px;
                font: 700 10px 'Segoe UI';
            }}
            QPushButton:hover {{ background: #2b3038; }}
            """
        )
        return btn

    def small_button_style(self, enabled=True, accent=BLUE, hover=None, text_color=None):
        if enabled:
            bg = accent
            fg = text_color or "#ffffff"
            if hover is None:
                if accent == BLUE:
                    hover = "#69adff"
                elif accent == YELLOW:
                    hover = "#ffca52"
                else:
                    hover = accent
        else:
            bg = "#2d3138"
            fg = "#6e7480"
            hover = "#2d3138"
        return f"""
        QPushButton {{
            background: {bg};
            color: {fg};
            border: none;
            border-radius: 12px;
            padding: 7px 12px;
            font: 700 10px 'Segoe UI';
        }}
        QPushButton:hover {{ background: {hover}; }}
        """

    def make_small_button(self, text, handler, accent=BLUE):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(handler)
        btn.setMinimumHeight(28)
        btn.setMinimumWidth(74)
        btn.setStyleSheet(self.small_button_style(True, accent=accent))
        return btn

    def make_info_value(self):
        label = QLabel("")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(f"color:{TEXT}; font: 600 10px 'Segoe UI'; background: transparent; border: none;")
        return label

    def refresh_all(self):
        self.home_page.refresh()
        self.settings_page.refresh()

    def save_post_template_if_needed(self, show_feedback=False):
        if self.stack.currentWidget() is self.post_template_page:
            self.post_template_page.save_template(show_feedback=show_feedback)

    def switch_page(self, page, animated=True):
        current = self.stack.currentWidget()
        if current is self.post_template_page and page is not self.post_template_page:
            self.post_template_page.save_template()
        page.refresh()
        self.stack.setCurrentWidget(page)
        if animated:
            effect = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(effect)
            self.anim = QPropertyAnimation(effect, b"opacity", self)
            self.anim.setDuration(170)
            self.anim.setStartValue(0.35)
            self.anim.setEndValue(1.0)
            self.anim.setEasingCurve(QEasingCurve.OutCubic)
            self.anim.start()
            self.anim.finished.connect(lambda: page.setGraphicsEffect(None))

    def go_home(self, animated=True):
        self.switch_page(self.home_page, animated)

    def open_webhook_page(self):
        self.switch_page(self.webhook_page)

    def open_folder_page(self):
        self.switch_page(self.folder_page)

    def open_settings_page(self):
        self.switch_page(self.settings_page)

    def open_post_template_page(self):
        self.switch_page(self.post_template_page)

    def show_message(self, kind, text):
        colors = {
            "success": GREEN,
            "error": RED,
            "warning": YELLOW,
            "info": MUTED,
        }
        self.message_label.setStyleSheet(f"color:{colors.get(kind, MUTED)}; font: 700 11px 'Segoe UI';")
        self.message_label.setText(text)
        self.message_timer.start(4200)

    def clear_message(self):
        self.message_label.setText("")

    def start_send_now(self):
        thread = threading.Thread(target=send_now_manual, daemon=True)
        thread.start()

    def toggle_monitoring(self):
        global monitoring
        monitoring = not monitoring
        signals.status_changed.emit(monitoring)

    def on_status_changed(self, active):
        self.home_page.update_pause_visual()

    def toggle_visible(self):
        if self.isVisible():
            self.save_post_template_if_needed()
            self.hide()
        else:
            self.show_near_tray()

    def hide_to_tray(self):
        self.save_post_template_if_needed()
        self.hide()
        self.clear_message()

    def hideEvent(self, event):
        self.save_post_template_if_needed()
        super().hideEvent(event)

    def show_near_tray(self):
        self.refresh_all()
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 20
        y = screen.bottom() - self.height() - 50
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def exit_app(self):
        self.save_post_template_if_needed()
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


class TrayExitBubble(QWidget):
    def __init__(self, on_exit, parent=None):
        super().__init__(parent)
        self.on_exit = on_exit
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.exit_btn = QPushButton("Encerrar")
        self.exit_btn.setCursor(Qt.PointingHandCursor)
        self.exit_btn.clicked.connect(self.handle_exit)
        self.exit_btn.setFixedSize(92, 30)
        self.exit_btn.setStyleSheet(f"""
            QPushButton {{
                background: #24272d;
                color: {TEXT};
                border: none;
                border-radius: 11px;
                font: 700 10px 'Segoe UI';
                padding: 4px 10px;
                text-align: center;
            }}
            QPushButton:hover {{
                background: #2b3038;
            }}
            QPushButton:pressed {{
                background: #20242b;
            }}
        """)
        outer.addWidget(self.exit_btn)
        self.hide()

    def handle_exit(self):
        self.hide()
        self.on_exit()

    def show_near_cursor(self):
        self.adjustSize()
        pos = QCursor.pos()
        x = max(0, pos.x() - self.width() + 6)
        y = max(0, pos.y() - self.height() - 6)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def focusOutEvent(self, event):
        self.hide()
        super().focusOutEvent(event)


class TrayController(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.tray = QSystemTrayIcon(create_tray_icon(True), app)
        self.tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")
        self.rotation = 0.0
        self._last_static_state = None

        self.window = MainWindow(self.tray)
        self.exit_bubble = TrayExitBubble(self.exit_app)
        self.tray.activated.connect(self.on_tray_activated)
        signals.status_changed.connect(self.sync_pause_action)

        self.tray_timer = QTimer(self)
        self.tray_timer.setInterval(80)
        self.tray_timer.timeout.connect(self.refresh_tray_icon)
        self.tray_timer.start()

        self.sync_pause_action(monitoring)
        self.tray.show()

    def start_send_now(self):
        thread = threading.Thread(target=send_now_manual, daemon=True)
        thread.start()

    def toggle_monitoring(self):
        global monitoring
        monitoring = not monitoring
        signals.status_changed.emit(monitoring)

    def open_settings(self):
        self.window.open_settings_page()
        self.window.show_near_tray()

    def refresh_tray_icon(self, force=False):
        sending = sending_event.is_set()
        active = monitoring

        if sending:
            self.rotation += 0.35
            self.tray.setIcon(create_tray_icon(active, sending=True, rotation=self.rotation))
            self._last_static_state = None
            return

        state_key = "normal" if active else "paused"
        if force or self._last_static_state != state_key:
            self.tray.setIcon(create_tray_icon(active, sending=False))
            self._last_static_state = state_key

    def sync_pause_action(self, active):
        self.window.home_page.update_pause_visual()
        self.refresh_tray_icon(force=True)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Context:
            self.exit_bubble.show_near_cursor()
            return
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self.exit_bubble.hide()
            self.window.toggle_visible()

    def exit_app(self):
        self.window.save_post_template_if_needed()
        stop_event.set()
        self.window.hide()
        self.tray.hide()
        QApplication.quit()


def ensure_first_run(window: MainWindow):
    if not config.get("webhook"):
        window.open_webhook_page()
        window.show_near_tray()
        return
    if not config.get("folder"):
        window.open_folder_page()
        window.show_near_tray()
        return


if __name__ == "__main__":
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    save_config()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = TrayController(app)
    ensure_first_run(controller.window)

    worker = threading.Thread(target=monitoring_loop, daemon=True)
    worker.start()

    sys.exit(app.exec())
