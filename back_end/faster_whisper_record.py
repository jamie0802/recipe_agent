from faster_whisper import WhisperModel
import io
from pvrecorder import PvRecorder #Picovoice提供的麥克風錄音工具，專門配合 Porcupine 使用
import pvporcupine #匯入 Porcupine 語音喚醒引擎，用來偵測喚醒詞（wake word）
import tempfile #建立暫存檔案
import os
import asyncio
import time
import numpy as np
import webrtcvad #pip install webrtcvad-wheels(已編譯版本)
import soundfile as sf #pip install soundfile(用來寫入 wav 檔案)

model = WhisperModel("small", device="cpu")
#Whisper 的 transcribe() 需要的是:檔案路徑or檔案物件
async def transcribe_audio(file):
    audio_bytes = await file.read()
    audio_buffer = io.BytesIO(audio_bytes) #io.BytesIO:把 bytes資料轉成記憶體中的檔案物件(就是轉成可讀取檔案的意思)
    segments, info = model.transcribe(audio_buffer, beam_size=5,initial_prompt="以下是繁體中文內容：" )#會回傳segement跟info(語音資訊:語言...)

    full_text = " ".join([segment.text for segment in segments])#用空格合併
    return full_text

class VoiceListener:
    def __init__(self, api_key, wake_word="picovoice", sensitivity=0.5):
        self.model = WhisperModel("small", device="cpu")
        self.vad = webrtcvad.Vad(3) #建立了一個WebRTC VAD，並設定了靈敏度(用來判斷每個frame是否有語音)
        self.porcupine = pvporcupine.create(
            access_key=api_key,
            keywords=[wake_word],
            sensitivities=[sensitivity]
        )#建立了喚醒詞偵測器
        #self.frame_length = self.porcupine.frame_length
        self.recorder = PvRecorder(frame_length=self.porcupine.frame_length, device_index=-1)#建立錄音器
        #frame_length 是 Porcupine 物件內建的屬性(每次處理音訊時，需要的「音訊樣本數（samples）)， -1 = 系統的預設麥克風
        self.last_trigger = 0    #上一次喚醒詞觸發時間
        self.cooldown = 2        #冷卻時間，單位秒
        self.running = False  # 控制loop運作用
        self.websockets = {"ai": [], "practice": []} # 分組儲存 WebSocket 連線

    def start(self):
        self.recorder.start() #開始監聽
        self.running = True
        print("Listening for wake word...")

    def close(self):
        self.running = False
        try:
            self.recorder.stop()
            self.recorder.delete()
            self.porcupine.delete()
        except:
            pass

    #WebSocket 傳輸是非同步 I/O
    async def broadcast(self, text):
        for tab, ws_list in self.websockets.items():#回傳的格式(tab,對應的websocket列表)
            for ws in ws_list[:]:
                try:
                    # 傳送文字與來源標籤，讓前端知道這是語音辨識來的
                    await ws.send_json({"source": "voice", "tab": tab, "text": text})
                except Exception:
                    ws_list.remove(ws)# 如果傳送失敗（例如前端已關閉視窗），就從列表中移除該連線

    # 新加的 因為要向前端傳送我正在錄音
    async def broadcast_status(self, status):
        for tab, ws_list in self.websockets.items():
            for ws in ws_list[:]:
                try:
                    await ws.send_json({"type": "status", "status": status})
                except Exception as e:
                    ws_list.remove(ws)

    async def start_loop(self):
        self.start()
        try:
            while self.running:
                text = await self.listen_once()#把 listen_once 丟到另一條 thread 跑
                if text:
                    print(f"語音辨識成功: {text}")
                    await self.broadcast(text) # 改用廣播機制
                await asyncio.sleep(0.01)
        finally:
            self.close()

    async def listen_once(self, fs=16000): 
        try:
            pcm = await asyncio.to_thread(self.recorder.read)
        except Exception as e:
            print(f"Error reading from recorder: {e}")
            return None
        
        keyword_index = self.porcupine.process(pcm)#判斷是否偵測到喚醒詞

        if keyword_index >= 0:
            now = time.time()
            #如果喚醒詞剛被觸發過，2 秒內再次偵測到也不會啟動錄音
            if now - self.last_trigger < self.cooldown:
                return None
            self.last_trigger = now

            print("Wake word detected! Recording command...")
            await self.broadcast_status("start_recording")
            await asyncio.sleep(0.1) # 稍微讓出 event loop

            print(">>> 進入錄音模式，請說話...", flush=True)
            # 用 PvRecorder錄
            audio = await asyncio.to_thread(self.record_until_silence, fs)
            
            await self.broadcast_status("stop_recording")
            if audio is None:
                return None
                
            audio = audio.astype(np.float32) / 32768.0 #將整數音訊標準化到 [-1, 1] 範圍

            segments, _ = await asyncio.to_thread(self.model.transcribe, audio)
            text = "".join([segment.text for segment in segments])
            return text
            
        return None
    
    #用來錄音直到偵測到連續靜音或超過最大時間
    def record_until_silence(self, fs,silence_duration=1.0, max_duration=20):
        silence_counter = 0 #記錄連續靜音的秒數
        start_time = time.time() #記錄錄音開始時間，用來判斷是否超過 max_duration
        recording=[] #用來存每個 frame 的錄音
        speech_started = False  # 一開始沒偵測到人聲
        # 每個 frame 30ms，符合 WebRTC VAD 建議
        frame_ms = 30
        frame_samples = int(fs * frame_ms / 1000)
        
        buffer = np.array([], dtype=np.int16)#buffer 用來暫存讀取的 PCM 音訊，方便切成固定長度的 frame 給VAD判斷是否有語音
        while True:
            pcm = self.recorder.read()
            frame = np.array(pcm, dtype=np.int16)#將讀取到的PCM音訊轉成 numpy array，數據型態為 16-bit整數
            recording.append(frame)
            buffer = np.concatenate([buffer, frame])

            # 將 frame 拆成 30ms 給 VAD
            while len(buffer) >= frame_samples:
                frame = buffer[:frame_samples]
                buffer = buffer[frame_samples:]

                is_speech = self.vad.is_speech(frame.tobytes(), fs)#self.vad → WebRTC VAD 實例
                #frame.tobytes() → 將 numpy array 轉成 bytes 格式給 VAD
                if is_speech:
                    speech_started = True
                    silence_counter = 0
                else:
                    if speech_started:
                        silence_counter += frame_ms / 1000  # 30ms

            if speech_started and silence_counter >= silence_duration:
                print(f"偵測{silence_duration}靜音，停止錄音")
                break

            if time.time() - start_time > max_duration:
                print("達最大錄音時間")
                break

        if not recording:
            return None

        return np.concatenate(recording)