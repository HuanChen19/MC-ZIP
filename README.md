<div align="center">

<img src="docs/logo.png" width="140" alt="MC-ZIP">

# MC-ZIP

**Minecraft 基岩版 Addon 打包工具** · 一键打包 / 版本号自增 / UUID 随机刷新

作者：**幻尘**　
**Vibe Coding开发**

</div>

---

面向 Minecraft 基岩版（Bedrock）Addon 开发的 Windows 桌面小工具。
导入 Addon 文件夹后，可以一键打包成 `.mcaddon` / `.mcpack` / `.zip`，并自动处理 `manifest.json` 的版本号与 UUID。

- **只打包 BP / RP**：皮肤包、世界模板、文档等杂项一律不进压缩包
- **记得住项目**：最近打开的文件夹、输出目录、选项偏好都会记下来，下次直接开工
- 界面：Python + tkinter（**零第三方依赖**，随系统 DPI 自动缩放）
- 安全：修改前自动备份，原子写入，键顺序与缩进风格原样保留

---

## 一、快速开始

### 方式 1：直接运行 exe（推荐）

双击 `dist\MC-ZIP.exe` 即可。

也可以把 Addon 文件夹直接拖到 exe 图标上，程序会自动导入该文件夹：

```
MC-ZIP.exe "D:\我的项目\MyAddon"
```

### 方式 2：运行源码

```bat
python addon_packer.py
```

或双击 `启动(源码运行).bat`（需要 Python 3.8+，且安装时勾选了 tkinter）。

### 方式 3：Ore UI 网页界面版

同一套核心逻辑，配 Minecraft Ore UI 风格界面（亮色 / 暗色 / 跟随系统三态主题）：

```bat
python mczip_web.py
```

或双击 `启动(网页界面).bat`；也可把 Addon 文件夹拖到该 bat 上。

- 界面跑在本机 `127.0.0.1` 的临时端口上，用 Edge 应用窗口承载（零第三方依赖）
- 文件操作、版本号、UUID、打包全部由 Python 核心完成，与桌面版行为一致
- 想要原生 WebView2 窗口：`pip install pywebview` 后加 `--pywebview`
- 只起服务不开窗口（调试用）：`--no-window`，会打印界面地址
- 打包成 exe：双击 `build_exe_web.bat`，产物在 `dist-web\MC-ZIP.exe`

### 方式 4：自己重新打包 exe（tkinter 版）

双击 `build_exe.bat`，产物在 `dist\MC-ZIP.exe`。
脚本会自动创建隔离虚拟环境 `.build\vpy` 并安装 PyInstaller，不会污染系统 Python。

---

## 二、界面与操作流程

| 区域 | 说明 |
| --- | --- |
| ① 导入 Addon 文件夹 | 「文件夹路径」是可编辑下拉框：直接输入 / 从历史里挑；「浏览文件夹…」选目录；「最近打开 ▾」是历史菜单；回车即可导入 |
| ② 检测到的 manifest.json | 列出每个包目录、「打包」标记、类型、当前版本、header.uuid、模块数、相对路径；**双击某行**可在资源管理器中打开该目录 |
| ③ 操作 | 两个核心按钮 + 若干选项 |
| ④ 操作日志 | 每一次字段改动都会逐条打印「旧值 -> 新值」 |
| 底部状态栏 | 当前状态提示 |

### 记住最近打开的项目

不用每次都重新选文件夹了：

- **自动记住**：每次导入一个 Addon 文件夹，就会写进「最近打开」列表（最多 10 条，按最近使用排序，重复打开只会置顶不会重复）
- **启动时自动打开上次的项目**：默认开启，可在导入区右侧取消勾选
- **历史下拉**：点「文件夹路径」输入框的下拉箭头，直接挑历史项目；选中的瞬间就导入
- **最近打开菜单**：「最近打开 ▾」里按顺序列出历史项目，已不存在的会标灰并禁用；菜单底部还有「打开当前项目目录」「打开输出目录」「清除最近记录」
- **记住每个项目的输出目录**：给某个项目单独改过输出目录后，下次打开这个项目会自动还原
- **记住选项偏好**：版本递增位、输出格式、UUID 格式、各勾选项都会记住，下次启动直接沿用

