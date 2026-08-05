import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Canvas } from "@react-three/fiber";
import { Grid, OrbitControls, Text } from "@react-three/drei";
import * as THREE from "three";
import { listElements, type BimElement } from "../api/client";
import { useArchicadFocus } from "../hooks/useArchicadFocus";

// ==============================
// Archicad座標(メートル、Z-up) → three.js座標(Y-up)への変換
// world.x = archicad.x, world.y = archicad.z(高さ), world.z = archicad.y(奥行)
// ==============================
type Vec2 = { x: number; y: number };
type Vec3 = [number, number, number];

type BoxItem = {
  kind: "box";
  guid: string;
  type: string;
  name: string;
  floorIndex: number;
  position: Vec3;
  size: Vec3;
  rotationY: number;
  color: string;
};

type PolygonItem = {
  kind: "polygon";
  guid: string;
  type: string;
  name: string;
  floorIndex: number;
  points: Vec2[];
  thickness: number;
  baseZ: number;
  color: string;
};

type SceneItem = BoxItem | PolygonItem;

const TYPE_COLORS: Record<string, string> = {
  Wall: "#8a8f98",
  Door: "#d99a3b",
  Window: "#3b8ed9",
  Column: "#6b5b95",
  Beam: "#b05c3b",
  Slab: "#c9b458",
  Zone: "#4f9d69",
  Object: "#9aa0a8",
};

const TYPE_LABELS_JA: Record<string, string> = {
  Wall: "壁",
  Door: "ドア",
  Window: "窓",
  Column: "柱",
  Beam: "梁",
  Slab: "スラブ",
  Zone: "部屋",
  Object: "オブジェクト",
};

const EMPTY_ELEMENTS: BimElement[] = [];

const DEFAULT_WALL_THICKNESS = 0.2;
const DEFAULT_BEAM_SIZE = 0.3;
const DEFAULT_COLUMN_SIZE = 0.4;

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function point2(value: unknown): Vec2 | null {
  const r = record(value);
  if (typeof r.x !== "number" || typeof r.y !== "number") return null;
  return { x: r.x, y: r.y };
}

function toScene(x: number, y: number, z: number): Vec3 {
  return [x, z, y];
}

// world空間で(beg→end)方向にX軸を向けるためのY軸回転角。
// world.x=archicad.x, world.z=archicad.yのマッピングでの導出値。
function orientationOf(beg: Vec2, end: Vec2) {
  const dx = end.x - beg.x;
  const dy = end.y - beg.y;
  const length = Math.hypot(dx, dy);
  return {
    length,
    centerX: (beg.x + end.x) / 2,
    centerY: (beg.y + end.y) / 2,
    unitX: length > 0 ? dx / length : 1,
    unitY: length > 0 ? dy / length : 0,
    rotationY: Math.atan2(-dy, dx),
  };
}

