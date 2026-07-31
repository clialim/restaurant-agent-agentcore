import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@cloudscape-design/global-styles/index.css';
import App from './App.jsx';

// 진입점에서 Cloudscape 글로벌 스타일만 한 번 로드합니다.
// Vite 템플릿 기본 CSS(index.css)는 Cloudscape 레이아웃과 충돌하므로 제거했습니다.
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
