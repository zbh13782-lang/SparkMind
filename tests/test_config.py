"""Tests for advisor configuration loading and environment overrides."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from config.config import (
    get_advisor_config,
    get_catalog_config,
    get_runtime_config,
)


class AdvisorConfigTests(unittest.TestCase):
    def test_yaml_fallback_produces_defaults(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "api:\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: test-model\n"
                "advisor:\n"
                "  enabled: true\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: advisor\n"
                "  timeout_seconds: 90\n"
                "  max_question_chars: 4000\n"
                "  max_context_chars: 16000\n"
                "  max_attempts_chars: 8000\n"
            )
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {}, clear=True):
                config = get_advisor_config(tmp_path)
            self.assertTrue(config.enabled)
            self.assertEqual(config.model, "advisor")
            self.assertEqual(config.base_url, "http://localhost:20128/v1")
            self.assertEqual(config.api_key, "main-key")
            self.assertEqual(config.timeout_seconds, 90)
            self.assertEqual(config.max_question_chars, 4_000)
            self.assertEqual(config.max_context_chars, 16_000)
            self.assertEqual(config.max_attempts_chars, 8_000)
        finally:
            os.unlink(tmp_path)

    def test_environment_overrides_yaml(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "api:\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: test-model\n"
                "advisor:\n"
                "  enabled: true\n"
                "  base_url: http://advisor.test/v1\n"
                "  api_key: advisor-key\n"
                "  model: from-yaml\n"
                "  timeout_seconds: 90\n"
                "  max_question_chars: 4000\n"
                "  max_context_chars: 16000\n"
                "  max_attempts_chars: 8000\n"
            )
            tmp_path = f.name

        env = {
            "SPARKMIND_ADVISOR_ENABLED": "false",
            "SPARKMIND_ADVISOR_BASE_URL": "http://advisor.test/v1",
            "SPARKMIND_ADVISOR_API_KEY": "advisor-key",
            "SPARKMIND_ADVISOR_MODEL": "advisor-v2",
            "SPARKMIND_ADVISOR_TIMEOUT_SECONDS": "60",
        }
        try:
            with patch.dict(os.environ, env, clear=True):
                config = get_advisor_config(tmp_path)
            self.assertFalse(config.enabled)
            self.assertEqual(config.base_url, "http://advisor.test/v1")
            self.assertEqual(config.api_key, "advisor-key")
            self.assertEqual(config.model, "advisor-v2")
            self.assertEqual(config.timeout_seconds, 60)
            self.assertEqual(config.max_question_chars, 4_000)
            self.assertEqual(config.max_context_chars, 16_000)
            self.assertEqual(config.max_attempts_chars, 8_000)
        finally:
            os.unlink(tmp_path)

    def test_validation_rejects_non_positive_timeout(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "api:\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: test-model\n"
                "advisor:\n"
                "  enabled: true\n"
                "  model: advisor\n"
                "  timeout_seconds: 0\n"
                "  max_question_chars: 4000\n"
                "  max_context_chars: 16000\n"
                "  max_attempts_chars: 8000\n"
            )
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                get_advisor_config(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_invalid_int_env_var_falls_back_to_default(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "api:\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: test-model\n"
                "advisor:\n"
                "  enabled: true\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: advisor\n"
                "  timeout_seconds: 90\n"
                "  max_question_chars: 4000\n"
                "  max_context_chars: 16000\n"
                "  max_attempts_chars: 8000\n"
            )
            tmp_path = f.name

        env = {
            "SPARKMIND_ADVISOR_TIMEOUT_SECONDS": "abc",
            "SPARKMIND_ADVISOR_ENABLED": "false",
        }
        try:
            with patch.dict(os.environ, env, clear=True):
                config = get_advisor_config(tmp_path)
            self.assertEqual(config.timeout_seconds, 90)
        finally:
            os.unlink(tmp_path)

    def test_validation_rejects_empty_model_when_enabled(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "api:\n"
                "  base_url: http://localhost:20128/v1\n"
                "  api_key: main-key\n"
                "  model: test-model\n"
                "advisor:\n"
                "  enabled: true\n"
                '  model: ""\n'
                "  timeout_seconds: 90\n"
                "  max_question_chars: 4000\n"
                "  max_context_chars: 16000\n"
                "  max_attempts_chars: 8000\n"
            )
            tmp_path = f.name

        try:
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                get_advisor_config(tmp_path)
        finally:
            os.unlink(tmp_path)


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_includes_advisor_limit(self) -> None:
        config = get_runtime_config()
        self.assertEqual(config.max_advisor_calls_per_step, 1)


class CatalogConfigTests(unittest.TestCase):
    def test_catalog_config_reads_values_and_defaults(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "catalog:\n"
                "  enabled: true\n"
                "  default_database: analytics\n"
                "  cache_ttl_seconds: 60\n"
                "  max_tables_per_response: 20\n"
                "  max_columns_per_response: 100\n"
            )
            tmp_path = f.name

        try:
            config = get_catalog_config(tmp_path)
            self.assertTrue(config.enabled)
            self.assertEqual(config.default_database, "analytics")
            self.assertEqual(config.cache_ttl_seconds, 60)
            self.assertEqual(config.cache_path, "artifacts/catalog/catalog.json")
        finally:
            os.unlink(tmp_path)
