"""Mangaka USD Scene + Pose ComfyUI custom nodes.

Four nodes that turn the Python-only USD scene + pose library into a
fully-self-contained ComfyUI workflow. Install this directory under
ComfyUI/custom_nodes/MangakaUSDScene/ and restart.

Node graph:
    MangakaUSDLoader(usd_path)
       └── scene (opaque)
           ├── MangakaCameraView(scene, ox,oy,oz, tx,ty,tz, fov, w, h)
           │      └── depth IMAGE, canny IMAGE, camera (opaque pass-through)
           │
           └── MangakaPoseFromLibrary(pose_name, px,py,pz, scale)
                  └── pose3d (opaque)
                      └── MangakaProjectPose(pose3d, camera)
                            └── openpose IMAGE
"""
from __future__ import annotations
import json, math
from pathlib import Path

import numpy as np
import torch
import trimesh
from pxr import Usd, UsdGeom, UsdLux, UsdShade, Gf, Sdf
from PIL import Image, ImageDraw, ImageFilter


ASSET_ROOT = Path(__file__).parent.parent.parent / "input" / "arc01-assets" / "usd"
# ComfyUI input root is two levels above custom_nodes; fall back to scanning.
def _find_usd_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        cand = p / "input" / "arc01-assets" / "usd"
        if cand.exists():
            return cand
        cand2 = p / "input" / "arc01-scenes"
        if cand2.exists():
            return cand2
    return Path(__file__).resolve().parent / "usd"


USD_ROOT = _find_usd_root()


# ── helpers ────────────────────────────────────────────────────────────────
def _arr_to_image(arr_u8: np.ndarray) -> torch.Tensor:
    """Convert HxW uint8 grayscale to ComfyUI IMAGE tensor (1, H, W, 3)."""
    if arr_u8.ndim == 2:
        rgb = np.stack([arr_u8, arr_u8, arr_u8], axis=-1)
    else:
        rgb = arr_u8
    return torch.from_numpy(rgb.astype(np.float32) / 255.0)[None, ...]


def _pil_to_image(im: Image.Image) -> torch.Tensor:
    arr = np.array(im.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


# ── 1. MangakaUSDLoader ────────────────────────────────────────────────────
class MangakaUSDLoader:
    @classmethod
    def INPUT_TYPES(cls):
        choices = sorted([p.stem for p in USD_ROOT.glob("*.usda")])
        if not choices:
            choices = ["(no .usda found)"]
        return {"required": {"scene_name": (choices,)}}

    RETURN_TYPES = ("MANGAKA_SCENE",)
    RETURN_NAMES = ("scene",)
    FUNCTION = "load"
    CATEGORY = "mangaka/usd"

    def load(self, scene_name):
        path = USD_ROOT / f"{scene_name}.usda"
        stage = Usd.Stage.Open(str(path))
        # Walk every Mesh prim and collect verts + faces
        all_verts = []
        all_faces = []
        for prim in stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            pts = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
            if pts is None or len(pts) == 0:
                continue
            face_idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
            face_cnt = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int64)
            # We assume triangles (build scripts always emit 3-vertex faces)
            tris = face_idx.reshape(-1, 3)
            offset = sum(len(v) for v in all_verts)
            all_faces.append(tris + offset)
            all_verts.append(pts)
        verts = np.concatenate(all_verts, axis=0) if all_verts else np.zeros((0, 3))
        faces = np.concatenate(all_faces, axis=0) if all_faces else np.zeros((0, 3), dtype=np.int64)
        scene_mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        return ({
            "name": scene_name,
            "mesh": scene_mesh,
            "n_verts": len(verts),
            "n_faces": len(faces),
        },)


# ── 2. MangakaCameraView ───────────────────────────────────────────────────
class MangakaCameraView:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "scene":     ("MANGAKA_SCENE",),
            "origin_x":  ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.1}),
            "origin_y":  ("FLOAT", {"default": 1.5, "min": -100.0, "max": 100.0, "step": 0.1}),
            "origin_z":  ("FLOAT", {"default": 1.3, "min": -100.0, "max": 100.0, "step": 0.1}),
            "target_x":  ("FLOAT", {"default": 3.7, "min": -100.0, "max": 100.0, "step": 0.1}),
            "target_y":  ("FLOAT", {"default": 0.85, "min": -100.0, "max": 100.0, "step": 0.1}),
            "target_z":  ("FLOAT", {"default": 2.1, "min": -100.0, "max": 100.0, "step": 0.1}),
            "fov_deg":   ("FLOAT", {"default": 50.0, "min": 10.0, "max": 150.0, "step": 0.5}),
            "width":     ("INT",   {"default": 1216, "min": 256, "max": 2048, "step": 8}),
            "height":    ("INT",   {"default": 832,  "min": 256, "max": 2048, "step": 8}),
            "render_resolution": ("INT", {"default": 608, "min": 128, "max": 1216, "step": 16,
                                            "tooltip": "raycast resolution; upscaled to width/height with nearest-neighbour"}),
        }}

    RETURN_TYPES = ("IMAGE", "IMAGE", "MANGAKA_CAMERA")
    RETURN_NAMES = ("depth", "canny", "camera")
    FUNCTION = "render"
    CATEGORY = "mangaka/usd"

    def render(self, scene, origin_x, origin_y, origin_z, target_x, target_y, target_z,
                fov_deg, width, height, render_resolution):
        mesh = scene["mesh"]
        o = np.array([origin_x, origin_y, origin_z], dtype=np.float64)
        t = np.array([target_x, target_y, target_z], dtype=np.float64)
        forward = t - o; forward /= np.linalg.norm(forward) + 1e-9
        right = np.cross(forward, [0, 1, 0])
        right /= np.linalg.norm(right) + 1e-9
        up = np.cross(right, forward)

        # Render at lower resolution for speed
        aspect = width / height
        rH = render_resolution
        rW = int(rH * aspect)
        fov = np.radians(fov_deg)
        half_h = np.tan(fov / 2)
        half_w = half_h * aspect

        xs = np.linspace(-half_w, half_w, rW)
        ys = np.linspace(half_h, -half_h, rH)
        gx, gy = np.meshgrid(xs, ys)
        dirs = (forward[None, None, :]
                + gx[..., None] * right[None, None, :]
                + gy[..., None] * up[None, None, :])
        dirs /= np.linalg.norm(dirs, axis=2, keepdims=True) + 1e-9
        df = dirs.reshape(-1, 3)
        orig = np.tile(o, (df.shape[0], 1))
        hits, idx_ray, _ = mesh.ray.intersects_location(
            ray_origins=orig, ray_directions=df, multiple_hits=False,
        )
        depth = np.full(df.shape[0], np.inf, dtype=np.float64)
        if len(hits):
            d = np.linalg.norm(hits - orig[idx_ray], axis=1)
            np.minimum.at(depth, idx_ray, d)
        depth = depth.reshape(rH, rW)
        finite = depth[np.isfinite(depth)]
        far = float(np.percentile(finite, 99)) if len(finite) else 15.0
        depth = np.where(np.isfinite(depth), depth, far)
        near = float(depth.min())
        depth_u8 = ((far - depth) / max(1e-6, far - near) * 255).clip(0, 255).astype(np.uint8)

        # Upscale to target width × height
        d_im = Image.fromarray(depth_u8).resize((width, height), Image.NEAREST)
        # Canny via Pillow FIND_EDGES
        c_im = d_im.filter(ImageFilter.FIND_EDGES)
        c_arr = np.array(c_im)
        c_arr = (c_arr > 30).astype(np.uint8) * 255
        depth_t = _pil_to_image(d_im)
        canny_t = _pil_to_image(Image.fromarray(c_arr))
        camera = {
            "origin": [origin_x, origin_y, origin_z],
            "target": [target_x, target_y, target_z],
            "fov_deg": fov_deg,
            "width": width, "height": height,
        }
        return (depth_t, canny_t, camera)


