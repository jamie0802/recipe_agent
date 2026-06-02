import React, { useRef, useEffect ,useState} from "react";
import '../style/AgentPage.css';
import ChatBox from "../components/ChatBox";
import PracticeBox from "./PracticePage";

function AgentPage({ user }) {
    const [messages, setMessages] = useState([]);//初始為空陣列
    const [inputValue, setInputValue] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    // 控制目前分頁的狀態 ('ai' 或 'practice')一個是agent聊天一個是語音跟練版
    const [activeTab, setActiveTab] = useState('ai');
    const [currentStep, setCurrentStep] = useState(0);
    const [practiceRecipe, setPracticeRecipe] = useState(null);
    const commandHandlerRef = useRef(null);//用於呼叫子元件的語音指令處理函式
    const activeTabRef = useRef(activeTab);//讓 WebSocket 的 onmessage 永遠讀到最新的 activeTab

    // 全域唯一音訊Ref：統一管理AI回覆語音 & Practice步驟語音
    // 無論哪個頁面播放，都寫入這個Ref，停止時也從這裡停
    const globalAudioRef = useRef(null);

    // 停止全域語音的函式（AgentPage自己用(practice有一個自己停止的)）
    const stopGlobalAudio = () => {
        if (globalAudioRef.current) {
            globalAudioRef.current.pause();
            globalAudioRef.current.currentTime = 0;
            globalAudioRef.current = null;
        }
    };

    // 當 activeTab 改變時，同步更新 Ref，切換頁面不停止語音
    useEffect(() => {
        activeTabRef.current = activeTab;
    }, [activeTab]);

    const [isListening, setIsListening] = useState(false);
    const socketRef = useRef(null);

    const handleSendRef = useRef(null);

    useEffect(() => {// 建立全域唯一連線
        const ws = new WebSocket(`ws://localhost:8000/ws/voice`);
        socketRef.current = ws;

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log("收到訊息:", data);

            if (data.type === "status") {
                if (data.status === "start_recording") {
                    setIsListening(true);

                    // 喚醒詞觸發：無論在哪個頁面，立即停止所有語音輸出
                    stopGlobalAudio();

                    // 同時通知 PracticeBox 清除其內部 audioRef（防止殘留）
                    if (commandHandlerRef.current) {
                        commandHandlerRef.current({ action: "stop" });
                    }

                    const audio = new Audio("/Ding.mp3");
                    audio.play();
                }
                if (data.status === "stop_recording") setIsListening(false);
                return;
            }

            const voiceText = data.text;
            if (!voiceText) return;

            // 語音切換頁面邏輯 
            if (voiceText.includes("切換") || voiceText.includes("換到")) {
                if (voiceText.includes("跟練") || voiceText.includes("練習") || voiceText.includes("模式")) {
                    setActiveTab('practice');
                    return; // 切換完即結束，不把文字輸入框
                } else if (voiceText.includes("助理") || voiceText.includes("聊天") || voiceText.includes("AI")) {
                    setActiveTab('ai');
                    return;
                } else {
                    // 若只說「切換頁面」，則直接對調目前狀態
                    setActiveTab(prev => prev === 'ai' ? 'practice' : 'ai');
                    return;
                }
            }


            if (activeTabRef.current === "practice") {
                if (commandHandlerRef.current) {
                    commandHandlerRef.current(voiceText);//呼叫 PracticeBox 註冊的函式
                }
            } else if (activeTabRef.current === "ai") {

                if (voiceText.includes("送出")) {
                    const cleanText = voiceText.replace(/送出/g, "").trim();
                    setInputValue(prev => {
                        const finalMessage = (prev + " " + cleanText).trim();
                        if (finalMessage && !isSendingRef.current) {
                            handleSendRef.current(finalMessage);
                        }
                        return "";
                    });
                } else if (voiceText.includes("清除") || voiceText.includes("清空")) {
                    setInputValue("");
                } else {
                    setInputValue(prev => (prev + " " + voiceText).trim());
                }
            }
        };
        return () => ws.close();
    }, []);

    //AI回覆語音播放，呼叫前會先停止任何正在播放的語音（包含Practice的步驟語音）
    const playAgentVoice = (text) => {
        if (!text) return;

        // 停止目前所有語音（含Practice步驟語音）
        stopGlobalAudio();
        // 同步清除 PracticeBox 內部的 audioRef
        if (commandHandlerRef.current) {
            commandHandlerRef.current({ action: "stop" });
        }

        try {
            const audioUrl = `http://localhost:8000/fridge/speak?text=${encodeURIComponent(text)}`;
            const audio = new Audio(audioUrl);
            globalAudioRef.current = audio;
            audio.play().catch(e => {
                console.warn("AI 語音播放失敗或被瀏覽器攔截:", e);
            });
            audio.onended = () => {
                globalAudioRef.current = null;
            };
        } catch (err) {
            console.error("建立 AI 語音物件出錯:", err);
        }
    };

    const handleSearchRecipe = async (name) => {
        setIsLoading(true);
        try {
            const res = await fetch("http://localhost:8000/recipe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name })// 傳送食譜名稱
            });

            const data = await res.json();
            if (data.status === "success") {
                setPracticeRecipe(data); // 包含 ingredients 和 steps 所以practicepage就可以直接用
                setCurrentStep(0); // 搜尋到新食譜後，重置步驟到第一步;
            } else {
                alert("找不到食譜：" + data.message);
            }
        } catch (err) {
            console.error("搜尋食譜失敗", err);
        } finally {
            setIsLoading(false);
        }
    };

    // 當語音轉文字回來時把它加到輸入框(給錄音鈕用的)
    const handleVoiceText = (text) => {
        setInputValue(prev => prev + text);
    };

    // 處理輸入框變化(處理使用者手動輸入)
    const handleInputChange = (e) => {
        setInputValue(e.target.value);
    };

    const isSendingRef = useRef(false);//用來防止重複發送的同步鎖
    const handleSend = async (textFromVoice) => {
        if (isLoading || isSendingRef.current) return;

        // 優先使用傳入的文字，否則才看輸入框的狀態(條件 ? 條件成立時的值 : 條件不成立時的值)
        const messageContent = typeof textFromVoice === 'string' ? textFromVoice : inputValue;
        if (!messageContent.trim()) return;//內容是空白的不送出

        const userMessage = {
            id: Date.now(),
            type: "user",
            content: messageContent,
            timestamp: new Date()
        };

        isSendingRef.current = true;
        setMessages(prev => [...prev, userMessage]);
        //如果沒有這句，你的網頁聊天視窗永遠只會顯示 1 則訊息（最新那則），舊的會全部消失
        setInputValue(""); // 送出後清空輸入框
        setIsLoading(true);

        try {
            const token = localStorage.getItem("access_token");
            const res = await fetch("http://localhost:8000/ask", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${token}`
                },
                body: JSON.stringify({
                    question: userMessage.content
                })
            });

            if (res.status === 401) {
                alert("登入逾時或無效，請重新登入");
                localStorage.removeItem("access_token"); // 清除過期的 Token
                window.location.href = "/login"; // 回登入頁面
                return;
            }

            const data = await res.json();
            const replyText = data.reply || "Agent 無回覆";
            // 後端回覆訊息加入
            const agentMessage = {
                id: Date.now() + 1,
                type: "agent",
                content: replyText,
                timestamp: new Date()
            };
            setMessages(prev => [...prev, agentMessage]);

            // AI 回覆完成後自動語音輸出，會打斷任何正在播放的 Practice 步驟語音
            playAgentVoice(replyText);

        } catch (err) {
            const errorMessage = {
                id: Date.now() + 2,
                type: "agent",
                content: "後端錯誤，請稍後再試",
                timestamp: new Date()
            };
            setMessages(prev => [...prev, errorMessage]);
        } finally {
            setIsLoading(false);
            isSendingRef.current = false;
        }
    };
    handleSendRef.current = handleSend;// 每次 render 都會跑這行，ref永遠是最新版本

    return (
        <div className="agent-container">
            <div className="top-nav">
                <div className="nav-logo">Smart Kitchen assistant</div>
                <div className="nav-menu">
                    <button
                        className={`nav-item ${activeTab === 'ai' ? 'active' : ''}`}
                        onClick={() => setActiveTab('ai')}
                    >
                        AI助理回覆
                    </button>
                    <button
                        className={`nav-item ${activeTab === 'practice' ? 'active' : ''}`}
                        onClick={() => setActiveTab('practice')}
                    >
                        語音烹飪模式
                    </button>
                </div>
                <div className="user-info">☺︎ {user}</div>
            </div>

            <div className="main-content">
                {isListening && (
                    <div className="voice-overlay">
                        <div className="voice-content">
                            <div className="waveform-container">
                                <span className="wave-bar"></span>
                                <span className="wave-bar"></span>
                                <span className="wave-bar"></span>
                                <span className="wave-bar"></span>
                                <span className="wave-bar"></span>
                            </div>
                            <p className="voice-status-text">正在傾聽您的指令...</p>
                        </div>
                    </div>
                )}

                {activeTab === 'ai' ? (
                    <div className={`chat-section ${isListening ? "content-disabled" : ""}`}>
                        <div className="agent-header">
                            <h2>AI智能助理</h2>
                            <p>詢問食譜、調整口味喜好或查詢食譜食材與步驟</p>
                        </div>
                        <ChatBox
                            messages={messages}
                            inputValue={inputValue}
                            onInputChange={handleInputChange}
                            onSend={handleSend}
                            onVoiceText={handleVoiceText}
                            isLoading={isLoading}
                        />
                    </div>
                ) : (
                    <div className={`practice-section ${isListening ? "content-disabled" : ""}`} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                        <div className="agent-header">
                            <h2>語音烹飪</h2>
                            <p>請搜尋食譜並說「開始料理」</p>
                        </div>
                        <PracticeBox
                            recipeData={practiceRecipe}
                            currentStep={currentStep}
                            setCurrentStep={setCurrentStep}
                            onSearch={handleSearchRecipe}
                            isLoading={isLoading}
                            commandHandlerRef={commandHandlerRef}
                            isListening={isListening}
                            globalAudioRef={globalAudioRef}
                        />
                    </div>
                )}
            </div>
        </div>
    );
}

export default AgentPage;