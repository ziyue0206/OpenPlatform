# V07 RoboDojo 轨迹与故障验收

复用 V05 RoboDojo Conda 和资产，沿用 V06 的 `stack_bowls`、`arx_x5` joint、seed 0、单环境和 GPU 0。验收 adapter 在上游无权重零动作之后重复同一动作，把返回 chunk 从两步变为三步。两种采集模式用同一个 adapter，原生 800 步上限使最后一个 chunk 只执行两步。GPU 验收依次运行采集关闭、采集开启、第三次推理抛错、仿真进程被中断、取消和取消后未启动项。[V06 证据与版本](../../implementation-status.md#v06-robodojo-官方入口对照)

## CPU

```bash
OPENPLATFORM_TEST_ROBODOJO=/private/tmp/openplatform-v00-robodojo-f57e163 \
  /private/tmp/openplatform-v01-env/bin/python -m pytest -q
/private/tmp/openplatform-v01-env/bin/ruff check src sdk tests docs/integrations/robodojo-v07
/private/tmp/openplatform-v01-env/bin/ruff format --check src sdk tests docs/integrations/robodojo-v07
bash -n docs/integrations/robodojo-v07/run-gpu.sh
UV_CACHE_DIR=/private/tmp/openplatform-v01-uv-cache \
  UV_PYTHON_INSTALL_DIR=/private/tmp/openplatform-v01-pythons \
  /private/tmp/openplatform-v01-tools/bin/uv build --offline --all-packages --wheel \
  --out-dir /private/tmp/openplatform-v07-accepted-wheels
```

## GPU

最终验收的服务器工作目录为 `/mnt/openplatform-v05/work/v07-accepted`。同步本目录、`examples/submissions/robodojo-demo` 和本轮 wheel，将 wheel 放在 `wheels/`。执行器复用 V06 的独立编排解释器，仿真和无权重 policy 继续使用各自 V05 环境。

```bash
cd /mnt/openplatform-v05/work/v07-accepted
mkdir -p pure logs
for f in wheels/*.whl; do python3 -m zipfile -e "$f" pure; done
V07_WORK="$PWD" bash docs/integrations/robodojo-v07/run-gpu.sh
python3 docs/integrations/robodojo-v07/check-evidence.py evidence > evidence/verification.json
```

每条实际 CLI 命令和生成的 YAML 保存在 `evidence/{off,on}/`。注入脚本在真实动作写出后，根据本次启动的 policy URL 和运行 ID 找到仿真 Python，再向该进程发 SIGKILL。取消用 SIGINT 通知本次 OpenPlatform CLI。持续使用相同的原生视频配置并保留两组视频。采集关闭组关闭动作和帧索引、policy 调用与 RGB 数组保存，保留握手、episode 状态和原生结果。

## 从动作到帧

从 attempt 的 `native/episode-index.json` 选 episode，再用 `episode_id` 查 `events.jsonl`。使用 `executed_action.policy_call_id` 和 `observation_call_id` 关联同 episode 的请求 `seq`。前者指向生成该 chunk 的请求，后者指向这一步之前的观测更新。返回 chunk 的动作在 `chunk` 事件，确认执行的动作在 `executed_action` 事件，`index` 为 chunk 内零基索引。

用动作的 `control_step` 和 `completed_control_step` 分别查同 episode 的动作前、后 `video_frame`。帧号取自原生写入器的实际写入计数，按相机记录零基索引。最后一步的帧由原生终止分支抓取。相机文件名与解码验证结果见 episode 索引。帧记录保留同时取得的 state。动作记录含原生仿真物理步计数前后值、主机单调时钟和动作调用耗时。采集只读取原生计数与已有观测。

动作值来自成功返回且原生控制计数前进的 `take_action` 调用。物理步内插后的控制目标为 `unavailable`。中断的调用可能已经做了部分物理工作，无法确认完成的动作不会进入已执行轨迹。原生未封装的视频索引为 `unavailable`。原生评分分母来自 `_result.json`，故障时缺失的分母标为 `unavailable`。

SDK 推理异常时，bridge 先记录 policy 错误并关闭连接，然后退出独立仿真进程。NativeRuntime 回收该进程组内的视频写入进程。这样避开了首轮 GPU 注入暴露的 Kit 隐式退出阻塞。已经完成的原生结果和视频继续由 run 存储保留。

`collection.observations: true` 会单独保存成功更新的 RGB 数组为 NPZ，使用 `observation_images` 事件关联文件。开销对照两组都保持默认关闭。单次配对的耗时差含同机运行抖动，按实测报告。
