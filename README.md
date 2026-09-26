# MusicUID

多平台点歌插件 for [GsCore](https://github.com/Genshin-bots/gsuid_core)。

支持 **网易云音乐 / QQ音乐 / 酷狗音乐** 三平台搜索与高品质音频下发，支持网易云歌词与多平台分享链接解析；集成 **手机扫码长效自动续期（方案一）** 与 **自建/第三方音源微服务平滑容灾（方案五）**。

---

## 核心特性

- 🎵 **三平台并发聚合**：网易云音乐、QQ 音乐、酷狗音乐统一检索与连续编号点歌。
- 📱 **手机扫码免密登录与长效保活（方案一）**：
  - **QQ 音乐**：支持手机 QQ 扫码一键授权，获取长达数月的移动端专用凭据（`refresh_token` / `refresh_key`），后台定时（每 12 小时）自动静默向官方服务器续签顺延，告别网页 Cookie 72 小时频繁失效痛点。
  - **酷狗音乐**：支持手机酷狗 App 扫码秒登。
- 🌐 **自建 / 第三方音源微服务支持（方案五）**：
  - 支持接入自建 `QQMusicApi`、`NeteaseCloudMusicApi`、`lx-music-api-server` 等微服务或自定义 URL 模板。
  - **多级容灾调度**：支持「官方优先 / 自建兜底（`fallback_only`，默认）」与「自建优先（`custom_first`）」两种模式，遇到版权受限或未下发音频时自动无缝兜底。
- 🎨 **开箱即用卡片渲染**：优先采用 GsCore 内置 **pytakumi** 本地渲染（速度极快、无内存负担、零浏览器依赖）；同时提供 Chromium 自动下载回退机制。
- 🔗 **分享链接全自动解析**：自动识别群聊与私聊中的网易云、QQ 音乐、酷狗单曲/歌单/专辑链接或卡片并自动点播。
- 🔊 **智能音频转码投递**：默认发送语音（支持超大体积 ffmpeg 自动降码率压缩防风控与丢包），完美适配 QQ 官方机器人及各类第三方适配器。

---

## 依赖与环境

| 依赖 | 说明 |
| --- | --- |
| GsCore | 运行环境，包含 httpx / jinja2 / Pillow / APScheduler |
| **pytakumi** | 图片卡片渲染，**GsCore 已内置**——纯本地、不需要浏览器，开箱即用 |
| `playwright` + Chromium | 可选回退。仅当 core 没带 pytakumi 且开启自动安装时由插件后台下载 |
| `pycryptodome` | 网易云 weapi 加密（可选）。缺失时自动回退公开外链 |
| `ffmpeg` | 语音压缩（可选）。音频超过「语音体积上限」时才会调用 |

---

## 指令一览

所有指令无需特殊前缀，直接在群聊或私聊发送即可。

### 1. 点歌与播放

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `点歌 关键词` | 三平台并发搜索并列出卡片结果（编号全平台连续） | `点歌 晴天` |
| `点歌 <平台> 关键词` | 临时指定平台（网易 / QQ / 酷狗） | `点歌 QQ 晴天` |
| `播放 关键词` | 搜索并直接播放第一首 | `播放 晴天` |
| `听N` | 播放当前搜索列表第 N 首（列表 10 分钟内有效） | `听8` |
| `歌词 关键词` | 查看歌词（当前支持网易云） | `歌词 晴天` |
| `音乐帮助` | 查看插件完整帮助与使用说明 | `音乐帮助` |

### 2. 账号登录、自建音源与凭证管理

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `QQ登录` / `点歌登录 qq` | 弹出手机 QQ 授权二维码扫码绑定（长效自动续期） | `QQ登录` |
| `酷狗登录` / `点歌登录 酷狗` | 弹出手机酷狗 App 授权二维码扫码绑定 | `酷狗登录` |
| `QQ音乐刷新` | 手动向官方服务器请求刷新并顺延 QQ 音乐凭据有效期 | `QQ音乐刷新` |
| `设置自建api <URL>` | 配置自建/第三方音源微服务地址（输入「清空」即可移除） | `设置自建api http://127.0.0.1:3300` |
| `自建api模式 <模式>` | 切换调度优先级：`fallback`（官方优先/自建兜底）或 `first`（自建优先） | `自建api模式 fallback` |
| `测试自建api` | 测试当前配置的自建音源微服务连通性与健康状态 | `测试自建api` |
| `点歌状态` | 查看网易云、QQ 音乐、酷狗及自建音源服务的配置与剩余有效期 | `点歌状态` |
| `网易云cookie <MUSIC_U>` | 导入网易云网页 Cookie（MUSIC_U 字段） | `网易云cookie 123456...` |
| `点歌导入cookie <平台> <值>` | 手动导入指定平台 Cookie（需权限） | `点歌导入cookie qq uin=...; qm_keyst=...` |
| `点歌加白 <用户ID/@用户>` | 添加登录与配置白名单（仅主人可用） | `点歌加白 12345678` |
| `点歌删白 <用户ID/@用户>` | 移除白名单（仅主人可用） | `点歌删白 12345678` |
| `点歌白名单` | 查看当前白名单用户列表 | `点歌白名单` |

---

## 分享链接解析支持

在群内直接发送分享链接或音乐卡片即可触发自动解析并播放：

- **网易云音乐**（`music.163.com` / `y.music.163.com` / `163cn.tv` 短链）：
  - **单曲** `song?id=...` → 自动获取详情并播放；
  - **歌单 / 专辑** `playlist?id=...` / `album?id=...` → 提取歌曲列表并写入会话，支持「听N」点播。
- **QQ音乐**（`y.qq.com` 各子域，含 `c6.y.qq.com` 短链）：
  - 支持 `playsong.html?songid=...`、App 分享卡片 `playsong.html?...&songmid=...`、网页版 `songDetail/{songmid}`。
- **酷狗音乐**（`kugou.com` 各子域，含 `m.kugou.com`）：
  - 支持带 `hash=` 的直链以及带 `chain=` 的 App 移动分享短链。

---

## 配置项说明

所有配置均已注册在 GsCore 统一配置系统中，可在 **GsCore 网页控制台（WebConsole）→ 插件配置 → MusicUID** 中可视化修改并实时热重载生效：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `default_platform` | `netease` | 未指定平台时的默认搜索平台（`netease` / `qq` / `kugou`） |
| `custom_api_url` | 空 | 自建/第三方音源微服务地址（填入地址即开启，留空关闭） |
| `custom_api_priority` | `fallback_only` | 调度优先级：`fallback_only`（官方优先/自建兜底）或 `custom_first`（优先自建） |
| `custom_api_token` | 空 | 自建音源服务的认证 Token / 密钥（若无鉴权留空） |
| `netease_cookie` | 空 | 网易云 `MUSIC_U`，配置后 VIP 歌曲与高音质生效 |
| `netease_level` | `exhigh` | 网易云音质偏好：`standard` / `exhigh` / `lossless` |
| `qqmusic_cookie` | 空 | QQ 音乐 Cookie（推荐直接发送「QQ登录」扫码生成长效凭据） |
| `kugou_cookie` | 空 | 酷狗音乐 Cookie（推荐直接发送「酷狗登录」扫码绑定） |
| `max_list` | `5` | 每个平台最多搜索条数（1-10），未指定平台时卡片总数 = 条数 × 平台数 |
| `render_card` | `true` | 是否使用图片卡片展示搜索结果（关闭后回退纯文本） |
| `auto_install_render` | `true` | 缺失 pytakumi 时是否自动下载 Chromium 作为渲染回退 |
| `send_voice` | `true` | 是否以语音消息发送音频 |
| `send_file` | `false` | 是否额外再发一份音频文件（默认关闭） |
| `local_file_ref` | `false` | 是否强制使用 `file://` 本机路径引用（默认自动识别适配器渠道） |
| `enable_resolve` | `true` | 是否开启音乐分享链接与卡片自动解析 |
| `download_timeout` | `60` | 音频下载超时时间（秒） |
| `voice_max_mb` | `2` | 语音体积上限（MB），超出自动由 ffmpeg 进行智能压缩 |
| `voice_bitrate` | `64` | 压缩时使用的 mp3 码率（kbps） |
| `keep_temp_sec` | `60` | 临时音频与卡片文件的保留秒数 |

---

## 目录结构

```
MusicUID/
├── __init__.py / __nest__.py        # 插件入口
├── pyproject.toml / ruff.toml
├── README.md
└── MusicUID/
    ├── __init__.py                  # 插件元信息与 SV 注册
    ├── musicuid_card/                # 卡片数据封装与 pytakumi/HTML 模板
    ├── musicuid_config/              # 插件配置项模型定义
    ├── musicuid_help/                # 帮助菜单与指令指引
    ├── musicuid_lifecycle/           # 定时任务（凭据自动续期）与资源释放
    ├── musicuid_login/               # 扫码登录、凭据管理与自建 API 指令
    ├── musicuid_play/                # 点歌、播放、选歌与会话状态控制
    ├── musicuid_resolve/             # 音乐分享链接与卡片自动解析
    ├── tests/                        # 离线单元测试
    └── utils/
        ├── login/                    # QQ 移动端协议、酷狗、网易云扫码实现
        ├── provider/                 # 各平台官方取流与 custom_api 自建微服务解析
        ├── delivery.py               # 语音/文件转码与平台投递适配
        ├── http.py                   # 统一 HTTP 请求封装
        └── render.py                 # 双模卡片渲染引擎
```

---

## 开发与测试

```bash
# 运行离线单元测试
pytest gsuid_core/plugins/MusicUID/MusicUID/tests

# 代码风格与语法检查
ruff check gsuid_core/plugins/MusicUID
ruff format --check gsuid_core/plugins/MusicUID
```

---

## 许可协议

MIT License。音频与元数据版权归各音乐平台与原创权利人所有，本项目仅供技术研究与学习交流。
