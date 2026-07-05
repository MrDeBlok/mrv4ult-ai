"""P0 regression: dashboard offer preload must not crash on large or failing batches."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from condition_normalizer import NEW_CONDITION
from database import OFFERS_BY_IDS_CHUNK_SIZE, get_offers_by_ids
from todays_best_deals import load_dashboard_todays_best_deals

OFFER_A = "11111111-1111-1111-1111-111111111111"
OFFER_B = "22222222-2222-2222-2222-222222222222"
WATCH_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _mock_offers_execute(rows: list[dict[str, Any]] | None = None) -> MagicMock:
    execute = MagicMock()
    execute.data = rows or []
    return execute


def _summary_row(*, offer_id: str, usd_price: int = 23_000) -> dict[str, Any]:
    return {
        "brand": "Rolex",
        "reference": "126610LN",
        "condition": NEW_CONDITION,
        "raw_condition": NEW_CONDITION,
        "usd_price": usd_price,
        "previous_lowest_usd": "N/A",
        "price_label": "No comparables",
        "market_condition": None,
        "offer_id": offer_id,
    }


def _import_log(import_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": import_id,
        "import_time": "2026-07-05T12:00:00+00:00",
        "dealer_whatsapp": "+85291234567",
        "dealer_alias": "Dealer A",
        "summary": {
            "status": "success",
            "rows": rows,
            "parsed_watches": rows,
        },
    }


class TestGetOffersByIdsChunking:
    @patch("database.get_client")
    def test_get_offers_by_ids_chunks_large_id_lists(self, mock_get_client: MagicMock) -> None:
        offer_ids = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(250)]
        execute = _mock_offers_execute(
            [{"id": offer_ids[0], "watch_id": WATCH_A, "usd_price": 1000}]
        )
        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.execute.return_value = execute

        result = get_offers_by_ids(offer_ids)

        in_calls = mock_get_client.return_value.table.return_value.select.return_value.in_.call_args_list
        assert len(in_calls) == 3
        assert len(in_calls[0].args[1]) == OFFERS_BY_IDS_CHUNK_SIZE
        assert len(in_calls[1].args[1]) == OFFERS_BY_IDS_CHUNK_SIZE
        assert len(in_calls[2].args[1]) == 50
        assert result[offer_ids[0]]["watch_id"] == WATCH_A

    @patch("database.get_client")
    def test_get_offers_by_ids_deduplicates_and_skips_empty_ids(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        execute = _mock_offers_execute([{"id": OFFER_A, "watch_id": WATCH_A}])
        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.execute.return_value = execute

        get_offers_by_ids(["", "  ", OFFER_A, OFFER_A, OFFER_B])

        chunk_ids = mock_get_client.return_value.table.return_value.select.return_value.in_.call_args.args[1]
        assert chunk_ids == [OFFER_A, OFFER_B]

    @patch("database.get_client")
    def test_get_offers_by_ids_continues_when_one_chunk_fails(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        first_chunk = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(OFFERS_BY_IDS_CHUNK_SIZE)]
        second_chunk = [f"{index:08d}-0000-4000-8000-000000000001" for index in range(10)]
        offer_ids = first_chunk + second_chunk

        success_execute = _mock_offers_execute(
            [{"id": first_chunk[0], "watch_id": WATCH_A, "usd_price": 12000}]
        )

        def execute_side_effect() -> MagicMock:
            call_index = execute_side_effect.calls
            execute_side_effect.calls += 1
            if call_index == 0:
                return success_execute
            raise RuntimeError("400 Bad Request: JSON could not be generated")

        execute_side_effect.calls = 0
        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.execute.side_effect = (
            execute_side_effect
        )

        result = get_offers_by_ids(offer_ids)

        assert result == {first_chunk[0]: {"id": first_chunk[0], "watch_id": WATCH_A, "usd_price": 12000}}
        assert mock_get_client.return_value.table.return_value.select.return_value.in_.call_count == 2


class TestDashboardPreloadResilience:
    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    @patch("database.get_client")
    def test_dashboard_does_not_crash_when_one_preload_chunk_fails(
        self,
        mock_get_client: MagicMock,
        mock_load_pools: MagicMock,
    ) -> None:
        first_chunk = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(OFFERS_BY_IDS_CHUNK_SIZE)]
        rows = [_summary_row(offer_id=first_chunk[0]), _summary_row(offer_id=OFFER_B, usd_price=21_000)]
        import_logs = [_import_log("log-chunk-fail", rows)]

        success_execute = _mock_offers_execute(
            [{"id": first_chunk[0], "watch_id": WATCH_A, "usd_price": 23000}]
        )

        def execute_side_effect() -> MagicMock:
            if execute_side_effect.calls == 0:
                execute_side_effect.calls += 1
                return success_execute
            raise RuntimeError("400 Bad Request: JSON could not be generated")

        execute_side_effect.calls = 0
        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.execute.side_effect = (
            execute_side_effect
        )
        mock_load_pools.return_value = {WATCH_A: []}

        deals, strong_count = load_dashboard_todays_best_deals(None, import_logs)

        assert isinstance(deals, list)
        assert isinstance(strong_count, int)

    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    @patch("database.get_client")
    def test_dashboard_does_not_crash_with_many_offer_ids(
        self,
        mock_get_client: MagicMock,
        mock_load_pools: MagicMock,
    ) -> None:
        offer_ids = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(220)]
        rows = [_summary_row(offer_id=offer_id) for offer_id in offer_ids[:3]]
        import_logs = [_import_log("log-many", rows)]

        offers_by_id = {
            row["offer_id"]: {"id": row["offer_id"], "watch_id": WATCH_A}
            for row in rows
        }

        def execute_side_effect() -> MagicMock:
            in_args = mock_get_client.return_value.table.return_value.select.return_value.in_.call_args.args[1]
            payload = [{"id": offer_id, "watch_id": WATCH_A} for offer_id in in_args if offer_id in offers_by_id]
            return _mock_offers_execute(payload)

        mock_get_client.return_value.table.return_value.select.return_value.in_.return_value.execute.side_effect = (
            execute_side_effect
        )
        mock_load_pools.return_value = {WATCH_A: []}

        deals, strong_count = load_dashboard_todays_best_deals(None, import_logs)

        assert isinstance(deals, list)
        assert isinstance(strong_count, int)

    @patch("deal_market_lookup.build_deal_market_preload")
    def test_dashboard_does_not_crash_when_preload_raises(
        self,
        mock_build_preload: MagicMock,
    ) -> None:
        mock_build_preload.side_effect = RuntimeError("preload failed")
        import_logs = [_import_log("log-fail", [_summary_row(offer_id=OFFER_A)])]

        deals, strong_count = load_dashboard_todays_best_deals(None, import_logs)

        assert deals == []
        assert strong_count == 0

    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("app.build_deal_analysis_cards")
    def test_dashboard_renders_todays_best_deals_when_preload_succeeds(
        self,
        mock_build_cards: MagicMock,
        mock_get_offers: MagicMock,
        mock_load_pools: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {OFFER_A: {"watch_id": WATCH_A}}
        mock_load_pools.return_value = {WATCH_A: [(OFFER_B, 24_000, NEW_CONDITION)]}
        mock_build_cards.return_value = [
            {
                "condition_is_known": True,
                "show_market_metrics": True,
                "recommendation": "Excellent Buy",
                "recommendation_class": "excellent",
                "offer_price": "$23,000",
                "market_price": "$24,000",
                "potential_profit": "$1,000",
                "condition_label": "New",
            }
        ]
        import_logs = [_import_log("log-ok", [_summary_row(offer_id=OFFER_A)])]

        deals, strong_count = load_dashboard_todays_best_deals(None, import_logs)

        assert len(deals) == 1
        assert strong_count == 1
        assert mock_build_cards.call_args.kwargs["market_preload"].offer_watch_ids[OFFER_A] == WATCH_A

    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("app.build_deal_analysis_cards")
    def test_dashboard_continues_with_reduced_results_when_offer_chunk_fails(
        self,
        mock_build_cards: MagicMock,
        mock_get_offers: MagicMock,
        mock_load_pools: MagicMock,
    ) -> None:
        rows = [
            _summary_row(offer_id=OFFER_A),
            _summary_row(offer_id=OFFER_B, usd_price=21_000),
        ]
        import_logs = [_import_log("log-partial", rows)]

        mock_get_offers.return_value = {OFFER_A: {"watch_id": WATCH_A}}
        mock_load_pools.return_value = {WATCH_A: [(OFFER_B, 24_000, NEW_CONDITION)]}
        mock_build_cards.return_value = [
            {
                "condition_is_known": True,
                "show_market_metrics": True,
                "recommendation": "Good Buy",
                "recommendation_class": "good",
                "offer_price": "$23,000",
                "market_price": "$24,000",
                "potential_profit": "$1,000",
                "condition_label": "New",
            },
            {
                "condition_is_known": True,
                "show_market_metrics": False,
                "recommendation": "Expensive",
                "recommendation_class": "expensive",
                "offer_price": "$21,000",
                "market_price": "Unknown",
                "potential_profit": None,
                "condition_label": "New",
            },
        ]

        deals, _strong_count = load_dashboard_todays_best_deals(None, import_logs)

        assert isinstance(deals, list)
        preload = mock_build_cards.call_args.kwargs["market_preload"]
        assert OFFER_A in preload.offer_watch_ids
        assert OFFER_B not in preload.offer_watch_ids
