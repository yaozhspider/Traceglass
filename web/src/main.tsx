import React from 'react';
import {createRoot} from 'react-dom/client';
import {AccountShell} from './features/account/AccountShell';
import './styles.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><AccountShell/></React.StrictMode>);
