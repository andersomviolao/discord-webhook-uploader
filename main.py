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

try:
    import ctypes
except Exception:
    ctypes = None

from send2trash import send2trash
from PySide6.QtCore import Qt, Signal, QObject, QEasingCurve, QPropertyAnimation, QTimer, QRect, QSize
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
    QSizePolicy,
    )

try:
    import winreg
except Exception:
    winreg = None

APP_NAME = "Discord Webhook Uploader"
APP_DIR_NAME = "discord-webhook-uploader"
APP_VERSION = "3.0.6"
WINDOW_WIDTH = 560
WINDOW_HEIGHT = 320
BASE_DIR = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / APP_DIR_NAME
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "sent_log.json"
DEBUG_FILE = BASE_DIR / "debug.json"
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

DEFAULT_WAIT_TIME = 3600
DEFAULT_POST_INTERVAL = 10
MONITOR_CHECK_INTERVAL = 5
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

file_lock = threading.RLock()
debug_lock = threading.RLock()
send_lock = threading.Lock()
sending_event = threading.Event()
monitoring = True
stop_event = threading.Event()


DEBUG_SESSION_STARTED_AT = datetime.datetime.now().isoformat(timespec="seconds")
debug_events = []


def _debug_enum_value(value):
    try:
        enum_name = value.name
    except Exception:
        enum_name = None
    try:
        enum_value = int(value.value)
    except Exception:
        try:
            enum_value = int(value)
        except Exception:
            enum_value = None
    if enum_name is not None and enum_value is not None:
        return {"name": str(enum_name), "value": enum_value}
    if enum_name is not None:
        return str(enum_name)
    if enum_value is not None:
        return enum_value
    return str(value)


def _safe_debug_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _safe_debug_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_debug_value(v) for v in value]
    if hasattr(value, "name") or hasattr(value, "value"):
        return _debug_enum_value(value)
    return str(value)




def set_window_pos_safely(widget, *, x=None, y=None, width=None, height=None, move=True, resize=True):
    if not move and not resize:
        return

    if x is None:
        x = widget.x()
    if y is None:
        y = widget.y()
    if width is None:
        width = widget.width()
    if height is None:
        height = widget.height()

    if sys.platform.startswith("win") and ctypes is not None:
        try:
            hwnd = int(widget.winId())
            flags = 0x0004 | 0x0010
            if not move:
                flags |= 0x0002
            if not resize:
                flags |= 0x0001
            ctypes.windll.user32.SetWindowPos(hwnd, 0, int(x), int(y), int(width), int(height), flags)
            return
        except Exception as exc:
            debug_log("set_window_pos_safely_fallback", error=str(exc), move=move, resize=resize, x=x, y=y, width=width, height=height)

    if move and resize:
        widget.setGeometry(int(x), int(y), int(width), int(height))
    elif move:
        widget.move(int(x), int(y))
    elif resize:
        widget.resize(int(width), int(height))


def enforce_fixed_window_size(widget, *, width=WINDOW_WIDTH, height=WINDOW_HEIGHT):
    widget.setMinimumSize(width, height)
    widget.setMaximumSize(width, height)
    widget.setBaseSize(width, height)
    if widget.width() != width or widget.height() != height:
        set_window_pos_safely(widget, width=width, height=height, move=False, resize=True)

def debug_enabled() -> bool:
    return bool(globals().get("config", {}).get("debug_mode", False))


def debug_snapshot():
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "session_started_at": DEBUG_SESSION_STARTED_AT,
        "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "debug_mode": debug_enabled(),
        "events": debug_events,
    }


def write_debug_file():
    with debug_lock:
        DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_FILE, "w", encoding="utf-8") as f:
            json.dump(debug_snapshot(), f, indent=4, ensure_ascii=False)


def debug_log(action: str, **details):
    if not debug_enabled():
        return
    entry = {
        "index": len(debug_events) + 1,
        "time": datetime.datetime.now().isoformat(timespec="milliseconds"),
        "action": action,
    }
    if details:
        entry["details"] = _safe_debug_value(details)
    line = f"[DEBUG] {entry['time']} | {action}"
    if details:
        detail_text = ", ".join(f"{key}={_safe_debug_value(value)}" for key, value in details.items())
        line += f" | {detail_text}"
    print(line, flush=True)
    with debug_lock:
        debug_events.append(entry)
    write_debug_file()


def init_debug_session():
    if debug_enabled():
        with debug_lock:
            debug_events.clear()
        write_debug_file()
        debug_log("debug_session_started", base_dir=str(BASE_DIR), config_file=str(CONFIG_FILE))


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


def normalize_multiline_text(value, default: str) -> str:
    text = value if isinstance(value, str) else default
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def normalize_int(value, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, parsed)


def load_template():
    return normalize_multiline_text(config.get("post_template", default_template_text()), default_template_text())


def save_template(text: str):
    config["post_template"] = normalize_multiline_text(text, default_template_text())
    debug_log("save_template")
    save_config()


def get_wait_time_seconds() -> int:
    return normalize_int(config.get("wait_time_seconds", DEFAULT_WAIT_TIME), DEFAULT_WAIT_TIME, minimum=0)


