# Climate Sleep Curve / 空调睡眠曲线

[![GitHub release](https://img.shields.io/github/v/release/eggleader-zhang/climate-sleep-curve)](https://github.com/eggleader-zhang/climate-sleep-curve/releases)
[![HACS validation](https://github.com/eggleader-zhang/climate-sleep-curve/actions/workflows/validate.yml/badge.svg)](https://github.com/eggleader-zhang/climate-sleep-curve/actions/workflows/validate.yml)
[![Hassfest validation](https://github.com/eggleader-zhang/climate-sleep-curve/actions/workflows/hassfest.yml/badge.svg)](https://github.com/eggleader-zhang/climate-sleep-curve/actions/workflows/hassfest.yml)

Climate Sleep Curve 是一个 Home Assistant 自定义集成，用于让已有的 `climate` 实体按照预设睡眠曲线，在夜间的离散时间点调整目标温度和可选风速。

它不会创建新的虚拟空调，不会打开设备，也不会改变运行模式、摆风或湿度；电源控制仅限用户明确启用后的自然结束关机。可视化曲线编辑和日常控制由独立的 [Climate Sleep Curve Card](https://github.com/eggleader-zhang/climate-sleep-curve-card) 提供。

> [!IMPORTANT]
> 本集成只在空调已经运行时调用 `climate.set_temperature`，并在曲线启用风速控制时调用 `climate.set_fan_mode`。只有控制器明确启用开关且会话自然运行到末尾时，才会调用 `climate.turn_off`。请求中不会包含 `hvac_mode`，集成永远不会尝试启动设备；空调为 `off`、`unavailable`、`unknown` 或实体不存在时会安全跳过。

## 主要功能

- 保存多条 4～12 小时的睡眠温度和风量曲线。
- 支持不控制风速、全程自动风和逐节点风量曲线。
- 为不同房间创建独立控制器，每个控制器可同时绑定多个空调实体。
- 手动启动、停止、重新开始和按星期定时启动。
- 会话启动时创建曲线快照，运行过程中编辑原曲线不会改变当前会话。
- Home Assistant 重启后恢复尚未到达的节点，不补发已经错过的节点。
- 自动进行摄氏/华氏转换、设备温度范围裁剪和步进吸附。
- 目标温度已经足够接近时不重复调用设备。
- 服务调用失败时可配置最多重试一次。
- 为每个控制器创建 Switch、Select、Sensor 和 Button 实体。
- 提供服务、事件、脱敏诊断和已认证 WebSocket 管理 API。
- 支持简体中文和英文。

## 要求

- Home Assistant `2025.1.0` 或更高版本。
- 至少一个支持目标温度的 `climate` 实体；同一控制器可选择最多 32 个实体。
- 如需图形化创建和编辑曲线，需要安装 Climate Sleep Curve Card。

## 安装

### 使用 HACS 自定义仓库

[![在 HACS 中打开此仓库](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eggleader-zhang&repository=climate-sleep-curve&category=integration)

1. 打开 HACS。
2. 进入右上角菜单中的“自定义仓库”。
3. 添加 `https://github.com/eggleader-zhang/climate-sleep-curve`，类别选择 **Integration**。
4. 搜索并下载 **Climate Sleep Curve**。
5. 重启 Home Assistant。

HACS 使用公开 GitHub 仓库作为下载源。Gitea 仅作为项目镜像，不应填写到 HACS 自定义仓库中。

### 手动安装

将仓库中的整个目录：

```text
custom_components/climate_sleep_curve
```

复制到 Home Assistant 配置目录，最终结构应为：

```text
/config/
└── custom_components/
    └── climate_sleep_curve/
        ├── __init__.py
        ├── manifest.json
        └── ...
```

然后重启 Home Assistant。仅重新加载 YAML 不足以加载新安装的 Python 集成。

## 初次配置

1. 打开“设置 → 设备与服务”。
2. 点击“添加集成”。
3. 搜索 **Climate Sleep Curve**。
4. 提交空白配置表单完成添加。
5. 安装前端卡片，通过卡片创建第一条曲线和控制器。

该集成只允许创建一个配置条目。曲线和控制器保存在 Home Assistant 的 `.storage` 中，不建议手工编辑存储文件。

## 核心概念

### 曲线 Profile

曲线描述从会话开始时刻算起的一组离散目标温度，以及可选的目标风速：

- 时长必须为 240～720 分钟。
- 包含 2～25 个节点。
- 第一个节点必须位于第 0 分钟。
- 节点时间必须严格递增并且位于曲线时长内。
- 内部统一使用摄氏温度，允许范围为 5～40 °C。
- 当前仅支持 `step` 离散执行，不做连续插值。
- 风速可设为不控制、全程 `auto`，或为每个温度节点选择一个设备原生 `fan_mode`。

例如，0 分钟为 26 °C、60 分钟为 26.5 °C，表示会话启动时尝试设为 26 °C，一小时后再尝试设为 26.5 °C；两个节点之间不会连续改变温度。

### 控制器 Controller

控制器把一条曲线绑定到一个或多个真实的 `climate` 实体，并保存：

- 控制器名称、启用状态和目标空调列表。
- 下一次会话默认使用的曲线。
- 自动启动时间与星期。
- 曲线自然结束后是否关闭所选空调，默认关闭此选项。
- 失败重试次数和重试间隔。
- `catch_up_window_minutes` 配置字段（当前重启恢复策略仍默认不补发错过节点）。

星期编号遵循 Python/Home Assistant 约定：`0` 为周一，`6` 为周日。自动启动使用 Home Assistant 配置的本地时区。
启用自然结束关机时，每台所选空调都必须在 Home Assistant 中声明支持 `TURN_OFF`；否则控制器保存会返回 `unsupported_turn_off`。

### 会话 Session

每次启动都会生成一个会话，并复制当时的曲线内容。这样可以保证：

- 修改曲线只影响之后启动的会话。
- 切换控制器默认曲线不会改变当前会话。
- 控制器可选择在曲线自然结束后关闭空调；该选择会在会话启动时形成快照。
- 手动停止、替换、重新开始或删除控制器不会关闭空调。
- 停止会话只取消后续调温任务，不会恢复启动前温度。

## 推荐使用流程

1. 确认目标空调已作为 `climate.xxx` 接入 Home Assistant。
2. 安装后端集成和前端卡片。
3. 在卡片中点击“开始设置”。
4. 创建默认 8 小时曲线，并多选要绑定的空调。
5. 拖动曲线节点，保存所需温度，并按需选择全程自动风或风量曲线。
6. 在控制器设置中按需启用每日自动启动，以及“曲线自然结束后关闭空调”。
7. 睡前先用原空调控制方式打开空调，再点击“启动曲线”。

如果启动时空调处于关闭状态，会话仍会正常记录进度，但对应温度节点会得到 `skipped_off`，不会自动打开空调。

## Home Assistant 实体

每个控制器会创建设备及以下实体。一个节点会独立应用到该控制器选择的所有空调；某台设备关闭、不可用或失败时，不影响其他设备：

| 类型 | 用途 |
| --- | --- |
| `switch` | 打开时启动会话，关闭时停止会话；不会开关真实空调 |
| `select` | 选择控制器下一次会话使用的默认曲线 |
| `sensor` | 显示 `idle`、`running`、`completed`、`cancelled` 等状态 |
| `button` | 停止当前会话并从第 0 分钟重新开始 |

状态传感器在运行时还会提供进度、下一执行时间、下一目标温度、下一目标风速、会话 ID、开始/结束时间、最近结果和最近错误等属性。

## 服务

### 启动曲线

```yaml
action: climate_sleep_curve.start
data:
  controller_id: "控制器 ID"
```

可选字段：

- `profile_id`：临时使用另一条曲线，不修改控制器默认曲线。
- `replace: true`：已有活动会话时将其标记为已替换，并启动新会话。

### 停止曲线

```yaml
action: climate_sleep_curve.stop
data:
  controller_id: "控制器 ID"
```

停止不会关闭空调，也不会改变其当前目标温度，即使当前会话启用了自然结束关机。

### 重新应用当前节点

```yaml
action: climate_sleep_curve.apply_current_point
data:
  controller_id: "控制器 ID"
```

该服务根据已运行时间找到最近的曲线节点并重新执行，但不把它重复写入节点历史。空调关闭或不可用时仍会安全跳过。

### 重新加载存储和计划

```yaml
action: climate_sleep_curve.reload
```

此服务重新加载持久化数据并重建调度，不控制任何空调设备。

控制器 ID 和曲线 ID 可以从卡片状态、实体属性或 WebSocket 状态接口中取得。它们是内部不透明标识，不应根据名称自行推导。

## 自动化示例

如果不使用控制器内置的每日计划，也可以用 Home Assistant 自动化启动：

```yaml
alias: 工作日晚间启动睡眠曲线
triggers:
  - trigger: time
    at: "23:00:00"
conditions:
  - condition: time
    weekday:
      - mon
      - tue
      - wed
      - thu
      - fri
actions:
  - action: climate_sleep_curve.start
    data:
      controller_id: "控制器 ID"
mode: single
```

是否自动打开空调应由你单独、明确地设计自动化；本集成本身不会打开空调。可选的关机功能只在会话本次正常运行到曲线末尾时执行。

## 节点执行结果

常见结果包括：

| 结果 | 含义 |
| --- | --- |
| `applied` | 已应用目标温度或风速 |
| `no_change` | 当前目标温度或风速已经符合要求，没有重复调用 |
| `skipped_unsupported` | 设备不支持该风速，温度调整仍可继续 |
| `skipped_off` | 空调处于关闭状态 |
| `skipped_unavailable` | 空调不可用 |
| `skipped_unknown` | 实体不存在或状态未知 |
| `skipped_off_after_failure` | 首次调用失败后设备变为关闭状态 |
| `skipped_mixed` | 多台设备均被跳过，但跳过原因不同；详情见 `entity_results` |
| `missed_during_restart` | Home Assistant 停机期间错过，恢复后没有补发 |
| `failed` | 服务调用失败，且允许的重试已经用尽 |
| `partial_failure` | 多台设备中至少一台失败，其他设备执行结果见 `entity_results` |

对华氏设备，曲线中的摄氏温度会在执行时转换为华氏温度，再按照设备的 `min_temp`、`max_temp` 和 `target_temp_step` 进行裁剪与吸附。风量曲线使用设备在 `fan_modes` 中公布的原生值；多空调控制器只在前端提供共同支持的风速。每个节点会保存逐设备 `temperature_result`、`fan_result` 和聚合结果，方便区分某台设备的跳过、成功或失败状态。

## 重启、历史和设置

- 活动会话会持久化，Home Assistant 重启后继续安排未来节点。
- 重启期间错过的节点记录为 `missed_during_restart`。
- 如果重启时会话已经超过结束时间，则状态变为 `completed_after_restart`。
- 重启恢复不会补执行关机；只有 Home Assistant 持续运行并实际触发本次自然结束时才会关机。
- 非活动历史默认保留 30 天。

在“设置 → 设备与服务 → Climate Sleep Curve → 配置”中可调整：

- 历史保留天数：1～365 天。
- 默认重试次数：0 或 1。
- 默认重试间隔：1～300 秒。

默认值用于新建控制器；已保存控制器拥有自己的重试设置。

## 事件

集成会在 Home Assistant 事件总线上发布：

- `climate_sleep_curve_session_started`
- `climate_sleep_curve_point_processed`
- `climate_sleep_curve_session_stopped`
- `climate_sleep_curve_session_completed`

事件数据包含控制器 ID、会话 ID、`climate_entity_ids` 目标空调列表、自然结束关机快照及关机结果，以及用于旧客户端兼容的首个 `climate_entity_id`；节点事件还包含计划时间、处理时间、目标温度、目标风速、聚合结果、逐设备 `entity_results` 和尝试次数。可以在“开发者工具 → 事件”中监听，用于通知或调试。

## 故障排查

### 添加集成时搜索不到

- 确认目录名是 `custom_components/climate_sleep_curve`。
- 确认目录没有多嵌套一层。
- 重启 Home Assistant，然后检查日志中的 `climate_sleep_curve`。

### 控制器无法保存

- 确认所有实体 ID 都以 `climate.` 开头且当前存在。
- 确认每台设备都支持设置目标温度。
- 配置曲线和控制器的 WebSocket 写操作需要 Home Assistant 管理员权限。

### 曲线运行但温度没有变化

- 查看状态传感器的 `last_result` 和 `last_error`。
- 检查当时空调是否为 `off`、`unknown` 或 `unavailable`。
- 检查设备是否已经处于相同目标温度，从而得到 `no_change`。
- 检查 Home Assistant 日志中设备集成本身的服务调用错误。

### 多个页面同时编辑后保存失败

曲线和控制器使用修订号进行乐观并发控制。出现 `revision_conflict` 表示另一个页面已经保存了更新，请关闭编辑框、刷新状态后重新修改。

## 卸载

1. 先停止不再需要的活动会话。
2. 在“设置 → 设备与服务”中删除 Climate Sleep Curve 配置条目。
3. 删除 `/config/custom_components/climate_sleep_curve`。
4. 重启 Home Assistant。

删除或卸载集成不会关闭空调。卸载前建议备份 Home Assistant，以便保留 `.storage` 中的曲线和历史。

## 开发

```text
custom_components/climate_sleep_curve/
├── __init__.py         # 配置条目、服务注册和生命周期
├── manager.py          # 曲线、控制器、会话、持久化和调度
├── executor.py         # 安全调温和调风、换算、裁剪、幂等和重试
├── models.py           # 数据校验和会话快照
├── websocket_api.py    # 卡片管理 API 和状态订阅
├── sensor.py           # 状态实体
├── switch.py           # 会话开关实体
├── select.py           # 默认曲线选择实体
├── button.py           # 重新开始按钮实体
└── translations/       # 本地化
tests/                  # 后端测试
```

基础校验：

```bash
python3 -m compileall -q custom_components
```

完整测试需要包含 Home Assistant 和 `pytest-homeassistant-custom-component` 的测试环境：

```bash
pytest
```

贡献或自动化修改前请阅读 [AGENTS.md](AGENTS.md)。

## 版本边界

当前版本为 `0.5.0`。这一版本新增默认关闭、仅在曲线自然结束时生效的可选关机功能，并继续支持温度曲线、全程自动风和逐节点风量曲线。单个动作最多同时处理 4 台设备，更多目标会排队执行。集成不做连续插值，不创建虚拟 `climate` 实体，不会打开空调，也不会改变 HVAC 模式、摆风或湿度。

## 许可证

本项目采用 [MIT License](LICENSE)。
