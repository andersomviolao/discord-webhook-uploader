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
from PySide6.QtCore import Qt, Signal, QObject, QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSystemTrayIcon,
    QPushButton,
    QStackedWidget,
    QGraphicsOpacityEffect,
    QFileDialog,
    QScrollArea,
)

try:
    import winreg
except Exception:
    winreg = None

APP_NAME = "Webhook-Uploader"
APP_VERSION = "1.9.4"
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

WAIT_TIME = 3600
POST_INTERVAL = 10
MONITOR_CHECK_INTERVAL = 5
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

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


def normalize_config(raw):
    return {
        "folder": raw.get("folder", ""),
        "webhook": raw.get("webhook", ""),
        "start_with_windows": bool(raw.get("start_with_windows", False)),
        "delete_after_send": bool(raw.get("delete_after_send", True)),
    }


config = normalize_config(load_json(CONFIG_FILE, {}))
sent_history = load_json(LOG_FILE, [])


class UISignals(QObject):
    status_changed = Signal(bool)
    toast = Signal(str, str)
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
        message = template.format(filename=filename, creation_str=creation_str, upload_str=upload_str)

        for attempt in range(4):
            try:
                with open(path, "rb") as f:
                    res = requests.post(
                        webhook,
                        data={"content": message},
                        files={"file": (filename, f)},
                        timeout=15,
                    )

                if res.status_code in [200, 204]:
                    finalize_sent_file(path, filename, file_hash, upload_str)
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
        root.setSpacing(12)

        top = QVBoxLayout()
        top.setSpacing(2)

        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{BLUE}; font: 700 20px 'Segoe UI';")
        top.addWidget(self.title)

        self.subtitle = QLabel(subtitle)
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(f"color:{MUTED}; font: 500 12px 'Segoe UI';")
        top.addWidget(self.subtitle)

        root.addLayout(top)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        root.addLayout(self.body, 1)