def get_post_interval_seconds() -> int:
    return normalize_int(config.get("post_interval_seconds", DEFAULT_POST_INTERVAL), DEFAULT_POST_INTERVAL, minimum=0)


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
    raw = raw if isinstance(raw, dict) else {}
    return {
        "folder": raw.get("folder", ""),
        "webhook": raw.get("webhook", ""),
        "start_with_windows": bool(raw.get("start_with_windows", False)),
        "delete_after_send": bool(raw.get("delete_after_send", True)),
        "use_embed": bool(raw.get("use_embed", False)),
        "embed_color": normalize_hex_color(raw.get("embed_color", DEFAULT_EMBED_COLOR)),
        "post_template": normalize_multiline_text(raw.get("post_template", default_template_text()), default_template_text()),
        "wait_time_seconds": normalize_int(raw.get("wait_time_seconds", DEFAULT_WAIT_TIME), DEFAULT_WAIT_TIME, minimum=0),
        "post_interval_seconds": normalize_int(raw.get("post_interval_seconds", DEFAULT_POST_INTERVAL), DEFAULT_POST_INTERVAL, minimum=0),
        "debug_mode": bool(raw.get("debug_mode", False)),
    }


config = normalize_config(load_json(CONFIG_FILE, {}))
sent_history = load_json(LOG_FILE, [])
if not isinstance(sent_history, list):
    sent_history = []


class UISignals(QObject):
    status_changed = Signal(bool)
    toast = Signal(str, str)
    refresh_fields = Signal()


signals = UISignals()


class CompactStackedWidget(QStackedWidget):
    def sizeHint(self):
        current = self.currentWidget()
        if current is not None:
            return current.sizeHint()
        return QSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)


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
    global config
    config = normalize_config(config)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    save_json(CONFIG_FILE, config)
    debug_log("config_saved", keys=sorted(config.keys()), debug_mode=config.get("debug_mode", False))
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
    debug_log("set_start_with_windows_requested", enabled=enabled)
    if winreg is None:
        raise RuntimeError("Windows registry is unavailable.")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, get_startup_command())
            debug_log("set_start_with_windows_applied", enabled=True)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                debug_log("set_start_with_windows_missing_registry_value")
                pass
            debug_log("set_start_with_windows_applied", enabled=False)


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


def clip_embed_description(text: str) -> str:
    return text if len(text) <= 4096 else text[:4093] + "..."


def is_embed_image_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def build_message_payload(message: str, use_embed: bool, embed_color: str, filename: str | None = None):
    if use_embed:
        embed = {
            "description": clip_embed_description(message),
            "color": discord_color_int(embed_color),
        }
        if filename and is_embed_image_file(filename):
            embed["image"] = {"url": f"attachment://{filename}"}
        return {"payload_json": json.dumps({"embeds": [embed]}, ensure_ascii=False)}
    return {"content": message}


def build_test_message(template_text: str | None = None) -> str:
    now_dt = datetime.datetime.now()
    now_str = f"{DAYS_OF_WEEK[now_dt.weekday()]}, {now_dt.strftime('%d/%m/%y %H:%M:%S')}"
    template_source = load_template() if template_text is None else template_text
    template = normalize_multiline_text(template_source, default_template_text())
    return render_template_text(template, "example.png", now_str, now_str)


def send_test_message(template_text: str | None = None, use_embed: bool | None = None):
    debug_log("send_test_message_started")
    webhook = (config.get("webhook") or "").strip()
    if not webhook:
        debug_log("send_test_message_blocked", reason="missing_webhook")
        return False, "Enter a webhook before testing."

    message = build_test_message(template_text)
    use_embed = bool(config.get("use_embed", False)) if use_embed is None else bool(use_embed)
    embed_color = normalize_hex_color(config.get("embed_color", DEFAULT_EMBED_COLOR))
    payload = build_message_payload(message, use_embed, embed_color)

    try:
        res = requests.post(webhook, data=payload, timeout=12)
        if res.status_code in (200, 204):
            debug_log("send_test_message_finished", success=True, status_code=res.status_code)
            return True, "Test sent successfully."
        if res.status_code == 404:
            debug_log("send_test_message_finished", success=False, status_code=404)
            return False, "Webhook not found."
        if res.status_code == 401:
            debug_log("send_test_message_finished", success=False, status_code=401)
            return False, "Webhook unauthorized."
        debug_log("send_test_message_finished", success=False, status_code=res.status_code)
        return False, f"Test failed ({res.status_code})."
    except Exception as exc:
        debug_log("send_test_message_exception", error=str(exc))
        return False, "Could not test the webhook."


def finalize_sent_file(path, filename, file_hash, upload_str):
    debug_log("finalize_sent_file_started", path=path, filename=filename)
    if config.get("delete_after_send", True):
        send2trash(os.path.abspath(path))
        debug_log("sent_file_moved_to_trash", path=path)
    with file_lock:
        sent_history.append({"file": filename, "hash": file_hash, "date": upload_str})
    save_json(LOG_FILE, sent_history)
    debug_log("finalize_sent_file_finished", filename=filename, upload_time=upload_str)


