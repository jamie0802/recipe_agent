// src/Register.js
import React, { useState } from 'react';
import '../style/auth.css';
import { useNavigate, Link } from 'react-router-dom';

function Register() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [message, setMessage] = useState('');
    const navigate = useNavigate();

    const handleRegister = async (e) => {
        e.preventDefault();
        try {
            const res = await fetch('http://localhost:8000/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            const data = await res.json();
            if (res.ok) {
                setMessage('註冊成功！1 秒後跳轉登入頁...');
                setTimeout(() => navigate('/login'), 1000);
            } else {
                setMessage(data.message || '註冊失敗');
            }
        } catch (err) {
            setMessage('網路錯誤');
        }
    };

    return (
            <div className="auth-container">
                <div className="auth-card">
                    <h2 className="auth-title">註冊</h2>
                    <form className="auth-form" onSubmit={handleRegister}>
                        <input
                            className="auth-input"
                            type="text"
                            placeholder="Username"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            required
                        />
                        <input
                            className="auth-input"
                            type="password"
                            placeholder="密碼"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                        />
                        <button className="auth-button" type="submit">註冊</button>
                    </form>
                    {message && <p className="auth-message">{message}</p>}
                    <p className="auth-footer">已經有帳號？ <Link to="/login">登入</Link></p>
                </div>
            </div>
        );
}

export default Register;