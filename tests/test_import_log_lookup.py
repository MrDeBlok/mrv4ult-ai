"""Tests for import log and message batch lookup helpers."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from database import (
    IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE,
    IMPORT_LOG_PREVIEW_COLUMNS,
    IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE,
    LOOKUP_IDS_CHUNK_SIZE,
    MESSAGE_PREVIEW_COLUMNS,
    _import_log_source_resolution_select_columns,
    _normalize_lookup_ids,
    get_import_logs_by_ids,
    get_import_logs_by_message_ids,
    get_import_logs_for_source_resolution,
    get_messages_by_ids,
)

IMPORT_LOG_ID_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
IMPORT_LOG_ID_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
MESSAGE_ID_1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
MESSAGE_ID_2 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _mock_table_client(*, execute_results: list[MagicMock]) -> MagicMock:
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_in = MagicMock()
    mock_client.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.in_.return_value = mock_in
    mock_in.execute.side_effect = execute_results
    return mock_client


def _mock_import_logs_by_message_id_client(*, execute_results: list[MagicMock]) -> MagicMock:
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_in = MagicMock()
    mock_order = MagicMock()
    mock_client.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.in_.return_value = mock_in
    mock_in.order.return_value = mock_order
    mock_order.execute.side_effect = execute_results
    return mock_client


class TestGetImportLogsByMessageIds:
    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_empty_input_skips_database_call(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        result = get_import_logs_by_message_ids([])

        assert result == {}
        mock_get_client.assert_not_called()

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_duplicate_and_empty_ids_are_deduplicated(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        mock_client = _mock_import_logs_by_message_id_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": IMPORT_LOG_ID_1,
                            "message_id": MESSAGE_ID_1,
                            "watches_parsed": 1,
                            "status": "success",
                        }
                    ]
                )
            ]
        )
        mock_get_client.return_value = mock_client

        result = get_import_logs_by_message_ids(
            ["", None, MESSAGE_ID_1, MESSAGE_ID_1.upper(), "  "],
        )

        assert result == {
            MESSAGE_ID_1.lower(): {
                "id": IMPORT_LOG_ID_1,
                "message_id": MESSAGE_ID_1,
                "watches_parsed": 1,
                "status": "success",
            }
        }
        mock_in = mock_client.table.return_value.select.return_value.in_
        assert mock_in.call_count == 1
        assert mock_in.call_args.args == ("message_id", [MESSAGE_ID_1.lower()])
        assert len(mock_in.call_args.args[1]) <= IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE
        assert mock_in.return_value.order.return_value.execute.call_count == 1

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_large_message_id_list_is_queried_in_bounded_batches(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        id_count = (IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE * 2) + 5
        message_ids = [str(uuid.uuid4()) for _ in range(id_count)]
        batch_count = (id_count + IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE - 1) // IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE

        mock_client = _mock_import_logs_by_message_id_client(
            execute_results=[MagicMock(data=[]) for _ in range(batch_count)]
        )
        mock_get_client.return_value = mock_client

        result = get_import_logs_by_message_ids(message_ids)

        assert result == {}
        mock_in = mock_client.table.return_value.select.return_value.in_
        assert mock_in.call_count == batch_count
        in_calls = mock_in.call_args_list
        assert all(len(call.args[1]) <= IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE for call in in_calls)
        assert sum(len(call.args[1]) for call in in_calls) == id_count

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_batches_are_merged_and_latest_log_per_message_is_kept(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        batch_one_ids = [str(uuid.uuid4()) for _ in range(IMPORT_LOG_MESSAGE_IDS_BATCH_SIZE)]
        batch_two_ids = [str(uuid.uuid4())]
        message_ids = batch_one_ids + batch_two_ids

        mock_get_client.return_value = _mock_import_logs_by_message_id_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": IMPORT_LOG_ID_1,
                            "message_id": batch_one_ids[0],
                            "watches_parsed": 1,
                            "status": "success",
                        }
                    ]
                ),
                MagicMock(
                    data=[
                        {
                            "id": IMPORT_LOG_ID_2,
                            "message_id": batch_two_ids[0],
                            "watches_parsed": 2,
                            "status": "success",
                        },
                        {
                            "id": "older-log",
                            "message_id": batch_two_ids[0],
                            "watches_parsed": 1,
                            "status": "success",
                        },
                    ]
                ),
            ]
        )

        result = get_import_logs_by_message_ids(message_ids)

        assert set(result.keys()) == {
            batch_one_ids[0].lower(),
            batch_two_ids[0].lower(),
        }
        assert result[batch_one_ids[0].lower()]["id"] == IMPORT_LOG_ID_1
        assert result[batch_two_ids[0].lower()]["id"] == IMPORT_LOG_ID_2

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_database_errors_are_not_swallowed(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_import_logs_by_message_id_client(
            execute_results=[RuntimeError("JSON could not be generated")]
        )

        with pytest.raises(RuntimeError, match="JSON could not be generated"):
            get_import_logs_by_message_ids([MESSAGE_ID_1])


def _mock_import_logs_by_id_client(*, execute_results: list[MagicMock]) -> MagicMock:
    mock_client = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_in = MagicMock()
    mock_client.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.in_.return_value = mock_in
    mock_in.execute.side_effect = execute_results
    return mock_client


class TestGetImportLogsForSourceResolution:
    def test_slim_select_columns_exclude_summary_and_message_id(self) -> None:
        columns = _import_log_source_resolution_select_columns()

        assert "summary" not in columns
        assert "message_id" not in columns
        assert "id" in columns
        assert "status" in columns
        assert "watches_parsed" in columns
        assert "imported_by_user_id" in columns

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_empty_input_skips_database_call(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        result = get_import_logs_for_source_resolution([])

        assert result == {}
        mock_get_client.assert_not_called()

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_selects_slim_columns_without_summary(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        mock_get_client.return_value = _mock_import_logs_by_id_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": IMPORT_LOG_ID_1,
                            "watches_parsed": 1,
                            "status": "success",
                            "imported_by_user_id": "user-1",
                        }
                    ]
                )
            ]
        )

        result = get_import_logs_for_source_resolution([IMPORT_LOG_ID_1])

        assert result == {
            IMPORT_LOG_ID_1: {
                "id": IMPORT_LOG_ID_1,
                "watches_parsed": 1,
                "status": "success",
                "imported_by_user_id": "user-1",
            }
        }
        mock_select = mock_get_client.return_value.table.return_value.select
        mock_select.assert_called_once_with(_import_log_source_resolution_select_columns())
        assert "summary" not in mock_select.call_args.args[0]

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_large_id_list_is_queried_in_bounded_batches(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        id_count = (IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE * 2) + 5
        import_log_ids = [str(uuid.uuid4()) for _ in range(id_count)]
        batch_count = (
            id_count + IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE - 1
        ) // IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE

        mock_get_client.return_value = _mock_import_logs_by_id_client(
            execute_results=[MagicMock(data=[]) for _ in range(batch_count)]
        )

        result = get_import_logs_for_source_resolution(import_log_ids)

        assert result == {}
        mock_in = mock_get_client.return_value.table.return_value.select.return_value.in_
        assert mock_in.call_count == batch_count
        in_calls = mock_in.call_args_list
        assert all(len(call.args[1]) <= IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE for call in in_calls)
        assert sum(len(call.args[1]) for call in in_calls) == id_count

    @patch("database.execute_postgrest_read", side_effect=lambda _operation, fn, **kwargs: fn())
    @patch("database.get_client")
    def test_batches_are_merged_by_import_log_id(
        self,
        mock_get_client: MagicMock,
        _mock_execute_read: MagicMock,
    ) -> None:
        batch_one_ids = [str(uuid.uuid4()) for _ in range(IMPORT_LOG_SOURCE_RESOLUTION_IDS_BATCH_SIZE)]
        batch_two_ids = [str(uuid.uuid4())]
        import_log_ids = batch_one_ids + batch_two_ids

        mock_get_client.return_value = _mock_import_logs_by_id_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": batch_one_ids[0],
                            "watches_parsed": 1,
                            "status": "success",
                            "imported_by_user_id": "owner-1",
                        }
                    ]
                ),
                MagicMock(
                    data=[
                        {
                            "id": batch_two_ids[0],
                            "watches_parsed": 2,
                            "status": "warning",
                            "imported_by_user_id": "owner-2",
                        }
                    ]
                ),
            ]
        )

        result = get_import_logs_for_source_resolution(import_log_ids)

        assert set(result.keys()) == {batch_one_ids[0], batch_two_ids[0]}
        assert result[batch_one_ids[0]]["imported_by_user_id"] == "owner-1"
        assert result[batch_two_ids[0]]["status"] == "warning"


class TestNormalizeLookupIds:
    def test_empty_list_returns_empty(self) -> None:
        assert _normalize_lookup_ids([], require_uuid=True) == []

    def test_filters_none_and_empty_strings(self) -> None:
        assert _normalize_lookup_ids(
            [None, "", "  ", IMPORT_LOG_ID_1],
            require_uuid=True,
        ) == [IMPORT_LOG_ID_1.lower()]

    def test_deduplicates_ids(self) -> None:
        assert _normalize_lookup_ids(
            [IMPORT_LOG_ID_1, IMPORT_LOG_ID_1.upper()],
            require_uuid=True,
        ) == [IMPORT_LOG_ID_1.lower()]
        assert _normalize_lookup_ids(
            [IMPORT_LOG_ID_1, IMPORT_LOG_ID_2],
            require_uuid=True,
        ) == [IMPORT_LOG_ID_1.lower(), IMPORT_LOG_ID_2.lower()]

    def test_skips_malformed_uuids_when_required(self) -> None:
        with patch("database.logger") as mock_logger:
            normalized = _normalize_lookup_ids(
                ["log-1", "not-a-uuid", IMPORT_LOG_ID_1],
                require_uuid=True,
            )
        assert normalized == [IMPORT_LOG_ID_1.lower()]
        mock_logger.warning.assert_called_once()


class TestGetImportLogsByIds:
    @patch("database.get_client")
    def test_empty_id_list_skips_database_call(self, mock_get_client: MagicMock) -> None:
        result = get_import_logs_by_ids([])

        assert result == {}
        mock_get_client.assert_not_called()

    @patch("database.get_client")
    def test_invalid_ids_are_filtered_without_query(self, mock_get_client: MagicMock) -> None:
        result = get_import_logs_by_ids(["", None, "log-1", "bad"])

        assert result == {}
        mock_get_client.assert_not_called()

    @patch("database.get_client")
    def test_valid_uuid_list_uses_lightweight_projection(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value = _mock_table_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": IMPORT_LOG_ID_1,
                            "message_id": MESSAGE_ID_1,
                        }
                    ]
                )
            ]
        )

        result = get_import_logs_by_ids(
            [IMPORT_LOG_ID_1],
            select_fields=IMPORT_LOG_PREVIEW_COLUMNS,
        )

        assert result == {
            IMPORT_LOG_ID_1: {
                "id": IMPORT_LOG_ID_1,
                "message_id": MESSAGE_ID_1,
            }
        }
        mock_get_client.return_value.table.assert_called_once_with("import_logs")
        select_args = mock_get_client.return_value.table.return_value.select.call_args
        assert select_args.args[0] == IMPORT_LOG_PREVIEW_COLUMNS
        in_args = mock_get_client.return_value.table.return_value.select.return_value.in_.call_args
        assert in_args.args == ("id", [IMPORT_LOG_ID_1.lower()])

    @patch("database._query_table_in_id_chunks", return_value=[])
    def test_large_id_list_is_chunked(self, mock_chunk_lookup: MagicMock) -> None:
        id_count = (LOOKUP_IDS_CHUNK_SIZE * 2) + 5
        ids = [str(uuid.uuid4()) for _ in range(id_count)]

        get_import_logs_by_ids(ids, select_fields=IMPORT_LOG_PREVIEW_COLUMNS)

        mock_chunk_lookup.assert_called_once()
        assert mock_chunk_lookup.call_args.args[2] == _normalize_lookup_ids(
            ids,
            require_uuid=True,
        )
        assert len(mock_chunk_lookup.call_args.args[2]) == id_count

    @patch("database.get_client")
    def test_failed_chunk_returns_partial_results(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value = _mock_table_client(
            execute_results=[
                MagicMock(data=[{"id": IMPORT_LOG_ID_1, "message_id": MESSAGE_ID_1}]),
                RuntimeError("chunk failed"),
            ]
        )
        ids = [IMPORT_LOG_ID_1] + [
            f"{index:08x}-0000-4000-8000-000000000001"
            for index in range(LOOKUP_IDS_CHUNK_SIZE)
        ]

        result = get_import_logs_by_ids(ids, select_fields=IMPORT_LOG_PREVIEW_COLUMNS)

        assert IMPORT_LOG_ID_1 in result
        assert len(result) == 1


class TestQueryTableInIdChunks:
    @patch("database.get_client")
    def test_chunks_large_lookup_lists(self, mock_get_client: MagicMock) -> None:
        from database import _query_table_in_id_chunks

        id_count = (LOOKUP_IDS_CHUNK_SIZE * 2) + 5
        ids = [str(uuid.uuid4()) for _ in range(id_count)]
        normalized = _normalize_lookup_ids(ids, require_uuid=True)
        assert len(normalized) == id_count

        mock_get_client.return_value = _mock_table_client(
            execute_results=[MagicMock(data=[]) for _ in range(3)]
        )

        _query_table_in_id_chunks(
            "import_logs",
            IMPORT_LOG_PREVIEW_COLUMNS,
            ids,
            id_column="id",
            require_uuid=True,
        )

        assert mock_get_client.call_count == 3
    @patch("database.get_client")
    def test_empty_id_list_skips_database_call(self, mock_get_client: MagicMock) -> None:
        result = get_messages_by_ids([])

        assert result == {}
        mock_get_client.assert_not_called()

    @patch("database.get_client")
    def test_valid_uuid_list_uses_preview_projection(self, mock_get_client: MagicMock) -> None:
        mock_get_client.return_value = _mock_table_client(
            execute_results=[
                MagicMock(
                    data=[
                        {
                            "id": MESSAGE_ID_1,
                            "raw_text": "Rolex 126610LN New 2024 USD14500",
                        }
                    ]
                )
            ]
        )

        result = get_messages_by_ids([MESSAGE_ID_1])

        assert result[MESSAGE_ID_1]["raw_text"] == "Rolex 126610LN New 2024 USD14500"
        select_args = mock_get_client.return_value.table.return_value.select.call_args
        assert select_args.args[0] == MESSAGE_PREVIEW_COLUMNS
