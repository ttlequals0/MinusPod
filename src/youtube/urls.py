"""Parse YouTube playlist, channel, handle and video URLs into a source identity."""
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

Kind = Literal['playlist', 'channel', 'handle', 'user', 'custom', 'video']
_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
_PLAYLIST_ID = re.compile(r'^(PL|UU|UULF|UUSH|UULV|OLAK5uy_|RD|FL)[A-Za-z0-9_-]{10,}$')
_CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_NAME = re.compile(r'^[A-Za-z0-9._-]{2,64}$')
_RESERVED = {'watch', 'feed', 'results', 'playlist', 'channel', 'shorts', 'embed', 'live', 'c', 'user', 'about'}


@dataclass(frozen=True)
class Source:
    kind: Kind
    id: str


def parse(text: str) -> Source | None:
    text = (text or '').strip()
    if not text:
        return None
    if _CHANNEL_ID.match(text):
        return Source('channel', text)
    if _PLAYLIST_ID.match(text):
        return Source('playlist', text)
    if text.startswith('@') and _NAME.match(text[1:]):
        return Source('handle', text[1:])
    parsed = urlparse(text if '://' in text else f'https://{text}')
    host = (parsed.hostname or '').lower()
    if host not in _HOSTS:
        return None
    if host == 'youtu.be':
        short_id = parsed.path.strip('/')
        return Source('video', short_id) if VIDEO_ID.match(short_id) else None
    listed = parse_qs(parsed.query).get('list', [''])[0]
    if listed:
        return Source('playlist', listed) if _PLAYLIST_ID.match(listed) else None
    parts = [p for p in parsed.path.split('/') if p]
    if not parts:
        return None
    head = parts[0]
    if head == 'watch':
        watched = parse_qs(parsed.query).get('v', [''])[0]
        return Source('video', watched) if VIDEO_ID.match(watched) else None
    if head in ('live', 'shorts') and len(parts) > 1 and VIDEO_ID.match(parts[1]):
        return Source('video', parts[1])
    if head == 'channel' and len(parts) > 1 and _CHANNEL_ID.match(parts[1]):
        return Source('channel', parts[1])
    if head.startswith('@') and _NAME.match(head[1:]):
        return Source('handle', head[1:])
    if head in ('c', 'user') and len(parts) > 1 and _NAME.match(parts[1]):
        return Source('custom' if head == 'c' else 'user', parts[1])
    if head not in _RESERVED and len(parts) == 1 and _NAME.match(head):
        return Source('custom', head)
    return None


def canonical_source_url(kind: str, source_id: str) -> str:
    return f'youtube://{kind}/{source_id}'


def source_url(source: Source) -> str:
    if source.kind == 'playlist':
        return f'https://www.youtube.com/playlist?list={source.id}'
    if source.kind == 'channel':
        return f'https://www.youtube.com/channel/{source.id}'
    if source.kind == 'handle':
        return f'https://www.youtube.com/@{source.id}'
    if source.kind == 'video':
        return watch_url(source.id)
    return f'https://www.youtube.com/{"user" if source.kind == "user" else "c"}/{source.id}'


def watch_url(video_id: str) -> str:
    return f'https://www.youtube.com/watch?v={video_id}'


def uploads_playlist(channel_id: str, include_shorts: bool) -> str:
    return ('UU' if include_shorts else 'UULF') + channel_id[2:]
