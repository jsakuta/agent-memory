from dataclasses import dataclass, field
from enum import Enum, auto
import json

class State(Enum):
    IDLE = auto()
    IN_TURN = auto()
    POST_COMPACT = auto()

@dataclass
class Exchange:
    user_text: str = ""
    assistant_texts: list[str] = field(default_factory=list)
    tools_used: set[str] = field(default_factory=set)
    files_touched: set[str] = field(default_factory=set)
    git_branch: str = ""
    api_tokens: int = 0
    timestamp: str = ""
    is_compact_summary: bool = False

def is_human_input(obj: dict) -> bool:
    """人間が直接入力した user メッセージか判定"""
    if obj.get("type") != "user":
        return False
    content = obj.get("message", {}).get("content", obj.get("content"))

    if isinstance(content, str):
        for prefix in ("<task-notification", "<local-command-caveat",
                       "<command-name>", "<local-command-stdout"):
            if content.startswith(prefix):
                return False
        return True

    if isinstance(content, list):
        has_text = False
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return False
            if block.get("type") == "text":
                text = block.get("text", "")
                if "[Request interrupted" in text:
                    return False
                if text.startswith("<ide_opened_file>"):
                    continue
                if text.strip():
                    has_text = True
        return has_text

    return False

def extract_human_text(obj: dict) -> str:
    content = obj.get("message", {}).get("content", obj.get("content"))
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if not text.startswith("<ide_opened_file>"):
                    texts.append(text)
        return "\n".join(texts)
    return ""

def parse_jsonl(filepath: str, offset: int = 0) -> tuple[list[Exchange], int]:
    """JSONL を FSM でパースし Exchange リストを返す。
    Returns: (exchanges, new_offset)
    NOTE: binary mode で開く（Windows \r\n のバイトオフセット不整合を回避）
    """
    with open(filepath, "rb") as f:
        f.seek(offset)
        raw = f.read()
    new_offset = offset + len(raw)
    text_lines = raw.decode("utf-8").splitlines()

    state = State.IDLE
    exchanges: list[Exchange] = []
    current: Exchange | None = None

    for line in text_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")
        subtype = obj.get("subtype", "")

        # Phase 1: 除外
        if msg_type in ("progress", "file-history-snapshot",
                        "queue-operation", "last-prompt", "custom-title"):
            continue

        # Phase 2: system
        if msg_type == "system":
            if subtype == "compact_boundary":
                if current and (current.user_text or current.assistant_texts):
                    exchanges.append(current)
                    current = None
                state = State.POST_COMPACT
            continue

        # Phase 3: user
        if msg_type == "user":
            content = obj.get("message", {}).get("content", obj.get("content"))

            # tool_result → スキップ（state維持）
            if isinstance(content, list):
                blocks = [b for b in content if isinstance(b, dict)]
                if all(b.get("type") == "tool_result" for b in blocks):
                    continue

            # POST_COMPACT: compact要約処理
            if state == State.POST_COMPACT:
                if isinstance(content, str) and content.startswith(
                        "This session is being continued"):
                    current = Exchange(
                        user_text=extract_human_text(obj),
                        is_compact_summary=True,
                        timestamp=obj.get("timestamp", ""),
                    )
                    exchanges.append(current)
                    current = None
                    state = State.IDLE
                    continue
                if not is_human_input(obj):
                    continue
                state = State.IDLE

            # 人間入力
            if is_human_input(obj):
                if current and current.user_text and not current.assistant_texts:
                    # アシスタント応答前の連続入力 → テキスト連結
                    current.user_text += "\n" + extract_human_text(obj)
                else:
                    if current and (current.user_text or current.assistant_texts):
                        exchanges.append(current)
                    current = Exchange(
                        user_text=extract_human_text(obj),
                        timestamp=obj.get("timestamp", ""),
                        git_branch=obj.get("gitBranch", ""),
                    )
                state = State.IN_TURN
                continue
            continue

        # Phase 4: assistant
        if msg_type == "assistant":
            if current is None:
                current = Exchange(timestamp=obj.get("timestamp", ""))
            state = State.IN_TURN

            message = obj.get("message", {})
            content = message.get("content", [])
            usage = message.get("usage", {})
            current.api_tokens += usage.get("output_tokens", 0)
            if not current.git_branch:
                current.git_branch = obj.get("gitBranch", "")

            if not isinstance(content, list) or len(content) == 0:
                continue
            block = content[0]
            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "")
                if text.strip():
                    current.assistant_texts.append(text)
            elif block_type == "tool_use":
                name = block.get("name", "")
                if name:
                    current.tools_used.add(name)
                inp = block.get("input", {})
                for key in ("file_path", "path"):
                    p = inp.get(key, "")
                    if p and not p.startswith("**"):
                        current.files_touched.add(p)
            # thinking → api_tokens のみ（集計済み）
            continue

    if current and (current.user_text or current.assistant_texts):
        exchanges.append(current)

    return exchanges, new_offset