// properties/geometryはArchicad要素タイプごとに形が異なるため、タイプ別に3D形状を組み立てる。
// details.error があるタイプ(Roof/Dimension/CutPlane/Elevation/BeamSegment/ColumnSegment)は
// Tapir側がまだ形状を返さないため描画をスキップする(architect_mcp/tapir.pyのdocstring参照)。
function buildSceneItems(elements: BimElement[]): { items: SceneItem[]; skipped: number } {
  const wallByGuid = new Map<string, BimElement>();
  for (const el of elements) {
    if (el.type === "Wall") wallByGuid.set(el.guid, el);
  }

  const items: SceneItem[] = [];
  let skipped = 0;

  for (const el of elements) {
    const props = record(el.properties);
    const details = record(props.archicad_details);
    const floorIndex = num(props.floorIndex, 0);

    if (typeof details.error === "string") {
      skipped++;
      continue;
    }

    if (el.type === "Wall") {
      const beg = point2(details.begCoordinate);
      const end = point2(details.endCoordinate);
      if (!beg || !end) {
        skipped++;
        continue;
      }
      const { length, centerX, centerY, rotationY } = orientationOf(beg, end);
      const height = num(details.height, 3);
      const thickness =
        (num(details.begThickness, DEFAULT_WALL_THICKNESS) +
          num(details.endThickness, DEFAULT_WALL_THICKNESS)) /
        2;
      const baseZ = num(details.zCoordinate, 0);
      items.push({
        kind: "box",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        position: toScene(centerX, centerY, baseZ + height / 2),
        size: [length, height, thickness],
        rotationY,
        color: TYPE_COLORS.Wall,
      });
      continue;
    }

    if (el.type === "Column") {
      const origin = point2(details.origin);
      if (!origin) {
        skipped++;
        continue;
      }
      const height = num(details.height, 3);
      const baseZ = num(details.zCoordinate, 0) + num(details.bottomOffset, 0);
      items.push({
        kind: "box",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        position: toScene(origin.x, origin.y, baseZ + height / 2),
        size: [DEFAULT_COLUMN_SIZE, height, DEFAULT_COLUMN_SIZE],
        rotationY: 0,
        color: TYPE_COLORS.Column,
      });
      continue;
    }

    if (el.type === "Beam") {
      const beg = point2(details.begCoordinate);
      const end = point2(details.endCoordinate);
      if (!beg || !end) {
        skipped++;
        continue;
      }
      const { length, centerX, centerY, rotationY } = orientationOf(beg, end);
      const baseZ = num(details.zCoordinate, 0);
      items.push({
        kind: "box",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        position: toScene(centerX, centerY, baseZ),
        size: [length, DEFAULT_BEAM_SIZE, DEFAULT_BEAM_SIZE],
        rotationY,
        color: TYPE_COLORS.Beam,
      });
      continue;
    }

    if (el.type === "Slab" || el.type === "Zone") {
      const outlineRaw = details.polygonOutline;
      const points = Array.isArray(outlineRaw)
        ? outlineRaw.map(point2).filter((p): p is Vec2 => p !== null)
        : [];
      if (points.length < 3) {
        skipped++;
        continue;
      }
      const thickness = el.type === "Slab" ? num(details.thickness, 0.2) : 0.05;
      const baseZ = num(details.zCoordinate, 0) - (el.type === "Slab" ? thickness : 0);
      items.push({
        kind: "polygon",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        points,
        thickness,
        baseZ,
        color: TYPE_COLORS[el.type],
      });
      continue;
    }

    if (el.type === "Door" || el.type === "Window") {
      const ownerId = record(details.ownerElementId);
      const ownerGuid = typeof ownerId.guid === "string" ? ownerId.guid : null;
      const ownerWall = ownerGuid ? wallByGuid.get(ownerGuid) : undefined;
      const ownerDetails = ownerWall ? record(record(ownerWall.properties).archicad_details) : null;
      const wallBeg = ownerDetails ? point2(ownerDetails.begCoordinate) : null;
      const wallEnd = ownerDetails ? point2(ownerDetails.endCoordinate) : null;
      if (!ownerDetails || !wallBeg || !wallEnd) {
        skipped++;
        continue;
      }
      const { unitX, unitY, rotationY, length } = orientationOf(wallBeg, wallEnd);
      const centerOffset = num(details.centerOffset, length / 2);
      const posX = wallBeg.x + unitX * centerOffset;
      const posY = wallBeg.y + unitY * centerOffset;
      const wallThickness =
        (num(ownerDetails.begThickness, DEFAULT_WALL_THICKNESS) +
          num(ownerDetails.endThickness, DEFAULT_WALL_THICKNESS)) /
        2;
      const baseZ = num(ownerDetails.zCoordinate, 0) + num(details.sillHeight, 0);
      const height = num(details.height, 2);
      const width = num(details.width, 0.9);
      items.push({
        kind: "box",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        position: toScene(posX, posY, baseZ + height / 2),
        size: [width, height, wallThickness * 1.1],
        rotationY,
        color: TYPE_COLORS[el.type],
      });
      continue;
    }

    if (el.type === "Object") {
      const origin = record(details.origin);
      const dims = record(details.dimensions);
      if (typeof origin.x !== "number" || typeof origin.y !== "number") {
        skipped++;
        continue;
      }
      const originZ = num(origin.z, 0);
      const sizeX = num(dims.x, 0.4);
      const sizeY = num(dims.y, 0.4);
      const sizeZ = num(dims.z, 0.4) || 0.1;
      const angle = num(details.angle, 0);
      items.push({
        kind: "box",
        guid: el.guid,
        type: el.type,
        name: el.name,
        floorIndex,
        position: toScene(origin.x, origin.y, originZ + sizeZ / 2),
        size: [sizeX, sizeZ, sizeY],
        rotationY: -angle,
        color: TYPE_COLORS.Object,
      });
      continue;
    }

    // Label/Dimensionなど、実寸の形状データを持たないその他のタイプは非表示。
    skipped++;
  }

  return { items, skipped };
}