# ── Pose library (mirrored from character-rig.py) ──────────────────────────
KP_NAMES = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist",
            "l_shoulder", "l_elbow", "l_wrist", "r_hip", "r_knee", "r_ankle",
            "l_hip", "l_knee", "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear"]
KP_COLORS = [(255,0,0),(255,85,0),(255,170,0),(255,255,0),(170,255,0),
             (85,255,0),(0,255,0),(0,255,85),(0,255,170),(0,255,255),
             (0,170,255),(0,85,255),(0,0,255),(85,0,255),(170,0,255),
             (255,0,255),(255,0,170),(255,0,85)]
LIMBS = [(0,1),(1,2),(2,3),(3,4),(1,5),(5,6),(6,7),(1,8),(8,9),
         (9,10),(1,11),(11,12),(12,13),(0,14),(0,15),(14,16),(15,17)]
LIMB_COLORS = [(255,0,0),(255,85,0),(255,170,0),(255,255,0),(170,255,0),
               (85,255,0),(0,255,0),(0,255,85),(0,255,170),(0,255,255),
               (0,170,255),(0,85,255),(0,0,255),(85,0,255),(170,0,255),
               (255,0,255),(255,0,170)]
SEGMENTS = {"neck_to_head":0.20,"shoulder_width":0.40,"shoulder_to_elbow":0.30,
            "elbow_to_wrist":0.28,"hip_width":0.30,"hip_to_knee":0.45,
            "knee_to_ankle":0.42,"torso_height":0.60,"head_radius":0.10}

def _deg(d): return math.radians(d)
def _rotx(a):
    c,s=math.cos(a),math.sin(a)
    return np.array([[1,0,0],[0,c,-s],[0,s,c]])

def _eyes_ears(nose):
    return {
        "r_eye": nose + np.array([-0.04, 0.03, 0.04]),
        "l_eye": nose + np.array([ 0.04, 0.03, 0.04]),
        "r_ear": nose + np.array([-0.08, 0.04, -0.02]),
        "l_ear": nose + np.array([ 0.08, 0.04, -0.02]),
    }

def pose_anxious_at_desk(p):
    s = SEGMENTS
    pose = {}
    torso_dir = _rotx(_deg(-5)) @ np.array([0, 1, 0])
    neck = p + torso_dir * s["torso_height"]; pose["neck"] = neck
    head_dir = _rotx(_deg(-15)) @ torso_dir
    pose["nose"] = neck + head_dir * s["neck_to_head"]; pose.update(_eyes_ears(pose["nose"]))
    sh_y = neck[1] + 0.02
    pose["r_shoulder"] = np.array([neck[0] - s["shoulder_width"]/2, sh_y, neck[2]])
    pose["l_shoulder"] = np.array([neck[0] + s["shoulder_width"]/2, sh_y, neck[2]])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([-0.05,-0.15,0.20])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0.05, 0.00,0.30])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([ 0.05,-0.15,0.18])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([0.00, 0.00,0.20])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0.02,-0.05,s["hip_to_knee"]])
    pose["l_knee"]  = pose["l_hip"] + np.array([-0.02,-0.05,s["hip_to_knee"]])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-s["knee_to_ankle"],0.05])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],0.05])
    return pose

