# MusicUID

多平台点歌插件 for [GsCore](https://github.com/Genshin-bots/gsuid_core)。

支持网易云 / QQ音乐 / 酷狗三平台搜索、语音下发与分享链接解析。卡片渲染走 GsCore 内置的
**pytakumi**（纯本地、不需要浏览器）；缺依赖时自动回退纯文本，不影响指令。

## 指令

无需前缀，直接发送。

| 指令 | 说明 |
| --- | --- |
| `点歌 关键词` | 三平台并发搜索（编号全平台连续）；`点歌 QQ 晴天` 可指定平台 |
| `播放 关键词` | 搜索并直接播放第一首 |
| `听N` | 播放当前列表第 N 首（列表 10 分钟内有效） |
| `歌词 关键词` | 查看歌词（仅网易云） |
| `音乐帮助` | 帮助 |

登录与凭据（仅主人与白名单可用）：

| 指令 | 说明 |
| --- | --- |
| `QQ登录` | 手机 QQ 扫码绑定，凭据后台自动续期 |
| `酷狗登录` | 手机酷狗扫码绑定 |
| `网易云cookie <MUSIC_U>` | 网易云扫码已被官方拦截，只能用 Cookie |
| `点歌导入cookie <平台> <值>` | 手动导入指定平台 Cookie |
| `QQ音乐刷新` | 手动续期 QQ 音乐凭据 |
| `点歌状态` | 各平台凭据与音源状态（图片卡片） |

自建音源与权限：

| 指令 | 说明 |
| --- | --- |
| `设置自建api <URL>` | 接入自建 / 第三方音源服务，发「清空」移除 |
| `自建api模式 fallback\|first` | `fallback` 官方优先、自建兜底；`first` 自建优先 |
| `测试自建api` | 测试自建服务连通性 |
| `点歌加白` / `点歌删白` / `点歌白名单` | 管理登录权限白名单（主人专用） |

## 配置

网页控制台 → 插件配置 → MusicUID，保存即生效。常改的几个：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `default_platform` | `netease` | 未指定平台时使用 |
| `max_list` | `5` | 每平台条数（最大 10） |
| `send_voice` / `send_file` | `true` / `false` | 发语音 / 额外再发一份文件 |
| `netease_level` | `exhigh` | 网易云音质；群里发不出去就调成 `standard` |
| `voice_max_mb` | `2` | 语音体积上限，超过先用 ffmpeg 压缩 |
| `render_card` | `true` | 图片卡片；关掉则全部走纯文本 |
| `custom_api_url` | 空 | 自建音源地址，留空即关闭 |
| `custom_api_priority` | `fallback_only` | `custom_first` 则优先走自建 |
| `login_whitelist` | 空 | 允许登录与改凭据的用户 ID |

Cookie、超时、临时文件保留等其余配置项，含义见配置页内的说明。

## 分享链接解析

发送网易云 / QQ音乐 / 酷狗的分享链接会自动解析并播放；歌单与专辑会列出歌曲，可用「听N」选播。

QQ 分享卡片只支持官机渠道——第三方协议（OneBot / NoneBot2 系）收不到腾讯的富媒体卡片。

## 开发

单测全部离线（不发网络请求、不需要 Core 进程），需在插件位于
`<core>/gsuid_core/plugins/MusicUID/` 时运行：

```bash
pytest gsuid_core/plugins/MusicUID/MusicUID/tests
ruff check gsuid_core/plugins/MusicUID
```

## 许可

MIT。音乐版权归各平台与权利人所有，本插件仅供学习交流使用。