class HomePage(PageBase):
    def __init__(self, window):
        super().__init__(f"Webhook Uploader v{APP_VERSION}", "Monitoramento simples, visual refinado e tudo dentro da mesma interface.")
        self.window = window

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addStretch(1)
        self.cfg_btn = HoverButton("⚙", size=24, tooltip="Configurações", bg="transparent", hover="#1d2025", fg="#6f7580", font_size=10)
        self.cfg_btn.clicked.connect(self.window.open_settings_page)
        top_row.addWidget(self.cfg_btn, 0, Qt.AlignTop)
        self.body.addLayout(top_row)

        self.body.addWidget(self.make_label("Webhook"))
        self.webhook_card = self.make_edit_card("Cole o webhook do Discord", "Editar", self.window.open_webhook_page)
        self.webhook_edit = self.webhook_card["field"]
        self.body.addWidget(self.webhook_card["card"])

        self.body.addWidget(self.make_label("Watched Folder"))
        self.folder_card = self.make_edit_card("Selecione a pasta monitorada", "Editar", self.window.open_folder_page)
        self.folder_edit = self.folder_card["field"]
        self.body.addWidget(self.folder_card["card"])

        self.body.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        bottom.setSpacing(8)

        self.pause_btn = self.window.make_secondary_button("Rodando", self.window.toggle_monitoring)
        self.pause_btn.setMinimumWidth(92)
        bottom.addWidget(self.pause_btn)

        self.close_btn = self.window.make_secondary_button("Esconder", self.window.hide_to_tray)
        self.close_btn.setMinimumWidth(92)
        bottom.addWidget(self.close_btn)

        self.body.addLayout(bottom)

    def make_label(self, text):
        label = QLabel(text)
        label.setStyleSheet(f"color:{TEXT}; font: 600 13px 'Segoe UI';")
        return label

    def make_field(self, placeholder):
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        edit.setMinimumHeight(34)
        edit.setStyleSheet(
            f"""
            QLineEdit {{
                background: transparent;
                color: {FIELD_TEXT};
                border: none;
                padding: 0;
                font: 600 12px 'Segoe UI';
            }}
            QLineEdit::placeholder {{ color: #6f7580; }}
            """
        )
        return edit

    def make_edit_card(self, placeholder, button_text, handler):
        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background: {CARD};
                border: 1px solid {CARD_BORDER};
                border-radius: 16px;
            }}
            """
        )
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)

        field = self.make_field(placeholder)
        row.addWidget(field, 1)

        button = self.window.make_small_button(button_text, handler)
        button.setMinimumWidth(70)
        row.addWidget(button)

        return {"card": card, "field": field, "button": button}

    def refresh(self):
        self.webhook_edit.setText(config.get("webhook", ""))
        self.folder_edit.setText(config.get("folder", ""))
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
        self.input.setMinimumHeight(42)
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
        self.input.setMinimumHeight(42)
        self.input.setReadOnly(True)
        self.input.setStyleSheet(self.window.input_style())
        row.addWidget(self.input, 1)

        self.browse_btn = self.window.make_secondary_button("Procurar", self.browse_folder)
        self.browse_btn.setMinimumHeight(42)
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
        t.setStyleSheet(f"color:{TEXT}; font: 700 12px 'Segoe UI';")
        left.addWidget(t)

        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setStyleSheet(f"color:{MUTED}; font: 500 11px 'Segoe UI';")
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

        test_wrap = QWidget()
        test_wrap.setStyleSheet("background: transparent;")
        test_layout = QHBoxLayout(test_wrap)
        test_layout.setContentsMargins(0, 0, 0, 0)
        self.test_btn = self.window.make_small_button("Testar", self.test_webhook)
        test_layout.addWidget(self.test_btn)
        self.scroll_body.addWidget(SettingRow("Testar webhook", "Envia uma mensagem de texto simples para o webhook atual.", test_wrap))

        self.delete_toggle = ToggleSwitch(config.get("delete_after_send", True))
        self.delete_toggle.clicked.connect(self.toggle_delete_after_send)
        self.scroll_body.addWidget(SettingRow("Excluir após enviar", "Ligado: move para a lixeira. Desligado: mantém o arquivo e evita duplicidade pelo log.", self.delete_toggle))

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
        has_webhook = bool((config.get("webhook") or "").strip())
        self.test_btn.setEnabled(has_webhook)
        self.test_btn.setStyleSheet(self.window.small_button_style(enabled=has_webhook, accent=BLUE))

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

    def test_webhook(self):
        ok, msg = send_test_message()
        self.window.show_message("success" if ok else "error", msg)

    def open_config_folder(self):
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(BASE_DIR))
            self.window.show_message("info", "Pasta raiz do Webhook-Uploader aberta.")
        except Exception:
            self.window.show_message("error", "Não foi possível abrir a pasta raiz do Webhook-Uploader.")


class MainWindow(QWidget):
    def __init__(self, tray_icon):
        super().__init__()
        self.tray_icon = tray_icon
        self.drag_pos = None
        self.anim = None
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(560, 332)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.panel = RoundedPanel()
        outer.addWidget(self.panel)

        root = QVBoxLayout(self.panel)
        root.setContentsMargins(22, 18, 18, 14)
        root.setSpacing(10)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.message_label = QLabel("")
        self.message_label.setMinimumHeight(20)
        self.message_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.message_label.setStyleSheet(f"color:{MUTED}; font: 600 11px 'Segoe UI';")
        root.addWidget(self.message_label)

        self.home_page = HomePage(self)
        self.webhook_page = WebhookPage(self)
        self.folder_page = FolderPage(self)
        self.settings_page = SettingsPage(self)

        for page in [self.home_page, self.webhook_page, self.folder_page, self.settings_page]:
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
            font: 600 12px 'Segoe UI';
        }}
        QLineEdit:focus {{ border: 1px solid {BLUE}; }}
        QLineEdit::placeholder {{ color: #6f7580; }}
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
                border-radius: 14px;
                padding: 9px 16px;
                font: 700 12px 'Segoe UI';
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
                border-radius: 14px;
                padding: 9px 14px;
                font: 700 12px 'Segoe UI';
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
            padding: 8px 14px;
            font: 700 11px 'Segoe UI';
        }}
        QPushButton:hover {{ background: {hover}; }}
        """

    def make_small_button(self, text, handler, accent=BLUE):
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(handler)
        btn.setStyleSheet(self.small_button_style(True, accent=accent))
        return btn

    def make_info_value(self):
        label = QLabel("")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(f"color:{TEXT}; font: 600 11px 'Segoe UI'; background: transparent; border: none;")
        return label

    def refresh_all(self):
        self.home_page.refresh()
        self.settings_page.refresh()

    def switch_page(self, page, animated=True):
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

    def toggle_monitoring(self):
        global monitoring
        monitoring = not monitoring
        signals.status_changed.emit(monitoring)

    def on_status_changed(self, active):
        self.tray_icon.setIcon(create_tray_icon(active))
        self.home_page.update_pause_visual()

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_near_tray()

    def hide_to_tray(self):
        self.hide()
        self.clear_message()

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
        self.configs_action = QAction("Settings")
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

        self.window = MainWindow(self.tray)

        self.open_action.triggered.connect(self.window.show_near_tray)
        self.send_now_action.triggered.connect(self.start_send_now)
        self.pause_action.triggered.connect(self.toggle_monitoring)
        self.configs_action.triggered.connect(self.open_settings)
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

    def open_settings(self):
        self.window.open_settings_page()
        self.window.show_near_tray()

    def sync_pause_action(self, active):
        self.pause_action.setText("Pause" if active else "Resume")
        self.tray.setIcon(create_tray_icon(active))
        self.window.home_page.update_pause_visual()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.toggle_visible()

    def exit_app(self):
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
