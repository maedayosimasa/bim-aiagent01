import { useState } from "react";
import "./App.css";
import Dashboard from "./tabs/Dashboard";
import ElementsTab from "./tabs/ElementsTab";
import SearchTab from "./tabs/SearchTab";
import LegalSearchTab from "./tabs/LegalSearchTab";
import GraphTab from "./tabs/GraphTab";
import PropertiesTab from "./tabs/PropertiesTab";
import SpaceViewer3D from "./tabs/SpaceViewer3D";
import AnalysisSnapshotTab from "./tabs/AnalysisSnapshotTab";

const TABS = [
  { key: "dashboard", label: "ダッシュボード", Component: Dashboard },
  { key: "elements", label: "要素同期", Component: ElementsTab },
  { key: "search", label: "意味検索", Component: SearchTab },
  { key: "legal-search", label: "法令検索", Component: LegalSearchTab },
  { key: "graph", label: "空間関係グラフ", Component: GraphTab },
  { key: "viewer3d", label: "3Dビュー", Component: SpaceViewer3D },
  { key: "analysis-db", label: "解析結果DB", Component: AnalysisSnapshotTab },
  { key: "properties", label: "プロパティ編集", Component: PropertiesTab },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("dashboard");

  const ActiveComponent = TABS.find((tab) => tab.key === activeTab)!.Component;

  return (
    <div className="app-shell">
      <nav className="tab-bar">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={tab.key === activeTab ? "tab-button active" : "tab-button"}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="tab-content">
        <ActiveComponent />
      </main>
    </div>
  );
}

export default App;
