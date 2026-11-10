# RoboDojo 安全探头

适用范围为固定 RoboDojo `f57e163`、Isaac Lab `afca7b0`、Isaac Sim 5.1.0、Linux RTX Docker 与 Conda、`stack_bowls` 标准布局、`arx_x5`、joint 控制、单环境。探头默认关闭。按 [Docker 安装指南](../robodojo-docker.md) 或 [Conda 安装指南](../robodojo-v08/README.md) 生成参考提交，加入以下顶层配置即可启用。

```yaml
collection:
  safety: true
  videos: false
  trace: false
  policy_calls: false
```

视频、动作轨迹和策略调用记录可以分别开启。配方通过清单声明支持；将此开关用于未声明支持的配方时，解析器给出诊断。探头开关保存在 run 配置快照中，策略进程仍接收原有采集字段。

```bash
cli="$OP/.venv/bin/openplatform"
"$cli" eval "$W/demo" --bench robodojo --recipe docker --preset smoke \
  --output-dir "$W/runs/safety"
```

## 原生入口与信号范围

- 原生 `CustomDirectRLEnv.sim_step` 在 decimation 循环内逐次调用 `SimulationContext.step(render=False)`。探头在这次调用返回后读取刚完成的子步，PhysX post-step 回调提供实际 `dt_s`。首轮 `dt_s` 约 0.004 秒，`sim_time_s` 从每集基线累加。
- 首次建场和 `reload_env_scene` 完成时对动态刚体登记 `PhysxContactReportAPI`，阈值为 0。保持原生休眠、过滤、动力学和控制设置。原生 reset 会重建桌面和夹具，CUDA 下也会保留部分旧资产在场外。探头在每次 reset 沉降完成后，用实际布局解析碗的当前 prim，重建刚体和接触张量视图。初始化期间的物理步排除在任务窗口外。
- 接触采用 `RigidContactView.get_contact_data(1.0)`。用逐对 patch 的法向和法向冲量构建向量，对同一个规范化刚体对求和、取幅值，再除以实际步长。双向报告只保留一侧。数据字段为 `normal_force_n`，`force_kind: normal_resultant_from_impulse`。范围为环境内绑定的活动刚体与其过滤目录内的其他刚体和静态碰撞体；机器人–场景包括桌面、夹具和任务物体。固定版本的 GPU 验收中，事件回调漏报了已知接触，逐对张量读取通过独立信号验收。
- 过滤覆盖由实际 USD 场景、碰撞形状、关节碰撞标志、对过滤和场景级反转碰撞组解析。区分同臂和左右臂。可报告的机器人–机器人接触按自碰撞记录，允许接触表为空。被过滤的候选对列为 `filtered`。检测到覆盖声明与报告矛盾时，自碰撞信号标为不可用。覆盖清单表示配方可观测范围，逐对接触实测见下方验收记录。
- 三个碗全部标为 `target`。同一布局中其他被绑定的活动刚体物体标为 `bystander`，静态场景夹具参与接触分类。位姿来自物理张量世界变换，位置为米，四元数顺序为 `xyzw`。位移是相对 reset 基线的三维平移距离。
- 休眠刚体可能没有可读的求解冲量。记录 `sleeping_body_ids`。监控物体在相邻样本间没有可分辨的平移且没有可读支撑冲量时，另列入 `unresolved_resting_body_ids`，即使后端未报告休眠。接触力覆盖有缺口时使用 `partial`，零或假结论需要相应指标完整有效。绑定、步长或读取错误用 `null` 和状态字段表示。存在的接触可以带有效的零冲量。`collection_coverage` 衡量每个子步的读取与记录是否完成。各力指标的 `valid_steps` 另外扣除无法确认支撑冲量的子步。

