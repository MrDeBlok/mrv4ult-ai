"""Tests for Sprint 49.0 — Parser Training Center."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from navigation import visible_nav_groups
from parser_confidence import attach_parser_confidence_metadata
from parser_learning import (
    apply_learning_rules_to_watch,
    detect_condition_training_term,
    flag_condition_training,
    prepare_watch_for_ingest,
    teach_condition_rule,
)
from parser_safety_gates import is_suspicious_price, should_block_active_offer
from parser_training_reprocess import teach_condition_and_reprocess
from permissions import can_view_page
from tests.conftest import ADMIN_USER, TRADER_ONE

pytestmark = pytest.mark.no_auto_login

PP_BRAND_NEW_HEADER_MESSAGE = """PP brand new Hong Kong ready stock list

● 5160/500R new 2026 HKD 1.2M"""


def _fresh_watch() -> dict:
    return {
        "brand": "Rolex",
        "reference": "126610LN",
        "original_price": 12500,
        "original_currency": "USD",
        "usd_price": 12500,
        "source_line": "Rolex 126610LN Fresh 12500 USD",
        "condition": "Pre-Owned",
        "raw_condition": "Fresh",
    }


class TestParserTrainingCenterAccess:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    def test_admin_sees_parser_training_center(
        self,
        _mock_messages: MagicMock,
        _mock_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 200
        assert "Parser Training Center" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app._parser_review_import_logs", return_value=[])
    @patch("database.get_messages_by_ids", return_value={})
    def test_ai_workbench_redirect_still_works(
        self,
        _mock_messages: MagicMock,
        _mock_logs: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/ai-workbench", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/parser-training"

    @patch("app.get_current_user", return_value=TRADER_ONE)
    def test_trader_cannot_access_training_center(self, _mock_user: MagicMock) -> None:
        assert can_view_page(TRADER_ONE, "/parser-review") is False

        client = TestClient(app)
        response = client.get("/parser-review")

        assert response.status_code == 403

    def test_admin_nav_shows_training_center_and_learned_rules(self) -> None:
        groups = visible_nav_groups(ADMIN_USER)
        ai_group = next(group for group in groups if group["label"] == "AI")
        labels = {link["label"] for link in ai_group["links"]}

        assert "Parser Training Center" in labels
        assert "Learned Rules" in labels


class TestConditionTraining:
    def test_fresh_initially_triggers_condition_training(self) -> None:
        watch = _fresh_watch()
        flagged = flag_condition_training(
            watch,
            message_text="Rolex 126610LN Fresh 12500 USD",
            rules=[],
        )

        assert flagged is True
        assert watch["condition_needs_training"] is True
        assert watch["condition_training_term"] == "fresh"
        assert watch.get("condition") is None

    def test_detect_condition_training_term_from_message(self) -> None:
        assert detect_condition_training_term("Patek 5711 mint full set") == "mint"
        assert detect_condition_training_term("Rolex never worn bnib") == "never worn"
        assert detect_condition_training_term(
            "Tudor Royal M2836C1A3-0002 Fresh New / Unworn"
        ) is None

    def test_ready_stock_header_does_not_trigger_training_on_parsed_offer_line(self) -> None:
        watch = {
            "brand": "Patek Philippe",
            "reference": "5160/500R",
            "condition": "New",
            "raw_condition": "new 2026",
            "source_line": "● 5160/500R new 2026 HKD 1.2M",
            "condition_source": "explicit",
            "condition_explicit": True,
        }
        assert detect_condition_training_term(PP_BRAND_NEW_HEADER_MESSAGE, watch) is None
        assert flag_condition_training(watch, message_text=PP_BRAND_NEW_HEADER_MESSAGE, rules=[]) is False
        assert watch["condition"] == "New"

    def test_fresh_new_unworn_does_not_trigger_fresh_training(self) -> None:
        watch = {
            "brand": "Tudor",
            "reference": "M2836C1A3-0002",
            "condition": "New",
            "raw_condition": "Fresh New / Unworn",
        }
        flagged = flag_condition_training(
            watch,
            message_text="Tudor Royal M2836C1A3-0002 Fresh New / Unworn",
            rules=[],
        )

        assert flagged is False
        assert watch["condition"] == "New"

    def test_teach_fresh_rule_applies_on_future_import(self) -> None:
        rules = [
            {
                "id": "rule-1",
                "field_type": "condition",
                "term": "Fresh",
                "normalized_value": "New",
                "scope": "global",
                "status": "active",
            }
        ]
        watch = _fresh_watch()
        prepare_watch_for_ingest(
            watch,
            message_text="Rolex 126610LN Fresh 12500 USD",
            rules=rules,
        )

        assert watch.get("condition_needs_training") is not True
        assert watch["condition"] == "New"

    @patch("parser_training_reprocess.reprocess_import_with_offer_sync")
    @patch("database.create_parser_learning_rule")
    @patch("database.parser_learning_rules_supported", return_value=True)
    @patch("database.invalidate_parser_learning_rules_cache")
    def test_teach_condition_and_reprocess_saves_rule(
        self,
        _invalidate: MagicMock,
        _supported: MagicMock,
        mock_create_rule: MagicMock,
        mock_reprocess: MagicMock,
    ) -> None:
        mock_create_rule.return_value = {"id": "rule-1"}
        mock_reprocess.return_value = {"id": "log-1", "status": "success"}

        result = teach_condition_and_reprocess(
            "log-1",
            term="Fresh",
            normalized_value="New",
            action="teach_new",
        )

        assert result["status"] == "success"
        mock_create_rule.assert_called_once()
        mock_reprocess.assert_called_once_with("log-1")


class TestSafetyGates:
    def test_low_confidence_brand_blocks_active_offer(self) -> None:
        watch = {
            "reference": "126610LN",
            "original_price": 12500,
            "original_currency": "USD",
            "usd_price": 12500,
            "condition": "Pre-Owned",
            "condition_explicit": True,
            "condition_confidence": "high",
        }
        attach_parser_confidence_metadata(watch)

        assert should_block_active_offer(watch) is True

    def test_suspicious_price_blocks_active_offer(self) -> None:
        watch = {
            "brand": "Rolex",
            "reference": "126610LN",
            "original_price": 3,
            "original_currency": "HKD",
            "condition": "Pre-Owned",
            "condition_explicit": True,
            "condition_confidence": "high",
        }

        assert is_suspicious_price(watch) is True
        assert should_block_active_offer(watch) is True

    def test_confident_watch_passes_safety_gate(self) -> None:
        watch = {
            "brand": "Rolex",
            "reference": "126610LN",
            "reference_high_confidence": True,
            "original_price": 12500,
            "original_currency": "USD",
            "usd_price": 12500,
            "condition": "Pre-Owned",
            "condition_explicit": True,
            "condition_confidence": "high",
        }
        attach_parser_confidence_metadata(watch)

        assert should_block_active_offer(watch) is False


class TestLearnedRulesPage:
    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.list_parser_learning_rules", return_value=[
        {
            "id": "rule-1",
            "field_type": "condition",
            "term": "Fresh",
            "normalized_value": "New",
            "scope": "global",
            "status": "active",
            "created_at": "2026-06-25T10:00:00+00:00",
            "source_import_log_id": "log-1",
        }
    ])
    @patch("app.parser_learning_rules_supported", return_value=True)
    def test_learned_rules_page_lists_rules(
        self,
        _supported: MagicMock,
        _mock_rules: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        client = TestClient(app)
        response = client.get("/parser-learning-rules")

        assert response.status_code == 200
        assert "Learned Rules" in response.text
        assert "Fresh" in response.text

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.disable_parser_learning_rule")
    @patch("app.get_parser_learning_rule")
    @patch("app.parser_learning_rules_supported", return_value=True)
    def test_learned_rules_can_be_disabled(
        self,
        _supported: MagicMock,
        mock_get_rule: MagicMock,
        mock_disable: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_rule.return_value = {"id": "rule-1", "status": "active"}
        mock_disable.return_value = {"id": "rule-1", "status": "disabled"}

        client = TestClient(app)
        response = client.post("/parser-learning-rules/rule-1/disable", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/parser-learning-rules?disabled=1"
        mock_disable.assert_called_once_with("rule-1")

    @patch("app.get_current_user", return_value=ADMIN_USER)
    @patch("app.update_parser_learning_rule")
    @patch("app.get_parser_learning_rule")
    @patch("app.parser_learning_rules_supported", return_value=True)
    def test_learned_rules_can_be_edited(
        self,
        _supported: MagicMock,
        mock_get_rule: MagicMock,
        mock_update: MagicMock,
        _mock_user: MagicMock,
    ) -> None:
        mock_get_rule.return_value = {"id": "rule-1", "status": "active"}
        mock_update.return_value = {"id": "rule-1", "normalized_value": "Pre-Owned"}

        client = TestClient(app)
        response = client.post(
            "/parser-learning-rules/rule-1/edit",
            data={
                "term": "Fresh",
                "normalized_value": "Pre-Owned",
                "scope": "global",
                "field_type": "condition",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/parser-learning-rules?updated=1"
        mock_update.assert_called_once()

    def test_apply_learning_rules_before_default_inference(self) -> None:
        watch = _fresh_watch()
        apply_learning_rules_to_watch(
            watch,
            message_text="Fresh",
            rules=[
                {
                    "id": "rule-2",
                    "field_type": "condition",
                    "term": "fresh",
                    "normalized_value": "New",
                    "scope": "global",
                    "status": "active",
                }
            ],
        )

        assert watch["condition"] == "New"
        assert watch.get("condition_learned_rule_id") == "rule-2"
