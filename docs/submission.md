# 本地模型与实验配置规范

“提交”在本仓库中指向本地 CLI／Python runner 提供文件路径，不涉及上传或交给维护者代跑。必要内容是可加载的模型及 adapter；训练不是前置条件。

## 模型目录

```text
my-model/
├── submission.yaml           # 本地模型与 adapter 的声明
├── model/
│   ├── weights/base/
│   ├── weights/libero/       # 可选：针对某 benchmark 的权重
│   ├── config.json
│   ├── normalization.json   # 模型需要时提供
│   └── ...                  # 必要代码与 tokenizer 等
└── adapter/
    ├── adapter.py
    ├── requirements.txt     # 需要时提供，不强制重新安装已有环境
    └── ...
```

权重目录结构可以不同；以配置路径为准。模型作者负责模型侧的完整可运行信息，工具包负责选定 benchmark 的评测工程。

## 配置字段

| 字段 | 必需 | 语义 |
| --- | --- | --- |
| `schema_version` | 是 | 配置协议，首版为字符串 `"1"` |
| `name` | 是 | 实验名称，不作为权重唯一标识 |
| `model.source` | 是 | `kind: local` 与 `path`；后续可增加固定 revision 的远端模型来源 |
| `model.checkpoint` | 是 | 相对 `model.source.path` 的文件或目录；无权重 policy 填 YAML `null` |
| `model.runtime` | 是 | `kind: native`，`python` 指定已有解释器，可用 `startup_timeout` 设置等待 policy 就绪的秒数，默认 15 |
| `adapter.source` | 是 | `kind: local` 与 adapter 代码根目录 `path` |
| `adapter.entrypoint` | 是 | `module:Class`，仅由模型进程导入 |
| `adapter.config` | 否 | 模型侧配置；不替用户推断动作语义 |
| `evals[]` | 实验配置必需 | 非空列表，每项包含唯一 id、benchmark、recipe 和可选的 suite／preset、checkpoint 等；模型声明可省略整项 |
| `collection` | 否 | RoboDojo Conda 分别控制原生视频、动作与帧索引、policy 调用记录和完整 RGB 数组。前三项默认开启，`observations` 默认关闭 |
| `resources` | 结构校验时可选 | `policy_gpu_ids`、`environment_gpu_ids` 为非负整数列表；CPU profile 可用空列表 |
| `output.root` | 否 | 本地结果根目录；未指定时为命令工作目录下 `runs/` |
| `training_info` | 否 | 已知训练背景；缺失标记 unknown，不阻断评测 |

`openplatform validate <config.yaml>` 接受模型声明和带 `evals` 的实验配置。`eval <model-dir>` 读取 `submission.yaml`，采用该 benchmark 与 recipe 下唯一匹配的 eval 配置。可通过 CLI 指定 suite 或 preset；有多项匹配时用 `run <experiment.yaml>` 按 YAML 中 `evals` 的顺序执行。[CPU fixture](../examples/submissions/dummy/submission.yaml) · [RoboDojo 无权重](../examples/submissions/robodojo-demo/submission.yaml) · [RoboDojo π0](../examples/submissions/robodojo-pi0/submission.yaml)

`model.source.path`、`adapter.source.path`、`model.runtime.python` 和显式 `output.root` 可用绝对路径，或者相对 YAML 所在目录的路径。省略 `output.root` 时使用命令工作目录下的 `runs/`。CLI 的配置文件路径相对当前工作目录。解释器路径保留符号链接，以便使用指定的虚拟环境。`adapter.config` 内部字符串按原样交给 adapter，adapter 可结合注入的 `model_dir`／`adapter_dir` 解析自己的参数。

## 运行环境

```yaml
model:
  source: {kind: local, path: ./model}
  checkpoint: weights/base
  runtime:
    kind: native
    python: ./.venv-policy/bin/python
adapter:
  source: {kind: local, path: ./adapter}
  entrypoint: adapter:Model
  config: {}
```

Native 模式复用用户指定的环境。V04 在该解释器中启动 SDK policy 进程并加载 adapter；结构和路径检查仍可单独运行。recipe 和 Docker 模型环境属于后续配方。

大权重默认原地只读引用，容器模式只读挂载。配置和模型代码快照进入结果目录；权重摘要可缓存，但缓存必须识别内容变化。凭证不写入实验 YAML 或报告。

## Adapter 责任

