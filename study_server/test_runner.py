"""Test runner that blocks real external network calls during the test suite.

``PORTABILITY_SERVER_URL`` defaults to a real dev server (``http://localhost:8001``) and the
AWARE ``db_connector`` talks to a MySQL instance, so an un-mocked test would silently hit them
(creating/deleting real donations, opening real DB connections). This runner replaces those
network seams with guards that raise immediately, so any leak fails loudly — naming the seam —
instead of reaching an external service.

Tests that legitimately exercise these paths patch them locally (e.g.
``@patch('data_sources.models.portability_client.requests.post')`` or
``@patch('data_sources.models.portability_client.create_donation')``), which overrides the guard
for the duration of that test and is restored to the guard afterwards.
"""
from unittest import mock

from django.test.runner import DiscoverRunner


class _BlockedCall:
    """Callable that raises when a blocked network seam is invoked un-mocked."""

    def __init__(self, seam):
        self.seam = seam

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            f"Blocked real network call to {self.seam} during tests. "
            f"Mock it in the test (e.g. patch '{self.seam}')."
        )


class NoNetworkTestRunner(DiscoverRunner):
    """DiscoverRunner that blocks portability-server HTTP and AWARE DB connections."""

    # (dotted target, attribute) seams replaced with a raising guard for the whole run.
    BLOCKED_SEAMS = (
        'data_sources.models.portability_client.requests.post',
        'data_sources.models.portability_client.requests.get',
        'data_sources.models.portability_client.requests.delete',
        'data_sources.models.db_connector.mysql.connector.connect',
    )

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._network_patchers = []
        for seam in self.BLOCKED_SEAMS:
            patcher = mock.patch(seam, _BlockedCall(seam))
            patcher.start()
            self._network_patchers.append(patcher)

    def teardown_test_environment(self, **kwargs):
        for patcher in getattr(self, '_network_patchers', []):
            patcher.stop()
        super().teardown_test_environment(**kwargs)