def send_file(path):
    debug_log("send_file_started", path=path)
    webhook = (config.get("webhook") or "").strip()
    if not webhook:
        debug_log("send_file_blocked", path=path, reason="missing_webhook")
        return False

    filename = os.path.basename(path)
    watched_folder = config.get("folder", "")
    error_dir = Path(watched_folder) / "fail" if watched_folder else None

    try:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        debug_log("send_file_size_checked", path=path, size_mb=round(size_mb, 3))
        if size_mb > 25:
            if error_dir is not None:
                error_dir.mkdir(exist_ok=True)
                shutil.move(path, error_dir / filename)
                debug_log("send_file_moved_to_fail", path=path, fail_path=str(error_dir / filename), reason="file_too_large")
            return False
    except Exception as exc:
        debug_log("send_file_size_check_failed", path=path, error=str(exc))
        return False

    file_hash = get_file_hash(path)
    if not file_hash:
        debug_log("send_file_blocked", path=path, reason="hash_failed")
        return False

    with file_lock:
        if any(item.get("hash") == file_hash for item in sent_history):
            debug_log("send_file_blocked", path=path, reason="duplicate_hash")
            return False

    if not file_is_free(path):
        debug_log("send_file_blocked", path=path, reason="file_locked")
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
                debug_log("send_file_attempt_started", path=path, filename=filename, attempt=attempt + 1)
                with open(path, "rb") as f:
                    sending_event.set()
                    try:
                        payload = build_message_payload(message, use_embed, embed_color, filename=filename)
                        res = requests.post(
                            webhook,
                            data=payload,
                            files={"file": (filename, f)},
                            timeout=15,
                        )
                    finally:
                        sending_event.clear()

                debug_log("send_file_attempt_finished", path=path, filename=filename, attempt=attempt + 1, status_code=res.status_code)
                if res.status_code in [200, 204]:
                    finalize_sent_file(path, filename, file_hash, upload_str)
                    debug_log("send_file_finished", path=path, filename=filename, success=True)
                    return True

                if res.status_code == 429:
                    debug_log("send_file_rate_limited", path=path, filename=filename, attempt=attempt + 1, wait_seconds=2 ** attempt)
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception as exc:
                sending_event.clear()
                debug_log("send_file_attempt_exception", path=path, filename=filename, attempt=attempt + 1, error=str(exc))
                time.sleep(2 ** attempt)

        debug_log("send_file_finished", path=path, filename=filename, success=False)
        return False
    except Exception as exc:
        debug_log("send_file_exception", path=path, error=str(exc))
        return False


def send_now_manual():
    debug_log("send_now_manual_started")
    if not config.get("folder"):
        debug_log("send_now_manual_blocked", reason="missing_folder")
        signals.toast.emit("error", "Select a folder first.")
        return

    folder = config.get("folder", "")
    if not os.path.isdir(folder):
        debug_log("send_now_manual_blocked", reason="folder_missing_on_disk", folder=folder)
        signals.toast.emit("error", "The watched folder does not exist.")
        return

    if not send_lock.acquire(blocking=False):
        debug_log("send_now_manual_blocked", reason="send_lock_busy")
        signals.toast.emit("warning", "A send operation is already in progress.")
        return

    try:
        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
        ]
        debug_log("send_now_manual_files_scanned", folder=folder, file_count=len(files))
        sent_any = False
        for file in sorted(files, key=os.path.getctime):
            if stop_event.is_set():
                break
            if send_file(file):
                sent_any = True
                signals.toast.emit("success", f"Sent: {os.path.basename(file)}")
                for _ in range(get_post_interval_seconds()):
                    if stop_event.is_set():
                        break
                    time.sleep(1)
        if not sent_any:
            debug_log("send_now_manual_finished", sent_any=False)
            signals.toast.emit("info", "No file is available to send right now.")
        else:
            debug_log("send_now_manual_finished", sent_any=True)
    except Exception as exc:
        traceback.print_exc()
        debug_log("send_now_manual_exception", error=str(exc))
        signals.toast.emit("error", "Send now failed.")
    finally:
        send_lock.release()
        debug_log("send_now_manual_lock_released")
        signals.refresh_fields.emit()


def monitoring_loop():
    global monitoring
    debug_log("monitoring_loop_started")
    while not stop_event.is_set():
        debug_log("monitoring_loop_tick", monitoring=monitoring, folder=config.get("folder", ""), webhook_configured=bool(config.get("webhook")))
        if monitoring and config.get("folder") and config.get("webhook"):
            folder = config.get("folder", "")
            if os.path.isdir(folder) and send_lock.acquire(blocking=False):
                debug_log("monitoring_lock_acquired", folder=folder)
                try:
                    now = time.time()
                    files = [
                        os.path.join(folder, f)
                        for f in os.listdir(folder)
                        if os.path.isfile(os.path.join(folder, f))
                    ]
                    ready = [p for p in files if now - os.path.getctime(p) >= get_wait_time_seconds()]
                    debug_log("monitoring_folder_scanned", folder=folder, file_count=len(files), ready_count=len(ready))
                    for file in sorted(ready, key=os.path.getctime):
                        if stop_event.is_set() or not monitoring:
                            break
                        if send_file(file):
                            signals.toast.emit("success", f"Sent automatically: {os.path.basename(file)}")
                            for _ in range(get_post_interval_seconds()):
                                if stop_event.is_set() or not monitoring:
                                    break
                                time.sleep(1)
                except Exception as exc:
                    traceback.print_exc()
                    debug_log("monitoring_loop_exception", error=str(exc))
                finally:
                    send_lock.release()
                    debug_log("monitoring_lock_released", folder=folder)
        for second in range(MONITOR_CHECK_INTERVAL):
            debug_log("monitoring_sleep_tick", second=second + 1, interval=MONITOR_CHECK_INTERVAL)
            if stop_event.is_set():
                break
            time.sleep(1)
    debug_log("monitoring_loop_stopped")


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
        self.setToolTip("Embed color")
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