配置文件位置（按优先级自动选择，通常只需知道第一个）：

1. 程序（exe）同目录下的 `config.json` —— 便携模式，整个文件夹拷走配置也跟着走
2. `%APPDATA%\MC-ZIP\config.json` —— 程序目录不可写时兜底

配置里只存路径字符串和开关，不含任何项目内容；删掉该文件即恢复出厂设置。

### 核心功能 1：打包并升级版本

点击 **「打包并升级版本」**：

1. 扫描 Addon 中所有 `manifest.json`
2. 只保留**行为包（BP）/ 资源包（RP）**，其余一律不参与（详见下节）
3. 把 `header.version` 递增（默认补丁位：`[0,0,1]` → `[0,0,2]`）
4. 写回 `manifest.json`（默认先备份为 `manifest.json.bak`）
5. 打包输出到输出目录

可选选项：

- **只处理 BP / RP**（默认开启）：打包、版本号、UUID 三项都只作用于 BP / RP
- **打包时升级版本号**：取消勾选则只打包、不写任何文件（纯只读打包）
- **同时升级 modules 内版本号**：默认关闭，只动 `header.version`
- **版本递增位**：补丁位（第 3 位）/ 次版本（第 2 位）/ 主版本（第 1 位）

版本递增规则：

| 设置 | `[1,4,7]` 递增后 | 说明 |
| --- | --- | --- |
| 补丁位 | `[1,4,8]` | 默认，只加最后一位 |
| 次版本 | `[1,5,0]` | 第 2 位 +1，其右位归零 |
| 主版本 | `[2,0,0]` | 第 1 位 +1，其右位归零 |

### 核心功能 2：随机刷新 UUID

点击 **「随机刷新 UUID」** 一次性完成：

- `header.uuid` → 新的随机 UUID v4
- `modules[*].uuid` → 每个模块各自新的随机 UUID（可在选项里关闭）
- 同一次操作内不会产生重复 UUID；BP 与 RP 的 UUID 互相独立

**不会改动**：`dependencies` 里的 uuid（它指向外部包，改了会导致依赖断裂）、`header.name`、`description`、`min_engine_version`、`metadata` 等一切非目标字段。

UUID 格式可选：带横线（标准，推荐）或 32 位无横线。

---

## 三、打包范围：只保留 BP / RP

导入 Addon 后，程序会扫描出所有含 `manifest.json` 的目录，并**按 modules 里的 type 判定包类型**：

| 包类型 | 判定依据（`modules[*].type`） | 是否打包 |
| --- | --- | --- |
| 行为包 BP | `data` / `script` / `javascript` | ✅ |
| 资源包 RP | `resources` / `client_data` / `interface` | ✅ |
| 皮肤包 | `skin_pack` | ❌ |
| 世界模板 | `world_template` | ❌ |
| 其他自定义类型 | 以上之外的 type | ❌ |

**Addon 根目录下的其他内容同样不会进压缩包** —— 例如 `docs/`、`tools/`、`screenshots/`、
`readme.txt` 这类没有 `manifest.json` 的目录和文件，以及任何非 BP/RP 的包目录。

因此 `.mcaddon` 解压后，顶层只会出现 BP 与 RP 两个目录，干净且不会让 MC 误识别。

包列表里的「打包」一列会实时标出哪些包会被打包、哪些被跳过（跳过的行显示为灰色）。

需要连皮肤包一起打包时，取消勾选 **「只处理 BP / RP」** 即可；此时非包目录（如 `docs/`）
仍然不会被压进去。

> 例外：如果你导入的就是**单个包目录本身**（该目录下直接有 `manifest.json`），
> 程序不会做类型过滤——你已经明确指定了要打包哪个目录。

---

