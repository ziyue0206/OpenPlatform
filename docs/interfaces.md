# 核心接口草案

V04 已将配置、SDK 和 NativeRuntime 接入顺序 `ExperimentRunner`，通过 CPU fixture 验证了 CLI 与 Python API。

## Python 与 CLI 入口

```python
from openplatform.runner import ExperimentRunner
from openplatform.schemas import ExperimentSpec

spec = ExperimentSpec.from_yaml("experiment.yaml")
runner = ExperimentRunner()
plan = runner.plan(spec)
result = runner.run(plan, output_dir="./runs")
print(result.status, result.run_dir)
```

`plan` 固定配置快照、插件与配方版本、I/O profile、有效 checkpoint、合并后的 adapter 配置和插件参数，无需启动进程。`run` 同步返回运行状态、结果目录和各 eval 的状态与指标，默认失败后继续。Python 可用 `continue_on_error=False` 停在首个运行错误，或设置传入的 `cancel_event` 请求取消。CLI 的 Ctrl-C 走同一取消路径。尚未启动的 eval 会记录 `started: false`。

`eval`／`run` 退出 `0` 表示评测完整完成，任务失败仍计入原生指标。退出 `1` 表示运行错误或部分完成，`2` 表示配置或选择有误，`130` 表示取消。`status` 仅读取状态，成功读取时返回 `0`。

| CLI | 行为 |
| --- | --- |
| `list` | 列出已安装插件、状态、recipe、suite 和 preset |
| `doctor --bench ID --checkout PATH [--recipe NAME]` | 检查本地 checkout、资产及所选配方的运行条件，缺项退出 1 |
| `prepare --bench ID --checkout PATH [--recipe NAME]` | 返回配方命令、解析路径及缓存检查结果 |
| `eval MODEL_DIR --bench ID ...` | 从模型声明和 CLI 生成单 eval |
| `run EXPERIMENT.yaml` | 按 YAML 顺序执行 eval |
| `status RUN_DIR` | 读取本地状态与已完成结果 |
| `report RUN_DIR` | 后续从本地文件生成静态报告 |
| `compare RUN_DIR...` | 后续按 benchmark／suite／protocol 对齐并导出对比 |

比较不同协议时明确标注不匹配，不把结果混入同一聚合分数。报告默认直接打开本地 HTML；浏览器文件访问有限制时可由用户自行启动本地静态文件服务，不需要业务 API。

## PolicyAdapter

```python
from typing import Any, Protocol
import numpy as np

Observation = dict[str, Any]
Action = dict[str, np.ndarray]
ActionChunk = list[Action]

class PolicyAdapter(Protocol):
    def __init__(self, config: dict[str, Any]): ...
    def reset(self) -> None: ...
    def update_obs(self, observation: Observation) -> None: ...
    def get_action(self) -> ActionChunk: ...
```

Policy 进程将 JSON 配置传给 adapter 构造器。调用方提供 `checkpoint_path`、`model_dir`、`adapter_dir`、`device`、`io_profile`、`evidence_dir` 和模型参数。无权重 policy 的 `checkpoint_path` 为 `None`。`adapter_dir` 指向 adapter 的绝对目录，`io_profile` 用于握手，`evidence_dir` 指向本次 attempt 的 `native/`。模型在 policy 进程内加载并串行调用，退出时调用可选的 `close()`。

一个进程只承载一个客户端会话和一个 active episode。`reset` 切换或重置 episode，同一 episode 的重复 `reset` 也逐次转发。首次 `get_action` 前需要成功的 `update_obs`。chunk 执行过程中可连续 `update_obs`，每帧按收到的顺序送给 adapter。调用方管理 chunk 的执行、截断和下一次取动作的时点。

公共观测外壳为 `instruction`、`vision`、`state` 和 `metadata`。图像传输为 profile 声明的相机字典，默认解码为 HWC RGB uint8；状态键、单位、相机命名、时间信息和动作语义由 profile 确定。metadata 包含 episode、step 和 profile 版本，不把评测答案／成功判定直接交给 policy。

Action 是命名数组字典，ActionChunk 是有序动作序列。例如某 profile 可用一个七维末端控制字段，另一个可用双臂关节和夹爪字段；这些不是通用等价表示。具体维数、单位、参考坐标、绝对／增量、夹爪约定、频率、chunk 执行长度和重规划策略必须写在插件 profile 中，并绑定原生协议。记录实际执行的动作，不能只保存模型输出 chunk 后声称它们全部执行过。

Adapter 负责观测和动作的模型侧转换。V02 检查观测是映射，动作是由有限值 ndarray 组成的非空 chunk，并校验数组的编码形状和长度。各 profile 的具体字段、维数、单位和频率由后续 benchmark 集成验证。

## 跨环境通信