class EmbedColorPopup(QWidget):
    colorChanged = Signal(str)
    colorSaved = Signal(str)

    def __init__(self, initial_hex=DEFAULT_EMBED_COLOR, parent=None):
        super().__init__(parent)
        self._closing = False
        self._syncing_hex = False
        self.selected_hex = normalize_hex_color(initial_hex)
        self._hue, self._sat, self._val = self.hex_to_hsv(self.selected_hex)

        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("embedColorPopup")
        panel.setStyleSheet(
            f"""
            QFrame#embedColorPopup {{
                background: {PANEL};
                border: 1px solid #23262d;
                border-radius: 18px;
            }}
            QLabel {{
                color: {TEXT};
                font: 700 10px 'Segoe UI';
                background: transparent;
                border: none;
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
            QLineEdit::placeholder {{
                color: #6f7580;
            }}
            """
        )
        outer.addWidget(panel)

        root = QVBoxLayout(panel)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.spectrum = ColorSpectrumBox(self._hue, self._sat, self._val)
        self.spectrum.setMinimumSize(238, 156)
        self.spectrum.colorChanged.connect(self.on_sv_changed)
        root.addWidget(self.spectrum)

        self.hue_slider = HueSlider(self._hue)
        self.hue_slider.hueChanged.connect(self.on_hue_changed)
        root.addWidget(self.hue_slider)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(10)

        self.preview = QLabel()
        self.preview.setFixedSize(28, 28)
        bottom.addWidget(self.preview, 0, Qt.AlignVCenter)

        hex_col = QVBoxLayout()
        hex_col.setContentsMargins(0, 0, 0, 0)
        hex_col.setSpacing(4)

        self.hex_label = QLabel('Hex')
        self.hex_label.setStyleSheet(f"color:{TEXT}; font: 700 9px 'Segoe UI';")
        hex_col.addWidget(self.hex_label)

        self.hex_input = QLineEdit(self.selected_hex)
        self.hex_input.setPlaceholderText('#F54927')
        self.hex_input.setMaxLength(7)
        self.hex_input.textChanged.connect(self.on_hex_text_changed)
        self.hex_input.editingFinished.connect(self.on_hex_editing_finished)
        hex_col.addWidget(self.hex_input)

        bottom.addLayout(hex_col, 1)
        root.addLayout(bottom)

        self.update_preview(self.selected_hex)

    @staticmethod
    def hex_to_hsv(hex_color):
        color = QColor(normalize_hex_color(hex_color))
        r, g, b, _ = color.getRgbF()
        return colorsys.rgb_to_hsv(r, g, b)

    def show_anchored(self, anchor_widget, boundary_widget=None):
        self.adjustSize()
        anchor_global = anchor_widget.mapToGlobal(anchor_widget.rect().topLeft())
        anchor_rect = QRect(anchor_global, anchor_widget.size())
        screen = anchor_widget.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

        margin = 8
        candidates = [
            (anchor_rect.left() - self.width() + anchor_rect.width(), anchor_rect.top() - self.height() - margin),
            (anchor_rect.left() - self.width() + anchor_rect.width(), anchor_rect.bottom() + margin),
            (anchor_rect.right() + margin, anchor_rect.top()),
            (anchor_rect.left() - self.width() - margin, anchor_rect.top()),
        ]

        def clamp(x, y):
            x = max(area.left() + 6, min(x, area.right() - self.width() - 6))
            y = max(area.top() + 6, min(y, area.bottom() - self.height() - 6))
            return x, y

        x, y = candidates[0]
        for cx, cy in candidates:
            if (area.left() <= cx <= area.right() - self.width()) and (area.top() <= cy <= area.bottom() - self.height()):
                x, y = cx, cy
                break
        x, y = clamp(x, y)

        if boundary_widget and boundary_widget.isVisible():
            parent_global = boundary_widget.mapToGlobal(boundary_widget.rect().topLeft())
            parent_rect = QRect(parent_global, boundary_widget.size())
            x = max(parent_rect.left() + 8, min(x, parent_rect.right() - self.width() - 8))
            y = max(parent_rect.top() + 8, min(y, parent_rect.bottom() - self.height() - 8))

        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()

    def update_preview(self, hex_color):
        self.preview.setStyleSheet(
            f'background:{hex_color}; border:2px solid #2f343d; border-radius:14px;'
        )

    def set_selected_hex(self, hex_color, sync_hsv=True, sync_hex=True, emit_live=True):
        normalized = normalize_hex_color(hex_color)
        self.selected_hex = normalized
        if sync_hsv:
            self._hue, self._sat, self._val = self.hex_to_hsv(normalized)
            self.hue_slider.set_hue(self._hue)
            self.spectrum.set_hsv(self._hue, self._sat, self._val)
        self.update_preview(normalized)
        if sync_hex:
            self._syncing_hex = True
            self.hex_input.setText(normalized)
            self._syncing_hex = False
        if emit_live:
            self.colorChanged.emit(normalized)

    def on_sv_changed(self, sat, val):
        self._sat = sat
        self._val = val
        color = QColor.fromHsvF(self._hue, self._sat, self._val)
        self.set_selected_hex(color.name().upper(), sync_hsv=False, sync_hex=True, emit_live=True)

    def on_hue_changed(self, hue):
        self._hue = hue
        self.spectrum.set_hue(hue)
        color = QColor.fromHsvF(self._hue, self._sat, self._val)
        self.set_selected_hex(color.name().upper(), sync_hsv=False, sync_hex=True, emit_live=True)

    def on_hex_text_changed(self, text):
        if self._syncing_hex:
            return
        parsed = parse_hex_color(text)
        if not parsed:
            return
        self.set_selected_hex(parsed, sync_hsv=True, sync_hex=False, emit_live=True)

    def on_hex_editing_finished(self):
        parsed = parse_hex_color(self.hex_input.text())
        self._syncing_hex = True
        self.hex_input.setText(parsed or self.selected_hex)
        self._syncing_hex = False

    def commit_and_close(self):
        if self._closing:
            return
        self._closing = True
        self.colorSaved.emit(self.selected_hex)
        self.hide()

    def hideEvent(self, event):
        if not self._closing:
            self._closing = True
            self.colorSaved.emit(self.selected_hex)
        super().hideEvent(event)

    def showEvent(self, event):
        self._closing = False
        super().showEvent(event)

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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
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

    def minimumSizeHint(self):
        return QSize(0, 0)


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
        super().__init__(f"{APP_NAME} v{APP_VERSION}", "Simple monitoring, polished visuals, and everything inside the same interface.")
        self.window = window

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)

        left = QVBoxLayout()
        left.setSpacing(1)
        left.addWidget(self.title)
        left.addWidget(self.subtitle)
        header.addLayout(left, 1)

        self.cfg_btn = HoverButton("⚙", size=18, tooltip="Settings", bg="transparent", hover="#1d2025", fg="#6f7580", font_size=8)
        self.cfg_btn.clicked.connect(self.window.open_settings_page)
        header.addWidget(self.cfg_btn, 0, Qt.AlignTop | Qt.AlignRight)

        self.layout().insertLayout(0, header)
        self.layout().removeItem(self.layout().itemAt(1))

        self.webhook_row = HomeValueRow(self.window, "Webhook", "Edit", self.window.open_webhook_page)
        self.body.addWidget(self.webhook_row)

        self.folder_row = HomeValueRow(self.window, "Watched Folder", "Edit", self.window.open_folder_page)
        self.body.addWidget(self.folder_row)

        self.body.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.setSpacing(8)

        self.pause_btn = self.window.make_small_button("Running", self.window.toggle_monitoring, accent=BLUE)
        self.pause_btn.setFixedSize(102, 30)
        bottom.addWidget(self.pause_btn)

        self.send_now_btn = self.window.make_small_button("Send Now", self.window.start_send_now, accent=BLUE)
        self.send_now_btn.setFixedSize(102, 30)
        bottom.addWidget(self.send_now_btn)

        self.close_btn = self.window.make_secondary_button("Hide", self.window.hide_to_tray)
        self.close_btn.setFixedSize(102, 30)
        bottom.addWidget(self.close_btn)

        self.body.addLayout(bottom)

    def refresh(self):
        self.webhook_row.set_value(config.get("webhook", ""), "No webhook configured")
        self.folder_row.set_value(config.get("folder", ""), "No folder selected")
        self.update_pause_visual()

    def update_pause_visual(self):
        if monitoring:
            self.pause_btn.setText("Running")
            self.pause_btn.setStyleSheet(self.window.small_button_style(enabled=True, accent=BLUE))
            self.pause_btn.setToolTip("Pause")
        else:
            self.pause_btn.setText("Paused")
            self.pause_btn.setStyleSheet(self.window.small_button_style(enabled=True, accent=YELLOW, hover="#ffca52", text_color="#1e1a10"))
            self.pause_btn.setToolTip("Resume")


