# AGENTS.md

本文件适用于整个 `climate-sleep-curve` 后端仓库。任何开发者、代码评审者或自动化编码代理在修改代码前都应阅读并遵循这些约束。

## 项目定位

这是 Home Assistant 自定义集成，负责保存睡眠曲线、将曲线绑定到真实 `climate` 实体、调度离散温度节点，以及向独立前端卡片提供 WebSocket API。

本仓库只包含后端。前端位于独立的 `climate-sleep-curve-card` 仓库。涉及接口或行为的改动必须评估前端兼容性。

## 不可破坏的安全约束

以下规则高于普通功能需求，除非维护者明确批准改变产品安全模型：

1. 唯一允许调用的设备控制服务是 `climate.set_temperature` 和 `climate.set_fan_mode`。
2. 禁止调用 `climate.turn_on`、`climate.turn_off`、`homeassistant.turn_on`、`toggle` 或任何等效电源服务。
3. 调用 `climate.set_temperature` 时只能传递 `entity_id` 和 `temperature`；调用 `climate.set_fan_mode` 时只能传递 `entity_id` 和 `fan_mode`。两者均不得包含 `hvac_mode`。
4. 目标实体不存在或状态为 `off`、`unknown`、`unavailable` 时必须直接跳过，不得尝试唤醒设备。
5. 服务调用失败后的重试必须重新读取实体状态；如果设备已经关闭，应停止重试。
6. 停止、替换、删除控制器或卸载集成只取消调度，不得关闭设备或恢复先前温度。
7. Home Assistant 重启后默认不得补发已经错过的节点。
8. 活动会话必须使用启动时的曲线快照，曲线后续修改不得改变该会话。

涉及 `executor.py`、会话生命周期或服务注册的修改必须包含针对这些规则的测试。

## 架构与文件职责

- `__init__.py`：注册服务、配置条目生命周期和平台加载。
- `const.py`：域、版本、限制、默认设置和公共事件名。
- `models.py`：输入校验、标准化、推荐曲线和会话快照。
- `storage.py`：Home Assistant Store 持久化。
- `manager.py`：事务、曲线/控制器 CRUD、会话状态机、恢复和调度。
- `executor.py`：设备状态检查、单位转换、范围/步进处理、幂等与重试。
- `websocket_api.py`：已认证状态接口、管理员写接口和事件订阅。
- `entity.py`、`platform.py`：控制器实体公共逻辑和动态创建。
- `sensor.py`、`switch.py`、`select.py`、`button.py`：Home Assistant 实体平台。
- `diagnostics.py`：隐私脱敏诊断。
- `services.yaml`、`strings.json`、`translations/`：服务描述和本地化。
- `tests/`：模型、执行器和管理器测试。

不要在其他文件中复制 `manager` 或 `executor` 的业务规则。共用行为应放在明确的单一职责模块中。

## 数据模型约束

### 曲线

- 名称去除首尾空白后长度为 1～64。
- `duration_minutes` 为整数，范围 240～720。
- `interpolation` 当前只能为 `step`。
- 节点数量 2～25。
- 第一个 `offset_minutes` 必须为 0，之后严格递增且不超过时长。
- 温度必须是有限数值，内部统一为 5～40 °C。
- `fan_mode_control` 只能为 `none`、`auto` 或 `curve`；`curve` 模式下每个节点必须包含 1～64 个可打印字符的 `fan_mode`。

### 控制器

- `climate_entity_ids` 必须包含 1～32 个匹配 `climate.[a-z0-9_]+` 的不重复实体；保存时每个实体都必须存在并支持目标温度。`climate_entity_id` 仅作为首个实体的旧协议兼容别名。
- `profile_id` 必须引用已有曲线。
- 自动启动时间使用 `HH:MM:SS`。
- 星期为 0～6，其中 0 是周一。
- `catch_up_window_minutes` 为 0～15；`retry_count` 为 0～1；`retry_delay_seconds` 为 1～300。

### 修订号

更新和删除必须使用 `expected_revision` 实现乐观并发控制。不得通过忽略修订号来“解决”冲突；应向调用方返回 `revision_conflict`。

新增字段时必须考虑：默认值、旧存储迁移、校验、深复制、诊断脱敏、WebSocket 输出、前端兼容和测试。

## 会话和并发

- 每个控制器同一时间最多一个 `running` 会话。
- `replace=False` 时重复启动应返回 `session_already_running`。
- `replace=True` 和重新开始必须原子地结束旧会话并启动新快照。
- 使用控制器级 `asyncio.Lock` 串行化启动、停止、执行和删除等冲突操作。
- 在等待设备服务或重试期间，必须允许停止/替换请求使旧任务失效。
- 节点处理必须幂等，同一会话的同一偏移不得重复记录。
- 所有调度回调在会话结束、配置条目卸载或控制器删除时都应取消。
- 自动启动使用 Home Assistant 本地时区，并避免同一控制器同一天重复触发。

