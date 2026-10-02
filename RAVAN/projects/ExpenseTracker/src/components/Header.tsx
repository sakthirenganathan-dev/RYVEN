import React from 'react';

interface HeaderProps {
  title: string;
  status: string;
}

export const Header: React.FC<HeaderProps> = ({ title, status }) => {
  return (
    <header className="app-header">
      <div className="logo-badge">RYVEN DEV</div>
      <h1>{title}</h1>
      <span className="status-indicator">● {status}</span>
    </header>
  );
};
