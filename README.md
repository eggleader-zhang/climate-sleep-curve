# Climate Sleep Curve / 空调睡眠曲线

一个 Home Assistant 自定义集成，为已有的 `climate` 实体执行睡眠温度曲线。可视化编辑界面由独立的 `climate-sleep-curve-card` 仓库提供。

最重要的安全保证：本集成只会在空调已经运行时调用 `climate.set_temperature`；不会调用开机、关机或切换服务，也不会传递 `hvac_mode`。空调关闭、不可用或未知时，节点会被记录并跳过。

## 功能

- 4～12 小时睡眠曲线与严格的数据校验
- 多曲线、多控制器与运行时曲线快照
- 手动启动、停止、重新开始和每日按星期自动启动
- 重启恢复未来节点，默认将错过节点标记为 `missed_during_restart` 而不补发
- 温度范围裁剪、步进吸附、摄氏/华氏转换、幂等设温和最多一次重试
- Switch、Select、Sensor、Button 实体
- 已认证 WebSocket 管理 API、乐观并发控制、脱敏诊断、中英文翻译

## 安装

将 `custom_components/climate_sleep_curve` 复制到 Home Assistant 配置目录下的 `custom_components/`，重启 Home Assistant，然后在“设置 → 设备与服务 → 添加集成”中添加 **Climate Sleep Curve**。

HACS 自定义仓库可直接指向本仓库，类别选择 Integration。

安装后端后，再安装独立的 **Climate Sleep Curve Card** Dashboard Plugin 来创建和编辑曲线。

## 服务

- `climate_sleep_curve.start`
- `climate_sleep_curve.stop`
- `climate_sleep_curve.apply_current_point`
- `climate_sleep_curve.reload`

`start` 可传 `replace: true` 替换活动会话。任何服务都不会改变空调电源或 HVAC 模式。

## 开发与校验

```bash
python3 -m compileall -q custom_components
```

完整测试需要 Home Assistant 官方 pytest 环境（`pytest-homeassistant-custom-component`）。测试位于 `tests/`。

## 项目结构

```text
custom_components/climate_sleep_curve/  # Home Assistant 后端
tests/                                  # 后端测试
```

## 当前版本边界

第一版执行离散节点，不做连续插值，不创建虚拟 `climate` 实体，也不控制风速、摆风、湿度或 HVAC 模式。
