/**
 * LOCAL voice mode: a full-duplex WebSocket to our own backend — 16 kHz s16le mic frames up, 24 kHz
 * s16le TTS down. The rates here are provisional; the server's `ready` frame is authoritative for both.
 *
 * ROUTING LIVES IN lib/voice-gate.ts, not here. This module owns the AudioContext, the worklet and the
 * socket, and only EXECUTES what `MicUplink.feed` and `applyAudioFrame` decide.
 *
 * The rest — the byte aligner, why an interrupt precedes any replayed frame, why mute is declared at
 * connect, and why the voice status is a latch — is stated at each of those lines below.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { tokenUrl } from "@/lib/api";
import { AgentTextStream } from "@/lib/transcript";
import { describeMicError, describeSkip, describeVoiceError, micUnsupportedNotice, socketNeverOpenedNotice, type VoiceErrorNotice, type VoiceLost } from "@/lib/voice-errors";
import { applyAudioFrame, MicUplink, type ServerAudioFrame } from "@/lib/voice-gate";
import { PcmChunkAligner, PlaybackScheduler } from "@/lib/voice-playback";

export type LocalVoiceStatus = "disconnected" | "connecting" | "connected" | "error";

type MessageHandler = (m: { message: string; source: "user" | "ai" }) => void;
type CorrectionHandler = (c: { corrected_agent_response?: string }) => void;

type ClientEvents = {
  onStatus: (s: LocalVoiceStatus) => void;
  onSpeaking: (s: boolean) => void;
  onError: (n: VoiceErrorNotice) => void;
  onMessage?: MessageHandler;
  onAgentResponseCorrection?: CorrectionHandler;
};

const MIC_WORKLET_URL = "/worklets/mic-capture.js";
const MIC_FRAME_SECONDS = 0.25;
const EMPTY_FREQ = new Uint8Array(0);

class LocalVoiceClient {
  muted = false;

  private ws: WebSocket | null = null;
  private closed = false;
  private micStream: MediaStream | null = null;
  private micCtx: AudioContext | null = null;
  private outCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private freqBuf: Uint8Array<ArrayBuffer> | null = null; // TS 5.7 DOM: getByteFrequencyData wants a non-shared buffer
  private inRate = 16000; // provisional — the server's "ready" frame is authoritative for both rates
  private ready = false;  // whether the call ever started, which is what separates a hang-up from a refusal
  private outRate = 24000;
  private sched = new PlaybackScheduler();
  private sources = new Set<AudioBufferSourceNode>();
  private speakTimer: ReturnType<typeof setTimeout> | null = null;
  private speaking = false;
  private mic = new MicUplink();
  private agentStream = new AgentTextStream();
  private aligner = new PcmChunkAligner();

  constructor(
    private url: string,
    private events: { readonly current: ClientEvents },
  ) {}

  start(): void {
    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (e) => {
      if (typeof e.data === "string") this.onControl(e.data);
      else this.onAudio(e.data as ArrayBuffer);
    };
    ws.onclose = (e) => {
      if (this.closed) return;
      this.closed = true;
      this.teardown();
      // A close before the server's `ready` frame means the call never started. Reported as a plain
      // disconnect it was invisible: the button went back to idle and named nothing.
      if (!this.ready && e.code !== 4401) this.events.current.onError(socketNeverOpenedNotice());
      this.events.current.onStatus(e.code === 4401 ? "error" : "disconnected");
    };
  }

  destroy(): void {
    if (this.closed) return;
    this.closed = true;
    try {
      this.ws?.close();
    } catch {
    }
    this.teardown();
  }

  sendText(text: string): boolean {
    return this.sendJson({ type: "text", text });
  }

  setMuted(m: boolean): void {
    this.muted = m;
    this.applyTrackMute();
    this.sendJson({ type: "mute", muted: m });
  }

  /** Deliberately mirrors the ElevenLabs SDK contract so lib/audio.ts works unchanged; empty when inactive. */
  getOutputByteFrequencyData(): Uint8Array {
    if (!this.analyser || !this.freqBuf) return EMPTY_FREQ;
    this.analyser.getByteFrequencyData(this.freqBuf);
    return this.freqBuf;
  }

  private onControl(raw: string): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    switch (msg.type) {
      case "ready": {
        this.ready = true;
        const ain = msg.audio_in as { sample_rate?: number } | undefined;
        const aout = msg.audio_out as { sample_rate?: number } | undefined;
        if (ain?.sample_rate) this.inRate = ain.sample_rate;
        if (aout?.sample_rate) this.outRate = aout.sample_rate;
        this.mic.arm(this.inRate);
        this.initPlayback();
        // Mute is DECLARED at connect, not only on toggle: the server otherwise keeps whatever an
        // earlier call left it at, and the call starts with the two halves disagreeing.
        this.sendJson({ type: "mute", muted: this.muted });
        this.events.current.onStatus("connected");
        void this.startMic();
        break;
      }
      case "committed":
        if (typeof msg.text === "string" && msg.text)
          this.events.current.onMessage?.({ message: msg.text, source: "user" });
        break;
      case "assistant_text": {
        const ev = this.agentStream.feed({ text: msg.text, turn: msg.turn });
        if (!ev) break;
        if (ev.kind === "message") this.events.current.onMessage?.({ message: ev.text, source: "ai" });
        else this.events.current.onAgentResponseCorrection?.({ corrected_agent_response: ev.text });
        break;
      }
      case "audio_start":
      case "audio_end":
      case "interrupted":
        applyAudioFrame(this.mic, this.sched, msg as ServerAudioFrame, performance.now(), () =>
          this.stopPlayback(),
        );
        break;
      case "skipped": {
        const reason = typeof msg.reason === "string" ? msg.reason : "";
        const notice = describeSkip(reason, this.muted);
        if (notice) this.events.current.onError(notice);
        if (reason === "muted" && !this.muted) this.sendJson({ type: "mute", muted: false });
        break;
      }
      case "error": {
        console.warn("[kotoba voice]", msg.code, msg.message, msg.fatal ? "(fatal)" : "");
        const notice = describeVoiceError(msg.code, msg.fatal);
        if (notice) this.events.current.onError(notice);
        break;
      }
      default:
        break;
    }
  }

  private async startMic(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      console.warn("[kotoba voice] mic unavailable: mediaDevices missing (insecure context?)");
      this.events.current.onError(micUnsupportedNotice());
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
      if (this.closed) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      this.micStream = stream;
      const ctx = new AudioContext();
      this.micCtx = ctx;
      await ctx.audioWorklet.addModule(MIC_WORKLET_URL);
      if (this.closed) return; // hung up while the worklet loaded — teardown() already closed ctx/stream
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-downsampler", {
        processorOptions: { targetRate: this.inRate, frameSeconds: MIC_FRAME_SECONDS },
      });
      /** MicUplink decides; this only executes. */
      node.port.onmessage = (e) => {
        const ws = this.ws;
        if (this.muted || ws?.readyState !== WebSocket.OPEN) return;
        const action = this.mic.feed(e.data as ArrayBuffer, performance.now());
        if (action.interrupt) this.bargeIn();
        for (const f of action.send) ws.send(f);
      };
      const sink = ctx.createGain();
      sink.gain.value = 0;
      src.connect(node);
      node.connect(sink);
      sink.connect(ctx.destination);
      void ctx.resume();
      this.applyTrackMute();
    } catch (err) {
      console.warn("[kotoba voice] mic unavailable:", err);
      if (!this.closed) this.events.current.onError(describeMicError(err));
    }
  }

  private applyTrackMute(): void {
    this.micStream?.getAudioTracks().forEach((t) => {
      t.enabled = !this.muted;
    });
  }

  private initPlayback(): void {
    let ctx: AudioContext;
    try {
      ctx = new AudioContext({ sampleRate: this.outRate });
    } catch {
      ctx = new AudioContext();
    }
    this.outCtx = ctx;
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.connect(ctx.destination);
    this.freqBuf = new Uint8Array(this.analyser.frequencyBinCount);
    void ctx.resume();
  }

  /** s16 samples are TWO bytes: the aligner carries a split byte within a stream and drops it across. */
  private onAudio(buf: ArrayBuffer): void {
    const ctx = this.outCtx;
    if (!ctx || !this.analyser || !this.sched.accepting) return;
    const bytes = this.aligner.align(new Uint8Array(buf), this.sched.streamGeneration);
    const n = bytes.length >> 1;
    if (!n) return;
    const at = this.sched.schedule(n / this.outRate, ctx.currentTime);
    if (at === null) return;
    const i16 = new Int16Array(bytes.buffer, bytes.byteOffset, n);
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;
    const audio = ctx.createBuffer(1, n, this.outRate);
    audio.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = audio;
    src.connect(this.analyser);
    src.start(at);
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
    if (ctx.state === "suspended") void ctx.resume();
    this.setSpeaking(true);
    this.armSpeakTimer();
  }

  private armSpeakTimer(): void {
    if (this.speakTimer) clearTimeout(this.speakTimer);
    const ctx = this.outCtx;
    if (!ctx) return;
    const ms = Math.max(0, (this.sched.playheadTime - ctx.currentTime) * 1000) + 90;
    this.speakTimer = setTimeout(() => {
      const c = this.outCtx;
      if (c && c.currentTime < this.sched.playheadTime - 0.05) this.armSpeakTimer();
      else this.setSpeaking(false);
    }, ms);
  }

  private stopPlayback(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
      }
    }
    this.sources.clear();
    this.aligner.reset();
    if (this.speakTimer) {
      clearTimeout(this.speakTimer);
      this.speakTimer = null;
    }
    this.setSpeaking(false);
  }

  /** Cut the turn. This must go out BEFORE any replayed frame, or the backend cannot drop stragglers. */
  private bargeIn(): void {
    this.sendJson({ type: "interrupt", turn: this.sched.currentTurn });
    this.sched.flush();
    this.stopPlayback();
  }

  private setSpeaking(on: boolean): void {
    this.mic.onPlayback(on, performance.now());
    if (this.speaking === on) return;
    this.speaking = on;
    this.events.current.onSpeaking(on);
  }

  private sendJson(payload: object): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(payload));
    return true;
  }

  private teardown(): void {
    this.sched.flush();
    this.stopPlayback();
    this.mic.clear();
    this.micStream?.getTracks().forEach((t) => t.stop());
    this.micStream = null;
    void this.micCtx?.close().catch(() => {});
    this.micCtx = null;
    void this.outCtx?.close().catch(() => {});
    this.outCtx = null;
    this.analyser = null;
    this.freqBuf = null;
  }
}

