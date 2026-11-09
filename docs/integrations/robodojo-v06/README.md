# V06 RoboDojo 原生入口对照

在 V05 验收服务器复用 RoboDojo Conda、资产和无权重 policy 环境。两路依次评测 `stack_bowls`、`arx_x5`、joint、seed 0、单环境，各请求一个有效 episode。仿真用物理 GPU 0，π0 推理用物理 GPU 1。对照范围是这组 smoke，按实际布局和重复编号配对。[对照协议](../robodojo-comparison.md) · [V05 配方](../robodojo-v05/README.md)

## CPU 契约

在仓库根目录用已有 Python 3.11 开发环境和 V00 的固定版 checkout 执行。fixture 用上游 `deploy.py` 经过真实 SDK loopback 调用，检查双 reset、chunk 内 `update_obs`、观测的键和值、关节顺序和提前截断。

```bash
OPENPLATFORM_TEST_ROBODOJO=/private/tmp/openplatform-v00-robodojo-f57e163 \
  /private/tmp/openplatform-v01-env/bin/python -m pytest -q
/private/tmp/openplatform-v01-env/bin/ruff check src sdk tests docs/integrations/robodojo-v06
/private/tmp/openplatform-v01-env/bin/ruff format --check src sdk tests docs/integrations/robodojo-v06
for f in docs/integrations/robodojo-v06/*.sh; do bash -n "$f"; done
UV_CACHE_DIR=/private/tmp/openplatform-v01-uv-cache \
  UV_PYTHON_INSTALL_DIR=/private/tmp/openplatform-v01-pythons \
  /private/tmp/openplatform-v01-tools/bin/uv build --offline --all-packages --wheel \
  --out-dir /private/tmp/openplatform-v06-build-verification
```

## 复用 Linux 环境

`common.sh` 默认使用 `~/openplatform-v05` 和 `/mnt/openplatform-v05/work/v06`。从仓库同步本目录、两个 `examples/submissions/robodojo-*` 示例和已构建的 wheel 到 `$W`，wheel 放在 `wheels/`。执行前将两个提交模板中的 `/path/to/robodojo-work` 路径填写为本机实际路径。源码 checkout 为 `$RD`，仿真和 demo policy 的解释器在 `$V/envs`，π0 解释器在 `$W/pi0/.venv/bin/python`。`setup-op.sh` 建独立编排环境，`pure/` 提供 core 和 SDK 源码供原有仿真与 policy 环境使用。仿真仍使用 V05 Conda 的 NumPy、MessagePack、WebSocket 和渲染依赖。

```bash
cd /mnt/openplatform-v05/work/v06
source docs/integrations/robodojo-v06/common.sh
mkdir -p evidence logs pure
for f in wheels/*.whl; do python3 -m zipfile -e "$f" pure; done
sha256sum wheels/*.whl > evidence/wheels-sha256.txt
bash docs/integrations/robodojo-v06/setup-op.sh
```

无权重示例用 `checkpoint: null`。π0 示例用同一发布目录的 `60000` 步，YAML 保留相对于模型 checkout 的路径。用上游 OpenPI lock 准备独立推理环境，补齐运行时导入用的依赖并固定版本。将下载目录链接到官方 checkpoint 查找路径，保留原始权重文件。

```bash
export UV_CACHE_DIR="$W/uv-cache" UV_PROJECT_ENVIRONMENT="$W/pi0/.venv"
export UV_LINK_MODE=hardlink UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5 GIT_LFS_SKIP_SMUDGE=1
(
  cd "$RD/XPolicyLab/policy/Pi_0/openpi"
  uv sync --locked --no-dev --python "$V/envs/RoboDojo/bin/python" --group lerobot
)
uv pip install --python "$W/pi0/.venv/bin/python" --no-deps -e "$RD/XPolicyLab" \
  msgpack-numpy==0.4.8 h5py==3.16.0
uv pip install --python "$W/pi0/.venv/bin/python" \
  pytest==8.3.5 iniconfig==2.1.0 pluggy==1.6.0
path=ckpt/RoboDojo/Pi_0/RoboDojo-sim-arx_x5-joint-0/60000
HF_HUB_DOWNLOAD_TIMEOUT=120 HF_HUB_ETAG_TIMEOUT=90 "$V/tools/hf/bin/hf" download \
  RoboDojo-Benchmark/RoboDojo --repo-type dataset \
  --revision 786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33 --include "$path/**" \
  --exclude "$path/train_state/**" --local-dir "$W/checkpoints" --max-workers 8
bash docs/integrations/robodojo-v06/fetch-tokenizer.sh
bash docs/integrations/robodojo-v06/prepare-checkpoint.sh
```