class WebhookPage(PageBase):
    def __init__(self, window):
        super().__init__("Edit Webhook", "Type or paste the full Discord webhook URL.")
        self.window = window
        self.body.addSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("https://discord.com/api/webhooks/...")
        self.input.setMinimumHeight(38)
        self.input.setStyleSheet(self.window.input_style())
        self.body.addWidget(self.input)

        buttons = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Back", self.window.go_home)
        self.save_btn = self.window.make_primary_button("Save", self.save)
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
            self.window.show_message("error", "Paste a valid webhook URL.")
            return
        config["webhook"] = text
        save_config()
        self.window.show_message("success", "Webhook updated.")
        self.window.go_home()


class FolderPage(PageBase):
    def __init__(self, window):
        super().__init__("Edit Watched Folder", "Choose the folder through the Windows browser instead of typing the path manually.")
        self.window = window
        self.body.addSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.input = QLineEdit()
        self.input.setPlaceholderText(r"No folder selected")
        self.input.setMinimumHeight(38)
        self.input.setReadOnly(True)
        self.input.setStyleSheet(self.window.input_style())
        row.addWidget(self.input, 1)

        self.browse_btn = self.window.make_secondary_button("Browse", self.browse_folder)
        self.browse_btn.setMinimumHeight(38)
        row.addWidget(self.browse_btn)

        self.body.addLayout(row)

        buttons = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Back", self.window.go_home)
        self.save_btn = self.window.make_primary_button("Save", self.save)
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
            "Select Watched Folder",
            current,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if selected:
            self.input.setText(selected)

    def save(self):
        text = self.input.text().strip().strip('"')
        if not text:
            self.window.show_message("error", "Select a valid folder.")
            return
        path = Path(text)
        if not path.exists() or not path.is_dir():
            self.window.show_message("error", "The selected folder does not exist.")
            return
        config["folder"] = str(path)
        save_config()
        self.window.show_message("success", "Watched folder updated.")
        self.window.go_home()



