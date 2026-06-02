import React, { useRef, useEffect } from "react";
import '../style/ChatBox.css';

function ChatBox({ 
    messages,        // 訊息陣列，由上層傳入
    inputValue,      // 輸入框值，由上層控制
    onInputChange,   // 輸入框變動事件，由上層提供
    onSend,          // 發送事件，由上層提供
    onVoiceText,     // 語音轉文字結果，由上層提供
    isLoading        // 是否顯示 loading，由上層傳入
}) {
    const messagesEndRef = useRef(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const handleKeyPress = (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
        }
    };

    return (
        <div className="chatbox-container">
            <div className="chatbox-messages">
                {messages.length === 0 ? (
                    <div className="chatbox-empty">
                        <p>開始與Agent聊天</p>
                        <p className="empty-hint">向Agent提出問題或請求</p>
                    </div>
                ) : (
                    <>
                        {messages.map((message) => (
                            <div
                                key={message.id}
                                className={`chatbox-message ${message.type}`}
                            >
                                <div className={`message-bubble ${message.type}`}>
                                    {message.content}
                                </div>
                                <span className="message-time">
                                    {message.timestamp.toLocaleTimeString()}
                                </span>
                            </div>
                        ))}
                        {isLoading && (
                            <div className="chatbox-message agent">
                                <div className="message-bubble agent loading">
                                    <span className="dot"></span>
                                    <span className="dot"></span>
                                    <span className="dot"></span>
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </>
                )}
            </div>

            <div className="chatbox-input-container">
                <textarea
                    className="chatbox-input"
                    value={inputValue}
                    onChange={onInputChange}
                    onKeyPress={handleKeyPress}
                    placeholder="輸入你的問題或請求... (Shift+Enter換行)"
                    rows="3"
                />
                <button
                    className="chatbox-send-btn"
                    onClick={onSend}
                    disabled={!inputValue.trim() || isLoading}
                >
                    <span>發送</span>
                    <span className="send-icon">→</span>
                </button>
            </div>
        </div>
    );
}

export default ChatBox;