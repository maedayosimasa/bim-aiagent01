import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import ArchicadConnection from "../ArchicadConnection";
import LegalKnowledgeBuilderLauncher from "../LegalKnowledgeBuilderLauncher";

function Dashboard() {
  const [message, setMessage] = useState<string>("Loading...");

  useEffect(() => {
    getHealth()
      .then((data) => setMessage(data.status))
      .catch(() => setMessage("Connection Error"));
  }, []);

  return (
    <>
      <h1>BIM空間知能エンジン</h1>
      <h2>{message}</h2>
      <ArchicadConnection />
      <LegalKnowledgeBuilderLauncher />
    </>
  );
}

export default Dashboard;