class PostTemplatePage(PageBase):
    def __init__(self, window):
        super().__init__("Customize Post", "Edit the raw content that will be sent together with the file on Discord.")
        self.window = window
        self._loading = False
        self.body.addSpacing(6)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText("Type the post content here...")
        self.editor.setStyleSheet(self.window.text_edit_style())
        self.editor.textChanged.connect(self.on_editor_text_changed)
        self.body.addWidget(self.editor, 1)

        self.help_label = QLabel("Variables: {filename}  •  {creation_str}  •  {upload_str}")
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet(f"color:{MUTED}; font: 500 9px 'Segoe UI';")
        self.body.addWidget(self.help_label)


        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self.back_btn = self.window.make_secondary_button("← Back", self.back_to_settings)
        buttons.addWidget(self.back_btn)

        self.test_btn = self.window.make_small_button("Test Webhook", self.test_webhook)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)

        self.color_popup = None

        self.color_btn = ColorSwatchButton(config.get("embed_color", DEFAULT_EMBED_COLOR))
        self.color_btn.clicked.connect(self.toggle_embed_color_popup)
        buttons.addWidget(self.color_btn, 0, Qt.AlignVCenter)

        self.embed_label = QLabel("Embed")
        self.embed_label.setStyleSheet(f"color:{TEXT}; font: 700 10px 'Segoe UI';")
        buttons.addWidget(self.embed_label, 0, Qt.AlignVCenter)

        self.embed_toggle = ToggleSwitch(config.get("use_embed", False))
        self.embed_toggle.clicked.connect(self.toggle_embed)
        buttons.addWidget(self.embed_toggle, 0, Qt.AlignVCenter)

        self.body.addLayout(buttons)

    def refresh(self):
        self._loading = True
        self.editor.setPlainText(load_template())
        self.embed_toggle.setChecked(bool(config.get("use_embed", False)))
        self.color_btn.set_color(config.get("embed_color", DEFAULT_EMBED_COLOR))
        if self.color_popup is not None and self.color_popup.isVisible():
            self.color_popup.set_selected_hex(config.get("embed_color", DEFAULT_EMBED_COLOR), sync_hsv=True, sync_hex=True, emit_live=False)
        has_webhook = bool((config.get("webhook") or "").strip())
        self.test_btn.setEnabled(has_webhook)
        self.test_btn.setStyleSheet(self.window.small_button_style(enabled=has_webhook, accent=BLUE))
        self._loading = False
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)


    def on_editor_text_changed(self):
        if self._loading:
            return

    def toggle_embed(self):
        config["use_embed"] = self.embed_toggle.isChecked()
        save_config()

    def ensure_color_popup(self):
        if self.color_popup is None:
            self.color_popup = EmbedColorPopup(config.get("embed_color", DEFAULT_EMBED_COLOR), self.window)
            self.color_popup.colorChanged.connect(self.on_embed_color_live_changed)
            self.color_popup.colorSaved.connect(self.on_embed_color_saved)
        return self.color_popup

    def toggle_embed_color_popup(self):
        popup = self.ensure_color_popup()
        if popup.isVisible():
            popup.commit_and_close()
            return
        popup.set_selected_hex(config.get("embed_color", DEFAULT_EMBED_COLOR), sync_hsv=True, sync_hex=True, emit_live=False)
        popup.show_anchored(self.color_btn, self.window)

    def on_embed_color_live_changed(self, hex_color):
        self.color_btn.set_color(hex_color)

    def on_embed_color_saved(self, hex_color):
        normalized = normalize_hex_color(hex_color)
        config["embed_color"] = normalized
        save_config()
        self.color_btn.set_color(normalized)

    def test_webhook(self):
        ok, msg = send_test_message(self.editor.toPlainText(), self.embed_toggle.isChecked())
        self.window.show_message("success" if ok else "error", msg)

    def save_template(self, show_feedback=False):
        text = self.editor.toPlainText().replace("\r\n", "\n")
        save_template(text)
        if show_feedback:
            self.window.show_message("success", "Post saved to config.json.")

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
        super().__init__("Settings", "Everything is saved immediately whenever you change an option.")
        self.window = window

        back_row = QHBoxLayout()
        self.back_btn = self.window.make_secondary_button("← Back", self.window.go_home)
        back_row.addWidget(self.back_btn)
        back_row.addStretch(1)
        self.body.addLayout(back_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(self.window.scrollbar_style("QScrollArea"))

        self.scroll_host = QWidget()
        self.scroll_host.setStyleSheet("background: transparent;")
        self.scroll_body = QVBoxLayout(self.scroll_host)
        self.scroll_body.setContentsMargins(0, 0, 4, 0)
        self.scroll_body.setSpacing(10)
        self.scroll.setWidget(self.scroll_host)
        self.body.addWidget(self.scroll, 1)

        self.start_toggle = ToggleSwitch(config.get("start_with_windows", False))
        self.start_toggle.clicked.connect(self.toggle_startup)
        self.scroll_body.addWidget(SettingRow("Start with Windows", "Starts hidden in the system tray when Windows launches.", self.start_toggle))

        self.delete_toggle = ToggleSwitch(config.get("delete_after_send", True))
        self.delete_toggle.clicked.connect(self.toggle_delete_after_send)
        self.scroll_body.addWidget(SettingRow("Delete after send", "On: moves the file to the Recycle Bin. Off: keeps the file and avoids duplicates through the log.", self.delete_toggle))

        post_wrap = QWidget()
        post_wrap.setStyleSheet("background: transparent;")
        post_layout = QHBoxLayout(post_wrap)
        post_layout.setContentsMargins(0, 0, 0, 0)
        self.post_btn = self.window.make_small_button("Edit Post", self.window.open_post_template_page)
        post_layout.addWidget(self.post_btn)
        self.scroll_body.addWidget(SettingRow("Customize Post", "Opens a page to edit the post text, choose the embed color, and save everything to config.json.", post_wrap))

        clear_wrap = QWidget()
        clear_wrap.setStyleSheet("background: transparent;")
        clear_layout = QHBoxLayout(clear_wrap)
        clear_layout.setContentsMargins(0, 0, 0, 0)
        self.clear_log_btn = self.window.make_small_button("Clear Log", self.clear_log, accent=YELLOW)
        clear_layout.addWidget(self.clear_log_btn)
        self.scroll_body.addWidget(SettingRow("Clear Log", "Deletes the history of already sent files and allows them to be sent again.", clear_wrap))

        open_wrap = QWidget()
        open_wrap.setStyleSheet("background: transparent;")
        open_layout = QHBoxLayout(open_wrap)
        open_layout.setContentsMargins(0, 0, 0, 0)
        self.open_cfg_btn = self.window.make_small_button("Open Folder", self.open_config_folder)
        open_layout.addWidget(self.open_cfg_btn)
        self.scroll_body.addWidget(SettingRow("Configuration Folder", str(BASE_DIR), open_wrap))

        self.version_value = self.window.make_info_value()
        self.scroll_body.addWidget(SettingRow("App Version", "Current version in use.", self.version_value))
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
            debug_log("toggle_start_with_windows", enabled=enabled)
            save_config()
            self.window.show_message("success", "Start with Windows updated.")
        except Exception:
            self.start_toggle.setChecked(not enabled)
            self.window.show_message("error", "Could not change the Start with Windows setting.")

    def toggle_delete_after_send(self):
        config["delete_after_send"] = self.delete_toggle.isChecked()
        debug_log("toggle_delete_after_send", enabled=config["delete_after_send"])
        save_config()
        self.window.show_message("success", "Delete option updated.")

    def clear_log(self):
        debug_log("clear_log_requested")
        clear_sent_log()
        self.window.show_message("success", "Send log cleared.")

    def open_config_folder(self):
        debug_log("open_config_folder_requested")
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(BASE_DIR))
            self.window.show_message("info", "Discord Webhook Uploader folder opened.")
        except Exception:
            self.window.show_message("error", "Could not open the Discord Webhook Uploader folder.")