def pose_standing_facing_camera(p):
    s = SEGMENTS
    pose = {}
    neck = p + np.array([0, s["torso_height"], 0]); pose["neck"] = neck
    pose["nose"] = neck + np.array([0, s["neck_to_head"], 0.02]); pose.update(_eyes_ears(pose["nose"]))
    pose["r_shoulder"] = neck + np.array([-s["shoulder_width"]/2,0.02,0])
    pose["l_shoulder"] = neck + np.array([ s["shoulder_width"]/2,0.02,0])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([-0.02,-s["shoulder_to_elbow"],0])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0,-s["elbow_to_wrist"],0])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([ 0.02,-s["shoulder_to_elbow"],0])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([0,-s["elbow_to_wrist"],0])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["l_knee"]  = pose["l_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-s["knee_to_ankle"],0])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],0])
    return pose

def pose_looking_at_phone(p):
    s = SEGMENTS
    pose = {}
    neck = p + np.array([0, s["torso_height"], 0]); pose["neck"] = neck
    head_dir = _rotx(_deg(-25)) @ np.array([0, 1, 0])
    pose["nose"] = neck + head_dir * s["neck_to_head"]; pose.update(_eyes_ears(pose["nose"]))
    pose["r_shoulder"] = neck + np.array([-s["shoulder_width"]/2,0,0])
    pose["l_shoulder"] = neck + np.array([ s["shoulder_width"]/2,0,0])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([0.02,-0.20,0.20])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0.05,-0.05,0.20])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([-0.02,-0.20,0.20])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([-0.05,-0.05,0.20])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["l_knee"]  = pose["l_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-s["knee_to_ankle"],0])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],0])
    return pose

def pose_sleeping_at_desk(p):
    s = SEGMENTS
    pose = {}
    neck = p + _rotx(_deg(-40)) @ np.array([0, s["torso_height"], 0]); pose["neck"] = neck
    pose["nose"]  = neck + np.array([0.05, 0.0, 0.20]); pose.update(_eyes_ears(pose["nose"]))
    pose["r_shoulder"] = neck + np.array([-s["shoulder_width"]/2, 0, 0])
    pose["l_shoulder"] = neck + np.array([ s["shoulder_width"]/2, 0, 0])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([-0.05,-0.05,0.30])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0.10,  0.00,0.20])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([ 0.05,-0.05,0.30])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([-0.10, 0.00,0.20])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0.02,-0.05,s["hip_to_knee"]])
    pose["l_knee"]  = pose["l_hip"] + np.array([-0.02,-0.05,s["hip_to_knee"]])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-s["knee_to_ankle"],0.05])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],0.05])
    return pose

def pose_talking_animated(p):
    s = SEGMENTS
    pose = {}
    neck = p + np.array([0, s["torso_height"], 0]); pose["neck"] = neck
    pose["nose"] = neck + np.array([0.03, s["neck_to_head"], 0.04]); pose.update(_eyes_ears(pose["nose"]))
    pose["r_shoulder"] = neck + np.array([-s["shoulder_width"]/2,0.02,0])
    pose["l_shoulder"] = neck + np.array([ s["shoulder_width"]/2,0.02,0])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([-0.10,-0.05,0.15])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0.10, 0.20,0.10])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([ 0.02,-s["shoulder_to_elbow"],0])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([0,-s["elbow_to_wrist"],0])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["l_knee"]  = pose["l_hip"] + np.array([0,-s["hip_to_knee"],0])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-s["knee_to_ankle"],0])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],0])
    return pose

def pose_walking_running(p):
    s = SEGMENTS
    pose = {}
    neck = p + _rotx(_deg(-8)) @ np.array([0, s["torso_height"], 0]); pose["neck"] = neck
    pose["nose"] = neck + np.array([0, s["neck_to_head"], 0.04]); pose.update(_eyes_ears(pose["nose"]))
    pose["r_shoulder"] = neck + np.array([-s["shoulder_width"]/2,0,0])
    pose["l_shoulder"] = neck + np.array([ s["shoulder_width"]/2,0,0])
    pose["r_elbow"] = pose["r_shoulder"] + np.array([0.02,-0.15,0.20])
    pose["r_wrist"] = pose["r_elbow"]   + np.array([0,-0.05,0.20])
    pose["l_elbow"] = pose["l_shoulder"] + np.array([-0.02,-0.15,-0.20])
    pose["l_wrist"] = pose["l_elbow"]   + np.array([0,-0.05,-0.15])
    pose["r_hip"] = p + np.array([-s["hip_width"]/2,0,0])
    pose["l_hip"] = p + np.array([ s["hip_width"]/2,0,0])
    pose["r_knee"]  = pose["r_hip"] + np.array([0,-0.10,0.30])
    pose["r_ankle"] = pose["r_knee"] + np.array([0,-0.35,0.10])
    pose["l_knee"]  = pose["l_hip"] + np.array([0,-s["hip_to_knee"],-0.10])
    pose["l_ankle"] = pose["l_knee"] + np.array([0,-s["knee_to_ankle"],-0.20])
    return pose


POSE_LIBRARY = {
    "anxious_at_desk":        pose_anxious_at_desk,
    "standing_facing_camera": pose_standing_facing_camera,
    "looking_at_phone":       pose_looking_at_phone,
    "sleeping_at_desk":       pose_sleeping_at_desk,
    "talking_animated":       pose_talking_animated,
    "walking_running":        pose_walking_running,
}


# ── 3. MangakaPoseFromLibrary ──────────────────────────────────────────────
class MangakaPoseFromLibrary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pose_name":  (sorted(POSE_LIBRARY.keys()),),
            "pelvis_x":   ("FLOAT", {"default": 3.0, "min": -100.0, "max": 100.0, "step": 0.1}),
            "pelvis_y":   ("FLOAT", {"default": 0.5, "min": -100.0, "max": 100.0, "step": 0.1}),
            "pelvis_z":   ("FLOAT", {"default": 2.0, "min": -100.0, "max": 100.0, "step": 0.1}),
            "scale":      ("FLOAT", {"default": 1.0, "min": 0.1, "max": 3.0, "step": 0.05}),
        }}
    RETURN_TYPES = ("MANGAKA_POSE",)
    FUNCTION = "build"
    CATEGORY = "mangaka/usd"

    def build(self, pose_name, pelvis_x, pelvis_y, pelvis_z, scale):
        pelvis = np.array([pelvis_x, pelvis_y, pelvis_z])
        pose_fn = POSE_LIBRARY[pose_name]
        kp = pose_fn(pelvis)
        # Apply scale (relative to pelvis)
        if abs(scale - 1.0) > 1e-3:
            scaled = {}
            for name, p in kp.items():
                scaled[name] = pelvis + (p - pelvis) * scale
            kp = scaled
        return ({"keypoints": kp, "pose_name": pose_name},)


