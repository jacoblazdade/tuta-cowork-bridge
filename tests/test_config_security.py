import os
import subprocess
import sys
from pathlib import Path

import pytest

from tuta_cowork_bridge.config import Settings
from tuta_cowork_bridge.credentials import CredentialError, CredentialStore
from tuta_cowork_bridge.logs import redact, register_secret
from tuta_cowork_bridge.tools import ALLOWED_TOOLS, ToolRunner, build_server, verify_allowlist

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tuta_cowork_bridge"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_non_loopback_listener_refused(host):
    with pytest.raises(ValueError):
        Settings.from_env({"TCB_HOST": host})


def test_loopback_defaults():
    s = Settings.from_env({})
    assert (s.host, s.port, s.mcp_path) == ("127.0.0.1", 8080, "/mcp")
    assert Settings.from_env({"TCB_HOST": "::1"}).host == "::1"


def test_container_opt_in_allows_non_loopback():
    assert Settings.from_env({"TCB_HOST": "0.0.0.0", "TCB_ALLOW_NON_LOOPBACK": "1"}).host == "0.0.0.0"


def test_http_origins_refused():
    with pytest.raises(ValueError):
        Settings.from_env({"TCB_ALLOWED_ORIGINS": "http://claude.ai"})


async def test_allowlist_matches_registry(reader, tuta):
    mcp = build_server(reader, tuta, ToolRunner(10, 2), "https://example.invalid/src")
    await verify_allowlist(mcp)
    mcp.add_tool(lambda: None, name="tuta_mail_send")
    with pytest.raises(RuntimeError):
        await verify_allowlist(mcp)


def test_no_upstream_tool_registration_in_source():
    """Never: upstream tool registration, permanent deletion, folder deletion, file I/O on
    attachments, telemetry or shell. Mailbox-modifying client calls only in write.py."""
    forbidden = ["register_mail_tools", "register_calendar_tools", "register_contacts_tools",
                 "register_drive_tools", "delete_mails", "delete_folder", "upload_attachment",
                 "load_attachments", "get_file_data", "attachment_paths=[", "external_password=",
                 "tuta.telemetry", "subprocess.Popen", "os.system", "eval(", "exec("]
    write_only = ["mark_mails_unread", "simple_move_mails", "move_mails_to_folder", "apply_labels",
                  "_send_core", "create_folder(session", "rename_folder(session"]
    for path in SRC.glob("*.py"):
        text = path.read_text()
        for word in forbidden:
            assert word not in text, f"{word} found in {path.name}"
        if path.name != "write.py":
            for word in write_only:
                assert word not in text, f"{word} outside write.py ({path.name})"


def test_tool_names_are_read_only():
    for name in ALLOWED_TOOLS:
        assert not any(w in name for w in ("send", "reply", "forward", "delete", "move", "mark", "label",
                                            "create", "update", "upload", "rename", "attachment"))


def test_telemetry_never_imported_and_hmac_switch_refused():
    code = ("import sys; import tuta_cowork_bridge.main; "
            "assert 'tuta.telemetry' not in sys.modules; import os; "
            "assert os.environ['TUTAPROXY_TELEMETRY'] == 'false'")
    subprocess.run([sys.executable, "-c", code], check=True)
    env = dict(os.environ, TUTA_SKIP_HMAC="1")
    r = subprocess.run([sys.executable, "-c", "import tuta_cowork_bridge.main"], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "TUTA_SKIP_HMAC" in r.stderr


def test_file_backend_permissions(tmp_path):
    f = tmp_path / "creds.env"
    f.write_text("TUTA_EMAIL=a@b.c\nTUTA_PASSWORD=pw\nTCB_API_KEY=" + "k" * 40 + "\n")
    store = CredentialStore(Settings.from_env({"TCB_CREDENTIAL_BACKEND": "file", "TCB_CREDENTIALS_FILE": str(f)}))
    f.chmod(0o644)
    with pytest.raises(CredentialError) as e:
        store.tuta()
    assert "pw" not in str(e.value)
    f.chmod(0o600)
    assert store.tuta().password == "pw" and store.api_key() == "k" * 40
    assert store.status() == {"TUTA_EMAIL": True, "TUTA_PASSWORD": True, "TUTA_TOTP_SECRET": False, "TCB_API_KEY": True}


def test_redaction():
    register_secret("hunter2-super-secret")
    line = ("GET https://w1.api.tuta.com/x?blobAccessToken=abc.def&accessToken=XYZ123 "
            "x-api-key: KEYVALUE from bob@example.org pw=hunter2-super-secret")
    out = redact(line)
    for leak in ("abc.def", "XYZ123", "KEYVALUE", "bob@example.org", "hunter2-super-secret"):
        assert leak not in out
