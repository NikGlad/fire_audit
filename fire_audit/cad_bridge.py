"""Bounded MCP client for the installed AUTOM8LABS bridge; no COM/LISP fallback."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


BRIDGE_NAME = "AUTOM8LABS MCP Connector for AutoCAD"
DEFAULT_COMMAND = r"C:\Program Files\Autodesk\ApplicationPlugins\AUTOM8LABS.MCPConnectorAutoCAD.bundle\Contents\Bridge\MCPConnectorAutoCAD.Bridge.exe"
READ_TOOLS = {"ping", "get_drawing_info", "get_entity_properties", "get_bounding_box",
              "get_entities", "list_layers", "list_text_styles", "list_dimension_styles"}


def unwrap(result: dict) -> Any:
    if result.get("isError"):
        raise RuntimeError(str(result.get("content", "Ошибка коннектора")))
    value = result.get("structuredContent")
    if value is None:
        texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
        if len(texts) != 1:
            raise RuntimeError("Коннектор вернул неподдерживаемый ответ")
        try:
            value = json.loads(texts[0])
        except ValueError as exc:
            raise RuntimeError("Коннектор вернул ответ без структурированных данных") from exc
    while isinstance(value, dict):
        if value.get("success") is False or value.get("errors") or value.get("error"):
            raise RuntimeError(str(value))
        if "result" not in value:
            break
        value = value["result"]
    return value


class AutoCADBridge:
    """A new, short-lived official bridge process per operation group.

    Never retries a write. A timeout is an uncertain outcome, not permission to retry.
    The application must apply its policy and obtain GUI consent before call_write.
    """
    def __init__(self, command: str = DEFAULT_COMMAND, timeout: float = 30):
        self.command, self.timeout = command, timeout
        self.process = None
        self.messages: queue.Queue = queue.Queue()
        self.request_id = 0

    def __enter__(self):
        if not Path(self.command).is_file():
            raise FileNotFoundError("Не найден AUTOM8LABS Bridge. Укажите путь в config/control.json")
        self.process = subprocess.Popen(
            [self.command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="strict", bufsize=1, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        def reader():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            except Exception as exc:
                self.messages.put(exc)
            finally:
                self.messages.put(EOFError("Связь с AUTOM8LABS закрыта"))
        threading.Thread(target=reader, daemon=True).start()
        try:
            result = self.request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "fire-audit-control", "version": "0.2.0"},
            })
            if result.get("protocolVersion") not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
                raise RuntimeError("Неподдерживаемая версия MCP коннектора")
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def send(self, message: dict):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.request_id += 1
        request_id = self.request_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                msg = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError("Нет ответа AUTOM8LABS. Изменяющую операцию нельзя повторять без сверки DWG.") from exc
            if isinstance(msg, Exception):
                raise msg
            if "method" in msg:
                if "id" in msg:
                    self.send({"jsonrpc": "2.0", "id": msg["id"], "error": {
                        "code": -32601, "message": "Запрос сервера не поддержан; автоматическое согласие запрещено"}})
                continue
            if msg.get("id") == request_id:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg["result"]
            if time.monotonic() >= deadline:
                raise TimeoutError("Истекло время ожидания AUTOM8LABS")

    def call(self, name: str, args: dict | None = None) -> Any:
        if name not in READ_TOOLS:
            raise ValueError("Чтение: изменяющий инструмент запрещён")
        return unwrap(self.request("tools/call", {"name": name, "arguments": args or {}}))

    def call_write(self, name: str, args: dict) -> Any:
        # Only the guarded executor calls this method; never expose it as an MCP tool.
        return unwrap(self.request("tools/call", {"name": name, "arguments": args}))

    def __exit__(self, *args):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            finally:
                if self.process.stdout:
                    self.process.stdout.close()


def drawing_identity(info: dict) -> str:
    value = str(info.get("path", "")).strip()
    if not value or not Path(value).is_absolute():
        raise ValueError("Коннектор не подтвердил полный путь открытого DWG")
    return os.path.normcase(os.path.abspath(value))


def assert_drawing(info: dict, expected: str):
    if drawing_identity(info) != os.path.normcase(os.path.abspath(expected)):
        raise ValueError("Открыт другой DWG. Переключитесь на исходный документ выбранного запуска.")
    if str(info.get("units", "")).lower() not in {"millimeters", "millimetres", "миллиметры", "4"}:
        raise ValueError("Единицы чертежа не подтверждены как миллиметры; автоматические обмеры остановлены")


def read_entities(client, handles: list[str]) -> dict[str, dict]:
    if not handles or len(handles) > 500 or len(set(handles)) != len(handles):
        raise ValueError("Нужны от 1 до 500 уникальных AutoCAD handles")
    result = client.call("get_entity_properties", {"handles": handles})
    entities = {str(e["handle"]).upper(): e for e in result.get("entities", [])}
    if set(entities) != {h.upper() for h in handles}:
        raise ValueError("Коннектор вернул не все запрошенные объекты; сверка не завершена")
    for handle, entity in entities.items():
        entity["bounds"] = client.call("get_bounding_box", {"handles": [handle]})
    return entities
