# RoboDojo 官方入口对照协议

V05 验证 RoboDojo 官方入口的 Conda 与 Docker 配方。V06 用 RoboDojo 官方入口作为 OpenPlatform 的实测基线。首个范围为 `stack_bowls` 标准布局、`arx_x5`、joint 控制、seed 0、单环境。最新实跑进度见实施记录。[V00 调查记录](robodojo-discovery.md) · [V05 实施记录](../implementation-status.md#v05-robodojo-环境准备与验收)

## 两条运行路径

| 路径 | 执行入口 | 策略接入与原始产物 |
| --- | --- | --- |
| RoboDojo 官方 | 宿主机 Conda 用固定上游的 `bash scripts/robodojo.sh eval`。对齐 Docker 环境时，容器运行上游 `client`，宿主机运行原生 XPolicyLab policy server。两种配方均保留上游 `deploy.py`。 | 先用官方无权重 `demo_policy`，再用发布的 `Pi_0`。保存日志、`_result.json` 和原生视频。[原生运行步骤](robodojo-discovery.md#最小原生运行步骤待-gpu-验证) |
| OpenPlatform | V06 插件交付后运行 `openplatform eval "$OP_MODEL_DIR" --bench robodojo --preset smoke --output-dir "$OP_OUT"`。可先用 V05 的宿主机 Conda 配方接通，容器交付使用 V05 验收过的上游镜像或其薄派生镜像。 | `OP_MODEL_DIR` 指向各 policy 的本地提交配置。插件在原生环境创建时接入 bridge，保留 `EvalEnv.reset()`、`deploy.py`、动作执行和计分。保存原生 JSON、run manifest、日志、公共指标和诊断事件。 |

## V05 原生 Conda 与上游 Docker smoke

1. **宿主机 Conda 原生参考。** 在目标 Linux RTX 机器按 V00 步骤准备 `RoboDojo` 和 `demo-policy` 环境，获取固定资产，运行一个有效的 `stack_bowls` episode。检查退出状态、实际 `layout_id`、`_result.json` 和相机视频。归档本次产物后再启动容器 smoke。[原生运行步骤](robodojo-discovery.md#最小原生运行步骤待-gpu-验证)
2. **同版本上游 Docker。** 在同一固定 `f57e163` checkout 下用上游脚本构建并运行镜像。复用同一资产快照、生成后的机器人路径配置和宿主机无权重 policy 环境。`demo-policy` 来自 V00 步骤，Docker 和 NVIDIA Container Toolkit 由目标机器预先配置。脚本使用 Linux host 网络连接宿主机 policy，资产只读挂载，结果写回宿主机。正式配方沿用上游允许的镜像交付方式。[上游 Dockerfile](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/Dockerfile) · [上游 smoke 脚本](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/docker/smoke_docker.sh)

```bash
# 目标 Linux RTX 机器，工作目录为已准备好资产的 RoboDojo f57e163 checkout
ROBODOJO_IMAGE=robodojo:v00-f57e163 \
ROBODOJO_CONDA_ENV=demo-policy \
ROBODOJO_SMOKE_TASK=stack_bowls \
ROBODOJO_SMOKE_ACTION_TYPE=joint \
ROBODOJO_SMOKE_EVAL_NUM=1 \
ROBODOJO_SKIP_BUILD=0 \
bash docker/smoke_docker.sh run
```

容器 smoke 检查 GPU 可见、实际相机渲染、policy 调用成功、资产挂载和结果落盘。根据 `docker/smoke_logs/` 中本次状态记录的结果路径读取 `_result.json`，核对恰有一个有效 episode 和对应视频。正常任务失败可通过流程 smoke。归档镜像 ID、可获取的 digest、构建参数与日志、GPU／驱动、实际依赖、挂载和执行命令。确认本地镜像 ID 后，可用 `ROBODOJO_SKIP_BUILD=1` 复用镜像测暖启动，分别记录构建与运行耗时。

## 冻结双路配对条件

1. **匹配仿真环境。** 两路使用固定的 RoboDojo `f57e1633ffed59761bc8e568cfe7530c4b33381c`。可先在同一宿主机 Conda 环境配对，交付时以 V05 验收过的上游镜像为共享仿真基础。核对 bridge 引入后生效的仿真包、渲染配置和系统依赖，归档必要改动。版本未对齐标为 `version_mismatch`，关键依赖或渲染环境未对齐标为 `environment_mismatch`，结论限于配方表现。
2. **冻结输入和运行环境。** 记录 RoboDojo 与子模块完整 SHA、资产 revision 和布局文件摘要、OpenPlatform 代码版本、依赖锁或镜像标识、GPU／驱动／仿真版本、渲染与相机配置、GPU 分配、策略及推理环境。π0 另存实际 checkpoint 路径与摘要、归一化统计、模型配置、推理精度和随机设置。核对 planner、故障恢复、重试、chunk 消耗和终止的生效配置。
3. **按实际布局配对。** seed 0 选择布局组。每条路径记录请求和实际 `layout_id`、布局文件摘要、被跳过的布局与原因、有效 episode 数。出现替补布局时按实际布局配对，未配对项单列。比较键包含任务、seed、实际布局及重复编号。官方 Conda 与 Docker 的产物按环境分别保留。

Mac 可以完成配置、版本表、无权重策略与 I/O fixture、命令解析和离线结果对齐样例。真实仿真与渲染证据在目标 Linux RTX 机器采集。[硬件与环境](robodojo-discovery.md#参考-policy-与运行条件)

## V06 按三层证据执行

1. **CPU 契约检查。** 复用 V02 SDK 和 V04 runner。让原生 `deploy.py` 以假环境重放调用序列，在 OpenPlatform bridge 上检查双 `reset`、chunk 内多次 `update_obs`、真实模型边界的观测键和值、命名关节动作和顺序。用非零且各关节值不同的 fixture 揭示维度错位。验证 chunk 提前终止时只登记已执行动作。
2. **无权重双路 smoke。** 在共同配方中，各路用同一个官方 `demo_policy` 执行一个有效 episode。两路记录请求及实际执行的动作。V05 同条件且记录齐全的官方运行可复用。官方 demo 的全零动作适合检查跑通与产物完整性，关节顺序由 CPU fixture 或受控动作序列检查。保留正常任务失败、运行错误、布局跳过和中断的独立状态。
3. **同 checkpoint 的 π0 对照。** 官方入口运行原生 Pi_0，OpenPlatform adapter 委托给同一发布权重与模型代码。用固定观测或短观测序列先核对两路模型输入和 chunk，包括相机、语言、关节顺序、归一化与动作解包，再闭环运行。记录请求频率、reset、chunk 长度或截断。输入或策略无法对齐时标为 `policy_mismatch` 或 `unavailable`，并记录原因。

固定观测／固定动作的局部重放用来排查映射和运行时。闭环 π0 对照记录真实调用序列，允许随机推理和物理模拟出现波动。完成单 episode smoke 后，预先确定布局范围、重复次数和数值容差，再扩大到声明的任务子集。报告每次结果及覆盖，单个 episode 的成功或失败仅作为流程证据。

## 证据和判定

| 层级 | 保存内容 | 比较方法 |
| --- | --- | --- |
| 环境与输入 | 解析配置、版本、机器、实际布局、有效输入／资产／checkpoint 标识 | 先检查配对条件。每条路径保留原始输出和配置，额外生成统一的配对表。 |
| 调用与动作 | episode 内事件序号、观测更新和推理 ID、观测摘要、返回 chunk 和索引、仿真实际接收的控制动作、可用的物理步／视频帧对应 | 沿原始时间顺序定位首个差异，区分推理调用、控制步和物理步。动作跟踪点记录是在 `take_action` 输入还是裁剪后的控制目标。缺失字段标为 `unavailable`。 |
| 终止与计分 | 原生终止原因、成功标志、过程分、有效／跳过／出错／计划 episode 数 | 按实际布局逐 episode 比较，保留各路径原始分母及覆盖。原生 episode 分数与 aggregate 分数统一量纲后再对照。 |
| 工程成本 | 从准备到首个有效结果的人工步骤、代码改动、耗时、显存、磁盘和故障定位时间 | 分别记录首次准备和暖缓存。用于性能对比的运行尽量对齐采集开关，另测诊断采集的成本。 |

每一组分开记录 `execution_status` 和 `comparison_status`。可比较的组须有对齐的运行输入、实际布局与可用的原始记录。版本或策略不一致、缺失证据与未运行都可单独归档。相同分数可能来自不同轨迹，差异报告应附第一个可观测分歧及可能的混杂因素。找不到分歧时记录观察范围和数据缺口。

V06 的受控对照需要官方与 OpenPlatform 两路真实运行，以及可配对的模型边界和评测证据。CPU 契约检查和单路实跑分别记录为部分进度。V07 再扩展长轨迹、视频帧索引、故障注入和采集开销。
