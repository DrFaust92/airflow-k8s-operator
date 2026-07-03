from unittest.mock import MagicMock

import airflow_client.client as client
import pytest
from airflow_client.client.exceptions import UnauthorizedException

from config.client import JwtAuthApiClient


def _make(host_root="http://airflow:8080"):
    configuration = client.Configuration(host=f"{host_root}/api/v2")
    return JwtAuthApiClient(configuration, host_root, "admin", "admin")


def test_fetches_and_caches_token(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"access_token": "tok-1"}),
        )
    )
    monkeypatch.setattr("config.client.requests.post", post)

    jwt = _make()
    assert jwt._authorization() == "Bearer tok-1"
    # Cached: a second call (no expiry) does not re-fetch.
    assert jwt._authorization() == "Bearer tok-1"
    post.assert_called_once()


def test_missing_token_raises(monkeypatch):
    monkeypatch.setattr(
        "config.client.requests.post",
        MagicMock(
            return_value=MagicMock(
                raise_for_status=MagicMock(),
                json=MagicMock(return_value={"detail": "bad creds"}),
            )
        ),
    )
    with pytest.raises(RuntimeError):
        _make()._authorization()


def test_retries_once_on_401(monkeypatch):
    post = MagicMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"access_token": "tok"}),
        )
    )
    monkeypatch.setattr("config.client.requests.post", post)

    # Parent call_api raises 401 the first time, succeeds the second.
    parent = MagicMock(side_effect=[UnauthorizedException(status=401), "ok"])
    monkeypatch.setattr(client.ApiClient, "call_api", parent)

    result = _make().call_api("/connections", "POST")
    assert result == "ok"
    assert parent.call_count == 2
    # Initial fetch + forced refresh after the 401.
    assert post.call_count == 2
