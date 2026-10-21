"""A real terminal captures private setup input without echo or credential files."""
import errno
import json
import os
import pty
import select
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize("provider,values,prompts", [
    ("feishu", ["https://open.feishu.cn/open-apis/bot/v2/hook/SYNTHETIC_PRIVATE", "SYNTHETIC_PRIVATE_SIGNING"],
     [b"webhook (hidden):", b"Signing secret"]),
    ("imessage", ["synthetic-private@example.invalid"], [b"recipient (hidden):"]),
])
def test_real_terminal_setup_keeps_private_values_out_of_transcript_and_state(tmp_path, provider, values, prompts):
    # Execute the real CLI/getpass/config writer. Replace only the native Keychain
    # boundary; this test must never create or read a real Keychain item.
    state = tmp_path / "state"
    root = tmp_path / "synthetic-workspaces"
    expected = dict(zip(["webhook_url", "signing_secret"] if provider == "feishu" else ["recipient"], values))
    script = '''
from contextlib import contextmanager
import json,sys
from agent_storage_guard import delivery_credentials, delivery_setup, imessage_delivery
from agent_storage_guard.cli import main
import os
expected=json.loads(os.environ['ASG_SYNTHETIC_EXPECTED'])
class Security:
 def SecKeychainAddGenericPassword(self,keychain,service_len,service,account_len,account,length,data,item):
  assert json.loads(data[:length])==expected
  assert account==b'agent-storage-guard'
  return 0
@contextmanager
def boundary():
 yield Security()
delivery_credentials._interaction=boundary
delivery_setup.sys.platform='darwin'
imessage_delivery.sys.platform='darwin'
imessage_delivery.os.access=lambda *args: True
sys.exit(main())
'''
    master, slave = pty.openpty()
    process = subprocess.Popen([sys.executable, "-c", script,
        "--state", str(state), "monitor", "setup", "--provider", provider, "--root", str(root)],
        env={**os.environ, "ASG_SYNTHETIC_EXPECTED": json.dumps(expected)},
        stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    transcript = bytearray()
    sent = 0
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                transcript.extend(chunk)
                if sent < len(prompts) and prompts[sent] in transcript:
                    # Wait for the whole hidden-input prompt, so bytes cannot race
                    # getpass's terminal echo suppression.
                    if transcript.rstrip().endswith(b":"):
                        os.write(master, values[sent].encode() + b"\n")
                        sent += 1
            elif process.poll() is not None:
                break
        assert process.wait(timeout=3) == 0, transcript.decode(errors="replace")
        assert sent == len(values)
        for value in values:
            assert value.encode() not in transcript
        config = json.loads((state / "monitor/config.json").read_text())
        assert config["destinations"] == [{"id": provider, "provider": provider,
                                            "settings_keychain": f"asg-{provider}-{provider}"}]
        assert config["roots"][0]["path"] == str(root)
        for path in state.rglob("*"):
            if path.is_file():
                assert not any(value.encode() in path.read_bytes() for value in values)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
