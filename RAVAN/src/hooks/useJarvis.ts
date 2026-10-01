import { useCallback, useEffect, useRef, useState } from "react";
import {
  executeJarvisCommand,
  executeRyvenCommand,
  interpretCommand,
  sampleCommand,
} from "@/services/jarvis";
import type { CommandStep, CoreState, HudMode, TranscriptLine } from "@/types/jarvis";

interface SpeechResultLike {
  results: { 0: { transcript: string }; length: number }[];
}
interface RecognitionLike {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechResultLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
}

function createRecognition(): RecognitionLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => RecognitionLike;
    webkitSpeechRecognition?: new () => RecognitionLike;
  };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  return rec;
}

let uid = 0;
const nextId = () => `l${++uid}`;

export function useJarvis() {
  const [state, setState] = useState<CoreState>("boot");
  const [mode, setMode] = useState<HudMode>("core");
  const [steps, setSteps] = useState<CommandStep[]>([]);
  const [activeStep, setActiveStep] = useState(-1);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [statusText, setStatusText] = useState("INITIALIZING");

  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const recognition = useRef<RecognitionLike | null>(null);
  const fallbackCommandIndex = useRef(0);
  const sessionIdRef = useRef<string>(`session-${Date.now()}`);

  const later = useCallback((fn: () => void, ms: number) => {
    timers.current.push(setTimeout(fn, ms));
  }, []);

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => {
    const boot = setTimeout(() => {
      setState("idle");
      setStatusText("READY WHEN YOU ARE");
    }, 2600);
    return () => {
      clearTimeout(boot);
      clearTimers();
      recognition.current?.stop();
    };
  }, [clearTimers]);

  const submit = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      clearTimers();

      setTranscript((prev) => [...prev.slice(-6), { id: nextId(), speaker: "user", text }]);
      setState("thinking");
      setStatusText("ANALYZING");
      setSteps([{ label: "Voice / Input", detail: text }, { label: "Routing Intent" }]);
      setActiveStep(0);

      const result = await executeRyvenCommand(text, sessionIdRef.current);

      setState("executing");
      setStatusText("EXECUTING");
      setSteps(result.steps);
      setActiveStep(-1);
      result.steps.forEach((_, i) => later(() => setActiveStep(i), i * 350));

      const execDone = result.steps.length * 350 + 250;

      later(() => {
        setMode(result.mode);
        setState("speaking");
        setStatusText("SPEAKING");
        setTranscript((prev) => [
          ...prev.slice(-6),
          { id: nextId(), speaker: "ryven", text: result.reply },
        ]);
        if (typeof window !== "undefined" && "speechSynthesis" in window) {
          try {
            const utter = new SpeechSynthesisUtterance(result.reply);
            utter.rate = 1.02;
            utter.pitch = 0.9;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utter);
          } catch {
            /* speech synthesis unavailable */
          }
        }
      }, execDone);

      later(
        () => {
          setState("idle");
          setStatusText("READY");
          setSteps([]);
          setActiveStep(-1);
        },
        execDone + 800 + Math.min(4200, result.reply.length * 45),
      );
    },
    [clearTimers, later],
  );

  const stopListening = useCallback(() => {
    recognition.current?.stop();
    recognition.current = null;
    clearTimers();
    setState("idle");
    setStatusText("READY");
  }, [clearTimers]);

  const startListening = useCallback(() => {
    clearTimers();
    setState("listening");
    setStatusText("LISTENING");

    const rec = createRecognition();
    if (rec) {
      recognition.current = rec;
      let heard = false;
      rec.onresult = (event) => {
        heard = true;
        const text = event.results[0]?.[0]?.transcript ?? "";
        recognition.current = null;
        submit(text);
      };
      rec.onerror = () => {
        recognition.current = null;
        if (!heard) submit(sampleCommand(fallbackCommandIndex.current++));
      };
      rec.onend = () => {
        recognition.current = null;
      };
      try {
        rec.start();
        return;
      } catch {
        recognition.current = null;
      }
    }

    // No speech recognition available — run fallback sample command.
    later(() => submit(sampleCommand(fallbackCommandIndex.current++)), 2200);
  }, [clearTimers, later, submit]);

  const toggleListening = useCallback(() => {
    if (state === "listening") stopListening();
    else if (state === "idle" || state === "speaking") startListening();
  }, [state, startListening, stopListening]);

  return {
    state,
    mode,
    setMode,
    steps,
    activeStep,
    transcript,
    statusText,
    submit,
    toggleListening,
  };
}

export const useRyven = useJarvis;
