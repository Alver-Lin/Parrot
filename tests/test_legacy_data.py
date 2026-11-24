from backend.legacy_data import import_legacy_into, remember_legacy_path, session_count
from backend.schemas import Settings
from backend.store import Store


def test_import_legacy_sessions_into_empty_support(tmp_path, monkeypatch):
    support = tmp_path / "support"
    legacy = tmp_path / "Parrot" / ".data"
    monkeypatch.setenv("PARROT_SUPPORT_DIR", str(support))
    monkeypatch.delenv("PARROT_DISABLE_LEGACY_IMPORT", raising=False)
    monkeypatch.setenv("PARROT_LEGACY_DATA", str(legacy))

    store = Store(legacy)
    sid = store.create(Settings(title="旧学习记录"))
    (legacy / sid).mkdir(exist_ok=True)
    (legacy / sid / "note.txt").write_text("keep-me", encoding="utf-8")

    imported = import_legacy_into(support)
    assert imported == legacy
    assert session_count(support / "soundscribe.db") == 1
    assert (support / sid / "note.txt").read_text(encoding="utf-8") == "keep-me"
    assert (support / ".legacy-import-done").is_file()
    assert import_legacy_into(support) is None


def test_remember_legacy_path_writes_breadcrumb(tmp_path, monkeypatch):
    support = tmp_path / "support"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("PARROT_SUPPORT_DIR", str(support))
    monkeypatch.delenv("PARROT_DISABLE_LEGACY_IMPORT", raising=False)

    store = Store(legacy)
    store.create(Settings(title="面包屑"))
    remember_legacy_path(legacy)
    assert (support / "legacy-data-path").read_text(encoding="utf-8").strip() == str(legacy.resolve())


def test_later_dev_data_changes_do_not_overwrite_install_store(tmp_path, monkeypatch):
    support = tmp_path / "support"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("PARROT_SUPPORT_DIR", str(support))
    monkeypatch.delenv("PARROT_DISABLE_LEGACY_IMPORT", raising=False)
    monkeypatch.setenv("PARROT_LEGACY_DATA", str(legacy))

    Store(legacy).create(Settings(title="首次导入"))
    assert import_legacy_into(support) == legacy
    before = (support / "soundscribe.db").read_bytes()

    Store(legacy).create(Settings(title="开发版后续新增"))
    assert session_count(legacy / "soundscribe.db") == 2
    assert import_legacy_into(support) is None
    assert (support / "soundscribe.db").read_bytes() == before
    assert session_count(support / "soundscribe.db") == 1
