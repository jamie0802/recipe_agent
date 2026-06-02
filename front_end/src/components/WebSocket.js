export function createVoiceWebSocket(onText) {
    //參數 onText 是一個 callback 函式，當收到文字訊息時會被呼叫
    //export：讓其他檔案可以 import 這個函式
    const ws = new WebSocket("ws://localhost:8000/ws/voice");
    
    ws.onopen = () => {//onopen 事件處理器，當 WebSocket 連線建立成功時觸發
        console.log("WebSocket 連線成功");
    };

    ws.onmessage = (event) => {
        console.log("收到 WebSocket 訊息:", event.data);
        try {
            const data = JSON.parse(event.data); //把字串轉成物件
            if (data.text && onText) {//如果有text、onText 存在->呼叫 callback
                onText(data.text); 
                //ws.onmessage 收到訊息後->解析 JSON->如果有 text 屬性且 onText 存在->呼叫 onText(data.text) 把文字丟給父元件
            }
        } catch (err) {
            console.error("解析 WebSocket 訊息失敗:", err);
        }
    };

    ws.onerror = (err) => {//連線發生錯誤時觸發
        console.error("WebSocket 錯誤:", err);
    };

    ws.onclose = () => {
        console.log("WebSocket 連線已關閉");
    };

    return ws;
}
