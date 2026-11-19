import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import Dashboard from "./Dashboard.jsx";
import "./index.css";

// "#/admin" shows the operations dashboard; anything else shows the main app.
const isAdmin = window.location.hash.startsWith("#/admin");
window.addEventListener("hashchange", () => window.location.reload());

class AppErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error) {
    console.error("ParcelPoint failed to render:", error);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="main">
          <div className="overline">APPLICATION ERROR</div>
          <h1>ParcelPoint could not load</h1>
          <p className="subtitle">A frontend error prevented the page from rendering. Check the browser console and frontend container logs.</p>
          <div className="error" role="alert">{this.state.error.message || "Unknown rendering error"}</div>
          <button className="primary submit" onClick={() => window.location.reload()}>Reload application</button>
        </main>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppErrorBoundary>
      {isAdmin ? <Dashboard /> : <App />}
    </AppErrorBoundary>
  </React.StrictMode>
);
