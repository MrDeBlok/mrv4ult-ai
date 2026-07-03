"""Tests for Sprint 48.2 dashboard batched Deal Analysis comparable lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app import build_deal_analysis_cards
from condition_normalizer import NEW_CONDITION, PRE_OWNED_CONDITION
from deal_market_lookup import (
    DealMarketPreload,
    build_deal_market_preload,
    load_active_offer_pools_by_watch_ids,
    resolve_deal_market_context,
)
from todays_best_deals import load_dashboard_todays_best_deals

WATCH_A = "watch-a"
WATCH_B = "watch-b"
OFFER_A = "offer-a"
OFFER_B = "offer-b"
OFFER_C = "offer-c"


def _summary_row(
    *,
    offer_id: str,
    usd_price: int,
    condition: str,
    reference: str = "126331",
) -> dict:
    return {
        "brand": "Rolex",
        "reference": reference,
        "condition": condition,
        "raw_condition": condition,
        "usd_price": usd_price,
        "previous_lowest_usd": "N/A",
        "price_label": "No comparables",
        "market_condition": None,
        "offer_id": offer_id,
    }


def _import_log(import_id: str, rows: list[dict]) -> dict:
    return {
        "id": import_id,
        "import_time": f"2026-07-01T{import_id[-2:]}:00:00+00:00",
        "dealer_whatsapp": "+85291234567",
        "dealer_alias": "Dealer A",
        "summary": {
            "status": "success",
            "rows": rows,
            "parsed_watches": rows,
        },
    }


class TestBatchedComparablePreload:
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    def test_build_deal_market_preload_batches_watch_ids(
        self,
        mock_load_pools: MagicMock,
        mock_get_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {
            OFFER_A: {"watch_id": WATCH_A},
            OFFER_B: {"watch_id": WATCH_B},
            OFFER_C: {"watch_id": WATCH_A},
        }
        mock_load_pools.return_value = {
            WATCH_A: [(OFFER_C, 24_000, NEW_CONDITION)],
            WATCH_B: [(OFFER_B, 22_000, PRE_OWNED_CONDITION)],
        }

        preload = build_deal_market_preload(
            [
                _summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION),
                _summary_row(offer_id=OFFER_B, usd_price=21_000, condition=PRE_OWNED_CONDITION),
                _summary_row(offer_id=OFFER_C, usd_price=20_000, condition=NEW_CONDITION),
            ]
        )

        mock_get_offers.assert_called_once()
        assert set(mock_get_offers.call_args.args[0]) == {OFFER_A, OFFER_B, OFFER_C}
        mock_load_pools.assert_called_once()
        assert set(mock_load_pools.call_args.args[0]) == {WATCH_A, WATCH_B}
        assert preload.offer_watch_ids[OFFER_A] == WATCH_A
        assert preload.active_pools_by_watch_id[WATCH_A][0][0] == OFFER_C

    @patch("ingest._get_active_offers")
    @patch("deal_market_lookup.get_offers_by_ids")
    @patch("deal_market_lookup.load_active_offer_pools_by_watch_ids")
    def test_dashboard_todays_best_deals_uses_batched_lookup(
        self,
        mock_load_pools: MagicMock,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        rows_a = [
            _summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION),
            _summary_row(offer_id=OFFER_B, usd_price=21_500, condition=NEW_CONDITION, reference="126334"),
        ]
        rows_b = [
            _summary_row(offer_id=OFFER_C, usd_price=20_500, condition=PRE_OWNED_CONDITION, reference="15500"),
        ]
        import_logs = [_import_log("log-01", rows_a), _import_log("log-02", rows_b)]

        mock_get_offers.return_value = {
            OFFER_A: {"watch_id": WATCH_A},
            OFFER_B: {"watch_id": WATCH_A},
            OFFER_C: {"watch_id": WATCH_B},
        }
        mock_load_pools.return_value = {
            WATCH_A: [
                (OFFER_A, 24_000, NEW_CONDITION),
                (OFFER_B, 23_500, NEW_CONDITION),
            ],
            WATCH_B: [
                (OFFER_C, 22_000, PRE_OWNED_CONDITION),
            ],
        }

        deals, _strong = load_dashboard_todays_best_deals(None, import_logs)

        mock_get_offers.assert_called_once()
        mock_load_pools.assert_called_once()
        mock_get_active_offers.assert_not_called()
        assert isinstance(deals, list)

    @patch("ingest._get_active_offers")
    @patch("deal_market_lookup.get_offers_by_ids")
    def test_activity_detail_without_preload_uses_live_lookup(
        self,
        mock_get_offers: MagicMock,
        mock_get_active_offers: MagicMock,
    ) -> None:
        mock_get_offers.return_value = {OFFER_A: {"watch_id": WATCH_A}}
        mock_get_active_offers.return_value = [
            ("other-offer", 24_000, NEW_CONDITION),
        ]
        summary = {
            "status": "success",
            "rows": [
                _summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION),
            ],
        }

        build_deal_analysis_cards(summary)

        mock_get_offers.assert_called_once()
        mock_get_active_offers.assert_called_once_with(WATCH_A)


class TestPreloadedConditionSafety:
    def _preload(
        self,
        *,
        current_offer_id: str,
        watch_id: str,
        pool: list[tuple[str, int, str | None]],
    ) -> DealMarketPreload:
        return DealMarketPreload(
            offer_watch_ids={current_offer_id: watch_id},
            active_pools_by_watch_id={watch_id: pool},
        )

    def test_new_offer_only_compares_to_new_comparables(self) -> None:
        preload = self._preload(
            current_offer_id=OFFER_A,
            watch_id=WATCH_A,
            pool=[
                (OFFER_A, 23_000, NEW_CONDITION),
                ("other-new", 24_000, NEW_CONDITION),
                ("other-pre", 22_000, PRE_OWNED_CONDITION),
                ("bad-price", 0, NEW_CONDITION),
            ],
        )
        row = _summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION)

        context = resolve_deal_market_context(row, row, market_preload=preload)

        assert context.comparison_safe is True
        assert context.market_usd == 24_000
        assert context.offer_condition == NEW_CONDITION

    def test_pre_owned_offer_only_compares_to_pre_owned_comparables(self) -> None:
        preload = self._preload(
            current_offer_id=OFFER_B,
            watch_id=WATCH_B,
            pool=[
                (OFFER_B, 21_000, PRE_OWNED_CONDITION),
                ("other-new", 24_000, NEW_CONDITION),
                ("other-pre", 22_500, PRE_OWNED_CONDITION),
            ],
        )
        row = _summary_row(offer_id=OFFER_B, usd_price=21_000, condition=PRE_OWNED_CONDITION)

        context = resolve_deal_market_context(row, row, market_preload=preload)

        assert context.comparison_safe is True
        assert context.market_usd == 22_500
        assert context.offer_condition == PRE_OWNED_CONDITION

    def test_current_offer_does_not_compare_against_itself(self) -> None:
        preload = self._preload(
            current_offer_id=OFFER_A,
            watch_id=WATCH_A,
            pool=[(OFFER_A, 23_000, NEW_CONDITION)],
        )
        row = _summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION)

        context = resolve_deal_market_context(row, row, market_preload=preload)

        assert context.comparison_safe is False
        assert context.insufficient_market_data is True

    @patch("database.get_client")
    def test_batch_loader_uses_light_projection(
        self,
        mock_get_client: MagicMock,
    ) -> None:
        mock_execute = MagicMock()
        mock_execute.data = []
        mock_query = MagicMock()
        mock_query.execute.return_value = mock_execute
        mock_query.eq.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_get_client.return_value.table.return_value.select.return_value = mock_query

        load_active_offer_pools_by_watch_ids([WATCH_A, WATCH_B])

        select_arg = mock_get_client.return_value.table.return_value.select.call_args.args[0]
        assert "watch_id" in select_arg
        assert "usd_price" in select_arg
        assert "condition" in select_arg
        assert "messages(" not in select_arg
        mock_query.in_.assert_called_once_with("watch_id", [WATCH_A, WATCH_B])


class TestDashboardKpiUnchanged:
    @patch("deal_market_lookup.build_deal_market_preload")
    @patch("app.build_deal_analysis_cards")
    def test_dashboard_kpi_count_matches_rankable_deals(
        self,
        mock_build_cards: MagicMock,
        mock_build_preload: MagicMock,
    ) -> None:
        mock_build_preload.return_value = DealMarketPreload({}, {})
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
            },
            {
                "condition_is_known": True,
                "show_market_metrics": False,
                "recommendation": "Expensive",
                "recommendation_class": "expensive",
                "offer_price": "$25,000",
                "market_price": "Unknown",
                "potential_profit": None,
                "condition_label": "New",
            },
        ]
        import_log = _import_log(
            "log-01",
            [_summary_row(offer_id=OFFER_A, usd_price=23_000, condition=NEW_CONDITION)],
        )

        deals, strong_count = load_dashboard_todays_best_deals(None, [import_log])

        assert len(deals) == 1
        assert strong_count == 1
        assert mock_build_preload.call_count == 1
        assert mock_build_cards.call_count == 1
        assert mock_build_cards.call_args.kwargs["market_preload"] is mock_build_preload.return_value