在模型环境安装 `sdk/` 后，使用 `python -m openplatform_sdk.server --entrypoint adapter:Model --config adapter-config.json --port 0` 启动。服务监听 `127.0.0.1`，端口 `0` 由操作系统分配。adapter 初始化且端口就绪后输出 `OPENPLATFORM_POLICY_READY <ws-url>`。配置为 JSON 对象，必须含 `adapter_dir` 和 `io_profile`。首个成功握手的客户端拥有会话；客户端关闭或断连后进程清理并退出。

在另一环境安装 SDK，使用 asyncio client：

```python
from openplatform_sdk import PolicyClient


async def decide(url, observation):
    async with await PolicyClient.connect(url, io_profile="example-v1", timeout=30) as client:
        await client.reset("episode-0")
        await client.update_obs(observation, step=0)
        return await client.get_action(step=0)
```

协议 v1 使用二进制 WebSocket 和 MessagePack。`hello` 校验版本与 `io_profile`，响应返回版本、profile 和 adapter 入口。每个请求带 `request_id`，状态调用另外带 `episode_id` 与非负整数 `step`。这些字段属于传输层，adapter 收到的观测字典保持原样。ndarray 扩展记录 dtype（含字节序）、shape、字节数和 C 顺序的字节数据，支持布尔、整数和常规浮点类型。默认单条消息上限为 16 MiB。

调用错误通过 `PolicyError.code`、`message`、`op` 和 `request_id` 定位。adapter 异常或动作校验失败后需显式 `reset` 才能继续。客户端在 RPC 超时、取消或协议错误后关闭连接，调用只发送一次。超时无法中断已经在 adapter 内执行的同步代码，服务会在该调用返回后清理。强制结束失去响应的进程由 V03 的进程管理处理。RoboDojo 的 XPolicyLab 协议需要单独的 bridge。

## BenchmarkPlugin

```python
class BenchmarkPlugin(Protocol):
    def resolve_parameters(self, parameters: Options) -> Options: ...
    def build_eval(self, ctx: EvalContext, policy: PolicyEndpoint) -> ExecutionSpec: ...
    def collect(self, ctx: EvalContext, process: ExitResult) -> list[Metric]: ...
```

`Registry` 从安装包内的 `benchmarks/*/benchmark.yaml` 发现插件，支持从仓库外调用。manifest 声明配方、可选 suite／preset 和 I/O profile。轻量插件生成原生评测命令并校验结果，benchmark worker 在独立子进程中通过 SDK client 调用 policy。`dummy` 标记为 `development_fixture`，两个 episode 使用确定性位置评分。配置 `parameters.fault: environment` 可注入环境故障，`parameters.episode_delay` 可延迟第二个 episode。示例 adapter 支持 `adapter_config.fail_inference: true`。

`robodojo` 提供 `conda` 和 `docker` 两种评测配方。两种配方都通过原生 `EvalEnv` 驱动 `robodojo-arx-x5-joint` profile。bridge 从 `EvalEnv` 构造时接入，将原生 `reset`、`update_obs` 和 `get_action` 转发到 SDK。观测保留原生相机、语言、状态和上下文，数组保留 dtype 和 shape。原生 `deploy.py` 决定取动作和执行时机，原生 `EvalEnv` 执行关节控制、终止和计分。动作顺序为左臂 6、左夹爪 1、右臂 6、右夹爪 1，每个字段按原生命名传输。 [Docker 配方](integrations/robodojo-docker.md) · [V06 对照协议](integrations/robodojo-comparison.md)

## Runtime 与 ExecutionSpec

```python
class Runtime(Protocol):
    def prepare(self, spec: RuntimeSpec) -> PreparedRuntime: ...
    def launch(self, spec: ExecutionSpec) -> ProcessHandle: ...
    def wait(self, handle: ProcessHandle) -> ExitResult: ...
    def stop(self, handle: ProcessHandle) -> ExitResult: ...
```

V03 的 `openplatform.runtime.ExecutionSpec` 接受 `argv`、`cwd`、`env`、`timeout`、`stdout_path` 和 `stderr_path`。`NativeRuntime.prepare(RuntimeSpec)` 检查解释器可执行并保留符号链接，将返回的 `python` 放在 `argv` 首项即可使用该解释器。`launch` 直接执行 argv，叠加本次的环境变量，并以独占创建方式打开日志文件。启动失败会保留已打开的日志及 stderr 中的诊断。