Adapter 实现初始化、`reset`、`update_obs`、`get_action`，负责模型加载、图像／状态预处理、prompt、归一化、推理、动作反归一化和输出形状。详见 [核心接口](interfaces.md)。工具包注入实际 checkpoint、设备、目录和 I/O profile。

不同 benchmark 可使用同一 adapter 类搭配不同 `adapter_config`，也可以用类内显式分派调用不同的模型侧转换模块。平台不自动补全缺失传感器，不从动作形状推断其物理含义，不通过重新训练修复能力差异。

## Eval 选择与 checkpoint

每项 eval 声明 `id`、`benchmark` 与 `recipe`。`id` 在当前 YAML 内唯一，以字母或数字开头，可包含 ASCII 字母、数字、下划线、短横线和点。`suite` 和 `preset` 互斥；两者均省略时由配方选择默认 suite。`preset: smoke` 标记小范围检查。配方绑定 I/O profile。

`model.checkpoint` 必须显式声明。字符串路径相对 `model.source.path`，可指文件或目录。无权重 policy 使用 `checkpoint: null`，例如 V00 的 `demo_policy`。`evals[].checkpoint` 省略时继承 `model.checkpoint`，相对路径覆盖时仍以 `model.source.path` 为基准，显式 `null` 则让该 eval 以无权重方式执行。未来执行时，adapter 的 `checkpoint_path` 对应实际路径或 `None`。

`adapter_config` 覆盖公共 `adapter.config` 的同名顶层键，嵌套对象整项替换。结构校验要求这些字段和 `parameters` 为字符串键、JSON 兼容值的映射。生成执行计划时，插件检查参数名和取值范围。

`resources` 提供公共设备选择，`evals[].resources` 可按角色覆盖。支持的 GPU 数由配方声明。RoboDojo Conda 每个角色最多选一张卡，两个提交示例分别声明了 CPU policy 和单 GPU policy。`evals[].training_info` 可覆盖公共训练背景，报告保留每个 eval 的实际声明。

## 可选训练与 fairness 记录

`training_info.regime` 可取 `unknown`、`no_target_finetuning`、`target_finetuning`、`from_scratch`；同时可记录 `datasets`、`steps`、`seed`、`data_source` 和 `notes`。工具包保存声明，不能据此核验模型全部训练历史。

训练执行是后续扩展。V01 发现 `evals[].training` 时返回 `training execution is unsupported`，可用 `training_info` 保留训练背景。[可选训练示例](../examples/submissions/optional-training.yaml) 用于说明后续字段，也可用来复现此诊断。

训练入口使用 argv 列表，只替换声明过的路径 token：`{python}`、`{model_dir}`、`{adapter_dir}`、`{checkpoint_dir}`、`{data_dir}`、`{output_dir}`。按 runtime 转换路径，不经 shell 展开。环境变量 `OPENPLATFORM_BASE_CHECKPOINT`、`OPENPLATFORM_DATA_DIR`、`OPENPLATFORM_OUTPUT_DIR` 提供同样信息。

训练数据可由用户本地提供；`source: benchmark` 仅在插件明确声明相应数据 binding 时可用，否则准备阶段报错。`output_checkpoint` 相对本次 training 输出目录解析。训练输出不得覆盖输入权重，报告单独记录训练配置、数据版本和新 checkpoint。

## 运行前检查与运行错误

```bash
uv run --locked openplatform validate examples/submissions/eval-only.yaml
uv run --locked openplatform validate examples/submissions/weightless-policy.yaml
uv run --locked openplatform validate /path/to/submission.yaml --check-paths
```

默认结构校验读取 YAML 并解析路径，检查必填字段、类型、重复键、未知字段和字段约束。`--check-paths` 在结构通过后检查模型与 adapter 目录、可执行解释器以及每项 eval 实际使用的非空 checkpoint；模型声明检查默认 checkpoint。这一步只检查本地输入路径。入口导入、依赖、GPU、渲染、配方和资产属于后续运行检查。退出码 `0` 表示所选检查通过，`1` 表示本地路径检查失败，`2` 表示配置无法读取、结构无效或 CLI 用法错误。

仓库中的 [有权重示例](../examples/submissions/eval-only.yaml) 和 [无权重示例](../examples/submissions/weightless-policy.yaml) 可直接做结构校验。本地路径检查需要用户提供示例声明的目录与解释器。
