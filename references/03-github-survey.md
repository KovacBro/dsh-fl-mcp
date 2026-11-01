# GitHub 上现成的 FL Studio MCP —— 调研结论

> 调研时间：2026-11。结论会过期，但判断标准不会。

## 一、候选清单

| 仓库 | 平台 | 能写音符 | 多声部 | 适合本机（Win + FL 26）|
|---|---|---|---|---|
| `rosasynthesiz/flstudio-mcp` | Win + macOS | ✅ 钢琴卷帘 | ✅ 多轨 MIDI 导出 | **最接近**，但依赖 loopMIDI 驱动 |
| `dawhubapp/flstudio-mcp` | macOS 为主 | ✅（离线改 .flp） | ✅ | ❌ live 模式绑 IAC Driver，离线模式要 bun + flpdiff |
| `karl-andres/fl-studio-mcp` | Win + macOS | ✅ | ❌ | ⚠ 只做钢琴卷帘，Win 支持不完整 |
| `calvinw/fl-studio-mcp` | macOS 主，Win 次要 | ✅ | ❌ | ⚠ 自己要写 Windows 的适配 |

## 二、它们的技术路线，只有两种

**路线 A：虚拟 MIDI 端口**
```
MCP → 虚拟 MIDI 端口 → FL 的 MIDI 控制器脚本（device_*.py）
```
- 需要装驱动：Windows 上是 loopMIDI，macOS 是 IAC Driver
- 优点：双向、实时、能做 transport/mixer/参数
- 缺点：**驱动是硬门槛**。loopMIDI 需要管理员权限装内核驱动；很多机器装不上或装了不稳
- 更关键的：FL 的 **MIDI 控制器脚本 API 没有钢琴卷帘音符接口**。
  所以这条路能控走带、混音、参数，**但写不了音符**——
  `rosasynthesiz` 的做法是「MIDI 端口管控制器 + 另一个 pyscript 管音符」，两套桥并存

**路线 B：钢琴卷帘脚本（flpianoroll）+ 文件队列**
```
MCP 写 JSON 队列 → FL 里跑一次 .pyscript → 脚本读队列、写音符、回写状态
```
- 不需要任何驱动，不需要 admin
- 缺点：只能操作**当前打开的那一个**钢琴卷帘；第一次要手动跑一次脚本
- 触发方式：FL 官方有 **Run last script again = Ctrl+Alt+Y**（钢琴卷帘 Tools 菜单里写着）
  → 手动跑过一次之后，外部发个快捷键就能反复触发，**不需要再点菜单**

## 三、为什么本技能包走路线 B

1. **本机没有 loopMIDI**，装驱动要提权，装完还要在 FL 的 MIDI Settings 里手工配输入输出端口
2. **FL 26 的钢琴卷帘脚本 API 是官方支持的写音符唯一入口**（`Piano roll script reference.txt` 就躺在
   `System\Config\Piano roll scripts\` 里），稳定、文档齐、不依赖第三方
3. 本机 FL 是**便携安装**（`D:\fls`），配置和脚本目录都在安装目录下，路线 B 天然适配
4. 只要拿到音符写入权，剩下的编曲逻辑全在自己的和声引擎里，不依赖别人的实现

代价（诚实说）：
- 一次手动初始化（打开钢琴卷帘 + 跑一次脚本）
- 一个钢琴卷帘一次只写一个声部
- 拿不到 transport / mixer 控制（那是路线 A 的活）

## 四、如果以后想要路线 A 的能力

要 transport、mixer、插件参数控制，才需要补虚拟 MIDI。届时：

1. 装 loopMIDI（或换 Windows MIDI Services loopback）
2. 建两个端口 `FLStudioMCP RX` / `FLStudioMCP TX`
3. 把 `rosasynthesiz/flstudio-mcp` 的 controller 脚本装进 `Settings\Hardware\`
4. 本技能包的 `fl_compose` / 六声部写作 **可以原样复用**——它只产出音符数据，不关心传输层

也就是说：**传输层可以换，编曲内核不用重写**。这是当前分层的好处。

## 五、判断一个 FL MCP 好不好的四条标准

1. 音符时间单位是不是**拍**（而不是 tick）——tick 会随 PPQ 变，跨工程就错
2. 力度是不是做了 0–127 ↔ 0.0–1.0 归一化——FL 脚本用的是后者
3. 有没有**幂等保护**——Ctrl+Alt+Y 一按就重跑，没做状态标记就会叠音符
4. 有没有**回读校验**——写完能不能读回来核对，否则 AI 永远在盲写

---

## 六、本项目的最终方案：第三条路（前两条之外）

调研完上面四个项目，实测下来两条主流路线在本机都不通，于是找到了第三条。

### 试过并放弃的

**路线 A（虚拟 MIDI）**：本机没 loopMIDI，装驱动要提权；而且 MIDI 控制器脚本
API 根本没有音符接口，写音符还得另配一套 pyscript —— 两套桥，维护成本翻倍。

**路线 B（点菜单跑 pyscript）**：能跑通，但要解决一堆 UI 自动化问题：
- FL 的工具菜单是自绘两栏面板，行位置随主题/窗口尺寸变化
- 播放列表和钢琴卷帘**是同一个窗口类** `TEventEditForm`，按类名抓会抓错
- `F7` 是显示/隐藏开关，不是「打开」
- 脚本在 FL 沙箱里 `os.replace` 会失败（能写 .tmp，替换被拒），外部拿不到回执

这些都能修（本项目也都修了），但每一步都要截图验证，脆弱且慢。

### 最终采用的：官方快捷键 + 文件对话框

钢琴卷帘菜单里本来就写着两个快捷键（`Piano roll menu` 官方文档）：

| 快捷键 | 动作 |
|---|---|
| `Ctrl+M` | 导入 MIDI 文件到当前钢琴卷帘（**只加音符**） |
| `Shift+Ctrl+M` | 把当前钢琴卷帘导出为 MIDI 文件 |

于是整条链路变成：

```
和声引擎 → 单声部 .mid 文件
   ↓ Ctrl+M
FL 标准文件对话框（文件名框已聚焦，直接打字 + 回车）
   ↓
FL「导入 MIDI 数据」对话框（默认设置就对，点「接受」）
   ↓
音符进钢琴卷帘
   ↓ Shift+Ctrl+M 反向导出
解析回来 → 和声引擎生成的数据逐音对比 → 闭环验证
```

优点：
- **不需要驱动、不需要 admin、不需要预装脚本**
- 标准 Windows 对话框，键盘可达，比点自绘菜单稳得多
- 任何 FL 版本通用（20.8+ 都有这两个快捷键）
- 自带**回读能力**：写完能导出解析核对，AI 不用盲写

缺点（诚实说）：
- 一次只能写一个钢琴卷帘（FL 的钢琴卷帘本来就是通道级的）
- 写不进和弦标记（marker）——要 marker 才用可选的脚本桥接模式
- 无法自动化换音源（FL 没接口）

### 两种模式并存

| 模式 | 工具 | 依赖 | 适用 |
|---|---|---|---|
| 自动导入（默认） | `fl_write_voice` / `fl_read_score` | 无 | 99% 的场景 |
| 脚本桥接（可选） | `fl_push_voice` | 需手动跑一次 `MCP_Bridge` | 需要和弦标记 marker |

同一个编曲内核，换传输层而已——这是分层设计的好处。