`wait` 和 `stop` 返回同一个进程结果，包含 `exited`、`timed_out` 或 `canceled`、原始退出码、耗时和日志路径。超时从启动起计，由监控线程执行。超时或显式取消先发 `SIGTERM`，宽限期后向该组发 `SIGKILL`。父进程自然退出时，仍会清理留在组内的派生进程。清理使用本次启动的新进程组，并在回收组长前完成信号发送。调用方持有 handle 并负责 `wait` 或 `stop`，派生进程须留在原进程组。macOS 使用 `kqueue`，Linux 使用 `waitid`。[Python 进程等待接口](https://docs.python.org/3.11/library/os.html#os.waitid)

原生 runner 先等待 policy 输出就绪地址，`model.runtime.startup_timeout` 控制这一阶段的秒数，默认 15。RoboDojo 的两种配方均支持每个角色最多一个 GPU。runner 以 `CUDA_VISIBLE_DEVICES` 选择 policy 的设备，模型进程内使用 `cuda:0`。Conda 直接向原生脚本传宿主机仿真设备号，Docker 只向容器暴露选中的宿主机 GPU，容器内使用 `cuda:0`。Docker 插件在结果收集前核对本次容器已退出并恢复产物所有权。

## 本地结果契约

以下布局包含后续迭代的产物。

```text
runs/<run-id>/
├── run.json                       # schema、run ID、各 eval 状态文件引用
├── input_config.yaml
├── resolved_config.yaml           # 实际选择与版本
├── environment.json               # 主机、工具包、GPU、各运行环境
├── source_snapshot/               # 配置、adapter、必要源码；不默认复制权重
├── summary.json
├── artifacts.json                 # 相对路径、类型、大小、摘要
├── report/index.html
└── evals/<eval-id>/attempts/0001/
    ├── state.json
    ├── metrics.json                # 指标公共外壳与原生值
    ├── episodes.jsonl
    ├── logs/
    ├── native/                    # 原始结果文件
    ├── training/                  # 仅配置训练时存在
    └── episodes/<episode-id>/
        ├── steps.jsonl
        ├── policy_calls.jsonl
        ├── arrays/                # NPZ 分块等，JSON 只存引用
        └── videos/
```

`RunStore.create(root, input_config=..., resolved_config=..., eval_ids=...)` 持有 run 的写入锁并创建唯一目录，保存原始 YAML 字节和调用方提供的已解析配置。每个 eval 使用 attempt `0001`。调用方先 `start_eval`，再启动进程并将两路日志写到该 attempt 的 `logs/`，例如 `stdout.log` 和 `stderr.log`。`finish_eval` 在日志进入产物索引后原子更新终态。启动失败用 `fail_launch` 保存诊断和已存在的标准日志。此阶段的 `succeeded` 表示进程正常退出 `0`，评测完整性由后续插件校验。

`LocalArtifactStore` 提供 `put/open/list/stat`，可用 `register` 登记已写完的文件。索引包含相对路径、类型、字节数和 SHA-256。状态和索引用同目录临时文件替换，`put` 拒绝覆盖现有文件。`read_status(run_dir)` 即时汇总各 attempt，写入锁已释放且仍为 `pending` 或 `running` 的记录会显示为 `interrupted`，磁盘上的原始记录保留以供排查。

V04 的 runner 在插件确认结果完整后才将 eval 标为 `succeeded`，保存 `native/`、`metrics.json`、policy 与 benchmark 的两路日志。失败或取消时保留已写出的原始结果和日志，状态记录失败阶段或取消结果。

RoboDojo V07 在 `native/episode-index.json` 中登记原生计分分母、episode 状态和已验证的相机文件。`events.jsonl` 分开记录返回 chunk、确认执行的动作和原生视频写入计数。可用 `episode_id`、`policy_call_id` 和前后控制步关联观测、动作与视频。`collection.trace` 控制动作与帧索引，`collection.policy_calls` 控制模型调用与返回 chunk。`collection.videos` 独立控制原生视频，`collection.observations` 控制完整 RGB 数组，默认关闭。[V07 字段及复现步骤](integrations/robodojo-v07/README.md)

每个 episode 包含 task／instance／seed、completed／errored 等状态、success（原生提供时）、原生 score、步数、耗时及错误。每步记录仿真／控制时间、实际动作、可获得的 state、reward／done、原生 info 的允许字段、视频帧索引和 policy call 引用。Policy call 保存输入摘要或引用、原始动作 chunk、耗时与执行范围；完整图像按 observations 开关保存。没有图像时应标明不能只凭该轨迹完整重放模型输入。

轨迹记录不默认保证物理状态可精确 replay；能否重放由环境状态序列化和版本决定。视频不是状态轨迹的替代品。未提供的 state／reward 等字段标记 unavailable，不伪造占位值。

指标使用 `name/value/unit/direction/scope` 外壳，原生聚合与分母规则来自插件。比如原生定义为 10 次完整评测中成功 3 次，可以报告 ratio 0.3；运行错误存在时单列错误数、完成数和覆盖率，是否计入榜单分母严格遵循原生规则，并标注完整性，不悄悄丢弃出错项后报高分。工具包执行失败与任务未成功是不同状态。

## 版本契约

结果记录 schema、SDK 协议、插件／profile／recipe 版本、工具包和上游 commit、环境锁／镜像 digest、资产摘要、checkpoint 摘要、adapter 快照及 seed。开发中的 dirty 源码也需记录并快照。拿不到的信息保留 unknown，不把路径或模型名称当唯一版本。
