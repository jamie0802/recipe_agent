import React, { useState } from 'react';
import {jwtDecode} from "jwt-decode";
import '../style/auth.css';
import { useNavigate, Link } from 'react-router-dom';

function Login({ setUser }) { //從父元件傳來的function用來儲存目前登入的使用者
    const [username, setUsername] = useState(''); //setUsername用來修改username的函式（初始是空字串）
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const navigate = useNavigate(); //取得跳頁函式，等等登入成功會用它跳轉

    const handleLogin = async (e) => { //當表單submit會觸發
        e.preventDefault(); //阻止瀏覽器「預設重新整理行為」，不然按下登入會整頁刷新
        try {
            const res = await fetch('http://localhost:8000/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json(); //把後端回傳的pydantic模型轉成JS物件
        if (res.ok) { //如果HTTP status是200-299
            localStorage.setItem("access_token", data.access_token); //把token存在localStorage（瀏覽器提供的簡單key-value儲存空間）

            const payload = jwtDecode(data.access_token); //解析JWT拿到payload
            setUser(payload.sub);//使用者id通常放在payload的sub欄位

            navigate('/agent'); //App裡的顯示內容從Login變成AgentPage
        } else {
            setError(data.message || '登入失敗');
        }
        } catch (err) { //API server沒開或連線失敗
            setError('網路錯誤');
        }
    };

        return ( //回傳JSX畫面
            <div className="auth-container">
                <div className="auth-card">
                    <h2 className="auth-title">登入</h2>
                    <form className="auth-form" onSubmit={handleLogin}>
                        <input //帳號輸入框
                            className="auth-input"
                            type="text"
                            placeholder="Username"
                            value={username} //綁定state
                            onChange={e => setUsername(e.target.value)} //每次在輸入框打字，onChange觸發，input顯示的值永遠跟username同步
                            //setUsername(e.target.value)會更新state，更新state後，React會重新render然後把username更新成最新的值
                            required
                        /> 
                        <input
                            className="auth-input"
                            type="password"
                            placeholder="密碼"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                        />{/*type="submit" → 會觸發 form 的 onSubmit*/}
                        <button className="auth-button" type="submit">登入</button>
                    </form>
                    {error && <p className="auth-error">{error}</p>}
                    <p className="auth-footer">沒有帳號？ <Link to="/register">註冊</Link></p>
                </div>
            </div>
        );
}

export default Login;