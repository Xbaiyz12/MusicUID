"""Unit tests for MusicUID: pure helpers, parsing and card rendering.

这些用例全部离线运行：不发网络请求、不依赖 Core 进程。
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from pathlib import Path

import pytest

from gsuid_core.models import Event
from MusicUID.MusicUID.utils.render import _env, close_browser
from MusicUID.MusicUID.musicuid_play import session as session_module, parse_platform, default_platform
from MusicUID.MusicUID.utils.delivery import ILLEGAL_NAME_CHARS, voice_segment, _safe_file_name
from MusicUID.MusicUID.utils.provider import (
    PROVIDERS,
    SongInfo,
    kugou as kugou_module,
    qqmusic as qq_module,
    get_provider,
    resolve_platform,
)
from MusicUID.MusicUID.musicuid_resolve import URL_RE, SONG_ID_RE, COLLECTION_RE
from MusicUID.MusicUID.utils.json_tools import (
    get_id,
    to_obj,
    get_int,
    get_obj,
    get_str,
    to_list,
    get_bool,
    get_list,
    join_names,
)
from MusicUID.MusicUID.musicuid_play.session import get_session, session_key, save_session
from MusicUID.MusicUID.utils.provider.netease import parse_song, parse_songs


@pytest.fixture(autouse=True)
def _clean_sessions():
    """会话缓存是模块级全局，逐个用例清空避免串味。"""
    session_module._sessions.clear()
    yield
    session_module._sessions.clear()


def _song(song_id: str = "1") -> SongInfo:
    return SongInfo(platform="netease", song_id=song_id, name="晴天", singers="周杰伦")


# ---------------------------------------------------------------- 平台解析


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("QQ", "qq"),
        ("qq", "qq"),
        ("QQ音乐", "qq"),
        ("网易", "netease"),
        ("网易云音乐", "netease"),
        ("wyy", "netease"),
        ("酷狗", "kugou"),
        ("KG", "kugou"),
        (" 酷狗 ", "kugou"),
    ],
)
def test_resolve_platform_aliases(word: str, expected: str) -> None:
    assert resolve_platform(word) == expected


@pytest.mark.parametrize("word", ["", "晴天", "spotify", "123"])
def test_resolve_platform_unknown(word: str) -> None:
    assert resolve_platform(word) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("QQ 晴天", ("qq", "晴天")),
        ("酷狗 稻香", ("kugou", "稻香")),
        ("晴天", ("netease", "晴天")),
        ("", ("netease", "")),
        ("   ", ("netease", "")),
        ("QQ", ("qq", "")),
        ("spotify 晴天", ("netease", "spotify 晴天")),
    ],
)
def test_parse_platform(text: str, expected: tuple[str, str]) -> None:
    assert parse_platform(text, "netease") == expected


def test_default_platform_is_registered() -> None:
    assert default_platform() in PROVIDERS


# ---------------------------------------------------------------- provider 注册表


def test_provider_registry() -> None:
    assert set(PROVIDERS) == {"netease", "qq", "kugou"}
    # 三个平台都能取流：网易云走 weapi，QQ音乐匿名取免费曲，酷狗走免登录 CDN
    assert get_provider("netease").supports_play is True
    assert get_provider("qq").supports_play is True
    assert get_provider("kugou").supports_play is True


def test_kugou_play_url_signs_lowercased_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """酷狗 CDN 只认 md5(hash + kgcloudv2)，且 hash 必须是小写。"""
    captured: dict[str, object] = {}

    async def fake_get_json(url: str, params: object = None, headers: object = None) -> object:
        captured["url"] = url
        captured["params"] = params
        return {"status": 1, "url": ["http://fsandroid.example/song.mp3"]}

    monkeypatch.setattr(kugou_module, "get_json", fake_get_json)
    monkeypatch.setattr(kugou_module.music_config, "get_config", lambda name: SimpleNamespace(data=""))
    song = SongInfo(platform="kugou", song_id="3801C2F0", name="晴天", singers="周杰伦")
    assert asyncio.run(kugou_module.KugouProvider().play_url(song)) == "http://fsandroid.example/song.mp3"
    assert captured["url"] == kugou_module.CDN_URL
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["hash"] == "3801c2f0"
    assert params["key"] == hashlib.md5(b"3801c2f0kgcloudv2").hexdigest()


def test_kugou_play_url_returns_empty_when_cdn_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_json(url: str, params: object = None, headers: object = None) -> object:
        return {"status": 2, "url": []}

    monkeypatch.setattr(kugou_module, "get_json", fake_get_json)
    monkeypatch.setattr(kugou_module.music_config, "get_config", lambda name: SimpleNamespace(data=""))
    song = SongInfo(platform="kugou", song_id="HASH", name="n", singers="s")
    assert asyncio.run(kugou_module.KugouProvider().play_url(song)) == ""


def test_kugou_play_url_uses_gateway_with_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """填了 Cookie 时走网关 v5/url：带登录态与 signature，IsFreePart 固定 0。"""
    calls: list[dict[str, object]] = []

    async def fake_get_json(url: str, params: object = None, headers: object = None) -> object:
        calls.append({"url": url, "params": params, "headers": headers})
        return {"status": 1, "url": ["http://fsandroid.example/vip.mp3"]}

    cookie = "kg_mid=abc; t=TOKEN123; KugooID=2039242825; dfid=xyz"
    monkeypatch.setattr(kugou_module, "get_json", fake_get_json)
    monkeypatch.setattr(kugou_module.music_config, "get_config", lambda name: SimpleNamespace(data=cookie))
    song = SongInfo(platform="kugou", song_id="ABC123", name="晴天", singers="周杰伦")
    assert asyncio.run(kugou_module.KugouProvider().play_url(song)) == "http://fsandroid.example/vip.mp3"
    assert calls[0]["url"] == kugou_module.GATEWAY_URL
    params = calls[0]["params"]
    assert isinstance(params, dict)
    assert params["hash"] == "abc123"
    assert params["token"] == "TOKEN123"
    assert params["userid"] == "2039242825"
    # 传 1 会把免费曲目也截成试听，必须固定 0 让服务端自行决定
    assert params["IsFreePart"] == 0
    # 网关校验 signature：上游 song_url.js 写的是 notSign，而 request.js 判断的是 notSignature，
    # 拼写不一致导致签名其实仍会生成——照字面省掉会被服务端以 err signature 拒绝
    assert "signature" in params
    mid = str(int(hashlib.md5(kugou_module.MID_SEED.encode()).hexdigest(), 16))
    assert params["mid"] == mid
    expected_key = hashlib.md5(
        f"abc123{kugou_module.KEY_SALT}{kugou_module.APP_ID}{mid}2039242825".encode()
    ).hexdigest()
    assert params["key"] == expected_key
    headers = calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["x-router"] == kugou_module.GATEWAY_ROUTER
    assert headers["Cookie"] == cookie


def test_kugou_play_url_skips_gateway_when_cookie_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cookie 里缺 t / KugooID 时不发网关请求，直接回退免登录 CDN。"""
    urls: list[str] = []

    async def fake_get_json(url: str, params: object = None, headers: object = None) -> object:
        urls.append(url)
        return {"status": 1, "url": ["http://cdn.example/free.mp3"]}

    monkeypatch.setattr(kugou_module, "get_json", fake_get_json)
    monkeypatch.setattr(
        kugou_module.music_config, "get_config", lambda name: SimpleNamespace(data="kg_mid=abc; dfid=xyz")
    )
    song = SongInfo(platform="kugou", song_id="HASH", name="n", singers="s")
    assert asyncio.run(kugou_module.KugouProvider().play_url(song)) == "http://cdn.example/free.mp3"
    assert urls == [kugou_module.CDN_URL]


