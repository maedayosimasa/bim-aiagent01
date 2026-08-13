import { useState } from "react";
import "./App.css";
import Dashboard from "./tabs/Dashboard";
import ElementsTab from "./tabs/ElementsTab";
import SearchTab from "./tabs/SearchTab";
import LegalSearchTab from "./tabs/LegalSearchTab";
import AgentChatTab from "./tabs/AgentChatTab";
import LegalReportTab from "./tabs/LegalReportTab";
import GraphTab from "./tabs/GraphTab";
import PropertiesTab from "./tabs/PropertiesTab";
import SpaceViewer3D from "./tabs/SpaceViewer3D";
import AnalysisSnapshotTab from "./tabs/AnalysisSnapshotTab";
import UsageTab from "./tabs/UsageTab";
import HeightRestrictionTab from "./tabs/HeightRestrictionTab";

const TABS = [
  { key: "dashboard", label: "ダッシュボード", Component: Dashboard },
  { key: "elements", label: "要素同期", Component: ElementsTab },
  { key: "search", label: "意味検索", Component: SearchTab },
  { key: "legal-search", label: "法令検索", Component: LegalSearchTab },
  { key: "agent", label: "AIエージェント", Component: AgentChatTab },
  { key: "legal-report", label: "法規レポート", Component: LegalReportTab },
  { key: "height-restriction", label: "高さ制限", Component: HeightRestrictionTab },
  { key: "graph", label: "空間関係グラフ", Component: GraphTab },
  { key: "viewer3d", label: "3Dビュー", Component: SpaceViewer3D },
  { key: "analysis-db", label: "解析結果DB", Component: AnalysisSnapshotTab },
  { key: "usage", label: "利用状況", Component: UsageTab },
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
