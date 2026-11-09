# V00 RoboDojo 原生评测调查

2026-11-07。**源码调查已验收，GPU 原生基线待验证。** 初始范围选 RoboDojo-Sim 的 `stack_bowls` 标准布局、`arx_x5` 双臂、`joint` 控制、seed 0、单环境。先用 `demo_policy` 跑 1 个有效 episode，再用发布的 `Pi_0` checkpoint 复跑同一范围。

## 固定版本与证据

| 组件 | 固定版本 | 依据 |
| --- | --- | --- |
| RoboDojo | `f57e1633ffed59761bc8e568cfe7530c4b33381c` | 官方 `HEAD` 实查，已 checkout。提交日期 2026-10-21，项目元数据版本 `0.2.0`。[提交](https://github.com/RoboDojo-Benchmark/RoboDojo/commit/f57e1633ffed59761bc8e568cfe7530c4b33381c) |
| XPolicyLab | `5d0be1499634d3176258c8f2e31f5fe816497b0a` | RoboDojo gitlink，已 checkout。[源码](https://github.com/XPolicyLab/XPolicyLab/tree/5d0be1499634d3176258c8f2e31f5fe816497b0a) |
| IsaacLab fork | `afca7b09d60d8beb9c1cb28b43066499940b969b` | RoboDojo `third_party/IsaacLab` gitlink。[提交](https://github.com/yuechen0614/IsaacLab/commit/afca7b09d60d8beb9c1cb28b43066499940b969b) |
| CuRobo fork | `d17b54ce32cba095c0b000c4c58777075d11de0e` | RoboDojo `third_party/curobo` gitlink。[提交](https://github.com/yuechen0614/curobo/commit/d17b54ce32cba095c0b000c4c58777075d11de0e) |
| 仿真资产 | `1127bda94f172d757386945af3eb3a6754d1a4e6` | 选用官方 Hugging Face dataset 的资产快照。实际下载、文件摘要与仿真兼容性待验证。[资产提交](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/commit/1127bda94f172d757386945af3eb3a6754d1a4e6) |
| π0 权重 | `786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33`，`ckpt/RoboDojo/Pi_0/RoboDojo-sim-arx_x5-joint-0/60000` | 官方发布提交及目录已确认，权重下载、加载和推理待验证。[发布提交](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/commit/786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33) · [权重目录](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/tree/main/ckpt/RoboDojo/Pi_0/RoboDojo-sim-arx_x5-joint-0/60000) |

安装与参数说明对照官方 Wiki 的 [安装与下载](https://robodojo-benchmark.com/doc/usage/install-and-download/)、[Quick Evaluation](https://robodojo-benchmark.com/doc/usage/quick-evaluation/) 和 [XPolicyLab](https://robodojo-benchmark.com/doc/usage/xpolicylab/)。Wiki 会更新，以下入口和行为以固定提交为准。RoboDojo 本地开源入口支持独立执行仿真评测，模型侧在本机启动 XPolicyLab server 即可。官方榜单另走云评测流程。[固定 README](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/README.md)

## 原生调用链

1. `bash scripts/robodojo.sh eval` 解析同机单任务参数，调用 `scripts/internal/run_policy_eval.sh`。后者直接调用 policy 的 `setup_eval_policy_server.sh` 和 `setup_eval_env_client.sh`，选 localhost 空闲端口，等 server 就绪，退出时清理 server 进程树。独立启动的入口是 `robodojo.sh server` 和 `robodojo.sh client`。`client` 仍要求 simulator checkout 下有匹配的 `XPolicyLab/policy/<name>/deploy.py`，并从该 policy 的 `deploy.yml` 读取 `eval_batch`。[公开入口](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/robodojo.sh) · [同机编排](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/internal/run_policy_eval.sh)
2. Server launcher 调 `XPolicyLab/setup_policy_server.py`，按 `deploy.yml` 和 overrides 加载 `policy/<name>/model.py` 的 `Model(model_cfg)`，对外监听 WS。Sim client 经 XPolicyLab 的 `setup_env_client.sh` 和 `run_sim_env_client.sh` 激活 `RoboDojo` conda 环境，再调 RoboDojo 的 `scripts/eval_policy.sh`。该脚本设置 `PYTHONPATH`、`CUDA_VISIBLE_DEVICES`，带 `--headless --enable_cameras` 启动 `src/eval_client/main.py`。[Server](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/setup_policy_server.py) · [Client launcher](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/utils/run_sim_env_client.sh) · [仿真启动](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/eval_policy.sh)
3. `main()` 通过 IsaacLab `AppLauncher` 初始化 Kit，合并 env、sim、scene、camera、robot、task 配置和任务覆盖项。`create_eval_env()` 派生自注册的任务类，创建 `WsModelClient`。`eval_batch: false` 把 sim 配置的 10 个环境降为 1。`main()` 从 `SeedManager` 取布局，调用 `env.reset(seed=...)`、`env.run_eval()`，按有效 episode 数继续取样。[主循环](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/main.py) · [任务覆盖项](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/utils/pipeline_utils.py)
4. `EvalEnv.reset()` 重放指定布局，检查稳定性、预热场景，初始化机器人与评分状态，再发无参 policy `reset`。`run_eval()` 配置任务的 reward 和过程分，动态导入 XPolicyLab 的 `deploy.py`，调用 `eval_one_episode(TASK_ENV, model_client)`。`demo_policy` 与 `Pi_0` 的 `deploy.py` 进入循环时各再发一次 `reset`，随后 `get_obs → update_obs → get_action`。执行 chunk 中每个 action 后检查终止。只要还有 chunk 内的下一步且未终止，就再取帧并 `update_obs`，不重新推理。chunk 耗尽才进入下一次 `get_action`。[EvalEnv](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/eval_env.py) · [demo 循环](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/demo_policy/deploy.py) · [π0 循环](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/deploy.py)

## `arx_x5` joint profile 的 I/O

| 边界 | 固定源码中的语义 |
| --- | --- |
| 语言与元数据 | `instruction` 是任务指令字符串，`stack_bowls` 生成叠起三个碗的指令。原生还有 `env_idx`、`data_format_version: v1.0`、`additional_info.frequency: 25`。观测中的 `action` 镜像来自当前臂关节位置和前次夹爪控制。 |
| 相机 | `vision.{cam_head,cam_left_wrist,cam_right_wrist}.color`。代码在读取前同步最新渲染帧，取 RGB 前三通道，名义形状 `(480, 640, 3)`、`uint8`，同级提供 `shape`。默认 profile 关闭深度和内外参。该仿真路径直接发送 ndarray。XPolicyLab server 可在调用模型前解码其他来源的 JPEG 字段，已解码数组直接通过。 |
| 手臂状态 | `state.left_arm_joint_state` / `right_arm_joint_state` 各 `(6,)`，由仿真 articulation 当前关节位置取得，转动关节单位 rad。顺序是 x5 资产 `arm_joints_name` 经 `find_joints()` 得到的顺序。资产实际关节名及索引顺序待在 GPU 机核对。 |
| 夹爪状态 | `state.left_ee_joint_state` / `right_ee_joint_state` 各 `(1,)`。代码用 `prev_control` 的主夹爪关节目标逆映射得到归一化开度，测量反馈与命令值应分开记录。 |
| 末端状态 | `state.left_ee_pose` / `right_ee_pose` 各 `(7,)`，位置单位 m，四元数顺序 `w,x,y,z`。`get_real_endpose()` 读取 EE link 的世界姿态，仅减去该并行环境原点的平移。坐标轴沿世界轴，原点随环境 tile，和各机器人底座坐标系不同。joint profile 保留此状态字段。 |
| joint action | 一步是含上述四个关节／夹爪键的字典，各臂 `(6,)` 为绝对关节位置目标，夹爪 `(1,)` 为开度，0 闭合端、1 张开端。原生将开度裁到 `[0,1]`，用资产的 `gripper_scale`、`gripper_move.sign` 和 mimic 参数映射到两指。动作顺序由左右字段及各自原生关节顺序确定。原生接收一维 ndarray 或 list／tuple；SDK 可以统一为 ndarray。 |
| 模式与频率 | `get_action_type()` 根据动作键推断 joint 或 EE，混用会报错。`eval --action-type joint` 配置模型输出，`client --action-type` 只写结果标签。EE 分支使用绝对 `*_ee_pose` 做 IK，本次范围仅覆盖 joint。`dt=0.004 s`、`decimation=1`，每个控制目标展开为 10 个物理步，前 8 步线性插值、后 2 步保持目标，名义 25 Hz 控制和 250 Hz 物理。推理、渲染耗时单独统计。 |

依据：[环境配置](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env_cfg/arx_x5.yml) · [物理配置](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env_cfg/sim/sim_config.yml) · [相机模板](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env_cfg/camera/template.py) · [ObsManager](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env/observation_manager/obs_manager.py) · [RobotManager](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env/robot_manager/robot_manager.py) · [双臂维度](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env_cfg/robot/_robot_info.json) · [EvalEnv 动作执行](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/eval_env.py#L337) · [x5 资产配置](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/blob/main/Assets/Robots/x5/robot_config.yml)

### Reset、终止与原始结果

- `--seed 0` 选择 `Assets/Eval_Layout/RoboDojo/arx_x5/0/`。`SeedManager` 对匹配 `stack_bowls_<整数>.json` 的文件按数字排序，再生成从 0 开始的 `layout_id`。episode 序号、布局索引和评测 seed 应分别记录。`--eval-num 1` 限制有效结果数。标准 `stack_bowls` 和 `stack_bowls_random` 各有 25 个原生 episode 配额，完整 Generalization 的该任务合并两个 split。官方 inventory 有 42 个逻辑任务，另有 12 个 `_random` 入口，共 54 个 runnable 条目。[布局取样](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/env/seed_manager/seed_manager.py) · [任务配额](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/task/RoboDojo/config/_task.yml) · [官方汇总](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/internal/summarize_result.py)
- `take_action()` 校验后调用 `take_action_batch()`，执行插值序列并更新 reward。`is_episode_end()` 在原生 reward 大于 `1 - 1e-3` 时判成功，达到任务上限 `800` 个控制动作或被判失败时结束，结束时另取最后一帧。`stack_bowls` 成功条件包括碗的姿态、堆叠和机械臂回到原位，另有过程分。它提供 `reset/get_obs/take_action/is_episode_end` 这组评测方法，内部 `step` 用于推进物理控制。[任务评分](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/task/RoboDojo/tasks/stack_bowls.py) · [终止实现](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/eval_env.py#L841)
- `_result.json` 原样保留 `eval_time`（有效成功数加有效失败数）、`success_rate`（0–1）、`score`（0–100）和 `details`（每项 `layout_id/success/score`，episode 分数 0–1）。成功项记 1，失败项取原生过程分或 0。非法布局、不稳定环境及跳过的异常轮次可从有效分母中排除。部分任务启用 PhysX 恢复，可能放弃坏布局并取替补。外部应检查退出码、预期有效 episode 数、日志和布局覆盖，原生进程有捕获异常后继续或提前用尽布局的路径。[结果写入](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/eval_env.py#L771) · [异常与恢复](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/main.py#L347)
- 输出目录为 `eval_result/RoboDojo/<task>/<policy>/<env_cfg>/<seed>_ckpt_name=<ckpt>,action_type=<type>/<ROBODOJO_RUN_ID>/`。视频按相机分别流式写入，文件名如 `episode_0000000_cam_head_fail.mp4`。`ffmpeg` 是这条视频路径的运行依赖。原生结果提供布局与评分，视频帧索引、实际控制序列、步数和 policy RPC 时序需要另行采集。视频写出失败可单独出现在日志中。[原生视频](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/src/eval_client/eval_env.py#L875) · [FFmpeg writer](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/utils/save_file.py#L24)

## 参考 policy 与运行条件

- **无权重原生 smoke：`demo_policy`。** 官方模板在 `joint` 模式输出两个全零的双臂关节／夹爪目标，`reset` 和 `update_obs` 不维护模型状态，`eval_batch: false`。`--ckpt demo` 是 CLI 要求的标记。成功标准为评测闭环与有效产物，即使任务失败也能通过。官方 Docker smoke 也使用此 policy，本轮采用原生进程。[demo 模型](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/demo_policy/model.py) · [官方 smoke](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/docker/smoke_docker.sh)
- **带权重对照：`Pi_0`。** 使用 XPolicyLab 自带 OpenPI 和 `uv` 环境，`action_type: joint`、`eval_batch: false`，模型配置 `pi0_base_aloha_full_sim_arx-x5_seed_0`，`repo_id: arx_x5_sim`。模型端将头部图映射到 `cam_high`，转换成 CHW `uint8`，打包机器人状态，调用 `policy.infer` 后解包为原生动作字典序列。配置的模型 horizon 为 50，实跑时记录实际 chunk 长度。发布目录 `60000` 有 `params` 和 `assets`，`deploy.yml` 的 `checkpoint_num: 59999` 对不上目录名时，resolver 回退到可用的最大数字步，在选定目录中为 `60000`。加载还需要该步的 `assets/arx_x5_sim` 归一化统计，首次 tokenizer 初始化可能访问 `gs://big_vision/paligemma_tokenizer.model`。这条 eval 路径直接加载发布权重。[π0 环境与命令](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/README.md) · [部署配置](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/deploy.yml) · [模型与 resolver](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/model.py) · [tokenizer](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/openpi/src/openpi/models/tokenizer.py)
- **上游声明的硬件。** RoboDojo 推荐 Ubuntu 22.04 x86_64、NVIDIA GPU、RAM ≥32 GB、VRAM ≥16 GB、Linux 驱动 570 或 580、CUDA 12.8、Vulkan。Isaac Sim 5.1 x86_64 表列 RTX 4080／16 GB、50 GB SSD 和测试过的 Linux 驱动 580.65.06，并明确渲染需 RT cores。50 GB 是 Isaac Sim 自身的规格。Headless 仍要渲染三路相机。Pi_0 显存、与仿真同卡的可行性、native 总峰值磁盘及耗时均待实测。[RoboDojo 要求](https://robodojo-benchmark.com/doc/usage/install-and-download/#1-system-requirements) · [Isaac Sim 5.1 要求](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html)
- **依赖与资产。** 仿真环境 Python 3.11，Isaac Sim `5.1.0`（含扩展缓存）、PyTorch `2.7.0` cu128、固定 IsaacLab 和 CuRobo 源码。安装脚本固定部分 Python 包，包括 NumPy `1.26.0` 与仿真侧 `websockets==12.0`。仿真 client 源码适配旧版 WS 的 `proxy` 参数，模型侧按 XPolicyLab 的独立依赖安装（`websockets>=14`）。完整依赖锁待 V05 在干净环境生成。资产需机器人 USD／URDF 与 CuRobo 模板、场景和对象／材质资源、评测布局。Wiki 写 ModelScope，固定 `scripts/init_assets.sh` 实际默认 Hugging Face dataset，通过 Git LFS 拉取 `Assets/**`。Hub 目录显示资产约 41.3 GB，π0 所选单步发布目录约 43.1 GB。代码 MIT、XPolicyLab Apache-2.0，HF dataset card 标示 Apache-2.0。模型和资产的再分发条件随实际取得的文件确认。[安装源码](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/install.sh) · [资产脚本](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/init_assets.sh) · [资产目录与许可](https://huggingface.co/datasets/RoboDojo-Benchmark/RoboDojo/tree/main/Assets) · [XPolicyLab 依赖](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/pyproject.toml)

## 最小原生运行步骤（待 GPU 验证）

在自备 Ubuntu 22.04 x86_64 RTX 机器的 Bash 中执行。预先准备 conda、可用的 NVIDIA 驱动和下列依赖源的网络访问。以下步骤以干净目录和新建 conda 环境为前提。

### 1. 固定源码并安装仿真环境

`install.sh -i` 会执行 `git submodule update --remote`，使 checkout 的子模块偏离 gitlink。下面手动完成前置步骤，再从上游安装器的 `isaacsim` 步继续。安装器的 IsaacLab 步设置 `OMNI_KIT_ACCEPT_EULA=YES`，运行前应由机器使用者确认适用条款。[安装脚本](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/install.sh) · [NVIDIA 许可](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/common/licenses-isaac-sim.html)

```bash
set -eo pipefail
nvidia-smi
sudo apt-get update
sudo apt-get install -y git cmake build-essential ffmpeg libvulkan1 mesa-vulkan-drivers vulkan-tools
vulkaninfo --summary

git clone https://github.com/RoboDojo-Benchmark/RoboDojo.git
cd RoboDojo
git checkout --detach f57e1633ffed59761bc8e568cfe7530c4b33381c
git submodule update --init
git submodule status

eval "$(conda shell.bash hook)"
conda create -y -n RoboDojo python=3.11 pip
conda activate RoboDojo
python -m pip install -r scripts/requirements.txt
python -m pip install opencv-python-headless==4.11.0.86 pillow matplotlib scipy==1.15.3 scikit-learn numpy==1.26.0
bash scripts/install.sh --from isaacsim
git submodule status
```

两次 `git submodule status` 都应匹配表中三条 gitlink，且无 `+`、`-` 或 `U` 状态前缀。安装脚本和依赖求解只做了源码核对，干净 Linux 环境的实际安装结果待记录。

### 2. 下载固定资产并准备 demo server 环境

下面用 `hf download` 指定完整 commit 和 `Assets/**`，跳过训练集与权重。官方资产脚本也可下载资产，但其缺省 revision 为 `main`，已有目录会短路检查。`update_embodiment_config_path.py` 从 checkout 根目录生成含本机绝对路径的 `curobo.yml` 等文件，换路径后重跑。[HF CLI](https://huggingface.co/docs/huggingface_hub/en/package_reference/cli#hf-download) · [机器人路径生成](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/utils/update_embodiment_config_path.py)

```bash
hf download RoboDojo-Benchmark/RoboDojo --repo-type dataset \
  --revision 1127bda94f172d757386945af3eb3a6754d1a4e6 \
  --include 'Assets/**' --local-dir "$PWD"
python utils/update_embodiment_config_path.py
test -s Assets/Robots/x5/curobo.yml
compgen -G 'Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_[0-9]*.json' >/dev/null

conda create -y -n demo-policy python=3.11 pip
conda run -n demo-policy bash XPolicyLab/policy/demo_policy/install.sh
bash scripts/robodojo.sh doctor --task stack_bowls --env-cfg arx_x5 \
  --policy-dir XPolicyLab/policy/demo_policy --policy-env demo-policy
```

### 3. 跑 1 个有效 episode 并核对产物

```bash
run_id="v00-demo-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p v00-logs
ROBODOJO_RUN_ID="$run_id" EVAL_ENV_TYPE=sim bash scripts/robodojo.sh eval \
  --policy-dir XPolicyLab/policy/demo_policy --task stack_bowls --ckpt demo \
  --policy-env demo-policy --eval-env RoboDojo --env-cfg arx_x5 --action-type joint \
  --seed 0 --policy-gpu 0 --env-gpu 0 --eval-num 1 \
  2>&1 | tee "v00-logs/$run_id.log"

native_dir="eval_result/RoboDojo/stack_bowls/demo_policy/arx_x5/0_ckpt_name=demo,action_type=joint/$run_id"
python - "$native_dir/_result.json" <<'PY'
import json
import sys
from pathlib import Path

result = json.loads(Path(sys.argv[1]).read_text())
assert result["eval_time"] == 1 and len(result["details"]) == 1, result
assert 0 <= result["success_rate"] <= 1 and 0 <= result["score"] <= 100, result
print(json.dumps(result, indent=2))
PY
for camera in cam_head cam_left_wrist cam_right_wrist; do
  videos=("$native_dir"/episode_0000000_"$camera"_*.mp4)
  test -s "${videos[0]}"
  ffprobe -v error -select_streams v:0 -show_entries stream=codec_type,width,height "${videos[0]}"
done
```

保留本次日志、原始 JSON、视频、GPU／驱动记录、资产 revision 与摘要、实际 `conda list` 和 `pip freeze`。检查 `layout_id` 与布局取样、实际 RGB 通道及动作执行日志，确认异常和重启情况。单卡编号 `0` 仅用于此 demo 配方，按机器实际 GPU 编号调整。V06 之前需要保留这类原生运行证据。

### 4. 带权重的原生对照

在同一 checkout 预装 `uv`。只下载选定发布 checkpoint 目录，并链接到 policy 的标准查找路径。官方 `download_ckpt.sh huggingface Pi_0` 会拉整个 policy 目录，默认跟踪分支。以下命令使用精确 revision，初次加载需校验 `norm_stats.json`、权重路径和 tokenizer 访问。[官方权重下载器](https://github.com/RoboDojo-Benchmark/RoboDojo/blob/f57e1633ffed59761bc8e568cfe7530c4b33381c/scripts/RoboDojo/download_ckpt.sh) · [π0 server launcher](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/policy/Pi_0/setup_eval_policy_server.sh)

```bash
pi0_name=RoboDojo-sim-arx_x5-joint-0
pi0_cache="$PWD/.cache/v00-pi0"
hf download RoboDojo-Benchmark/RoboDojo --repo-type dataset \
  --revision 786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33 \
  --include "ckpt/RoboDojo/Pi_0/$pi0_name/60000/**" --local-dir "$pi0_cache"
test -d "$pi0_cache/ckpt/RoboDojo/Pi_0/$pi0_name/60000/params"
test -s "$pi0_cache/ckpt/RoboDojo/Pi_0/$pi0_name/60000/assets/arx_x5_sim/norm_stats.json"
mkdir -p XPolicyLab/policy/Pi_0/checkpoints
ln -s "$pi0_cache/ckpt/RoboDojo/Pi_0/$pi0_name" "XPolicyLab/policy/Pi_0/checkpoints/$pi0_name"
bash XPolicyLab/policy/Pi_0/install.sh

run_id="v00-pi0-$(date -u +%Y%m%dT%H%M%SZ)-$$"
ROBODOJO_RUN_ID="$run_id" EVAL_ENV_TYPE=sim bash scripts/robodojo.sh eval \
  --policy-dir XPolicyLab/policy/Pi_0 --task stack_bowls --ckpt "$pi0_name" \
  --policy-env uv --eval-env RoboDojo --env-cfg arx_x5 --action-type joint \
  --seed 0 --policy-gpu "${PI0_GPU:-0}" --env-gpu "${SIM_GPU:-0}" --eval-num 1 \
  2>&1 | tee "v00-logs/$run_id.log"
```

这里的 GPU 编号默认都为 `0`，沿用上游 CLI 默认值。同卡资源需求待实测，有两块合适的卡时可用 `PI0_GPU`、`SIM_GPU` 显式分配。结果按相同方式校验，路径中的 policy 和 checkpoint 分别为 `Pi_0` 和 `RoboDojo-sim-arx_x5-joint-0`。完整任务评测再选原生配额和标准／随机布局范围，分别保存结果。

## 与 OpenPlatform 草案的对照

| 草案边界 | 结论与后续改动 |
| --- | --- |
| 四方法 adapter 与单 active episode | 构造、无参 `reset`、`update_obs`、`get_action`、命名数组和有序 chunk 可直接表达所选单环境 policy。XPolicyLab 的 `Model(model_cfg)` 使用 `ckpt_name/env_cfg_type/action_type` 等模型配置，OpenPlatform 用 `checkpoint_path/io_profile`，模型侧示例 adapter 需显式映射并复用加载、归一化和解包。`demo_policy` 明确是无 checkpoint 的 smoke 例外，V01 若要求所有模型都提供 checkpoint 文件，应保留无权重 policy 的配置路径。 |
| 生命周期与 chunk | [接口草案](../interfaces.md#policyadapter) 仅描述 episode 开始 reset 和决策前更新。V02 应允许连续 `update_obs`、chunk 内补帧及同一初始化边界两次 `reset`，保留各原生调用的顺序。一个进程只承载一个 active episode 的约束适用于本 profile。批处理和需要自定义 RPC 的 policy 需另行验证。 |
| 观测外壳 | `instruction/vision/state` 可复用，插件 profile 明确相机子字段 `color/shape`、单位、命令派生夹爪状态、原生 `env_idx/additional_info` 的映射。对参考 adapter 保留其依赖字段。是否透传观测中的 `action` 按 profile 说明来源和消费者。 |
| WS 兼容性 | XPolicyLab 和 OpenPlatform 草案都用 WS + MessagePack。XPolicyLab 实际使用 `hello/hello_ack`、`reset/reset_result`、`call/call_result`，把方法名和 `obs` 放在 `payload`，用 `message_id/evaluation_id/trial_id/action_case_id/step` 等标识，数组用 `msgpack_numpy`，`step` 随取 chunk 的 RPC 增长。OpenPlatform 草案定义自己的操作、数组编码和握手，原生 client 无法直接连 SDK server。V06 的候选路径是在仿真环境增加 `call(func_name, obs)` 到 SDK client 的薄 bridge，保留 `deploy.py` 的控制循环，继续用原生 server 做对照。XPolicyLab 自带重连、同 server 实例检查及请求去重，SDK 仍按自己的协议处理超时。 |
| 插件、运行时与结果 | `build_eval` 适合保持轻量，由 simulator 环境执行 wrapper。原生 `client` 还需要本地 `deploy.py`，一个 `PolicyEndpoint` 地址不足以替代它。`collect` 保留原生 JSON 并检查有效数与 coverage。一个 `step` 字段无法区分 policy call、25 Hz 控制动作和 250 Hz 物理 substep，V07 采集应区分这三层，并关联原生视频帧与已执行 chunk 范围。 |
| 示例清单 | [清单示例](../../examples/benchmarks/benchmark.yaml) 的 Docker、双卡和各项 trace capability 是结构占位。首个 recipe 以 native、单环境、`stack_bowls` joint smoke 为验收范围。`full` suite、资产和依赖锁、实际硬件支持与轨迹能力在相应迭代用运行证据确认。 |

RPC 依据：[同步 client](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/client_server/ws/model_client.py#L125) · [消息外壳](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/client_server/ws/protocol/schemas.py) · [编解码](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/client_server/ws/protocol/codec.py) · [服务端分发](https://github.com/XPolicyLab/XPolicyLab/blob/5d0be1499634d3176258c8f2e31f5fe816497b0a/client_server/ws/model_server.py#L402)

## 验证记录与交接

| 检查 | 结果 |
| --- | --- |
| `git ls-remote <官方 RoboDojo URL> HEAD`、`git log -1`、`git ls-tree HEAD XPolicyLab third_party/`、XPolicyLab `git rev-parse HEAD` | 固定的 RoboDojo 和 XPolicyLab checkout 与 gitlink 一致。IsaacLab、CuRobo SHA 已从 gitlink 记录。 |
| `bash scripts/robodojo.sh tasks --only-runnable` | 共 54 行，含 `stack_bowls` 和 `stack_bowls_random`。 |
| 以上 demo / π0 的 `robodojo.sh eval` 参数加 `--dry-run` | 均退出 0，解析到 `scripts/internal/run_policy_eval.sh`、`arx_x5 joint` 和 `eval_num=1`。 |
| `robodojo.sh client --task stack_bowls --policy-name demo_policy --policy-host 127.0.0.1 --policy-port 6060 --ckpt demo --env-cfg arx_x5 --action-type joint --seed 0 --env-gpu 0 --eval-num 1 --dry-run` | 退出 0，解析到 `scripts/eval_policy.sh` 的 `ws` 和本地 deploy 名称。 |
| `bash scripts/install.sh --help`、`bash scripts/robodojo.sh doctor --help` | 核对了安装起始步骤和 doctor 参数。 |
| 文档检查 | 四个 Bash 命令块通过 `bash -n`，本地相对链接通过路径检查。 |
| 运行条件与原生证据 | 实查本机 `uname -sm` 为 `Darwin x86_64`，`command -v nvidia-smi` 无结果。依赖安装、资产及权重下载、WebSocket 实际握手、渲染、关节顺序和开度端点、控制时序、π0 推理和原生结果均待 Linux RTX 机器验证。本轮无仿真日志、原生分数或视频。 |

V01–V04 可使用 CPU 开发；V05 准备干净 Linux RTX 环境和固定资产，先完成 demo 原生 smoke 并归档，再核对 π0 的 checkpoint 与模型 GPU 条件。接入成本记录可从本轮复用已确认的启动入口、两条协议边界和子模块安装差异，耗时与资源数据留到实际执行时记录。