不要使用裸 `asyncio.create_task` 绕过 Home Assistant 生命周期；使用现有任务跟踪方式。

## Home Assistant 开发约定

- I/O 路径必须是异步的，不得阻塞事件循环。
- 使用 Home Assistant 提供的时间、单位转换、事件跟踪和 Store API。
- 不直接读写 `.storage` 文件。
- 配置条目运行数据保存在 `entry.runtime_data`。
- 配置条目卸载必须卸载所有平台并释放回调和任务。
- 新实体需要稳定 `unique_id`，并归属对应控制器设备。
- 用户可见字段新增或更名时，同步修改 `strings.json`、`translations/en.json` 和 `translations/zh-Hans.json`。
- 服务 schema 与 `services.yaml` 必须保持一致。
- 最低支持的 Home Assistant 版本变更时，同步修改 `hacs.json` 和 README。

## WebSocket API 规则

- 读取状态和会话动作要求已认证连接。
- 创建、修改、复制和删除曲线/控制器必须保留管理员权限校验。
- 不向客户端返回内部异常堆栈；已知校验错误使用稳定错误码，未知异常记录日志后返回通用错误。
- `get_state`、订阅事件、CRUD 返回值和错误码属于跨仓库协议。
- 修改命令名或字段时，必须同步更新卡片仓库、README 和测试；不兼容变更应明确升级版本。
- 订阅必须注册取消函数，连接关闭后不得遗留监听器。

## 隐私与诊断

- 诊断输出中的曲线名、控制器名和实体 ID必须脱敏。
- 日志只记录排错所需信息，不记录完整家庭配置、访问令牌或用户输入的大块数据。
- 错误字符串持久化或向前端返回前应限制长度。
- 新增任何可识别家庭环境的字段时，应同步更新 `diagnostics.py` 的脱敏逻辑。

## 测试要求

最低静态校验：

```bash
python3 -m compileall -q custom_components
```

在包含 Home Assistant 测试依赖的环境中运行：

```bash
pytest
```

改动应按风险增加测试，重点包括：

- `off`、`unknown`、`unavailable` 和实体缺失时无服务调用。
- 温度和风速服务数据不含 `hvac_mode`，且没有任何电源服务调用。
- 不支持的风速、相同风速、风速调用失败重试，以及重试期间关闭设备。
- 摄氏/华氏转换、最小/最大范围、步进和无效设备属性。
- 相同目标的 `no_change` 行为。
- 首次失败、重试、重试期间关闭和停止会话。
- 同时启动、停止、替换和删除造成的竞争条件。
- 重启恢复、错过节点和已结束会话。
- 曲线快照不受后续编辑影响。
- 修订冲突、无效输入和存储失败回滚。
- 自动启动的星期、本地时区和重复触发。

运行编译后清理并确认没有提交 `__pycache__` 或 `.pyc`。

## 修改工作流

1. 运行 `git status --short`，保护用户已有改动。
2. 阅读与变更相关的实现和测试，不根据 README 猜测行为。
3. 做最小且完整的实现，避免无关重构。
4. 更新测试、服务描述、翻译、README 和版本元数据中受影响的部分。
5. 运行与风险相称的校验。
6. 检查 `git diff --check` 和最终 diff，确认没有生成物或敏感信息。
7. 在本仓库内提交并推送；如果由总仓库引用，再更新总仓库的子模块指针。

未经明确请求，不要改写提交历史、强制推送、删除用户分支或修改远程地址。

## 提交与版本

- 提交应聚焦单一目的，消息使用简洁的祈使句。
- `const.py`、`manifest.json` 和 `pyproject.toml` 的版本必须一致。
- 发布时同步检查 `hacs.json`、README、翻译和前端兼容版本。
- 当前仓库根目录结构符合 HACS Integration 自定义仓库要求，不要把 `custom_components/climate_sleep_curve` 再嵌套一层。

## 代码评审清单

评审时按以下优先级检查：

1. 是否可能意外开关设备或改变 HVAC 模式。
2. 是否可能因重启、并发、失败回滚或删除而丢失/破坏数据。
3. 会话快照、调度和状态机是否保持一致。
4. 温度单位、范围、步进、时间和时区是否正确。
5. WebSocket 权限、输入校验、错误处理和诊断脱敏是否完整。
6. Home Assistant 生命周期和异步 API 是否使用正确。
7. 前端协议、用户文档、翻译和测试是否同步。

缺陷报告应包含可复现条件、影响范围、期望行为和建议测试。格式问题不应掩盖更高优先级的功能或安全问题。
