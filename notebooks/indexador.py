"""
indexador.py - indexacao markdown robusta para ChromaDB.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

DEFAULT_VAULT = Path(r"C:\agenteIA\BrainTwo")
DEFAULT_PERSIST_DIR = Path(r"C:\agenteIA\database")
MIN_CHUNK_LEN = 50
MAX_CHUNK_LEN = 1200
OVERLAP = 120


def _normalizar_texto(texto: str) -> str:
    return texto.replace("\r\n", "\n").replace("\r", "\n").strip()


def _chunk_bloco(bloco: str) -> list[str]:
    if len(bloco) <= MAX_CHUNK_LEN:
        return [bloco] if len(bloco) >= MIN_CHUNK_LEN else []
    saida: list[str] = []
    inicio = 0
    while inicio < len(bloco):
        fim = min(inicio + MAX_CHUNK_LEN, len(bloco))
        trecho = bloco[inicio:fim].strip()
        if len(trecho) >= MIN_CHUNK_LEN:
            saida.append(trecho)
        if fim >= len(bloco):
            break
        inicio = max(0, fim - OVERLAP)
    return saida


def _chunk_markdown(texto: str) -> list[str]:
    texto = _normalizar_texto(texto)
    if not texto:
        return []
    blocos_brutos = [b.strip() for b in texto.split("\n\n") if b.strip()]
    chunks: list[str] = []
    for bloco in blocos_brutos:
        chunks.extend(_chunk_bloco(bloco))
    if chunks:
        return chunks
    compacto = " ".join(texto.split())
    return _chunk_bloco(compacto)


def _coletar_docs(vault: Path, logger: logging.Logger) -> list[Any]:
    from langchain_core.documents import Document

    arquivos = [p for p in vault.rglob("*.md") if ".obsidian" not in p.parts]
    logger.info("[RAG] Arquivos markdown detectados: %d", len(arquivos))
    docs: list[Document] = []
    for md in arquivos:
        try:
            texto = md.read_text(encoding="utf-8", errors="replace")
            chunks = _chunk_markdown(texto)
            for idx, chunk in enumerate(chunks):
                docs.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            "nome_arquivo": md.stem,
                            "caminho": str(md),
                            "chunk": idx,
                            "mtime": md.stat().st_mtime,
                        },
                    )
                )
        except Exception as exc:
            logger.warning("[RAG] Falha ao ler %s: %s", md, exc)
    return docs


def reindexar_direto(
    logger: logging.Logger,
    embeddings: Any,
    vault_dir: str | Path = DEFAULT_VAULT,
    persist_dir: str | Path = DEFAULT_PERSIST_DIR,
    limpar_indice_antigo: bool = False,
) -> Optional[Any]:
    """
    Reindexa markdown do vault em Chroma e retorna vectorstore.
    """
    try:
        from langchain_chroma import Chroma
    except ImportError:
        logger.error("[RAG] Dependencia ausente: langchain-chroma.")
        return None

    vault = Path(vault_dir)
    destino = Path(persist_dir)
    if not vault.exists():
        logger.warning("[RAG] Vault nao encontrado: %s", vault)
        return None

    docs = _coletar_docs(vault, logger)
    if not docs:
        logger.warning("[RAG] Nenhum documento elegivel para indexacao.")
        return None

    if limpar_indice_antigo and destino.exists():
        try:
            shutil.rmtree(destino)
        except OSError as exc:
            logger.warning("[RAG] Nao foi possivel limpar indice antigo: %s", exc)

    destino.mkdir(parents=True, exist_ok=True)
    try:
        db = Chroma.from_documents(
            documents=docs,
            embedding=embeddings,
            persist_directory=str(destino),
        )
        logger.info("[RAG] Indexacao concluida: %d chunks em %s", len(docs), destino)
        return db
    except Exception as exc:
        logger.error("[RAG] Erro ao persistir Chroma: %s", exc)
        return None
