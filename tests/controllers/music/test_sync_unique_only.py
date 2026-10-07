"""Tests for the 'only sync library items unique to this source' option."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import Album, ItemMapping, ProviderMapping, Track

from music_assistant.constants import CONF_ENTRY_LIBRARY_SYNC_UNIQUE_ONLY, DB_TABLE_ALBUM_TRACKS
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
    provider.mass.music.albums = _controller()
    provider.mass.music.tracks = _controller()
    provider.mass.music.database.delete = AsyncMock()
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


def _track(*instances: str, album: Album | ItemMapping | None = None, item_id: str = "20") -> Track:
    return Track(
        item_id=item_id,
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
    controller.get_library_album_tracks = AsyncMock(return_value=[])
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


def _album_with_leftovers(provider: MusicProvider, nas_track_album: Album | None) -> Mock:
    """
    Library album 10 with a padding track (21), a shared track (22) and a borrowed one (23).

    :param nas_track_album: The library album the NAS says track 23's file is on.
    """
    albums = provider.mass.music.albums
    albums.get_library_album_tracks.return_value = [
        _track(NAS, item_id="20"),
        _track(APPLE, item_id="21"),
        _track(APPLE, NAS, item_id="22"),
        _track(APPLE, NAS, item_id="23"),
    ]
    here = _album(NAS, item_id="nas-here", provider=NAS)
    there = _album(NAS, item_id="nas-there", provider=NAS)
    nas = Mock(spec=MusicProvider)
    nas.get_track = AsyncMock(
        side_effect=lambda item_id: _track(NAS, album=here if item_id == "22" else there)
    )
    provider.mass.get_provider = Mock(return_value=nas)
    albums.get_library_item_by_prov_id = AsyncMock(
        side_effect=lambda item_id, _prov: (
            _album(NAS, item_id="10") if item_id == "nas-here" else nas_track_album
        )
    )
    # every library track maps the NAS file under its own id
    for track in albums.get_library_album_tracks.return_value:
        for mapping in track.provider_mappings:
            if mapping.provider_instance == NAS:
                mapping.item_id = track.item_id
    return albums


async def test_unmerging_an_album_takes_this_providers_tracks_off_it() -> None:
    """Padding leaves the library; a track whose file is on another album is unlinked."""
    provider = _provider()
    _album_with_leftovers(provider, _album(NAS, item_id="11"))
    controller = _controller(_album(APPLE, NAS))
    assert await provider._skip_for_unique_only(controller, _album(APPLE), 10)
    provider.mass.music.tracks.remove_provider_mappings.assert_awaited_once_with("21", APPLE)
    provider.mass.music.database.delete.assert_awaited_once_with(
        DB_TABLE_ALBUM_TRACKS, {"track_id": 23, "album_id": 10}
    )
    controller.remove_provider_mappings.assert_awaited_once_with(10, APPLE)


async def test_album_unmerged_earlier_still_loses_leftover_tracks() -> None:
    """A skipped album the library holds from another source is cleaned on every sync."""
    provider = _provider()
    _album_with_leftovers(provider, _album(NAS, item_id="11"))
    controller = _controller(match=10)
    assert await provider._skip_for_unique_only(controller, _album(APPLE), None)
    provider.mass.music.tracks.remove_provider_mappings.assert_awaited_once_with("21", APPLE)
    provider.mass.music.database.delete.assert_awaited_once()


async def test_track_stays_linked_when_its_other_source_names_no_library_album() -> None:
    """Without proof that another album holds the file, the link is left alone."""
    provider = _provider()
    _album_with_leftovers(provider, None)
    assert await provider._skip_for_unique_only(_controller(match=10), _album(APPLE), None)
    provider.mass.music.database.delete.assert_not_awaited()


async def test_track_stays_linked_when_its_other_source_can_not_be_read() -> None:
    """A file that is gone, or a provider that is not loaded, removes no link."""
    provider = _provider()
    _album_with_leftovers(provider, _album(NAS, item_id="11"))
    provider.mass.get_provider.return_value.get_track.side_effect = MediaNotFoundError("gone")
    assert await provider._skip_for_unique_only(_controller(match=10), _album(APPLE), None)
    provider.mass.music.database.delete.assert_not_awaited()
    provider.mass.get_provider.return_value = None
    assert await provider._skip_for_unique_only(_controller(match=10), _album(APPLE), None)
    provider.mass.music.database.delete.assert_not_awaited()