## 执行和配对

各命令使用新证据目录名。`run-official.sh` 调用上游 `scripts/robodojo.sh eval`。官方 client 只加取证包装，原生 transport 仍照常调用。`run-openplatform.sh` 调用 `openplatform eval ... --bench robodojo --preset smoke`，runner 启动 SDK policy，再通过原生 `eval_policy.sh` 启动仿真。命令、子进程日志、退出码、原始结果和三路视频保存在各自目录。SDK 路径还保留 resolved config、attempt 状态、产物索引和公共指标。

```bash
bash docs/integrations/robodojo-v06/run-official.sh demo_policy official-demo-01
bash docs/integrations/robodojo-v06/run-openplatform.sh robodojo-demo op-demo-01

export XLA_FLAGS='--xla_gpu_autotune_level=0 --xla_gpu_exclude_nondeterministic_ops=true'
CUDA_VISIBLE_DEVICES=1 PYTHONPATH="$RD:$RD/XPolicyLab:$W/pure" \
  "$W/pi0/.venv/bin/python" docs/integrations/robodojo-v06/replay.py "$RD" \
  "$(cat evidence/pi0-provenance/checkpoint-path.txt)" \
  "$W/examples/submissions/robodojo-pi0" "$W/evidence/pi0-replay-03"

bash docs/integrations/robodojo-v06/run-official.sh Pi_0 official-pi0-01
bash docs/integrations/robodojo-v06/run-openplatform.sh robodojo-pi0 op-pi0-01
```

在两路均完成后，对比 `simulator-pip.txt`、`input-sha256.txt`、`assets-revision.txt`、`robodojo.txt` 和 Vulkan／仿真选项。检查实际 policy 配置和 π0 重放；确认对齐情况后填写 `conditions.json`，匹配时写入 `comparison_status: paired`。按下列命令生成逐 episode 对照。`tag` 选择 `demo` 或 `pi0`。

```bash
tag=demo
native="$(find "evidence/op-$tag-01/runs" -type d -name native)"
python3 docs/integrations/robodojo-v06/compare.py "evidence/official-$tag-01" "$native" \
  "$RD/Assets/Eval_Layout/RoboDojo/arx_x5/0" "evidence/$tag-conditions.json" \
  >"evidence/$tag-comparison.json"
```

`events.jsonl` 区分模型请求、返回 chunk 和 `take_action` 成功返回的控制步。观测包含数组 dtype、shape、内容摘要和小数组值。固定 π0 重放另外保留编码及归一化后的输入摘要、token、两组完整动作数组与容差。闭环对照的动作证据位于 `take_action` 输入边界，物理步、裁剪后控制目标及视频帧关联为 `unavailable`。

默认 XLA 设置的固定输入重放已保留在 `pi0-replay-02`。两路输入摘要完全相同，动作最大绝对差为 `0.01752596`，超出 `rtol=atol=1e-5`。两路采用上述 XLA 确定性设置重跑后，`pi0-replay-03` 的两个 `float64 (50, 14)` chunk 逐项相等。闭环使用同样的确定性设置。OpenPI 以 JAX 默认 key 0 启动，加载 `bfloat16` 参数，模型内采样与原生 reset 逻辑保持原样。[OpenXLA GPU 确定性文档](https://openxla.org/xla/determinism)