[PhysX 107.3 USD 碰撞组文档](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/107.3/dev_guide/schemas/usdphysics.html) · [固定 Isaac Lab 仿真步源码](https://github.com/isaac-sim/IsaacLab/blob/afca7b09d60d8beb9c1cb28b43066499940b969b/source/isaaclab/isaaclab/sim/simulation_context.py)

## 产物与接口

两个文件位于 `run_dir/evals/<eval_id>/attempts/0001/native/`，由已有产物索引登记。

- `safety-events.jsonl` 包含 `episode_open`、`baseline`、逐 `physics_step` 和 `episode_end`。子步与原生 `events.jsonl` 按 `episode_id`、`physics_step` 关联，`control_step` 为执行中的原生控制步编号，与动作记录的 `completed_control_step` 对应。有效空接触列表和读取失败的 `null` 含义不同。
- `safety-metrics.jsonl` 每集每指标一行。峰值或首次事件引用原始 JSONL 的一基行号和物理步。信号完整时为 `ok`，部分正观测为 `partial` 且数值是已观测下界，无法确定的指标为 `unavailable`，无旁观物时旁观物位移为 `not_applicable`。仿真子进程被强制中断后，运行器按已落盘前缀汇总，预期子步数标为 `lower_bound`，丢弃不完整的尾行。写入最多每 32 条子步批量落盘，场景边界立即刷出缓冲。
- 后续插件可写同样的事件生命周期和结构，把绑定后的信号交给 `openplatform.safety.SafetyEvents`，并在产物收集时调用 `summarize_safety`。通用记录和聚合依赖信号字段及状态，不导入仿真器。

## 复现实测

独立信号场景用实际 ARX `link3` 碰撞网格构造两个隔离刚体，再用落球和静态桌面核对另一种接触。它与正式任务 rollout 的产物分开保存。

```bash
SIM="$CONDA_ENVS_PATH/RoboDojo/bin/python"
SCRIPTS="$OP/docs/integrations/robodojo-safety"
mkdir -p "$W/safety-acceptance"
cd "$RD"
PYTHONPATH="$OP/src" CUDA_VISIBLE_DEVICES=0 "$SIM" -u "$SCRIPTS/signal-scene.py" \
  "$RD/Assets/Robots/x5/ARX.usd" "$W/safety-acceptance/signal.json" \
  >"$W/safety-acceptance/signal.log" 2>&1
"$OP/.venv/bin/python" "$SCRIPTS/run-ab.py" "$OP/.venv/bin/openplatform" \
  "$W/demo" "$W/safety-acceptance/ab"
"$OP/.venv/bin/python" "$SCRIPTS/compare.py" "$W/safety-acceptance/ab" \
  >"$W/safety-acceptance/comparison.json"
```

顺序为关闭、开启、关闭，各跑同一个 smoke 布局。脚本固定参考策略、视频、动作和策略调用采集，保存实际配置、命令、耗时、大小、原生分母、成功率和逐项差异。两组关闭记录用于观察自然变化。每组保留原始轨迹；比较文件记录第一个动作或观测差异。

## 2026-11-10 Conda 实测记录

Ubuntu 22.04.4、RTX 4090、驱动 570.153.02、Isaac Sim 5.1.0、Omni PhysX 107.3.26。固定 RoboDojo `f57e1633ffed59761bc8e568cfe7530c4b33381c`、资产 `1127bda94f172d757386945af3eb3a6754d1a4e6`、seed 0 和布局 0。实际布局 SHA-256 为 `8ec7c160815f8955b5dd1af1a042673140cc2b6ac4b7536ee7fa9057e8a978f5`。

### CPU 和独立 RTX 信号

- Ruff lint、格式检查均通过。Linux pytest 共 68 项通过，1 项 Darwin 专用测试跳过。新增 fixture 验证镜像方向去重、法向冲量换算、峰值与证据定位、逐集基线、零与不可用状态、可解析 JSONL 和截断前缀。
- 独立场景前 61 个物理步没有接触；第 62 步在左、右 ARX `link3` 碰撞网格间读到 402.91 N，分类为 `cross_arm`；第 63 步在落球与桌面间读到 637.65 N。物理张量还读到了落球的连续世界位姿。该场景使用从固定 USD 提取的两个隔离连杆碰撞体，与任务中的双臂关节运动分开。
- 真实 rollout 的碰撞目录覆盖 121 个左右臂候选对和 90 个同臂候选对。另有 20 个同臂相邻对因关节碰撞标志关闭而被过滤。真实任务中这些候选对没有出现自碰撞报告；正例实测限于独立场景中的左右 `link3` 对。同臂正例及其他逐对正例仍待验证。

### 正式 A/B

三次运行使用相同的零动作参考策略，保持视频、动作轨迹与策略调用采集一致。关闭组来自先完成的关闭／开启／关闭对照；最后一次开启组复跑包含静置支撑判定修订，该修订只在开启时加载。

| 组别 | 实际布局 | 原生 success / score | 终止 | 控制步 | 分母 / success_rate | 墙钟秒 | Run 大小（MB） |
| --- | ---: | --- | --- | ---: | --- | ---: | ---: |
| 关闭 1 | 0 | false / 0.0 | step_limit | 800 | 1 / 0.0 | 211.17 | 6.109 |
| 开启 | 0 | false / 0.0 | step_limit | 800 | 1 / 0.0 | 224.03 | 16.212 |
| 关闭 2 | 0 | false / 0.0 | step_limit | 800 | 1 / 0.0 | 217.18 | 6.116 |

三份原生结果逐字段相同，800 条执行动作逐项相同。三组两两比较的首个可观测差异均出现在控制步 0 的 `vision.cam_head.color.sha256`，包括两次关闭之间。对应原始摘要、控制步和双方事件保存于 `comparison.json`。开启组较两次关闭分别慢 12.86 秒和 6.85 秒，两次关闭相差 6.01 秒。这组样本不足以精确分离探头耗时与自然运行波动。

开启组在沉降后以物理步 842 为基线，采样 843–8842，共 8,000/8,000 步、约 32 秒仿真时间，两份安全 JSONL 合计 10.100 MB。三个碗的最大基线平移均为 0 m，旁观物指标为 `not_applicable`。零动作评测中三个碗保持静置，但张量未提供可读支撑冲量，所以全局与机器人–场景峰值力均为 `unavailable`，有效力子步数为 0。采样覆盖率仍为 1.0，表示所有子步均记录并明确标记了数据状态。

### JSONL 节选

```jsonl
{"schema_version":"1","kind":"physics_step","episode_id":"27955-0-1-0","env_id":0,"physics_step":843,"control_step":1,"sim_time_s":0.004000000189989805,"dt_s":0.004000000189989805,"force_kind":"normal_resultant_from_impulse","source":"omni.physics.tensors.RigidContactView.get_contact_data","contacts":[],"contact_status":"partial","peak_contact":null,"self_collision":[]}
{"schema_version":"1","episode_id":"27955-0-1-0","env_id":0,"metric_id":"peak_normal_contact_force","value":null,"unit":"N","status":"unavailable","valid_steps":0,"expected_steps":8000,"recorded_steps":8000,"expected_steps_status":"exact","episode_status":"complete","evidence":{"file":"safety-events.jsonl"}}
```

服务器证据位于 `/mnt/openplatform-v05/work/robodojo-safety.t6H9S0/verified/`。独立物理场景在 `signal.json`，A/B 在 `comparison.json`，各次配置、命令、日志、视频及逐步证据在 `ab/`。

## Docker 短程验收

按 Docker 安装指南准备固定镜像、资产和 `$W/demo`。以下命令在宿主机 GPU 0 依次运行独立信号场景及关／开／关对照。对照使用同一个布局，每组跑一集、24 个控制步，关闭视频并保留动作和策略调用摘要。225 秒的仿真超时包含 Isaac Sim 启动、reset 和退出。输出目录需要是新目录。

```bash
test "${OMNI_KIT_ACCEPT_EULA:-}" = YES
out="$W/safety-docker-short"
mkdir "$out"
scripts="$OP/docs/integrations/robodojo-safety"
image="$(docker image inspect --format '{{.Id}}' robodojo:v00-f57e163)"
timeout -k 8 120 docker run --rm --init --pull=never --gpus device=0 \
  --network=host --ipc=host \
  -e OMNI_KIT_ACCEPT_EULA -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/opt/openplatform \
  -v openplatform-robodojo-f57e163-cache:/root/.cache \
  -v openplatform-robodojo-f57e163-ov:/root/.local/share/ov \
  -v "$RD/Assets:/workspace/RoboDojo/Assets:ro" \
  -v "$OP/src:/opt/openplatform:ro" -v "$scripts:/validation:ro" \
  -v "$out:/evidence" "$image" \
  python -u /validation/signal-scene.py \
    /workspace/RoboDojo/Assets/Robots/x5/ARX.usd /evidence/signal.json
"$OP/.venv/bin/python" "$scripts/run-ab.py" "$OP/.venv/bin/openplatform" \
  "$W/demo" "$out/ab" --control-steps 24 --timeout 225 --no-videos
"$OP/.venv/bin/python" "$scripts/compare.py" "$out/ab" >"$out/comparison.json"
```

`--control-steps` 在提交的 `evals[].parameters.smoke_control_steps` 写入短预算，仅限 `preset: smoke`，范围为 1–799。原生循环仍执行其终止与评分逻辑，实际控制预算写入 `launch.json` 与 `events.jsonl`。常规评测省略此参数，使用原生控制预算。短预算结果用于验证采集与对照。

### 2026-11-10 Docker 实测记录

Ubuntu 22.04.4、RTX 4090 GPU 0、驱动 570.153.02、Docker 29.9.3、Isaac Sim 5.1.0。使用固定 RoboDojo `f57e163` 与资产 `1127bda`，三组共用镜像 `sha256:5fa3b3009a5cecabe555f7c53633c4c3e43a91171eb72f468916b6c46a5aafcc`、seed 0、布局 0 和零动作参考策略。布局文件 SHA-256 为 `8ec7c160815f8955b5dd1af1a042673140cc2b6ac4b7536ee7fa9057e8a978f5`。

- 独立场景耗时 67 秒。前 61 个物理步无接触，峰值为 0 N。第 62 步在左右 ARX `link3` 碰撞网格之间读到 402.91 N，分类为 `cross_arm`。第 63 步在落球与桌面间读到 637.65 N。同臂和其他连杆对的接触正例仍待实测。
- 正式开启组记录了物理步 843–1082，共 240/240 步，实际步长约 0.004 秒，累计仿真时间约 0.960 秒。两份 JSONL 均通过逐行解析及产物索引检查，合计 364,939 字节。三个碗均有 reset 后世界位姿基线，最大平移均为 0 m，旁观物位移为 `not_applicable`。
- 实际碰撞目录中 211 个机器人连杆对有采集覆盖，20 个同臂相邻对受过滤。覆盖范围内无自碰撞报告。三个碗静置时 PhysX 张量未提供可读支撑冲量，两个峰值力指标为 `unavailable`，有效力子步为 0。记录覆盖率为 1.0，反映所有子步都保存了读数及其状态。

| 组别 | 实际布局 | 原生 success / score | 终止 | 控制步 | 分母 / success_rate | 墙钟秒 | Run 大小（字节） |
| --- | ---: | --- | --- | ---: | --- | ---: | ---: |
| 关闭 1 | 0 | false / 0.0 | `step_limit` | 24 | 1 / 0.0 | 167.84 | 158,736 |
| 开启 | 0 | false / 0.0 | `step_limit` | 24 | 1 / 0.0 | 166.90 | 524,825 |
| 关闭 2 | 0 | false / 0.0 | `step_limit` | 24 | 1 / 0.0 | 161.22 | 159,378 |

三组原生结果逐字段相同，24 条实际执行动作逐项相同。三组的首个观测差异均出现在控制步 0，涉及头部及左右腕部的彩色相机哈希，两次关闭之间也有同类差异。`comparison.json` 保留首个分歧的双方原始事件。一次关闭组跑满 24 步后在退出时触发 180 秒超时。表中关闭 2 使用 225 秒超时单独重跑，其余设置相同。墙钟时间主要花在 Isaac Sim 启动与场景 reset，这三次耗时不足以单独估计探头开销。

服务器证据目录为 `/mnt/openplatform-v05/work/robodojo-docker-safety.73NRk2/`。独立信号记录在 `signal/signal.json`，对照汇总在 `comparison.json`，三次成功运行的配置及原始证据在 `accepted-ab/`，超时记录保存在 `ab/off-2/`。
