import pytest

from tuta_cowork_bridge.credentials import TutaCredentials
from tuta_cowork_bridge.mail import MailReader
from tuta_cowork_bridge.session import TutaSession

from fakes import EMAIL, PASSWORD, FakeTutaClient

API_KEY = "test-api-key-" + "x" * 40


@pytest.fixture
def fake():
    return FakeTutaClient()


@pytest.fixture
def tuta(fake):
    return TutaSession(lambda: TutaCredentials(EMAIL, PASSWORD, None), client_factory=lambda: fake)


@pytest.fixture
def reader(tuta):
    return MailReader(tuta)


class FakeStore:
    """CredentialStore stand-in with synthetic values."""

    def __init__(self, password=PASSWORD, api_key=API_KEY, totp=None):
        self.password, self.key, self.totp = password, api_key, totp

    def tuta(self):
        return TutaCredentials(EMAIL, self.password, self.totp)

    def api_key(self):
        return self.key
