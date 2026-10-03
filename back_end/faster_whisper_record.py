from faster_whisper import WhisperModel
import io
import sounddevice as sd
from openwakeword.model import Model as OWWModel
from openwakeword.utils import download_models
import os
import asyncio
import time
import numpy as np
import webrtcvad

model = WhisperModel("small", device="cpu")

async def transcribe_audio(file):
    audio_bytes = await file.read()
    audio_buffer = io.BytesIO(audio_bytes)
    segments, info = model.transcribe(audio_buffer, beam_size=5, initial_prompt="以下是繁體中文內容：")
    full_text = " ".join([segment.text for segment in segments])
    return full_text


def _get_oww_model_path(model_name: str) -> str:
    import openwakeword
    pkg_dir = os.path.dirname(openwakeword.__file__)
    model_path = os.path.join(pkg_dir, "resources", "models", f"{model_name}.onnx")
    if not os.path.exists(model_path):
        download_models([model_name])
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"找不到 OWW 模型：{model_path}")
    return model_path


class VoiceListener:
    WAKE_WORD_KEY = "hey_mycroft_v0.1"
    WAKE_WORD_MODEL = "hey_mycroft_v0.1"

    def __init__(self):
        self.model = WhisperModel("tiny", device="cpu")
        self.vad = webrtcvad.Vad(2)

        model_path = _get_oww_model_path(self.WAKE_WORD_MODEL)
        self.oww_model = OWWModel(
            wakeword_model_paths=[model_path],
            inference_framework="onnx"
        )

        self.fs = 16000
        self.frame_samples = 1280        # 80ms @ 16kHz
        self.last_trigger = 0
        self.cooldown = 5
        self.running = False
        self.websockets = {"ai": [], "practice": []}
        self._loop = None
        self._mic_queue = None          # 喚醒詞用
        self._rec_queue = None          # 錄音用（獨立 queue）
        self._mode = "wake"             # "wake" | "record" | "ignore"
        self._stream = None

        self._oww_buffer = np.zeros(self.frame_samples * 10, dtype=np.int16)
        self._last_score_broadcast = 0
        self._score_broadcast_interval = 0.5
        print(self.oww_model.models.keys())

    def _sd_callback(self, indata, frames, time_info, status):
        if indata is None or self._loop is None:
            return
        pcm = indata[:, 0].copy()
        
        if self._mode == "wake" and self._mic_queue is not None:
            self._loop.call_soon_threadsafe(self._mic_queue.put_nowait, pcm)
        elif self._mode == "record" and self._rec_queue is not None:
            self._loop.call_soon_threadsafe(self._rec_queue.put_nowait, pcm)

    def start(self):
        self._loop = asyncio.get_event_loop()
        self._mic_queue = asyncio.Queue()
        self._rec_queue = asyncio.Queue()
        self._stream = sd.InputStream(
            samplerate=self.fs,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            callback=self._sd_callback,
            device=None
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

    async def broadcast(self, text):
        for tab, ws_list in self.websockets.items():
            for ws in ws_list[:]:
                try:
                    await ws.send_json({"source": "voice", "tab": tab, "text": text})
                except Exception:
                    ws_list.remove(ws)

    async def broadcast_status(self, status):
        for tab, ws_list in self.websockets.items():
            for ws in ws_list[:]:
                try:
                    await ws.send_json({"type": "status", "status": status})
                except Exception:
                    ws_list.remove(ws)

    async def broadcast_score(self, score: float):
        now = time.time()
        if now - self._last_score_broadcast < self._score_broadcast_interval:
            return
        self._last_score_broadcast = now
        for tab, ws_list in self.websockets.items():
            for ws in ws_list[:]:
                try:
                    await ws.send_json({"type": "wake_score", "score": round(float(score), 4)})
                except Exception:
                    ws_list.remove(ws)

    async def start_loop(self):
        self.start()
        try:
            while self.running:
                text = await self.listen_once()
                if text:
                    print(f"語音辨識成功: {text}")
                    await self.broadcast(text)
                await asyncio.sleep(0)   # 讓出控制權給其他 task
        finally:
            self.close()

    async def listen_once(self):
        if self._mode != "wake":
            await asyncio.sleep(0.05)
            return None

        try:
            pcm_int16 = await self._mic_queue.get()
        except Exception as e:
            print(f"Error reading from mic: {e}")
            return None

        self._oww_buffer = np.roll(self._oww_buffer, -self.frame_samples)
        self._oww_buffer[-self.frame_samples:] = pcm_int16

        prediction = self.oww_model.predict(self._oww_buffer)
        max_score = prediction.get(self.WAKE_WORD_KEY, 0.0)
        detected = max_score > 0.7

        await self.broadcast_score(max_score)

        if not detected:
            return None

        # 核心修正：一旦偵測到，立刻調用 openwakeword 內建的 reset 清空模型 LSTM 狀態記憶
        self.oww_model.reset()
        self._oww_buffer = np.zeros(self.frame_samples * 10, dtype=np.int16)

        now = time.time()
        if now - self.last_trigger < self.cooldown:
            print(f"⏳ 偵測到喚醒詞，但處於冷卻時間內，已忽略。score={max_score:.4f}")
            while not self._mic_queue.empty():
                try: self._mic_queue.get_nowait()
                except: break
            return None
            
        self.last_trigger = now
        print(f"✅ 真正觸發喚醒詞！score={max_score:.4f}")

        await self.broadcast_status("start_recording")

        self._mode = "record"
        while not self._rec_queue.empty():
            try: self._rec_queue.get_nowait()
            except: break

        print(">>> 進入錄音模式，請說話...", flush=True)
        audio = await self._record_until_silence_async()

        # 進入 ignore 狀態，拒絕接收回調音訊
        self._mode = "ignore"
        await self.broadcast_status("stop_recording")

        # 暫停一小段時間排空舊音訊
        await asyncio.sleep(0.3)

        # 清空 Queue 殘留
        for q in (self._mic_queue, self._rec_queue):
            while not q.empty():
                try: q.get_nowait()
                except: break
                    
        # 再次重設模型內部記憶與外部 buffer，做到雙重保險
        self.oww_model.reset()
        self._oww_buffer = np.zeros(self.frame_samples * 10, dtype=np.int16)
        
        self._mode = "wake"
        print(">>> 系統回復，重新開始監聽喚醒詞...\n")

        if audio is None:
            return None

        audio_float = audio.astype(np.float32) / 32768.0
        segments, _ = await asyncio.to_thread(
            self.model.transcribe, audio_float,
            initial_prompt="以下是繁體中文內容："
        )
        return "".join([seg.text for seg in segments])

    async def _record_until_silence_async(self, silence_duration=1.0, max_duration=20):
        vad_frame_samples = int(self.fs * 0.03)  # 30ms = 480 samples
        recording = []
        speech_started = False
        silence_counter = 0.0
        start_time = asyncio.get_event_loop().time()
        vad_buffer = np.array([], dtype=np.int16)

        while True:
            try:
                frame = await asyncio.wait_for(self._rec_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() - start_time > max_duration:
                    print("達最大錄音時間")
                    break
                continue

            recording.append(frame)
            vad_buffer = np.concatenate([vad_buffer, frame])

            while len(vad_buffer) >= vad_frame_samples:
                chunk = vad_buffer[:vad_frame_samples]
                vad_buffer = vad_buffer[vad_frame_samples:]
                is_speech = self.vad.is_speech(chunk.tobytes(), self.fs)
                if is_speech:
                    speech_started = True
                    silence_counter = 0.0
                elif speech_started:
                    silence_counter += 0.03

            if speech_started and silence_counter >= silence_duration:
                print(f"偵測 {silence_duration}s 靜音，停止錄音")
                break

            if asyncio.get_event_loop().time() - start_time > max_duration:
                print("達最大錄音時間")
                break

        if not recording:
            return None
        return np.concatenate(recording)