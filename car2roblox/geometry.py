"""Mesh algorithms: winding repair, lossless splitting under a triangle cap, and
grid-based decimation."""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from .model import Mesh


# --------------------------------------------------------------------------- winding
def auto_fix_winding(mesh: Mesh, min_tris: int = 8) -> tuple[Mesh, bool]:
    """If the mesh carries vertex normals, make sure the triangle winding agrees with them.

    Returns ``(mesh, flipped)``. Only acts when the evidence is clear (area-weighted
    agreement is strongly negative), so already-correct or normal-less meshes are untouched.
    """
    if mesh.normals is None or mesh.tri_count < min_tris:
        return mesh, False
    fn = mesh.face_normals()
    vn = mesh.normals.astype(np.float64)[mesh.triangles].sum(axis=1)
    dots = np.einsum("ij,ij->i", fn, vn)
    # weight by face area (|fn|) * |vn| so tiny/degenerate faces do not vote
    weight = np.linalg.norm(fn, axis=1) * np.linalg.norm(vn, axis=1)
    total = weight.sum()
    if total <= 0:
        return mesh, False
    score = dots.sum() / total  # -1 .. +1
    if score < -0.2:
        return mesh.flipped(), True
    return mesh, False


# --------------------------------------------------------------------------- splitting
def split_by_limit(mesh: Mesh, max_tris: int) -> List[Mesh]:
    """Lossless spatial split into chunks of at most ``max_tris`` triangles
    (median split along the longest axis of the triangle centroids)."""
    if max_tris < 4:
        raise ValueError("max_tris too small")
    if mesh.tri_count <= max_tris:
        return [mesh]
    out: List[Mesh] = []
    stack = [mesh]
    while stack:
        m = stack.pop()
        if m.tri_count <= max_tris:
            out.append(m)
            continue
        cent = m.positions[m.triangles].mean(axis=1)
        axis = int(np.argmax(cent.max(axis=0) - cent.min(axis=0)))
        order = np.argsort(cent[:, axis], kind="stable")
        half = len(order) // 2
        a, b = order[:half], order[half:]
        # push in reverse so the final list comes out in spatial order
        stack.append(m.select(np.sort(b)))
        stack.append(m.select(np.sort(a)))
    return out


# --------------------------------------------------------------------------- decimation
def _cluster(mesh: Mesh, cell: float, uv_cell: float) -> Mesh:
    p = mesh.positions.astype(np.float64)
    origin = p.min(axis=0)
    g = np.floor((p - origin) / cell).astype(np.int64)
    cols = [g[:, 0], g[:, 1], g[:, 2]]
    if mesh.uvs is not None:
        u = np.floor(mesh.uvs.astype(np.float64) / uv_cell).astype(np.int64)
        u -= u.min(axis=0)
        cols += [u[:, 0], u[:, 1]]
    key = np.stack(cols, axis=1)
    _, inverse = np.unique(key, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    n = int(inverse.max()) + 1
    counts = np.bincount(inverse, minlength=n).astype(np.float64)

    def mean(a: np.ndarray) -> np.ndarray:
        out = np.zeros((n, a.shape[1]))
        for k in range(a.shape[1]):
            out[:, k] = np.bincount(inverse, weights=a[:, k].astype(np.float64), minlength=n)
        return out / counts[:, None]

    pos = mean(p)
    uvs = mean(mesh.uvs) if mesh.uvs is not None else None
    nrm = None
    if mesh.normals is not None:
        nrm = mean(mesh.normals)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        nrm = nrm / ln
    tris = inverse[mesh.triangles]
    ok = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    out = Mesh(pos, tris[ok], mesh.material_ids[ok],
               None if nrm is None else nrm, uvs).compact()
    return out


def decimate_to(mesh: Mesh, target_tris: int) -> Mesh:
    """Crude vertex-clustering decimation. Positions *and* UVs are clustered, so texture
    seams are preserved approximately. Quality is deliberately basic: use it to get
    heavy meshes (e.g. GTA V cars) under a budget, not for hero assets."""
    target_tris = max(int(target_tris), 4)
    if mesh.tri_count <= target_tris:
        return mesh
    lo_p, hi_p = mesh.bounds()
    diag = float(np.linalg.norm(hi_p - lo_p)) or 1.0
    # binary search on the cell size (log scale): smallest cell that satisfies the target
    lo, hi = diag / 4096.0, diag / 2.0
    best: Optional[Mesh] = None
    for _ in range(14):
        mid = float(np.sqrt(lo * hi))
        cand = _cluster(mesh, mid, max(mid / diag, 1.0 / 2048.0))
        if cand.tri_count <= target_tris:
            best = cand
            hi = mid
        else:
            lo = mid
    if best is None:
        best = _cluster(mesh, hi, max(hi / diag, 1.0 / 2048.0))
    return best
