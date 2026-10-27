import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import Dashboard from "./Dashboard.jsx";
import "./index.css";

// "#/admin" shows the operations dashboard; anything else shows the booking page.
const isAdmin = window.location.hash.startsWith("#/admin");
window.addEventListener("hashchange", () => window.location.reload());

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>{isAdmin ? <Dashboard /> : <App />}</React.StrictMode>
);
