"""Local triangle tests for an IFC world mesh.

AABB overlap is not solid penetration. A point is enclosed only when a vertical
line through it meets the exported mesh both below and above the point.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


SURFACE_BAND_M = 1e-4
MAX_TRIANGLES = 20000


def triangles_from_mesh(vertices: Sequence[float], faces: Sequence[int]) -> np.ndarray:
    verts = np.asarray(vertices, dtype=float).reshape(-1, 3)
    index = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    return verts[index]


def vertical_section(point: Sequence[float], triangles: np.ndarray) -> dict:
    """Z values where a vertical line through ``point`` meets a triangle."""
    if len(triangles) == 0:
        return {"hit_z_m": [], "enclosed": False, "on_surface": False, "resolved": True}
    if len(triangles) > MAX_TRIANGLES:
        return {"hit_z_m": [], "enclosed": False, "on_surface": False, "resolved": False, "reason": "MESH_TOO_LARGE"}
    xy = np.asarray(point[:2], dtype=float)
    origin = triangles[:, 0, :2]
    v0 = triangles[:, 2, :2] - origin
    v1 = triangles[:, 1, :2] - origin
    v2 = xy - origin
    dot00 = np.einsum("ij,ij->i", v0, v0)
    dot01 = np.einsum("ij,ij->i", v0, v1)
    dot02 = np.einsum("ij,ij->i", v0, v2)
    dot11 = np.einsum("ij,ij->i", v1, v1)
    dot12 = np.einsum("ij,ij->i", v1, v2)
    denom = dot00 * dot11 - dot01 * dot01
    valid = np.abs(denom) > 1e-14
    inv = np.zeros_like(denom)
    inv[valid] = 1.0 / denom[valid]
    u = (dot11 * dot02 - dot01 * dot12) * inv
    v = (dot00 * dot12 - dot01 * dot02) * inv
    inside = valid & (u >= -1e-8) & (v >= -1e-8) & (u + v <= 1.0 + 1e-8)
    if not np.any(inside):
        return {"hit_z_m": [], "enclosed": False, "on_surface": False, "resolved": True}
    selected = triangles[inside]
    uu = u[inside]
    vv = v[inside]
    height = (
        selected[:, 0, 2] * (1.0 - uu - vv)
        + selected[:, 1, 2] * vv
        + selected[:, 2, 2] * uu
    )
    hits = sorted({round(float(value), 6) for value in height})
    z = float(point[2])
    below = any(value < z - SURFACE_BAND_M for value in hits)
    above = any(value > z + SURFACE_BAND_M for value in hits)
    on_surface = any(abs(value - z) <= SURFACE_BAND_M for value in hits)
    return {
        "hit_z_m": hits,
        "enclosed": bool(below and above),
        "on_surface": bool(on_surface and not (below and above)),
        "resolved": True,
    }


def surface_distance_m(point: Sequence[float], triangles: np.ndarray) -> float | None:
    """Minimum distance from a point to the triangle surfaces."""
    if len(triangles) == 0 or len(triangles) > MAX_TRIANGLES:
        return None
    query = np.asarray(point, dtype=float)
    first = triangles[:, 0]
    second = triangles[:, 1]
    third = triangles[:, 2]
    best = np.full(len(triangles), np.inf)
    for start, end in ((first, second), (second, third), (third, first)):
        direction = end - start
        length2 = np.einsum("ij,ij->i", direction, direction)
        safe = np.where(length2 < 1e-18, 1.0, length2)
        parameter = np.clip(np.einsum("ij,ij->i", query - start, direction) / safe, 0.0, 1.0)
        closest = start + parameter[:, None] * direction
        distance = np.linalg.norm(closest - query, axis=1)
        best = np.minimum(best, distance)
    # Interior of the triangle is closer than the edges when the projection lands inside.
    normal = np.cross(second - first, third - first)
    norm = np.linalg.norm(normal, axis=1)
    usable = norm > 1e-12
    if np.any(usable):
        unit = np.zeros_like(normal)
        unit[usable] = normal[usable] / norm[usable, None]
        signed = np.einsum("ij,ij->i", query - first, unit)
        projected = query - signed[:, None] * unit
        # Barycentric via areas, only where the projection is finite.
        area = np.einsum("ij,ij->i", np.cross(second - first, third - first), unit)
        w0 = np.einsum("ij,ij->i", np.cross(second - projected, third - projected), unit)
        w1 = np.einsum("ij,ij->i", np.cross(third - projected, first - projected), unit)
        w2 = np.einsum("ij,ij->i", np.cross(first - projected, second - projected), unit)
        inside = usable & (w0 >= -1e-7) & (w1 >= -1e-7) & (w2 >= -1e-7) & (np.abs(area) > 1e-12)
        planar = np.abs(signed)
        best = np.where(inside, np.minimum(best, planar), best)
    return float(best.min())


def cells_touching_mesh(
    points: np.ndarray, triangles: np.ndarray, *, contact_m: float = 1e-3,
) -> np.ndarray:
    """Return a mask of points that are enclosed by the mesh or within ``contact_m`` of it.

    Points far from every triangle are not occupied, even if they lie in the mesh AABB.
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    occupied = np.zeros(len(points), dtype=bool)
    if len(points) == 0 or len(triangles) == 0 or len(triangles) > MAX_TRIANGLES:
        if len(triangles) > MAX_TRIANGLES:
            occupied[:] = True
        return occupied
    below = np.zeros(len(points), dtype=bool)
    above = np.zeros(len(points), dtype=bool)
    near = np.zeros(len(points), dtype=bool)
    for triangle in triangles:
        origin = triangle[0, :2]
        v0 = triangle[2, :2] - origin
        v1 = triangle[1, :2] - origin
        v2 = points[:, :2] - origin
        dot00 = float(np.dot(v0, v0))
        dot01 = float(np.dot(v0, v1))
        dot11 = float(np.dot(v1, v1))
        denom = dot00 * dot11 - dot01 * dot01
        if abs(denom) > 1e-14:
            dot02 = v2 @ v0
            dot12 = v2 @ v1
            inv = 1.0 / denom
            u = (dot11 * dot02 - dot01 * dot12) * inv
            v = (dot00 * dot12 - dot01 * dot02) * inv
            inside = (u >= -1e-8) & (v >= -1e-8) & (u + v <= 1.0 + 1e-8)
            if np.any(inside):
                height = (
                    triangle[0, 2] * (1.0 - u[inside] - v[inside])
                    + triangle[1, 2] * v[inside]
                    + triangle[2, 2] * u[inside]
                )
                selected = np.flatnonzero(inside)
                z = points[selected, 2]
                below[selected] |= height < z - SURFACE_BAND_M
                above[selected] |= height > z + SURFACE_BAND_M
                near[selected] |= np.abs(height - z) <= max(contact_m, SURFACE_BAND_M)
        # Edge distance for contact.
        for start, end in ((triangle[0], triangle[1]), (triangle[1], triangle[2]), (triangle[2], triangle[0])):
            direction = end - start
            length2 = float(np.dot(direction, direction))
            if length2 < 1e-18:
                distance = np.linalg.norm(points - start, axis=1)
            else:
                parameter = np.clip(((points - start) @ direction) / length2, 0.0, 1.0)
                distance = np.linalg.norm(points - (start + parameter[:, None] * direction), axis=1)
            near |= distance <= contact_m
    return (below & above) | near


def mesh_facts(point: Sequence[float], other: Sequence[float], triangles: np.ndarray) -> dict:
    """Enclosure and surface distance for a port and a second point, usually the cell centre."""
    port_section = vertical_section(point, triangles)
    other_section = vertical_section(other, triangles)
    if not port_section.get("resolved", False) or not other_section.get("resolved", False):
        return {"resolved": False, "reason": "MESH_TOO_LARGE"}
    return {
        "resolved": True,
        "port_enclosed": port_section["enclosed"],
        "port_on_surface": port_section["on_surface"],
        "port_hit_z_m": port_section["hit_z_m"],
        "other_enclosed": other_section["enclosed"],
        "other_on_surface": other_section["on_surface"],
        "other_hit_z_m": other_section["hit_z_m"],
        "port_surface_distance_m": surface_distance_m(point, triangles),
        "other_surface_distance_m": surface_distance_m(other, triangles),
    }
