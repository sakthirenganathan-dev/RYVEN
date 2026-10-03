import React, { useState } from "react";
import { Header } from "./components/Header";

export default function App() {
  const [count, setCount] = useState<number>(0);

  return (
    <div className="container">
      <Header title="ExpenseTracker" status="SYSTEM NOMINAL" />
      <main className="card">
        <h2>Engineered Project: ExpenseTracker</h2>
        <p className="subtitle">
          Features: User Authentication & Session Management, Expense & Budget Tracking Engine
        </p>
        <div className="action-zone">
          <button onClick={() => setCount((c) => c + 1)}>Increment Metric [{count}]</button>
        </div>
      </main>
    </div>
  );
}
