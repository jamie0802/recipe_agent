//主要路由控制元件

import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
//控制整個SPA路由，Routes裡放所有Route，Route放單一路徑和對應的Component，Navigate用來重定向路徑
import Login from './pages/Login';
import Register from './pages/Register';
import AgentPage from './pages/AgentPage';

function App() { //整個網站的根元件
  const [user, setUser] = React.useState(null); //整個網站共用的登入狀態（user存使用者名稱）
  //Login可以把user傳給AgentPage，AgentPage可以顯示使用者資訊也可以知道是誰登入
  return (
    //控制路由跳轉
    <Router> 
      <Routes>
        {/* 根目錄直接導到登入頁 */}
        <Route 
          path="/"
          element={
            <Navigate to="/login" />
          }
        />

        {/* 登入頁，登入成功會呼叫 setUser */}
        <Route
          path="/login" 
          element={
            user
              ? <Navigate to="/agent" />
              : <Login setUser={setUser} /> //我是在App.js定義狀態，但登入成功的判斷發生在Login.js裡，所以我Login.js必須要有SetUser才可以在登入成功時改寫user（App.js這邊的user才會有值）
          } //如果user有值（已登入）就導到Agent頁面，沒有值（未登入）就顯示Login元件
        />

        {/* 註冊頁 */}
        <Route 
          path="/register"
          element={
            <Register />
          }
        />

        {/* Agent頁面 */}
        <Route 
          path="/agent" 
          element={
            user 
              ? <AgentPage user={user} /> //如果App.js現在的user有值（已登入），就跳轉到Agent頁面 
              : <Navigate to="/login" /> //user還是null就強制跳回Login
          }
        />
      </Routes>
    </Router>
  );
}

export default App;