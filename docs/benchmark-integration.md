# Benchmark 增量集成规范

## 集成边界

每次开发只新增一个 benchmark，沿用“用户提供模型与 adapter，在自己的机器执行”的边界。插件维护者提供仿真依赖配方、资产准备、原生评测包装、profile、指标解析和产物收集；用户不再自行拼装这些评测工程。

不承诺只写一份通用 Dockerfile 就能支持所有 simulator。每个插件声明经验证的平台、驱动／渲染要求、环境管理方式和本机资源需求。工具包帮用户隔离准备依赖，无法替代其 GPU 或安装硬件驱动。

插件从 `benchmarks/*/benchmark.yaml` 发现，未来可增加 Python entry points。核心 runner 不允许按 benchmark 名称写分支。增加或修订通用能力应通过独立契约变更说明，而不是偷偷塞入特例。

## 清单要求

- ID、插件版本、状态、上游仓库与固定 revision。
- 原生评测入口、配方版本、suite／preset、参数 schema 和默认值。
- I/O profile：相机／状态／动作形状与语义、单位、坐标、控制频率、reset、终止、chunk 执行策略。
- Runtime 配方、已验证平台、依赖锁、资产来源／摘要、许可与访问条件。
- GPU／CPU／内存／磁盘和渲染检查，以及模型与仿真共享 GPU 的已验证限制。
- 原生指标／聚合规则、episode 完整性判断、支持的轨迹字段与视频能力。
- 可选训练数据 binding；没有训练数据不影响 eval 插件成立。

设计样例见 [benchmark.yaml](../examples/benchmarks/benchmark.yaml)。其中占位 revision 和资源信息不是已验证配置，不得用于声明 ready。

## 一次集成的步骤

1. **固定原生基线。** 从上游获取固定版本，用其官方推荐模型／示例 policy 跑通原生流程，保存命令、硬件与原始结果；区分 smoke 与完整评测。
2. **准备独立环境。** 将已跑通的依赖写入 env／Docker 配方，验证新环境安装、暖缓存与失败诊断。大资产单独缓存，eval-only 不拉训练集。
3. **写 profile。** 明确观测与动作语义；逐项核对控制频率、归一化责任、夹爪和 chunk。提供最小模型侧 adapter 例子。
4. **封装原生入口。** SDK client 只替换 policy 调用边界，保留原生 reset、采样、任务设置、终止和评分；包装代码在 simulator 环境运行。
5. **接入采集。** 保存实际动作、可用状态、policy 调用、视频帧对应和日志；保证采集开关不改变控制协议，测量开销。
6. **解析原生结果。** 保存原始文件和公共结果外壳，区分任务失败与运行错误；测试不完整结果、取消和格式变化。
7. **本机验收与文档。** 从干净机器／环境按 README 完成 prepare、smoke 与指定完整评测，对照原生结果，记录资源和限制，再独立合入。

小型 fixture 可进入仓库用于 CPU CI；真实 GPU 验收由维护者或贡献者使用自有机器执行并记录证据，不要求项目运营一套 GPU 服务。没有真机证据的插件保留 integrating 状态。

## 插件状态

| 状态 | 含义 |
| --- | --- |
| planned | 只有计划，无可运行保证 |
| integrating | 正在开发，依赖／协议尚未全部验证 |
| smoke_passed | 指定平台上的小规模流程通过，尚不能宣称完整评测通过 |
| ready | 指定版本与硬件范围内完成原生对照及完整验收 |

Ready 绑定具体配方／平台，不代表所有模型有能力完成任务。上游升级新增配方版本，不能静默替换旧实验依赖。

## 候选路线

下表是集成路线，**全部尚未实现**；顺序可依据依赖可获得性、研究目标和可用硬件调整。每项单独开发／验收，不同时铺开全部环境。

| 编号 | 候选 benchmark | 初始范围 | 集成时优先确认 |
| --- | --- | --- | --- |
| B01 | RoboDojo | 先选一个机器人／控制 profile | 原生协议、模型 client、渲染、任务资产与动作采集 |
| B02 | LIBERO | 先 Spatial，再扩展其余 suite | 固定测试实例、初始状态、末端控制与参考 checkpoint |
| B03 | RoboTwin 2 | 一个可获得任务组 | 双臂动作、数据／资产版本与原生评测入口 |
| B04 | SIMPLER | 一个机器人／任务配置 | 实体差异、渲染、控制频率与原生聚合 |
| B05 | RoboCasa | 一个明确任务子集 | 场景资产、初始化与控制 wrapper |
| B06 | ManiSkill | 一个 manipulation suite | 环境版本、向量化限制与动作模式 |
| B07 | MetaWorld | 一个已固定版本的 suite | task／goal 设置、观测与终止条件 |
| B08 | CALVIN | 一个固定语言任务协议 | 长程序列、历史状态和成功判定 |
| B09 | RLBench | 一个任务子集 | CoppeliaSim、相机配置与 action mode |
| B10 | BEHAVIOR | 可获得的一组任务 | simulator／资产条件、任务规模和硬件预算 |
| B11 | RMBench | 先核实具体版本与公开入口 | 仓库／协议／许可可获得性，确认后确定范围 |

路线表不证明这些 benchmark 使用同一种控制语义，也不证明均有 training data。采用哪一个具体版本，在其集成 PR 中给出来源和实测支持矩阵。

## 每个插件的验收清单

- 用户只新增模型侧 adapter／配置，CLI 接管 benchmark 准备、启动、评分和收集。
- 干净环境可按文档复现；离线能力、下载体积和暖缓存行为有说明。
- 与原生入口对照 task 列表、seed／初始化、控制循环、终止、指标和分母。
- 输出包含原生结果、实际 checkpoint、profile、版本和资源记录。
- Smoke 不混充完整评测；缺失 episode、推理异常、simulator 崩溃和取消有明确结果。
- 采集能力按可得性声明；视频与动作轨迹可以对齐；不声称未验证的精确 replay。
- 新插件不要求用户修改通用 runner；新增抽象有至少真实用例支撑。

## 集成参考入口

以下链接用于开始集成时核实上游实现；本文没有对上游当前版本完成兼容性测试。

- [RoboDojo](https://robodojo-benchmark.com/)
- [LIBERO](https://github.com/Lifelong-Robot-Learning/LIBERO)
- [OpenPI 的 LIBERO 示例](https://github.com/Physical-Intelligence/openpi/tree/main/examples/libero)
- [XPolicyLab](https://github.com/XPolicyLab/XPolicyLab)