def clear_sent_log():
    global sent_history
    debug_log("clear_sent_log_started")
    with file_lock:
        sent_history = []
    save_json(LOG_FILE, sent_history)
    debug_log("clear_sent_log_finished")


class MainWindow(QWidget):
    def __init__(self, tray_icon):
        super().__init__()
        self.tray_icon = tray_icon
        self.drag_pos = None
        self.is_dragging = False
        self.anim = None
        self._drag_origin = None
        self._geometry_fix_pending = False
        self._enforcing_geometry = False
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.MSWindowsFixedSizeDialogHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMaximumSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setBaseSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.panel = RoundedPanel()
        outer.addWidget(self.panel)

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        self.stack = CompactStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
            page.setMinimumSize(0, 0)
            page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.stack.addWidget(page)

        debug_log("main_window_initialized", width=WINDOW_WIDTH, height=WINDOW_HEIGHT)

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

    def scrollbar_style(self, selector="QScrollArea"):
        return f"""
        {selector} {{
            background: transparent;
            border: none;
        }}
        QScrollBar:vertical {{
            background: transparent;
            border: none;
            width: 8px;
            margin: 6px 0 6px 0;
        }}
        QScrollBar::handle:vertical {{
            background: #2a2d34;
            border: none;
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #343944;
        }}
        QScrollBar::handle:vertical:pressed {{
            background: #3d4451;
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
            background: transparent;
            border: none;
        }}
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: transparent;
            border: none;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            border: none;
            height: 0px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal,
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: transparent;
            border: none;
            width: 0px;
        }}
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
        {self.scrollbar_style("QTextEdit")}
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
        debug_log("refresh_all_started")
        self.home_page.refresh()
        self.settings_page.refresh()
        debug_log("refresh_all_finished")

    def save_post_template_if_needed(self, show_feedback=False):
        if self.stack.currentWidget() is self.post_template_page:
            self.post_template_page.save_template(show_feedback=show_feedback)

    def switch_page(self, page, animated=True):
        debug_log("switch_page", target_page=type(page).__name__, animated=animated)
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
        debug_log("show_message", kind=kind, text=text)
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
        debug_log("toggle_visible", currently_visible=self.isVisible())
        if self.isVisible():
            self.save_post_template_if_needed()
            self.hide()
        else:
            self.show_near_tray()

    def ensure_expected_geometry(self):
        debug_log("ensure_expected_geometry_started", width=self.width(), height=self.height(), window_state=self.windowState())
        if self._enforcing_geometry:
            return
        self._enforcing_geometry = True
        try:
            if self.windowState() != Qt.WindowNoState:
                self.setWindowState(Qt.WindowNoState)
            enforce_fixed_window_size(self, width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
        finally:
            self._enforcing_geometry = False
            debug_log("ensure_expected_geometry_finished", width=self.width(), height=self.height())

    def schedule_geometry_fix(self):
        debug_log("schedule_geometry_fix_requested", pending=self._geometry_fix_pending)
        if self._geometry_fix_pending:
            return
        self._geometry_fix_pending = True
        QTimer.singleShot(0, self.apply_scheduled_geometry_fix)

    def apply_scheduled_geometry_fix(self):
        debug_log("apply_scheduled_geometry_fix")
        self._geometry_fix_pending = False
        self.ensure_expected_geometry()

    def hide_to_tray(self):
        debug_log("hide_to_tray")
        self.save_post_template_if_needed()
        self.is_dragging = False
        self.drag_pos = None
        self.hide()
        self.clear_message()

    def hideEvent(self, event):
        debug_log("main_window_hide_event")
        self.save_post_template_if_needed()
        super().hideEvent(event)

    def show_near_tray(self):
        debug_log("show_near_tray_started")
        self.refresh_all()
        self.ensure_expected_geometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - WINDOW_WIDTH - 20
        y = screen.bottom() - WINDOW_HEIGHT - 50
        set_window_pos_safely(self, x=x, y=y, move=True, resize=False)
        debug_log("show_near_tray_positioned", x=x, y=y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.ensure_expected_geometry()

    def exit_app(self):
        debug_log("exit_app_requested")
        self.save_post_template_if_needed()
        stop_event.set()
        self.hide()
        self.tray_icon.hide()
        QApplication.quit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.ensure_expected_geometry()
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_origin = self.pos()
            self.is_dragging = True
            debug_log("window_drag_started", mouse_x=event.globalPosition().toPoint().x(), mouse_y=event.globalPosition().toPoint().y(), window_x=self.x(), window_y=self.y())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            target = event.globalPosition().toPoint() - self.drag_pos
            set_window_pos_safely(self, x=target.x(), y=target.y(), move=True, resize=False)
            debug_log("window_drag_moved", target_x=target.x(), target_y=target.y(), width=self.width(), height=self.height())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        debug_log("window_drag_released", width=self.width(), height=self.height())
        self.drag_pos = None
        self.is_dragging = False
        self.ensure_expected_geometry()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        debug_log("main_window_resize_event", width=event.size().width(), height=event.size().height(), expected_width=WINDOW_WIDTH, expected_height=WINDOW_HEIGHT)
        super().resizeEvent(event)
        if self._enforcing_geometry:
            return
        if event.size().width() != WINDOW_WIDTH or event.size().height() != WINDOW_HEIGHT:
            debug_log("unexpected_main_window_resize_detected", actual_width=event.size().width(), actual_height=event.size().height())
            self.ensure_expected_geometry()

    def changeEvent(self, event):
        debug_log("main_window_change_event", event_type=event.type(), window_state=self.windowState())
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange and self.windowState() != Qt.WindowNoState:
            self.ensure_expected_geometry()

    def showEvent(self, event):
        debug_log("main_window_show_event")
        self.ensure_expected_geometry()
        super().showEvent(event)

    def sizeHint(self):
        return QSize(WINDOW_WIDTH, WINDOW_HEIGHT)

    def minimumSizeHint(self):
        return QSize(WINDOW_WIDTH, WINDOW_HEIGHT)


class TrayExitBubble(QWidget):
    def __init__(self, on_exit, parent=None):
        super().__init__(parent)
        self.on_exit = on_exit
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.exit_btn = QPushButton("Quit")
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
        debug_log("tray_controller_initialized")
        self.tray.setToolTip(f"{APP_NAME} v{APP_VERSION}")
        self.rotation = 0.0
        self._last_static_state = None

        self.window = MainWindow(self.tray)
        self.exit_bubble = TrayExitBubble(self.exit_app)
        self.tray.activated.connect(self.on_tray_activated)
        signals.status_changed.connect(self.sync_pause_action)

        self.focus_loss_timer = QTimer(self)
        self.focus_loss_timer.setSingleShot(True)
        self.focus_loss_timer.setInterval(120)
        self.focus_loss_timer.timeout.connect(self.handle_focus_loss)
        self.app.focusChanged.connect(self.on_focus_changed)

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
        debug_log("refresh_tray_icon", force=force, sending=sending_event.is_set(), monitoring=monitoring)
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

    def on_focus_changed(self, old, now):
        debug_log("focus_changed", old=str(old), now=str(now), is_dragging=self.window.is_dragging)
        if self.window.is_dragging:
            self.focus_loss_timer.stop()
            return
        if now is None:
            self.focus_loss_timer.start()
        else:
            self.focus_loss_timer.stop()

    def iter_managed_windows(self):
        managed = [self.window, self.exit_bubble]
        seen = {id(self.window), id(self.exit_bubble)}
        for widget in QApplication.topLevelWidgets():
            if id(widget) in seen:
                continue
            parent = widget.parentWidget()
            if parent is self.window or self.window.isAncestorOf(widget):
                managed.append(widget)
                seen.add(id(widget))
        return managed

    def hide_interface_to_tray(self):
        debug_log("hide_interface_to_tray_requested", is_dragging=self.window.is_dragging)
        if self.window.is_dragging:
            return
        for widget in self.iter_managed_windows():
            if widget is not self.window:
                widget.hide()
        self.window.hide_to_tray()

    def handle_focus_loss(self):
        debug_log("handle_focus_loss_started", is_dragging=self.window.is_dragging)
        if self.window.is_dragging:
            return
        visible_windows = [widget for widget in self.iter_managed_windows() if widget.isVisible()]
        if not visible_windows:
            return
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return
        active_window = QApplication.activeWindow()
        if active_window in visible_windows:
            return
        for widget in visible_windows:
            focus_widget = widget.focusWidget()
            if focus_widget is not None and focus_widget.hasFocus():
                return
        self.hide_interface_to_tray()

    def on_tray_activated(self, reason):
        debug_log("tray_activated", reason=reason)
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
    debug_log("ensure_first_run")
    if not config.get("webhook"):
        window.open_webhook_page()
        window.show_near_tray()
        return
    if not config.get("folder"):
        window.open_folder_page()
        window.show_near_tray()
        return


if __name__ == "__main__":
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    save_config()
    init_debug_session()
    debug_log("application_bootstrap_started", argv=sys.argv)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = TrayController(app)
    debug_log("application_qt_created")
    ensure_first_run(controller.window)

    worker = threading.Thread(target=monitoring_loop, daemon=True)
    worker.start()
    debug_log("monitoring_thread_started")

    exit_code = app.exec()
    debug_log("application_exit", exit_code=exit_code)
    sys.exit(exit_code)
