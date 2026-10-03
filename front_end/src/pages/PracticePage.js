import React, { useState, useEffect, useRef } from "react";
import "../style/PracticePage.css";

// 接收 globalAudioRef：由 AgentPage 傳入，用於統一管理跨頁面的語音播放
// Practice 播放步驟語音前，會把音訊物件同時寫入 globalAudioRef，
// 這樣 AgentPage 收到 AI 回覆或喚醒詞時也能正確停掉 Practice 正在播放的聲音
function PracticePage({ recipeData, currentStep, setCurrentStep, onSearch, isLoading, commandHandlerRef, isListening, globalAudioRef }) {
    const [input, setInput] = useState("");

    // 每次播放時都會同步更新 globalAudioRef，確保 AgentPage 也能打斷
    const audioRef = useRef(null);//來存儲當前播放中的音訊物件
    /*
    const stopVoice = () => {//停止音訊的函式(只有停止practice的)
        if (audioRef.current) {
            audioRef.current.pause();
            audioRef.current.currentTime = 0;// 回到開頭
            audioRef.current = null;
        }
        // (為了不讓agentpage認為我還在撥放，所以要清空)如果 globalAudioRef 指向的就是 Practice 的音訊
        if (globalAudioRef && globalAudioRef.current === audioRef.current) {
            globalAudioRef.current = null;
        }
    };*/

    const stopVoice = () => {
        const current = audioRef.current;
        if (current) {
            current.pause();
            current.currentTime = 0;
            audioRef.current = null;
        }
        if (globalAudioRef && globalAudioRef.current === current) {
            globalAudioRef.current = null;
        }
    };

    const handleStepClick = (index) => {//點擊步驟時觸發
        setCurrentStep(index);
        playVoice(recipeData.steps[index]);
    };

    useEffect(() => {
        if (recipeData) {
            setCurrentStep(0);//重置 currentStep 為 0
        }
    }, [recipeData]);//當 recipeData 改變時觸發

    const chineseNumbers = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9
    };

    const extractChineseNumber = (text) => {
        if (text.includes("十")) {
            const parts = text.split("十");//用十去拆分，like:二十->["二", ""]
            const tens = parts[0] ? chineseNumbers[parts[0]] : 1;
            const units = parts[1] ? chineseNumbers[parts[1]] : 0;
            return tens * 10 + units;
        }
        for (const key in chineseNumbers) {// 單一數字
            if (text.includes(key)) {
                return chineseNumbers[key];
            }
        }
        return null;
    };

    // 提取數字的邏輯
    const extractStepNumber = (text) => {
        const matchDigit = text.match(/\d+/);
        if (matchDigit) return parseInt(matchDigit[0], 10);//轉成整數回傳，10進制
        const number = extractChineseNumber(text);
        if (number != null) return number;
        return null;
    };

    const handleSearch = (manualInput) => {// 搜尋
        const searchTerm = (typeof manualInput === 'string' ? manualInput : input).trim();
        if (!searchTerm) return;
        onSearch && onSearch(searchTerm);//如果 onSearch 存在，呼叫他(在父元件)
        setInput("");
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // 防止換行
            handleSearch();
        }
    };

    const restart = () => {
        if (recipeData && recipeData.steps.length > 0) {
            setCurrentStep(0);
            playVoice(recipeData.steps[0]);
        }
    };

    const nextStep = () => {
        if (recipeData && currentStep < recipeData.steps.length - 1) {
            const next = currentStep + 1;
            setCurrentStep(next);
            playVoice(recipeData.steps[next]);
        }
    };

    const prevStep = () => {
        if (currentStep > 0) {
            const prev = currentStep - 1;
            setCurrentStep(prev);
            playVoice(recipeData.steps[prev]);
        }
    };

    const isjump = (text) => {
        const stepNumber = extractStepNumber(text);
        if (stepNumber !== null && recipeData) {
            const targetIndex = stepNumber - 1;
            if (targetIndex >= 0 && targetIndex < recipeData.steps.length) {
                setCurrentStep(targetIndex);
                playVoice(recipeData.steps[targetIndex]);
            }
        }
    };

    //流程:先判斷是不是指令->如果是指令就處理指令(包含搜尋)(不是指令就放到input欄位)-->最後清空輸入框
    useEffect(() => {
        if (commandHandlerRef) {
            commandHandlerRef.current = (text) => {
                //console.log("語音指令辨識中:", text);//debug用
                if (typeof text === "string") {
                    const isControlCommand =
                        text.includes("下一步") || text.includes("繼續") || text.includes("继续") ||
                        text.includes("上一步") ||
                        text.includes("開始料理") || text.includes("開始") || text.includes("开始") || text.includes("开始料理") ||
                        text.includes("重來") || text.includes("再一次") || text.includes("再說一遍") ||
                        text.includes("清空") || text.includes("清除") ||
                        text.includes("跳到") || text.includes("第");

                    const isSearchCommand = text.includes("送出") || text.includes("搜尋");

                    if (isControlCommand) {
                        // 純指令，直接執行，不留文字在輸入框
                        if (text.includes("下一步") || text.includes("繼續") || text.includes("继续")) nextStep();
                        else if (text.includes("上一步")) prevStep();
                        else if (text.includes("開始") || text.includes("开始") || text.includes("開始料理") || text.includes("开始料理")) restart();
                        else if (text.includes("重來") || text.includes("再一次") || text.includes("再說一遍")) playVoice(recipeData?.steps[currentStep]);
                        else if (text.includes("清空") || text.includes("清除")) setInput("");
                        else if (text.includes("跳到") || text.includes("第")) isjump(text);

                        if (!text.includes("清空")) setInput("");
                    } else if (isSearchCommand) {
                        const cleanText = text.replace(/搜尋|送出/g, "").trim();
                        if (cleanText) {
                            // console.log("搜尋:", cleanText);
                            handleSearch(cleanText); //「蛋糕送出」 -> 查蛋糕
                        } else {
                            handleSearch(input);//「送出」 -> 查目前框內的文字
                        }
                        setInput("");// 送出後清空
                    } else {
                        setInput(text);
                    }
                } else if (text && text.action === "stop") {
                    // 收到停止訊號（喚醒詞觸發 or AI 回覆要播放）：停止 Practice 語音
                    stopVoice();
                    //console.log("收到中斷訊號，停止語音輸出");
                }
            };
        }
    }, [currentStep, recipeData, commandHandlerRef, input]);

    // 播放步驟語音,同時把音訊物件寫入globalAudioRef,讓AgentPage也能在必要時停掉它
    const playVoice = (text) => {
        if (!text) return;

        stopVoice();// 先停止本地正在播放的語音

        // 如果 globalAudioRef 有其他來源的音訊（AI回覆語音），一起停掉
        if (globalAudioRef && globalAudioRef.current) {
            globalAudioRef.current.pause();
            globalAudioRef.current.currentTime = 0;
            globalAudioRef.current = null;
        }

        try {
            const audioUrl = `http://localhost:8000/fridge/speak?text=${encodeURIComponent(text)}`;
            const audio = new Audio(audioUrl);

            // 寫入本地Ref與全域Ref
            audioRef.current = audio;
            if (globalAudioRef) globalAudioRef.current = audio;

            audio.play().catch(e => {
                console.warn("播放失敗或被瀏覽器攔截:", e);
            });

            audio.onended = () => {//當音訊（audio）播放「完全結束」時會自動觸發
                audioRef.current = null;// 播放結束後清空 Ref
                //only當全域Ref還指向這個音訊時才清除，避免誤清除AI語音
                if (globalAudioRef && globalAudioRef.current === audio) {
                    globalAudioRef.current = null;
                }
            };
        } catch (err) {
            console.error("建立音訊物件出錯:", err);
        }
    };

    return (
        <div className="practice-box">
            {/* 搜尋區 */}
            <div className="practice-search">
                <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}//當值改變時觸發
                    onKeyDown={handleKeyDown}
                    placeholder="輸入食譜名稱"
                />
                <button onClick={handleSearch}>搜尋</button>
            </div>

            {isLoading && <p>載入中...</p>}{/*真 (true) → 顯示 <p> */}

            {recipeData && (
                <div className="recipe-area">
                    <div className="recipe-header">
                        <h3>{recipeData.name}</h3>

                        <div className="recipe-info">
                            {recipeData.cooking_time && (
                                <div className="info-item">
                                    <span className="info-label">⏱ 烹飪時間</span>
                                    <span className="info-value">{recipeData.cooking_time}</span>
                                </div>
                            )}
                            <div className="info-item">
                                <span className="info-label">👥 份量</span>
                                <span className="info-value">{recipeData.servings || 1}</span>
                            </div>
                        </div>
                    </div>

                    {/* 食材 */}
                    <div className="ingredients">
                        <h4>食材</h4>
                        <ol>
                            {recipeData.ingredients?.map((item, i) => (
                                <li key={i}>{item}</li>
                            ))}
                        </ol>
                    </div>

                    {/* 控制按鈕 */}
                    <div className="controls">
                        <button onClick={restart}>開始料理</button>
                        <button onClick={prevStep}>上一步</button>
                        <button onClick={nextStep}>下一步</button>
                    </div>

                    {/* 步驟 */}
                    <div className="steps">
                        <h4>步驟</h4>
                        <ol>
                            {recipeData.steps.map((step, index) => (
                                <li
                                    key={index}
                                    onClick={() => handleStepClick(index)}//被點擊時呼叫，這樣就可以知道在第幾步
                                    className={index === currentStep ? "active-step" : ""}
                                >
                                    {step}
                                </li>
                            ))}
                        </ol>
                    </div>
                </div>
            )}
        </div>
    );
}

export default PracticePage;