export function useLocalVoice(
  sessionId: string,
  apiUrl: string,
  handlers: { onMessage?: MessageHandler; onAgentResponseCorrection?: CorrectionHandler },
) {
  const [status, setStatus] = useState<LocalVoiceStatus>("disconnected");
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [micBlocked, setMicBlocked] = useState(false);
  // The latch behind the badge and the status line: cleared only when a call starts or ends.
  const [voiceLost, setVoiceLost] = useState<VoiceLost | null>(null);
  const [errorNotice, setErrorNotice] = useState<VoiceErrorNotice | null>(null);
  const clientRef = useRef<LocalVoiceClient | null>(null);
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const noticeIsFatal = useRef(false);

  const clearNoticeTimer = () => {
    if (noticeTimer.current) {
      clearTimeout(noticeTimer.current);
      noticeTimer.current = null;
    }
  };

  /** Fatal wins and stays until dismissed or the next call; transient auto-dismisses. */
  const showNotice = (n: VoiceErrorNotice) => {
    if (noticeIsFatal.current && !n.fatal) return;
    noticeIsFatal.current = n.fatal;
    setErrorNotice(n);
    clearNoticeTimer();
    if (!n.fatal) noticeTimer.current = setTimeout(() => setErrorNotice(null), 6000);
  };

  const dismissError = useCallback(() => {
    clearNoticeTimer();
    noticeIsFatal.current = false;
    setErrorNotice(null);
  }, []);

  const eventsRef = useRef<ClientEvents>({ onStatus: () => {}, onSpeaking: () => {}, onError: () => {} });
  eventsRef.current = {
    onStatus: (s) => {
      if (s === "disconnected" || s === "error") {
        clientRef.current?.destroy();
        clientRef.current = null;
        window._kotobaVoice = undefined;
        setIsSpeaking(false);
        setIsMuted(false);
        setMicBlocked(false);
        setVoiceLost(null);
      }
      setStatus(s);
    },
    onSpeaking: setIsSpeaking,
    onError: (n) => {
      showNotice(n);
      if (n.code.startsWith("mic_")) setMicBlocked(true);
      else if (n.fatal) setVoiceLost((prev) => (prev === "hearing" ? prev : (n.lost ?? "hearing")));
    },
    onMessage: handlers.onMessage,
    onAgentResponseCorrection: handlers.onAgentResponseCorrection,
  };

  const start = useCallback(() => {
    if (clientRef.current) return;
    dismissError();
    setMicBlocked(false);
    setVoiceLost(null);
    const wsBase = apiUrl.replace(/^http/, "ws");
    const client = new LocalVoiceClient(tokenUrl(`${wsBase}/api/voice/${sessionId}`), eventsRef);
    clientRef.current = client;
    window._kotobaVoice = client; // console handle on the live local-mode call; nothing in-tree reads it
    setStatus("connecting");
    client.start();
  }, [apiUrl, sessionId, dismissError]);

  const end = useCallback(() => {
    clientRef.current?.destroy();
    clientRef.current = null;
    window._kotobaVoice = undefined;
    setIsSpeaking(false);
    setIsMuted(false);
    setMicBlocked(false);
    setVoiceLost(null);
    setStatus("disconnected");
  }, []);

  /** Unmuting IS the recovery from a mic_* block, so the pill must stop saying "blocked" here. */
  const setMuted = useCallback((m: boolean) => {
    clientRef.current?.setMuted(m);
    setIsMuted(m);
    if (!m) setMicBlocked(false);
  }, []);

  const sendText = useCallback((text: string) => clientRef.current?.sendText(text) ?? false, []);
  const getOutputByteFrequencyData = useCallback(
    () => clientRef.current?.getOutputByteFrequencyData() ?? EMPTY_FREQ,
    [],
  );

  useEffect(
    () => () => {
      clientRef.current?.destroy();
      clientRef.current = null;
      if (noticeTimer.current) clearTimeout(noticeTimer.current);
    },
    [],
  );

  return {
    status,
    isSpeaking,
    isMuted,
    micBlocked,
    voiceLost,
    errorNotice,
    dismissError,
    start,
    end,
    setMuted,
    sendText,
    getOutputByteFrequencyData,
  };
}
