"""Runtime paths owned by MusicUID (``data/MusicUID/``)."""

from pathlib import Path

from gsuid_core.data_store import get_res_path

MAIN_PATH: Path = get_res_path() / "MusicUID"
CACHE_PATH: Path = MAIN_PATH / "cache"
TEMP_PATH: Path = MAIN_PATH / "temp"
CONFIG_PATH: Path = MAIN_PATH / "config.json"
QQ_CREDENTIAL_PATH: Path = MAIN_PATH / "qqmusic_credential.json"

for _path in (MAIN_PATH, CACHE_PATH, TEMP_PATH):
    _path.mkdir(parents=True, exist_ok=True)
