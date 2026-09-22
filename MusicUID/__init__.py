"""init"""

from gsuid_core.sv import Plugins

# 不设强制前缀：关键词以裸词注册（点歌 / 听1 / 歌词 …），由用户消息直接命中
Plugins(name="MusicUID", prefix=[], force_prefix=[], alias=["点歌", "music"])
