"""Independent RTX fixture using a dropped sphere and two isolated ARX link meshes."""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    from isaacsim import SimulationApp

    _app = SimulationApp({"headless": True})
    try:
        run(args.asset, args.output)
    except Exception:
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)
    # Kit can hang during implicit shutdown of this standalone fixture.
    # The JSON evidence has been closed and checked at this point.
    print("RTX signal fixture passed", flush=True)
    os._exit(0)


def run(asset, output):
    import isaaclab.sim as sim_utils
    import omni.usd
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from openplatform.benchmarks.robodojo.safety import ENV_ROOT, PhysxSignals

    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=0.004, device="cuda:0"))
    stage = omni.usd.get_context().get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)

    def place(schema, position):
        UsdGeom.Xformable(schema).AddTranslateOp().Set(Gf.Vec3d(*position))
        return schema.GetPrim()

    def rigid(prim, *, fixed=False):
        body = UsdPhysics.RigidBodyAPI.Apply(prim)
        body.CreateKinematicEnabledAttr(fixed)
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(1.0)

    table = UsdGeom.Cube.Define(stage, f"{ENV_ROOT}/Table")
    table.CreateSizeAttr(1.0)
    table_prim = place(table, (0.0, 0.0, -0.05))
    UsdGeom.Xformable(table).AddScaleOp().Set(Gf.Vec3d(4.0, 4.0, 0.1))
    UsdPhysics.CollisionAPI.Apply(table_prim)

    sphere = UsdGeom.Sphere.Define(stage, f"{ENV_ROOT}/drop")
    sphere.CreateRadiusAttr(0.05)
    sphere_prim = place(sphere, (0.0, 0.0, 0.38))
    UsdPhysics.CollisionAPI.Apply(sphere_prim)
    rigid(sphere_prim)

    source = Usd.Stage.Open(str(asset))
    link = source.GetPrimAtPath("/X5A/link3")
    collider = next(prim for prim in Usd.PrimRange(link) if prim.HasAPI(UsdPhysics.CollisionAPI))
    mesh_prim = next(
        prim
        for prim in Usd.PrimRange(collider, Usd.TraverseInstanceProxies())
        if prim.IsA(UsdGeom.Mesh)
    )
    mesh = UsdGeom.Mesh(mesh_prim)
    xforms = UsdGeom.XformCache()
    to_link = xforms.GetLocalToWorldTransform(mesh_prim) * (
        xforms.GetLocalToWorldTransform(link).GetInverse()
    )
    points = [to_link.Transform(Gf.Vec3d(*point)) for point in mesh.GetPointsAttr().Get()]
    center = Gf.Vec3d(
        *(
            (max(point[i] for point in points) + min(point[i] for point in points)) / 2
            for i in range(3)
        )
    )
    points = [Gf.Vec3f(*(point - center)) for point in points]
    height = max(p[2] for p in points) - min(p[2] for p in points)
    for index, z in enumerate((0.5, 0.8 + height)):
        root = UsdGeom.Xform.Define(stage, f"{ENV_ROOT}/robot{index}/link3")
        rigid(place(root, (0.8, 0.0, z)), fixed=index == 0)
        shape = UsdGeom.Mesh.Define(stage, root.GetPath().AppendChild("collision"))
        shape.CreatePointsAttr(points)
        shape.CreateFaceVertexCountsAttr(mesh.GetFaceVertexCountsAttr().Get())
        shape.CreateFaceVertexIndicesAttr(mesh.GetFaceVertexIndicesAttr().Get())
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(shape.GetPrim()).CreateApproximationAttr("convexHull")

    reader = PhysxSignals()
    reader.enable_reports(stage)
    sim.reset()
    coverage = reader.bind(stage, {"drop": (f"{ENV_ROOT}/drop", "target")})
    steps = []
    previous = reader.poses()
    dts = []
    subscription = reader.physx.subscribe_physics_on_step_events(dts.append, False, 0)
    for step in range(170):
        dts.clear()
        sim.step(render=False)
        assert len(dts) == 1, dts
        dt = float(dts[0])
        pairs, collisions = reader.contacts(dt)
        poses = reader.poses()
        steps.append(
            {
                "step": step + 1,
                "dt_s": dt,
                "contacts": pairs,
                "self_collision": collisions,
                "poses": poses,
                "sleeping_body_ids": reader.sleeping(),
                "unresolved_resting_body_ids": reader.unresolved_support(pairs, previous, poses),
            }
        )
        previous = poses
    del subscription
    output.write_text(
        json.dumps({"coverage": coverage, "source_arx_mesh": str(mesh.GetPath()), "steps": steps})
        + "\n"
    )
    assert steps[0]["contacts"] == [] and steps[0]["self_collision"] == []
    assert not steps[0]["sleeping_body_ids"]
    assert not steps[0]["unresolved_resting_body_ids"]
    assert steps[0]["poses"]["drop"]["position_m"][2] > steps[60]["poses"]["drop"]["position_m"][2]
    assert any(
        {body["name"] for body in pair["bodies"]} == {"drop", "Table"}
        and pair["normal_force_n"] > 0
        for step in steps
        for pair in step["contacts"]
    )
    assert any(
        pair["kind"] == "cross_arm"
        and {body["arm"] for body in pair["bodies"]} == {"left", "right"}
        and pair["normal_force_n"] > 0
        for step in steps
        for pair in step["self_collision"]
    )


if __name__ == "__main__":
    main()
