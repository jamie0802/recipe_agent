//這只是把React App掛到html

import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import reportWebVitals from './reportWebVitals'; //效能監測工具（可以監測loading time）

const root = ReactDOM.createRoot(document.getElementById('root')); //找到html理這個元素<div id="root"></div>
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
); //把App.js渲染root

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals(); //加了console.log參數可以印出效能數據
