"""Tests for the 'only sync library items unique to this source' option."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest
from music_assistant_models.media_items import Album, ItemMapping, ProviderMapping, Track

from music_assistant.constants import CONF_ENTRY_LIBRARY_SYNC_UNIQUE_ONLY
from music_assistant.models.music_provider import MusicProvider

APPLE = "apple_music_1"
NAS = "filesystem_1"


def _provider(unique_only: bool = True) -> MusicProvider:
    provider = MusicProvider.__new__(MusicProvider)
    provider.config = Mock()
    provider.config.instance_id = APPLE
    provider.config.get_value.side_effect = lambda key, default=None: (
        unique_only if key == CONF_ENTRY_LIBRARY_SYNC_UNIQUE_ONLY.key else default
    )
    provider.logger = logging.getLogger("test.unique_only")
    provider.mass = Mock()
    return provider


def _mappings(*instances: str) -> set[ProviderMapping]:
    return {
        ProviderMapping(item_id=f"{i}-id", provider_domain=i.rsplit("_", 1)[0], provider_instance=i)
        for i in instances
    }


def _album(*instances: str, item_id: str = "10", provider: str = "library") -> Album:
    return Album(
        item_id=item_id, provider=provider, name="Album", provider_mappings=_mappings(*instances)
    )


def _track(*instances: str, album: Album | ItemMapping | None = None) -> Track:
    return Track(
        item_id="20",
        provider="library",
        name="Track",
        provider_mappings=_mappings(*instances),
        album=album,
    )


def _controller(library_item: object = None, match: int | None = None) -> Mock:
    controller = Mock()
    controller._get_library_item_by_match = AsyncMock(return_value=match)
    controller.get_library_item = AsyncMock(return_value=library_item)
    controller.remove_provider_mappings = AsyncMock()
    return controller


def _albums(provider: MusicProvider, db_album: Album | None) -> Mock:
    albums = _controller(db_album)
    albums.get_library_item_by_prov_id = AsyncMock(return_value=db_album)
    provider.mass.music.albums = albums
    return albums


async def test_option_off_never_skips() -> None:
    """Without the option every item syncs as before and nothing is looked up."""
    controller = _controller(match=7)
    assert not await _provider(unique_only=False)._skip_for_unique_only(controller, Mock(), None)
    controller._get_library_item_by_match.assert_not_awaited()


@pytest.mark.parametrize(("match", "expected"), [(None, False), (7, True)])
async def test_new_item_skipped_only_when_the_library_holds_it(
    match: int | None, expected: bool
) -> None:
    """A new album is skipped exactly when MA's matcher finds it in the library."""
    controller = _controller(match=match)
    assert await _provider()._skip_for_unique_only(controller, _album(), None) is expected


@pytest.mark.parametrize(("album_mappings", "expected"), [((APPLE,), False), ((NAS,), True)])
async def test_new_track_skipped_when_its_album_is_held_elsewhere(
    album_mappings: tuple[str, ...], expected: bool
) -> None:
    """A new track whose album another source holds would pad that album, so it is skipped."""
    provider = _provider()
    _albums(provider, _album(*album_mappings))
    prov_track = _track(APPLE, album=_album(APPLE, item_id="a1", provider=APPLE))
    assert await provider._skip_for_unique_only(_controller(), prov_track, None) is expected


async def test_merged_album_is_unmerged() -> None:
    """An album merged with the NAS copy earlier loses this provider's mapping."""
    controller = _controller(_album(APPLE, NAS))
    assert await _provider()._skip_for_unique_only(controller, _album(APPLE), 10)
    controller.remove_provider_mappings.assert_awaited_once_with(10, APPLE)


async def test_unique_album_is_kept() -> None:
    """An album only this provider holds keeps syncing."""
    controller = _controller(_album(APPLE))
    assert not await _provider()._skip_for_unique_only(controller, _album(APPLE), 10)
    controller.remove_provider_mappings.assert_not_awaited()


async def test_padding_track_is_removed_and_its_album_unmerged() -> None:
    """A track only this provider maps, on an album merged with the NAS, is padding."""
    provider = _provider()
    library_album = _album(APPLE, NAS)
    albums = _albums(provider, library_album)
    controller = _controller(_track(APPLE, album=library_album))
    assert await provider._skip_for_unique_only(controller, _track(APPLE), 20)
    controller.remove_provider_mappings.assert_awaited_once_with(20, APPLE)
    albums.remove_provider_mappings.assert_awaited_once_with("10", APPLE)


async def test_shared_track_is_kept() -> None:
    """A track the NAS also maps is a second source for the same file, not padding."""
    provider = _provider()
    _albums(provider, _album(NAS))
    controller = _controller(_track(APPLE, NAS, album=_album(NAS)))
    assert not await provider._skip_for_unique_only(controller, _track(APPLE), 20)
    controller.remove_provider_mappings.assert_not_awaited()


async def test_existing_artists_are_never_unmerged() -> None:
    """Artist merges are left alone: one artist page across sources is wanted."""
    controller = _controller()
    assert not await _provider()._skip_for_unique_only(controller, Mock(), 5)
    controller.remove_provider_mappings.assert_not_awaited()
