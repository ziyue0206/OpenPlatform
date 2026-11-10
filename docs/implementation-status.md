# 实施记录

下文中的路径与版本为各轮实际验收环境。原始视频、日志和证据包与源码仓库分开管理。当前安装入口见 [RoboDojo Docker 安装指南](integrations/robodojo-docker.md)。

## V00 RoboDojo 调查

源码调查已验收。Linux RTX 原生 smoke 待验证，固定上游、命令和已核实的 I/O 见 [调查记录](integrations/robodojo-discovery.md)。

## V01 包与配置

2026-11-07，已验收。`openplatform` 与 `openplatform-sdk` 均为 `0.1.0`。当前工作目录没有 Git 元数据，本轮以这两份包定义、`uv.lock` 和下列验收记录标识交付。

实现了 `openplatform validate`、`ExperimentSpec.from_yaml()`、本地路径检查及结果类型。V00 的无权重 policy 用必填且可为 `null` 的 `model.checkpoint` 表达。逐 eval 省略 `checkpoint` 时继承，显式 `null` 时跳过权重。详细路径规则见 [配置规范](submission.md)。

本机验收使用 macOS x86_64、CPython `3.11.18`、uv `0.13.7`。uv、Python、项目环境和 wheel 均使用临时目录。表中的 `uv` 为同一 uv 二进制，`$wheel_dir` 为临时 wheel 目录。

| 实际检查命令 | 结果 |
| --- | --- |
| `uv sync --python 3.11 --default-index https://pypi.org/simple`，`uv sync --locked --offline --python 3.11` | 安装编排包并生成锁文件。锁定的依赖可用缓存再次同步。 |
| `uv run --locked --offline ruff check .`，`uv run --locked --offline ruff format --check .` | 检查通过。 |
| `uv run --locked --offline pytest -q` | 18 项通过。覆盖相对路径、checkpoint 继承和清除、文件和目录权重、无权重、解释器符号链接、结构与路径检查、非法配置和训练诊断。 |
| `uv build --offline --all-packages --wheel --out-dir "$wheel_dir"` | 两个包均产出 wheel。 |
| `uv venv --offline --python 3.11 <env>`，`uv pip install --offline --python <env>/bin/python <wheel>` | 两个 wheel 分别安装到干净环境。在仓库外可导入 SDK，且其环境中没有编排包。 |
| 已安装 wheel 的 `openplatform --help`，`openplatform validate <eval-only.yaml>`，`openplatform validate <weightless-policy.yaml>` | 在仓库目录之外运行，三条命令均退出 `0`。 |
| 已安装 wheel 的 `openplatform validate <optional-training.yaml>` | 退出 `2`，定位 `evals.0` 并报告 `training execution is unsupported`。 |
| 已安装 wheel 的 `openplatform validate <weightless-policy.yaml> --check-paths` | 退出 `1`，报告示例模型和 adapter 目录及解释器缺失。临时测试 fixture 补齐路径后检查通过。 |

[CPU CI](../.github/workflows/cpu.yml) 已配置。GitHub 托管执行待验证，本机已运行其锁定安装、Ruff、pytest、CLI 帮助和 SDK 独立安装对应检查。V01 的 `--check-paths` 只检查本地输入，benchmark 参数语义、模型加载和 GPU／仿真条件由后续迭代验证。V02 从独立 SDK 包继续实现四方法协议，并保留 V00 记录的原生调用顺序需求。

## V02 Adapter 与跨环境调用

