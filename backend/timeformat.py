"""Display recording timestamps consistently without changing their stored UTC instant."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISPLAY_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def format_created_at(value):
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(DISPLAY_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S（北京时间）")
    except (ValueError, TypeError, AttributeError):
        return value


def normalize_creation_header(content, created_at):
    """Only replace the old generated header; leave edited dates and body examples intact."""
    if content is None:
        return content
    original = f"- 创建时间：{created_at}"
    formatted = f"- 创建时间：{format_created_at(created_at)}"
    if original == formatted:
        return content
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        plain = line.rstrip("\r\n")
        if re.match(r"#{2,6}[ \t]+|`{3,}|~{3,}", plain.lstrip()):
            break
        if plain == original:
            lines[index] = formatted + line[len(plain):]
            return "".join(lines)
    return content
