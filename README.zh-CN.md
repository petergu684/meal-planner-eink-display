# 电子墨水屏每周菜谱

[English](README.md) | **中文**

一款用于 **ELECROW CrowPanel ESP32-S3 5.79 寸电子墨水屏** 的每周菜谱展示项目（[Amazon](https://www.amazon.com/dp/B0FX4PDW6M)）。一台一直开机的小型 Linux 服务器（树莓派、NVIDIA Jetson、NUC，任何长期在线的设备都行)根据你的数据生成菜谱图片并提供 HTTP 服务；墨水屏设备主动拉取并显示图片。ESP32-S3 上的深度睡眠固件让设备只靠一块小锂电池就能运行数月。

> 💡 **配套项目：** 这个仓库是 *显示前端*，从一个 SQLite 数据库里读取菜谱。对应的后端 —— 也就是你实际编辑菜谱的地方 —— 是 **[meal-planner](https://github.com/petergu684/meal-planner)**：一个自托管的 FastAPI 应用，提供网页 UI 来管理菜品、拖拽式周菜谱编排、自动生成购物清单等等。两个仓库之间只通过一个 SQLite 文件通信；如果你不想用 meal-planner，也可以接入任何其他数据源 —— 只需要改写一个 Python 函数（详见下文 [接入你的数据源](#接入你的数据源)）。

![预览图](docs/preview.png)

## 功能特点

- **省电** — ESP32-S3 在每次刷新之间进入深度睡眠（约 10 µA）。墨水屏在断电后仍能保持图像。一块 2100 mAh 电池预计可以使用数月。
- **每日自动刷新** — 每天在指定时间（默认本地时间 02:00）唤醒一次，同步 NTP 时间、获取本周菜谱、重绘屏幕，然后再次进入睡眠。
- **旋转按钮浏览** — 板载旋转编码器：向上 = 上一周，向下 = 下一周，按下 = 刷新当前周；空闲 2.5 秒后自动睡眠。
- **支持中文** — 通过 Noto Sans CJK 字体渲染中文（或任意 Unicode 字符）菜名。
- **高亮今日** — 当天所在列以反色显示（黑底白字），一眼就能看出今天吃什么。
- **可替换的数据源** — 默认读取下文描述的 SQLite 数据库结构；只需替换 `get_meal_plan()` 函数即可接入任何数据源（CalDAV、Google Sheets、REST API 等）。
- **网页设置界面** — 首次启动时提供 WiFi 配网页面;固件中不内置任何 WiFi 凭据。

## 系统架构

```
 ┌────────────────────────┐    ┌──────────────────────┐    ┌──────────────────────────┐
 │  meal-planner          │    │  本仓库               │    │  ESP32-S3 + 墨水屏       │
 │  (FastAPI 网页应用)    │    │  (图片生成 + 服务)   │    │  CrowPanel 5.79 寸       │
 │                        │    │                      │    │                          │
 │  • 浏览器 UI           │───▶│  • image_server.py   │◀───│  • 每天 02:00 唤醒       │
 │    菜品库、周菜谱、    │SQLite│   读取该 DB          │HTTP│  • 或被按键唤醒          │
 │    购物清单            │ 文件 │  • 按需渲染 792x272  │ 拉取│  • 拉取图片              │
 │  • 把菜谱写入 SQLite   │    │   1-bit 位图          │    │  • 显示                  │
 │                        │    │  • get_meal_plan()   │    │  • 深度睡眠到下次唤醒    │
 │                        │    │   是接入数据源的接口 │    │                          │
 └────────────────────────┘    └──────────────────────┘    └──────────────────────────┘
   github.com/.../meal-planner   ← 本仓库 →                   贴在冰箱上
```

- **meal-planner** 服务把菜谱写到 `data/meal_planner.db`（SQLite）。
- 本仓库的 **图片服务器** 直接读这个数据库文件，每次 HTTP 请求时实时渲染一张图。
- **ESP32** 是主动方 —— 它定时唤醒、拉取图片、显示、然后回到睡眠。

只有当你想要一个好看的网页 UI 来编辑菜谱时才需要 meal-planner。如果你的菜谱数据已经在别处维护，可以完全跳过 meal-planner，只需要替换一个函数（详见下文）。

## 硬件清单

| 部件 | 说明 |
|------|------|
| **ELECROW CrowPanel ESP32-S3 5.79 寸电子墨水屏** | 分辨率 792x272 像素，1 位黑白，GDEY0579T93 面板带双 SSD1683 主控（[产品页](https://www.elecrow.com/crowpanel-esp32-5-79-e-paper-hmi-display-with-272-792-resolution-black-white-color-driven-by-spi-interface.html)、[厂商 GitHub](https://github.com/Elecrow-RD/CrowPanel-ESP32-5.79-E-paper-HMI-Display-with-272-792)）。**屏幕不附带电池** —— 如果想用电池供电，需要另外购买。 |
| **USB-C 数据线** | 用于烧录和充电 |
| **单节 3.7V 锂电池（可选，电池供电时需要)** | 板载电池接口是 **JST SH 1.0mm 双针**。任何带匹配接口的单节锂电池都能用 —— 例如[我用的这块 2100 mAh 电池](https://www.amazon.com/dp/B0F1TF89ZC)。**不同厂家电池的接口极性并不统一** —— 很多挂着「JST SH」标签的电池实际接线反向，轻则板子上不了电，重则烧坏元件。插之前一定先对照 ELECROW 的原理图核对正负极，需要的话小心地把两根线从接口外壳里拔出来重新插。极性正确接好后，板载 LTC4054 充电芯片会自动管理充电，用普通手机 USB-C 充电器就能给它充电。 |
| **长期在线服务器** | 任何能跑 Python 3.8+ 且有固定局域网 IP 的设备。一台树莓派 Zero 2 W 都绰绰有余。 |

固件使用的引脚（来自 ELECROW 官方原理图）：

| 功能 | GPIO |
|---|---|
| 墨水屏 SCK / MOSI | 12 / 11 |
| 墨水屏 CS / DC / RST / BUSY | 45 / 46 / 47 / 48 |
| **屏幕电源使能** | **7**（必须置高） |
| 旋转编码器 UP / DOWN / OK | 6 / 4 / 5 |
| HOME / EXIT 按钮 | 2 / 1 |

> ⚠️ **关于 GPIO 7**：墨水屏在 GPIO 7 拉高之前是没有电的。许多为其他 ESP32 墨水屏（如 LilyGo T5 4.7 寸）写的"通用"示例代码可以在这块板子上编译通过、运行起来，但屏幕永远不会刷新 —— 这是头号大坑。

## 仓库结构

```
.
├── firmware/
│   └── meal_receiver/
│       └── meal_receiver.ino   # ESP32-S3 深度睡眠固件
├── sender/
│   ├── send_meal_plan.py        # 命令行：生成图片并推送到设备
│   ├── image_server.py          # HTTP 服务：设备从这里拉取图片
│   ├── update_display.sh        # 适合 cron 调用的 send_meal_plan.py 包装脚本
│   └── meal-image-server.service # systemd 服务单元模板
├── docs/
│   └── preview.png
├── LICENSE
└── README.md
```

## 安装配置

### 1. 服务器端

需要 Python 3.8+ 和 Pillow：

```bash
pip3 install Pillow
```

如果需要显示中文菜名，请安装 CJK 字体（脚本会自动检测 Noto Sans CJK）：

```bash
# Debian/Ubuntu/Raspberry Pi OS
sudo apt install fonts-noto-cjk

# Fedora
sudo dnf install google-noto-sans-cjk-fonts
```

#### 接入你的数据源

##### 方案 A —— 配合配套的 [meal-planner](https://github.com/wenhao-anthropic/meal-planner) 应用（推荐）

配套的后端仓库是一个自托管的 FastAPI 应用，提供手机/平板友好的网页 UI 用来管理菜品、拖拽式周菜谱编排、自动生成购物清单等等。它把数据写到一个 SQLite 文件，本仓库的图片服务器直接读取这个文件。

```bash
# 把两个仓库克隆到同一目录下
git clone https://github.com/wenhao-anthropic/meal-planner.git
git clone https://github.com/wenhao-anthropic/eink-meal-display.git

# 按 meal-planner 的 README 配置好它，然后让本服务指向它的数据库：
export MEAL_PLANNER_DB=/absolute/path/to/meal-planner/data/meal_planner.db
python3 eink-meal-display/sender/image_server.py
```

两个进程可以跑在同一台机器上，它们之间不通过网络通信，只共享一个 SQLite 文件。SQLite 完美支持「多个读者（本服务器）+ 单个写者（meal-planner）」的并发模式。

##### 方案 B —— 使用你自己的数据库

如果你维护的数据库结构和 meal-planner 一样，直接指向它即可：

```sql
CREATE TABLE dish (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE meal_plan (
    id          INTEGER PRIMARY KEY,
    week_start  TEXT NOT NULL,        -- 'YYYY-MM-DD'（必须是周一)
    day_of_week INTEGER NOT NULL,     -- 0=周一 ... 6=周日
    meal_type   TEXT NOT NULL,        -- 'lunch'|'dinner'
    dish_id     INTEGER NOT NULL REFERENCES dish(id)
);
```

```bash
export MEAL_PLANNER_DB=/path/to/your.db
```

##### 方案 C —— 任何其他数据源

如果你的菜谱数据存在 Google Sheets、Notion、REST API、YAML 文件或其他地方，只需重写 `sender/send_meal_plan.py` 里的 `get_meal_plan()` 函数，返回如下结构的字典：

```python
{
  "2026-04-13": {"lunch": ["意面", "沙拉"], "dinner": ["牛排"]},
  "2026-04-14": {"lunch": [...],            "dinner": [...]},
  ...
}
```

其他所有部分（图像渲染、图片服务器、ESP32 固件）都不需要修改。

#### 不连接设备预览图片

```bash
python3 sender/send_meal_plan.py --preview --output /tmp/preview.png
```

会得到一张 792x272 的 1-bit PNG，可以用任何图片查看器打开。

#### 启动图片服务器

```bash
python3 sender/image_server.py
# Serving on 0.0.0.0:5000
```

生产环境推荐使用 systemd（先替换模板里的占位符）：

```bash
# 编辑 sender/meal-image-server.service，把以下占位符替换为你的实际值：
#   <USER>      → 你的用户名
#   <REPO_DIR>  → 本仓库的绝对路径
#   <PYTHON>    → `which python3` 的输出

sudo cp sender/meal-image-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now meal-image-server
sudo systemctl status meal-image-server
```

### 2. 烧录固件

在任何通过 USB-C 连接到屏幕的电脑上用 Arduino IDE 2.x：

1. **添加 ESP32 开发板支持**：文件 → 偏好设置 → 附加开发板管理器网址：
   ```
   https://espressif.github.io/arduino-esp32/package_esp32_index.json
   ```
   然后 工具 → 开发板 → 开发板管理器 → 安装 **"esp32" by Espressif Systems**（建议 v3.0+）。

2. **安装库**（项目 → 包含库 → 管理库）：
   - `GxEPD2` by Jean-Marc Zingg
   - `Adafruit GFX Library`
   - `Adafruit BusIO`

3. **开发板设置**（工具菜单)：
   - 开发板: **ESP32S3 Dev Module**
   - PSRAM: **OPI PSRAM**
   - Flash Size: **8MB**
   - Partition Scheme: **Huge APP (3MB No OTA / 1MB SPIFFS)**
   - USB CDC On Boot: **Enabled**

4. **设置时区** — 在 `firmware/meal_receiver/meal_receiver.ino` 里：
   ```c
   const char* POSIX_TZ = "EST5EDT,M3.2.0,M11.1.0";  // 改成你所在的时区
   ```
   POSIX TZ 语法说明：参见 [GNU 手册](https://www.gnu.org/software/libc/manual/html_node/TZ-Variable.html)。常用示例：
   - 美国东部: `"EST5EDT,M3.2.0,M11.1.0"`
   - 美国太平洋: `"PST8PDT,M3.2.0,M11.1.0"`
   - UTC: `"UTC0"`
   - 中欧时间: `"CET-1CEST,M3.5.0,M10.5.0/3"`
   - 中国: `"CST-8"`

5. 打开 `firmware/meal_receiver/meal_receiver.ino` 点击上传。如果上传失败，按住板上的 BOOT 按钮，点上传，看到 "Connecting…" 时再松开 BOOT。

### 3. 首次启动配置

烧录完成后，设备没有保存 WiFi，会进入 AP 配网模式：

1. 屏幕显示: `Setup Mode — WiFi: MealDisplay / 12345678 — Open 192.168.4.1`。
2. 用手机或电脑连接 WiFi **`MealDisplay`**，密码 **`12345678`**。
3. 浏览器打开 **<http://192.168.4.1>**：
   - **Image Server** → 填入图片服务器的局域网 IP（如 `192.168.1.50`）和端口（`5000`）。保存。
   - **WiFi** → 填入家里 WiFi 的 SSID 和密码。保存并重启。
4. 设备会重启并连上你家 WiFi，然后拉取本周菜谱图片，显示出来，再进入深度睡眠。

屏幕底部会出现一行小小的状态信息，显示最近一次拉取的时间和下一次计划唤醒的时间 —— 不需要打开串口监视器就能确认调度是否正常。

## 日常使用

- 配置完成后，设备就可以无人值守地运行。每天在指定时间（默认本地时间 02:00）唤醒一次，拉取最新图片，然后睡到第二天。
- 板载的旋转编码器只要被按下就会唤醒：**上** = 上一周，**下** = 下一周，**按下** = 刷新当前周。空闲 2.5 秒后再次进入深度睡眠。
- 推送式流程依然可用（适合做临时测试和手动更新）：
  ```bash
  python3 sender/send_meal_plan.py --ip 192.168.1.42
  ```
  仅在设备恰好醒着的时候才能成功（比如你刚按过按钮）。

## 配置参考

### 唤醒时间

在 `firmware/meal_receiver/meal_receiver.ino` 中：

```c
#define WAKE_HOUR    2   // 0–23
#define WAKE_MINUTE  0
```

### 活动超时

```c
#define ACTIVE_TIMEOUT 2500  // 按钮空闲多少毫秒后进入深度睡眠
```

### 图片服务器接口（`image_server.py`)

| 接口 | 说明 |
|---|---|
| `GET /meal_image?offset=0` | 当前周的原始 1-bit 位图（26928 字节） |
| `GET /meal_image?offset=-1` | 上一周 |
| `GET /meal_image?offset=1` | 下一周 |
| `GET /health` | 返回 `OK` |

### 设备网页接口（仅 AP 配网模式有效)

| 接口 | 说明 |
|---|---|
| `GET /` | HTML 配置页 |
| `POST /wifi` | 保存 WiFi 凭据并重启 |
| `POST /server` | 保存图片服务器 IP 和端口 |
| `GET /reset` | 清除 WiFi 凭据，重启回 AP 模式 |

## 功耗分析

| 状态 | 电流 |
|---|---|
| 深度睡眠 | ~10 µA |
| 唤醒 → WiFi → 拉取 → 绘制（约 10 秒) | ~150 mA |
| 按键活跃模式（最后一次按键后 ≤2.5 秒) | ~150 mA |

一块 2100 mAh 锂电池，按每天唤醒一次加偶尔按键操作的使用强度估算，应该可以用好几个月。

## 故障排查

**烧录后屏幕完全不刷新。**
GPIO 7（屏幕电源）没有被置高。本项目固件已经显式做了这件事。如果你改过代码，请确保 `enableDisplayPower()` 在其他逻辑之前运行。

**状态行显示 UTC 时间而不是本地时间。**
POSIX TZ 字符串没有生效。固件在 `configTime()` 返回 **之后** 才调用 `setenv("TZ", …)`；如果你调换了顺序，请改回去。

**编译报错: `'ESP_GPIO_WAKEUP_GPIO_LOW' was not declared`。**
你的 ESP32 Arduino 内核版本偏旧。固件使用 `esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW)` 替代 —— 请确保本地代码与仓库一致。

**macOS 上传时报错 `termios.error: (22, 'Invalid argument')`。**
重新插拔 USB-C 线缆，重选 Tools → Port，或者换一根数据线（有些线只能充电）。如果都不行，上传时按住 BOOT 按钮。

**设备每次重启 IP 都变。**
在路由器后台为设备 MAC 地址保留一个 DHCP 租约，或者修改固件用静态 IP。大多数家用环境下，DHCP 保留是最简单的做法。

**能监控电池电量或充电状态吗？**
ELECROW 这块板子 **没有** 把电池电压引到任何 ADC 引脚，LTC4054 的 `CHRG` 状态引脚也没接到 GPIO。如果不动焊枪（在 BAT 焊盘上接一对 100k/100k 分压电阻到空闲的 ADC 引脚），软件上读不出电池状态。

## 适配其他屏幕

图像生成和服务的代码是通用的 —— 可以生成任意尺寸的 1-bit 位图。如果要换其他墨水屏：

1. 调整 `send_meal_plan.py` 和 `image_server.py` 调用中的 `DISPLAY_WIDTH` / `DISPLAY_HEIGHT` 匹配你的屏幕尺寸。
2. 把固件里 `GxEPD2_BW<…>` 模板参数换成对应你屏幕型号的 GxEPD2 类（[GxEPD2 支持列表](https://github.com/ZinggJM/GxEPD2#supported-spi-e-paper-panels-from-good-display)）。
3. 修改引脚定义。

## 致谢

- 墨水屏驱动：Jean-Marc Zingg 的 [GxEPD2](https://github.com/ZinggJM/GxEPD2)。
- 感谢 ELECROW 提供硬件和[参考代码仓库](https://github.com/Elecrow-RD/CrowPanel-ESP32-5.79-E-paper-HMI-Display-with-272-792)。
- 与 Claude（Anthropic）密切协作完成。

## 许可证

MIT —— 详见 [LICENSE](LICENSE)。
