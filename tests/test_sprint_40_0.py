"""Tests for Sprint 40.0 multi-offer dealer list parsing."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contact_classification import CONTACT_TYPE_DEALER
from dealer_list_splitter import split_dealer_list_message
from import_classification import split_offer_watches
from ingest import dealer_list_line_count, ingest_message, is_dealer_list_bulk_import
from watch_parser import parse_message, parse_watch_line

LANGE_OFFER_LINES = [
    "101.021 2011 Used Fullset HKD210k",
    "116.025 2015 Used Fullset HKD269k",
    "116.033 2009 Used Fullset HKD280k",
    "136.025 2024 Used Fullset HKD399k",
    "136.025 2025 Used Fullset HKD399k",
    "137.033 2023 Used Fullset HKD270k",
    "139.032 2020 Used Fullset HKD270k",
    "139.035 2017 Used Fullset HKD1.26m",
    "140.032 2022 Used Fullset HKD620k",
]

LANGE_DEALER_LIST = "A. Lange & Söhne✅✅ 🐣🐣\n" + "\n".join(LANGE_OFFER_LINES)

LARGE_LANGE_DEALER_LIST = "A. Lange & Söhne✅✅ 🐣🐣\n" + "\n".join(LANGE_OFFER_LINES * 3)

LARGE_30_LINE_DEALER_LIST = "A. Lange & Söhne✅✅ 🐣🐣\n" + "\n".join((LANGE_OFFER_LINES * 4)[:30])

SINGLE_OFFER = "Rolex Submariner 126610LN New full set €12700"

WTB_LIST = """WTB
101.021 budget 210k
116.025 budget 269k"""

CASUAL_MULTILINE = """Hey team
Are we still meeting for lunch tomorrow?
Thanks"""


class TestDealerListSplitter:
    def test_lange_list_creates_multiple_watches(self) -> None:
        result = parse_message(LANGE_DEALER_LIST)

        assert result["message_type"] == "offer_list"
        assert len(result["watches"]) == 9
        references = [watch["reference"] for watch in result["watches"]]
        assert references == [
            "101.021",
            "116.025",
            "116.033",
            "136.025",
            "136.025",
            "137.033",
            "139.032",
            "139.035",
            "140.032",
        ]

    def test_brand_header_applies_to_all_lines(self) -> None:
        result = parse_message(LANGE_DEALER_LIST)

        assert all(watch["brand"] == "A. Lange & Söhne" for watch in result["watches"])

    def test_hkd_k_and_m_prices_parse_correctly(self) -> None:
        result = parse_message(LANGE_DEALER_LIST)
        by_reference = {watch["reference"]: watch for watch in result["watches"]}

        first = by_reference["101.021"]
        assert first["original_currency"] == "HKD"
        assert first["original_price"] == 210_000
        assert first["production_year"] == 2011
        assert first["full_set"] is True

        million = by_reference["139.035"]
        assert million["original_currency"] == "HKD"
        assert million["original_price"] == 1_260_000

        high = by_reference["140.032"]
        assert high["original_price"] == 620_000

    def test_splitter_detects_nine_offer_lines(self) -> None:
        split = split_dealer_list_message(LANGE_DEALER_LIST)

        assert split is not None
        brand, lines = split
        assert brand == "A. Lange & Söhne"
        assert len(lines) == 9


class TestDealerListRegression:
    def test_single_offer_still_works(self) -> None:
        result = parse_message(SINGLE_OFFER)

        assert result["message_type"] == "offer"
        assert len(result["watches"]) == 1
        assert result["watches"][0]["reference"] == "126610LN"
        assert result["watches"][0]["original_price"] == 12_700

    def test_wtb_list_remains_request_intent_not_offers(self) -> None:
        parsed = parse_message(WTB_LIST)
        offer_watches, classification = split_offer_watches(
            WTB_LIST,
            parsed,
            parsed["watches"],
        )

        assert classification == "request_intent"
        assert offer_watches == []
        assert split_dealer_list_message(WTB_LIST) is None

    def test_casual_multiline_message_is_not_split_into_offers(self) -> None:
        parsed = parse_message(CASUAL_MULTILINE)
        offer_watches, classification = split_offer_watches(
            CASUAL_MULTILINE,
            parsed,
            parsed["watches"],
        )

        assert parsed["message_type"] == "unknown"
        assert offer_watches == []
        assert classification is None
        assert split_dealer_list_message(CASUAL_MULTILINE) is None

    def test_dotted_reference_line_parses_with_brand_context(self) -> None:
        watch = parse_watch_line(
            "101.021 2011 Used Fullset HKD210k",
            current_brand="A. Lange & Söhne",
        )

        assert watch is not None
        assert watch["reference"] == "101.021"
        assert watch["brand"] == "A. Lange & Söhne"
        assert watch["condition"] == "Used"

    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_ingest_summary_counts_all_lange_offers(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_record_unknown: MagicMock,
        mock_record_unknown_nicknames: MagicMock,
    ) -> None:
        mock_find_watch.side_effect = [
            ({"id": f"watch-{index}"}, True) for index in range(9)
        ]
        mock_insert_offer.side_effect = [
            ({"id": f"offer-{index}"}, True) for index in range(9)
        ]
        mock_insert_import_log.return_value = {"id": "log-lange-list"}

        summary = ingest_message(
            LANGE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == 9
        assert summary["new_offers"] == 9
        assert mock_insert_offer.call_count == 9


class TestDealerListNicknameLearning:
    def test_large_lange_list_creates_many_watches_without_nickname_noise(self) -> None:
        result = parse_message(LARGE_LANGE_DEALER_LIST)

        assert result["message_type"] == "offer_list"
        assert len(result["watches"]) == 27
        assert all(watch.get("dealer_list_line") for watch in result["watches"])

    @patch("database.record_unknown_nickname_sighting")
    @patch("database.watch_identification_supported", return_value=True)
    def test_large_dealer_list_skips_unknown_nickname_learning(
        self,
        _mock_supported: MagicMock,
        mock_record_sighting: MagicMock,
    ) -> None:
        from unknown_nickname_intelligence import record_unknown_nicknames_for_watches

        watches = parse_message(LARGE_LANGE_DEALER_LIST)["watches"]

        recorded = record_unknown_nicknames_for_watches(
            watches,
            example_message=LARGE_LANGE_DEALER_LIST,
            dealer_id="dealer-1",
        )

        assert len(watches) >= 20
        assert recorded == []
        mock_record_sighting.assert_not_called()

    @patch("unknown_nickname_intelligence.identify_text", return_value=None)
    @patch("database.record_unknown_nickname_sighting")
    @patch("database.watch_identification_supported", return_value=True)
    def test_single_offer_still_learns_unknown_nicknames(
        self,
        _mock_supported: MagicMock,
        mock_record_sighting: MagicMock,
        _mock_identify: MagicMock,
    ) -> None:
        from unknown_nickname_intelligence import record_unknown_nicknames_for_watches

        mock_record_sighting.return_value = {"id": "unk-1"}
        watches = parse_message("Thunderbolt blue dial 25000")["watches"]

        recorded = record_unknown_nicknames_for_watches(
            watches,
            example_message="Thunderbolt blue dial 25000",
            dealer_id="dealer-1",
        )

        assert len(recorded) == 1
        mock_record_sighting.assert_called_once()

    def test_structured_dealer_list_line_does_not_extract_nickname_text(self) -> None:
        from unknown_nickname_intelligence import extract_unknown_nickname_text

        watch = parse_watch_line(
            "101.021 2011 Used Fullset HKD210k",
            current_brand="A. Lange & Söhne",
        )
        assert watch is not None
        watch["dealer_list_line"] = True
        watch["source_line"] = "101.021 2011 Used Fullset HKD210k"

        assert extract_unknown_nickname_text(watch) is None

    @patch("database.record_unknown_nickname_sighting")
    @patch("database.watch_identification_supported", return_value=True)
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_duplicate_offer", return_value=None)
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    def test_large_dealer_list_ingest_does_not_call_unknown_nickname_db(
        self,
        mock_record_unknown_brands: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_find_duplicate: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        _mock_supported: MagicMock,
        mock_record_sighting: MagicMock,
    ) -> None:
        watches_count = len(parse_message(LARGE_LANGE_DEALER_LIST)["watches"])
        unique_watch_count = 8
        unique_offer_count = 9
        mock_find_watch.side_effect = [
            ({"id": f"watch-{index}"}, True) for index in range(unique_watch_count)
        ]
        mock_insert_offer.side_effect = [
            ({"id": f"offer-{index}"}, True) for index in range(unique_offer_count)
        ]
        mock_insert_import_log.return_value = {"id": "log-large-lange-list"}

        summary = ingest_message(
            LARGE_LANGE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["status"] == "success"
        assert summary["watches_parsed"] == watches_count
        assert summary["new_offers"] == unique_offer_count
        assert summary["duplicate_offers"] == watches_count - unique_offer_count
        assert mock_find_watch.call_count == unique_watch_count
        assert mock_insert_offer.call_count == unique_offer_count
        mock_record_sighting.assert_not_called()


class TestDealerListBulkImport:
    def test_bulk_mode_threshold(self) -> None:
        watches = parse_message(LARGE_LANGE_DEALER_LIST)["watches"]

        assert dealer_list_line_count(watches) == 27
        assert is_dealer_list_bulk_import(watches) is True
        assert is_dealer_list_bulk_import(parse_message(LANGE_DEALER_LIST)["watches"]) is False

    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches")
    @patch("ingest._get_active_offers")
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_duplicate_offer", return_value=None)
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-bulk"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_large_dealer_list_skips_expensive_per_offer_intelligence(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_find_duplicate: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
    ) -> None:
        watches_count = len(parse_message(LARGE_LANGE_DEALER_LIST)["watches"])
        unique_watch_count = 8
        unique_offer_count = 9
        mock_find_watch.side_effect = [
            ({"id": f"watch-{index}"}, True) for index in range(unique_watch_count)
        ]
        mock_insert_offer.side_effect = [
            ({"id": f"offer-{index}"}, True) for index in range(unique_offer_count)
        ]
        mock_insert_import_log.return_value = {"id": "log-bulk-list"}

        summary = ingest_message(
            LARGE_LANGE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["bulk_import"] is True
        assert summary["watches_parsed"] == watches_count
        assert summary["new_offers"] == unique_offer_count
        assert summary["duplicate_offers"] == watches_count - unique_offer_count
        assert mock_insert_offer.call_count == unique_offer_count
        assert mock_find_watch.call_count == unique_watch_count
        mock_get_active_offers.assert_not_called()
        mock_process_matches.assert_not_called()
        mock_record_notifications.assert_not_called()
        assert all(
            row["price_label"] == "Deferred for bulk import"
            for row in summary["rows"]
            if "New offer" in row["results"]
        )

    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.insert_message", return_value={"id": "message-small"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch("ingest.record_unknown_brands_for_watches", return_value=[])
    @patch("ingest.record_unknown_nicknames_for_watches", return_value=[])
    def test_small_dealer_list_still_runs_normal_intelligence_path(
        self,
        mock_record_unknown_nicknames: MagicMock,
        mock_record_unknown_brands: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
    ) -> None:
        watches_count = len(parse_message(LANGE_DEALER_LIST)["watches"])
        mock_find_watch.side_effect = [
            ({"id": f"watch-{index}"}, True) for index in range(watches_count)
        ]
        mock_insert_offer.side_effect = [
            ({"id": f"offer-{index}"}, True) for index in range(watches_count)
        ]
        mock_insert_import_log.return_value = {"id": "log-small-list"}

        summary = ingest_message(
            LANGE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["bulk_import"] is False
        assert summary["watches_parsed"] == 9
        assert mock_get_active_offers.call_count == 9
        assert mock_process_matches.call_count == 9
        mock_record_notifications.assert_called_once()


class TestDealerListBulkCaching:
    def test_thirty_line_repeating_list_has_bulk_import(self) -> None:
        watches = parse_message(LARGE_30_LINE_DEALER_LIST)["watches"]

        assert len(watches) == 30
        assert is_dealer_list_bulk_import(watches) is True

    @patch("ingest.insert_offer")
    @patch("ingest.find_duplicate_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches")
    @patch("ingest._get_active_offers")
    @patch("ingest.update_import_log")
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_message", return_value={"id": "message-cache"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    def test_repeated_references_cache_watch_and_duplicate_lookups(
        self,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_insert_import_log: MagicMock,
        mock_update_import_log: MagicMock,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_find_watch: MagicMock,
        mock_find_duplicate: MagicMock,
        mock_insert_offer: MagicMock,
    ) -> None:
        unique_watch_keys: dict[tuple[str | None, str | None, str | None, str | None], str] = {}

        def fake_find_watch(
            brand: str | None = None,
            reference: str | None = None,
            model: str | None = None,
            dial: str | None = None,
            bracelet: str | None = None,
        ) -> tuple[dict[str, str], bool]:
            key = (
                (brand or "").strip().lower() or None,
                (reference or "").strip().lower() or None,
                (dial or "").strip().lower() or None,
                (bracelet or "").strip().lower() or None,
            )
            if key not in unique_watch_keys:
                unique_watch_keys[key] = f"watch-{len(unique_watch_keys)}"
                return (
                    {
                        "id": unique_watch_keys[key],
                        "brand": brand,
                        "reference": reference,
                    },
                    True,
                )
            return (
                {
                    "id": unique_watch_keys[key],
                    "brand": brand,
                    "reference": reference,
                },
                False,
            )

        created_offer_keys: set[tuple[Any, ...]] = set()
        offer_counter = 0

        def fake_find_duplicate(
            watch_id: str,
            dealer_id: str,
            *,
            original_price: int | None = None,
            original_currency: str | None = None,
            condition: str | None = None,
            card_date: str | None = None,
            production_year: int | None = None,
        ) -> dict[str, Any] | None:
            key = (
                watch_id,
                dealer_id,
                original_price,
                (original_currency or "").strip() or None,
                (condition or "").strip() or None,
                (card_date or "").strip() or None,
                production_year,
            )
            if key in created_offer_keys:
                return {"id": f"existing-{key[0]}-{production_year}", "watch_id": watch_id}
            return None

        def fake_insert_offer(
            message_id: str,
            watch_id: str,
            dealer_id: str,
            *,
            condition: str | None = None,
            production_year: int | None = None,
            card_date: str | None = None,
            notes: str | None = None,
            original_price: int | None = None,
            original_currency: str | None = None,
            usd_price: int | None = None,
            exchange_rate_to_usd: float | None = None,
            source_line: str | None = None,
            line_index: int = 0,
            skip_duplicate_check: bool = False,
            **_: Any,
        ) -> tuple[dict[str, str], bool]:
            nonlocal offer_counter
            key = (
                watch_id,
                dealer_id,
                original_price,
                (original_currency or "").strip() or None,
                (condition or "").strip() or None,
                (card_date or "").strip() or None,
                production_year,
            )
            created_offer_keys.add(key)
            offer_counter += 1
            return {"id": f"offer-{offer_counter}", "watch_id": watch_id}, True

        mock_find_watch.side_effect = fake_find_watch
        mock_find_duplicate.side_effect = fake_find_duplicate
        mock_insert_offer.side_effect = fake_insert_offer
        mock_insert_import_log.return_value = {"id": "log-cache-list"}

        summary = ingest_message(
            LARGE_30_LINE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
        )

        assert summary["bulk_import"] is True
        assert summary["watches_parsed"] == 30
        assert summary["new_offers"] == 9
        assert summary["duplicate_offers"] == 21
        assert mock_find_watch.call_count == 8
        assert mock_find_watch.call_count < 30
        assert mock_find_duplicate.call_count == 9
        assert mock_find_duplicate.call_count < 30
        assert mock_insert_offer.call_count == 9


class TestWhatsAppMessageDedup:
    @patch("ingest.insert_import_log")
    @patch("ingest.insert_message", return_value={"id": "message-1"})
    @patch("ingest.find_or_create_group", return_value="group-1")
    @patch("ingest.find_or_create_dealer", return_value=("dealer-1", CONTACT_TYPE_DEALER))
    @patch("ingest.find_duplicate_offer", return_value=None)
    @patch("ingest.insert_offer")
    @patch("ingest.find_or_create_watch")
    @patch("ingest.record_import_notifications")
    @patch("ingest.process_offer_request_matches", return_value=[])
    @patch("ingest._get_active_offers", return_value=[])
    def test_duplicate_whatsapp_message_id_skips_reingest(
        self,
        mock_get_active_offers: MagicMock,
        mock_process_matches: MagicMock,
        mock_record_notifications: MagicMock,
        mock_find_watch: MagicMock,
        mock_insert_offer: MagicMock,
        mock_find_duplicate: MagicMock,
        mock_find_dealer: MagicMock,
        mock_find_group: MagicMock,
        mock_insert_message: MagicMock,
        mock_insert_import_log: MagicMock,
    ) -> None:
        watches_count = len(parse_message(LARGE_30_LINE_DEALER_LIST)["watches"])
        assert watches_count == 30
        mock_find_watch.side_effect = [
            ({"id": f"watch-{index}"}, True) for index in range(8)
        ]
        mock_insert_offer.side_effect = [
            ({"id": f"offer-{index}"}, True) for index in range(9)
        ]
        mock_insert_import_log.return_value = {"id": "log-first", "summary": {}}

        first = ingest_message(
            LARGE_30_LINE_DEALER_LIST,
            group_name="HK Dealers",
            dealer_whatsapp="+85291234567",
            whatsapp_message_id="WA-MSG-LANGE-001",
        )

        assert first["status"] == "success"
        assert first.get("already_processed") is not True
        assert mock_insert_message.call_count == 1

        with patch(
            "ingest.find_message_by_whatsapp_id",
            return_value={"id": "message-1", "whatsapp_message_id": "WA-MSG-LANGE-001"},
        ), patch(
            "ingest.find_import_log_by_message_id",
            return_value={
                "id": "log-first",
                "message_id": "message-1",
                "summary": first,
            },
        ):
            second = ingest_message(
                LARGE_30_LINE_DEALER_LIST,
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
                whatsapp_message_id="WA-MSG-LANGE-001",
            )

        assert second["status"] == "already_imported"
        assert second["already_processed"] is True
        assert second["message_id"] == "message-1"
        assert second["import_log_id"] == "log-first"
        assert mock_insert_message.call_count == 1

    @patch("evolution_webhook.collect_message")
    def test_webhook_replay_returns_already_imported_without_reingest(
        self,
        mock_collect: MagicMock,
    ) -> None:
        from evolution_webhook import handle_evolution_webhook

        mock_collect.return_value = {
            "status": "already_imported",
            "already_processed": True,
            "message_id": "message-1",
            "import_log_id": "log-first",
            "watches_parsed": 30,
            "new_offers": 9,
            "duplicate_offers": 21,
        }

        payload = {
            "event": "messages.upsert",
            "instance": "mrv4ult",
            "data": {
                "key": {
                    "remoteJid": "120363000000000000@g.us",
                    "fromMe": False,
                    "id": "WA-MSG-LANGE-001",
                    "participantAlt": "+85291234567",
                },
                "message": {"conversation": LARGE_30_LINE_DEALER_LIST},
                "messageTimestamp": 1719496800,
                "pushName": "HK Dealer",
                "subject": "HK Dealers",
            },
        }

        result = handle_evolution_webhook(payload)

        assert result["status"] == "already_imported"
        assert result["already_processed"] is True
        assert result["whatsapp_message_id"] == "WA-MSG-LANGE-001"
        mock_collect.assert_called_once()
        whatsapp_message = mock_collect.call_args.args[0]
        assert whatsapp_message.whatsapp_message_id == "WA-MSG-LANGE-001"


class TestIngestLifecycleInvestigation:
    def test_insert_offer_does_not_trigger_ingest(self) -> None:
        import database
        import notifications

        assert "ingest_message" not in open(database.__file__, encoding="utf-8").read()
        assert "collect_message" not in open(notifications.__file__, encoding="utf-8").read()

    def test_lifecycle_logs_repeated_whatsapp_message_id_in_same_process(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        from ingest_lifecycle import reset_import_trace_state

        reset_import_trace_state()
        caplog.set_level(logging.INFO, logger="mrv4ult.ingest.lifecycle")

        with patch("ingest.find_message_by_whatsapp_id", return_value=None), patch(
            "ingest.insert_message",
            return_value={"id": "message-a"},
        ), patch("ingest.find_or_create_group", return_value="group-1"), patch(
            "ingest.find_or_create_dealer",
            return_value=("dealer-1", CONTACT_TYPE_DEALER),
        ), patch("ingest.find_duplicate_offer", return_value=None), patch(
            "ingest.insert_offer",
            return_value=({"id": "offer-1"}, True),
        ), patch("ingest.find_or_create_watch", return_value=({"id": "watch-1"}, True)), patch(
            "ingest.insert_import_log",
            return_value={"id": "log-a", "summary": {}},
        ), patch("ingest.record_import_notifications"), patch(
            "ingest.process_offer_request_matches",
            return_value=[],
        ), patch("ingest._get_active_offers", return_value=[]):
            ingest_message(
                SINGLE_OFFER,
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
                whatsapp_message_id="WA-DUP-TEST",
                source="whatsapp_webhook",
            )
            ingest_message(
                SINGLE_OFFER,
                group_name="HK Dealers",
                dealer_whatsapp="+85291234567",
                whatsapp_message_id="WA-DUP-TEST",
                source="whatsapp_webhook",
            )

        assert any("REPEATED IMPORT: whatsapp_message_id=WA-DUP-TEST" in record.message for record in caplog.records)
        assert any("START IMPORT:" in record.message for record in caplog.records)
        assert any("END IMPORT:" in record.message for record in caplog.records)
        reset_import_trace_state()
