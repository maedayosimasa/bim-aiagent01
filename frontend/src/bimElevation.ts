import type { BimElement } from "./api/client";

export function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export type Vec2 = { x: number; y: number };

export function point2(value: unknown): Vec2 | null {
  const r = record(value);
  if (typeof r.x !== "number" || typeof r.y !== "number") return null;
  return { x: r.x, y: r.y };
}

// 要素タイプごとにArchicad座標(メートル)上での代表高さ(鉛直方向の中心)を導出する。
// SpaceViewer3D(立体表示)と同じ式を使い、表示間で高さの解釈がずれないようにする。
// 形状データが無い/未対応の要素はMapにキーを含めない(呼び出し側でフォールバックする)。
export function deriveElevationsMeters(elements: BimElement[]): Map<string, number> {
  const wallByGuid = new Map<string, BimElement>();
  for (const el of elements) {
    if (el.type === "Wall") wallByGuid.set(el.guid, el);
  }

  const elevations = new Map<string, number>();

  for (const el of elements) {
    const props = record(el.properties);
    const details = record(props.archicad_details);

    if (typeof details.error === "string") continue;

    if (el.type === "Wall") {
      const height = num(details.height, 3);
      const baseZ = num(details.zCoordinate, 0);
      elevations.set(el.guid, baseZ + height / 2);
      continue;
    }

    if (el.type === "Column") {
      const height = num(details.height, 3);
      const baseZ = num(details.zCoordinate, 0) + num(details.bottomOffset, 0);
      elevations.set(el.guid, baseZ + height / 2);
      continue;
    }

    if (el.type === "Beam") {
      elevations.set(el.guid, num(details.zCoordinate, 0));
      continue;
    }

    if (el.type === "Slab" || el.type === "Zone") {
      const thickness = el.type === "Slab" ? num(details.thickness, 0.2) : 0.05;
      const baseZ = num(details.zCoordinate, 0) - (el.type === "Slab" ? thickness : 0);
      elevations.set(el.guid, baseZ + thickness / 2);
      continue;
    }

    if (el.type === "Door" || el.type === "Window") {
      const ownerId = record(details.ownerElementId);
      const ownerGuid = typeof ownerId.guid === "string" ? ownerId.guid : null;
      const ownerWall = ownerGuid ? wallByGuid.get(ownerGuid) : undefined;
      const ownerDetails = ownerWall ? record(record(ownerWall.properties).archicad_details) : null;
      if (!ownerDetails) continue;
      const baseZ = num(ownerDetails.zCoordinate, 0) + num(details.sillHeight, 0);
      const height = num(details.height, 2);
      elevations.set(el.guid, baseZ + height / 2);
      continue;
    }

    if (el.type === "Object") {
      const origin = record(details.origin);
      const dims = record(details.dimensions);
      if (typeof origin.x !== "number" || typeof origin.y !== "number") continue;
      const originZ = num(origin.z, 0);
      const sizeZ = num(dims.z, 0.4) || 0.1;
      elevations.set(el.guid, originZ + sizeZ / 2);
      continue;
    }
  }

  return elevations;
}
