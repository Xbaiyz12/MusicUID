# MusicUID

多平台点歌插件 for [GsCore](https://github.com/Genshin-bots/gsuid_core)。

支持网易云 / QQ音乐 / 酷狗三平台搜索、语音下发与分享链接解析。卡片渲染走 GsCore 内置的
**pytakumi**（纯本地、不需要浏览器）；**超过 50 字的回复会自动渲染成图片**，短提示仍走文本，
渲染不可用时全部回退纯文本，不影响任何指令。

## 指令

无需前缀，直接发送。

| 指令 | 说明 |
| --- | --- |
| `点歌 关键词` | 三平台并发搜索（编号全平台连续）；`点歌 QQ 晴天` 可指定平台 |
| `播放 关键词` | 搜索并直接播放第一首 |
| `听N` | 播放**自己**的当前列表第 N 首（列表按人独立保存，10 分钟内有效） |
| `歌词 关键词` | 查看歌词（仅网易云） |
| `音乐帮助` | 帮助 |

登录与凭据（仅主人与白名单可用）：

| 指令 | 说明 |
| --- | --- |
| `QQ登录` | 手机 QQ 扫码绑定，凭据后台自动续期 |
| `酷狗登录` | 手机酷狗扫码绑定 |
| `网易云登录` | 手机网易云扫码绑定 |
| `网易云登录 <手机号>` | 验证码登录，再回复「网易云验证码 <码>」完成 |
| `点歌账号` | 查看网易云 / QQ音乐 / 酷狗的账号池（多号按顺序轮换取流） |
| `网易云cookie <MUSIC_U>` | 手动导入网易云 Cookie（不想扫码时用） |
| `点歌导入cookie <平台> <值>` | 手动导入指定平台 Cookie |
| `QQ音乐刷新` | 手动续期 QQ 音乐凭据 |
| `点歌状态` | 各平台凭据与音源状态（图片卡片） |

自建音源、任务与权限：

| 指令 | 说明 |
| --- | --- |
| `设置自建api <URL>` | 接入自建 / 第三方音源服务，发「清空」移除 |
| `自建api模式 fallback\|first` | `fallback` 官方优先、自建兜底；`first` 自建优先 |
| `测试自建api` | 测试自建服务连通性 |
| `音乐签到` | 网易云每日签到 + 领取云贝（只做签到与领取，不刷听歌） |
| `点歌加白` / `点歌删白` / `点歌白名单` | 管理登录权限白名单（主人专用） |

## 配置

网页控制台 → 插件配置 → MusicUID，保存即生效。常改的几个：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `default_platform` | `netease` | 未指定平台时使用 |
| `max_list` | `5` | 每平台条数（最大 10） |
| `send_voice` / `send_file` | `true` / `false` | 发语音 / 额外再发一份文件 |
| `send_music_card` | `false` | 通过 OneBotUID 额外发送音乐卡片；语音、文件、卡片相互独立 |
| `music_card_sign_url` | `https://itapi.top/api/QQMusicJSONArk` | 音乐卡片签名服务地址 |
| `netease_level` | `exhigh` | 网易云音质；选 `lossless` 会按 flac 取流，文件名也是 `.flac` |
| `download_hint` | `true` | 点歌后先回一句「正在准备」；QQ 官方通道每次回复算额度，介意可关 |
| `write_meta` | `true` | 用内置 mutagen 把标题/歌手/专辑/专辑歌手写进音频（纯 Python，不重编码，支持 FLAC） |
| `voice_max_mb` | `10` | 语音体积上限；超过时用 ffmpeg 压缩，官机软限 20MB（最大可填 20），普通 QQ 渠道更严 |
| `render_card` | `true` | 图片卡片；关掉则全部走纯文本 |
| `custom_api_url` | 空 | 自建音源地址，留空即关闭 |
| `custom_api_priority` | `fallback_only` | `custom_first` 则优先走自建 |
| `netease_task_enable` | `false` | 每天 23:30 自动跑网易云签到与云贝领取 |
| `netease_task_notify` | 空 | 签到结果推送目标：群号或 `p:QQ号`，留空只写日志 |
| `login_whitelist` | 空 | 允许登录与改凭据的用户 ID |
| `open_login` | `false` | 放开登录权限；新号先验会员（网易云 / QQ 音乐），没有的只挂起不参与取流 |

Cookie、超时、临时文件保留等其余配置项，含义见配置页内的说明。

`send_music_card` 只通过 OneBotUID 发送 QQ 音乐 JSON 卡片，其他适配器不支持。插件把解析后的音频直链交给签名服务，平台歌曲页作为点击跳转，签名返回的 JSON 原样放进 `json` 消息段；卡片、语音、文件三项开关互相独立，卡片失败不会影响音频下发。

## 分享链接解析

发送网易云 / QQ音乐 / 酷狗的分享链接会自动解析并播放；歌单与专辑会列出歌曲，可用「听N」选播。

群里的**卡片**同样会尝试解析：QQ 音乐卡片（`com.tencent.music.lua`）按 `songmid` 精确取歌，
其余卡片（网易云 / 酷狗等）从 JSON 里递归找音乐链接再走链接解析（也认正文里的 `[json:data=...]`）。

能不能收到取决于上游适配器：卡片在协议里是 `json` 段，部分适配器直接丢弃，纯卡片消息（没有文字）
会整条不上报，core 连内容都拿不到。遇到发卡片没反应，先确认适配器放行了 `json` 段——例如 AstrBot 的
gscore 适配器，需要在 `_build_single_content` 里补一条 `Json` 分支。

## 韩文渲染

歌名里的韩文（谚文）需要韩文字体；渲染器只认已注册的字体，系统装了也不会自动生效：

- **Windows / macOS**：框架自动探测系统韩文字体（`malgun.ttf`、`AppleSDGothicNeo.ttc` 等），无需处理。
- **Linux / Docker**：镜像通常没装，插件启动时会把自己带的一份 Nanum Gothic（OFL-1.1，
  `MusicUID/resource/fonts/NanumGothic-Regular.ttf`）注册成框架的谚文回退脸，装插件即不缺字形。
- 想换字体：把任意韩文字体放进 `<core>/gsuid_core/utils/fonts/`，文件名带 `KR`
  （如 `NotoSansKR-Regular.otf`）即可被自动加载。

## 开发

单测全部离线（不发网络请求、不需要 Core 进程），需在插件位于
`<core>/gsuid_core/plugins/MusicUID/` 时运行：

```bash
pytest gsuid_core/plugins/MusicUID/MusicUID/tests
ruff check gsuid_core/plugins/MusicUID
```

## 许可

MIT。音乐版权归各平台与权利人所有，本插件仅供学习交流使用。

## 运行依赖

- `mutagen`：纯 Python 写音频标签（标题 / 歌手 / 专辑），不重编码
- `imageio-ffmpeg`：自带 ffmpeg 二进制（约 84MB），供语音压缩使用；
  取不到时自动回退系统 `ffmpeg`。该包为 BSD-2，其内置 ffmpeg 为 LGPL-2.1+
