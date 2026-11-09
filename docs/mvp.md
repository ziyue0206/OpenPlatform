# MVP：本地具身评测研究工具包

## 定位与边界

交付物是 GitHub 仓库及可安装的 Python 工具包。研究者在自己的工作站或实验室机器上运行，项目维护者提供集成代码、环境配方和文档，不提供 GPU、在线排队、权重上传或代跑服务。

核心承诺：对于**已集成并验证的 benchmark／运行配方**，用户提供 model 和 adapter，指定自己已有的模型环境或依赖配方，就能由工具包完成环境准备、调用原生评测、收集和展示结果。承诺不等于任意模型无需适配就能获得有效控制能力，也不等于所有硬件都能运行所有 simulator。

“只提供 model 和 adapter”指用户不需要再维护各 benchmark 的评测工程。模型代码、配置、归一化统计、依赖和 checkpoint 定位仍属于 model 的必要运行信息。模型是否经过 SFT 不影响入口资格。

## 研究问题与预期贡献

OpenPlatform 的研究问题是，能否在复用 benchmark 原生执行路径的同时，降低接入与复现成本，并让分数差异有可追查的过程证据。实测以各 benchmark 的官方入口为基线。`vla-eval` 提供跨 benchmark 的模型接入、隔离环境、统一评测和逐步记录，作为相关工作引用。[vla-eval 论文](https://arxiv.org/abs/2603.13966v3) · [vla-eval 仓库](https://github.com/allenai/vla-evaluation-harness)

| 拟验证的贡献 | 具体做法 | 可检验的证据 |
| --- | --- | --- |
| **保留原生控制语义的集成方法** | 插件负责固定并启动上游评测，通过 policy bridge 接入自定义模型。RoboDojo 保留原生 reset、控制循环、chunk 消耗、终止和评分。 | 同条件下对比官方入口与 OpenPlatform 的有效布局、调用顺序、执行动作和 episode 结果，定位并解释差异。 |
| **解释评测分歧的实验记录** | 按 episode 关联配置与实际布局、观测和推理调用、返回 chunk、实际控制动作、终止与原生分数。 | 选定相同分数但过程不同、提前终止、布局跳过和运行故障等案例，比较各路径的可观测字段、首个可观测差异和定位耗时。 |
| **通用工具包的工程取舍** | 延用 V01–V04 的本地 runner，用户提供 model、adapter 和配置。每次通过插件增加一个 benchmark。 | 与直接使用官方入口比较接入改动、准备步骤、冷热启动时间、推理／采集开销。第二个 benchmark 检验需要修改多少核心代码，两台受支持机器检验复现条件。 |

首个 RoboDojo 实验在相同模型、资产和仿真条件下比较官方入口与 OpenPlatform，检验 policy bridge 与结果采集对原生控制和评分的影响。收益、开销和适用范围以实测为准，单 benchmark 结果只支持该案例的结论。[官方入口对照协议](integrations/robodojo-comparison.md)

## 默认用户流程

1. 克隆仓库并安装轻量编排包；运行 `doctor` 检查本机平台、驱动、GPU、渲染和磁盘。
2. 用 `prepare` 创建所选 benchmark 的隔离环境、下载必要资产并缓存；训练集默认不下载。
3. 指向本地模型、checkpoint、adapter 和模型环境。用户已有环境可复用，不强制把模型迁入工具包的 Python 环境。
4. 先跑少量 episode 的 smoke，再明确选择完整评测套件。Smoke 结果标记为调试结果，不等同完整榜单分数。
5. 本地 runner 启动模型推理进程和 benchmark 进程，记录原生结果、轨迹、视频和日志。
6. 在本地报告比较同一 benchmark 的不同实验；将结果目录交给合作者，对方根据配置与版本记录复现。

一次多 benchmark 实验可以逐项指定 checkpoint。默认顺序执行，不需要常驻服务；长任务可由用户放在 tmux、nohup 或已有调度系统中运行。

## 第一版范围

| 必需 | 具体内容 |
| --- | --- |
| CLI 与 Python API | `list`、`doctor`、`prepare`、`eval`、`run`、`status`、`report`、`compare` |
| 模型接入 | 四方法 adapter、必要配置、用户环境／模型配方、独立推理进程 |
| Benchmark 插件 | 固定上游版本、资产、I/O profile、环境配方、原生评测包装与评分解析 |
| 本地 runner | 解析计划、顺序运行、取消、超时、退出码、分阶段日志和失败后产物收集 |
| 结果规范 | 原生指标、episode 表、实际 checkpoint、动作轨迹、视频与日志索引 |
| 本地展示 | 静态 HTML 报告和 CSV／JSON 导出；不需要 Web 应用服务 |
| 可复现记录 | 解析后的参数、源码版本、环境锁、资产摘要、seed 和硬件信息 |

先交付一个真实 benchmark 的 eval-only 闭环，再独立集成第二个验证抽象；候选路线见 [集成规范](benchmark-integration.md)。最终覆盖多个 benchmark，每次新增一个。

第一版不承担自动 SFT、自动寻找最佳 checkpoint、动作空间学习迁移、跨 benchmark 总榜或集群资源调度。已有训练背景可以记录；训练执行扩展后置。

## 分阶段验收

- **M0：契约与假环境闭环。** CPU fixture 验证配置、adapter 调用、runner 状态、取消和结果文件，无真实仿真分数声明。
- **M1：单 benchmark。** 在受支持的 GPU 机器上完成 RoboDojo 官方入口与 OpenPlatform 对照，从单 episode smoke 扩展到预先声明的任务范围。归档实际配方、覆盖、差异与不可比项。
- **M2：多 benchmark。** 第二个插件不引入核心按名称分支；同一模型目录可选择各 benchmark 的配置／checkpoint；不强求同一 adapter 配置解决全部动作语义。
- **M3：研究版本。** 扩展官方入口对照，测量诊断能力、接入与运行成本及跨机器复现，公开实验脚本和限制。

开发者没有 GPU 时，CPU 契约测试仍可推进，但真实插件不能标记 ready。研究验证和 GPU 验收使用项目参与者自有或另行获得的算力，不由本工具包供给。

## 关于公平性

报告同时呈现 checkpoint、已知训练数据／训练预算、评测协议和运行配置。未披露部分标记 unknown。允许 base、不同 SFT 方法和已训练 checkpoint，比较时由研究者控制变量；一次工具包运行本身不证明分数只来自模型架构，也不自动证明公平。
