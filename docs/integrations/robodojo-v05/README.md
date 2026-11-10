# V05 RoboDojo 配方

本页保留上游官方入口的 V05 验收流程。OpenPlatform 0.1.1 的容器评测入口见 [Docker 安装与评测](../robodojo-docker.md)。

本地用 `doctor` 检查路径、固定 revision、Conda 环境、GPU 和 Docker runtime。退出码 `1` 表示有缺项。`prepare` 返回命令数组、解析后的路径和缓存状态，供评审和执行。仅存在标签的镜像显示为 `present_unverified`，计划给出上游构建和已校验 wheel 的缓存构建命令。Docker smoke 通过后写入 checkout 下的 `.openplatform-docker-smoke.json`。凭据与当前镜像 ID、源码及资产 revision 匹配时，计划显示 `present_verified` 并给出带镜像 ID 检查的复用命令。资产 marker 记录下载完成的快照 revision，运行证据另存布局和生成配置的 SHA-256。

V05 依次验收 RoboDojo 官方入口的 Conda 与 Docker smoke。通过的官方产物用于 V06 与 OpenPlatform 配对。[官方入口对照协议](../robodojo-comparison.md)

```bash
openplatform list
openplatform doctor --bench robodojo --recipe conda --checkout "$RD"
openplatform prepare --bench robodojo --recipe docker --checkout "$RD"
```

## 版本表

| 输入 | 官方 Conda 与 Docker |
| --- | --- |
| RoboDojo | `f57e1633ffed59761bc8e568cfe7530c4b33381c` |
| XPolicyLab | `5d0be1499634d3176258c8f2e31f5fe816497b0a` |
| IsaacLab | `afca7b09d60d8beb9c1cb28b43066499940b969b` |
| CuRobo | `d17b54ce32cba095c0b000c4c58777075d11de0e` |
| 资产 | HF `RoboDojo-Benchmark/RoboDojo@1127bda94f172d757386945af3eb3a6754d1a4e6` 的 `Assets/**` |
| 任务、策略 | `stack_bowls`，`arx_x5`，joint，seed 0，单环境，有效 episode 1，官方 demo |
| Python、仿真 | Python 3.11，Isaac Sim 5.1.0，PyTorch 2.7.0 cu128，完整依赖以实跑环境清单为准 |

## A Conda 原生

在 Linux x86_64 RTX 上准备 Bash、Git、Python 3、Conda、CUDA 12.8 toolkit、ffmpeg、Vulkan 和 HF CLI。Ubuntu 22.04 上安装上游 Dockerfile 的系统渲染库，包括 `libxt6` 和 `libglu1-mesa`。Isaac Sim 的 MaterialX 和 MDL 分别需要 `libXt.so.6` 和 `libGLU.so.1`。将 Conda 环境和缓存放在本轮目录。运行人确认 NVIDIA Isaac Sim 许可后再设置 `OMNI_KIT_ACCEPT_EULA=YES`。[V00 安装说明](../robodojo-discovery.md#最小原生运行步骤待-gpu-验证)

```bash
RD="$HOME/openplatform-v05/src/RoboDojo"
EVIDENCE="$HOME/openplatform-v05/evidence"
RECIPE="$PWD/src/openplatform/benchmarks/robodojo/recipe.sh"
mkdir -p "$EVIDENCE"
export CONDA_ENVS_PATH="$HOME/openplatform-v05/envs"
export CONDA_PKGS_DIRS="$HOME/openplatform-v05/conda-pkgs"
export OMNI_KIT_ACCEPT_EULA=YES
export PIP_DEFAULT_TIMEOUT=90 HF_HUB_DOWNLOAD_TIMEOUT=90 HF_XET_CHUNK_CACHE_SIZE_BYTES=0
# 在具有此 NVIDIA ICD 文件的验收机上，仿真只加载该 Vulkan ICD。
export VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json
export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
bash "$RECIPE" source "$RD"
bash "$RECIPE" install "$RD"
bash "$RECIPE" assets "$RD"
bash "$RECIPE" conda-smoke "$RD" "$EVIDENCE/conda-01"
```

`source` 按 gitlink 初始化子模块。`install` 从上游安装器的 `isaacsim` 步骤继续，随后创建隔离的 `demo-policy` 环境。`assets` 在完整下载和机器人配置生成成功后写 marker。smoke 的证据目录必须新建，包含实际 argv、日志、上游退出码、Conda／pip 依赖清单、原生 JSON 和三个相机视频。完成时 `verified.json` 给出有效 episode 数和实际布局，布局组的摘要见 `input-sha256.txt`。

## A 上游 Docker

Conda smoke 验收后，在同一 checkout 使用 Docker、NVIDIA Container Toolkit 和原生 demo-policy 环境。两次 smoke 共用同一只读资产树，额外挂载生成配置所引用的宿主机路径。上游脚本用 host 网络连接本机 policy，结果写入 checkout 的 `eval_result`。镜像按 NVIDIA 授权条件在本地构建使用。[上游容器 smoke](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/docker/smoke_docker.sh)

```bash
ROBODOJO_CN_MIRRORS=0 ROBODOJO_SKIP_BUILD=0 \
  bash "$RECIPE" docker-smoke "$RD" "$EVIDENCE/docker-cold-01"

verified_id="$(docker image inspect --format '{{.Id}}' robodojo:v00-f57e163)"
test -s "$EVIDENCE/docker-cold-01/verified.json"
test "$verified_id" = "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["Id"])' \
  "$EVIDENCE/docker-cold-01/image.json")"
ROBODOJO_CN_MIRRORS=0 ROBODOJO_SKIP_BUILD=1 ROBODOJO_EXPECTED_IMAGE_ID="$verified_id" \
  bash "$RECIPE" docker-smoke "$RD" "$EVIDENCE/docker-warm-01"
```

冷、暖分别归档，阶段时间可从上游 `run.log` 和总耗时核对。以 `image.json` 的实际 ID 和可用的 RepoDigests 标识镜像。

本次服务器单连接下载 CUDA PyTorch wheel 较慢。可用 [缓存构建配方](../../../src/openplatform/benchmarks/robodojo/build-cached-torch.sh) 先构建镜像，再通过 `ROBODOJO_SKIP_BUILD=1` 执行同一上游 smoke。配方校验固定 Dockerfile 和官方 wheel 的 SHA-256，生成只替换该 wheel 下载来源的构建副本，归档实际差异。

## 历史 vla-eval 准备

早期调查固定了 vla-eval `v0.7.0`，保留发布配方和同版本配方的脚本、配置及已通过的 CPU 检查。它们作为历史研究材料归档，V05–V06 的验收以 RoboDojo 官方入口为基线。[历史配方脚本](vla-eval.sh) · [实施记录](../../implementation-status.md#v05-robodojo-环境准备与验收)