def test_kugou_play_url_falls_back_when_gateway_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """网关拒绝（例如凭证过期）时要回退 CDN，而不是直接放弃。"""
    urls: list[str] = []

    async def fake_get_json(url: str, params: object = None, headers: object = None) -> object:
        urls.append(url)
        if url == kugou_module.GATEWAY_URL:
            return {"status": 2, "fail_process": ["pkg", "buy"]}
        return {"status": 1, "url": ["http://cdn.example/free.mp3"]}

    monkeypatch.setattr(kugou_module, "get_json", fake_get_json)
    monkeypatch.setattr(
        kugou_module.music_config, "get_config", lambda name: SimpleNamespace(data="t=TOKEN; KugooID=123")
    )
    song = SongInfo(platform="kugou", song_id="HASH", name="n", singers="s")
    assert asyncio.run(kugou_module.KugouProvider().play_url(song)) == "http://cdn.example/free.mp3"
    assert urls == [kugou_module.GATEWAY_URL, kugou_module.CDN_URL]


def test_qq_play_url_sends_anonymous_vkey_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """匿名取流固定 guid=10000 / uin=0，filename 里的 songmid 要拼两遍。"""
    calls: list[dict[str, object]] = []

    async def fake_post_json(url: str, data: object, headers: object = None, as_json: bool = False) -> object:
        calls.append({"url": url, "data": data, "as_json": as_json})
        return {
            "req_1": {
                "data": {
                    "sip": ["http://aqqmusic.example/"],
                    "midurlinfo": [{"purl": "C400abc.m4a?vkey=1"}],
                }
            }
        }

    monkeypatch.setattr(qq_module, "post_json", fake_post_json)
    song = SongInfo(platform="qq", song_id="0039MnYb0qxYhV", name="晴天", singers="周杰伦")
    assert asyncio.run(qq_module.QqMusicProvider().play_url(song)) == "http://aqqmusic.example/C400abc.m4a?vkey=1"
    assert calls[0]["url"] == qq_module.VKEY_URL
    assert calls[0]["as_json"] is True
    data = calls[0]["data"]
    assert isinstance(data, dict)
    req = data["req_1"]
    assert isinstance(req, dict)
    param = req["param"]
    assert isinstance(param, dict)
    assert param["filename"] == ["C4000039MnYb0qxYhV0039MnYb0qxYhV.m4a"]
    assert param["guid"] == "10000"
    assert param["uin"] == "0"
    assert param["loginflag"] == 1


