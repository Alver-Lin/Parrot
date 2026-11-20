import pytest

from backend.schemas import Settings
from backend.store import Store, markdown
from backend.timeformat import format_created_at, normalize_creation_header


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-11-19T15:20:28.374023+00:00", "2026-11-19 23:20:28（北京时间）"),
    ("2026-11-19T16:05:00Z", "2026-11-20 00:05:00（北京时间）"),
    ("2026-11-19T23:20:28+08:00", "2026-11-19 23:20:28（北京时间）"),
    ("2026-11-19T15:20:28", "2026-11-19 23:20:28（北京时间）"),
    ("未知日期", "未知日期"),
])
def test_created_at_uses_beijing_time(value, expected):
    assert format_created_at(value) == expected


def test_generated_markdown_displays_timezone_without_changing_stored_instant(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="学习记录"))
    instant = "2026-11-19T15:20:28.374023+00:00"
    with store.connect() as db:
        db.execute("UPDATE sessions SET created_at=? WHERE id=?", (instant, sid))
    detail = store.get(sid)
    assert "- 创建时间：2026-11-19 23:20:28（北京时间）" in markdown(detail)
    assert detail["created_at"] == instant


def test_existing_markdown_and_undo_snapshots_are_normalized_together(tmp_path):
    store = Store(tmp_path)
    sid = store.create(Settings(title="学习记录"))
    instant = "2026-11-19T15:20:28.374023+00:00"
    original = f"# 学习记录\r\n\r\n- 创建时间：{instant}\r\n\r\n## 正文\r\n\r\n原文。\r\n- 创建时间：{instant}\r\n"
    result = original.replace("原文。", "润色正文。")
    with store.connect() as db:
        db.execute("UPDATE sessions SET status='completed',created_at=?,markdown_content=? WHERE id=?",
                   (instant, original, sid))
    assert store.apply_polish(sid, result, original)
    revision = store.get(sid)["polish_revision"]
    migrated = Store(tmp_path)
    detail = migrated.get(sid)
    expected_original = original.replace(instant, "2026-11-19 23:20:28（北京时间）", 1)
    expected_result = result.replace(instant, "2026-11-19 23:20:28（北京时间）", 1)
    assert detail["markdown_content"] == expected_result
    assert detail["created_at"] == instant
    assert detail["polish_revision"] == revision
    assert migrated.apply_polish(sid, "", expected_result, revision_id=revision)
    assert migrated.get(sid)["markdown_content"] == expected_original
    assert Store(tmp_path).get(sid)["markdown_content"] == expected_original


def test_normalization_keeps_custom_dates_body_examples_and_fenced_code():
    instant = "2026-11-19T15:20:28.374023+00:00"
    for content in [
        "# 自定义笔记\n\n- 创建时间：2026-11-19 晚上\n",
        f"# 示例\n\n## 正文\n\n- 创建时间：{instant}\n",
        f"# 示例\n\n### 正文\n\n- 创建时间：{instant}\n",
        f"# 示例\n\n```md\n- 创建时间：{instant}\n```\n",
    ]:
        assert normalize_creation_header(content, instant) == content
