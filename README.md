# OpenPlatform

OpenPlatform 是面向具身研究的本地 benchmark 集成工具包。用户提供 model 与 adapter，在自己的机器上执行评测，得到本地指标、轨迹、视频和运行日志。

目标是让研究者把时间花在模型和实验上：工具包维护 benchmark 的依赖配方、评测入口与结果收集；用户维护模型加载和输入输出适配。统一的是实验执行方式，各 benchmark 的任务语义、控制方式和原生评分仍分别保留。

## 0.1.0 测试版

CPU fixture `dummy` 支持 `list`、`eval`、`run`、`status` 和 Python `ExperimentRunner`。RoboDojo Conda 已在干净环境通过单集 smoke 和 5 个有效 episode 的多布局验收。同一 π0 权重的双路对照已在 V06 完成。原生 25 集自然结束、外部复现和 GitHub 托管 CI 待验证。此测试版供私有仓库使用。[验收记录](docs/implementation-status.md) · [上传到 GitHub](docs/github-upload.md)

## RoboDojo 快速开始

从 [安装指南和支持矩阵](docs/integrations/robodojo-v08/README.md) 准备固定 checkout、资产、仿真环境和独立 SDK policy 环境。生成 `demo/submission.yaml` 后，使用已安装的 OpenPlatform CLI 执行。

```bash
cli="$OP/.venv/bin/openplatform"
"$cli" doctor --bench robodojo --recipe conda --checkout "$RD"
"$cli" prepare --bench robodojo --recipe conda --checkout "$RD"
"$cli" eval "$W/demo" --bench robodojo --preset smoke --output-dir "$W/runs/smoke"
"$cli" eval "$W/demo" --bench robodojo --preset full --output-dir "$W/runs/full"
```

`smoke` 请求一个有效 episode。`full` 使用 `stack_bowls` 的原生配额 25，当前集成范围为 ARX X5 joint、标准布局、seed 0、单环境。执行结果给出 `run_dir`，可用 `openplatform status` 查看原始结果和产物。外部复现待验证。

## 安装与配置检查

需要 Python 3.11+ 和 uv。验收使用 Python 3.11 与 uv 0.13.7。

```bash
uv sync --locked --python 3.11
uv run --locked openplatform --help
uv run --locked openplatform validate examples/submissions/eval-only.yaml
uv run --locked openplatform validate examples/submissions/weightless-policy.yaml
```

配置填写真实路径后，可加 `--check-paths` 检查本地输入。SDK 可在模型或 benchmark 环境中用 `python -m pip install ./sdk` 单独安装。进程入口和 client 用法见 [跨环境通信](docs/interfaces.md#跨环境通信)，双环境验收命令和结果见 [实施记录](docs/implementation-status.md)。

## CPU 开发 fixture

```bash
uv sync --locked --python 3.11
UV_PROJECT_ENVIRONMENT=examples/submissions/dummy/.venv \
  uv sync --locked --package openplatform-sdk --no-dev --no-editable --python 3.11
uv run --locked openplatform list
uv run --locked openplatform eval examples/submissions/dummy --bench dummy --preset smoke
uv run --locked openplatform run examples/submissions/dummy/submission.yaml
# 用上一条命令返回的 run_dir 替换路径
uv run --locked openplatform status ./runs/RUN_ID
```

fixture 的两个 episode 预期一成功、一失败，`success_rate` 为 `0.5`，评测正常完成。policy 在单独的 Python 环境和进程中运行，通过本机回环连接。结果保存到 `runs/`，包含配置快照、原始结果、指标和日志。Python 调用见 [核心接口](docs/interfaces.md#python-与-cli-入口)。

## 项目原则

1. **Eval 优先。** Base 或 checkpoint 都可以尝试评测；没有目标 benchmark 的 SFT、训练脚本或训练数据，不阻断 eval。
2. **用户只维护模型侧。** 必要输入是模型、adapter 及其可运行环境描述；评测选择可以来自 CLI 或 YAML。工具包不要求用户再编写 benchmark 启动和评分脚本。
3. **逐个集成。** 每次只新增一个 benchmark 插件，持续扩展覆盖范围，避免把每个 benchmark 的特殊分支写进通用 runner。
4. **原生协议优先。** 不把不同动作空间强行变成同一种语义，不擅自修改终止条件和指标计算方式。
5. **低分也是结果。** 正常完成的失败任务与运行错误分别记录；接口错误不能伪装成 0 分。
6. **实验可追溯。** 保存实际 checkpoint、adapter、依赖、benchmark、参数和 seed。不同 benchmark 默认不合成一个总分。
7. **Training 可选。** 训练记录帮助解释比较条件；可选训练执行后置，不自动赋予实验公平性。

## 研究定位

OpenPlatform 研究如何通过统一工具包复用各 benchmark 的原生控制循环，并用从 policy 调用到实际动作与评分的证据解释结果差异。每个 benchmark 以官方入口作为实测基线，首个实验用 RoboDojo 衡量协议保真、差异定位和接入成本。[研究问题](docs/mvp.md#研究问题与预期贡献) · [RoboDojo 官方入口对照协议](docs/integrations/robodojo-comparison.md)

## 开发文档

| 文档 | 内容 |
| --- | --- |
| [MVP 定义](docs/mvp.md) | 研究问题、边界、使用流程与验证方案 |
| [技术架构](docs/architecture.md) | 本地技术栈、模块职责、目录和运行生命周期 |
| [模型与实验配置规范](docs/submission.md) | 本地 model、adapter、环境、checkpoint 与可选训练 |
| [核心接口](docs/interfaces.md) | Python runner、adapter、插件、runtime 和产物协议 |
| [Benchmark 集成规范](docs/benchmark-integration.md) | 一次新增一个 benchmark 的流程和候选路线 |
| [开发任务](docs/development-tasks.md) | 任务、依赖、交付物和验收标准 |
| [Vibe coding 执行计划](docs/vibe-coding-plan.md) | 逐轮开发顺序、可复制任务提示、验收与交接 |

示例：[CPU 可运行 fixture](examples/submissions/dummy/submission.yaml)、[有权重 eval](examples/submissions/eval-only.yaml)、[无权重 policy](examples/submissions/weightless-policy.yaml)、[可选 training](examples/submissions/optional-training.yaml)、[插件清单](examples/benchmarks/benchmark.yaml)。

先完成第一个 benchmark 的本地 eval 闭环，再按同一插件规范逐个集成；本地报告和研究验证随后完善。Training 不阻塞 eval 版本发布。
