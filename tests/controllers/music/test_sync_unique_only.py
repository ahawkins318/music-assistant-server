"""Tests for the 'only sync library items unique to this source' option."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from music_assistant.constants import CONF_ENTRY_LIBRARY_SYNC_UNIQUE_ONLY
from music_assistant.models.music_provider import MusicProvider


def _provider(unique_only: bool) -> MusicProvider:
    provider = MusicProvider.__new__(MusicProvider)
    provider.config = Mock()
    provider.config.get_value.side_effect = lambda key, default=None: (
        unique_only if key == CONF_ENTRY_LIBRARY_SYNC_UNIQUE_ONLY.key else default
    )
    provider.logger = logging.getLogger("test.unique_only")
    return provider


@pytest.mark.parametrize(
    ("unique_only", "library_match", "expected"),
    [(False, 7, False), (True, None, False), (True, 7, True)],
)
async def test_library_holds_item(
    unique_only: bool, library_match: int | None, expected: bool
) -> None:
    """Only a unique-only provider skips an item, and only one the library already holds."""
    controller = Mock()
    controller._get_library_item_by_match = AsyncMock(return_value=library_match)

    assert await _provider(unique_only)._library_holds_item(controller, Mock()) is expected
    if not unique_only:
        controller._get_library_item_by_match.assert_not_awaited()
