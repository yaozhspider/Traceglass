import React from "react";
import { createRoot } from "react-dom/client";
import { WorkbenchShell } from "./features/workspaces/WorkbenchShell";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkbenchShell runId={new URLSearchParams(location.search).get("run") ?? "run-demo"} />
  </React.StrictMode>,
);
