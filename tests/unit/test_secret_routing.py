"""Plus aucun secret dans les fichiers du profil ; les appelants ne changent pas."""

import json
import os

from src.mirai import credentials, local_config
from tests.stubs.uno_stubs import install, make_job, read_user_config, seed_user_config

install()


def _files_text(root):
    out = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            out.append(open(os.path.join(folder, name), errors="ignore").read())
    return "\n".join(out)


def test_stored_keys_go_to_the_store_and_read_back(tmp_path):
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_key", "SECRET-rk")
    job.set_config("proxy_password", "SECRET-pw")
    job.set_config("access_token", "SECRET-at")
    assert job._get_config_from_file("relay_client_key", "") == "SECRET-rk"
    assert job._get_config_from_file("proxy_password", "") == "SECRET-pw"
    assert job._get_config_from_file("access_token", "") == "SECRET-at"
    assert "SECRET" not in _files_text(str(tmp_path))


def test_startup_moves_secrets_out_of_settings(tmp_path):
    # Fichier écrit tel que l'a laissé la Partie 2 (ou un rollback) : sans passer
    # par seed_user_config, qui rangerait déjà les secrets.
    local_config.write_json_atomic(
        os.path.join(str(tmp_path), "mirai", "settings.json"),
        {"plugin_uuid": "u", "refresh_token": "SECRET-rt",
         "relay_client_key": "SECRET-rk", "access_token": "SECRET-at"})
    job = make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("refresh_token", "") == "SECRET-rt"
    assert job._get_config_from_file("relay_client_key", "") == "SECRET-rk"
    settings = read_user_config(str(tmp_path))
    assert settings["plugin_uuid"] == "u"
    assert not set(settings) & (set(credentials.STORED_KEYS) | credentials.MEMORY_KEYS)
    assert "SECRET" not in _files_text(str(tmp_path))


def test_new_environment_hides_bound_secrets(tmp_path):
    seed_user_config(str(tmp_path), {"bootstrap_url": "https://dm-a"})
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_key", "rk")
    assert job._get_config_from_file("relay_client_key", "") == "rk"
    job._local_config().update({"bootstrap_url": "https://dm-b"})
    assert job._get_config_from_file("relay_client_key", "") == ""


def test_transport_change_at_startup_clears_bound_secrets(tmp_path):
    seed_user_config(str(tmp_path), {"bootstrap_url": "https://dm-a"})
    job = make_job(config_dir=str(tmp_path))
    job.set_config("refresh_token", "rt")
    job.set_config("proxy_password", "pw")
    job._apply_install_changes({"transport"})
    assert credentials.store().get("refresh_token") is None
    assert job._get_config_from_file("proxy_password", "") == "pw"


def test_secret_left_in_settings_replaces_the_stored_one(tmp_path):
    settings_path = os.path.join(str(tmp_path), "mirai", "settings.json")
    local_config.write_json_atomic(settings_path, {"bootstrap_url": "https://dm",
                                                   "relay_client_key": "nouvelle"})
    scope = local_config.LocalConfig(str(tmp_path), []).transport_scope()
    credentials.set_secret("relay_client_key", "ancienne", scope)
    job = make_job(config_dir=str(tmp_path))
    assert job._credential_scope() == scope
    assert job._get_config_from_file("relay_client_key", "") == "nouvelle"
    assert "relay_client_key" not in read_user_config(str(tmp_path))


def test_rollback_then_upgrade_keeps_the_pair_enrolled_by_the_older_version(tmp_path):
    # La version antérieure s'est réinscrite (le DM a révoqué la paire A) et a
    # écrit la paire B dans config.json.
    seed_user_config(str(tmp_path), {"plugin_uuid": "u", "bootstrap_url": "https://dm",
                                     "relay_client_id": "A-id", "relay_client_key": "A-key"})
    local_config.write_json_atomic(
        os.path.join(str(tmp_path), "config.json"),
        {"plugin_uuid": "u", "enrolled": True,
         "relay_client_id": "B-id", "relay_client_key": "B-key"})
    job = make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_id", "") == "B-id"
    assert job._get_config_from_file("relay_client_key", "") == "B-key"
    assert read_user_config(str(tmp_path))["enrolled"] is True
    assert "B-key" not in _files_text(str(tmp_path))


class _RefusingStore(credentials.MemoryStore):
    """Trousseau désynchronisé, stratégie qui plafonne la persistance…"""

    def set(self, name, value):
        return False


