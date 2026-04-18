"""
RobertoO 4.0
Agente integrado ao Windows com:
- tool-calling
- RAG local em markdown (Chroma)
- automacoes de sistema
- interface CLI mais limpa
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import tool

    LANGCHAIN_CORE_OK = True
except ImportError:
    HumanMessage = SystemMessage = ToolMessage = None
    LANGCHAIN_CORE_OK = False

    def tool(func):
        setattr(func, "name", func.__name__)

        def _invoke(payload: Any):
            if isinstance(payload, dict):
                return func(**payload)
            return func(payload)

        setattr(func, "invoke", _invoke)
        return func

try:
    import aiohttp

    AIOHTTP_OK = True
except ImportError:
    aiohttp = None
    AIOHTTP_OK = False

try:
    from bs4 import BeautifulSoup

    BS4_OK = True
except ImportError:
    BeautifulSoup = None
    BS4_OK = False

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.3
    PYAUTOGUI_OK = True
except ImportError:
    pyautogui = None
    PYAUTOGUI_OK = False

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_OK = True
except ImportError:
    FileSystemEventHandler = object
    Observer = None
    WATCHDOG_OK = False

try:
    import psutil

    PSUTIL_OK = True
except ImportError:
    psutil = None
    PSUTIL_OK = False

try:
    from indexador import reindexar_direto
except ImportError:
    def reindexar_direto(logger: logging.Logger, embeddings: Any, **_: Any) -> Optional[Any]:
        logger.warning("[RAG] indexador.py nao encontrado; RAG desativado.")
        return None


@dataclass(frozen=True)
class Config:
    caminho_base: str = r"C:\agenteIA"
    modelo_ollama: str = "qwen2.5-coder:1.5b"
    modelo_embeddings: str = "sentence-transformers/all-MiniLM-L6-v2"
    temperatura: float = 0.0
    max_latencia_ms: int = 4500
    max_ctx_chars: int = 8000
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def caminho_vault(self) -> str:
        return str(Path(self.caminho_base) / "BrainTwo")

    @property
    def caminho_database(self) -> str:
        return str(Path(self.caminho_vault) / "Database")

    @property
    def caminho_vector_db(self) -> str:
        return str(Path(self.caminho_base) / "database")

    @property
    def caminho_downloads(self) -> str:
        return str(Path(self.caminho_base) / "downloads")

    @property
    def caminho_logs(self) -> str:
        return str(Path(self.caminho_base) / "logs")

    @property
    def caminho_log_latencia(self) -> str:
        return str(Path(self.caminho_logs) / "latencia.jsonl")

    @property
    def caminho_log_aprendizado(self) -> str:
        return str(Path(self.caminho_database) / "LOG_DE_APRENDIZADO_ROBERTOO.md")


CONFIG = Config()
COMANDOS_DESTRUTIVOS = {"del ", "remove-item", "rm ", "rmdir", "format", "shutdown", "restart", "taskkill"}
APPS_PERMITIDOS = {
    "notepad", "calc", "mspaint", "explorer", "cmd", "wt", "code", "firefox", "chrome",
    "telegram", "powershell", "obsidian", "steam", "excel", "word", "winword", "cursor",
}
ATALHOS_APPS = {
    "bloco de notas": "notepad",
    "calculadora": "calc",
    "navegador": "firefox",
    "browser": "firefox",
    "terminal": "wt",
    "vscode": "code",
}
COMANDOS_RAPIDOS = {
    "/help", "/status", "/tools", "/clear", "/reindex", "/quit", "/sair",
}
PREFIXOS_FERRAMENTA = {
    "ler_arquivo:": "ler_arquivo",
    "criar_arquivo:": "criar_arquivo",
    "criar_pasta:": "criar_pasta",
    "excluir:": "excluir",
    "mover:": "mover",
    "baixar:": "baixar",
    "execute:": "execute",
    "powershell:": "powershell",
    "notificar_telegram:": "notificar_telegram",
}

agente_instance: Optional["RobertoO"] = None
FERRAMENTAS: list[Any] = []
FERRAMENTAS_MAP: dict[str, Any] = {}


def _get_agent() -> "RobertoO":
    if agente_instance is None:
        raise RuntimeError("Agente nao inicializado.")
    return agente_instance


def nome_usuario_preferido() -> str:
    return (os.getenv("ROBERTOO_USER_NAME") or os.getenv("USERNAME") or "Usuario").strip() or "Usuario"


def _truncar(texto: str, limite: int = 1600) -> str:
    if len(texto) <= limite:
        return texto
    return texto[:limite] + "\n...[truncado]..."


def _normalizar_texto_bruto(texto: str) -> str:
    base = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", base).strip()


def configurar_logging() -> logging.Logger:
    os.makedirs(CONFIG.caminho_logs, exist_ok=True)
    caminho_log = Path(CONFIG.caminho_logs) / (datetime.now().strftime("%Y-%m-%d_%H-%M") + ".log")
    logger = logging.getLogger("RobertoO")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    file_handler = logging.FileHandler(caminho_log, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)
    return logger


class ConsoleUI:
    def __init__(self) -> None:
        self._sep = "=" * 78

    def banner(self, nome: str) -> None:
        print(self._sep)
        print("ROBERTOO 4.0 | AGENTE LOCAL WINDOWS")
        print(f"Usuario: {nome}")
        print("Comandos: /help /status /tools /reindex /clear /quit")
        print(self._sep)

    def info(self, msg: str) -> None:
        print(f"[INFO] {msg}")

    def ok(self, msg: str) -> None:
        print(f"[OK] {msg}")

    def warn(self, msg: str) -> None:
        print(f"[WARN] {msg}")

    def err(self, msg: str) -> None:
        print(f"[ERRO] {msg}")

    def user_prompt(self) -> str:
        return "\nVoce> "

    def agent(self, msg: str) -> None:
        print(f"RobertoO> {msg}")


class VaultWatcher(FileSystemEventHandler):
    def _on_event(self, event: Any) -> None:
        if getattr(event, "is_directory", False):
            return
        src = str(getattr(event, "src_path", ""))
        if not src.lower().endswith(".md") or ".obsidian" in src.lower():
            return
        agent = agente_instance
        if not agent or agent.embeddings is None:
            return
        try:
            novo_db = reindexar_direto(
                agent.logger,
                agent.embeddings,
                vault_dir=CONFIG.caminho_vault,
                persist_dir=CONFIG.caminho_vector_db,
            )
            with agent._db_lock:
                agent.db = novo_db
            agent.ui.info(f"Vault reindexado: {Path(src).name}")
        except Exception as exc:
            agent.logger.error("[WATCHDOG] Falha ao reindexar: %s", exc)

    def on_created(self, event: Any) -> None:
        self._on_event(event)

    def on_modified(self, event: Any) -> None:
        self._on_event(event)


class RobertoO:
    def __init__(self) -> None:
        global agente_instance
        agente_instance = self
        self.logger = configurar_logging()
        self.ui = ConsoleUI()
        self.nome_usuario = nome_usuario_preferido()
        self.info_sistema = {
            "pc": platform.node(),
            "usuario": os.getenv("USERNAME", "desconhecido"),
            "os": f"{platform.system()} {platform.release()}",
            "arquitetura": platform.machine(),
            "base": CONFIG.caminho_base,
            "database_dir": CONFIG.caminho_database,
            "vector_db_dir": CONFIG.caminho_vector_db,
        }
        self._db_lock = threading.Lock()
        self._latencia_hist: list[dict[str, Any]] = []
        self._turnos_memoria: list[tuple[str, str]] = []
        self.max_turnos_memoria = 18
        self.cache_memoria: dict[str, str] = {}
        self.db: Any = None
        self.embeddings: Any = None
        self.llm: Any = None
        self.llm_with_tools: Any = None
        self.observer: Any = None
        self._running = False
        self._iniciou = False

    def _normalizar_caminho_usuario(self, caminho: str) -> str:
        base = (caminho or "").strip().strip('"').strip("'")
        if not base:
            return CONFIG.caminho_base
        base = base.replace("~", str(Path.home()))
        base = base.replace("{base}", CONFIG.caminho_base).replace("{database}", CONFIG.caminho_database)
        base = base.replace("{downloads}", CONFIG.caminho_downloads)
        path = Path(base)
        if not path.is_absolute():
            path = Path(CONFIG.caminho_base) / path
        return str(path.resolve())

    def _registrar_latencia(self, etapa: str, ms: float, extra: Optional[dict[str, Any]] = None) -> None:
        entrada = {"ts": datetime.now().isoformat(timespec="seconds"), "etapa": etapa, "ms": round(ms, 2), **(extra or {})}
        self._latencia_hist.append(entrada)
        os.makedirs(CONFIG.caminho_logs, exist_ok=True)
        try:
            with open(CONFIG.caminho_log_latencia, "a", encoding="utf-8") as arq:
                arq.write(json.dumps(entrada, ensure_ascii=False) + "\n")
        except OSError:
            pass
        if ms > CONFIG.max_latencia_ms:
            self.logger.warning("[LATENCIA] %s: %.0fms", etapa, ms)

    def _diagnostico_performance(self, ultimos: int = 10) -> str:
        amostra = self._latencia_hist[-max(1, ultimos):]
        if not amostra:
            return "Sem dados de latencia."
        linhas = ["Relatorio de latencia:"]
        for item in amostra:
            estado = "ALTO" if item["ms"] > CONFIG.max_latencia_ms else "ok"
            linhas.append(f"- {item['ts']} | {item['etapa']}: {item['ms']}ms [{estado}]")
        media = sum(x["ms"] for x in amostra) / len(amostra)
        linhas.append(f"Media: {media:.1f}ms")
        return "\n".join(linhas)

    def _bloco_comando_perigoso(self, cmd: str) -> bool:
        c = (cmd or "").strip().lower()
        return any(token in c for token in COMANDOS_DESTRUTIVOS)

    def _arquivo_existe(self, caminho: str) -> bool:
        return Path(caminho).exists()

    def _resolver_caminho_arquivo(self, caminho: str) -> Path:
        """
        Resolve caminho com fallback inteligente:
        - caminho absoluto/relativo normal
        - nome curto sem extensão em pastas base conhecidas
        - tentativa com extensões comuns
        """
        bruto = (caminho or "").strip().strip('"').strip("'")
        destino = Path(self._normalizar_caminho_usuario(bruto))
        if destino.exists():
            return destino

        nome = Path(bruto).name
        if not nome:
            return destino
        exts = ["", ".md", ".txt", ".py", ".json"]
        bases = [
            Path(CONFIG.caminho_base),
            Path(CONFIG.caminho_database),
            Path(CONFIG.caminho_vault),
            Path(CONFIG.caminho_vault) / "Database",
        ]
        for pasta in bases:
            for ext in exts:
                cand = pasta / f"{nome}{ext}" if not Path(nome).suffix else pasta / nome
                if cand.exists():
                    return cand
        return destino

    def _eh_saudacao(self, entrada: str) -> bool:
        t = _normalizar_texto_bruto(entrada)
        if not t:
            return False
        if t in {"oi", "ola", "bom dia", "boa tarde", "boa noite"}:
            return True
        if any(t.startswith(x) for x in ("oi ", "ola ", "e ai", "fala ", "salve ")):
            return True
        if "robertoo" in t and any(x in t for x in ("oi", "ola", "bom dia", "boa tarde", "boa noite")):
            return True
        return False

    def _eh_conversa_casual(self, entrada: str) -> bool:
        t = _normalizar_texto_bruto(entrada)
        if not t:
            return False
        if self._eh_saudacao(t):
            return True
        sinais = (
            "tudo bem", "como voce esta", "como vai", "obrigado", "valeu",
            "bom trabalho", "legal", "kkk", "haha", "boa", "blz",
        )
        return any(s in t for s in sinais)

    def _pode_executar_tools(self, entrada: str) -> bool:
        t = _normalizar_texto_bruto(entrada)
        if not t:
            return False
        if self._eh_conversa_casual(t):
            return False
        if t.startswith("/"):
            return False
        if any(t.startswith(prefixo) for prefixo in PREFIXOS_FERRAMENTA.keys()):
            return True
        verbos_acao = (
            "criar", "crie", "ler", "leia", "abrir", "abra", "executar", "execute",
            "mover", "apagar", "excluir", "baixar", "salvar", "editar", "edite",
            "powershell", "cmd", "terminal", "abrir arquivo", "criar pasta",
        )
        return any(v in t for v in verbos_acao)

    def _resposta_saudacao(self) -> str:
        return (
            f"Ola, {self.nome_usuario}. Estou pronto.\n"
            "Use /help para ver comandos ou mande algo como:\n"
            "- ler_arquivo: c:\\agenteIA\\BrainTwo\\Database\\Regras_do_RobertoO.md\n"
            "- powershell: Get-Date"
        )

    def _resposta_casual(self) -> str:
        return "Tudo certo por aqui. Quando quiser executar algo, use /help ou um comando com prefixo."

    async def _fetch_url(self, session: Any, url: str) -> str:
        if not (AIOHTTP_OK and BS4_OK):
            return ""
        t0 = time.perf_counter()
        try:
            timeout = aiohttp.ClientTimeout(total=10, connect=3)
            async with session.get(url, timeout=timeout) as res:
                html = await res.text(errors="replace")
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            texto = soup.get_text(" ", strip=True)
            self._registrar_latencia("web_fetch", (time.perf_counter() - t0) * 1000, {"url": url})
            return _truncar(f"[PAGINA {url}]\n{texto}", 4000)
        except Exception:
            return ""

    async def _processar_entradas_extras(self, pergunta: str) -> str:
        urls = re.findall(r"(https?://[^\s]+)", pergunta or "")
        if not urls or not AIOHTTP_OK:
            return pergunta
        async with aiohttp.ClientSession(headers={"User-Agent": "RobertoO/4.0"}) as sessao:
            extras = await asyncio.gather(*[self._fetch_url(sessao, u) for u in urls], return_exceptions=True)
        anexos = "\n\n".join(x for x in extras if isinstance(x, str) and x)
        if not anexos:
            return pergunta
        return _truncar(pergunta + "\n\nContexto web:\n" + anexos, CONFIG.max_ctx_chars)

    async def consultar_memoria(self, pergunta: str) -> str:
        if self.db is None:
            return ""
        chave = (pergunta or "").strip().lower()
        if chave in self.cache_memoria:
            return self.cache_memoria[chave]
        t0 = time.perf_counter()
        try:
            with self._db_lock:
                # Evita warning de relevancia fora de faixa; usamos busca sem score.
                docs = self.db.similarity_search(pergunta, k=5)
            self._registrar_latencia("rag_busca", (time.perf_counter() - t0) * 1000)
        except Exception as exc:
            self.logger.warning("[RAG] Falha de consulta: %s", exc)
            return ""
        if not docs:
            return ""
        partes: list[str] = []
        for doc in docs[:3]:
            origem = doc.metadata.get("nome_arquivo", "desconhecido")
            partes.append(f"[{origem}] {doc.page_content}")
        texto = _truncar("\n---\n".join(partes), 3000)
        self.cache_memoria[chave] = texto
        return texto

    def _rastrear(self, acao: str, alvo: str) -> None:
        self.logger.info("[ACAO] %s | %s", acao, alvo)

    def _criar_arquivo(self, caminho: str, conteudo: str) -> str:
        destino = self._normalizar_caminho_usuario(caminho)
        if not Path(destino).suffix:
            return "ERRO: informe extensao do arquivo."
        try:
            Path(destino).parent.mkdir(parents=True, exist_ok=True)
            Path(destino).write_text(conteudo or "", encoding="utf-8")
            self._rastrear("criar_arquivo", destino)
            return f"OK: arquivo criado em {destino}"
        except OSError as exc:
            return f"ERRO: nao foi possivel criar arquivo ({exc})"

    def _editar_codigo(self, caminho: str, busca: str, troca: str) -> str:
        destino = self._normalizar_caminho_usuario(caminho)
        if not self._arquivo_existe(destino):
            return f"ERRO: arquivo nao encontrado ({destino})"
        try:
            original = Path(destino).read_text(encoding="utf-8", errors="replace")
            if busca not in original:
                return "ERRO: trecho de busca nao encontrado."
            if original.count(busca) > 1:
                return "ERRO: trecho ambiguo; forneca contexto unico."
            Path(destino).write_text(original.replace(busca, troca), encoding="utf-8")
            self._rastrear("editar_codigo", destino)
            return f"OK: arquivo editado ({destino})"
        except OSError as exc:
            return f"ERRO: falha ao editar ({exc})"

    def _ler_arquivo(self, caminho: str) -> str:
        destino = str(self._resolver_caminho_arquivo(caminho))
        if not self._arquivo_existe(destino):
            return f"ERRO: arquivo nao encontrado ({destino})"
        try:
            conteudo = Path(destino).read_text(encoding="utf-8", errors="replace")
            return _truncar(conteudo, 4500)
        except OSError as exc:
            return f"ERRO: falha ao ler arquivo ({exc})"

    def _excluir(self, caminho: str) -> str:
        destino = self._normalizar_caminho_usuario(caminho)
        if not self._arquivo_existe(destino):
            return f"ERRO: caminho nao encontrado ({destino})"
        confirmar = input(f"Confirmar exclusao de '{destino}'? (s/N): ").strip().lower()
        if confirmar != "s":
            return "Operacao cancelada."
        try:
            if Path(destino).is_dir():
                shutil.rmtree(destino)
            else:
                Path(destino).unlink()
            self._rastrear("excluir", destino)
            return f"OK: excluido ({destino})"
        except OSError as exc:
            return f"ERRO: falha ao excluir ({exc})"

    def _criar_pasta(self, caminho: str) -> str:
        destino = self._normalizar_caminho_usuario(caminho)
        try:
            Path(destino).mkdir(parents=True, exist_ok=True)
            self._rastrear("criar_pasta", destino)
            return f"OK: pasta criada ({destino})"
        except OSError as exc:
            return f"ERRO: falha ao criar pasta ({exc})"

    def _mover(self, origem: str, destino: str) -> str:
        src = self._normalizar_caminho_usuario(origem)
        dst = self._normalizar_caminho_usuario(destino)
        if not self._arquivo_existe(src):
            return f"ERRO: origem nao encontrada ({src})"
        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            saida = shutil.move(src, dst)
            self._rastrear("mover", f"{src} -> {saida}")
            return f"OK: movido para {saida}"
        except (OSError, shutil.Error) as exc:
            return f"ERRO: falha ao mover ({exc})"

    def _baixar(self, url: str, nome_arquivo_destino: str) -> str:
        nome = Path((nome_arquivo_destino or "").strip('"').strip("'")).name
        if not nome:
            return "ERRO: nome de destino invalido."
        destino = Path(CONFIG.caminho_downloads) / nome
        Path(CONFIG.caminho_downloads).mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        try:
            urllib.request.urlretrieve(url, destino)
            self._registrar_latencia("download", (time.perf_counter() - t0) * 1000, {"url": url})
            self._rastrear("baixar", str(destino))
            return f"OK: download concluido em {destino}"
        except urllib.error.URLError as exc:
            return f"ERRO: download falhou ({exc})"

    def _executar_comando(self, cmd_ou_url: str) -> str:
        cmd = (cmd_ou_url or "").strip()
        if not cmd:
            return "ERRO: comando vazio."
        if cmd.startswith(("http://", "https://")):
            webbrowser.open(cmd)
            self._rastrear("open_url", cmd)
            return f"OK: URL aberta ({cmd})"
        primeiro = Path(cmd.split()[0].strip('"').strip("'")).stem.lower()
        primeiro = ATALHOS_APPS.get(primeiro, primeiro)
        if primeiro in {"vscode"}:
            primeiro = "code"
        if primeiro in {"telegramdesktop"}:
            primeiro = "telegram"
        if primeiro not in APPS_PERMITIDOS:
            return f"ERRO: aplicativo nao permitido ({primeiro})"
        try:
            subprocess.Popen(f"start {cmd}", shell=True)
            self._rastrear("execute", cmd)
            return f"OK: aplicativo iniciado ({cmd})"
        except OSError as exc:
            return f"ERRO: nao foi possivel iniciar ({exc})"

    def _executar_powershell(self, comando: str) -> str:
        if self._bloco_comando_perigoso(comando):
            return "ERRO: comando bloqueado por seguranca."
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", comando],
                capture_output=True,
                text=True,
                timeout=35,
            )
            self._registrar_latencia("powershell", (time.perf_counter() - t0) * 1000)
            output = proc.stdout.strip() or proc.stderr.strip() or "(sem saida)"
            self._rastrear("powershell", comando)
            return "PS> " + _truncar(output, 1800)
        except subprocess.TimeoutExpired:
            return "ERRO: timeout no PowerShell (>35s)."
        except FileNotFoundError:
            return "ERRO: PowerShell nao encontrado."

    def _automacao(self, instrucoes: str) -> str:
        if not PYAUTOGUI_OK:
            return "ERRO: pyautogui nao instalado."
        confirm = input("Permitir automacao de mouse/teclado? (s/N): ").strip().lower()
        if confirm != "s":
            return "Operacao cancelada."
        passos = [x.strip() for x in (instrucoes or "").split(">>") if x.strip()]
        if not passos:
            return "ERRO: nenhuma instrucao valida."
        Path(CONFIG.caminho_downloads).mkdir(parents=True, exist_ok=True)
        for passo in passos:
            uc = passo.upper()
            try:
                if uc.startswith("ABRIR "):
                    pyautogui.hotkey("win", "s")
                    time.sleep(0.7)
                    pyautogui.write(passo[6:])
                    pyautogui.press("enter")
                elif uc.startswith("ESPERAR "):
                    time.sleep(float(passo[8:].strip()))
                elif uc.startswith("TECLA "):
                    pyautogui.press(passo[6:].strip().lower())
                elif uc.startswith("HOTKEY "):
                    keys = passo[7:].replace("+", " ").split()
                    pyautogui.hotkey(*[k.lower() for k in keys])
                elif uc.startswith("ESCREVER "):
                    pyautogui.write(passo[9:], interval=0.05)
                elif uc.startswith("CLICAR "):
                    x, y = [int(v.strip()) for v in passo[7:].split(",")]
                    pyautogui.click(x, y)
                elif uc.startswith("MOVER "):
                    x, y = [int(v.strip()) for v in passo[6:].split(",")]
                    pyautogui.moveTo(x, y, duration=0.5)
                elif uc.startswith("SCREENSHOT"):
                    nome = "screenshot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
                    caminho = str(Path(CONFIG.caminho_downloads) / nome)
                    pyautogui.screenshot(caminho)
                    return f"OK: screenshot salva em {caminho}"
                else:
                    return f"ERRO: passo desconhecido ({passo})"
            except Exception as exc:
                return f"ERRO: falha no passo '{passo}' ({exc})"
        return f"OK: automacao concluida ({len(passos)} passos)"

    def _notificar_telegram(self, mensagem: str) -> str:
        token = CONFIG.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = CONFIG.telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return "ERRO: configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."
        payload = json.dumps({"chat_id": chat_id, "text": mensagem, "parse_mode": "Markdown"})
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if data.get("ok"):
                return "OK: mensagem enviada ao Telegram."
            return f"ERRO: Telegram API retornou falha ({data})"
        except Exception as exc:
            return f"ERRO: falha ao enviar Telegram ({exc})"

    def _consultar_api(self, url: str, headers_json: str = "{}") -> str:
        try:
            headers = json.loads(headers_json or "{}")
            req = urllib.request.Request(url, headers={"User-Agent": "RobertoO/4.0", **headers})
            with urllib.request.urlopen(req, timeout=15) as resp:
                texto = resp.read().decode("utf-8", errors="replace")
            return _truncar(texto, 3000)
        except Exception as exc:
            return f"ERRO: falha na consulta ({exc})"

    def _monitorar_sistema(self) -> str:
        if not PSUTIL_OK:
            return "ERRO: psutil nao instalado."
        cpu = psutil.cpu_percent(interval=1)
        ram = psutil.virtual_memory()
        disco = psutil.disk_usage("C:\\")
        return (
            f"CPU: {cpu:.1f}%\n"
            f"RAM: {ram.percent:.1f}% ({ram.used // 1024**2}MB/{ram.total // 1024**2}MB)\n"
            f"DISCO C: {disco.percent:.1f}% ({disco.free // 1024**3}GB livres)"
        )

    def _salvar_artigo(self, titulo: str, conteudo: str) -> str:
        nome = (titulo or "artigo").strip()
        if not nome.lower().endswith(".md"):
            nome += ".md"
        caminho = Path(CONFIG.caminho_database) / nome
        try:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(conteudo or "", encoding="utf-8")
            self._rastrear("salvar_artigo", str(caminho))
            return f"OK: artigo salvo em {caminho}"
        except OSError as exc:
            return f"ERRO: nao foi possivel salvar artigo ({exc})"

    def _aprender_regra(self, regra: str) -> str:
        Path(CONFIG.caminho_database).mkdir(parents=True, exist_ok=True)
        linha = f"- [{datetime.now().strftime('%d/%m/%Y %H:%M')}] {regra.strip()}\n"
        try:
            with open(CONFIG.caminho_log_aprendizado, "a", encoding="utf-8") as arq:
                arq.write(linha)
            return "OK: regra registrada."
        except OSError as exc:
            return f"ERRO: nao foi possivel registrar regra ({exc})"

    def _status(self) -> str:
        llm_on = "sim" if self.llm_with_tools is not None else "nao"
        rag_on = "sim" if self.db is not None else "nao"
        return (
            f"LLM ativo: {llm_on}\n"
            f"RAG ativo: {rag_on}\n"
            f"Watchdog: {'sim' if self.observer else 'nao'}\n"
            f"Base: {CONFIG.caminho_base}\n"
            f"Vault: {CONFIG.caminho_vault}"
        )

    async def _router_deterministico(self, entrada: str) -> Optional[str]:
        texto = (entrada or "").strip()
        if not texto:
            return "Comando vazio."

        if self._eh_saudacao(texto):
            return self._resposta_saudacao()
        if self._eh_conversa_casual(texto):
            return self._resposta_casual()

        low = texto.lower()
        if low in {"/quit", "/sair", "sair", "exit", "quit"}:
            self._running = False
            return "Encerrando..."
        if low == "/help":
            return (
                "Comandos rapidos:\n"
                "- /help /status /tools /reindex /clear /quit\n"
                "- execute: notepad\n"
                "- powershell: Get-Process | Select-Object -First 5\n"
                "- ler_arquivo: caminho\n"
                "- criar_arquivo: caminho | conteudo"
            )
        if low == "/status":
            return self._status()
        if low == "/tools":
            return "Ferramentas: " + ", ".join(sorted(FERRAMENTAS_MAP.keys()))
        if low == "/clear":
            os.system("cls")
            self.ui.banner(self.nome_usuario)
            return "Tela limpa."
        if low == "/reindex":
            if self.embeddings is None:
                return "ERRO: embeddings indisponiveis."
            with self._db_lock:
                self.db = reindexar_direto(
                    self.logger,
                    self.embeddings,
                    vault_dir=CONFIG.caminho_vault,
                    persist_dir=CONFIG.caminho_vector_db,
                )
            return "OK: reindexacao concluida."

        for prefixo, nome_tool in PREFIXOS_FERRAMENTA.items():
            if low.startswith(prefixo):
                payload = texto[len(prefixo):].strip()
                return self._executar_prefixo_tool(nome_tool, payload)
        return None

    def _executar_prefixo_tool(self, nome_tool: str, payload: str) -> str:
        if nome_tool == "ler_arquivo":
            return self._ler_arquivo(payload)
        if nome_tool == "criar_pasta":
            return self._criar_pasta(payload)
        if nome_tool == "excluir":
            return self._excluir(payload)
        if nome_tool == "execute":
            return self._executar_comando(payload)
        if nome_tool == "powershell":
            return self._executar_powershell(payload)
        if nome_tool == "notificar_telegram":
            return self._notificar_telegram(payload)
        if nome_tool == "criar_arquivo":
            if "|" not in payload:
                return "ERRO: use criar_arquivo: caminho | conteudo"
            caminho, conteudo = payload.split("|", 1)
            return self._criar_arquivo(caminho.strip(), conteudo.lstrip())
        if nome_tool == "mover":
            if "|" not in payload:
                return "ERRO: use mover: origem | destino"
            origem, destino = payload.split("|", 1)
            return self._mover(origem.strip(), destino.strip())
        if nome_tool == "baixar":
            if "|" not in payload:
                return "ERRO: use baixar: url | nome_arquivo"
            url, nome = payload.split("|", 1)
            return self._baixar(url.strip(), nome.strip())
        return f"ERRO: prefixo nao mapeado ({nome_tool})"

    def _extrair_chamada_tool_textual(self, conteudo: str) -> Optional[tuple[str, dict[str, Any]]]:
        texto = (conteudo or "").strip()
        if not texto:
            return None
        blocos = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", texto, flags=re.IGNORECASE)
        candidatos = blocos + [texto]
        for candidato in candidatos:
            bruto = candidato.strip()
            if bruto.startswith("{"):
                try:
                    payload = json.loads(bruto)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    nome = payload.get("name") or payload.get("tool")
                    argumentos = payload.get("arguments", payload.get("args", {}))
                    if isinstance(nome, str):
                        if not isinstance(argumentos, dict):
                            argumentos = {}
                        return nome.strip(), argumentos

            dsl = re.match(r"^\s*([A-Z_]+)\s*:\s*(.+)$", bruto, flags=re.DOTALL)
            if not dsl:
                continue
            keyword = dsl.group(1).strip().upper()
            value = dsl.group(2).strip()
            if keyword == "LER_ARQUIVO":
                return "ler_arquivo", {"caminho": value}
            if keyword == "CRIAR_PASTA":
                return "criar_pasta", {"caminho": value}
            if keyword == "EXECUTE":
                return "execute", {"cmd_ou_url": value}
            if keyword == "POWERSHELL":
                return "powershell", {"comando": value}
        return None

    async def _executar_com_llm(self, pergunta: str, memoria: str) -> Optional[str]:
        if self.llm_with_tools is None:
            return None
        permitir_tools = self._pode_executar_tools(pergunta)
        sistema = (
            "Voce e RobertoO, agente local no Windows.\n"
            "Se precisar agir no sistema, use ferramenta.\n"
            "Nunca invente caminhos; use caminhos fornecidos pelo usuario.\n"
            "Quando o usuario pedir leitura de arquivo, prefira ler_arquivo com caminho literal."
        )
        entrada = pergunta
        if memoria:
            entrada += "\n\nMemoria relevante:\n" + memoria
        t0 = time.perf_counter()
        resposta = self.llm_with_tools.invoke([SystemMessage(content=sistema), HumanMessage(content=entrada)])
        self._registrar_latencia("llm_resposta", (time.perf_counter() - t0) * 1000)
        chamadas = getattr(resposta, "tool_calls", []) or []
        if chamadas and not permitir_tools:
            return "Entendi. Estou bem e pronto para ajudar. Para executar acao, use /help."
        if not chamadas:
            conteudo = (getattr(resposta, "content", "") or "").strip()
            fallback = self._extrair_chamada_tool_textual(conteudo)
            if fallback and not permitir_tools:
                return "Entendi. Quando quiser executar acao no sistema, use um comando explicito."
            if not fallback:
                return conteudo
            nome, args = fallback
            tool_fn = FERRAMENTAS_MAP.get(nome)
            if not tool_fn:
                return f"Ferramenta desconhecida: {nome}"
            try:
                retorno = tool_fn.invoke(args)
            except Exception as exc:
                retorno = f"ERRO na ferramenta {nome}: {exc}"
            return f"{nome}: {retorno}"

        resultados: list[str] = []
        for chamada in chamadas:
            nome = chamada.get("name", "")
            args = chamada.get("args", {})
            tool_fn = FERRAMENTAS_MAP.get(nome)
            if not tool_fn:
                resultados.append(f"Ferramenta desconhecida: {nome}")
                continue
            try:
                retorno = tool_fn.invoke(args)
            except Exception as exc:
                retorno = f"ERRO na ferramenta {nome}: {exc}"
            resultados.append(f"{nome}: {retorno}")
        msg_tools = "\n".join(resultados)
        if ToolMessage is None:
            return msg_tools
        segunda = self.llm.invoke(
            [
                SystemMessage(content=sistema),
                HumanMessage(content=entrada),
                resposta,
                ToolMessage(content=msg_tools, tool_call_id="tools_result"),
            ]
        )
        return (getattr(segunda, "content", "") or msg_tools).strip()

    def _modo_sem_llm(self, pergunta: str) -> str:
        retorno = self._executar_prefixo_tool("powershell", "Get-Date")
        if pergunta.strip().lower() in {"oi", "ola", "olá"}:
            return f"Modo sem LLM ativo. Exemplo rapido:\n{retorno}\nUse /help."
        return "Modo sem LLM ativo. Use comandos por prefixo (execute:, powershell:, ler_arquivo:, ...)."

    async def iniciar_ambiente(self) -> None:
        if self._iniciou:
            return
        self.ui.info("Inicializando ambiente...")
        Path(CONFIG.caminho_downloads).mkdir(parents=True, exist_ok=True)
        Path(CONFIG.caminho_database).mkdir(parents=True, exist_ok=True)
        if LANGCHAIN_CORE_OK:
            try:
                from langchain_huggingface import HuggingFaceEmbeddings
                from langchain_ollama import ChatOllama

                self.embeddings = HuggingFaceEmbeddings(model_name=CONFIG.modelo_embeddings)
                self.db = reindexar_direto(
                    self.logger,
                    self.embeddings,
                    vault_dir=CONFIG.caminho_vault,
                    persist_dir=CONFIG.caminho_vector_db,
                )
                self.llm = ChatOllama(
                    model=CONFIG.modelo_ollama,
                    temperature=CONFIG.temperatura,
                    num_ctx=4096,
                    num_predict=512,
                )
                self.llm_with_tools = self.llm.bind_tools(FERRAMENTAS)
                self.llm.invoke([HumanMessage(content="ok")])
                self.ui.ok(f"LLM conectado: {CONFIG.modelo_ollama}")
            except Exception as exc:
                self.llm = None
                self.llm_with_tools = None
                self.logger.warning("[LLM] Modo sem LLM: %s", exc)
                self.ui.warn("LLM indisponivel, continuando em modo deterministico.")
        else:
            self.ui.warn("langchain_core ausente; modo deterministico.")

        if WATCHDOG_OK and Observer and Path(CONFIG.caminho_vault).is_dir():
            self.observer = Observer()
            self.observer.schedule(VaultWatcher(), path=CONFIG.caminho_vault, recursive=True)
            self.observer.start()
            self.ui.ok("Watchdog ativo no vault.")
        self._iniciou = True
        self.ui.ok("Agente pronto.")

    async def processar(self, pergunta: str) -> str:
        inicio = time.perf_counter()
        rota = await self._router_deterministico(pergunta)
        if rota is not None:
            self._registrar_latencia("router_direto", (time.perf_counter() - inicio) * 1000)
            return rota
        texto = await self._processar_entradas_extras(pergunta)
        memoria = await self.consultar_memoria(texto)
        resposta = await self._executar_com_llm(texto, memoria)
        if resposta is None:
            resposta = self._modo_sem_llm(texto)
        self._turnos_memoria.append((pergunta, resposta))
        self._turnos_memoria = self._turnos_memoria[-self.max_turnos_memoria:]
        return resposta

    async def iniciar(self) -> None:
        await self.iniciar_ambiente()
        self.ui.banner(self.nome_usuario)
        self._running = True
        while self._running:
            try:
                entrada = input(self.ui.user_prompt()).strip()
            except EOFError:
                break
            if not entrada:
                continue
            try:
                resposta = await self.processar(entrada)
            except Exception as exc:
                self.logger.error("[ERRO] Falha no processamento: %s", exc, exc_info=True)
                resposta = f"Erro interno: {exc}"
            self.ui.agent(resposta)

    def parar(self) -> None:
        self._running = False
        if self.observer is not None:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass
        self.logger.info("[SISTEMA] Encerrado.")


@tool
def editar_codigo(caminho: str, busca: str, troca: str) -> str:
    """Substitui trecho unico em arquivo."""
    return _get_agent()._editar_codigo(caminho, busca, troca)


@tool
def ler_arquivo(caminho: str) -> str:
    """Le arquivo de texto."""
    return _get_agent()._ler_arquivo(caminho)


@tool
def aprender_regra(regra: str) -> str:
    """Registra regra/preferecia no log de aprendizado."""
    return _get_agent()._aprender_regra(regra)


@tool
def salvar_artigo(titulo: str, conteudo: str) -> str:
    """Salva markdown em Database."""
    return _get_agent()._salvar_artigo(titulo, conteudo)


@tool
def criar_arquivo(caminho: str, conteudo: str) -> str:
    """Cria ou sobrescreve arquivo."""
    return _get_agent()._criar_arquivo(caminho, conteudo)


@tool
def criar_pasta(caminho: str) -> str:
    """Cria pasta recursivamente."""
    return _get_agent()._criar_pasta(caminho)


@tool
def excluir(caminho: str) -> str:
    """Exclui arquivo/pasta com confirmacao."""
    return _get_agent()._excluir(caminho)


@tool
def mover(origem: str, destino: str) -> str:
    """Move arquivo/pasta."""
    return _get_agent()._mover(origem, destino)


@tool
def baixar(url: str, nome_arquivo_destino: str) -> str:
    """Baixa arquivo para downloads."""
    return _get_agent()._baixar(url, nome_arquivo_destino)


@tool
def automacao(instrucoes: str) -> str:
    """Executa sequencia pyautogui via DSL de passos."""
    return _get_agent()._automacao(instrucoes)


@tool
def execute(cmd_ou_url: str) -> str:
    """Abre app permitido ou URL."""
    return _get_agent()._executar_comando(cmd_ou_url)


@tool
def powershell(comando: str) -> str:
    """Executa comando powershell seguro."""
    return _get_agent()._executar_powershell(comando)


@tool
def notificar_telegram(mensagem: str) -> str:
    """Envia notificacao Telegram."""
    return _get_agent()._notificar_telegram(mensagem)


@tool
def consultar_api(url: str, headers_json: str = "{}") -> str:
    """Consulta API HTTP GET."""
    return _get_agent()._consultar_api(url, headers_json)


@tool
def diagnostico_performance(ultimos: int = 10) -> str:
    """Retorna diagnostico de latencia."""
    return _get_agent()._diagnostico_performance(ultimos)


@tool
def monitorar_sistema() -> str:
    """Mede CPU/RAM/disco."""
    return _get_agent()._monitorar_sistema()


FERRAMENTAS = [
    editar_codigo,
    ler_arquivo,
    aprender_regra,
    salvar_artigo,
    criar_arquivo,
    criar_pasta,
    excluir,
    mover,
    baixar,
    automacao,
    execute,
    powershell,
    notificar_telegram,
    consultar_api,
    diagnostico_performance,
    monitorar_sistema,
]
FERRAMENTAS_MAP = {f.name: f for f in FERRAMENTAS}


if __name__ == "__main__":
    agente: Optional[RobertoO] = None
    try:
        agente = RobertoO()
        asyncio.run(agente.iniciar())
    except KeyboardInterrupt:
        print("\n[SISTEMA] Interrompido pelo usuario.")
    except Exception as exc:
        logging.getLogger("RobertoO").critical("[FATAL] %s", exc, exc_info=True)
        print(f"\n[ERRO] Falha ao iniciar: {exc}")
    finally:
        if agente is not None:
            agente.parar()
