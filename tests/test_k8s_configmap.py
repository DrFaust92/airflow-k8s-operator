import base64
from unittest.mock import MagicMock, patch

import pytest

from config.k8s_configmap import get_config_map_value


def _api(config_map):
    mock_instance = MagicMock()
    mock_instance.read_namespaced_config_map.return_value = config_map
    return mock_instance


def test_reads_text_data_as_bytes():
    cm = MagicMock(data={"dag.py": "print('x')\n"}, binary_data=None)
    with patch("config.k8s_configmap.client.CoreV1Api", return_value=_api(cm)):
        assert get_config_map_value("cm", "dag.py", "ns") == b"print('x')\n"


def test_reads_binary_data_base64_decoded():
    raw = b"PK\x03\x04zipbytes"
    cm = MagicMock(data=None, binary_data={"dag.zip": base64.b64encode(raw).decode()})
    with patch("config.k8s_configmap.client.CoreV1Api", return_value=_api(cm)):
        assert get_config_map_value("cm", "dag.zip", "ns") == raw


def test_missing_key_raises_value_error():
    cm = MagicMock(data={"other": "x"}, binary_data=None)
    with patch("config.k8s_configmap.client.CoreV1Api", return_value=_api(cm)):
        with pytest.raises(ValueError, match="not found"):
            get_config_map_value("cm", "dag.py", "ns")


def test_api_error_raises_value_error():
    from kubernetes import client

    mock_instance = MagicMock()
    mock_instance.read_namespaced_config_map.side_effect = (
        client.exceptions.ApiException(status=404, reason="Not Found")
    )
    with patch("config.k8s_configmap.client.CoreV1Api", return_value=mock_instance):
        with pytest.raises(ValueError, match="Failed to fetch ConfigMap"):
            get_config_map_value("cm", "dag.py", "ns")