2026-11-07，本机 CPU 验收通过。SDK 包 `0.1.0`，传输协议 `1`。新增四方法 adapter、独立进程入口、异步 client 和 ndarray codec。启动、调用、超时和退出语义见 [核心接口](interfaces.md#跨环境通信)。

验收使用 macOS x86_64、CPython `3.11.18` 和 uv `0.13.7`。两个 SDK 环境分别从锁文件做非 editable 安装，均只安装 SDK、NumPy `2.4.6`、MessagePack `1.2.4` 和 websockets `17.4`。验收脚本核对进程 `sys.prefix` 不同，并确认两边均无编排包、PyTorch 或 JAX。

```bash
uv sync --python 3.11 --default-index https://pypi.org/simple
uv run --locked --offline ruff check .
uv run --locked --offline ruff format --check .
uv run --locked --offline pytest -q

for role in client policy; do
  UV_PROJECT_ENVIRONMENT="/private/tmp/openplatform-v02-$role" \
    uv sync --offline --package openplatform-sdk --locked --no-dev --no-editable --python 3.11
done
/private/tmp/openplatform-v02-policy/bin/python -m openplatform_sdk.server --help
/private/tmp/openplatform-v02-client/bin/python tests/policy_acceptance.py \
  --policy-python /private/tmp/openplatform-v02-policy/bin/python
```

离线命令沿用依赖下载后的 uv 缓存。当前工作区沙箱拒绝绑定 loopback 端口，双进程命令获准后实测退出 `0`。

| 检查 | 实际结果 |
| --- | --- |
| Ruff、pytest、policy 入口 | Ruff 检查通过，`33 passed`（含 V01 的 18 项）。进程入口帮助退出 `0`。 |
| 状态与动作顺序 | 同一 episode 的两次 `reset` 均执行。第一次取动作后连续送入两帧，下一次取动作读到按序累积的 `123`。两个 chunk 的数组 dtype、字节序、shape、值和动作顺序均符合预期。 |
| 握手、消息和推理错误 | 拒绝错误版本／profile、第二个客户端、非法 MessagePack、超限帧及不匹配的响应 ID。模型异常和无效动作带错误码与请求上下文，显式 reset 后恢复。 |
| 超时和清理 | 慢推理只执行一次。客户端收到 `timeout` 后关闭连接，后续调用返回 `closed`。正常及超时的 policy 子进程都以 `0` 退出，关闭钩子均执行，两个监听端口均拒绝新的 TCP 连接。 |

双环境命令已加入 [CPU CI](../.github/workflows/cpu.yml)，托管执行待验证。V02 以 CPU toy policy 校验传输与调用顺序。具体 benchmark 的 profile 字段及 XPolicyLab bridge 随真实集成验证。已开始执行且始终不返回的 adapter 调用需要 V03 的进程管理来终止。

## V03 NativeRuntime 与本地 run 存储

2026-11-07，本机 CPU 验收通过。编排包 `0.1.0`，run 和产物索引 schema `1`。实现位于 `openplatform.runtime` 和 `openplatform.storage`，调用和状态语义见 [核心接口](interfaces.md#runtime-与-executionspec)。

验收使用 macOS x86_64、CPython `3.11.18`、uv `0.13.7`，复用已有的锁定项目环境。最终验收使用以下命令，pytest 的配置、日志和子进程均在临时目录中创建。

```bash
uv run --locked --offline ruff check .
uv run --locked --offline ruff format --check .
uv run --locked --offline pytest -q tests/test_runtime.py tests/test_storage.py
uv run --locked --offline pytest -q
```

| 检查 | 实际结果 |
| --- | --- |
| 开发检查 | Ruff 检查通过，V03 专项 `13 passed`，全量 `46 passed`。 |
| 正常和非零退出 | 验证退出码 `0`、`19`、`23`，解释器符号链接、带空格的 cwd、自定义 env 和 argv 字面量均按预期传给子进程。两路日志单独保留，非零退出记录为 `failed`。 |
| 超时和取消 | 超时在 `wait()` 前已触发，忽略 `SIGTERM` 的组长以 `-9` 退出。显式取消后，处理 `SIGTERM` 并以 `0` 退出的组长仍被归类为 `canceled`。两种情况均保留信号前后已写入的日志。 |
| 进程组范围 | 正常、非零、超时和取消四种场景中，忽略 `SIGTERM` 的同组派生进程均释放了持有的文件锁，组长已回收。另起的独立进程保持存活，直到测试自身关闭它。macOS 的 `EPERM` 兼容分支对仍有活跃成员的组保留真实权限错误。 |
| run 隔离、日志与原子性 | 重跑产生不同 ID，原始配置和同名产物不变。启动失败保留诊断，复用日志路径被拒绝。模拟状态替换失败后旧 JSON 完整，模拟索引写入失败后状态仍为 `running`，重试登记后进入终态。并发写入的索引条目全部保留。 |
| 中断识别 | 写入者用 `os._exit(17)` 退出后，`read_status()` 将未完成 attempt 显示为 `interrupted`，磁盘记录仍保留退出前的 `running` 状态。 |

macOS 的退出观察和组清理路径已实测，Linux 的 `waitid` 路径和 GitHub 托管 CI 待验证。V03 管理启动时创建的进程组。主动脱离该组的派生进程以及编排进程遭强制终止后的孤儿清理不在本轮验收范围内。run 状态仍可用于识别异常退出留下的未完成记录。

## V04 CPU 评测闭环

2026-11-07，本机 CPU 验收通过。编排包和 SDK 均为 `0.1.0`。安装包内的 `dummy` 清单标记为 `development_fixture`。顺序 runner 复用 SDK policy 进程、NativeRuntime 和本地 run 存储，提供 CLI 和 Python API。运行入口见 [核心接口](interfaces.md#python-与-cli-入口)。

验收使用 macOS x86_64、CPython `3.11.18`、uv `0.13.7`。编排包在 `/private/tmp/openplatform-v04-env` 做非 editable 安装，policy 使用 V02 的独立 SDK 环境。依赖来自本机缓存。默认沙箱拒绝 loopback 绑定，授权后在本机临时端口执行通过。

```bash
uv sync --offline --python 3.11
UV_PROJECT_ENVIRONMENT=/private/tmp/openplatform-v04-env \
  uv sync --locked --offline --no-editable --reinstall-package openplatform --python 3.11
UV_PROJECT_ENVIRONMENT=/private/tmp/openplatform-v04-env uv run --locked --offline --no-sync ruff check .
UV_PROJECT_ENVIRONMENT=/private/tmp/openplatform-v04-env uv run --locked --offline --no-sync ruff format --check .
OPENPLATFORM_TEST_POLICY_PYTHON=/private/tmp/openplatform-v02-policy/bin/python \
  /private/tmp/openplatform-v04-env/bin/python -m pytest -q
```

另在 `/private/tmp/openplatform-v04-demo.DBQ91o` 中复制原样示例，将 `model/.venv` 指向独立 policy 环境，从仓库外执行下列命令。`$run_dir` 取自 `run.json` 中的 `run_dir`。

```bash
cli=/private/tmp/openplatform-v04-env/bin/openplatform
"$cli" list > benchmarks.json
"$cli" eval ./model --bench dummy --preset smoke --output-dir results > eval.json
"$cli" run ./model/submission.yaml --output-dir results > run.json
"$cli" status "$run_dir" > status.json
```

| 检查 | 实际结果 |
| --- | --- |
| 开发检查和安装 | Ruff 检查通过，pytest 全量 `54 passed in 12.98s`，其中 V04 新增 8 项。安装包内的清单在仓库外可发现。核心和轻量插件导入时未加载 SDK、NumPy、PyTorch、JAX 或 Isaac Sim。 |
| CLI 与 Python 正常闭环 | `list`、`eval`、`run`、`status` 实际命令均退出 `0`。两个 episode 一成功、一失败，`success_rate=0.5`。CLI 和 Python API 均返回 `succeeded`。保存了原始及解析配置、native JSON、公共指标、相对产物索引和两组进程日志。 |
| 运行错误和继续 | 注入推理异常和环境异常后，两个 eval 分别报告 `inference`、`environment`，保留诊断和已完成的原始结果，公共指标留空。随后第三个 eval 照常完成，run 为 `partially_succeeded`，CLI 退出 `1`。无效插件参数在启动前被拒绝，退出 `2`。 |
| 取消与清理 | 在首个 episode 落盘后用 Python `Event` 及 CLI `SIGINT` 分别取消。当前 eval 标记 `canceled`，下一项记录 `started: false`，未启动子进程。CLI 退出 `130`，已写出的结果和日志保留，未完成的临时文件未进入索引。验收检查 policy 监听端口已关闭。 |

CPU CI 已接入 V04 测试和独立 policy 环境，GitHub 托管执行待验证。V04 验收范围为 CPU fixture。

## V05 RoboDojo 环境准备与验收

2026-11-09，**CPU 准备和 Linux RTX 验收均通过**。RoboDojo 清单为 `preparation_only`。新增 `doctor` 和 `prepare`，固定官方原生 Conda 和 Docker 配方。版本、执行命令及复用规则见 [V05 配方](integrations/robodojo-v05/README.md)。

当前验收范围是 RoboDojo 官方入口的 Conda 与 Docker smoke。vla-eval 配方、CPU 检查和当时的远端排程保留为历史记录。后续验证按 [官方入口对照协议](integrations/robodojo-comparison.md) 执行。

### CPU 检查

macOS x86_64，CPython 3.11.18，沿用 V01 临时项目环境。安装检查另使用 `/private/tmp/openplatform-v05-wheel-env`。

| 实际命令 | 结果 |
| --- | --- |
| `/private/tmp/openplatform-v01-env/bin/python -m pytest -q` | `59 passed in 12.76s`，含 V01–V04 回归和 5 项 V05 fixture。完整测试获准在本机 loopback 起进程。 |
| `/private/tmp/openplatform-v01-env/bin/python -m pytest -q tests/test_robodojo_preparation.py` | `5 passed`。覆盖带空格 checkout、双只读资产挂载、可写结果目录、缺失资源、仅存在与已验收的镜像缓存、实际布局和缺视频。 |
| `/private/tmp/openplatform-v01-env/bin/ruff check src tests`、同路径 `ruff format --check` | 通过。 |
| `bash -n src/openplatform/benchmarks/robodojo/recipe.sh`、`bash -n src/openplatform/benchmarks/robodojo/build-cached-torch.sh`、固定上游 `eval` 与 `client` 的 `--dry-run` | 退出 0，解析到 `stack_bowls`、`arx_x5`、joint、seed 0、1 个有效 episode。 |
| `uv build --offline --wheel --out-dir /private/tmp/openplatform-v05-wheels`、`uv pip install --offline --python /private/tmp/openplatform-v05-wheel-env/bin/python --reinstall --no-deps <wheel>` | wheel 包含清单、版本锁、诊断和 shell 配方。初次离线 sync 命中旧版同版本号缓存，重新安装刚构建的 wheel 后，从仓库外发现 RoboDojo。 |
| 安装版 `openplatform list`、`doctor --bench robodojo --recipe docker --checkout './missing checkout'`、`prepare --bench robodojo --recipe docker --checkout './missing checkout'` | 分别退出 0、1、0，缺失路径返回诊断，计划仍可查看。 |

### Linux RTX 检查

服务器工作目录为 `~/openplatform-v05`。Ubuntu 22.04.4 x86_64，RTX 4090 × 2，每卡 24,564 MiB，驱动 570.153.02。初始根盘可用 83 GiB。Vulkan 枚举到两张 NVIDIA 设备。启动前确认了 NVIDIA Isaac Sim 许可。实际命令和证据在 `tools/`、`logs/` 和 `evidence/`。

| 实际命令 | 结果及位置 |
| --- | --- |
| `bash tools/bootstrap.sh`，`git -C src/RoboDojo submodule status` | 固定 RoboDojo 和三个子模块全部匹配。记录在 `logs/source.log`。 |
| `bash tools/conda-alternate.sh` | micromamba 从 conda-forge 安装隔离的 Conda 25.3.1。原 Miniforge 发布包下载较慢，实际使用 conda-forge 环境。 |
| `sudo -n apt-get update`，`sudo -n apt-get install -y cmake build-essential ffmpeg libvulkan1 mesa-vulkan-drivers vulkan-tools python3-venv`，`vulkaninfo --summary` | 系统依赖安装通过，Vulkan 枚举通过。 |
| `sudo -n env USE_CN_MIRRORS=0 bash src/RoboDojo/docker/install_docker_nvidia.sh` | 首次因系统缺少 `/etc/apt/sources.list.d` 退出 1。补齐目录后退出 0，安装 Docker 29.9.3、NVIDIA Container Toolkit 1.20.1，daemon 注册 `nvidia` runtime。 |
| `bash tools/robodojo/recipe.sh assets "$PWD/src/RoboDojo"`，`bash tools/resume-assets.sh` | 首次 HF CLI 缺少 click，补装后重试。下载中遇到 Hugging Face 连接中断，复用已下载的文件从同一 revision 续传。固定快照共 15,365 个文件、约 41.27 GB，包含完整 `Assets/**`。 |
| `bash tools/run-setup.sh`，`bash tools/resume-native-install.sh` | 基础 Conda 环境完成。首次在上游升级 pip 时因 PyPI 读取超时退出 1。续装使用 90 秒超时和 6 次重试，从 `scripts/install.sh --from isaacsim` 继续。 |
| `python3 tools/torch-wheel-cache-direct.py`，`sha256sum -c`，`bash tools/resume-native-from-cache.sh` | 单连接下载 1.1 GB 的 CUDA wheel 偏慢。对官方同一文件分段下载，SHA-256 `c4bbc0b4be60319ba1cefc90be9557b317f0b3c261eeceb96ca6e0343eec56bf` 匹配索引。结束慢速下载进程，使用校验后的 wheel 续装。 |
| `curl -4 https://registry-1.docker.io/v2/`，`docker manifest inspect docker.io/docker/dockerfile:1` | 服务器直连超时。NVIDIA NGC 的 manifest 查询也遇到 TLS 或 token 请求超时。Mac 用 `crane pull --platform linux/amd64 nvidia/cuda@sha256:e869de8a68e95272e51c4c7071b1d39941aaab707f9bc051a99062a3791448b1 cuda-base.tar` 取得探测镜像，远端 `docker load` 后核对架构和层摘要一致。 |
| `wipefs -n /dev/vdb`、`mkfs.ext4 -F -L op-v05-data -m 1 /dev/vdb`、`mount`、`bash tools/migrate-docker-storage.sh` | 经用户授权核实新增 300 GiB 空盘后初始化并挂载到 `/mnt/openplatform-v05`。启用 UUID 持久挂载。Docker 和 containerd 已重启并切换到新盘，保留原目录和配置备份，已缓存 CUDA 探测镜像 ID 一致。证据在 `evidence/data-disk-mount.log`、`evidence/docker-storage-after.log`。 |
| `bash tools/validate-sequential-nvidia-icd.sh`，`bash tools/validate-after-native.sh` | 自动执行原生 smoke、Docker 构建和容器首跑／暖跑，阶段日志在 `logs/validation-phases.log`。新版计划确认后停止旧排程余下阶段。 |
| 原生预检与渲染重试 | 第一次预检使用 Conda 基础环境的 Python，缺少 PyYAML，退出 1。切换到 RoboDojo 环境后预检为 18 pass、1 warn、0 fail。两次渲染分别缺少 `libXt.so.6` 和 `libGLU.so.1`，补装上游 Dockerfile 的渲染库后 `ldd libneuray.so` 无缺项。两次未响应 TERM 的仿真进程分别回收，退出 137，保留各自日志。最后一次沿用单 NVIDIA Vulkan ICD。 |

### GPU smoke

| 配方 | 状态 | 证据 |
| --- | --- | --- |
| A · Conda 原生 | 通过 | `bash tools/robodojo/recipe.sh conda-smoke "$PWD/src/RoboDojo" "$PWD/evidence/conda-20261109T040533Z"`。上游退出 0，278 秒，`layout_id=0`，有效 episode 1，`success_rate=0.0`、`score=0.0`。三路 640×480 视频各 801 帧，抽检第 1 秒帧有非恒定像素。原始 JSON、视频、依赖与日志在同一证据目录。 |
| A · 上游 Docker 首跑 | 通过 | `ROBODOJO_SKIP_BUILD=1 bash tools/robodojo/recipe.sh docker-smoke "$PWD/src/RoboDojo" "$PWD/evidence/docker-first-20261109T043908Z"`。上游 GPU 检查、宿主机 policy 连接、eval 和结果验证均通过。退出 0，301 秒，`layout_id=0`，有效 episode 1，`success_rate=0.0`、`score=0.0`。三路 640×480 视频各 801 帧，抽检非恒定像素。 |
| A · 上游 Docker 暖跑 | 通过 | 同一镜像用 `ROBODOJO_SKIP_BUILD=1` 重跑。退出 0，290 秒，`layout_id=0`，有效 episode 1，成绩 0，三路 640×480 视频各 801 帧。证据在 `evidence/docker-warm-20261109T043908Z/`。 |

原始 Docker 冷构建在官方 PyTorch wheel 速率降至约 0.11 MB/s 时主动中断，退出 1。保留 `evidence/docker-cold-20261109T040533Z/`。用同一官方 wheel 经 SHA-256 校验后续建，构建退出 0、耗时 2817 秒，含镜像导出和解包。构建副本只替换该 wheel 的获取来源。命令、Dockerfile diff、镜像与构建日志在 `evidence/docker-build-cached-20261109T043908Z/`。301 秒和 290 秒为两次 smoke 的运行耗时。

镜像 `robodojo:v00-f57e163` 的实际 ID 和本地 RepoDigest 都是 `sha256:5fa3b3009a5cecabe555f7c53633c4c3e43a91171eb72f468916b6c46a5aafcc`。各容器证据目录保存 `mounts.txt`、`options.txt`、`image.json`、仿真及 policy 依赖清单。宿主机资产在容器内双只读挂载，结果从可写挂载成功读回。两份原生配方的 `stack_bowls_0.json` SHA-256 均为 `8ec7c160815f8955b5dd1af1a042673140cc2b6ac4b7536ee7fa9057e8a978f5`。

在远端用 `python3 tools/robodojo/verify.py <Docker 首跑证据> <checkout>` 写入镜像验收凭据，再调用与 CLI 相同的 `preparation.inspect("docker", checkout)`。实际报告 `ready: true`、`present_verified`，复用计划带 `ROBODOJO_SKIP_BUILD=1` 与精确镜像 ID。完整 JSON 见 `evidence/docker-first-20261109T043908Z/prepare.json`。

本地证据包 `runs/v05-robodojo-evidence.tar.gz` 用 `tar -tzf` 核对，包含 3 份原生结果、9 个视频、构建日志、依赖清单及镜像元数据。证据包 SHA-256 为 `25eb7de9e9b5ac9e4e995478832cd4eca5be704138e64377972e438fb13a2b76`。

### 历史准备

旧范围下固定 vla-eval `v0.7.0`（`6cc3e1bd9fc4bed26fb0a3aabd342c5fed11ca09`）。官方 `vla-eval test -c configs/benchmarks/robodojo/smoke_test.yaml --dry-run` 识别到 1 项。使用 `vla-eval serve -c demo_server.yaml --address 127.0.0.1:18764` 和固定版 `Connection` 的 CPU 检查，两次返回 `float32 (14,)` 零动作，非零分组向量确认了关节顺序。按更新后的计划，旧队列在暖跑通过后取消了刚开始的 vla-eval 基础镜像构建，GPU 对比未启动。停止记录在 `logs/validation-phases.log`。

## V06 RoboDojo 官方入口对照

2026-11-09，**CPU 契约与 Linux RTX Conda smoke 验收通过**。`robodojo` 插件在原生 `EvalEnv` 构造时连接 SDK policy 进程。两路沿用上游 reset、`deploy.py`、动作执行、终止和评分。实测范围为 `stack_bowls`、`arx_x5`、joint、seed 0、单环境和每路一个有效 episode。Docker 配方提供 V05 准备与诊断，OpenPlatform 容器评测待验证。[V06 执行步骤](integrations/robodojo-v06/README.md) · [对照协议](integrations/robodojo-comparison.md)

### CPU 契约

macOS x86_64、CPython 3.11.18。`OPENPLATFORM_TEST_ROBODOJO=/private/tmp/openplatform-v00-robodojo-f57e163 /private/tmp/openplatform-v01-env/bin/python -m pytest -q` 在本机临时回环端口运行，最终 `61 passed in 12.88s`。用原生 `deploy.py` 重放，模型边界收到两次 reset 和连续观测。14 维非零 fixture 动作按左臂、左夹爪、右臂、右夹爪交付，返回 6 步并实际执行 4 步。非法动作形状和映射键冲突被拒绝。

`ruff check src sdk tests docs/integrations/robodojo-v06`、同范围的 `ruff format --check`、各执行脚本的 `bash -n`、两个示例的 `openplatform validate` 均通过。`uv build --offline --all-packages --wheel` 产出 core 和 SDK 的 `0.1.0` wheel。Linux 实际使用的 wheel SHA-256 随证据归档。

### 配对条件

服务器复用 V05 的 RoboDojo `f57e1633ffed59761bc8e568cfe7530c4b33381c`、XPolicyLab `5d0be1499634d3176258c8f2e31f5fe816497b0a`、资产 `1127bda94f172d757386945af3eb3a6754d1a4e6`、Isaac Sim 5.1.0 和 NVIDIA 570.153.02。按 Conda `pip freeze`、完整上游与子模块提交、资产 marker、布局与 planner 文件摘要和 Vulkan 配置核对两路。实际布局 0 的 SHA-256 为 `8ec7c160815f8955b5dd1af1a042673140cc2b6ac4b7536ee7fa9057e8a978f5`。仿真使用 GPU 0，`demo_policy` 用 CPU，π0 两路使用 GPU 1 和同一独立推理环境。

π0 固定发布 revision `786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33` 的 `RoboDojo-sim-arx_x5-joint-0/60000`。原始整步下载随后停止，推理 `params`、`assets/arx_x5_sim` 和 tokenizer 已完整下载并取 SHA-256。上游 `deploy.yml` 中的 `checkpoint_num: 59999` 最终选中发布步 `60000`。两路使用 `pi0_base_aloha_full_sim_arx-x5_seed_0`、`arx_x5_sim` 归一化统计、JAX 0.5.3，参数加载 dtype `bfloat16`、初始采样 key 0，显存预分配比例 `0.3`。

### 实际运行

服务器在 `/mnt/openplatform-v05/work/v06`。命令中的脚本位于 `docs/integrations/robodojo-v06/`，各路径的原生命令保存在 `command.txt` 和 `launch.json`。固定观测重放的完整命令与每个 policy 子进程 argv 随日志归档。

| 实际命令 | 结果 |
| --- | --- |
| `bash docs/integrations/robodojo-v06/run-official.sh demo_policy official-demo-01` | 退出 `0`，212 秒。布局 0，1 个有效 episode，任务失败，原生分数 `0.0`，三路视频各 801 帧。 |
| `bash docs/integrations/robodojo-v06/run-openplatform.sh robodojo-demo op-demo-01` | 退出 `0`，220 秒。run 为 `succeeded`。布局 0，1 个有效 episode，任务失败，原生分数 `0.0`，三路视频各 801 帧。 |
| `bash run-replay.sh pi0-replay-01` | 退出 `1`。上游模型启动时导入 `pytest`，`uv sync --locked --no-dev` 未安装。按上游锁文件补装 `pytest==8.3.5`、`iniconfig==2.1.0`、`pluggy==1.6.0`。 |
| `bash run-replay.sh pi0-replay-02` | 两路模型完成推理，比较退出 `1`，`policy_mismatch`。编码与归一化输入摘要一致。默认 XLA 下两块 `float64 (50, 14)` 动作的最大绝对差分别为 `0.01752596` 和 `0.01597392`。第一块首个超差项为索引 `[0, 2]`，官方 `0.01915577`、OpenPlatform `0.01971777`。 |
| `XLA_FLAGS='--xla_gpu_autotune_level=0 --xla_gpu_exclude_nondeterministic_ops=true' bash run-replay.sh pi0-replay-03` | 退出 `0`，`paired`。两路编码与归一化输入摘要一致，两个 `float64 (50, 14)` chunk 逐项相同，最大绝对差 `0`，`rtol=atol=1e-5`。闭环沿用相同 XLA 设置。 |
| `bash docs/integrations/robodojo-v06/run-official.sh Pi_0 official-pi0-01` | 退出 `0`，263 秒。布局 0，1 个有效 episode，任务失败，episode 分 `0.15`，聚合分 `15.0`，三路视频各 801 帧。 |
| `bash docs/integrations/robodojo-v06/run-openplatform.sh robodojo-pi0 op-pi0-01` | 退出 `0`，252 秒。run 为 `succeeded`。布局 0，1 个有效 episode，任务失败，episode 分 `0.15`，聚合分 `15.0`，三路视频各 801 帧。 |
| `python3 docs/integrations/robodojo-v06/compare.py <official> <native> <layouts> <conditions>`，`openplatform status <run_dir>` | 无权重与 π0 两组均按 `(stack_bowls, 0, 0, 0)` 配对，两组实际覆盖均为 `1/1`，无跳过或运行错误。两个 OpenPlatform run 的终态、公共指标和产物引用可读。 |

无权重两路的 `execution_status` 均为 `succeeded`，`comparison_status` 为 `paired`。各有 2 次 reset、800 次观测更新、400 个返回 chunk 和 800 个实际执行动作，末尾均为 `step_limit`。400 个 chunk 和 800 个 `take_action` 输入完全相同。第一个可见差异在控制步 0 的 `cam_head/color` 摘要。该观察区分了相机输入差异与无权重策略的动作一致性。

π0 闭环两路也为 `succeeded`／`paired`。各有 2 次 reset、800 次观测更新、16 个 50 步 chunk 和 800 个实际执行动作，末尾均为 `step_limit`。首个可见差异在控制步 0 的 `cam_head/color` 摘要，此后同一步的首个 chunk 和 `take_action` 输入也已不同。首个执行动作的左臂第一个分量分别为 `0.0008500813` 和 `0.0014560486`。固定观测重放与闭环输入差异分开记录，单次配对无法确定相机差异的来源或估计成功率波动。

本轮的动作跟踪点是成功返回的原生 `take_action` 的输入。裁剪后目标、物理步与视频帧关联为 `unavailable`。GPU 采样核对仿真进程在 GPU 0、π0 进程在 GPU 1。退出后检查两种 policy 与仿真子进程已清理，两张 GPU 均为 1 MiB、0% 利用率。单 episode 耗时随原始记录保存。

本地证据包 `runs/v06-robodojo-evidence.tar.gz` 包含四份原生结果、12 个视频、四份 episode 轨迹、逐 episode 对照、三次 π0 重放、两份实际 wheel、命令、配置、依赖、checkpoint 摘要和退出日志。`gzip -t` 和 `tar -tzf` 验证通过，SHA-256 为 `f96d82137b40fd6decaef58d33ff8098a131ae3ab7d492c545e7a1af9a8bf805`。服务器原始证据保留在 `/mnt/openplatform-v05/work/v06/evidence/`。

## V07 RoboDojo 轨迹与故障验证

2026-11-09，**CPU 契约与 Linux RTX 轨迹、故障和开销对照验收通过**。使用与 V06 相同的 RoboDojo 提交、资产、Conda 仿真环境和 GPU 0。实测任务为 `stack_bowls`、`arx_x5` joint、seed 0、单环境。验收 adapter 将无权重零动作组成三步 chunk，以触发原生步数上限处的截断。两组开销实验使用同一 adapter 和原生视频设置。[执行步骤与字段说明](integrations/robodojo-v07/README.md)

### CPU 契约

`OPENPLATFORM_TEST_ROBODOJO=/private/tmp/openplatform-v00-robodojo-f57e163 /private/tmp/openplatform-v01-env/bin/python -m pytest -q` 首次得到 `63 passed in 14.12s`，修复故障退出后得到 `64 passed in 13.43s`。原生 `deploy.py` 通过真实 SDK 回环重放，采集关闭与开启均返回 6 步、实际执行 4 步。开启时核对 policy 调用 ID、控制步、物理步和前后帧，终止帧单列。用 NPZ 回读 opt-in RGB 数组，校验中断时忽略末尾未写完的 JSONL 行，保留完整行及原生分母。子进程测试确认 policy 错误写盘后跳过 Kit 的隐式退出路径。

`ruff check src sdk tests docs/integrations/robodojo-v0{6,7}`、`ruff format --check`、`bash -n docs/integrations/robodojo-v07/run-gpu.sh` 和 `uv build --offline --all-packages --wheel` 均通过。用 V06 存档的两路无权重结果重跑 `compare.py`，输出与原始对照逐字节一致。

### GPU 实跑

首次在 `/mnt/openplatform-v05/work/v07` 执行 `bash docs/integrations/robodojo-v07/run-gpu.sh`。两种采集模式完成，模型异常在第 6 步后记录。SDK 已关闭连接，但仿真在 Kit 隐式退出阶段阻塞，三个视频写入进程仍存活。注入脚本等待超时后发 SIGINT 并回收进程，脚本退出 1。修复后，独立仿真进程写完错误并关闭 SDK 后以 1 退出，NativeRuntime 回收该组内的 ffmpeg。原始错误、跟踪和退出日志随证据保留。

最终使用新工作目录 `/mnt/openplatform-v05/work/v07-accepted` 运行 `V07_WORK="$PWD" bash docs/integrations/robodojo-v07/run-gpu.sh`。脚本内分别执行两次 `openplatform run <submission.yaml> --output-dir <runs>`，用 `openplatform status <run_dir>` 读回状态。实际 argv、提交、依赖和 wheel SHA-256 保存在证据目录。

| 项目 | 最终实测结果 |
| --- | --- |
| 采集关闭／开启 | 两组布局均为 0，原生 `_result.json` 一致。各 1 个有效 episode，任务失败，分数 0。两组原生日志均显示 800 个控制步，模型日志均显示 2 次 reset、800 次观测更新和 267 次推理。各保留三路 640×480 视频，每路 801 帧。 |
| 逐步关联 | 开启组的 800 个动作逐条对应生成 chunk 的调用、执行前观测及前后帧。物理步计数从 842 到 8842，每个动作实际前进 10 步。最后一个三步 chunk 只执行索引 0 和 1。终止帧为每路索引 800。 |
| 模型异常 | 第三次 `get_action` 抛错，仿真退出 1，eval 为 `failed`，分类 `policy`。保留 6 个已执行动作和 SDK 错误详情。 |
| 仿真中断 | 写出 6 个动作后，按本次 run ID 和 policy URL 定位仿真 Python 并发送 SIGKILL。仿真退出 137，eval 为 `failed`，分类 `environment`。保留 6 个动作和原生 stderr。 |
| 取消 | 写出 6 个动作后向 CLI 发送 SIGINT，CLI 退出 130。当前 eval 为 `canceled`，下一项 `started: false`。仿真和 policy 都以 `-15` 退出。 |
| 计分与清理 | 三个故障项均无原生评分结果，分母标为 `unavailable`。之前完成的 episode 分母仍为 1，其 JSON 和视频保留。退出后无活跃的本轮仿真、policy 或 ffmpeg，两个 GPU 均为 1 MiB、0% 利用率。 |

### 采集开销与证据

最终同构建开关对照中，`NativeRuntime` 的仿真子进程耗时为关闭 211.89 秒、开启 210.82 秒，差 `-1.07` 秒。episode 从开始事件到原生结果写入为关闭 156.07 秒、开启 164.12 秒，差 `+8.04` 秒。轨迹从 811 字节增至 4,861,611 字节。两组均启用原生视频并关闭完整 RGB 数组保存。这是一组顺序实测，两个计时口径包含的启动阶段不同，耗时差仍包含单次运行抖动。

`python3 docs/integrations/robodojo-v07/check-evidence.py evidence > evidence/verification.json` 退出 0，核对全部帧、调用、物理步、尾部截断、故障分类、分母和取消。物理步内插后的控制目标及未封装的故障视频标为 `unavailable`。完整 RGB 数组的 opt-in 保存已在 CPU fixture 回读，GPU 开销对照使用默认关闭配置。

本地证据包 `runs/v07-robodojo-evidence.tar.gz` 已通过 `gzip -t` 和 `tar -tzf`。正式验收含两份原生结果、六个视频、五份逐 episode 轨迹及退出诊断。包内另有首轮阻塞证据、注入命令、wheel、依赖和复现脚本。SHA-256 为 `7484151ca0395af53713fa1f97866dbba0bdc7580ce365e3693e8ed8f5817ee0`。

## V08 RoboDojo 干净部署与完整评测

**独立部署、smoke 和调整后的 5 个有效 episode 验收通过。** 新增 Conda `full` preset。当前范围为 `stack_bowls` 标准布局、ARX X5 joint、seed 0、单环境。`full` 向上游传 `EVAL_NUM=native`，依据固定 `_task.yml` 的 `eval_nums` 请求 25 个有效 episode。`smoke` 请求 1 个。结果核验按实际布局与原生结果索引关联视频和轨迹，并保留原生分母。Docker 保留 V05 准备路径，25 个 episode 自然结束与外部复现均待验证。[安装指南和支持矩阵](integrations/robodojo-v08/README.md)

### 干净 checkout 与 CPU

当前项目工作目录未提供 Git 元数据或发布 URL。将工作区源码和锁文件建成验收提交 `74f6b5e07f5cd3b3bc08bfa189c69ad4ab0d6efb`，再从 Git bundle 分别克隆到本机与服务器，未使用原工作区作为运行目录。bundle SHA-256 `978477ad5a262ca6bda6fde33e1dafd7a36fd8b2c94818d8e0126fe17dbae6aa`。本机在干净 clone 执行 `uv sync --locked --offline --no-editable --python 3.11`，`ruff check .`、`ruff format --check .`、CLI 帮助和 `list` 均通过。执行 `OPENPLATFORM_TEST_ROBODOJO=/private/tmp/openplatform-v00-robodojo-f57e163 .venv/bin/python -m pytest -q` 得到 `65 passed in 15.19s`。CPU 检查未下载权重、资产或仿真软件。

### Linux 独立安装

服务器工作目录 `/mnt/openplatform-v05/work/v08`。复用 V05 已验收的 RTX 硬件、驱动、系统渲染库和 CUDA 12.8。用 `git clone --no-local --no-checkout` 和 `git submodule update --init --depth 1 --jobs 3` 从已验证的 Git 对象缓存建立独立 RoboDojo checkout，再执行 `bash recipe/recipe.sh source "$RD"` 核对所有固定提交。资产从已验收快照复制，执行上游 `python3 utils/update_embodiment_config_path.py` 重新生成新路径，核对 85 份布局及机器人配置的摘要。保留了原始固定 revision 和实际获取命令。联网首次拉取由安装指南说明，本轮实跑复用了上述缓存。

在新 `CONDA_ENVS_PATH` 下分别 `conda create -y --prefix <prefix> python=3.11 pip`。Open3D 首次下载降速，另取同一官方 wheel 并核对 SHA-256 `18ae25cbc9f8fcabc06bad71e00cca3cd0ff0a762ce0fbec093f2e2a4d05eddf`。续装的 pip 仍从索引取同一版本，最终完成。CUDA 分段预取有区间校验失败，未采用不完整文件。PyTorch 使用 V05 已核验的同一官方 `2.7.0+cu128` wheel 显式安装，之后执行 `bash scripts/install.sh --from isaacsim` 和 `bash recipe/recipe.sh policy "$RD"`，安装退出 0。实际命令和两次续装的日志均保留。

### 干净 shell 检查

`env -i` 下运行 `run-clean.sh`，从新项目 clone 用 `uv sync --locked --no-dev --no-editable --python "$sim"` 安装 core，`uv build --all-packages --wheel` 后分别在仿真和 policy 环境执行 `python -m pip install --no-deps <SDK wheel>`。三个 `sys.prefix` 独立，两个子进程环境可导入 SDK，只有编排环境可导入 core。独立 CPU SDK client 与 policy 环境运行 `tests/policy_acceptance.py`，重复 reset、观测更新、超时和错误恢复均通过。

首次清空环境变量后的 `PATH` 缺少 `/usr/sbin`，`doctor` 退出 1 并误报两个渲染库缺失。用显式系统路径续跑 `resume-clean.sh`，`doctor` 和 `prepare --recipe conda` 均返回 `ready: true`，负例 `doctor --checkout <missing>` 退出 1。`prepare --recipe docker` 返回 `ready: true`，V05 镜像在当前 checkout 标记为 `present_unverified`，本轮验收使用 Conda。

额外执行 `python -m pip check`。无权重 policy 环境通过，仿真环境报告上游安装配方留下的 `isaaclab`／`starlette`、`pytest`／`packaging`、`httpx2`／`idna` 三组版本约束差异。实际环境清单、检查输出及功能实跑证据均归档。

### GPU 验收

| 实际命令 | 实测结果 |
| --- | --- |
| `openplatform eval "$W/demo" --bench robodojo --preset smoke --output-dir "$W/runs/smoke"` | 退出 0，CLI 耗时 299 秒。布局 0，1 个有效 episode，任务失败，原生聚合分 0。保留 800 个动作和三路各 801 帧的视频。仿真握手使用新 Conda 环境内的 SDK。 |
| `openplatform eval "$W/demo" --bench robodojo --preset full --output-dir "$W/runs/full"` | 原生请求 25 个。运行中用户将验收范围调整为 5 个。`python3 stop-after-five.py` 观察第 5 个 `episode_result` 写盘后向本次 CLI 发送 SIGINT，CLI 耗时 851 秒，按预期退出 130，终态 `canceled`。原生结果保留布局 0–4 的 5 个有效 episode，第 6 集未启动。 |
| `openplatform status <run_dir>`、`env -u PYTHONPATH src/OpenPlatform/.venv/bin/python check-v08.py "$PWD"` | 核对两个 run 的真实状态和原生分母。5 集均达到 800 步，任务均失败，原生聚合分 0。共 4,000 个实际执行动作和 15 路 640×480 视频，每路 801 帧。取消后的文件独立解码复核，结果写入 `five-episodes-verified.json`。 |

5 集路径的 `native/` 产物约 30.0 MB，保留原生视频与轨迹，完整 RGB 观测数组关闭。退出后无活跃的本轮仿真、policy 或 ffmpeg 进程，两张 GPU 均为 1 MiB、0% 利用率。

本地证据包 `runs/v08-robodojo-evidence.tar.gz` 通过 `gzip -t`、`tar -tzf` 和内容检查，含两份原生结果、18 路视频、两份轨迹流、独立核验、安装与续装日志、实际 CLI 状态及项目 bundle。SHA-256 为 `a3d518638287b1164c98b73ab3f9c6ee0f5411d85964251e769a50df008472eb`。

## 0.1.1 RoboDojo Docker 发布验收

2026-11-10，在 Ubuntu 22.04.4 x86_64、RTX 4090、驱动 570.153.02 和 Docker Engine 29.9.3 上执行。复用 V05 已验收的 RoboDojo 镜像 `sha256:5fa3b3009a5cecabe555f7c53633c4c3e43a91171eb72f468916b6c46a5aafcc`，以及 V08 checkout 的固定资产。编排包升级为 `0.1.1`，SDK 保持 `0.1.0`。从独立目录安装源码包，用 uv 管理的 CPython 3.11.18 创建编排、SDK client 和参考策略三个隔离环境。Docker 成为默认评测配方，已有 Conda 提交继续沿用原配置。

| 检查 | 结果 |
| --- | --- |
| 锁定安装与 CPU | `uv sync --locked --no-editable`、`uv build --offline --all-packages --wheel`、Ruff、shell 语法、`validate --check-paths`、Docker `doctor` 和 `prepare` 均通过。Linux pytest 为 65 项通过、1 项 macOS 专用检查跳过。独立 SDK client 与 policy 的握手、状态顺序、错误恢复和超时检查通过。 |
| Docker smoke | 首轮集成构建在宿主机 GPU 1 完成布局 0，执行 800 步，生成 3 路 640×480、各 801 帧视频。原生 JSON 与 V05 同镜像官方 Docker smoke 逐字段相同。仿真进程耗时 434.5 秒，包含容器卷首次初始化和运行时扩展获取。CLI 状态为 `succeeded`。 |
| Docker 取消 | 发布候选版在宿主机 GPU 0 执行 6 条动作后收到 `SIGINT`，CLI 状态 `canceled`、退出码 130。动作记录和 episode 索引保留，容器已移除，归档文件归宿主机用户所有。 |
| Docker 五集验收 | 发布候选版在宿主机 GPU 1 运行 `--preset full`，原生请求 25 集。按验收范围在第 5 集结果写盘后发送 `SIGINT`，第 6 集未启动。CLI 耗时 937 秒，状态 `canceled`，退出码 130。布局 0–4 的 5 集均到达 800 步上限，任务成功率和原生分数均为 0。原生 JSON 与轨迹逐集一致，保留 4,000 条动作、15 路 640×480 视频，每路 801 帧。结果文件归宿主机用户所有，容器已移除，两个 GPU 均恢复空闲。 |

单集 smoke 原始文件位于服务器 `/mnt/openplatform-v05/work/v08-docker.cCfhdc/`。发布候选版的源码包摘要、wheel 摘要、终态和验收汇总 `five-verified.json` 保存在 `/mnt/openplatform-v05/work/openplatform-0.1.1-docker.KsJWEl/evidence/`。本轮复用了 V05 的本地镜像，Docker 冷构建、25 集自然结束和第三方复现待独立验证。安装步骤见 [Docker 发布指南](integrations/robodojo-docker.md)。