def test_refused_store_keeps_the_secret_in_memory_for_the_session(tmp_path):
    credentials.use_store(_RefusingStore())
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_key", "SECRET-rk")
    assert job._get_config_from_file("relay_client_key", "") == "SECRET-rk"
    assert "SECRET" not in _files_text(str(tmp_path))


def test_refused_store_at_startup_keeps_moved_secrets_in_memory(tmp_path):
    credentials.use_store(_RefusingStore())
    local_config.write_json_atomic(
        os.path.join(str(tmp_path), "mirai", "settings.json"),
        {"plugin_uuid": "u", "relay_client_key": "SECRET-rk", "refresh_token": "SECRET-rt"})
    job = make_job(config_dir=str(tmp_path))
    assert job._get_config_from_file("relay_client_key", "") == "SECRET-rk"
    assert job._get_config_from_file("refresh_token", "") == "SECRET-rt"
    assert not set(read_user_config(str(tmp_path))) & set(credentials.STORED_KEYS)
    assert "SECRET" not in _files_text(str(tmp_path))


def test_accepted_write_or_delete_after_a_refusal_leaves_no_memory_copy(tmp_path):
    credentials.use_store(_RefusingStore())
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_key", "rk-1")
    job.set_config("relay_client_key", "")
    assert job._get_config_from_file("relay_client_key", "") == ""
    job.set_config("relay_client_key", "rk-2")
    credentials.use_store(credentials.MemoryStore())
    job.set_config("relay_client_key", "rk-3")
    assert credentials.recall("relay_client_key") == ""
    assert job._get_config_from_file("relay_client_key", "") == "rk-3"


def test_refused_newer_value_wins_over_the_older_stored_one(tmp_path):
    # Paire révoquée encore dans le coffre, nouvelle paire refusée par le coffre :
    # relire l'ancienne mènerait à des 401 en boucle.
    store = _RefusingStore()
    credentials.use_store(store)
    job = make_job(config_dir=str(tmp_path))
    credentials.MemoryStore.set(store, "relay_client_key", json.dumps(
        {"scope": job._credential_scope(), "value": "ancienne"}))
    assert job._get_config_from_file("relay_client_key", "") == "ancienne"
    job.set_config("relay_client_key", "nouvelle")
    assert job._get_config_from_file("relay_client_key", "") == "nouvelle"


class _OsStore(credentials.MemoryStore):
    persistent = True


def _startup_secrets_line(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr("src.mirai.entrypoint.log_to_file", logged.append)
    local_config.write_json_atomic(
        os.path.join(str(tmp_path), "mirai", "settings.json"),
        {"plugin_uuid": "u", "relay_client_key": "rk"})
    make_job(config_dir=str(tmp_path))
    return next(line for line in logged if line.startswith("[secrets]"))


def test_startup_line_says_memory_without_an_os_store(tmp_path, monkeypatch):
    line = _startup_secrets_line(tmp_path, monkeypatch)
    assert "en mémoire pour la session" in line
    assert "dans le coffre" not in line


def test_startup_line_names_the_os_store_when_there_is_one(tmp_path, monkeypatch):
    credentials.use_store(_OsStore())
    assert "dans le coffre" in _startup_secrets_line(tmp_path, monkeypatch)


def test_shell_routes_store_diagnostics_to_its_log():
    from src.mirai import entrypoint
    assert credentials._log is entrypoint.log_to_file


class _SpyStore(credentials.MemoryStore):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, name):
        self.reads.append(name)
        return super().get(name)


def test_disabled_proxy_never_reads_its_credentials(tmp_path):
    store = _SpyStore()
    credentials.use_store(store)
    job = make_job(config_dir=str(tmp_path))
    job.set_config("proxy_username", "u")
    job.set_config("proxy_password", "pw")
    job.set_config("proxy_enabled", False)
    store.reads.clear()
    cfg = job._get_proxy_config()
    assert store.reads == []
    assert (cfg["username"], cfg["password"]) == ("", "")
    dialog = job._get_proxy_config(with_credentials=True)
    assert (dialog["username"], dialog["password"]) == ("u", "pw")
    job.set_config("proxy_enabled", True)
    assert job._get_proxy_config()["password"] == "pw"


def test_transport_change_forgets_bound_secrets_kept_in_memory(tmp_path):
    credentials.use_store(_RefusingStore())
    job = make_job(config_dir=str(tmp_path))
    job.set_config("relay_client_key", "rk")
    job._apply_install_changes({"transport"})
    assert job._get_config_from_file("relay_client_key", "") == ""
