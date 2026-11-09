# RoboDojo 安装与评测

## 支持范围

| 环境或入口 | 支持范围与证据 |
| --- | --- |
| Linux x86_64 RTX · OpenPlatform Conda | `stack_bowls` 标准布局、`arx_x5`、joint、seed 0、单环境。`smoke` 请求 1 个有效 episode，`full` 使用上游任务配置的原生配额 25。本轮验收按更新后的范围核对前 5 个有效 episode，完整 25 个的自然结束待验证。 |
| 无权重参考策略 | `checkpoint: null`，CPU policy 和一张仿真 GPU。以下新用户流程使用此策略。 |
| 发布 π0 | V06 在同一固定上游上验证了 `60000` checkpoint 的 Conda smoke。仿真和推理分别用 GPU 0、1。 |
| RoboDojo 上游 Docker | V05 已验收上游容器 smoke。OpenPlatform 的 `docker` recipe 提供 `doctor`／`prepare`。 |
| macOS／无 GPU 主机 | 安装、配置和 CPU fixture。真实 RoboDojo 仿真使用 Linux RTX。 |
| 其他 RoboDojo 任务、机器人、控制和批量环境 | 待验证。 |
| 外部复现 | 待验证。 |

固定 RoboDojo `f57e1633ffed59761bc8e568cfe7530c4b33381c` 和 Git 子模块，资产使用 `1127bda94f172d757386945af3eb3a6754d1a4e6` 的 `Assets/**`。验收服务器为 Ubuntu 22.04.4、RTX 4090 24 GiB、驱动 570.153.02、CUDA toolkit 12.8。Isaac Sim 为 5.1.0，仿真 Python 为 3.11。显存、存储和驱动的其他组合参考 NVIDIA 版本说明。为固定资产、独立环境、安装临时文件和运行结果预留约 100 GiB 可用磁盘。[固定版本表](../robodojo-v05/README.md#版本表) · [Isaac Sim 5.1.0 规格](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)

## 从 checkout 到环境

准备 NVIDIA 驱动及 CUDA toolkit 12.8。Ubuntu 22.04 安装 Bash、Git、Python 3 和系统库。Conda 可用 Miniforge 安装，将其 `bin` 加入 `PATH`。以下 `OPENPLATFORM_REPO` 指向本项目仓库 URL 或本地 Git bundle。选择一个空工作目录 `W`，将工具、Conda 环境、checkout 和结果分开存放。[Miniforge 安装](https://github.com/conda-forge/miniforge#install) · [Isaac Sim 5.1.0 Python 安装](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_python.html)

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates curl cmake build-essential ffmpeg python3-venv \
  libvulkan1 vulkan-tools libxt6 libglu1-mesa libegl1 libgl1
export W="$HOME/openplatform-work"
export OP="$W/src/OpenPlatform" RD="$W/src/RoboDojo"
mkdir -p "$W/src" "$W/tmp"
git clone "$OPENPLATFORM_REPO" "$OP"

python3 -m venv "$W/tools"
"$W/tools/bin/python" -m pip install 'uv==0.13.7' 'huggingface_hub==1.3.7' 'click>=8.1,<9'
export PATH="$W/tools/bin:$PATH:/usr/sbin:/sbin"
export CONDA_ENVS_PATH="$W/envs" CONDA_PKGS_DIRS="$W/conda-pkgs"
export UV_CACHE_DIR="$W/uv-cache" TMPDIR="$W/tmp"
export CUDA_HOME=/usr/local/cuda-12.8
export PIP_DEFAULT_TIMEOUT=90 PIP_RETRIES=6 HF_HUB_DOWNLOAD_TIMEOUT=120
export HF_XET_CHUNK_CACHE_SIZE_BYTES=0
export VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json
export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
nvidia-smi
vulkaninfo --summary

# 阅读并接受 Isaac Sim 许可后执行。
export OMNI_KIT_ACCEPT_EULA=YES
recipe="$OP/src/openplatform/benchmarks/robodojo/recipe.sh"
bash "$recipe" source "$RD"
conda create -y --prefix "$CONDA_ENVS_PATH/RoboDojo" python=3.11 pip
conda create -y --prefix "$CONDA_ENVS_PATH/demo-policy" python=3.11 pip
bash "$recipe" install "$RD"
bash "$recipe" assets "$RD"
```

`source` 固定 checkout 与子模块提交。`install` 使用上游 Isaac Sim、PyTorch cu128、IsaacLab 和 CuRobo 配方，并独立安装无权重 policy。`assets` 使用 `hf download` 下载固定 revision，生成当前 checkout 的机器人资产路径并写入 revision marker。下载中断时可在同一目录重跑，HF 客户端复用已下载文件。同一固定版本已有可信的本地缓存时，可以复制资产树到新的 checkout，重新运行 `python3 utils/update_embodiment_config_path.py`，检查生成路径和布局摘要后再登记 revision。[NVIDIA Isaac Sim 许可](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/common/licenses-isaac-sim.html) · [固定资产快照](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/tree/1127bda94f172d757386945af3eb3a6754d1a4e6/Assets)

大 wheel 下载停滞时，可以用上游索引中同一文件的 SHA-256 校验缓存，再将 wheel 路径作为 `pip install` 的显式参数。V08 对 PyTorch cu128 使用了这个路径，随后从 `scripts/install.sh --from isaacsim` 续装。续装命令、日志和实际依赖清单见验收证据。[PyTorch cu128 wheel 索引](https://download.pytorch.org/whl/cu128/torch/)

## 安装 SDK 并运行

```bash
unset PYTHONPATH UV_PROJECT_ENVIRONMENT
export PYTHONNOUSERSITE=1
cd "$OP"
sim="$CONDA_ENVS_PATH/RoboDojo/bin/python"
policy="$CONDA_ENVS_PATH/demo-policy/bin/python"
uv sync --locked --no-dev --no-editable --python "$sim"
uv build --all-packages --wheel --out-dir "$W/wheels"
"$sim" -m pip install --no-deps "$W/wheels"/openplatform_sdk-*.whl
"$policy" -m pip install --no-deps "$W/wheels"/openplatform_sdk-*.whl
cli="$OP/.venv/bin/openplatform"
"$OP/.venv/bin/python" docs/integrations/robodojo-v08/configure-demo.py \
  "$RD" "$CONDA_ENVS_PATH" "$W/demo" --gpu 0

"$cli" list
"$cli" validate "$W/demo/submission.yaml" --check-paths
"$cli" doctor --bench robodojo --recipe conda --checkout "$RD"
"$cli" prepare --bench robodojo --recipe conda --checkout "$RD"
"$cli" eval "$W/demo" --bench robodojo --preset smoke --output-dir "$W/runs/smoke"
"$cli" eval "$W/demo" --bench robodojo --preset full --output-dir "$W/runs/full"
# 将 RUN_ID 替换为 eval 输出的 run_dir。
"$cli" status "$W/runs/full/RUN_ID"
```

SDK wheel 分别装入模拟器和 policy 环境，复用其 NumPy、MessagePack 与 WebSocket 依赖。OpenPlatform 编排使用独立的 `$OP/.venv`。插件管理仿真子进程的 hook 与原生启动目录，命令可从 checkout 外执行。可用 [验收脚本](run-clean.sh) 自动归档上面的安装和运行步骤。

`doctor` 检查当前机器和固定输入，缺项退出 1。`prepare` 输出可执行的上游准备计划，环境和资产由调用方按计划准备。评测的原始结果、日志、动作、相机索引、视频和公共指标保存在输出的 `run_dir`。用 `native/verified.json` 检查有效 episode 和实际布局，用 `native/episode-index.json` 定位每个 episode 的视频和轨迹。完整评测沿用上游 `eval_nums` 与评分分母。若原生进程只返回部分配额，run 保留已有产物并报告收集失败。正常任务失败可产生有效 episode。

`full` 会在同一仿真 GPU 上顺序执行原生 25 个有效 episode。默认仿真子进程超时是 4 小时，可在提交的 `evals[].parameters.timeout` 调整。验收机上 smoke 用时 299 秒，按用户要求停止的 5 集样本用时 851 秒，产生约 30 MB 原生证据。估算完整运行时还需考虑布局差异和场景初始化。两种模式默认保留三路视频和轨迹，完整 RGB 观测数组默认关闭。[轨迹字段](../robodojo-v07/README.md#从动作到帧) · [实际验收记录](../../implementation-status.md#v08-robodojo-干净部署与完整评测)

## 自定义 adapter 与 π0 权重

从 [无权重 adapter](../../../examples/submissions/robodojo-demo/policy.py) 和 [提交示例](../../../examples/submissions/robodojo-demo/submission.yaml) 开始。adapter 的四个方法处理模型加载、episode reset、观测和命名关节动作。替换为自己的模型时，仍使用相同仿真配方和 profile，配置独立模型解释器。权重路径相对于 `model.source.path`。[提交规范](../../submission.md) · [SDK 接口](../../interfaces.md#policyadapter)

发布 π0 使用 `RoboDojo-sim-arx_x5-joint-0/60000`。在新 checkout 从固定权重 revision 取推理文件和归一化统计，训练状态可排除。另取 OpenPI tokenizer。推理环境按上游 `uv.lock` 创建，V06 记录了版本、补齐的依赖和确定性设置。按 [π0 示例](../../../examples/submissions/robodojo-pi0/submission.yaml) 填入自己的路径和 GPU。这个参考 adapter 导入 OpenPlatform 的参考模型包装，可把同次构建的 core wheel 和 SDK wheel 用 `--no-deps` 安装到已准备的 π0 环境。[固定权重、tokenizer 与环境命令](../robodojo-v06/README.md#复用-linux-环境) · [发布权重目录](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/tree/786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33/ckpt/RoboDojo/Pi_0/RoboDojo-sim-arx_x5-joint-0/60000)
