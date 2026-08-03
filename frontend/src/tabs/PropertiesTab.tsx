import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  deleteArchicadElements,
  getArchicadPropertyValues,
  getArchicadProperties,
  listElements,
  moveArchicadElement,
  setArchicadPropertyValue,
} from "../api/client";

function PropertiesTab() {
  const [selectedGuid, setSelectedGuid] = useState("");
  const [selectedPropertyGuid, setSelectedPropertyGuid] = useState("");
  const [newValue, setNewValue] = useState("");
  const [dx, setDx] = useState("0");
  const [dy, setDy] = useState("0");
  const [dz, setDz] = useState("0");
  const [duplicate, setDuplicate] = useState(false);

  const elementsQuery = useQuery({ queryKey: ["bim-elements"], queryFn: listElements });
  const propertiesQuery = useQuery({
    queryKey: ["archicad-properties"],
    queryFn: getArchicadProperties,
  });

  const valuesMutation = useMutation({
    mutationFn: () =>
      getArchicadPropertyValues([selectedGuid], [selectedPropertyGuid]),
  });

  const setValueMutation = useMutation({
    mutationFn: () =>
      setArchicadPropertyValue(selectedGuid, selectedPropertyGuid, newValue),
  });

  const moveMutation = useMutation({
    mutationFn: () =>
      moveArchicadElement(
        selectedGuid,
        Number(dx),
        Number(dy),
        Number(dz),
        duplicate
      ),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteArchicadElements([selectedGuid]),
  });

  const canAct = Boolean(selectedGuid);

  return (
    <div className="tab-panel">
      <h2>プロパティ編集(Archicad直接操作)</h2>
      <p className="hint">
        ここでの操作はローカルキャッシュではなくArchicad本体に直接反映されます
        (Tapir経由・破壊的操作)。PC側ブリッジが未接続の場合はエラーになります。
      </p>

      <div className="field-row">
        <label>対象要素</label>
        <select value={selectedGuid} onChange={(e) => setSelectedGuid(e.target.value)}>
          <option value="">選択してください</option>
          {elementsQuery.data?.map((el) => (
            <option key={el.guid} value={el.guid}>
              {el.type} / {el.name} ({el.guid.slice(0, 8)}...)
            </option>
          ))}
        </select>
      </div>

      <div className="field-row">
        <label>プロパティ</label>
        <select
          value={selectedPropertyGuid}
          onChange={(e) => setSelectedPropertyGuid(e.target.value)}
        >
          <option value="">選択してください</option>
          {propertiesQuery.data?.map((prop, i) => {
            const guid = prop.propertyId?.guid ?? "";
            return (
              <option key={guid || i} value={guid}>
                {prop.group?.name ? `${prop.group.name} / ` : ""}
                {prop.name ?? guid}
              </option>
            );
          })}
        </select>
        {propertiesQuery.isError && (
          <span className="error">{String(propertiesQuery.error)}</span>
        )}
      </div>

      <div className="button-row">
        <button
          onClick={() => valuesMutation.mutate()}
          disabled={!canAct || !selectedPropertyGuid || valuesMutation.isPending}
        >
          値を取得
        </button>
      </div>
      {valuesMutation.isError && <p className="error">{String(valuesMutation.error)}</p>}
      {valuesMutation.data && (
        <pre className="json-block">{JSON.stringify(valuesMutation.data, null, 2)}</pre>
      )}

      <div className="field-row">
        <label>新しい値</label>
        <input type="text" value={newValue} onChange={(e) => setNewValue(e.target.value)} />
        <button
          onClick={() => setValueMutation.mutate()}
          disabled={!canAct || !selectedPropertyGuid || setValueMutation.isPending}
        >
          値を設定
        </button>
      </div>
      {setValueMutation.isError && (
        <p className="error">{String(setValueMutation.error)}</p>
      )}
      {setValueMutation.isSuccess && <p>設定しました。</p>}

      <hr />

      <h3>要素の移動(相対ベクトル)</h3>
      <div className="field-row">
        <label>dx</label>
        <input type="number" value={dx} onChange={(e) => setDx(e.target.value)} />
        <label>dy</label>
        <input type="number" value={dy} onChange={(e) => setDy(e.target.value)} />
        <label>dz</label>
        <input type="number" value={dz} onChange={(e) => setDz(e.target.value)} />
        <label>
          <input
            type="checkbox"
            checked={duplicate}
            onChange={(e) => setDuplicate(e.target.checked)}
          />
          複製して移動
        </label>
        <button
          onClick={() => {
            if (window.confirm("Archicad本体の要素を移動します。よろしいですか?")) {
              moveMutation.mutate();
            }
          }}
          disabled={!canAct || moveMutation.isPending}
        >
          移動を実行
        </button>
      </div>
      {moveMutation.isError && <p className="error">{String(moveMutation.error)}</p>}
      {moveMutation.isSuccess && <p>移動しました。</p>}

      <h3>要素の削除</h3>
      <div className="button-row">
        <button
          className="danger"
          onClick={() => {
            if (
              window.confirm(
                "Archicad本体からこの要素を削除します。元に戻せません。よろしいですか?"
              )
            ) {
              deleteMutation.mutate();
            }
          }}
          disabled={!canAct || deleteMutation.isPending}
        >
          削除を実行
        </button>
      </div>
      {deleteMutation.isError && <p className="error">{String(deleteMutation.error)}</p>}
      {deleteMutation.isSuccess && <p>削除しました。</p>}
    </div>
  );
}

export default PropertiesTab;
