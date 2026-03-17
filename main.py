import os
import json
import time
import threading
import requests
import shutil
import hashlib
import datetime
import queue
from pathlib import Path
from send2trash import send2trash
from tkinter import Tk, simpledialog, filedialog
from PIL import Image, ImageDraw
import pystray

# CONFIGURAÇÕES BÁSICAS
APP_NAME = "Webhook_Uploader"
CONFIG_DIR = Path(os.getenv("LOCALAPPDATA")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "enviados_log.json"
DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# CONTROLE GLOBAL
file_lock = threading.Lock()
gui_queue = queue.Queue()
monitorando = True

def carregar_json(caminho, default):
    with file_lock:
        if not caminho.exists(): return default
        try:
            with open(caminho, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar JSON {caminho.name}: {e}")
            return default

def salvar_json(caminho, data):
    with file_lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Erro ao salvar JSON {caminho.name}: {e}")

config = carregar_json(CONFIG_FILE, {"pasta": "", "webhook": ""})
historico_enviados = carregar_json(LOG_FILE, [])

# UTILITÁRIOS
def get_file_hash(caminho):
    h = hashlib.sha256()
    try:
        with open(caminho, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print(f"Erro ao gerar hash: {e}")
        return None

def arquivo_esta_livre(caminho):
    try:
        for _ in range(5):
            size1 = os.path.getsize(caminho)
            time.sleep(1.5)
            size2 = os.path.getsize(caminho)
            if size1 == size2:
                with open(caminho, 'rb+'): return True
        return False
    except Exception:
        return False

# INTERFACE (Sempre chamadas pela Thread Principal via Fila)
def abrir_gui_config():
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    
    if not config.get("pasta") or not os.path.isdir(config["pasta"]):
        nova_pasta = filedialog.askdirectory(title="Selecione a pasta para monitorar", parent=root)
        if nova_pasta:
            config["pasta"] = nova_pasta
            salvar_json(CONFIG_FILE, config)

    if not config.get("webhook") or "discord.com/api/webhooks/" not in config["webhook"]:
        novo = simpledialog.askstring("Webhook", "Cole o URL do Webhook do Discord:", parent=root)
        if novo and "discord.com/api/webhooks/" in novo:
            config["webhook"] = novo.strip()
            salvar_json(CONFIG_FILE, config)
    root.destroy()

def trocar_pasta_gui():
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    nova = filedialog.askdirectory(title="Nova Pasta Monitorada", parent=root)
    if nova and os.path.isdir(nova):
        config["pasta"] = nova
        salvar_json(CONFIG_FILE, config)
    root.destroy()

def trocar_webhook_gui():
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    novo = simpledialog.askstring("Novo Webhook", "Cole o novo URL do webhook:", parent=root)
    if novo and "discord.com/api/webhooks/" in novo:
        config["webhook"] = novo.strip()
        salvar_json(CONFIG_FILE, config)
    root.destroy()

def limpar_cache():
    global historico_enviados
    with file_lock:
        historico_enviados.clear()
    salvar_json(LOG_FILE, historico_enviados)

# LÓGICA DE ENVIO
def enviar_arquivo(caminho):
    if not config.get("webhook"): return False
    nome_arquivo = os.path.basename(caminho)
    error_dir = Path(config["pasta"]) / "ERROR"

    try:
        if os.path.getsize(caminho) / (1024 * 1024) > 25:
            error_dir.mkdir(exist_ok=True)
            shutil.move(caminho, error_dir / nome_arquivo)
            return False
    except Exception: return False

    file_hash = get_file_hash(caminho)
    if not file_hash: return False

    with file_lock:
        if any(item.get('hash') == file_hash for item in historico_enviados):
            return False

    if not arquivo_esta_livre(caminho): return False

    try:
        stat = os.stat(caminho)
        agora_dt = datetime.datetime.now()
        criacao_dt = datetime.datetime.fromtimestamp(stat.st_ctime)
        
        data_criacao_str = f"{DIAS_SEMANA[criacao_dt.weekday()]}, {criacao_dt.strftime('%d/%m/%y %H:%M:%S')}"
        data_upload_str = f"{DIAS_SEMANA[agora_dt.weekday()]}, {agora_dt.strftime('%d/%m/%y %H:%M:%S')}"
        mensagem = f"🆕\n📄 {nome_arquivo}\n📅 Criado: {data_criacao_str}\n🆙 Upload: {data_upload_str}\n___"

        for tentativa in range(4):
            try:
                with open(caminho, "rb") as f:
                    res = requests.post(config["webhook"], data={"content": mensagem}, files={"file": (nome_arquivo, f)}, timeout=60)
                
                if res.status_code in [200, 204]:
                    send2trash(os.path.abspath(caminho))
                    with file_lock:
                        historico_enviados.append({"arquivo": nome_arquivo, "hash": file_hash, "data": data_upload_str})
                        if len(historico_enviados) > 2000: del historico_enviados[:-2000]
                    salvar_json(LOG_FILE, historico_enviados)
                    return True
                elif res.status_code == 429: # Rate Limit
                    time.sleep(2 ** tentativa)
                    continue
                elif res.status_code == 413: # File too large
                    error_dir.mkdir(exist_ok=True)
                    shutil.move(caminho, error_dir / nome_arquivo)
                    return False
                break
            except Exception:
                time.sleep(2 ** tentativa)
        return False
    except Exception: return False

def enviar_agora():
    if not monitorando: return
    try:
        arquivos = [os.path.join(config["pasta"], f) for f in os.listdir(config["pasta"]) if os.path.isfile(os.path.join(config["pasta"], f))]
        for arquivo in sorted(arquivos, key=os.path.getctime):
            if not monitorando: break
            if enviar_arquivo(arquivo):
                time.sleep(10) # Intervalo de 10s entre arquivos no envio manual
    except Exception as e:
        print(f"Erro no envio manual: {e}")

def loop_monitoramento():
    while True:
        if monitorando and config.get("pasta") and config.get("webhook") and os.path.isdir(config["pasta"]):
            try:
                agora = time.time()
                arquivos = [os.path.join(config["pasta"], f) for f in os.listdir(config["pasta"]) if os.path.isfile(os.path.join(config["pasta"], f))]
                # Mantido o delay de 1 hora (3600s) para arquivos novos
                arquivos_prontos = [p for p in arquivos if agora - os.path.getctime(p) >= 3600]
                
                for arquivo in sorted(arquivos_prontos, key=os.path.getctime):
                    if not monitorando: break
                    if enviar_arquivo(arquivo):
                        time.sleep(10) # Intervalo de 10s entre arquivos no automático
            except Exception: pass
        
        # Espera 5 minutos (300 segundos) para a próxima varredura
        for _ in range(300):
            if not monitorando: break
            time.sleep(1)

# SISTEMA TRAY
def criar_imagem(ativo):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cor = (0, 200, 255) if ativo else (255, 200, 0)
    d.ellipse((4, 4, 60, 60), fill=cor + (255,))
    d.ellipse((18, 18, 46, 46), fill=(30, 30, 30, 255))
    return img

def acao_pausar(icon):
    global monitorando
    monitorando = not monitorando
    icon.icon = criar_imagem(monitorando)

def acao_sair(icon):
    icon.stop()
    gui_queue.put("sair")

if __name__ == "__main__":
    # Inicia monitoramento
    threading.Thread(target=loop_monitoramento, daemon=True).start()
    
    # Configuração Inicial
    if not config.get("pasta") or not config.get("webhook"):
        gui_queue.put("pedir_config")

    # Menu do Ícone
    menu = pystray.Menu(
        pystray.MenuItem("Pausar / Retomar", acao_pausar),
        pystray.MenuItem("Enviar Agora", lambda: threading.Thread(target=enviar_agora, daemon=True).start()),
        pystray.MenuItem("Limpar Cache", lambda: threading.Thread(target=limpar_cache, daemon=True).start()),
        pystray.MenuItem("Trocar Pasta", lambda: gui_queue.put("trocar_pasta")),
        pystray.MenuItem("Trocar Webhook", lambda: gui_queue.put("trocar_webhook")),
        pystray.MenuItem("Abrir Pasta Config", lambda: os.startfile(CONFIG_DIR)),
        pystray.MenuItem("Sair", acao_sair)
    )
   
    icon = pystray.Icon(APP_NAME, criar_imagem(True), "Webhook Uploader", menu)
    icon.run_detached()

    # LOOP DA THREAD PRINCIPAL: Processa pedidos de interface sem travar
    while True:
        try:
            tarefa = gui_queue.get(timeout=1)
            if tarefa == "pedir_config": abrir_gui_config()
            elif tarefa == "trocar_pasta": trocar_pasta_gui()
            elif tarefa == "trocar_webhook": trocar_webhook_gui()
            elif tarefa == "sair": break
        except queue.Empty:
            continue

    os._exit(0)