## 四、压缩包结构

| 输出格式 | 适用场景 | 压缩包结构 |
| --- | --- | --- |
| `.mcaddon` | 一份 Addon 含多个包（BP + RP 等） | 各子包以**文件夹**并列放在压缩包根部 |
| `.mcpack` | 单个包 | 包内容直接位于压缩包根部，`manifest.json` 在根 |
| `.zip` | 只想打包一份目录快照 | 不套外层目录：单包时内容直接在根，多包时各包文件夹并列 |

> `.mcaddon` 采用「子包各自成文件夹」的结构，是为了避免 BP 与 RP 的
> `manifest.json`、`pack_icon.png` 在压缩包根部互相覆盖。
> Minecraft 导入时会递归查找所有 `manifest.json`，这种结构可正常识别。

`.zip` 与 `.mcaddon` 的区别只有一点：**`.zip` 不保留 Addon 根目录名**。
也就是说，选中 `MyAddon_BP`、`MyAddon_RP` 这两个文件夹打包时：

```
MyAddon_v1.3.0.zip          MyAddon_v1.3.0.mcaddon
├── MyAddon_BP/             ├── MyAddon_BP/
│   ├── manifest.json       │   ├── manifest.json
│   └── ...                 │   └── ...
└── MyAddon_RP/             └── MyAddon_RP/
    └── ...                     └── ...
```

而只选**一个**包目录打包成 `.zip` 时，包内容会直接铺在压缩包根部，
连包文件夹那一层也不会有（适合单包分发）：

```
MyAddon_BP_v0.0.1.zip
├── manifest.json
├── pack_icon.png
└── ...
```

文件名形如 `MyAddon_v1.3.0.mcaddon`，版本号取各包中的最高版本。

### 包内仍会自动忽略的内容

- 目录：`.git` `.svn` `.idea` `.vscode` `__pycache__` `node_modules` `.venv` 等，以及任何以 `.` 开头的目录
- 文件：`.DS_Store` `Thumbs.db` `desktop.ini` `manifest.json.bak`
- 后缀：`.bak` `.pyc` `.pyo` `.swp` `.tmp` `.orig` `.log`
- 输出目录本身与已生成的压缩包（重复打包不会自我嵌套）

---

## 五、安全机制

对 `manifest.json` 的修改准确、可回溯：

1. **备份**：每次写入前把原文件复制为同目录的 `manifest.json.bak`（可在选项里关闭）。界面上的「还原备份」按钮可一键回滚。
2. **原子写入**：先写 `manifest.json.tmp` 再替换，避免中途失败写坏源文件。
3. **键顺序保留**：使用有序字典解析并回写，`format_version` / `header` / `modules` / `dependencies` 的原始顺序不变。
4. **缩进风格保留**：自动探测原文缩进（2 空格 / 4 空格 / Tab）并沿用；保留末尾换行与 BOM。
5. **中文不转义**：写回时 `ensure_ascii=False`，中文描述保持可读。
6. **只改目标字段**：其余内容字节级等价（除缩进重排外）。

---

## 六、目录结构

