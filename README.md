# MusicUID

多平台点歌插件 for [GsCore](https://github.com/Genshin-bots/gsuid_core)，移植自
[astrbot_plugin_neteasemusic](https://github.com/wbndmqaq/astrbot_plugin_neteasemusic)（MIT）。

支持 **网易云音乐 / QQ音乐 / 酷狗音乐** 三平台搜索与音频下发，网易云另支持歌词与分享链接解析。

## 依赖与前提

| 依赖 | 说明 |
| --- | --- |
| GsCore | 已包含 httpx / jinja2 / Pillow |
| `playwright` | 图片卡片渲染（可选）。未安装或未下载内核时自动回退纯文本 |
| Chromium 内核 | `python -m playwright install chromium` |
| `pycryptodome` | 网易云 weapi 加密（可选）。缺失时自动回退公开外链 |
| `ffmpeg` | 语音压缩（可选）。音频超过「语音体积上限」时才会调用 |

**不依赖任何外部 API 服务**：三个平台都走各自的公开接口直连，装上即可用。

卡片环境不可用时不会报错，所有指令以纯文本返回。

## 指令

所有指令无需前缀，直接发送即可。

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `点歌 关键词` | 三平台并发搜索并列出结果（编号全平台连续） | `点歌 晴天` |
| `点歌 <平台> 关键词` | 临时指定平台（网易 / QQ / 酷狗） | `点歌 QQ 晴天` |
| `播放 关键词` | 搜索并直接播放第一首 | `播放 晴天` |
| `听N` | 播放当前列表第 N 首（列表 10 分钟内有效） | `听8` |
| `歌词 关键词` | 查看歌词（当前仅网易云） | `歌词 晴天` |
| `音乐帮助` | 帮助文本 | `音乐帮助` |

发送分享链接会自动解析：

**网易云音乐**（`music.163.com` / `y.music.163.com` / `163cn.tv` 短链）：

- **单曲** `song?id=...` → 拉详情并直接播放
- **歌单 / 专辑** `playlist?id=...` / `album?id=...` → 列出歌曲并写入会话，可用「听N」选播

**QQ音乐**（`y.qq.com` 各子域，含 `c6.y.qq.com` 短链）：

- **单曲**三种形态都认：`playsong.html?songid=...`（数字 id）、App 分享卡片的
  `playsong.html?...&songmid=...`、网页版 `songDetail/{songmid}` → 拉详情并直接播放
- 歌单 / 专辑暂未接入

**酷狗音乐**（`kugou.com` 各子域，含 `m.kugou.com`）：

- **单曲**两种形态都认：卡片/长链直接带 `hash=`（它就是取流用的 song_id）；App 分享的
  纯链接只带 `chain=`，需要抓一次移动版页面把内嵌 JSON 里的 hash 换出来
- 歌单 / 专辑暂未接入

**QQ 分享卡片**同样能解析：官机适配器会把卡片正文（含 `jump_url`）拼进消息文本，链接照常被
抓取。注意第三方协议（OneBot / NoneBot2 系）**收不到**腾讯的富媒体卡片消息——这是平台限制，
只能用官机渠道。

## 配置

在 GsCore 网页控制台 → 插件配置 → MusicUID 中修改：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `default_platform` | `netease` | 未指定平台时使用 |
| `netease_cookie` | 空 | 网易云 `MUSIC_U` 的值，填入后 VIP 歌曲与高音质才生效 |
| `netease_level` | `exhigh` | 网易云取流优先档位，拿不到会自动降到标准档 |
| `qqmusic_cookie` | 空 | QQ音乐 `y.qq.com` 的完整 Cookie。填入后会员曲目与 320kbps 档位可播放，且搜索更稳定；必须含 `uin` 与 `qm_keyst` |
| `kugou_cookie` | 空 | 酷狗网页版完整 Cookie。填入后需单独购买专辑的曲目（周杰伦等原唱）可播 60 秒试听，完整版仍需在酷狗购买该专辑 |
| `max_list` | `5` | 每平台条数（最大 10）。未指定平台时卡片总数 = 该值 × 3 |
| `render_card` | `true` | 用图片卡片展示结果 |
| `send_voice` | `true` | 以语音消息发送音频 |
| `send_file` | `false` | 额外再发一份音频文件（语音能正常收听时无需开启） |
| `local_file_ref` | `false` | 强制所有渠道用 `file://` 引用本地音频。默认按渠道自动选择 |
| `enable_resolve` | `true` | 自动解析网易云分享链接 |
| `download_timeout` | `60` | 音频下载超时（秒） |
| `voice_max_mb` | `2` | 语音体积上限，超过则先用 ffmpeg 压缩再发 |
| `voice_bitrate` | `64` | 语音压缩码率（kbps） |
| `keep_temp_sec` | `60` | 临时文件保留秒数 |

## 平台能力

| 平台 | 搜索 | 歌词 | 音频下发 | 分享链接解析 |
| --- | --- | --- | --- | --- |
| 网易云音乐 | ✅ | ✅ | ✅ 免费曲 + VIP（需 `netease_cookie`） | ✅ 单曲 / 歌单 / 专辑 |
| QQ音乐 | ✅ | — | ✅ 免费曲 + VIP 320kbps（需 `qqmusic_cookie`） | ✅ 单曲 |
| 酷狗音乐 | ✅ | — | ✅ 免费曲完整版；需单独购买专辑的曲目给 60 秒试听（需 `kugou_cookie`） | ✅ 单曲 |

三个平台的取流链路都由插件自己实现，各有一条免登录捷径：

- **网易云**：`weapi/song/enhance/player/url/v1`（AES-CBC + RSA 加密，带登录态）。
  Cookie 里必须包含 `os=pc`，否则 VIP 歌曲只会返回 `br=0` 的空地址。拿不到时回退
  `/song/media/outer/url` 公开外链（128kbps，仅免费曲）。
- **QQ音乐**：`u.y.qq.com/cgi-bin/musicu.fcg` 的 `vkey.GetVkeyServer/CgiGetVkey`。
  取流固定 `guid=10000`，`filename` 必须写成「前缀 + songmid + songmid + 后缀」（songmid
  要拼两遍），前缀决定档位：`M800` = 320kbps mp3、`C400` = m4a。匿名时 `uin` 传 `0`，
  只有 `pay_play=0` 的免费曲目能拿到地址，会员曲目一律返回 `104003`；填入
  `qqmusic_cookie` 后改用 Cookie 里的真实 QQ 号（`uin`、`loginUin`、`comm.uin` 三处都要
  跟着改）并带上 Cookie，会员曲目与 320kbps 档位才会下发。⚠️ **搜索也必须带 Cookie**：
  QQ音乐对未登录的搜索请求会直接返回空列表。
- **酷狗**：两条链路。免登录走旧版 CDN `trackercdn.kugou.com/i/v2/`，只校验
  `md5(hash + kgcloudv2)` 签名，不需要登录态也不需要设备指纹。填入 `kugou_cookie` 后先走
  网关 `gateway.kugou.com/v5/url`，它要两个签名：`key` = md5(hash + 盐 + appid + mid +
  userid)，以及 `signature` = md5(盐 + 按参数名排序的 k=v 拼接 + 盐)；`free_part` 固定为 0，
  让服务端自行决定下发完整版还是试听片段（传 1 会把免费曲目也截断成试听）。⚠️ 上游
  KuGouMusicApi 的 `song_url.js` 写的是 `notSign`，而它的 `request.js` 判断的是
  `notSignature`，这个拼写不一致意味着签名**其实仍会生成**——照字面省掉签名会被服务端以
  `err signature` 拒绝。设备指纹则相反：`mid` 用固定派生值、`dfid` 每次随机，直接沿用
  Cookie 里的 mid / dfid 反而会被判 `20018`。

因为走的是各平台的公开接口，**版权受限的曲目**（例如周杰伦在酷狗/QQ音乐的部分作品）
即使标记为免费也可能取不到地址，插件会明确提示无法播放。

## 音频投递

- 默认只发**语音**，不发文件（`send_file` 默认关闭）。语音时长与体积都符合平台要求。
- 音频超过 `voice_max_mb` 时，先用 ffmpeg 压成单声道 `voice_bitrate` 再发。320kbps
  整首歌（10MB+）直接当语音发会被 QQ 静默丢弃，压缩后约 1.7MB。
- 媒体引用形式按渠道自动选择：**QQ 官方机器人发 `file://` 本机路径**，其余渠道发
  `base64://`。原因是 QQ 官方适配器在缺少格式信息时会把无后缀的 base64 音频当作
  WAV 解析，报 `file does not start with RIFF id` 后整条语音被丢弃。

## 目录结构

```
MusicUID/
├── __init__.py / __nest__.py        # 嵌套加载入口
├── pyproject.toml / ruff.toml
└── MusicUID/
    ├── __init__.py                  # Plugins(...) 声明
    ├── musicuid_card/                # 卡片数据 + HTML 模板
    ├── musicuid_config/              # 配置项
    ├── musicuid_help/                # 帮助
    ├── musicuid_play/                # 点歌 / 播放 / 选歌 / 歌词
    ├── musicuid_lifecycle/           # 关闭 / 重载时释放 Chromium 与连接池
    ├── musicuid_resolve/             # 分享链接解析
    ├── tests/                        # 离线单测（pytest，不需要网络）
    └── utils/                        # provider / 渲染 / 投递 / HTTP
```

## 开发与测试

单测全部离线（不发网络请求、不需要 Core 进程），必须在插件被放在
`<core>/gsuid_core/plugins/MusicUID/` 时运行——`SV()` 依赖路径中的 `plugins` 段推断归属：

```bash
pytest gsuid_core/plugins/MusicUID/MusicUID/tests        # 61 项
ruff check gsuid_core/plugins/MusicUID
ruff format --check gsuid_core/plugins/MusicUID
basedpyright                                             # 在插件目录内运行
```

## 许可

MIT。音乐版权归各平台与权利人所有，本插件仅供学习交流使用。