# ── 4. MangakaProjectPose ──────────────────────────────────────────────────
class MangakaProjectPose:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pose":   ("MANGAKA_POSE",),
            "camera": ("MANGAKA_CAMERA",),
        }}
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("openpose",)
    FUNCTION = "project"
    CATEGORY = "mangaka/usd"

    def project(self, pose, camera):
        kp = pose["keypoints"]
        o = np.array(camera["origin"]); t = np.array(camera["target"])
        forward = t - o; forward /= np.linalg.norm(forward) + 1e-9
        right = np.cross(forward, [0, 1, 0])
        right /= np.linalg.norm(right) + 1e-9
        up = np.cross(right, forward)
        W, H = camera["width"], camera["height"]
        fov = math.radians(camera["fov_deg"])
        half_h = math.tan(fov / 2)
        half_w = half_h * (W / H)

        pts2d = {}
        for name in KP_NAMES:
            if name not in kp:
                continue
            p3 = kp[name]
            diff = p3 - o
            z = float(diff @ forward)
            if z <= 1e-3:
                continue
            x_cam = float(diff @ right)
            y_cam = float(diff @ up)
            u = (x_cam / z + half_w) / (2 * half_w) * W
            v = (1.0 - (y_cam / z + half_h) / (2 * half_h)) * H
            if 0 <= u < W and 0 <= v < H:
                pts2d[name] = (u, v)

        img = Image.new("RGB", (W, H), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        for li, (i, j) in enumerate(LIMBS):
            ni, nj = KP_NAMES[i], KP_NAMES[j]
            if ni in pts2d and nj in pts2d:
                color = LIMB_COLORS[li] if li < len(LIMB_COLORS) else (255, 255, 255)
                draw.line([pts2d[ni], pts2d[nj]], fill=color, width=6)
        for idx, name in enumerate(KP_NAMES):
            if name in pts2d:
                u, v = pts2d[name]
                color = KP_COLORS[idx]
                draw.ellipse([u - 6, v - 6, u + 6, v + 6], fill=color)
        return (_pil_to_image(img),)


# ── 5. MangakaCharacterEmotion ─────────────────────────────────────────────
# Body posture and facial expression are independent axes — same posture can
# carry many emotions, same emotion can be paired with many postures. This
# node emits the emotion-specific prompt fragment (facial expression + body
# tension + symbolic cues like sweat / tears / blush) that gets appended to
# the panel prompt, leaving the openpose skeleton to handle posture only.

EMOTION_PROMPTS = {
    "neutral": (
        "neutral expression, calm eyes",
        "",
    ),
    "anxious": (
        "(anxious face:1.2), worried eyes, tight lips, slight sweat drop on temple, "
        "shadow under eyes, tense brow",
        "(tense:1.1), nervous",
    ),
    "shocked": (
        "(shocked expression:1.3), wide eyes, mouth slightly open, "
        "(surprised:1.2), dilated pupils, raised eyebrows, dramatic chiaroscuro",
        "(rigid posture:1.1), frozen",
    ),
    "sad": (
        "(sad face:1.2), tearful eyes, single tear sliding down cheek, "
        "downturned mouth, lowered gaze, soft shadow",
        "(slumped:1.1), heavy",
    ),
    "angry": (
        "(angry expression:1.3), furrowed brow, gritted teeth, eyes narrowed, "
        "vein on forehead, intense glare",
        "(tense:1.1), clenched",
    ),
    "joyful": (
        "(joyful smile:1.3), bright eyes, raised cheeks, mouth open in laughter, "
        "lifted brows, warm expression",
        "(light:1.1), open posture",
    ),
    "determined": (
        "(determined expression:1.2), focused eyes, set jaw, slight forward lean, "
        "intense gaze, brows slightly drawn",
        "(steady:1.1), grounded",
    ),
    "fearful": (
        "(fearful expression:1.3), wide trembling eyes, mouth slightly open, "
        "pale face, beads of sweat, lifted brows",
        "(small posture:1.1), curled",
    ),
    "contemplative": (
        "(contemplative expression:1.1), thoughtful eyes, slight smile, "
        "head slightly tilted, gentle gaze, soft shading",
        "quiet, still",
    ),
    "embarrassed": (
        "(embarrassed expression:1.2), blushing cheeks, averted gaze, "
        "small awkward smile, hand near face",
        "(flustered:1.1), restless",
    ),
}


class MangakaCharacterEmotion:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "emotion":   (sorted(EMOTION_PROMPTS.keys()), {"default": "neutral"}),
            "intensity": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.5, "step": 0.1}),
        }}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("face_prompt", "body_modifier_prompt")
    FUNCTION = "build"
    CATEGORY = "mangaka/usd"

    def build(self, emotion, intensity):
        face, body = EMOTION_PROMPTS.get(emotion, EMOTION_PROMPTS["neutral"])
        if intensity < 0.5:
            # Soften — drop weight emphasis
            face = face.replace(":1.3)", ":1.0)").replace(":1.2)", ":1.0)").replace(":1.1)", ":1.0)")
            body = body.replace(":1.1)", ":1.0)")
        elif intensity > 1.1:
            # Strengthen
            face = face.replace(":1.2)", ":1.4)").replace(":1.3)", ":1.5)")
        return (face, body)


