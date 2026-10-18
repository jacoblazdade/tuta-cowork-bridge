import os

import aiohttp
import pytest

from tuta.api import TutaAPIError
from tuta_cowork_bridge.credentials import TutaCredentials
from tuta_cowork_bridge.mail import MailReader
from tuta_cowork_bridge.session import LoginUnavailable, TutaSession

from fakes import EMAIL, PASSWORD, FakeTutaClient


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_lazy_login_and_reuse(tuta, fake, reader):
    assert fake.logins == 0
    await reader.list_folders()
    await reader.list_emails(limit=1)
    assert fake.logins == 1


async def test_failed_login_backs_off_without_retrying():
    fake, clock = FakeTutaClient(password="other"), Clock()
    s = TutaSession(lambda: TutaCredentials(EMAIL, PASSWORD, None), lambda: fake, clock=clock)
    with pytest.raises(TutaAPIError):
        await MailReader(s).list_folders()
    assert fake.logins == 1 and fake.closed
    for _ in range(5):  # no further login attempts while paused
        with pytest.raises(LoginUnavailable) as e:
            await MailReader(s).list_folders()
    assert fake.logins == 1 and e.value.reason == "rejected credentials" and e.value.retry_in >= 299
    clock.t += 301
    with pytest.raises(TutaAPIError):
        await MailReader(s).list_folders()
    assert fake.logins == 2
    clock.t += 301  # second failure doubles the pause to 600 s
    with pytest.raises(LoginUnavailable):
        await MailReader(s).list_folders()
    assert s.status()["last_error"] == "rejected credentials"


async def test_expired_session_relogs_and_retries_once(tuta, fake, reader):
    await reader.list_folders()
    fake.fail_next = [TutaAPIError(440, "expired")]
    page = await reader.list_emails(limit=2)
    assert len(page["emails"]) == 2 and fake.logins == 2


async def test_network_error_retried_once(tuta, fake, reader):
    await reader.list_folders()
    fake.fail_next = [aiohttp.ClientConnectionError("boom")]
    assert len((await reader.list_emails(limit=1))["emails"]) == 1
    fake.fail_next = [aiohttp.ClientConnectionError("a"), aiohttp.ClientConnectionError("b")]
    with pytest.raises(aiohttp.ClientConnectionError):
        await reader.list_emails(limit=1)


async def test_totp_secret_only_in_env_during_login():
    fake = FakeTutaClient()
    s = TutaSession(lambda: TutaCredentials(EMAIL, PASSWORD, "JBSWY3DPEHPK3PXP"), lambda: fake)
    await MailReader(s).list_folders()
    assert fake.totp_seen == "JBSWY3DPEHPK3PXP"
    assert "TUTA_TOTP_SECRET" not in os.environ


async def test_close_logs_out(tuta, fake, reader):
    await reader.list_folders()
    await tuta.close()
    assert fake.logouts == 1 and fake.closed and tuta.status()["logged_in"] is False


def test_credentials_repr_hides_values():
    c = TutaCredentials(EMAIL, PASSWORD, "SEED")
    assert PASSWORD not in repr(c) and "SEED" not in repr(c) and EMAIL not in repr(c)
