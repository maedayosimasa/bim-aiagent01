import { useMutation } from "@tanstack/react-query";
import { focusArchicadElements } from "../api/client";

// フロントエンドで要素を選択した際、Archicad本体側でも同じ要素を選択+
// ハイライトするための共通フック。Archicadブリッジが未接続でも他の
// 表示・操作をブロックしないよう、エラーは無視する(fire-and-forget)。
export function useArchicadFocus() {
  const mutation = useMutation({ mutationFn: focusArchicadElements });

  const focus = (guid: string | null) => {
    mutation.mutate(guid ? [guid] : []);
  };

  return focus;
}
