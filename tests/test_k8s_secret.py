import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from k8s_secret import _get_secret_value, resolve_value


def test_resolve_value_direct_string():
    assert resolve_value("plain-value", "default") == "plain-value"


def test_resolve_value_with_explicit_key():
    spec = {"value": "test"}
    assert resolve_value(spec, "default") == "test"


def test_resolve_value_secret_ref():
    with patch("k8s_secret._get_secret_value") as mock_get_secret:
        mock_get_secret.return_value = "secret-value"
        spec = {"secretRef": {"name": "my-secret", "key": "my-key"}}
        result = resolve_value(spec, "default")
        assert result == "secret-value"
        mock_get_secret.assert_called_once_with("my-secret", "my-key", "default", None)


def test_extra_args_resolve_value_secret_ref():
    with patch("k8s_secret._get_secret_value") as mock_get_secret:
        mock_get_secret.return_value = "secret-value"
        spec = {
            "description": "Example Airflow Variable that fetches value from a Kubernetes Secret.",
            "secretRef": {"key": "s3-path", "name": "my-secret"},
        }
        result = resolve_value(spec, "default")
        assert result == "secret-value"
        mock_get_secret.assert_called_once_with("my-secret", "s3-path", "default", None)


def test_resolve_value_secret_ref_missing_fields():
    spec = {"secretRef": {"name": "my-secret"}}  # missing 'key'
    with pytest.raises(ValueError):
        resolve_value(spec, "default")


def test_resolve_value_invalid_spec():
    # Not a string, not a dict with 'value' or 'secretRef'
    with pytest.raises(ValueError):
        resolve_value({"foo": "bar"}, "default")


def test__get_secret_value_success():
    # Patch the Kubernetes client and secret object
    with patch("k8s_secret.client.CoreV1Api") as mock_api:
        mock_instance = MagicMock()
        mock_api.return_value = mock_instance
        secret_obj = MagicMock()
        secret_obj.data = {
            "my-key": b"c2VjcmV0LXZhbHVl".decode()
        }  # base64 for 'secret-value'
        mock_instance.read_namespaced_secret.return_value = secret_obj
        with patch("base64.b64decode", return_value=b"secret-value"):
            value = _get_secret_value("my-secret", "my-key", "default")
            assert value == "secret-value"


def test__get_secret_value_missing_key():
    with patch("k8s_secret.client.CoreV1Api") as mock_api:
        mock_instance = MagicMock()
        mock_api.return_value = mock_instance
        secret_obj = MagicMock()
        secret_obj.data = {"other-key": "irrelevant"}
        mock_instance.read_namespaced_secret.return_value = secret_obj
        with pytest.raises(ValueError):
            _get_secret_value("my-secret", "my-key", "default")


def test__get_secret_value_api_exception():
    with patch("k8s_secret.client.CoreV1Api") as mock_api:
        mock_instance = MagicMock()
        mock_api.return_value = mock_instance
        mock_instance.read_namespaced_secret.side_effect = Exception("API error")
        with pytest.raises(ValueError):
            _get_secret_value("my-secret", "my-key", "default")