```
MC-ZIP\
├── addon_packer.py         GUI 主程序（tkinter 版入口）
├── mczip_web.py            Ore UI 网页界面版入口（HTTP 服务 + Edge 应用窗口）
├── addon_core.py           核心逻辑：扫描 / 版本号 / UUID / 打包
├── addon_config.py         配置持久化：最近打开的项目、选项偏好
├── frontend\               Ore UI 前端（HTML / CSS / JS + 资源包原贴图）
│   ├── index.html          界面结构：①导入 ②包列表 ③操作 ④日志
│   ├── css\oreui.css       Ore UI 设计系统（贴图九宫格 + 双主题变量）
│   ├── css\app.css         应用布局与组件样式
│   ├── js\core.js          addon_core.py 的浏览器移植版（可被 Node 引做自测）
│   ├── js\oreui.js         主题管理与通用组件（开关/下拉/弹窗/提示/滚动条）
│   ├── js\app.js           主逻辑（后端探测、包列表、打包、日志）
│   └── assets\             两个资源包的原贴图 + Silkscreen 字体 + logo
├── app.ico                 应用图标（多尺寸，16~256）
├── config.json             运行后自动生成（配置，可删）
├── README.md               本文件
├── LICENSE                 MIT
├── build_exe.bat           打包 tkinter 版 exe
├── build_exe_web.bat       打包网页界面版 exe
├── 启动(源码运行).bat        tkinter 版免打包运行
├── 启动(网页界面).bat        Ore UI 网页界面版免打包运行
├── docs\
│   └── logo.png            透明底 logo，用于 README 与网页界面
├── dev\                    开发辅助脚本（普通使用可忽略）
│   ├── selftest.py         核心逻辑自测（含 BP/RP 过滤、配置持久化等断言）
│   ├── smoketest_gui.py    GUI 端到端冒烟测试
│   ├── verify_exe.ps1      验证构建出的 exe 能否正常启动
│   ├── decode_image.ps1    把任意图片解码为裸像素（图标生成的辅助步骤）
│   └── make_logo.py        由图片生成 app.ico 与 docs/logo.png
├── dist\                   打包产物目录（tkinter 版 exe）
├── dist-web\               打包产物目录（网页界面版 exe）
└── .build\                 构建用隔离虚拟环境与测试数据（可整个删掉，不影响 exe）
```

---

## 七、界面二次开发

网页界面的视觉规范来自两个 Minecraft Java 版资源包，**贴图原样使用、未做重绘**：

| 主题 | 资源包 |
| --- | --- |
| 亮色 | `OreUI Expanded`（by DiamondIsntHere） |
| 暗色 | `Dark OreUI Recreation v2.5.2`（by bitznotmikel & tmc249） |

关键做法：

- 每个组件的九宫格参数取自贴图同名 `.mcmeta` 的 `gui.scaling.nine_slice.border`，
  映射到 CSS `border-image-slice`（顺序 T R B L，并加 `fill`），`border-width` 取 `2 × border`
  （渲染比例 2×，配 `image-rendering: pixelated`）
- 切换主题只改 `<html data-theme="light|dark">`，所有贴图与配色由 CSS 变量组切换
- 暗色包自带 `shaders/core/text.fsh`，会把 GUI 文字灰 `#3F3F3F` 档替换为白色，
  网页版沿用该规则（亮色标签用 `#3F3F3F`，暗色用 `#FFFFFF`）
- 资源包不含字体文件，故内置 OFL 许可的 Silkscreen 像素字体；中文回退系统黑体

前端可独立在浏览器打开调试：

```bat
:: 演示模式（内置内存虚拟 Addon，无需任何授权）
start frontend\index.html?demo=1
```

> 直接以 `file://` 打开时，浏览器不支持 File System Access API，界面会退化为「演示模式可用、
> 真实读写不可用」；正式使用请通过 `mczip_web.py` 或 `启动(网页界面).bat` 启动。

---

## 八、开发辅助

```bat
python dev\selftest.py                                    :: 核心逻辑自测
python dev\smoketest_gui.py                               :: GUI 端到端测试（会真实创建窗口）
python mczip_web.py --selftest                            :: 网页界面版后端自测（无窗口）
powershell -ExecutionPolicy Bypass -File dev\verify_exe.ps1   :: 验证 exe 能否启动
```

重新生成图标（换成自己的图片时）：

```bat
powershell -ExecutionPolicy Bypass -File dev\decode_image.ps1 你的图片.png .build\logo_src.raw
python dev\make_logo.py .build\logo_src.raw
```

`make_logo.py` 会自动识别棋盘格透明底、定位 logo 外接框与圆角半径、重建 alpha 通道，
输出多尺寸 `app.ico` 与 `docs\logo.png`，只依赖标准库。

`addon_core.py` 不依赖 tkinter，也可以直接作为库使用：

