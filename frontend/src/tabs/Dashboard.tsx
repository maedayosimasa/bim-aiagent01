import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import ArchicadConnection from "../ArchicadConnection";

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
    </>
  );
}

export default Dashboard;