function computeBounds(items: SceneItem[]) {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;

  const consume = (x: number, y: number, z: number) => {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
  };

  for (const item of items) {
    if (item.kind === "box") {
      consume(...item.position);
    } else {
      for (const p of item.points) {
        consume(p.x, item.baseZ, p.y);
      }
    }
  }

  if (!Number.isFinite(minX)) {
    return { center: [0, 0, 0] as Vec3, radius: 20 };
  }

  const center: Vec3 = [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2];
  const radius = Math.max(
    5,
    Math.hypot(maxX - minX, maxY - minY, maxZ - minZ) / 2 + 5
  );
  return { center, radius };
}

function PolygonMesh({
  item,
  explodeOffset,
  selected,
  onSelect,
}: {
  item: PolygonItem;
  explodeOffset: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const geometry = useMemo(() => {
    const shape = new THREE.Shape(item.points.map((p) => new THREE.Vector2(p.x, -p.y)));
    return new THREE.ExtrudeGeometry(shape, { depth: item.thickness, bevelEnabled: false });
  }, [item.points, item.thickness]);

  const centroid = useMemo(() => {
    const cx = item.points.reduce((s, p) => s + p.x, 0) / item.points.length;
    const cy = item.points.reduce((s, p) => s + p.y, 0) / item.points.length;
    return toScene(cx, cy, item.baseZ + explodeOffset + item.thickness + 0.3);
  }, [item.points, item.baseZ, item.thickness, explodeOffset]);

  return (
    <group>
      <mesh
        geometry={geometry}
        position={[0, item.baseZ + explodeOffset, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      >
        <meshStandardMaterial
          color={item.color}
          transparent
          opacity={selected ? 0.9 : 0.55}
          emissive={selected ? item.color : "#000000"}
          emissiveIntensity={selected ? 0.4 : 0}
        />
      </mesh>
      {item.type === "Zone" && (
        <Text position={centroid} fontSize={0.5} color="#3a2f1c" anchorX="center" anchorY="middle">
          {item.name}
        </Text>
      )}
    </group>
  );
}

function BoxMesh({
  item,
  explodeOffset,
  selected,
  onSelect,
}: {
  item: BoxItem;
  explodeOffset: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const position: Vec3 = [item.position[0], item.position[1] + explodeOffset, item.position[2]];
  return (
    <mesh
      position={position}
      rotation={[0, item.rotationY, 0]}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
    >
      <boxGeometry args={item.size} />
      <meshStandardMaterial
        color={item.color}
        emissive={selected ? item.color : "#000000"}
        emissiveIntensity={selected ? 0.5 : 0}
      />
    </mesh>
  );
}

function Scene({
  items,
  hiddenFloors,
  explodeGap,
  selectedGuid,
  onSelect,
  bounds,
}: {
  items: SceneItem[];
  hiddenFloors: Set<number>;
  explodeGap: number;
  selectedGuid: string | null;
  onSelect: (guid: string) => void;
  bounds: { center: Vec3; radius: number };
}) {
  return (
    <>
      <ambientLight intensity={0.7} />
      <directionalLight position={[bounds.radius, bounds.radius * 1.5, bounds.radius]} intensity={1} />
      <Grid
        args={[bounds.radius * 4, bounds.radius * 4]}
        position={[bounds.center[0], 0, bounds.center[2]]}
        cellColor="#c9bfae"
        sectionColor="#a8987a"
        fadeDistance={bounds.radius * 6}
      />
      {items
        .filter((item) => !hiddenFloors.has(item.floorIndex))
        .map((item) => {
          const explodeOffset = item.floorIndex * explodeGap;
          const selected = item.guid === selectedGuid;
          return item.kind === "polygon" ? (
            <PolygonMesh
              key={item.guid}
              item={item}
              explodeOffset={explodeOffset}
              selected={selected}
              onSelect={() => onSelect(item.guid)}
            />
          ) : (
            <BoxMesh
              key={item.guid}
              item={item}
              explodeOffset={explodeOffset}
              selected={selected}
              onSelect={() => onSelect(item.guid)}
            />
          );
        })}
      <OrbitControls target={bounds.center} makeDefault />
    </>
  );
}

function SpaceViewer3D() {
  const elementsQuery = useQuery({
    queryKey: ["bim-elements"],
    queryFn: listElements,
  });

  const [hiddenFloors, setHiddenFloors] = useState<Set<number>>(new Set());
  const [explodeGap, setExplodeGap] = useState(0);
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);
  const focusInArchicad = useArchicadFocus();

  const elements = useMemo(() => elementsQuery.data ?? EMPTY_ELEMENTS, [elementsQuery.data]);

  const { items, skipped } = useMemo(() => buildSceneItems(elements), [elements]);
  const bounds = useMemo(() => computeBounds(items), [items]);

  const floors = useMemo(
    () => Array.from(new Set(items.map((item) => item.floorIndex))).sort((a, b) => a - b),
    [items]
  );

  const elementByGuid = useMemo(() => new Map(elements.map((el) => [el.guid, el])), [elements]);
  const selectedElement = selectedGuid ? elementByGuid.get(selectedGuid) : undefined;

  const selectAndFocus = (guid: string | null) => {
    setSelectedGuid(guid);
    focusInArchicad(guid);
  };

  const toggleFloor = (floorIndex: number) => {
    setHiddenFloors((prev) => {
      const next = new Set(prev);
      if (next.has(floorIndex)) {
        next.delete(floorIndex);
      } else {
        next.add(floorIndex);
      }
      return next;
    });
  };

  return (
    <div className="tab-panel">
      <h2>3Dビュー</h2>
      <p className="hint">
        実座標(Archicadのメートル単位)を使って壁・柱・梁・スラブ・部屋・ドア・窓を立体表示します。
        マウスドラッグで回転、ホイールでズーム、右ドラッグでパンできます。
      </p>
      <p className="hint">
        要素をクリックするとArchicad本体でも同じ要素が選択+ハイライトされます
        (ブリッジ接続時のみ)。画面をその位置までスクロールする機能はArchicad側の
        APIにないため、選択された要素を手動で探してください。
      </p>

      {elementsQuery.isLoading && <p>読み込み中...</p>}
      {elementsQuery.isError && <p className="error">{String(elementsQuery.error)}</p>}

      {elements.length === 0 && !elementsQuery.isLoading && (
        <p>要素がありません。先に「要素同期」タブで同期してください。</p>
      )}

      {elements.length > 0 && (
        <>
          <div className="button-row">
            <span>階の表示切替:</span>
            {floors.map((floorIndex) => (
              <label key={floorIndex}>
                <input
                  type="checkbox"
                  checked={!hiddenFloors.has(floorIndex)}
                  onChange={() => toggleFloor(floorIndex)}
                />
                {floorIndex}F
              </label>
            ))}
            <label className="graph-spacing-control">
              階を分解(間隔)
              <input
                type="range"
                min={0}
                max={5}
                step={0.2}
                value={explodeGap}
                onChange={(e) => setExplodeGap(Number(e.target.value))}
              />
              {explodeGap.toFixed(1)}m
            </label>
          </div>

          <p className="status-line">
            表示中: {items.length}件
            {skipped > 0 && ` / 形状データ未対応のため非表示: ${skipped}件`}
          </p>

          <div className="graph-legend">
            {Object.entries(TYPE_COLORS).map(([type, color]) => (
              <span key={type} className="graph-legend-item">
                <span className="graph-legend-swatch" style={{ background: color }} />
                {TYPE_LABELS_JA[type] ?? type}
              </span>
            ))}
          </div>

          <div className="graph-canvas viewer3d-canvas">
            <Canvas
              camera={{
                position: [
                  bounds.center[0] + bounds.radius,
                  bounds.center[1] + bounds.radius * 0.8,
                  bounds.center[2] + bounds.radius,
                ],
                fov: 50,
              }}
              onPointerMissed={() => selectAndFocus(null)}
            >
              <Scene
                items={items}
                hiddenFloors={hiddenFloors}
                explodeGap={explodeGap}
                selectedGuid={selectedGuid}
                onSelect={selectAndFocus}
                bounds={bounds}
              />
            </Canvas>
          </div>

          {selectedElement && (
            <>
              <h3>詳細(properties / geometry)</h3>
              <pre className="json-block">{JSON.stringify(selectedElement, null, 2)}</pre>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default SpaceViewer3D;