```python
import addon_core as core

packs = core.load_addon(r"D:\我的项目\MyAddon")
included, excluded = core.select_packs(packs, only_bp_rp=True, root=r"D:\我的项目\MyAddon")

for pack in included:
    core.bump_versions(pack, part="patch")            # 版本号 +1
    core.refresh_uuids(pack)                          # 刷新 UUID

# 只打包 BP / RP（默认），Addon 根目录下的其他内容不进压缩包
core.build_package(r"D:\我的项目\MyAddon", packs, r"D:\输出", only_bp_rp=True)
```

配置模块同样可以单独使用：

```python
from addon_config import Config

cfg = Config()                                   # 自动选择可写位置
cfg.remember_folder(r"D:\我的项目\MyAddon")       # 记入最近打开
cfg.set_output_dir(r"D:\我的项目\MyAddon", r"D:\输出")
cfg.set_options({"version_part": "minor"})
cfg.save()
print(cfg.recent_folders)
```

---

## 八、常见问题

**Q：网页界面版打开后一片空白 / 只有标题栏？**
请确认是通过 `启动(网页界面).bat` 或 `python mczip_web.py` 启动（走本地 HTTP 服务）。
若直接双击 `frontend\index.html` 用 `file://` 打开，浏览器会拦截本地文件读写，
界面会停在「未导入」状态——请改用上述启动方式。

**Q：网页界面版选择文件夹时弹的是不是浏览器自带的对话框？**
是系统原生「选择文件夹」对话框（由 Python 侧调用），不是网页控件。

**Q：提示「没有在该文件夹中找到任何 manifest.json」？**
请选择 Addon 的**根目录**（其下含 BP/RP 子文件夹），或直接选到某一个含 `manifest.json` 的包目录。程序最多向下查找 4 层。

**Q：我的 Addon 里还有皮肤包 / 世界模板，会被一起打包吗？**
不会。默认「只处理 BP / RP」开启，非行为包 / 资源包类型的包会被自动跳过（列表里显示为灰色「跳过」行）。
如果确实要一起打包，取消勾选该选项即可。

**Q：Addon 根目录下的 docs、素材、构建脚本会被压进去吗？**
不会。压缩包只会包含被认定为包的目录，且默认只含 BP / RP。

**Q：刷新 UUID 后游戏里显示重名或旧内容？**
UUID 变更后需要重新导入世界/全局资源。同一世界若仍引用旧 UUID，请同时在世界设置里重新启用该包。

**Q：为什么 `.mcaddon` 解压后是文件夹套文件夹？**
见「压缩包结构」一节，这是为了避免同名文件互相覆盖，Minecraft 能正常识别。
如果你不想要这层，改用 `.zip` 格式：它不保留 Addon 根目录名，单包时内容更是直接铺在压缩包根。

**Q：打包后 manifest.json 被改了，怎么恢复？**
点「还原备份」，会用 `manifest.json.bak` 覆盖当前的 `manifest.json`。

**Q：能处理 format_version 1 的老包吗？**
可以。老版本用 `header.uuid` 但不带 `modules`，程序会正常刷新 header 的 UUID，并在日志里提示「modules 为空」。
不过由于它无法判定 BP/RP 类型，在多包 Addon 中会被当作「其他类型」跳过，必要时取消勾选「只处理 BP / RP」。

**Q：怎么让它下次自动打开我的项目？**
导入一次就会记住。默认开启「启动时自动打开上次项目」，下次打开 exe 直接就是上次的项目。
不想自动加载就取消勾选，但仍然可以在「文件夹路径」下拉框或「最近打开 ▾」里一键选回。

**Q：项目文件夹改名 / 移动了怎么办？**
「最近打开 ▾」里会把失效的条目标灰并禁用。重新用「浏览文件夹…」选一次新位置即可，列表会自动更新。

**Q：记录会不会泄露或上传？**
不会。配置只存在本机 `config.json` 里，内容是路径字符串和界面开关，没有任何项目内容，也不联网。