# ── 6. MangakaPromptConcat ─────────────────────────────────────────────────
# Helper to combine base prompt + emotion fragment + body modifier in a
# single CLIPTextEncode upstream. ComfyUI has built-in ConditioningConcat
# but joining strings before encoding produces cleaner conditioning.
class MangakaPromptConcat:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "base":            ("STRING", {"multiline": True, "default": ""}),
            "face_emotion":    ("STRING", {"default": ""}),
            "body_modifier":   ("STRING", {"default": ""}),
            "extra_suffix":    ("STRING", {"default": "", "multiline": True}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "concat"
    CATEGORY = "mangaka/usd"

    def concat(self, base, face_emotion, body_modifier, extra_suffix):
        parts = [base, face_emotion, body_modifier, extra_suffix]
        return (", ".join(p.strip() for p in parts if p.strip()),)


# ── 7. MangakaShotComposition ──────────────────────────────────────────────
# Cinematic shot framing — given a subject (3D position) and a shot type,
# compute the camera origin/target/fov so the subject lands in the frame
# per film grammar conventions. Replaces hand-tuned xyz inputs.

SHOT_PRESETS = {
    # (subject_distance, height_offset_from_head_y, fov_deg, vertical_pitch_deg)
    # Camera lands at subject_head_y + height_offset (so seated and standing
    # characters both get correctly framed). vertical_pitch_deg = extra tilt.
    "ECU":  (0.5,  0.0,  35.0,  0.0),   # extreme close-up: eyes / mouth
    "CU":   (1.0,  0.0,  38.0,  0.0),   # close-up: face + shoulders
    "MS":   (1.8, -0.10, 45.0, -3.0),   # medium: waist up
    "MLS":  (2.6, -0.05, 50.0, -5.0),   # medium long: knees up
    "LS":   (4.0,  0.20, 55.0, -8.0),   # long: full figure, slight high angle
    "ELS":  (8.0,  0.50, 70.0, -2.0),   # extreme long: tiny figure
    "POV":  (0.0,  0.0,  60.0, -10.0),  # at character's eye, looking out
    "OTS":  (1.5, -0.10, 50.0,  0.0),   # over-the-shoulder
    "TWO":  (2.8,  0.0,  55.0, -3.0),   # two-shot
    "INSERT": (0.4, -0.3, 30.0, -25.0), # tight on object (phone, hand)
}

ANGLE_OFFSETS = {
    # (extra_height_offset, extra_pitch_deg, horizontal_yaw_deg)
    "eye_level":  (0.0,    0.0,  0.0),
    "high":       (1.5,   -25.0, 0.0),
    "low":        (-1.0,   25.0, 0.0),
    "overhead":   (3.0,   -70.0, 0.0),
    "worm":       (-1.4,   40.0, 0.0),
    "dutch_L":    (0.0,    0.0, -10.0),
    "dutch_R":    (0.0,    0.0,  10.0),
}

COMPOSITION_OFFSETS = {
    # Subject placement in frame (horizontal_frac, vertical_frac)
    "center":           (0.50, 0.50),
    "thirds_L":         (0.33, 0.50),
    "thirds_R":         (0.67, 0.50),
    "thirds_top_L":     (0.33, 0.33),
    "thirds_top_R":     (0.67, 0.33),
    "thirds_bot_L":     (0.33, 0.67),
    "thirds_bot_R":     (0.67, 0.67),
    "symmetry":         (0.50, 0.50),
    "triangular":       (0.50, 0.60),
    "negative_space_L": (0.70, 0.50),
    "negative_space_R": (0.30, 0.50),
}


class MangakaShotComposition:
    """Compute a camera that frames the subject per film-grammar conventions."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "subject_pose":  ("MANGAKA_POSE",),
            "shot_type":     (sorted(SHOT_PRESETS.keys()), {"default": "MS"}),
            "angle":         (sorted(ANGLE_OFFSETS.keys()), {"default": "eye_level"}),
            "rule":          (sorted(COMPOSITION_OFFSETS.keys()), {"default": "center"}),
            "subject_yaw_deg": ("FLOAT", {"default": 0.0, "min": -180.0, "max": 180.0, "step": 5.0,
                                            "tooltip": "yaw around subject — 0=facing camera, 90=profile right"}),
            "width":         ("INT", {"default": 1216, "min": 256, "max": 2048, "step": 8}),
            "height":        ("INT", {"default": 832,  "min": 256, "max": 2048, "step": 8}),
        }}
    RETURN_TYPES = ("MANGAKA_CAMERA_PARAMS",)
    RETURN_NAMES = ("camera_params",)
    FUNCTION = "compose"
    CATEGORY = "mangaka/usd"

    def compose(self, subject_pose, shot_type, angle, rule, subject_yaw_deg, width, height):
        kp = subject_pose["keypoints"]
        # Subject reference points
        pelvis = np.array(kp.get("pelvis_center") or kp["neck"] - np.array([0, 0.6, 0]))
        head = np.array(kp.get("nose", kp["neck"]))
        chest = (pelvis + head) / 2.0
        # subject facing direction: assume +z, modify by yaw
        yaw_rad = math.radians(subject_yaw_deg)
        face_dir = np.array([math.sin(yaw_rad), 0, math.cos(yaw_rad)])
        # opposite direction = where camera should sit relative to subject
        cam_dir_world = -face_dir  # from subject TOWARDS camera
        # Apply shot type preset (height_offset is relative to subject's HEAD)
        dist, height_off, fov, pitch_base = SHOT_PRESETS[shot_type]
        # Angle modifier
        h_off2, pitch2, yaw_off = ANGLE_OFFSETS[angle]
        # Camera height = subject head Y + shot preset offset + angle offset
        head_y = float(head[1])
        cam_y = head_y + height_off + h_off2
        # Camera origin = chest_xz + cam_dir * dist
        # Apply angle yaw modifier (rotate cam_dir around vertical axis)
        if abs(yaw_off) > 0.01:
            yaw_off_rad = math.radians(yaw_off)
            c, s = math.cos(yaw_off_rad), math.sin(yaw_off_rad)
            cd = cam_dir_world.copy()
            cam_dir_world = np.array([c * cd[0] + s * cd[2], cd[1], -s * cd[0] + c * cd[2]])

        cam_origin = chest + cam_dir_world * dist
        cam_origin[1] = cam_y  # override Y with computed height

        # Target = chest + composition offset (in screen space)
        # Compute target relative to camera frame: we want subject at COMPOSITION_OFFSETS
        # For simplicity: aim at chest, then nudge target laterally based on rule.
        target = chest.copy()
        h_frac, v_frac = COMPOSITION_OFFSETS[rule]
        # If subject should be off-center, shift target in screen space.
        # Approximation: shift target perpendicular to camera direction.
        forward = target - cam_origin
        forward_n = forward / (np.linalg.norm(forward) + 1e-9)
        right = np.cross(forward_n, [0, 1, 0])
        right /= np.linalg.norm(right) + 1e-9
        up = np.cross(right, forward_n)
        # Horizontal: 0.5 - h_frac (subject at 0.33 means target shifts +half-frame right)
        h_shift = (0.5 - h_frac) * 2.0 * np.tan(math.radians(fov / 2)) * dist
        v_shift = (0.5 - v_frac) * 2.0 * np.tan(math.radians(fov / 2)) * dist * (height / width)
        target = target + right * h_shift + up * v_shift

        # Apply additional pitch (camera tilt)
        total_pitch = pitch_base + pitch2
        if abs(total_pitch) > 0.01:
            pitch_rad = math.radians(total_pitch)
            # rotate target around camera origin by pitch around right axis
            rel = target - cam_origin
            c, s = math.cos(pitch_rad), math.sin(pitch_rad)
            # right is the rotation axis (camera-local)
            # Rodrigues: v_rot = v*cos + (axis × v)*sin + axis*(axis·v)*(1-cos)
            axis = right
            rel_new = (rel * c + np.cross(axis, rel) * s
                       + axis * (axis @ rel) * (1 - c))
            target = cam_origin + rel_new

        params = {
            "origin_x": float(cam_origin[0]),
            "origin_y": float(cam_origin[1]),
            "origin_z": float(cam_origin[2]),
            "target_x": float(target[0]),
            "target_y": float(target[1]),
            "target_z": float(target[2]),
            "fov_deg":  float(fov),
            "width":    int(width),
            "height":   int(height),
        }
        return (params,)


# ── 8. MangakaCameraViewFromParams ────────────────────────────────────────
# Convenience: take MANGAKA_CAMERA_PARAMS from MangakaShotComposition and
# render canny+depth, without re-typing the 6 xyz inputs.
class MangakaCameraViewFromParams:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "scene":        ("MANGAKA_SCENE",),
            "camera_params": ("MANGAKA_CAMERA_PARAMS",),
            "render_resolution": ("INT", {"default": 608, "min": 128, "max": 1216, "step": 16}),
        }}
    RETURN_TYPES = ("IMAGE", "IMAGE", "MANGAKA_CAMERA")
    RETURN_NAMES = ("depth", "canny", "camera")
    FUNCTION = "render"
    CATEGORY = "mangaka/usd"

    def render(self, scene, camera_params, render_resolution):
        cv = MangakaCameraView()
        return cv.render(
            scene,
            camera_params["origin_x"], camera_params["origin_y"], camera_params["origin_z"],
            camera_params["target_x"], camera_params["target_y"], camera_params["target_z"],
            camera_params["fov_deg"], camera_params["width"], camera_params["height"],
            render_resolution,
        )


# ── 9. MangakaUSDInspect ───────────────────────────────────────────────────
# Dump scene graph as text — debug + inspection. Outputs prim type counts
# + first 30 prim paths so you can see what's actually in the USD file.
class MangakaUSDInspect:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"scene": ("MANGAKA_SCENE",)}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    FUNCTION = "inspect"
    CATEGORY = "mangaka/usd"

    def inspect(self, scene):
        name = scene["name"]
        path = USD_ROOT / f"{name}.usda"
        if not path.exists():
            return (f"USD not found: {path}",)
        stage = Usd.Stage.Open(str(path))
        from collections import Counter
        kinds = Counter()
        prims = []
        for prim in stage.Traverse():
            kinds[prim.GetTypeName()] += 1
            prims.append((prim.GetPath().pathString, prim.GetTypeName()))
        lines = [f"# USD scene: {name}", f"# verts={scene['n_verts']}  faces={scene['n_faces']}",
                  "", "## prim type counts:"]
        for k, n in kinds.most_common():
            lines.append(f"  {k:25s}  {n}")
        lines += ["", "## first 30 prims:"]
        for p, t in prims[:30]:
            lines.append(f"  {p:60s}  ({t})")
        return ("\n".join(lines),)


# ── 10. MangakaUSDLight ────────────────────────────────────────────────────
# Extract UsdLux lights from the scene; emit a lighting-direction prompt
# hint that biases the manga generator's chiaroscuro towards the dominant
# light source.

def _light_direction_prompt(direction: np.ndarray, intensity: float) -> str:
    """Convert a world-space light direction vector into a natural prompt."""
    # Normalize
    d = direction / (np.linalg.norm(direction) + 1e-9)
    # Resolve to coarse compass: up/down + horizontal quadrant
    parts = []
    if d[1] > 0.6:    parts.append("overhead light")
    elif d[1] < -0.6: parts.append("uplight from below")
    if d[0] > 0.5:    parts.append("light from right")
    elif d[0] < -0.5: parts.append("light from left")
    if d[2] > 0.5:    parts.append("backlight")
    elif d[2] < -0.5: parts.append("frontlight")
    if not parts:
        parts.append("diffuse light")
    base = ", ".join(parts)
    if intensity > 2.0:
        return f"({base}:1.2), strong contrast, harsh shadow, dramatic chiaroscuro"
    if intensity < 0.4:
        return f"{base}, soft shadow, gentle screentone"
    return f"{base}, balanced shadow"


class MangakaUSDLight:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"scene": ("MANGAKA_SCENE",)}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("lighting_prompt", "lights_report")
    FUNCTION = "extract"
    CATEGORY = "mangaka/usd"

    def extract(self, scene):
        path = USD_ROOT / f"{scene['name']}.usda"
        if not path.exists():
            return ("", f"no USD: {path}")
        stage = Usd.Stage.Open(str(path))
        lines = []
        # Dominant light = highest intensity * focus weight
        best = None
        best_score = -1.0
        # USD 22+ split Light into LightAPI applied schema + concrete types
        # (RectLight, SphereLight, DistantLight, etc.). Detect via either
        # LightAPI applied schema OR by type name containing "Light".
        for prim in stage.Traverse():
            type_name = prim.GetTypeName()
            is_light = (
                type_name in ("RectLight", "SphereLight", "DistantLight",
                              "DomeLight", "DiskLight", "CylinderLight",
                              "GeometryLight", "PortalLight", "PluginLight")
                or "Light" in type_name
            )
            if not is_light:
                continue
            kind = type_name
            xf = UsdGeom.Xformable(prim)
            xform_mat = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default()) if xf else None
            pos = (0.0, 0.0, 0.0)
            if xform_mat:
                pos = xform_mat.ExtractTranslation()
                pos = (float(pos[0]), float(pos[1]), float(pos[2]))
            # Intensity attr
            intensity = 1.0
            attr = prim.GetAttribute("inputs:intensity")
            if attr.IsValid() and attr.Get() is not None:
                intensity = float(attr.Get())
            lines.append(f"  {prim.GetPath()}  ({kind})  pos={pos}  intensity={intensity}")
            score = intensity
            if score > best_score:
                best_score = score
                # Direction: assume the light points DOWN (-Y) by default;
                # for DistantLight or DirectionalLight, the prim's local -Z
                # in world space is the direction. We use position from
                # scene center as the direction (rough heuristic for area
                # lights without explicit direction attr).
                # Scene center ~ (W/2, H/2, D/2). For yuto-bedroom that's
                # roughly (2, 1.25, 2). Use the prim position as light origin
                # and (origin - center) as direction (light shines FROM
                # outside scene towards center).
                origin = np.array(pos)
                center = np.array([2.0, 1.25, 2.0])  # heuristic
                direction = origin - center
                if np.linalg.norm(direction) < 0.1:
                    direction = np.array([0, 1, 0])
                best = (direction, intensity, kind, pos)
        if best is None:
            return ("balanced flat lighting", "no lights found in scene")
        direction, intensity, kind, pos = best
        prompt = _light_direction_prompt(direction, intensity)
        report = f"dominant light: {kind} at {pos} intensity={intensity}\n" + "\n".join(lines)
        return (prompt, report)


# ── 11. MangakaUSDCameraPrim ───────────────────────────────────────────────
# Load a pre-defined UsdGeomCamera prim by name. Lets scene authors bake
# named camera angles into the USD itself instead of hand-typing xyz in
# ComfyUI. Returns MANGAKA_CAMERA_PARAMS so it slots into
# MangakaCameraViewFromParams.
class MangakaUSDCameraPrim:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "scene":       ("MANGAKA_SCENE",),
            "camera_path": ("STRING", {"default": "/Bedroom/Cameras/yuto_at_desk"}),
            "width":       ("INT", {"default": 1216, "min": 256, "max": 2048, "step": 8}),
            "height":      ("INT", {"default": 832,  "min": 256, "max": 2048, "step": 8}),
        }}
    RETURN_TYPES = ("MANGAKA_CAMERA_PARAMS",)
    FUNCTION = "load"
    CATEGORY = "mangaka/usd"

    def load(self, scene, camera_path, width, height):
        path = USD_ROOT / f"{scene['name']}.usda"
        stage = Usd.Stage.Open(str(path))
        prim = stage.GetPrimAtPath(camera_path)
        if not prim or not prim.IsValid():
            # Fallback: scan for any UsdGeomCamera and return first
            for p in stage.Traverse():
                if p.IsA(UsdGeom.Camera):
                    prim = p
                    break
        if not prim or not prim.IsA(UsdGeom.Camera):
            raise ValueError(f"no UsdGeomCamera at {camera_path} in {scene['name']}")
        cam = UsdGeom.Camera(prim)
        xf = UsdGeom.Xformable(prim)
        m = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        origin = m.ExtractTranslation()
        # Camera looks down -Z in its local frame
        forward_local = Gf.Vec3d(0, 0, -1)
        forward = m.TransformDir(forward_local)
        # Compute fov from focalLength + horizontalAperture
        focal = cam.GetFocalLengthAttr().Get() or 50.0
        h_aperture = cam.GetHorizontalApertureAttr().Get() or 36.0
        fov = 2 * math.degrees(math.atan(h_aperture / (2 * focal)))
        # Target = origin + forward * 2 (arbitrary distance)
        target = origin + forward * 2.0
        return ({
            "origin_x": float(origin[0]),
            "origin_y": float(origin[1]),
            "origin_z": float(origin[2]),
            "target_x": float(target[0]),
            "target_y": float(target[1]),
            "target_z": float(target[2]),
            "fov_deg":  float(fov),
            "width":    int(width),
            "height":   int(height),
        },)


# ── 12. MangakaUSDMaterial ─────────────────────────────────────────────────
# Extract UsdShade.Material prims + emit a prompt hint summarizing the
# surface vocabulary present in the scene (e.g. "wooden desk, painted
# walls, glass window"). Manga gen doesn't render materials directly,
# but the prompt hint biases the stylization.

MATERIAL_PROMPT_MAP = {
    "wood":     "wooden surface",
    "metal":    "polished metal",
    "fabric":   "soft fabric",
    "glass":    "clear glass",
    "concrete": "concrete wall",
    "plaster":  "painted plaster wall",
    "stone":    "stone surface",
    "screen":   "glowing screen",
    "tatami":   "tatami mat",
    "paper":    "paper",
}


class MangakaUSDMaterial:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"scene": ("MANGAKA_SCENE",)}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("material_prompt",)
    FUNCTION = "extract"
    CATEGORY = "mangaka/usd"

    def extract(self, scene):
        path = USD_ROOT / f"{scene['name']}.usda"
        stage = Usd.Stage.Open(str(path))
        seen = set()
        for prim in stage.Traverse():
            if prim.IsA(UsdShade.Material):
                seen.add(prim.GetName().lower())
        if not seen:
            # Fallback heuristic by scene name
            name = scene["name"].lower()
            if "bedroom" in name:    return ("wooden desk, painted walls, soft fabric, glass window",)
            if "classroom" in name:  return ("wooden desks, painted walls, blackboard, glass windows",)
            if "ren" in name:        return ("metal monitors, glowing screens, dark walls, plastic chair",)
            if "shrine" in name:     return ("wooden torii, stone path, stone lanterns, traditional architecture",)
            if "street" in name:     return ("concrete sidewalk, painted building walls, metal lamp posts",)
            if "cyber" in name:      return ("abstract glowing surfaces, data streams, neon",)
            return ("painted walls, wooden surfaces",)
        hints = []
        for s in seen:
            for k, v in MATERIAL_PROMPT_MAP.items():
                if k in s:
                    hints.append(v); break
        return (", ".join(hints) or ", ".join(seen),)


# ── 13. MangakaUSDReference ────────────────────────────────────────────────
# Compose two USD scenes by referencing one into another. Use case: load
# bedroom + apply a "messy" variant overlay; or load classroom + place a
# Yuto character mesh at a desk.
class MangakaUSDReference:
    @classmethod
    def INPUT_TYPES(cls):
        choices = sorted([p.stem for p in USD_ROOT.glob("*.usda")])
        if not choices:
            choices = ["(no .usda)"]
        return {"required": {
            "base":         ("MANGAKA_SCENE",),
            "ref_scene":    (choices,),
            "x":            ("FLOAT", {"default": 0.0, "step": 0.1}),
            "y":            ("FLOAT", {"default": 0.0, "step": 0.1}),
            "z":            ("FLOAT", {"default": 0.0, "step": 0.1}),
        }}
    RETURN_TYPES = ("MANGAKA_SCENE",)
    FUNCTION = "compose"
    CATEGORY = "mangaka/usd"

    def compose(self, base, ref_scene, x, y, z):
        # Load both scenes' meshes and concatenate with the ref offset.
        # In a production version we'd write a USD layer with a Reference;
        # for our trimesh-based renderer we just merge the geometry.
        ref_path = USD_ROOT / f"{ref_scene}.usda"
        ref_stage = Usd.Stage.Open(str(ref_path))
        ref_verts = []
        ref_faces = []
        for prim in ref_stage.Traverse():
            if not prim.IsA(UsdGeom.Mesh):
                continue
            mesh = UsdGeom.Mesh(prim)
            pts = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
            if pts is None or not len(pts): continue
            face_idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int64)
            tris = face_idx.reshape(-1, 3)
            offset = sum(len(v) for v in ref_verts)
            ref_faces.append(tris + offset)
            ref_verts.append(pts + np.array([x, y, z]))
        if not ref_verts:
            return (base,)
        rv = np.concatenate(ref_verts, axis=0)
        rf = np.concatenate(ref_faces, axis=0)
        # Merge with base
        base_mesh = base["mesh"]
        base_offset = len(base_mesh.vertices)
        merged_verts = np.concatenate([base_mesh.vertices, rv], axis=0)
        merged_faces = np.concatenate([base_mesh.faces, rf + base_offset], axis=0)
        merged = trimesh.Trimesh(vertices=merged_verts, faces=merged_faces, process=False)
        return ({
            "name": f"{base['name']}+{ref_scene}",
            "mesh": merged,
            "n_verts": len(merged_verts),
            "n_faces": len(merged_faces),
        },)


NODE_CLASS_MAPPINGS = {
    "MangakaUSDLoader":             MangakaUSDLoader,
    "MangakaCameraView":            MangakaCameraView,
    "MangakaPoseFromLibrary":       MangakaPoseFromLibrary,
    "MangakaProjectPose":           MangakaProjectPose,
    "MangakaCharacterEmotion":      MangakaCharacterEmotion,
    "MangakaPromptConcat":          MangakaPromptConcat,
    "MangakaShotComposition":       MangakaShotComposition,
    "MangakaCameraViewFromParams":  MangakaCameraViewFromParams,
    "MangakaUSDInspect":            MangakaUSDInspect,
    "MangakaUSDLight":              MangakaUSDLight,
    "MangakaUSDCameraPrim":         MangakaUSDCameraPrim,
    "MangakaUSDMaterial":           MangakaUSDMaterial,
    "MangakaUSDReference":          MangakaUSDReference,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MangakaUSDLoader":             "Mangaka USD Loader",
    "MangakaCameraView":            "Mangaka Camera View",
    "MangakaPoseFromLibrary":       "Mangaka Pose From Library",
    "MangakaProjectPose":           "Mangaka Project Pose to 2D",
    "MangakaCharacterEmotion":      "Mangaka Character Emotion",
    "MangakaPromptConcat":          "Mangaka Prompt Concat",
    "MangakaShotComposition":       "Mangaka Shot Composition (cinematic)",
    "MangakaCameraViewFromParams":  "Mangaka Camera View (from shot params)",
    "MangakaUSDInspect":            "Mangaka USD Inspect",
    "MangakaUSDLight":              "Mangaka USD Light (UsdLux → prompt)",
    "MangakaUSDCameraPrim":         "Mangaka USD Camera Prim (UsdGeomCamera)",
    "MangakaUSDMaterial":           "Mangaka USD Material (UsdShade → prompt)",
    "MangakaUSDReference":          "Mangaka USD Reference (compose scenes)",
}