def test_qq_play_url_falls_back_to_mp3(monkeypatch: pytest.MonkeyPatch) -> None:
    """m4a 拿不到时（会员曲目返回 104003）要退到 128kbps mp3。"""
    tried: list[str] = []

    async def fake_post_json(url: str, data: object, headers: object = None, as_json: bool = False) -> object:
        assert isinstance(data, dict)
        req = data["req_1"]
        assert isinstance(req, dict)
        param = req["param"]
        assert isinstance(param, dict)
        name = param["filename"][0]
        assert isinstance(name, str)
        tried.append(name.rsplit(".", 1)[-1])
        purl = "" if name.endswith(".m4a") else "M500abc.mp3"
        return {"req_1": {"data": {"sip": ["http://s.example/"], "midurlinfo": [{"purl": purl, "result": 104003}]}}}

    monkeypatch.setattr(qq_module, "post_json", fake_post_json)
    song = SongInfo(platform="qq", song_id="MID", name="n", singers="s")
    assert asyncio.run(qq_module.QqMusicProvider().play_url(song)) == "http://s.example/M500abc.mp3"
    assert tried == ["m4a", "mp3"]


def test_get_provider_falls_back_to_default() -> None:
    assert get_provider("not-a-platform").platform == "netease"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, ""), (-1, ""), (59, "00:59"), (269, "04:29"), (3600, "60:00")],
)
def test_duration_text(seconds: int, expected: str) -> None:
    song = SongInfo(platform="qq", song_id="x", name="n", singers="s", duration_sec=seconds)
    assert song.duration_text == expected


# ---------------------------------------------------------------- JSON 读取器


def test_to_obj_and_to_list_reject_wrong_types() -> None:
    assert to_obj({"a": 1}) == {"a": 1}
    assert to_obj([1, 2]) == {}
    assert to_obj("text") == {}
    assert to_obj(None) == {}
    assert to_list([1, 2]) == [1, 2]
    assert to_list({"a": 1}) == []


def test_to_obj_drops_non_string_keys() -> None:
    assert to_obj({1: "a", "b": 2}) == {"b": 2}


def test_scalar_readers_narrow_types() -> None:
    payload: dict[str, object] = {
        "s": "晴天",
        "n": 5,
        "f": 4.9,
        "numeric_str": "12",
        "bad_str": "abc",
        "flag_int": 1,
        "flag_bool": False,
        "wrong": [1],
    }
    assert get_str(payload, "s") == "晴天"
    assert get_str(payload, "n", "fallback") == "fallback"
    assert get_int(payload, "n") == 5
    assert get_int(payload, "f") == 4
    assert get_int(payload, "numeric_str") == 12
    assert get_int(payload, "bad_str", -1) == -1
    assert get_int(payload, "missing", 7) == 7
    assert get_bool(payload, "flag_int") is True
    assert get_bool(payload, "flag_bool") is False
    assert get_bool(payload, "missing") is False
    assert get_id(payload, "n") == "5"
    assert get_id(payload, "s") == "晴天"
    assert get_id(payload, "wrong") == ""


