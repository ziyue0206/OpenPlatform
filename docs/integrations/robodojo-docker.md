# RoboDojo Docker 安装与评测

OpenPlatform 在宿主机运行编排和模型进程。RoboDojo 评测使用本地构建的上游 NVIDIA 镜像，容器通过 Linux host 网络连接只监听 `127.0.0.1` 的模型进程。宿主机只需 Python 和模型依赖，镜像内部自带仿真 Conda 环境。

## 支持范围

| 项目 | 范围 |
| --- | --- |
| 宿主机 | Linux x86_64、NVIDIA RTX、Docker Engine、NVIDIA Container Toolkit、Git、Bash、FFmpeg（含 `ffprobe`） |
| 仿真 | 固定 RoboDojo 上游提交，Isaac Sim 5.1.0、CUDA 12.8 容器、单张仿真 GPU |
| 任务 | `stack_bowls`、`arx_x5`、joint、标准布局、seed 0、单环境 |
| 评测 | `smoke` 请求 1 集并已通过。`full` 使用上游配置的 25 集原生配额，本次验收在 5 个有效 episode 后主动停止，25 集自然结束与外部复现待验证。见 [实施记录](../implementation-status.md#011-robodojo-docker-发布验收)。 |
| 安全探头 | 设置 `collection.safety: true` 启用。见 [短程 Docker 验收](robodojo-safety/README.md#docker-短程验收)。 |
| 策略 | 示例使用无权重参考策略。自定义策略提供 SDK adapter 和独立 Python 解释器。π0 的既有 GPU 对照使用 Conda 配方。 |

建议在 Docker 存储和工作目录所在文件系统合计预留约 150 GiB 以上，用于约 41 GB 仿真资产、上游镜像、构建缓存、容器缓存卷和结果。两块盘分担时，分别检查 Docker 的 `DockerRootDir` 和工作目录。 [NVIDIA Container Toolkit 安装](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) · [Isaac Sim 5.1.0 许可](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/common/licenses-isaac-sim.html)

## 准备镜像和资产

安装 Docker Engine 和 NVIDIA Container Toolkit，确认当前用户可直接执行 `docker info`，并通过 `nvidia-smi` 查看 GPU。选择一个空工作目录 `W` 和可访问的 OpenPlatform 仓库地址 `OPENPLATFORM_REPO`。

```bash
export W="$HOME/openplatform-work"
export OP="$W/src/OpenPlatform" RD="$W/src/RoboDojo"
mkdir -p "$W/src"
git clone "$OPENPLATFORM_REPO" "$OP"
python3 -m venv "$W/tools"
"$W/tools/bin/python" -m pip install 'uv==0.13.7' 'huggingface_hub==1.3.7' 'click>=8.1,<9'
export PATH="$W/tools/bin:$PATH"

# 阅读并接受 NVIDIA Isaac Sim 许可后执行。
export OMNI_KIT_ACCEPT_EULA=YES
recipe="$OP/src/openplatform/benchmarks/robodojo/recipe.sh"
bash "$recipe" source "$RD"
bash "$recipe" assets "$RD"
bash "$recipe" docker-build "$RD"
```

`source` 校验 RoboDojo 和子模块的固定提交。`assets` 下载固定 revision，并生成包含本机绝对路径的机器人配置。移动资产或 checkout 后，在新位置重跑 `assets`。镜像标签为 `robodojo:v00-f57e163`。已有来自同一固定上游的可信本地镜像时，可复用该镜像。构建大文件下载受限时可按 [已校验 PyTorch wheel 的缓存构建流程](robodojo-v05/README.md#a-上游-docker) 构建同一上游镜像。

## 安装编排和参考策略

```bash
cd "$OP"
uv sync --locked --no-dev --no-editable --python 3.11
UV_PROJECT_ENVIRONMENT="$W/policy" \
  uv sync --locked --package openplatform-sdk --no-dev --no-editable --python 3.11
uv pip install --python "$W/policy/bin/python" -e "$RD/XPolicyLab"
"$OP/.venv/bin/python" docs/integrations/robodojo-v08/configure-demo.py \
  "$RD" "$W/policy/bin/python" "$W/demo" --recipe docker --gpu 0

cli="$OP/.venv/bin/openplatform"
"$cli" validate "$W/demo/submission.yaml" --check-paths
"$cli" doctor --bench robodojo --recipe docker --checkout "$RD"
"$cli" prepare --bench robodojo --recipe docker --checkout "$RD"
"$cli" eval "$W/demo" --bench robodojo --recipe docker --preset smoke \
  --output-dir "$W/runs/smoke"
"$cli" eval "$W/demo" --bench robodojo --recipe docker --preset full \
  --output-dir "$W/runs/full"
```

可以用 `openplatform status RUN_DIR` 读回终态和日志路径。原生 JSON、三路视频、调用与动作轨迹及 episode 索引在 `evals/robodojo/attempts/0001/native/`。评测中断时，已写盘的原生结果仍可在该目录查看。`full` 的默认超时为四小时，可用提交的 `evals[].parameters.timeout` 调整。

`resources.environment_gpu_ids` 选择宿主机 GPU。容器只获得该设备，原生仿真在容器内使用 `cuda:0`。资产以只读方式挂载，容器结果目录按 attempt 隔离。首次运行可能需要从 NVIDIA 扩展仓库获取依赖并编译 GPU 内核，后续运行可复用 `openplatform-robodojo-f57e163-cache` 和 `openplatform-robodojo-f57e163-ov` 两个 Docker 卷。 [Docker GPU 设备说明](https://docs.docker.com/engine/containers/gpu/) · [Docker 挂载说明](https://docs.docker.com/engine/containers/run/#bind-mounts)

## 使用自己的模型

从 [参考 adapter](../../examples/submissions/robodojo-demo/policy.py) 和生成的提交配置开始。保留 `recipe: docker`、绝对路径 `parameters.checkout` 和 RoboDojo profile，把模型、权重、adapter 和解释器路径换成自己的环境。模型环境需安装 `openplatform-sdk`；它可以使用宿主机的另一张 GPU。 [配置与 adapter 规范](../submission.md)
