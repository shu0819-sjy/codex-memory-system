# -*- coding: utf-8 -*-
"""Codex PreToolUse 安全钩子。

只拦截两类高风险行为：输出敏感凭据文件、执行高破坏性命令。
无法解析事件或无法确定风险时放行，避免钩子故障阻塞正常开发。
"""
import json
import re
import sys


SENSITIVE_PATH = re.compile(
    r"(?:^|[\\/\s'\"])(?:\.env(?:\.[^\s'\"]+)?|"
    r"[^\\/\s'\"]*(?:\.pem|\.key|\.p12|\.pfx)|"
    r"(?:id_rsa|id_ed25519|credentials|secrets?|private[_-]?key|auth\.json)"
    r")(?:$|[\\/\s'\"])",
    re.IGNORECASE,
)
SENSITIVE_READ = re.compile(
    r"(?:get-content|set-content|type\s+|cat\s+|more\s+|head\s+|tail\s+|"
    r"select-string|read-file|read_text|open\s*\(|copy-item|copy\s+)",
    re.IGNORECASE,
)
DESTRUCTIVE_COMMAND = re.compile(
    r"(?:git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f|"
    r"remove-item|rmdir|rd\s|del\s|rm\s+-r|format-volume|diskpart|"
    r"stop-process|taskkill|shutdown(?:\.exe)?|restart-computer)",
    re.IGNORECASE,
)


def emit(payload):
    """输出一个符合钩子契约的 JSON 对象。"""
    print(json.dumps(payload, ensure_ascii=False))


def read_event():
    """读取并解析标准输入事件；解析失败时返回空对象。"""
    try:
        raw = sys.stdin.read()
        return json.loads(raw or "{}")
    except (OSError, TypeError, ValueError):
        return {}


def get_tool_text(event):
    """提取工具名和工具参数中的文本，避免执行任意对象访问。"""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    values = [str(event.get("tool_name", ""))]
    for key in ("command", "cmd", "file_path", "path", "file", "content"):
        value = tool_input.get(key)
        if isinstance(value, str):
            values.append(value)
    paths = tool_input.get("paths")
    if isinstance(paths, list):
        values.extend(str(item) for item in paths[:20])
    return " ".join(values)


def is_sensitive_read(text):
    """判断文本是否同时包含敏感路径和读取/复制行为。"""
    return bool(SENSITIVE_PATH.search(text) and SENSITIVE_READ.search(text))


def is_destructive_command(text):
    """判断文本是否命中高破坏性命令模式。"""
    return bool(DESTRUCTIVE_COMMAND.search(text))


def main():
    """处理一次 PreToolUse 事件，命中风险时阻止，否则放行。"""
    event = read_event()
    text = get_tool_text(event)
    if is_sensitive_read(text):
        emit({
            "continue": False,
            "stopReason": "已阻止：工具调用可能读取或复制敏感凭据文件。",
            "systemMessage": (
                "检测到 .env、私钥、凭据或令牌文件的读取/复制操作。"
                "请改用脱敏内容，或由用户明确确认后再执行。"
            ),
        })
        return
    if is_destructive_command(text):
        emit({
            "continue": False,
            "stopReason": "已阻止：工具调用包含高破坏性命令。",
            "systemMessage": (
                "检测到删除、强制重置、停止进程或关机等高风险命令。"
                "请先向用户说明目标、影响范围和回退方式，再由用户确认。"
            ),
        })
        return
    emit({"continue": True})


if __name__ == "__main__":
    main()
