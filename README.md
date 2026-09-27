# MusicUID

多平台点歌插件 for [GsCore](https://github.com/Genshin-bots/gsuid_core)。

支持 **网易云音乐 / QQ音乐 / 酷狗音乐** 三平台搜索与音频下发，网易云另支持歌词与分享链接解析。

## 依赖与前提

| 依赖 | 说明 |
| --- | --- |
| GsCore | 已包含 httpx / jinja2 / Pillow |
| **pytakumi** | 图片卡片渲染，**GsCore 已内置**——纯本地、不需要浏览器，开箱即用 |
| `playwright` + Chromium | 可选回退。仅当 core 没带 pytakumi 时才会用到，那时启动会自动下载 |
| `pycryptodome` | 网易云 weapi 加密（可选）。缺失时自动回退公开外链 |
| `ffmpeg` | 语音压缩（可选）。音频超过「语音体积上限」时才会调用 |

卡片渲染优先走 GsCore 内置的 pytakumi，只有 core 未提供时才会在后台准备浏览器回退。
卡片环境不可用时不会报错，所有指令以纯文本返回。

## 指令

所有指令无需前缀，直接发送即可。

| 指令 | 说明 |
| --- | --- |
| `点歌 关键词` | 三平台并发搜索并列出结果（编号全平台连续） |
| `点歌 <平台> 关键词` | 临时指定平台（网易 / QQ / 酷狗） |
| `播放 关键词` | 搜索并直接播放第一首 |
| `听N` | 播放当前列表第 N 首（列表 10 分钟内有效） |
| `歌词 关键词` | 查看歌词（当前仅网易云） |
| `音乐帮助` | 帮助文本 |

登录与凭据（需权限）：

| 指令 | 说明 |
| --- | --- |
| `QQ登录` | 手机 QQ 扫码绑定，凭据后台自动续期，无需再手动贴 Cookie |
| `酷狗登录` | 手机酷狗扫码绑定 |
| `网易云cookie <MUSIC_U>` | 网易云扫码已被官方拦截，只能填 Cookie |
| `点歌导入cookie <平台> <值>` | 手动导入指定平台 Cookie |
| `QQ音乐刷新` | 手动刷新 QQ 音乐凭据有效期 |
| `点歌状态` | 查看各平台凭据与音源配置状态 |

自建音源（可选，需权限）：

| 指令 | 说明 |
| --- | --- |
| `设置自建api <URL>` | 配置自建 / 第三方音源服务地址，发「清空」移除 |
| `自建api模式 <模式>` | `fallback` 官方优先、自建兜底；`first` 自建优先 |
| `测试自建api` | 测试自建服务连通性 |

权限管理：

| 指令 | 说明 |
| --- | --- |
| `点歌加白 用户ID/@用户` | 添加登录权限白名单（主人专用） |
| `点歌删白 用户ID/@用户` | 移除登录权限白名单（主人专用） |
| `点歌白名单` | 查看当前白名单 |

## 分享链接解析

发送分享链接会自动解析：

- **网易云**：单曲 `song?id=...` 直接播放；歌单 / 专辑列出歌曲，可用「听N」选播
- **QQ音乐**：`playsong.html?songid=...`、App 卡片的 `songmid=`、`songDetail/{songmid}`
- **酷狗**：带 `hash=` 的直链；App 分享的 `chain=` 短链会抓一次移动页换出 hash

QQ 分享卡片同样能解析，但第三方协议（OneBot / NoneBot2 系）收不到腾讯的富媒体卡片，
这是平台限制，只能用官机渠道。

## 配置

在 GsCore 网页控制台 → 插件配置 → MusicUID 中修改，保存即生效：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `default_platform` | `netease` | 未指定平台时使用 |
| `login_whitelist` | 空 | 允许扫码登录与改凭据的用户 ID（主人默认有权限） |
| `netease_cookie` | 空 | 网易云 `MUSIC_U`，填入后 VIP 与高音质生效 |
| `netease_level` | `exhigh` | 网易云取流档位，拿不到会自动降档 |
| `qqmusic_cookie` | 空 | QQ音乐 Cookie，推荐改用「QQ登录」扫码 |
| `kugou_cookie` | 空 | 酷狗 Cookie，推荐改用「酷狗登录」扫码 |
| `custom_api_url` | 空 | 自建 / 第三方音源服务地址，留空即关闭 |
| `custom_api_priority` | `fallback_only` | `custom_first` 则优先走自建服务 |
| `custom_api_token` | 空 | 自建服务的鉴权 Token，无鉴权留空 |
| `max_list` | `5` | 每平台条数（最大 10） |
| `render_card` | `true` | 用图片卡片展示结果 |
| `auto_install_render` | `true` | 缺渲染依赖时自动下载 Chromium |
| `send_voice` | `true` | 以语音消息发送音频 |
| `send_file` | `false` | 额外再发一份音频文件 |
| `local_file_ref` | `false` | 强制所有渠道用 `file://` 引用本地音频 |
| `enable_resolve` | `true` | 自动解析分享链接 |
| `download_timeout` | `60` | 音频下载超时（秒） |
| `voice_max_mb` | `2` | 语音体积上限，超过则先用 ffmpeg 压缩再发 |
| `voice_bitrate` | `64` | 语音压缩码率（kbps） |
| `keep_temp_sec` | `60` | 临时文件保留秒数 |

## 目录结构

```
MusicUID/
├── __init__.py / __nest__.py        # 嵌套加载入口
├── pyproject.toml / ruff.toml
└── MusicUID/
    ├── __init__.py                  # Plugins(...) 声明
    ├── musicuid_card/               # 卡片数据 + HTML 模板
    ├── musicuid_config/             # 配置项
    ├── musicuid_help/               # 帮助
    ├── musicuid_login/              # 扫码登录 / 凭据 / 音源设置
    ├── musicuid_play/               # 点歌 / 播放 / 选歌 / 歌词
    ├── musicuid_lifecycle/          # 凭据续期定时任务与资源释放
    ├── musicuid_resolve/            # 分享链接解析
    ├── tests/                       # 离线单测（pytest，不需要网络）
    └── utils/                       # provider / 登录 / 渲染 / 投递 / HTTP
```

## 开发与测试

单测全部离线（不发网络请求、不需要 Core 进程），必须在插件被放在
`<core>/gsuid_core/plugins/MusicUID/` 时运行——`SV()` 依赖路径中的 `plugins` 段推断归属：

```bash
pytest gsuid_core/plugins/MusicUID/MusicUID/tests
ruff check gsuid_core/plugins/MusicUID
ruff format --check gsuid_core/plugins/MusicUID
```

## 许可

MIT。音乐版权归各平台与权利人所有，本插件仅供学习交流使用。
