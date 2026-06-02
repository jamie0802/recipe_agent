from faster_whisper import WhisperModel
import io
import sounddevice as sd           # pip install sounddevice（麥克風錄音工具，替代 PvRecorder）
from openwakeword.model import Model as OWWModel  # pip install openwakeword（開源喚醒詞偵測，替代 pvporcupine）
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
    # 不再需要 api_key，wake_word 改用 OpenWakeWord 的預訓練模型名稱
    def __init__(self, wake_word="hey_jarvis"):
        self.model = WhisperModel("small", device="cpu")
        self.vad = webrtcvad.Vad(2) #建立了一個WebRTC VAD，並設定了靈敏度(用來判斷每個frame是否有語音)
        self.oww_model = OWWModel(
            wakeword_models=[wake_word],  # 指定預訓練喚醒詞模型（hey_jarvis）
            inference_framework="onnx"    # 使用 ONNX 推論，速度較快
        )
        self.fs = 16000                  # 取樣率，OpenWakeWord 固定需要 16kHz
        self.frame_samples = 1280        # OpenWakeWord 每次需要 1280 samples（80ms @ 16kHz）
        self.last_trigger = 0            #上一次喚醒詞觸發時間
        self.cooldown = 2                #冷卻時間，單位秒
        self.running = False             # 控制loop運作用
        self.websockets = {"ai": [], "practice": []} # 分組儲存 WebSocket 連線
        self._mic_buffer = np.array([], dtype=np.int16)  # 麥克風讀取緩衝區
        self._loop = None               # 【修正】儲存 event loop，讓 callback 跨 thread 安全操作 queue
        self._mic_queue = None          # 【修正】延遲初始化，確保在正確的 event loop 上建立

    def _sd_callback(self, indata, frames, time_info, status):
        # sounddevice 的錄音 callback 在背景 thread 執行
        # 【修正】必須用 call_soon_threadsafe 才能安全地從其他 thread 放資料進 asyncio queue
        pcm = indata[:, 0].copy()  # 取單聲道
        if self._loop is not None and self._mic_queue is not None:
            self._loop.call_soon_threadsafe(self._mic_queue.put_nowait, pcm)

    def start(self):
        # 【修正】在 start() 時才初始化 queue 與 loop，確保綁定到正確的 asyncio event loop
        self._loop = asyncio.get_event_loop()
        self._mic_queue = asyncio.Queue()

        # 開啟 sounddevice 輸入串流（替代 PvRecorder.start()）
        self._stream = sd.InputStream(
            samplerate=self.fs,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,  # 每次 callback 給 1280 samples
            callback=self._sd_callback
        )
        self._stream.start()
        self.running = True
        print("Listening for wake word...")

    def close(self):
        self.running = False
        try:
            self._stream.stop()
            self._stream.close()
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
                text = await self.listen_once()
                if text:
                    print(f"語音辨識成功: {text}")
                    await self.broadcast(text) # 改用廣播機制
                await asyncio.sleep(0.01)
        finally:
            self.close()

    async def listen_once(self, fs=16000):
        try:
            # 從 queue 取出 sounddevice callback 送來的 PCM（替代 PvRecorder.read()）
            pcm = await self._mic_queue.get()
        except Exception as e:
            print(f"Error reading from mic: {e}")
            return None

        #【Debug】確認麥克風有收到聲音，若 max 一直是 0.000 代表麥克風裝置選錯
        print(f"pcm shape: {pcm.shape}, max amplitude: {np.abs(pcm).max()}")

        # OpenWakeWord 需要 float32，範圍 [-1, 1]
        pcm_float = pcm.astype(np.float32) / 32768.0
        prediction = self.oww_model.predict(pcm_float)  # 回傳 dict: {模型名稱: 信心分數}
        print(f"OWW scores: {prediction}")
        # 任一模型的信心分數超過門檻即視為偵測到喚醒詞
        detected = any(score > 0.5 for score in prediction.values())

        if detected:
            now = time.time()
            #如果喚醒詞剛被觸發過，2 秒內再次偵測到也不會啟動錄音
            if now - self.last_trigger < self.cooldown:
                return None
            self.last_trigger = now

            print("Wake word detected! Recording command...")
            await self.broadcast_status("start_recording")
            await asyncio.sleep(0.1) # 稍微讓出 event loop

            print(">>> 進入錄音模式，請說話...", flush=True)
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
    def record_until_silence(self, fs, silence_duration=1.0, max_duration=20):
        silence_counter = 0 #記錄連續靜音的秒數
        start_time = time.time() #記錄錄音開始時間，用來判斷是否超過 max_duration
        recording = [] #用來存每個 frame 的錄音
        speech_started = False  # 一開始沒偵測到人聲
        # 每個 frame 30ms，符合 WebRTC VAD 建議
        frame_ms = 30
        frame_samples = int(fs * frame_ms / 1000)

        buffer = np.array([], dtype=np.int16) #buffer 用來暫存讀取的 PCM 音訊，方便切成固定長度的 frame 給VAD判斷是否有語音

        # 用 sounddevice 同步錄音（替代 PvRecorder.read()）
        # 開一個獨立的 InputStream 專門給錄音階段用
        with sd.InputStream(samplerate=fs, channels=1, dtype="int16", blocksize=frame_samples) as rec_stream:
            while True:
                pcm_raw, _ = rec_stream.read(frame_samples)
                frame = pcm_raw[:, 0].astype(np.int16) #取單聲道並轉成 numpy int16
                recording.append(frame)
                buffer = np.concatenate([buffer, frame])

                # 將 frame 拆成 30ms 給 VAD
                while len(buffer) >= frame_samples:
                    frame = buffer[:frame_samples]
                    buffer = buffer[frame_samples:]

                    is_speech = self.vad.is_speech(frame.tobytes(), fs) #self.vad → WebRTC VAD 實例
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