def test_get_obj_and_get_list_nested() -> None:
    payload: dict[str, object] = {"a": {"b": 1}, "c": [1, 2], "d": "x"}
    assert get_obj(payload, "a") == {"b": 1}
    assert get_obj(payload, "d") == {}
    assert get_list(payload, "c") == [1, 2]
    assert get_list(payload, "d") == []


def test_join_names() -> None:
    assert join_names([{"name": "周杰伦"}]) == "周杰伦"
    assert join_names([{"name": "A"}, {"name": "B"}]) == "A/B"
    assert join_names([{"other": "x"}, {"name": "B"}]) == "B"
    assert join_names(None) == ""
    assert join_names("not-a-list") == ""


# ---------------------------------------------------------------- 文件名清洗


def test_safe_file_name_strips_illegal_chars() -> None:
    song = SongInfo(platform="netease", song_id="1", name="A/B:C", singers="D*E?F")
    name = _safe_file_name(song, ".mp3")
    assert name.endswith(".mp3")
    assert not any(ch in name for ch in ILLEGAL_NAME_CHARS)


def test_safe_file_name_truncates_and_falls_back() -> None:
    long_song = SongInfo(platform="netease", song_id="1", name="歌" * 100, singers="s")
    assert len(_safe_file_name(long_song, ".mp3")) <= 64 + len(".mp3")
    empty_song = SongInfo(platform="netease", song_id="1", name="", singers="")
    assert _safe_file_name(empty_song, ".mp3") == "music.mp3"


def test_safe_file_name_without_artist() -> None:
    song = SongInfo(platform="netease", song_id="1", name="纯音乐", singers="")
    assert _safe_file_name(song, ".mp3") == "纯音乐.mp3"


def test_voice_segment_uses_local_uri_for_qq_official(tmp_path: Path) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"ID3" + b"\x00" * 64)
    seg = voice_segment(audio, "qq_official", False)
    assert seg.type == "record"
    assert str(seg.data).startswith("file://")
    assert "song.mp3" in str(seg.data)


def test_voice_segment_inlines_base64_for_other_channels(tmp_path: Path) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"ID3" + b"\x00" * 64)
    for bot_id in ("onebot", "AstrBot"):
        seg = voice_segment(audio, bot_id, False)
        assert seg.type == "record"
        assert str(seg.data).startswith("base64://")


def test_voice_segment_honours_force_local(tmp_path: Path) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"ID3" + b"\x00" * 64)
    seg = voice_segment(audio, "onebot", True)
    assert str(seg.data).startswith("file://")


# ---------------------------------------------------------------- 会话缓存


def test_session_key_prefers_group() -> None:
    assert session_key(Event(user_id="u1", group_id="g1")) == "g1"
    assert session_key(Event(user_id="u1")) == "u1"


def test_session_roundtrip() -> None:
    ev = Event(user_id="u1", group_id="g1")
    assert get_session(ev) is None
    save_session(ev, "netease", "晴天", [_song()])
    session = get_session(ev)
    assert session is not None
    assert session.platform == "netease"
    assert session.keyword == "晴天"
    assert session.songs[0].name == "晴天"


def test_session_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    ev = Event(user_id="u1", group_id="g1")
    save_session(ev, "qq", "稻香", [_song()])
    monkeypatch.setattr(session_module, "SESSION_TTL_SEC", -1)
    assert get_session(ev) is None


def test_group_sessions_are_isolated() -> None:
    save_session(Event(user_id="u1", group_id="g1"), "qq", "a", [_song("1")])
    save_session(Event(user_id="u2", group_id="g2"), "kugou", "b", [_song("2")])
    first = get_session(Event(user_id="u9", group_id="g1"))
    second = get_session(Event(user_id="u9", group_id="g2"))
    assert first is not None
    assert second is not None
    assert first.keyword == "a"
    assert second.keyword == "b"
    assert get_session(Event(user_id="u3", group_id="g3")) is None


# ---------------------------------------------------------------- 分享链接解析


@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://music.163.com/song?id=186016", "186016"),
        ("https://music.163.com/#/song?id=1330348068", "1330348068"),
        ("https://y.music.163.com/m/song?id=123&userid=1", "123"),
    ],
)
def test_song_id_pattern(url: str, expected_id: str) -> None:
    match = SONG_ID_RE.search(url)
    assert match is not None
    assert match.group(1) == expected_id


