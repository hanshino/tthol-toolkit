import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { installGlobalErrorHooks } from './diag/report';
import './styles.css';

// Before render: an error thrown during the first paint should still be
// recorded, not lost to a console nobody reads.
installGlobalErrorHooks();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
