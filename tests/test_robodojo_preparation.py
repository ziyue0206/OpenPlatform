import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openplatform.benchmark import Registry
from openplatform.benchmarks.robodojo import preparation
from openplatform.benchmarks.robodojo.verify import DOCKER_RECEIPT, record_docker_image, verify
from openplatform.cli import app

runner = CliRunner()


def test_manifest_keeps_docker_preparation_and_enables_native_eval():
    registry = Registry()
    manifest = registry.describe("robodojo")
    assert manifest.status == "native_eval"
    assert set(manifest.recipes) == {"conda", "docker"}
    assert manifest.recipes["conda"].eval_enabled
    assert not manifest.recipes["docker"].eval_enabled
    assert type(registry.load(manifest)).__name__ == "RoboDojoPlugin"


def test_docker_plan_resolves_checkout_and_cache_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "checkout with spaces"
    assets = root / "Assets"
    layout = assets / "Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_12.json"
    layout.parent.mkdir(parents=True)
    layout.write_text("{}")
    (layout.parent / "stack_bowls_extra.json").write_text("{}")
    generated = assets / "Robots/x5/curobo.yml"
    generated.parent.mkdir(parents=True)
    generated.write_text(f"urdf_path: {assets}/Robots/x5/X5A.urdf\n")
    pins = json.loads((preparation.PACKAGE / "versions.json").read_text())
    (root / preparation.ASSET_MARKER).write_text(pins["assets"]["revision"] + "\n")

    def probe(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse"):
            assert cwd == root
            return pins["robodojo"]["revision"]
        if argv[:2] == ("git", "submodule"):
            return "\n".join(
                f" {revision} {path}" for path, revision in pins["robodojo"]["submodules"].items()
            )
        if argv[:2] == ("docker", "info"):
            return '{"nvidia": {}}'
        if argv[:3] == ("docker", "image", "inspect"):
            return "sha256:fixture"
        if argv[:2] == ("conda", "env"):
            return '{"envs": ["/conda/demo-policy"]}'
        if argv == ("nvidia-smi", "-L"):
            return "GPU 0: test fixture"
        pytest.fail(f"unexpected probe {argv}")

    monkeypatch.setattr(preparation, "_probe", probe)
    monkeypatch.setattr(preparation.shutil, "which", lambda tool: f"/test/bin/{tool}")
    monkeypatch.setattr(preparation.platform, "system", lambda: "Linux")
    monkeypatch.setattr(preparation.platform, "machine", lambda: "x86_64")
    result = runner.invoke(
        app,
        ["prepare", "--bench", "robodojo", "--recipe", "docker", "--checkout", root.name],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["ready"] and plan["issues"] == []
    assert plan["layouts"] == [str(layout)]
    assert plan["checkout"] == str(root)
    assert plan["docker_volume_args"] == [
        "-v",
        f"{assets}:/workspace/RoboDojo/Assets:ro",
        "-v",
        f"{assets}:{assets}:ro",
        "-v",
        f"{root}/eval_result:/workspace/RoboDojo/eval_result",
    ]
    assert plan["image_cache"]["status"] == "present_unverified"
    assert plan["image_cache"]["id"] == "sha256:fixture"
    assert plan["commands"][-1]["argv"][2:] == [
        "docker-smoke",
        str(root),
        "<new-absolute-evidence-dir>",
    ]
    assert plan["commands"][-1]["env"] == {"ROBODOJO_SKIP_BUILD": "0"}
    assert plan["commands"][-1]["cached_wheel_alternative"][0]["argv"][1].endswith(
        "build-cached-torch.sh"
    )
    (root / DOCKER_RECEIPT).write_text(
        json.dumps(
            {
                "image_id": "sha256:fixture",
                "robodojo_revision": pins["robodojo"]["revision"],
                "asset_revision": pins["assets"]["revision"],
                "effective_episodes": 1,
                "layout_id": 12,
            }
        )
    )
    verified = preparation.inspect("docker", root)
    assert verified["image_cache"]["status"] == "present_verified"
    assert verified["commands"][-1]["env"] == {
        "ROBODOJO_SKIP_BUILD": "1",
        "ROBODOJO_EXPECTED_IMAGE_ID": "sha256:fixture",
    }
    assert not (root / "eval_result").exists()


def test_doctor_reports_missing_resources_and_prepare_still_prints_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, "_probe", lambda *args, **kwargs: None)
    monkeypatch.setattr(preparation.shutil, "which", lambda _: None)
    args = ["--bench", "robodojo", "--recipe", "docker", "--checkout", str(tmp_path / "missing")]
    doctor = runner.invoke(app, ["doctor", *args])
    assert doctor.exit_code == 1, doctor.output
    report = json.loads(doctor.stdout)
    assert not report["ready"]
    assert "commands" not in report
    assert report["image_cache"]["status"] == "unchecked"
    for message in (
        "missing executable: conda",
        "wrong revision",
        "asset revision",
        "stack_bowls layouts",
        "stale generated paths",
        "Docker daemon unavailable",
        "result directory is not writable",
    ):
        assert any(message in issue for issue in report["issues"])
    prepare = runner.invoke(app, ["prepare", *args])
    assert prepare.exit_code == 0
    assert json.loads(prepare.stdout)["commands"][0]["argv"][2] == "source"
    assert not (tmp_path / "missing").exists()
    unknown = runner.invoke(app, ["prepare", *args, "--recipe", "invalid"])
    assert unknown.exit_code == 2 and "unknown recipe" in unknown.stderr
    native = runner.invoke(app, ["doctor", *args, "--recipe", "conda"])
    assert native.exit_code == 1
    assert "missing rendering library: libGLU.so.1" in json.loads(native.stdout)["issues"]


def test_native_evidence_uses_actual_layout_and_requires_all_cameras(tmp_path, monkeypatch):
    result = tmp_path / "_result.json"
    result.write_text(
        json.dumps(
            {
                "eval_time": 1,
                "success_rate": 0,
                "score": 10,
                "details": {"0": {"layout_id": 9, "success": False, "score": 0.1}},
            }
        )
    )
    for camera in ("cam_head", "cam_left_wrist", "cam_right_wrist"):
        (tmp_path / f"episode_0000000_{camera}_test.mp4").write_bytes(b"fixture")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            0,
            '{"streams":[{"width":320,"height":240,"nb_read_frames":"2"}]}',
        ),
    )
    verified = verify(tmp_path)
    assert verified["layout_id"] == 9
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (tmp_path / "image.json").write_text('[{"Id":"sha256:fixture"}]')
    (tmp_path / "robodojo.txt").write_text("fixture-revision\n")
    (tmp_path / "assets-revision.txt").write_text("fixture-assets\n")
    record_docker_image(tmp_path, checkout, verified)
    assert json.loads((checkout / DOCKER_RECEIPT).read_text()) == {
        "image_id": "sha256:fixture",
        "robodojo_revision": "fixture-revision",
        "asset_revision": "fixture-assets",
        "layout_id": 9,
        "effective_episodes": 1,
    }
    (tmp_path / "episode_0000000_cam_head_test.mp4").unlink()
    with pytest.raises(ValueError, match="cam_head"):
        verify(tmp_path)


def test_shell_recipe_parses():
    script = Path(preparation.__file__).with_name("recipe.sh")
    subprocess.run(["bash", "-n", script], check=True)
    subprocess.run([sys.executable, "-m", "py_compile", script.with_name("verify.py")], check=True)


def test_full_native_coverage_uses_each_episode_camera_index(tmp_path, monkeypatch):
    raw = {
        "eval_time": 2,
        "success_rate": 0.5,
        "score": 50,
        "details": {
            "0": {"layout_id": 4, "success": True, "score": 1.0},
            "1": {"layout_id": 7, "success": False, "score": 0.0},
        },
    }
    (tmp_path / "_result.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="expected 25 effective episodes"):
        verify(tmp_path, videos=False, expected_episodes=25)

    def cameras(_, index):
        return {"cam_head": {"file": f"episode_{index:07d}_cam_head_test.mp4"}}

    from openplatform.benchmarks.robodojo import verify as native_verify

    monkeypatch.setattr(native_verify, "_verify_cameras", cameras)
    verified = verify(tmp_path, expected_episodes=2)
    assert verified["layout_ids"] == [4, 7]
    assert verified["episodes"]["1"]["cameras"]["cam_head"]["file"].startswith("episode_0000001")