@pytest.mark.parametrize(
    ("url", "expected_kind", "expected_id"),
    [
        ("https://music.163.com/playlist?id=3778678", "playlist", "3778678"),
        ("https://music.163.com/#/album?id=32311", "album", "32311"),
        ("https://music.163.com/playlist?id=1&userid=2", "playlist", "1"),
    ],
)
def test_collection_pattern_captures_kind_and_id(url: str, expected_kind: str, expected_id: str) -> None:
    assert SONG_ID_RE.search(url) is None
    match = COLLECTION_RE.search(url)
    assert match is not None
    assert match.group(1) == expected_kind
    assert match.group(2) == expected_id


def test_parse_song_reads_full_field_names() -> None:
    item: dict[str, object] = {
        "id": 186016,
        "name": "晴天",
        "artists": [{"name": "周杰伦"}],
        "album": {"name": "叶惠美", "picUrl": "https://cdn/cover.jpg"},
        "duration": 269000,
        "fee": 0,
    }
    song = parse_song(item)
    assert song is not None
    assert song.song_id == "186016"
    assert song.singers == "周杰伦"
    assert song.album == "叶惠美"
    assert song.duration_sec == 269
    assert song.cover_url.endswith("cover.jpg")
    assert song.payplay is False


def test_parse_song_reads_abbreviated_field_names() -> None:
    # 歌单接口返回的是简写字段（ar / al / dt）
    item: dict[str, object] = {
        "id": 1,
        "name": "热歌",
        "ar": [{"name": "A/B"}],
        "al": {"name": "专辑"},
        "dt": 61000,
        "fee": 1,
    }
    song = parse_song(item)
    assert song is not None
    assert song.singers == "A/B"
    assert song.album == "专辑"
    assert song.duration_sec == 61
    assert song.payplay is True


def test_parse_songs_skips_items_without_id() -> None:
    songs = parse_songs([{"id": 1, "name": "a"}, {"name": "no-id"}, "junk", 42])
    assert len(songs) == 1
    assert songs[0].name == "a"
    assert parse_songs("not-a-list") == []


def test_url_pattern_extracts_from_message() -> None:
    text = "分享单曲 https://music.163.com/song?id=186016 一起听"
    match = URL_RE.search(text)
    assert match is not None
    assert match.group(0) == "https://music.163.com/song?id=186016"


# ---------------------------------------------------------------- 卡片模板


def _card_data(keyword: str) -> dict[str, object]:
    return {
        "theme": "multi",
        "eyebrow": "全平台 · SEARCH",
        "keyword": keyword,
        "total": 1,
        "subtitle": "QQ音乐 共 1 首",
        "groups": [
            {
                "platform": "qq",
                "platform_name": "QQ音乐",
                "songs": [
                    {
                        "index": 1,
                        "cover": "",
                        "songName": "晴天",
                        "singerName": "周杰伦",
                        "albumName": "叶惠美",
                        "duration": "04:29",
                        "payplay": True,
                    }
                ],
                "playable": False,
                "empty_text": "没有搜到相关歌曲",
            }
        ],
        "tip": "该平台暂不可播放",
        "footer_left": "QQ音乐 · 数据来自公开接口",
    }


def test_card_template_renders() -> None:
    html = _env.get_template("song_list.html").render(data=_card_data("晴天"))
    assert "晴天" in html
    assert "周杰伦" in html
    assert "QQ音乐" in html
    assert 'class="multi"' in html
    assert "p-qq" in html
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html


def test_card_template_handles_empty_group() -> None:
    data: dict[str, object] = {
        "theme": "multi",
        "eyebrow": "全平台 · SEARCH",
        "keyword": "晴天",
        "total": 0,
        "subtitle": "无结果",
        "groups": [
            {
                "platform": "qq",
                "platform_name": "QQ音乐",
                "songs": [],
                "playable": False,
                "empty_text": "搜索失败（超时）",
            }
        ],
        "tip": "x",
        "footer_left": "x",
    }
    html = _env.get_template("song_list.html").render(data=data)
    assert "搜索失败（超时）" in html


def test_card_template_escapes_user_input() -> None:
    html = _env.get_template("song_list.html").render(data=_card_data("<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_close_browser_is_idempotent() -> None:
    # 从未启动过浏览器时也必须能安全收尾（插件重载 / 进程退出都会调它）
    asyncio.run(close_browser())
    asyncio.run(close_browser())
