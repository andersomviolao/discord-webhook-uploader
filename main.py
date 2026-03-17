import os
import json
import time
import threading
import requests
import shutil
import hashlib
import datetime
import queue
import customtkinter as ctk
from pathlib import Path
from PIL import Image, ImageDraw
import pystray
from tkinter import filedialog
from send2trash import send2trash

BACKGROUND_COLOR = "#1e1f22"
CARD_COLOR = "#2b2d31"
BLURPLE = "#5865f2"
TEXT_COLOR = "#dbdee1"
GREEN = "#23a559"
YELLOW = "#f2a318"
RED = "#f23f42"

APP_NAME = "Webhook-Uploader"
BASE_DIR = Path(os.getenv("LOCALAPPDATA")) / APP_NAME

CFG_DIR = BASE_DIR / "cfg"
LOG_DIR = BASE_DIR / "log"

CONFIG_FILE = CFG_DIR / "cfg.json"
LOG_FILE = LOG_DIR / "log.json"
TEMPLATE_FILE = CFG_DIR / "post.txt"
DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

file_lock = threading.RLock()
gui_queue = queue.Queue()
monitoring = True
icon_global = None


def load_json(path, default):
    with file_lock:
        if not path.exists():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default



def save_json(path, data):
    with file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving file: {e}")



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
            with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return """🆕
📄 `{filename}`
📅 `{creation_str}`
🆙 Upload: {upload_str}
___"""


config = load_json(CONFIG_FILE, {"folder": "", "webhook": ""})
sent_history = load_json(LOG_FILE, [])



def create_status_image(active):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (88, 101, 242) if active else (242, 163, 24)
    d.ellipse((4, 4, 60, 60), fill=color + (255,))
    d.ellipse((16, 16, 48, 48), fill=(30, 31, 34, 255))
    return img



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
    error_dir = Path(config["folder"]) / "ERROR"

    try:
        if os.path.getsize(path) / (1024 * 1024) > 25:
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



def monitoring_loop():
    while True:
        if monitoring and config.get("folder") and config.get("webhook"):
            try:
                now = time.time()
                files = [
                    os.path.join(config["folder"], f)
                    for f in os.listdir(config["folder"])
                    if os.path.isfile(os.path.join(config["folder"], f))
                ]
                ready = [p for p in files if now - os.path.getctime(p) >= 3600]

                for file in sorted(ready, key=os.path.getctime):
                    if not monitoring:
                        break
                    if send_file(file):
                        time.sleep(10)
            except Exception:
                pass

        for _ in range(300):
            if not monitoring:
                break
            time.sleep(1)


class FloatingMenu(ctk.CTkToplevel):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=BACKGROUND_COLOR)
        self.bind("<FocusOut>", lambda e: self.withdraw())

        self.width = 240
        self.height = 340

        self.frame = ctk.CTkFrame(
            self,
            fg_color=BACKGROUND_COLOR,
            corner_radius=12,
            border_width=1,
            border_color="#3f4147",
        )
        self.frame.pack(expand=True, fill="both", padx=2, pady=2)

        self.lbl_status = ctk.CTkLabel(
            self.frame,
            text="● MONITORING",
            font=("Segoe UI", 11, "bold"),
            text_color=GREEN,
        )
        self.lbl_status.pack(pady=(15, 10))

        self.btn_send = self.add_item("Send Now", self.action_send)
        self.btn_pause = self.add_item("Pause / Resume", self.action_pause)

        ctk.CTkFrame(self.frame, height=1, fg_color="#3f4147").pack(fill="x", padx=15, pady=8)

        self.add_item("Change Folder", self.action_folder)
        self.add_item("Configure Webhook", self.action_webhook)
        self.add_item("Clear History", self.action_clear)
        self.add_item("Exit Program", self.action_exit, RED)

    def add_item(self, text, command, color=TEXT_COLOR):
        btn = ctk.CTkButton(
            self.frame,
            text=text,
            command=command,
            fg_color="transparent",
            hover_color=CARD_COLOR,
            anchor="w",
            font=("Segoe UI", 12),
            text_color=color,
            height=38,
            corner_radius=8,
        )
        btn.pack(fill="x", padx=8, pady=1)
        return btn

    def show(self):
        x, y = self.winfo_pointerxy()

        pos_x = x - self.width
        pos_y = y - self.height - 40

        self.geometry(f"{self.width}x{self.height}{pos_x:+d}{pos_y:+d}")

        self.deiconify()
        self.focus_force()
        self.update_visual()

    def update_visual(self):
        color = GREEN if monitoring else YELLOW
        txt = "● MONITORING" if monitoring else "● PAUSED"
        self.lbl_status.configure(text=txt, text_color=color)

    def action_pause(self):
        global monitoring
        monitoring = not monitoring
        icon_global.icon = create_status_image(monitoring)
        self.update_visual()

    def action_send(self):
        threading.Thread(target=send_now_manual, daemon=True).start()
        self.withdraw()

    def action_folder(self):
        p = filedialog.askdirectory()
        if p:
            config["folder"] = p
            save_json(CONFIG_FILE, config)
        self.withdraw()

    def action_webhook(self):
        dialog = ctk.CTkInputDialog(
            text="Paste the Discord Webhook:",
            title="Configuration",
            button_fg_color=BLURPLE,
        )
        w = dialog.get_input()
        if w and "discord.com" in w:
            config["webhook"] = w.strip()
            save_json(CONFIG_FILE, config)
        self.withdraw()

    def action_clear(self):
        with file_lock:
            sent_history.clear()
        save_json(LOG_FILE, sent_history)
        self.withdraw()

    def action_exit(self):
        icon_global.stop()
        os._exit(0)



def send_now_manual():
    if not config.get("folder"):
        return

    files = [
        os.path.join(config["folder"], f)
        for f in os.listdir(config["folder"])
        if os.path.isfile(os.path.join(config["folder"], f))
    ]
    for file in sorted(files, key=os.path.getctime):
        if send_file(file):
            time.sleep(10)



def open_menu_direct(icon, item):
    gui_queue.put("open")


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()

    menu_ui = FloatingMenu()
    menu_ui.withdraw()

    if not config.get("folder"):
        p = filedialog.askdirectory(title="Select folder to monitor")
        if p:
            config["folder"] = p
            save_json(CONFIG_FILE, config)
    if not config.get("webhook"):
        dialog = ctk.CTkInputDialog(
            text="Paste the Discord Webhook:",
            title="First Run - Webhook Setup",
            button_fg_color=BLURPLE,
        )
        w = dialog.get_input()
        if w and "discord.com" in w:
            config["webhook"] = w.strip()
            save_json(CONFIG_FILE, config)

    item_invisible = pystray.MenuItem("Open", open_menu_direct, default=True, visible=False)
    menu = pystray.Menu(item_invisible)

    icon_global = pystray.Icon(APP_NAME, create_status_image(True), "Discord Uploader", menu)
    icon_global.run_detached()

    threading.Thread(target=monitoring_loop, daemon=True).start()

    def process():
        try:
            msg = gui_queue.get_nowait()
            if msg == "open":
                menu_ui.show()
        except queue.Empty:
            pass
        except Exception as e:
            print(f"Error opening interface: {e}")

        root.after(100, process)

    root.after(100, process)
    root.mainloop()
